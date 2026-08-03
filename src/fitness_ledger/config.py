"""Configuration for the fitness ledger.

Every machine-specific path lives in ``.env`` (git-ignored). No secrets belong in
this repo: the Hevy API key stays inside the hevy-mcp server's own dotenv, and the
Google Health OAuth token stays in the token file the health MCP server manages.
We only point at them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    """Runtime configuration.

    The tunables in the second block are the conventions the plan flags as
    "convention, not law" -- they are values so they can be changed and the
    effect measured, rather than constants buried in the math.
    """

    db_path: Path

    # --- data sources (stdio MCP servers) ---
    hevy_command: str
    hevy_args: list[str]
    hevy_env: dict[str, str]
    health_command: str
    health_args: list[str]
    health_env: dict[str, str]

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

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    # Escape hatch for any other /chat/completions server (Groq, OpenRouter, a
    # self-hosted vLLM). Also overrides the base URL for the named providers.
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    # Reasoning models spend `max_tokens` on thinking before writing anything.
    # This app forbids the model from reasoning about numbers, so the default is
    # to switch it off where the provider supports it. "off" sends no parameter.
    llm_reasoning_effort: str | None = None

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
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            llm_base_url=os.environ.get("LLM_BASE_URL") or None,
            llm_model=os.environ.get("LLM_MODEL") or None,
            llm_api_key=os.environ.get("LLM_API_KEY") or None,
            llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT") or None,
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
