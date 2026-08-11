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
    assert configure_adk_environment(config(gemini_api_key="k")) == "gemini-2.5-flash"


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
    assert model == "gemini-2.5-flash"


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
    """The default path must not start depending on LiteLLM."""
    from fitness_ledger.coach import configure_adk_environment
    from fitness_ledger.config import Config

    assert isinstance(configure_adk_environment(Config.load()), str)


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
