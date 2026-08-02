"""Model layer.

The model gets tools that return already-computed state and is told, explicitly,
not to do arithmetic. Every number in an answer comes from the rules engine, so
"the LLM did the maths wrong" is not a failure mode this app has.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .config import Config
from .db import SQLiteRepository
from . import queries, sections

SYSTEM_PROMPT = """You are a training assistant with read access to one person's \
Hevy lifting log and Google Health data.

How volume is defined here, so your language matches the numbers:
- effective sets per muscle group per week =
  (sets where the muscle is primary) + {secondary_weight} x (sets where it is secondary)
- warmup sets are {warmup_policy}
- frequency = number of distinct days in the window that muscle was trained
- AEI (Aerobic Efficiency Index) = grade-adjusted metres travelled per heart beat.
  Higher is better. Grade comes from Minetti's cost curve over 25 m distance bins.
  It is only comparable between runs of similar length: a long run at high heart
  rate scores lower than a short easy one, so do not read that as lost fitness.
  Some runs are excluded for unreliable GPS; say so rather than ignoring them.
- weeks run {week_start_name} to {week_end_name}

Rules:
- Never calculate. Call a tool and report what it returns. If a number you need is
  not in a tool result, say so rather than deriving it.
- Today is {today}. "Last week" means the previous complete week, not the last 7 days.
- Be concise and concrete. Lead with the number asked for.
- You are an advisor, not an autopilot: you have read-only tools, and nothing you
  say changes the user's training log.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_volume",
        "description": "Effective sets per muscle group for a window, with target comparison and frequency.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "description": "this-week, last-week, last-N-weeks, last-N-days, YYYY-MM, or YYYY-MM-DD:YYYY-MM-DD",
                }
            },
            "required": ["window"],
        },
    },
    {
        "name": "get_muscle_volume",
        "description": "Effective sets, frequency and tonnage for a single muscle group in a window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "muscle_group": {"type": "string", "description": "e.g. chest, lats, quadriceps"},
                "window": {"type": "string"},
            },
            "required": ["muscle_group", "window"],
        },
    },
    {
        "name": "get_neglected",
        "description": "Muscle groups furthest below their weekly target in a window.",
        "input_schema": {
            "type": "object",
            "properties": {"window": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["window"],
        },
    },
    {
        "name": "get_volume_trend",
        "description": "Week-by-week effective sets, optionally for one muscle group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer"},
                "muscle_group": {"type": "string"},
            },
            "required": ["weeks"],
        },
    },
    {
        "name": "get_exercise_progress",
        "description": "Best working set and estimated 1RM per session for one lift over time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise": {"type": "string", "description": "exercise name, fuzzy matched"},
                "weeks": {"type": "integer"},
            },
            "required": ["exercise"],
        },
    },
    {
        "name": "find_exercise",
        "description": "Search the exercise catalog; returns ids, muscles worked and whether the user logs it.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_runs",
        "description": "Runs in a window with distance, duration, pace and average heart rate.",
        "input_schema": {
            "type": "object",
            "properties": {"window": {"type": "string"}},
            "required": ["window"],
        },
    },
    {
        "name": "get_health",
        "description": "Daily sleep minutes, resting heart rate and steps for a window.",
        "input_schema": {
            "type": "object",
            "properties": {"window": {"type": "string"}},
            "required": ["window"],
        },
    },
    # --- v0.3 -------------------------------------------------------------
    {
        "name": "get_run_section",
        "description": (
            "Running summary: Aerobic Efficiency Index (grade-adjusted metres per "
            "heart beat) per run and its trend, run counts, distance and average "
            "heart rate. Also lists runs excluded from AEI and why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "description": "e.g. last-90-days"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
        },
    },
    {
        "name": "get_gym_section",
        "description": "Effective sets per muscle group and tonnage per period against its average.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
        },
    },
    {
        "name": "get_vitals",
        "description": (
            "Age, height, weight, resting heart rate, VO2 max, max heart rate, "
            "Karvonen heart-rate zones and BMR. Says which figures are estimates."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_exercise_detail",
        "description": (
            "One exercise over a window: estimated 1RM per session, "
            "double-progression state, whether it is stalled, and volume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise": {"type": "string", "description": "name, fuzzy matched"},
                "window": {"type": "string"},
            },
            "required": ["exercise"],
        },
    },
    {
        "name": "list_exercises",
        "description": "Exercises the user actually trains, most-logged first.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def dispatch(
    repo: SQLiteRepository, config: Config, name: str, arguments: dict[str, Any]
) -> Any:
    """Route a tool call to the deterministic query layer."""
    if name == "get_volume":
        return queries.volume_report(repo, config, arguments["window"])
    if name == "get_muscle_volume":
        return queries.muscle_volume(
            repo, config, arguments["muscle_group"], arguments.get("window", "last-week")
        )
    if name == "get_neglected":
        return queries.neglected(
            repo, config, arguments.get("window", "last-week"), arguments.get("limit", 5)
        )
    if name == "get_volume_trend":
        return queries.volume_trend(
            repo, config, arguments.get("weeks", 8), arguments.get("muscle_group")
        )
    if name == "get_exercise_progress":
        return queries.exercise_progress(
            repo, config, arguments["exercise"], arguments.get("weeks", 12)
        )
    if name == "find_exercise":
        return queries.find_exercise(repo, arguments["query"])
    if name == "get_runs":
        return queries.run_log(repo, config, arguments.get("window", "last-4-weeks"))
    if name == "get_health":
        return queries.health_summary(repo, config, arguments.get("window", "last-2-weeks"))

    if name == "get_run_section":
        return sections.run_section(
            repo, config, arguments.get("window", "last-90-days"),
            arguments.get("start"), arguments.get("end"),
        )
    if name == "get_gym_section":
        return sections.gym_section(
            repo, config, arguments.get("window", "last-90-days"),
            arguments.get("start"), arguments.get("end"),
        )
    if name == "get_vitals":
        return sections.vitals_card(repo, config)
    if name == "list_exercises":
        # Trimmed: the model needs names and ids, not every muscle group.
        return [
            {"id": row["id"], "title": row["title"], "logged_sets": row["logged_sets"]}
            for row in sections.exercise_catalog(repo, only_logged=True)[:60]
        ]
    if name == "get_exercise_detail":
        matches = queries.find_exercise(repo, arguments["exercise"], limit=1)
        if not matches:
            return {"error": f"no exercise matching {arguments['exercise']!r}"}
        return sections.exercise_detail(
            repo, config, matches[0]["id"], arguments.get("window", "last-90-days")
        )

    return {"error": f"unknown tool {name}"}


def build_system_prompt(config: Config) -> str:
    week_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    start_name = week_days[config.week_starts_on]
    end_name = week_days[(config.week_starts_on + 6) % 7]
    return SYSTEM_PROMPT.format(
        secondary_weight=config.secondary_weight,
        warmup_policy="counted" if config.count_warmup_sets else "not counted",
        week_start_name=start_name,
        week_end_name=end_name,
        today=date.today().isoformat(),
    )


async def answer(
    repo: SQLiteRepository, config: Config, question: str, max_turns: int = 6
) -> str:
    """Answer a natural language question by letting the model call query tools."""
    if not config.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set, so `ask` is unavailable. "
            "Every other command works without it."
        )

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=config.anthropic_api_key)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for _ in range(max_turns):
        response = await client.messages.create(
            model=config.anthropic_model,
            max_tokens=1500,
            system=build_system_prompt(config),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                payload = dispatch(repo, config, block.name, block.input or {})
            except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                payload = {"error": str(exc)}
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, default=str),
                }
            )
        messages.append({"role": "user", "content": results})

    return "Gave up after too many tool calls without reaching an answer."
