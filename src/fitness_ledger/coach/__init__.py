"""The coach: a planning agent over the ledger.

Separate from `chat.py` by design. The dock answers questions about what
happened; the coach proposes what to do next. They share the discipline that
makes both trustworthy -- every number comes from the rules engine -- but not
their machinery, and the coach must never become a reason to change the dock.

ADK is an optional extra (`pip install -e ".[coach]"`). It pulls 25 direct
dependencies, and nothing else in this app needs them: the dashboard, sync,
CLI and chat dock all work without it. Import errors from a missing install
are turned into a stated instruction rather than a traceback.
"""

from __future__ import annotations

import os
from typing import Any

from ..config import Config


class CoachUnavailable(RuntimeError):
    """ADK isn't installed, or no model provider is configured."""


def require_adk() -> None:
    """Fail with something actionable rather than a ModuleNotFoundError."""
    try:
        import google.adk  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - install-time path
        raise CoachUnavailable(
            "The coach needs ADK. Install it with:  pip install -e \".[coach]\"\n"
            "Everything else in this app works without it."
        ) from exc


def configure_adk_environment(config: Config):
    """Point ADK at the same model this app already uses.

    ADK reads `GOOGLE_API_KEY` and `GOOGLE_GENAI_USE_VERTEXAI` from the
    environment; this app stores `GEMINI_API_KEY` in `.env`. Bridging here
    keeps one key in one place -- otherwise the dock and the coach can end up
    authenticated differently, which is confusing precisely when something is
    already going wrong.

    Returns what ADK should be given as its model: a Gemini model id as a
    string, or a `LiteLlm` instance for any OpenAI-compatible provider.

    `COACH_PROVIDER` decides, falling back to `LLM_PROVIDER` when unset. The
    two were one setting until 2026-09-04, and they want opposite things: the
    dock is frequent and wants low latency, planning is occasional and wants a
    model that can plan. Measured on `back_neglected`, same prompt and code,
    DeepSeek returned an empty week and Gemini returned four sessions and 64
    sets -- so choosing for the dock was choosing wrong for the coach.
    """
    from .. import llm

    provider = coach_provider(config)

    if provider == "openai-compatible":
        return _openai_compatible_model(config)

    if not config.gemini_api_key:
        raise CoachUnavailable(
            "The coach needs GEMINI_API_KEY in .env. Get a free key at "
            "https://aistudio.google.com/apikey"
        )

    # setdefault: an explicitly exported GOOGLE_API_KEY wins, so a user who
    # deliberately points ADK elsewhere is not overridden by us.
    os.environ.setdefault("GOOGLE_API_KEY", config.gemini_api_key)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    # COACH_MODEL is the coach's own, so it applies whenever the coach is on
    # Gemini. LLM_MODEL is the *dock's*, and only carries over when the dock is
    # on Gemini too -- honouring it otherwise would hand ADK an Ollama tag or a
    # Claude id and fail somewhere far from the cause.
    if config.coach_model:
        return config.coach_model
    if config.coach_provider is None and llm.resolve_provider(config) == "gemini" and config.llm_model:
        return config.llm_model
    return config.gemini_model


def coach_provider(config: Config) -> str:
    """Which provider the coach talks to.

    `COACH_PROVIDER` when set, otherwise whatever the dock resolved to. The
    inherit-by-default keeps every existing `.env` behaving exactly as it did.
    """
    from .. import llm

    if config.coach_provider:
        return config.coach_provider.strip().lower()
    return llm.resolve_provider(config)


def _openai_compatible_model(config: Config):
    """An OpenAI-compatible provider -- DeepSeek, OpenRouter, Groq, Ollama.

    ADK speaks Gemini natively and reaches everything else through LiteLLM, an
    optional extra. The model id is prefixed `openai/` deliberately: that is
    LiteLLM's instruction to treat the endpoint as OpenAI-shaped and use the
    base URL given, rather than to look the name up in its provider registry.
    Naming the provider instead (`deepseek/...`) makes LiteLLM ignore
    `api_base` and route to that vendor's own default host, which is wrong the
    moment the endpoint is a proxy like OpenRouter.

    The dock reaches the same provider through `llm.py` with no code at all,
    because it was written against the OpenAI wire format from the start. Only
    the coach needs this, and only because ADK is Gemini-shaped.
    """
    if not config.llm_base_url or not config.llm_model:
        raise CoachUnavailable(
            "LLM_PROVIDER=openai-compatible needs LLM_BASE_URL and LLM_MODEL "
            "in .env. For DeepSeek: "
            "LLM_BASE_URL=https://api.deepseek.com and "
            "LLM_MODEL=deepseek-v4-flash"
        )
    if not config.llm_api_key:
        raise CoachUnavailable(
            "LLM_PROVIDER=openai-compatible needs LLM_API_KEY in .env "
            "(except for a local server such as Ollama, which takes any value)."
        )

    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - install-time path
        raise CoachUnavailable(
            "A non-Gemini provider needs LiteLLM. Install it with: "
            'pip install -e ".[coach]" (which now includes '
            "google-adk[extensions]). Gemini needs no extra."
        ) from exc

    return LiteLlm(
        model=f"openai/{config.llm_model}",
        api_base=config.llm_base_url.rstrip("/"),
        api_key=config.llm_api_key,
        **_limits(config),
    )


def _limits(config: Config) -> dict[str, Any]:
    """How long a planning request may take, and how often to ask again.

    The coach is a third transport. `llm._limits` bounds the dock's two and
    says it is kept in one place "so a provider cannot be left unbounded by
    omission" -- but it is never reached from here, because ADK talks to
    everything non-Gemini through LiteLLM. So every plan ran on LiteLLM's
    600-second default with no cap on retries.

    Found by watching an eval pass sit at zero CPU for 45 seconds, 42 minutes
    into a run that takes twenty. A stalled connection was indistinguishable
    from a slow model, which is the same confusion PR #34 removed from the
    dock and the intake box.

    `0` means "use the SDK default", the same escape hatch `llm._limits` has,
    for a local model that is legitimately slow.
    """
    limits: dict[str, Any] = {"num_retries": max(config.llm_max_retries, 0)}
    if config.coach_timeout_seconds > 0:
        limits["timeout"] = config.coach_timeout_seconds
    return limits


def is_quota_error(exc: BaseException) -> bool:
    """Whether a failure is the provider refusing, rather than a real fault.

    Delegates to `llm.is_quota_refusal` rather than keeping a second matcher.
    The two were independent and had already drifted -- this one knew
    RESOURCE_EXHAUSTED and the dock's did not -- so the coach and the chat dock
    could disagree about whether the same exception was a quota problem. This is
    the more expensive disagreement of the two: a false positive here launches a
    paid fallback run.
    """
    from .. import llm

    return llm.is_quota_refusal(exc)


def fallback_model(config: Config):
    """The coach's second provider, or None when none is configured.

    Planning is the one place a quota error is fatal rather than annoying: the
    dock can say "ask again in a minute", but a week that will not generate is
    simply absent, and the free tiers that plan well are exactly the ones with
    tight caps.

    Deliberately separate from LLM_* rather than reusing it. Those settings are
    the dock's provider; making the coach's fallback share them would mean you
    could not run a free primary and a paid backstop, which is the entire point.
    """
    if not config.has_coach_fallback:
        return None

    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - install-time path
        raise CoachUnavailable(
            "A fallback provider needs LiteLLM. Install it with: "
            'pip install -e ".[coach]"'
        ) from exc

    # openai/ for the same reason as _openai_compatible_model: it tells LiteLLM
    # to honour api_base rather than routing to a vendor's default host.
    return LiteLlm(
        model=f"openai/{config.coach_fallback_model}",
        api_base=config.coach_fallback_base_url.rstrip("/"),
        api_key=config.coach_fallback_api_key,
        **_limits(config),
    )
