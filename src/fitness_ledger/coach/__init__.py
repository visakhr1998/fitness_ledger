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
    """
    from .. import llm

    if llm.resolve_provider(config) == "openai-compatible":
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

    # LLM_MODEL overrides whichever provider the *dock* is using, which may not
    # be Gemini. Honouring it unconditionally would hand ADK an Ollama tag or a
    # Claude id and fail somewhere far from the cause.
    if llm.resolve_provider(config) == "gemini" and config.llm_model:
        return config.llm_model
    return config.gemini_model


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
    )


def is_quota_error(exc: BaseException) -> bool:
    """Whether a failure is the provider refusing, rather than a real fault.

    Matched on the message because each SDK wraps it differently: ADK raises
    _ResourceExhaustedError, LiteLLM its own, and a bare httpx call gives you a
    status. The one thing they all carry is the 429 and the phrase.
    """
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text or "quota" in text.lower()


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
    )
