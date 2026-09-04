"""Configuration for the fitness ledger.

Every machine-specific path lives in ``.env`` (git-ignored). No secrets belong in
this repo: the Hevy API key stays inside the hevy-mcp server's own dotenv, and the
Google Health OAuth token stays in the token file the health MCP server manages.
We only point at them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Config:
    """Settings, loaded from .env.

    **Secrets carry `repr=False`.** A dataclass prints every field, so without
    it one traceback anywhere -- an API 500, a CLI crash, a failing assertion --
    puts an API key into a log, a terminal, or a bug report. That happened once,
    in pytest output, which is exactly the kind of place nobody thinks to check.
    """

    """Runtime configuration.

    The tunables in the second block are the conventions the plan flags as
    "convention, not law" -- they are values so they can be changed and the
    effect measured, rather than constants buried in the math.
    """

    db_path: Path

    # --- data sources (stdio MCP servers) ---
    hevy_command: str
    hevy_args: list[str]
    hevy_env: dict[str, str] = field(repr=False)
    health_command: str
    health_args: list[str]
    health_env: dict[str, str] = field(repr=False)

    # --- tunable conventions ---
    secondary_weight: float = 0.5
    count_warmup_sets: bool = False
    local_utc_offset_minutes: int = 120
    week_starts_on: int = 0  # 0 = Monday
    # Default double-progression rep range; per-exercise overrides live in the DB.
    rep_range_low: int = 6
    rep_range_high: int = 10

    # --- model layer (optional; only the chat dock and `ledger ask` need it) ---
    # Which vendor answers. Blank means "whichever key is set", preferring the
    # free one. See llm.py for the transports.
    llm_provider: str | None = None

    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_model: str = "claude-opus-5"

    gemini_api_key: str | None = field(default=None, repr=False)
    # Not 2.5-flash: it returns 404 "no longer available to new users" on a
    # fresh key, so defaulting to it broke the first plan of every new clone
    # with an error that reads like a bad key.
    gemini_model: str = "gemini-3.6-flash"

    # Escape hatch for any other /chat/completions server (Groq, OpenRouter, a
    # self-hosted vLLM). Also overrides the base URL for the named providers.
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    # Reasoning models spend `max_tokens` on thinking before writing anything.
    # This app forbids the model from reasoning about numbers, so the default is
    # to switch it off where the provider supports it. "off" sends no parameter.
    llm_reasoning_effort: str | None = None
    # How long to wait for one model reply before giving up on it.
    #
    # Measured against gemini-3.6-flash on 2026-09-02: the *same* request five
    # times took 2.9s, 15.0s, 26.5s, 103.7s and 6.4s. The variance is the
    # provider's, not ours, and `reasoning_effort` caps the hint rather than the
    # clock. Without a timeout the SDK waits 600 seconds, which behind a spinner
    # is indistinguishable from a hang.
    #
    # Paired with one retry: a slow draw is usually followed by a fast one, so
    # cutting it short and asking again beats waiting out the tail.
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 1

    # The same bound for the coach, which reaches its provider through ADK and
    # LiteLLM rather than through `llm.py` -- so `llm._limits` never touched it
    # and a planning request was left on LiteLLM's 600-second default. That is
    # the omission `_limits` says it exists to prevent; the coach was simply a
    # third transport nobody counted.
    #
    # Larger than the dock's 30s on purpose, not by guesswork: a planning
    # request carries the whole exercise pool (~3.4k characters on real data)
    # plus a structured output schema, and a full generation is roughly three
    # of them -- the Week tab's own control starts apologising at 45 seconds.
    # 30 here would cut short requests that were going to succeed.
    coach_timeout_seconds: float = 120.0

    # A second provider for the coach only, used when the first refuses.
    # Planning is the one place a quota error is fatal rather than annoying:
    # the dock can say "ask again in a minute", but a week that will not
    # generate is just absent. Any OpenAI-compatible endpoint.
    coach_fallback_base_url: str | None = None
    coach_fallback_model: str | None = None
    coach_fallback_api_key: str | None = field(default=None, repr=False)

    @property
    def has_coach_fallback(self) -> bool:
        return bool(
            self.coach_fallback_base_url
            and self.coach_fallback_model
            and self.coach_fallback_api_key
        )

    @classmethod
    def load(cls) -> "Config":
        return cls(
            db_path=Path(
                os.environ.get("LEDGER_DB_PATH", PROJECT_ROOT / "data" / "ledger.db")
            ),
            hevy_command=_env("HEVY_MCP_COMMAND"),
            hevy_args=_split(os.environ.get("HEVY_MCP_ARGS", "")),
            hevy_env=_kv(os.environ.get("HEVY_MCP_ENV", "")),
            health_command=_env("HEALTH_MCP_COMMAND"),
            health_args=_split(os.environ.get("HEALTH_MCP_ARGS", "")),
            health_env=_kv(os.environ.get("HEALTH_MCP_ENV", "")),
            secondary_weight=float(os.environ.get("SECONDARY_WEIGHT", "0.5")),
            count_warmup_sets=_bool(os.environ.get("COUNT_WARMUP_SETS", "false")),
            local_utc_offset_minutes=int(os.environ.get("LOCAL_UTC_OFFSET_MINUTES", "120")),
            week_starts_on=int(os.environ.get("WEEK_STARTS_ON", "0")),
            rep_range_low=int(os.environ.get("REP_RANGE_LOW", "6")),
            rep_range_high=int(os.environ.get("REP_RANGE_HIGH", "10")),
            llm_provider=os.environ.get("LLM_PROVIDER") or None,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
            llm_base_url=os.environ.get("LLM_BASE_URL") or None,
            llm_model=os.environ.get("LLM_MODEL") or None,
            llm_api_key=os.environ.get("LLM_API_KEY") or None,
            coach_fallback_base_url=os.environ.get("COACH_FALLBACK_BASE_URL") or None,
            coach_fallback_model=os.environ.get("COACH_FALLBACK_MODEL") or None,
            coach_fallback_api_key=os.environ.get("COACH_FALLBACK_API_KEY") or None,
            llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT") or None,
            llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "30")),
            llm_max_retries=int(os.environ.get("LLM_MAX_RETRIES", "1")),
            coach_timeout_seconds=float(os.environ.get("COACH_TIMEOUT_SECONDS", "120")),
        )


def _split(raw: str) -> list[str]:
    """Split an argv string on '||' so Windows paths with spaces survive."""
    return [part for part in (p.strip() for p in raw.split("||")) if part]


def _kv(raw: str) -> dict[str, str]:
    """Parse 'KEY=value||KEY2=value2' into a dict."""
    out: dict[str, str] = {}
    for part in _split(raw):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip()] = value.strip()
    return out


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}
