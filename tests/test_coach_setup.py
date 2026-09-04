"""Wiring the coach to a model.

Small surface, but two things here fail far from their cause if they are
wrong: handing ADK a model id belonging to a different provider, and letting a
missing optional dependency surface as a bare ModuleNotFoundError.
"""

from __future__ import annotations

import pytest

from fitness_ledger.coach import CoachUnavailable, configure_adk_environment
from fitness_ledger.config import Config


def config(**overrides) -> Config:
    base = Config(
        db_path="unused",
        hevy_command="x", hevy_args=[], hevy_env={},
        health_command="x", health_args=[], health_env={},
    )
    from dataclasses import replace

    return replace(base, **overrides)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # configure_adk_environment uses setdefault, so a leaked value from an
    # earlier test would make the next one pass for the wrong reason.
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)


def test_without_a_key_the_coach_says_what_to_do(monkeypatch):
    with pytest.raises(CoachUnavailable, match="GEMINI_API_KEY"):
        configure_adk_environment(config())


def test_the_gemini_key_is_bridged_to_the_name_adk_reads(monkeypatch):
    # The app stores GEMINI_API_KEY; ADK reads GOOGLE_API_KEY. One key in one
    # place, or the dock and the coach end up authenticated differently.
    import os

    configure_adk_environment(config(gemini_api_key="secret"))
    assert os.environ["GOOGLE_API_KEY"] == "secret"
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "FALSE"


def test_an_explicit_google_api_key_is_not_overridden(monkeypatch):
    # Someone pointing ADK somewhere deliberately should stay pointed there.
    monkeypatch.setenv("GOOGLE_API_KEY", "theirs")
    configure_adk_environment(config(gemini_api_key="ours"))

    import os

    assert os.environ["GOOGLE_API_KEY"] == "theirs"


def test_the_default_model_is_the_gemini_one():
    assert configure_adk_environment(config(gemini_api_key="k")) == "gemini-3.6-flash"


def test_llm_model_applies_when_the_dock_is_on_gemini():
    model = configure_adk_environment(
        config(gemini_api_key="k", llm_provider="gemini", llm_model="gemini-3.5-flash")
    )
    assert model == "gemini-3.5-flash"


def test_a_model_belonging_to_another_provider_is_ignored():
    """The one that would fail far from its cause.

    LLM_MODEL overrides whichever provider the *dock* uses. If the dock is on
    Ollama, honouring it hands ADK the string "qwen3:4b" and the failure
    surfaces inside a model call, not here.
    """
    model = configure_adk_environment(
        config(gemini_api_key="k", llm_provider="ollama", llm_model="qwen3:4b")
    )
    assert model == "gemini-3.6-flash"


# --- providers that are not Gemini ------------------------------------------


def test_an_openai_compatible_provider_becomes_a_litellm_model():
    """ADK speaks Gemini natively and reaches everything else through LiteLLM.
    One config switch moves both the dock and the coach."""
    pytest.importorskip("litellm", reason="coach extra not installed")
    from dataclasses import replace

    from fitness_ledger.coach import configure_adk_environment
    from fitness_ledger.config import Config

    model = configure_adk_environment(
        replace(
            Config.load(),
            llm_provider="openai-compatible",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-v4-flash",
            llm_api_key="not-a-real-key",
        )
    )

    # `openai/` tells LiteLLM to treat the endpoint as OpenAI-shaped and honour
    # api_base. Naming the vendor instead makes it ignore api_base and route to
    # that vendor's default host -- wrong the moment the endpoint is a proxy.
    assert model.model == "openai/deepseek-v4-flash"


def test_gemini_is_still_a_plain_model_id():
    """The Gemini path must not start depending on LiteLLM.

    The provider is stated rather than read from `Config.load()`. This
    previously took whatever the developer's own `.env` said, so pointing the
    machine at DeepSeek turned a passing suite red without a line of source
    changing -- a test that measures the room, not the code.
    """
    from dataclasses import replace

    from fitness_ledger.coach import configure_adk_environment
    from fitness_ledger.config import Config

    gemini = replace(
        Config.load(),
        llm_provider="gemini",
        gemini_api_key="not-a-real-key",
        llm_model=None,
    )
    assert isinstance(configure_adk_environment(gemini), str)


def test_an_openai_compatible_provider_without_a_url_says_so():
    from dataclasses import replace

    from fitness_ledger.coach import CoachUnavailable, configure_adk_environment
    from fitness_ledger.config import Config

    with pytest.raises(CoachUnavailable, match="LLM_BASE_URL"):
        configure_adk_environment(
            replace(Config.load(), llm_provider="openai-compatible", llm_base_url="")
        )


def test_an_openai_compatible_provider_without_a_key_says_so():
    from dataclasses import replace

    from fitness_ledger.coach import CoachUnavailable, configure_adk_environment
    from fitness_ledger.config import Config

    with pytest.raises(CoachUnavailable, match="LLM_API_KEY"):
        configure_adk_environment(
            replace(
                Config.load(),
                llm_provider="openai-compatible",
                llm_base_url="https://api.deepseek.com",
                llm_model="deepseek-v4-flash",
                llm_api_key="",
            )
        )


# --- the fallback provider --------------------------------------------------


def fallback_config(**overrides):
    from dataclasses import replace

    from fitness_ledger.config import Config

    settings = {
        "coach_fallback_base_url": "https://openrouter.ai/api/v1",
        "coach_fallback_model": "deepseek/deepseek-v4-flash-0731",
        "coach_fallback_api_key": "not-a-real-key",
    }
    settings.update(overrides)
    return replace(Config.load(), **settings)


def test_no_fallback_means_no_fallback():
    """It costs money. Opting in has to be deliberate.

    Built explicitly rather than from `Config.load()`: reading the developer's
    own .env made this pass or fail depending on whose machine it ran on, and
    it started failing the moment a real fallback was configured.
    """
    from fitness_ledger.coach import fallback_model

    assert fallback_model(fallback_config(coach_fallback_base_url=None)) is None


def test_a_partly_configured_fallback_is_no_fallback():
    """A base URL with no key would fail at the worst moment -- mid-plan, after
    the primary has already refused."""
    from fitness_ledger.coach import fallback_model

    assert fallback_model(fallback_config(coach_fallback_api_key=None)) is None
    assert fallback_model(fallback_config(coach_fallback_model=None)) is None


def test_a_configured_fallback_resolves():
    pytest.importorskip("litellm", reason="coach extra not installed")
    from fitness_ledger.coach import fallback_model

    assert fallback_model(fallback_config()).model == (
        "openai/deepseek/deepseek-v4-flash-0731"
    )


def test_the_coach_inherits_the_dock_provider_by_default():
    """Unset `COACH_PROVIDER` must behave exactly as before the split, or every
    existing .env changes meaning on upgrade."""
    from fitness_ledger.coach import coach_provider

    assert coach_provider(config(llm_provider="gemini", gemini_api_key="k")) == "gemini"
    assert coach_provider(
        config(
            llm_provider="openai-compatible",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-v4-flash",
            llm_api_key="k",
        )
    ) == "openai-compatible"


def test_the_coach_provider_can_differ_from_the_dock():
    """The point of the split, and the decision behind it.

    Measured 2026-09-04 on `back_neglected`, same prompt and same code:
    deepseek-v4-flash-0731 returned 0 sessions and 0 sets; gemini-3.6-flash
    returned 4 sessions and 64 sets across all eleven short muscles. A full
    gate pass on DeepSeek left five of nine fixtures with an empty week.

    The dock wants low latency and runs constantly; the coach runs about three
    requests when you ask for a week and wants a model that can plan. One
    setting could not serve both.
    """
    from fitness_ledger.coach import coach_provider, configure_adk_environment

    cfg = config(
        llm_provider="openai-compatible",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_model="deepseek/deepseek-v4-flash-0731",
        llm_api_key="k",
        coach_provider="gemini",
        gemini_api_key="gk",
    )

    assert coach_provider(cfg) == "gemini"
    # The dock's LLM_MODEL must not leak into the coach's Gemini request --
    # that would hand ADK a DeepSeek id and fail far from the cause.
    assert configure_adk_environment(cfg) == cfg.gemini_model


def test_coach_model_overrides_the_gemini_default():
    from fitness_ledger.coach import configure_adk_environment

    cfg = config(
        llm_provider="gemini",
        gemini_api_key="gk",
        coach_provider="gemini",
        coach_model="gemini-3.5-flash",
    )

    assert configure_adk_environment(cfg) == "gemini-3.5-flash"


def test_every_coach_transport_is_bounded():
    """Neither LiteLLM model may be built without a timeout and a retry cap.

    `llm._limits` exists so "a provider cannot be left unbounded by omission",
    and the coach was that omission: it reaches its provider through ADK, never
    through `llm.py`, so both models ran on LiteLLM's 600-second default.
    Found by watching an eval pass hold at zero CPU for 45 seconds, 42 minutes
    into a twenty-minute run.

    Asserted over both constructors together, so a third one added later is
    caught by the same test rather than needing someone to remember.
    """
    pytest.importorskip("litellm", reason="coach extra not installed")
    from dataclasses import replace

    from fitness_ledger.coach import _openai_compatible_model, fallback_model

    cfg = replace(
        fallback_config(),
        llm_provider="openai-compatible",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
        llm_api_key="not-a-real-key",
        coach_timeout_seconds=90.0,
        llm_max_retries=2,
    )

    for build in (_openai_compatible_model, fallback_model):
        args = build(cfg)._additional_args
        assert args.get("timeout") == 90.0, build.__name__
        assert args.get("num_retries") == 2, build.__name__


def test_a_zero_timeout_hands_back_to_the_sdk_default():
    """The escape hatch `llm._limits` has, for a local model that is
    legitimately slow -- Ollama on CPU can take minutes."""
    pytest.importorskip("litellm", reason="coach extra not installed")
    from dataclasses import replace

    from fitness_ledger.coach import _limits

    assert "timeout" not in _limits(replace(fallback_config(), coach_timeout_seconds=0))


def test_only_a_quota_refusal_triggers_the_fallback():
    """A malformed proposal is a real fault. Retrying it elsewhere would hide
    the cause behind a second bill."""
    from fitness_ledger.coach import is_quota_error

    assert is_quota_error(Exception("429 RESOURCE_EXHAUSTED"))
    assert is_quota_error(Exception("You exceeded your current quota"))
    assert not is_quota_error(ValueError("2 validation errors for StrengthProposal"))
    assert not is_quota_error(TimeoutError("read timeout"))


def test_the_fallback_is_separate_from_the_dock_provider():
    """Sharing LLM_* would mean you could not run a free primary and a paid
    backstop, which is the entire point of having one."""
    from dataclasses import replace

    from fitness_ledger.coach import fallback_model
    from fitness_ledger.config import Config

    dock_only = replace(
        fallback_config(coach_fallback_base_url=None, coach_fallback_api_key=None,
                        coach_fallback_model=None),
        llm_provider="openai-compatible",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
        llm_api_key="k",
    )
    assert fallback_model(dock_only) is None


def test_secrets_never_reach_a_repr():
    """A dataclass prints every field, so one traceback anywhere -- an API 500,
    a CLI crash, a failing assertion -- puts an API key into a log or a bug
    report. It happened once, in pytest output."""
    import re

    from fitness_ledger.config import Config

    text = repr(Config.load())
    assert not re.search(r"sk-[A-Za-z0-9-]{8,}|AIza[A-Za-z0-9_-]{8,}", text), text[:200]
    for field in ("gemini_api_key", "llm_api_key", "coach_fallback_api_key"):
        assert field not in text
