"""Running the coach against a fixture week, once.

The expensive half of the eval. Everything here costs model requests, so two
properties matter more than they usually would:

**One run per fixture, reused by every assertion.** Ten fixtures against six
assertions each would be sixty runs if the assertions each triggered one. The
cache below makes it ten. On a free tier measured at 5 requests a minute this is
the difference between a suite that finishes and one that 429s halfway.

**Nothing is stored.** `assemble(persist=False)`, and each fixture gets its own
temporary database, so an eval never writes to the real ledger and never leaves
a plan behind that the next run would read as "last week's".

The assertions themselves live in `test_eval_coach.py` and are skipped unless
`RUN_COACH_EVALS=1`. Left on by default they would spend the daily quota every
time anyone ran `pytest`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import fixtures
from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import Plan

ENABLE_VAR = "RUN_COACH_EVALS"

# **Temperature 0 does not make this provider deterministic.** Measured on the
# same fixture, minutes apart, at temperature 0: one run made a single tool call
# and trained back; another made five and did not. Setting temperature was
# necessary and is not sufficient.
#
# So an eval failure here is evidence, not proof. A single red run means "this
# happened once", and the useful question is how often -- which is why the
# report below prints every fixture rather than stopping at the first failure.
# Do not tune an assertion until it passes; that turns the suite into a
# thermometer that only reads room temperature.

# The strength planner is given the pool and the deficit; one call for the pool
# is the expected shape, and the running planner has no tools at all. Anything
# above this means the context injection has stopped working.
MAX_TOOL_CALLS = 2

# Tools a correct run never touches. get_volume_vs_target is the sharp one: left
# to itself the agent called it with `this-week`, a barely-started week where
# every muscle reads a full target short, and planned against that.
FORBIDDEN_TOOLS = frozenset({"get_volume_vs_target", "get_neglected"})


def enabled() -> bool:
    return os.environ.get(ENABLE_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class EvalRun:
    """One fixture, planned. Everything an assertion might want to look at."""

    fixture: fixtures.Fixture
    week_start: date
    context: dict[str, Any]
    proposal: dict[str, Any]
    plan: Plan
    problems: list[str] = field(default_factory=list)
    unmet: dict[str, float] = field(default_factory=dict)
    unplaced: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tools_called(self) -> list[str]:
        return [step["tool"] for step in self.trace]

    @property
    def rationale(self) -> str:
        return self.plan.rationale

    @property
    def planned_muscles(self) -> set[str]:
        return {
            muscle
            for session in self.plan.sessions
            for exercise in session.exercises
            for muscle in exercise.targets
        }

    @property
    def planned_template_ids(self) -> set[str]:
        return {
            exercise.exercise_template_id
            for session in self.plan.sessions
            for exercise in session.exercises
        }

    def summary(self) -> str:
        """One line per fixture, for reading a whole run at a glance."""
        return (
            f"{self.fixture.name:20} {len(self.plan.sessions)} sessions"
            f" / {self.plan.total_sets:3} sets"
            f" / {len(self.trace)} tool calls"
            f" / {len(self.problems)} problems"
        )


_cache: dict[str, EvalRun] = {}


def run(name: str, tmp_root: Path) -> EvalRun:
    """Plan one fixture week. Cached, because each call costs model requests."""
    if name in _cache:
        return _cache[name]

    from dataclasses import replace

    from fitness_ledger.coach.agent import propose_week
    from fitness_ledger.coach.assembler import assemble
    from fitness_ledger.coach.context import gather_context, next_monday

    db = Path(tmp_root) / f"eval-{name}.db"
    config = replace(Config.load(), db_path=db)
    week = next_monday()

    with SQLiteRepository(db, 120) as repo:
        fixtures.build(repo, name)
        context = gather_context(repo, config, week)
        result = asyncio.run(propose_week(repo, config, week))
        assembled = assemble(repo, config, result, persist=False)

    _cache[name] = EvalRun(
        fixture=fixtures.BY_NAME[name],
        week_start=week,
        context=context,
        proposal=result.get("proposal") or {},
        plan=assembled["plan"],
        problems=assembled["problems"],
        unmet=assembled["unmet"],
        unplaced=assembled["unplaced"],
        trace=result.get("agent_trace") or [],
    )
    return _cache[name]


def deficits(context: dict[str, Any]) -> dict[str, float]:
    """What the coach was told is short, straight from the injected context."""
    return {
        row["muscle_group"]: row["sets_deficit"]
        for row in context["ledger_state"]["volume"]["muscles"]
        if (row["sets_deficit"] or 0) > 0
    }


def worst_deficits(context: dict[str, Any], count: int = 3) -> list[str]:
    """The muscles a correct plan is most obliged to address."""
    short = deficits(context)
    return sorted(short, key=lambda muscle: short[muscle], reverse=True)[:count]
