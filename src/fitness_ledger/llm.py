"""Model transport.

``chat.py`` owns the tools, the system prompt and the loop. This module owns the
one thing that differs between vendors: how a turn is put on the wire and how a
tool call comes back.

Two transports:

- **anthropic** — the native SDK.
- **openai** — a single code path for every provider that speaks
  ``/chat/completions``: Gemini, Groq, Ollama, OpenRouter. Only the base URL, key
  and model name change, so switching provider is configuration, not code.

Tools are declared once, in Anthropic's shape, and translated here. Adding a
provider must never mean restating thirteen tool definitions.

The transport is deliberately dumb: it carries messages and reports what the
model asked for. It never dispatches a tool and never inspects a result, because
that would put provider-specific code on the path where numbers are produced.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .config import Config

# Gemini's OpenAI-compatibility shim. Using it rather than google-genai keeps one
# transport for every non-Anthropic provider.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Ollama serves the same surface, so local models need no extra code.
OLLAMA_BASE_URL = "http://localhost:11434/v1"

# Generous because `max_tokens` caps thinking *plus* visible output on models
# that reason. Gemini 2.5 Flash spent 554 thinking tokens on an 11-token prompt
# and cut the answer mid-sentence at 1500. It is a ceiling, not a spend.
MAX_TOKENS = 4000

# Gemini reasons by default. This app forbids the model from reasoning about
# numbers -- it picks a tool and reads the result back -- so thinking is pure
# cost and pure truncation risk, and the default asks for as little as the
# provider allows.
#
# It was "none" until 2026-08-29, which Gemini accepted on 2.5-flash and now
# rejects on gemini-3.6-flash with `400 INVALID_ARGUMENT: Request contains an
# invalid argument`. That 400 took down every Gemini request the app made --
# the chat dock included -- and reads nothing like a rejected parameter, so it
# is worth knowing it can mean exactly one bad value in the payload. Measured
# the same day: "none" is the only value the model refuses; minimal, low,
# medium and high all succeed.
#
# `LLM_REASONING_EFFORT=off` still suppresses the parameter entirely, which is
# the escape hatch if a provider starts rejecting this one too.
DEFAULT_REASONING_EFFORT = {"gemini": "minimal"}


@dataclass(frozen=True)
class ToolCall:
    """A tool the model wants run, normalised across providers."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Turn:
    """One model reply. ``tool_calls`` empty means the model is finished."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Hit the token ceiling mid-sentence. Silent truncation reads as a model
    # that trailed off, so the loop turns it into a stated limit instead.
    truncated: bool = False


class ProviderError(RuntimeError):
    """Configuration is missing or contradictory. Surfaced to the user as 503."""


class ProviderUnavailable(ProviderError):
    """The provider was reachable but would not answer this time.

    A timeout or a quota refusal, as opposed to a misconfiguration. Separate
    because the two need different words: one says try again, the other says
    change a setting. Both are `ProviderError` so existing handlers that catch
    `RuntimeError` keep working.
    """


def describe_provider_failure(exc: BaseException) -> str | None:
    """Turn a transport exception into something worth showing a user.

    Returns None when the failure is not one of the two we can explain, so the
    caller re-raises rather than dressing up an unknown fault.

    Matched on class name and message rather than by importing the SDK's
    exception types: two SDKs are in play, the openai one is optional at import
    time in some install shapes, and the strings are what every provider agrees
    on.
    """
    name = type(exc).__name__

    if "Timeout" in name or "timed out" in str(exc).lower():
        return (
            "The model did not answer in time. This provider's response time "
            "varies a lot, so trying again often works. Raise "
            "LLM_TIMEOUT_SECONDS in .env if it keeps happening."
        )
    if is_quota_refusal(exc):
        return (
            "The model provider refused the request: quota exhausted. Free "
            "tiers reset on their own schedule -- wait, or point LLM_PROVIDER "
            "at a different provider in .env."
        )
    if _is_credential_failure(exc):
        return (
            "The model provider rejected the credentials. Check the API key for "
            "the provider named by LLM_PROVIDER in .env."
        )
    if "NotFound" in name:
        return (
            "The model provider does not recognise that model. Check GEMINI_MODEL "
            "or LLM_MODEL in .env -- a model id can stop being available."
        )
    if "APIConnection" in name or "Connection" in name:
        return (
            "Could not reach the model provider. Check the network, and "
            "LLM_BASE_URL in .env if you have set one."
        )
    return None


def _is_credential_failure(exc: BaseException) -> bool:
    """Whether the provider rejected the key rather than the request.

    Not just the exception class: Gemini's OpenAI-compatible shim answers a bad
    key with `400 BadRequestError: Please pass a valid API key`, not a 401, so
    matching only `AuthenticationError` left the commonest setup mistake in this
    app reporting as an unexplained 500.
    """
    name = type(exc).__name__
    if "Authentication" in name or "PermissionDenied" in name:
        return True
    if getattr(exc, "status_code", None) in (401, 403):
        return True

    lowered = str(exc).lower()
    return (
        "api key" in lowered
        or "api_key" in lowered
        or "unauthorized" in lowered
        or "invalid authentication" in lowered
    )


def is_quota_refusal(exc: BaseException) -> bool:
    """Whether the provider refused because we asked too often.

    The status code first, because it is the thing that actually means 429.
    Both SDKs expose it on their status errors, and LiteLLM carries one too.

    Text is the fallback, and it is anchored: matching a bare "429" anywhere in
    the message classified `Bad request: invalid argument (request id
    req_a429bf)` as a quota refusal, which told the user to change providers and
    -- because the coach shares this judgement -- launched a paid fallback run to
    re-execute a plan that was going to fail for its own reasons.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429

    if "RateLimit" in type(exc).__name__:
        return True

    lowered = str(exc).lower()
    return (
        "resource_exhausted" in lowered
        or "quota" in lowered
        or "too many requests" in lowered
        or re.search(r"(?<!\w)429(?!\w)", lowered) is not None
    )


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool schema -> OpenAI function schema.

    Only the envelope differs; ``input_schema`` is already JSON Schema and passes
    through untouched. The defaults matter for the two argument-less tools --
    several providers reject a function whose parameters omit ``type`` or
    ``properties`` outright.
    """
    converted: list[dict[str, Any]] = []
    for tool in tools:
        schema = dict(tool.get("input_schema") or {})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": schema,
                },
            }
        )
    return converted


class Transport(ABC):
    """A conversation with one model. Holds its own message history because the
    two wire formats disagree about where a tool result lives -- Anthropic puts
    it in a user turn, OpenAI gives it a role of its own."""

    def __init__(self, model: str, system: str, tools: list[dict[str, Any]]):
        self.model = model
        self.system = system
        self.tools = tools

    @abstractmethod
    def ask(self, question: str) -> None:
        """Add the user's question."""

    @abstractmethod
    async def turn(self) -> Turn:
        """Send the conversation and record the reply."""

    @abstractmethod
    def record(self, results: list[tuple[ToolCall, str]]) -> None:
        """Add tool results, ready for the next turn."""


class AnthropicTransport(Transport):
    def __init__(
        self,
        model,
        system,
        tools,
        *,
        api_key: str | None,
        timeout: float | None = None,
        max_retries: int = 1,
    ):
        super().__init__(model, system, tools)
        from anthropic import AsyncAnthropic

        # Same bounding as the OpenAI path, so a slow provider behaves the same
        # way whichever transport is configured.
        options: dict[str, Any] = {"max_retries": max_retries}
        if timeout is not None:
            options["timeout"] = timeout
        # A None key is fine: the SDK then resolves ANTHROPIC_AUTH_TOKEN or an
        # `ant auth login` profile, so an OAuth profile works with no key set.
        self._client = (
            AsyncAnthropic(api_key=api_key, **options)
            if api_key
            else AsyncAnthropic(**options)
        )
        self._messages: list[dict[str, Any]] = []

    def ask(self, question: str) -> None:
        self._messages.append({"role": "user", "content": question})

    async def turn(self) -> Turn:
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=self.tools,
                messages=self._messages,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless explainable
            explanation = describe_provider_failure(exc)
            if explanation is None:
                raise
            raise ProviderUnavailable(explanation) from exc
        self._messages.append({"role": "assistant", "content": response.content})

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input or {})
            for block in response.content
            if block.type == "tool_use"
        ]
        return Turn(
            text=text,
            tool_calls=calls,
            truncated=response.stop_reason == "max_tokens",
        )

    def record(self, results: list[tuple[ToolCall, str]]) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": payload,
                    }
                    for call, payload in results
                ],
            }
        )


class OpenAICompatibleTransport(Transport):
    """Gemini, Groq, Ollama, OpenRouter -- anything serving /chat/completions."""

    def __init__(
        self,
        model,
        system,
        tools,
        *,
        api_key: str,
        base_url: str,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
        max_retries: int = 1,
    ):
        super().__init__(model, system, tools)
        self.reasoning_effort = reasoning_effort
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - install-time path
            raise ProviderError(
                "This provider needs the `openai` package (used only as a "
                "protocol client). Install it with: pip install -e ."
            ) from exc

        # The SDK's own defaults are 600 seconds and two retries, which behind
        # a spinner is indistinguishable from a hang -- and the retries are
        # silent, so a slow provider looks like one very long request.
        client_options: dict[str, Any] = {"max_retries": max_retries}
        if timeout is not None:
            client_options["timeout"] = timeout
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, **client_options)
        self._tools = to_openai_tools(tools)
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def ask(self, question: str) -> None:
        self._messages.append({"role": "user", "content": question})

    async def turn(self) -> Turn:
        extra: dict[str, Any] = {}
        if self.reasoning_effort:
            # Omitted unless configured: providers that don't reason reject it.
            extra["reasoning_effort"] = self.reasoning_effort

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=self._messages,
                tools=self._tools,
                **extra,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless explainable
            # A timeout and a quota refusal are the two failures a user can act
            # on. Everything else keeps its own traceback.
            explanation = describe_provider_failure(exc)
            if explanation is None:
                raise
            raise ProviderUnavailable(explanation) from exc
        choice = response.choices[0]
        message = choice.message
        # Echo the assistant turn back verbatim; a hand-rebuilt message loses the
        # tool_call ids the next request has to match.
        self._messages.append(message.model_dump(exclude_none=True))

        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_loads(call.function.arguments),
            )
            for call in (message.tool_calls or [])
        ]
        return Turn(
            text=(message.content or "").strip(),
            tool_calls=calls,
            truncated=choice.finish_reason == "length",
        )

    def record(self, results: list[tuple[ToolCall, str]]) -> None:
        # One message per result, unlike Anthropic's single bundled user turn.
        for call, payload in results:
            self._messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": payload}
            )


def _loads(raw: str | None) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string. A small model can emit malformed
    JSON; an empty dict lets the tool report a sensible error instead of the
    whole request dying on a parse."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_provider(config: Config) -> str:
    """Which provider to use. Explicit setting wins; otherwise whichever key
    exists, preferring the free one."""
    if config.llm_provider:
        return config.llm_provider.strip().lower()
    if config.gemini_api_key:
        return "gemini"
    if config.anthropic_api_key:
        return "anthropic"
    return "none"


def _effort(config: Config, provider: str) -> str | None:
    """Configured reasoning effort, else the provider's default. ``off`` sends
    nothing, for a provider that rejects the parameter outright."""
    setting = (config.llm_reasoning_effort or "").strip().lower()
    if setting == "off":
        return None
    return setting or DEFAULT_REASONING_EFFORT.get(provider)


def _limits(config: Config) -> dict[str, Any]:
    """How long to wait, and how many times to ask again.

    Kept here rather than at each call site so both transports and all four
    providers are bounded the same way. A timeout of 0 or less means "use the
    SDK default", which is the escape hatch for a deliberately slow local
    model -- Ollama on CPU can legitimately take minutes.
    """
    limits: dict[str, Any] = {"max_retries": max(config.llm_max_retries, 0)}
    if config.llm_timeout_seconds > 0:
        limits["timeout"] = config.llm_timeout_seconds
    return limits


def model_name(config: Config) -> str:
    """The model string the resolved provider will send. `LLM_MODEL` overrides
    every provider so a swap needs one variable, not one per vendor."""
    provider = resolve_provider(config)
    if provider == "anthropic":
        return config.llm_model or config.anthropic_model
    if provider == "gemini":
        return config.llm_model or config.gemini_model
    if provider == "ollama":
        return config.llm_model or "qwen3:4b"
    return config.llm_model or ""


def build(config: Config, system: str, tools: list[dict[str, Any]]) -> Transport:
    """Construct the configured transport, or explain what is missing."""
    provider = resolve_provider(config)
    model = model_name(config)

    if provider == "anthropic":
        return AnthropicTransport(
            model, system, tools, api_key=config.anthropic_api_key, **_limits(config)
        )

    if provider == "gemini":
        if not config.gemini_api_key:
            raise ProviderError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. Get a free key "
                "at https://aistudio.google.com/apikey"
            )
        return OpenAICompatibleTransport(
            model,
            system,
            tools,
            api_key=config.gemini_api_key,
            base_url=config.llm_base_url or GEMINI_BASE_URL,
            reasoning_effort=_effort(config, provider),
            **_limits(config),
        )

    if provider == "ollama":
        return OpenAICompatibleTransport(
            model,
            system,
            tools,
            # Ollama ignores the key but the client insists on one.
            api_key=config.llm_api_key or "ollama",
            base_url=config.llm_base_url or OLLAMA_BASE_URL,
            reasoning_effort=_effort(config, provider),
            **_limits(config),
        )

    if provider == "openai-compatible":
        if not config.llm_base_url or not model:
            raise ProviderError(
                "LLM_PROVIDER=openai-compatible needs LLM_BASE_URL and LLM_MODEL."
            )
        return OpenAICompatibleTransport(
            model,
            system,
            tools,
            api_key=config.llm_api_key or "unused",
            base_url=config.llm_base_url,
            reasoning_effort=_effort(config, provider),
            **_limits(config),
        )

    if provider == "none":
        raise ProviderError(
            "The assistant has no model configured. Set GEMINI_API_KEY in .env "
            "(free key: https://aistudio.google.com/apikey), or ANTHROPIC_API_KEY. "
            "Everything else on the dashboard works without one."
        )

    raise ProviderError(
        f"Unknown LLM_PROVIDER {provider!r}. Use anthropic, gemini, ollama or "
        "openai-compatible."
    )
