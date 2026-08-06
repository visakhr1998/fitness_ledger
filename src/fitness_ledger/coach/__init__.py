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


def configure_adk_environment(config: Config) -> str:
    """Point ADK at the same model this app already uses.

    ADK reads `GOOGLE_API_KEY` and `GOOGLE_GENAI_USE_VERTEXAI` from the
    environment; this app stores `GEMINI_API_KEY` in `.env`. Bridging here
    keeps one key in one place -- otherwise the dock and the coach can end up
    authenticated differently, which is confusing precisely when something is
    already going wrong.

    Returns the model id the coach should use.
    """
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
    from .. import llm

    if llm.resolve_provider(config) == "gemini" and config.llm_model:
        return config.llm_model
    return config.gemini_model
