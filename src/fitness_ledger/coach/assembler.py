"""From a proposal to a plan.

The agent hands back exercises and days. This turns that into a week with set
counts, checks it against the hard constraints, and stores it.

The division of labour is the whole point: `planning.py` does the arithmetic and
is pure; this module knows about the repository and the settings table and does
none of the maths itself. Anything here that looked like a calculation would be
a calculation happening outside the tested rules engine.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import Config
from ..db import SQLiteRepository
from ..models import Plan
from ..planning import allocate, validate
from ..queries import plan_adherence, planning_preferences  # noqa: F401  (re-export)


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


def weekly_targets(ledger_state: dict[str, Any]) -> dict[str, float]:
    """Sets per week per muscle -- the amount a plan should deliver.

    Scaled to the planning window by `volume.coverage`, which for `last-week`
    is one week, so these are weekly figures as they stand.
    """
    muscles = (ledger_state or {}).get("volume", {}).get("muscles", [])
    return {
        row["muscle_group"]: row["target_sets"] or 0.0
        for row in muscles
        if (row.get("target_sets") or 0) > 0
    }


def pool_ids(exercise_pool: list[dict[str, Any]] | None) -> set[str]:
    """Template ids the plan may use. Tolerates either key the pool has used."""
    return {
        row.get("exercise_template_id") or row.get("id")
        for row in exercise_pool or []
        if row.get("exercise_template_id") or row.get("id")
    }


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
    prefs = planning_preferences(repo)

    ledger = result.get("ledger_state") or {}
    # The weekly running distance comes from the stored target, the same way
    # set counts come from the volume target. The agent picks the days.
    running_target = repo.get_running_target()
    allocation = allocate(
        with_targets(proposal.get("sessions") or [], result.get("exercise_pool")),
        weekly_targets(ledger),
        prefs,
        deficits=deficits(ledger),
        weekly_distance_km=(
            running_target.distance_km_per_week if running_target else None
        ),
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


def with_targets(
    sessions: list[dict[str, Any]], exercise_pool: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Fill in the muscles an exercise serves when the agent left them out.

    Allocation works from `targets`: an exercise serving nothing is short of
    nothing, gets zero sets, and is dropped. So an agent that names an exercise
    but forgets its muscles loses it silently -- observed in a real run, which
    proposed `Calf Raise` with `targets=[]` and ended up with a week of four
    sets.

    We already know the answer. The exercise came from the pool, and the pool
    carries the primary and secondary muscles for every entry. Looking it up is
    strictly better than discarding the choice, and it is not the agent doing
    arithmetic -- the muscles come from the catalog, same as everything else.
    """
    by_id = {
        row.get("exercise_template_id") or row.get("id"): row
        for row in exercise_pool or []
    }
    if not by_id:
        return sessions

    filled = []
    for session in sessions:
        exercises = []
        for exercise in session.get("exercises") or []:
            if not (exercise.get("targets") or []):
                known = by_id.get(exercise.get("exercise_template_id"))
                if known:
                    exercise = {
                        **exercise,
                        "targets": [
                            muscle
                            for muscle in (
                                known.get("primary_muscle_group"),
                                *(known.get("secondary_muscle_groups") or ()),
                            )
                            if muscle
                        ],
                    }
            exercises.append(exercise)
        filled.append({**session, "exercises": exercises})
    return filled
