"""From a proposal to a plan.

The agent hands back exercises and days. This turns that into a week with set
counts, checks it against the hard constraints, and stores it.

The division of labour is the whole point: `planning.py` does the arithmetic and
is pure; this module knows about the repository and the settings table and does
none of the maths itself. Anything here that looked like a calculation would be
a calculation happening outside the tested rules engine.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..config import Config
from ..db import SQLiteRepository
from ..models import WORKING_SET_TYPES, Plan
from ..planning import Adherence, Preferences, adherence, allocate, validate

# Preference keys in user_settings. Absent means "no opinion", and the defaults
# in Preferences apply.
SETTING_KEYS = {
    "max_sets_per_session": "max_sets_per_session",
    "min_sets_per_exercise": "min_sets_per_exercise",
    "max_sets_per_exercise": "max_sets_per_exercise",
    "min_rest_days_same_muscle": "min_rest_days_same_muscle",
}
ALLOW_RUN_AFTER_LEGS_KEY = "allow_run_after_leg_day"


def preferences(repo: SQLiteRepository) -> Preferences:
    """Read the hard constraints, falling back to the documented defaults."""
    settings = repo.get_settings()
    values: dict[str, Any] = {}
    for field, key in SETTING_KEYS.items():
        raw = settings.get(key)
        if raw not in (None, ""):
            try:
                values[field] = int(float(raw))
            except (TypeError, ValueError):
                # A malformed setting should not take the planner down; the
                # default is a safe week, not a wrong one.
                continue

    raw_run = settings.get(ALLOW_RUN_AFTER_LEGS_KEY)
    if raw_run not in (None, ""):
        values["allow_run_after_leg_day"] = str(raw_run).strip().lower() not in {
            "0", "false", "no", "off"
        }
    return Preferences(**values)


def deficits(ledger_state: dict[str, Any]) -> dict[str, float]:
    """Sets short per muscle group, straight from the volume tool.

    Read, not computed: `sets_deficit` was produced by `volume.coverage` over
    one complete week, which is the only window a one-week plan can be measured
    against.
    """
    muscles = (ledger_state or {}).get("volume", {}).get("muscles", [])
    return {
        row["muscle_group"]: row["sets_deficit"] or 0.0
        for row in muscles
        if (row.get("sets_deficit") or 0) > 0
    }


def pool_ids(exercise_pool: list[dict[str, Any]] | None) -> set[str]:
    return {row["id"] for row in exercise_pool or []}


def assemble(
    repo: SQLiteRepository,
    config: Config,
    result: dict[str, Any],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Allocate sets, validate, and store the week.

    `result` is what `propose_week` returns. Returns the plan alongside the
    validation problems and the shortfall the week could not close -- the
    caller decides what to do about them, because a plan that breaks a
    constraint is still worth showing next to the reason.
    """
    proposal = result.get("proposal") or {}
    week_start = date.fromisoformat(result["week_start"])
    prefs = preferences(repo)

    allocation = allocate(
        proposal.get("sessions") or [],
        deficits(result.get("ledger_state") or {}),
        prefs,
    )
    problems = validate(
        allocation.sessions,
        pool_ids=pool_ids(result.get("exercise_pool")) or None,
        training_days=set(result.get("training_days") or []) or None,
        preferences=prefs,
    )

    plan = Plan(
        week_start=week_start,
        sessions=allocation.sessions,
        rationale=proposal.get("rationale", ""),
        trade_offs=_trade_offs(proposal.get("trade_offs", ""), allocation),
        agent_trace=tuple(result.get("agent_trace") or ()),
    )
    if persist:
        plan = repo.add_plan(plan)

    return {
        "plan": plan,
        "problems": problems,
        "unmet": allocation.unmet,
        "unplaced": list(allocation.unplaced),
    }


def _trade_offs(stated: str, allocation) -> str:
    """The agent's account of what it gave up, plus what the arithmetic says.

    Both, deliberately. The agent explains its intent and the allocator knows
    what the week actually delivers, and when they disagree the disagreement is
    the interesting part -- a rationale claiming a muscle was covered while the
    numbers say it is still four sets short is exactly the failure the plan
    warned about.
    """
    parts = [stated.strip()] if stated and stated.strip() else []

    if allocation.unmet:
        short = ", ".join(
            f"{muscle.replace('_', ' ')} short {sets:g}"
            for muscle, sets in sorted(allocation.unmet.items())
        )
        parts.append(f"Still short after allocation: {short}.")
    if allocation.unplaced:
        names = ", ".join(m.replace("_", " ") for m in allocation.unplaced)
        parts.append(f"No exercise in this week trains: {names}.")
    return " ".join(parts)


def plan_adherence(repo: SQLiteRepository, plan) -> Adherence:
    """How much of a stored plan was actually trained.

    The gathering half: reads the logged week out of the cache and hands it to
    the pure comparison in planning.py. Everything below the read is arithmetic
    the rules engine owns.
    """
    if plan is None or not plan.sessions:
        return Adherence(week_start=date.min)

    start = min(session.local_date for session in plan.sessions)
    end = max(session.local_date for session in plan.sessions) + timedelta(days=1)

    logged_by_day: dict[date, dict[str, int]] = {}
    for entry in repo.get_sets(start, end):
        if entry.set_type not in WORKING_SET_TYPES or not entry.exercise_template_id:
            continue
        day = logged_by_day.setdefault(entry.local_date, {})
        day[entry.exercise_template_id] = day.get(entry.exercise_template_id, 0) + 1

    run_days = {
        run.local_date
        for run in repo.get_runs(start, end)
        if run.exercise_type in {"RUNNING", "TREADMILL"}
    }
    return adherence(plan.sessions, logged_by_day, run_days, today=date.today())
