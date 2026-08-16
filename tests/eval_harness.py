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
import re
import time
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


# Measured, the hard way: gemini-3.5-flash-lite allows **15 requests a minute**.
# A run costs about three, so the harness can start roughly one run every 13
# seconds and no faster. Fired back to back the suite exhausts the minute in
# seconds and every fixture after the fifth fails -- and fails *as though the
# coach misbehaved*, which is the worse half of the problem.
MIN_SECONDS_BETWEEN_RUNS = float(os.environ.get("COACH_EVAL_PACE_SECONDS", "13"))

# Which provider to grade. "fallback" pins every run to the backstop, which is
# how you get a coherent baseline: with the fallback live, a suite silently
# grades whichever model happened to answer, and a 2-of-3 that is really
# "primary twice, backstop once" describes neither of them.
EVAL_MODEL = os.environ.get("COACH_EVAL_MODEL", "primary").strip().lower()
QUOTA_RETRIES = 3
DEFAULT_RETRY_WAIT = 25.0

# Stop after this many fixtures in a row fail on quota. A daily cap does not
# recover in twenty-five seconds, so retrying every remaining fixture only buys
# a slower way to learn the same thing: the first full run on gemini-3.6-flash
# spent **two hours and nine minutes** discovering that nothing would run.
QUOTA_GIVE_UP_AFTER = 2

_last_started = 0.0
_consecutive_quota_failures = 0


class QuotaExhausted(RuntimeError):
    """The API refused, so this fixture has no result -- of any kind.

    Distinct from an assertion failure on purpose. "The coach planned on a day
    it was told it could not use" and "the coach never got to plan" are
    different findings, and reporting the second as the first would send someone
    debugging a prompt over a rate limit.
    """


def _is_quota_error(exc: BaseException) -> bool:
    return "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc)


def _retry_after(exc: BaseException) -> float:
    """The server says how long to wait; believe it rather than guessing."""
    match = re.search(r"retry in ([\d.]+)s", str(exc))
    return float(match.group(1)) + 1.0 if match else DEFAULT_RETRY_WAIT


def _pace() -> None:
    """Hold the floor between runs so the minute is never spent at once."""
    global _last_started
    wait = MIN_SECONDS_BETWEEN_RUNS - (time.monotonic() - _last_started)
    if wait > 0:
        time.sleep(wait)
    _last_started = time.monotonic()


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
    # Which model actually produced this. With a fallback configured a suite can
    # silently grade two models at once, and a 2-of-3 score that is really
    # "primary twice, backstop once" describes neither of them.
    planned_by: str = "?"
    fell_back: bool = False

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
        model = self.planned_by.replace("openai/", "")
        return (
            f"{self.fixture.name:20} {len(self.plan.sessions)} sessions"
            f" / {self.plan.total_sets:3} sets"
            f" / {len(self.trace)} tools"
            f" / {len(self.problems)} problems"
            f" / {model}{'  (fell back)' if self.fell_back else ''}"
        )


# Keyed by (fixture, attempt) so repeats are distinct runs but each is still
# fetched once however many assertions read it.
_cache: dict[tuple[str, int], EvalRun] = {}


def run(name: str, tmp_root: Path, attempt: int = 0) -> EvalRun:
    """Plan one fixture week, paced and retried. Cached: each call costs quota."""
    if (name, attempt) in _cache:
        return _cache[(name, attempt)]

    global _consecutive_quota_failures

    if _consecutive_quota_failures >= QUOTA_GIVE_UP_AFTER:
        raise QuotaExhausted(
            f"giving up: {_consecutive_quota_failures} fixtures in a row hit quota. "
            "Nothing here is measuring the coach."
        )

    last: BaseException | None = None
    for retry in range(QUOTA_RETRIES):
        _pace()
        try:
            planned = _plan_once(name, tmp_root, attempt)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is quota
            if not _is_quota_error(exc):
                raise
            last = exc
            if retry < QUOTA_RETRIES - 1:
                time.sleep(_retry_after(exc))
        else:
            _consecutive_quota_failures = 0
            return planned

    _consecutive_quota_failures += 1
    raise QuotaExhausted(
        f"{name} (attempt {attempt}) could not be planned after {QUOTA_RETRIES} "
        f"tries: {last}"
    )


def completed() -> int:
    """How many runs actually happened. Zero means the suite proved nothing."""
    return len(_cache)


def _plan_once(name: str, tmp_root: Path, attempt: int) -> EvalRun:
    from dataclasses import replace

    from fitness_ledger.coach.agent import propose_week
    from fitness_ledger.coach.assembler import assemble
    from fitness_ledger.coach.context import gather_context, next_monday

    db = Path(tmp_root) / f"eval-{name}-{attempt}.db"
    config = replace(Config.load(), db_path=db)
    week = next_monday()

    with SQLiteRepository(db, 120) as repo:
        fixtures.build(repo, name)
        context = gather_context(repo, config, week)
        pinned = None
        if EVAL_MODEL == "fallback":
            from fitness_ledger.coach import fallback_model

            pinned = fallback_model(config)
            if pinned is None:
                raise RuntimeError(
                    "COACH_EVAL_MODEL=fallback but no COACH_FALLBACK_* is configured"
                )
        result = asyncio.run(propose_week(repo, config, week, model=pinned))
        assembled = assemble(repo, config, result, persist=False)

    _cache[(name, attempt)] = EvalRun(
        fixture=fixtures.BY_NAME[name],
        week_start=week,
        context=context,
        proposal=result.get("proposal") or {},
        plan=assembled["plan"],
        problems=assembled["problems"],
        unmet=assembled["unmet"],
        unplaced=assembled["unplaced"],
        trace=result.get("agent_trace") or [],
        planned_by=str(result.get("planned_by") or "?"),
        fell_back=bool(result.get("fell_back_from")),
    )
    return _cache[(name, attempt)]


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


# --- scoring ----------------------------------------------------------------

# How many times a judgement fixture is replanned. Three is the smallest number
# that distinguishes "always", "usually" and "never"; raise it when the quota is
# known to allow more, since the resolution of a k-of-N score is entirely N.
REPEATS = int(os.environ.get("COACH_EVAL_REPEATS", "3"))


@dataclass(frozen=True)
class Score:
    """How often a judgement held, rather than whether it held once.

    A judgement that survives four runs in five is *information*. Turning that
    into a pass/fail assertion discards it in both directions: green hides the
    one failure, red hides the four successes.
    """

    name: str
    fixture: str
    passed: int
    total: int
    failures: tuple[str, ...] = ()

    @property
    def ratio(self) -> str:
        return f"{self.passed}/{self.total}"

    @property
    def never(self) -> bool:
        """Zero for N is not drift. Something is actually wrong."""
        return self.total > 0 and self.passed == 0

    def line(self) -> str:
        mark = "  <-- never" if self.never else ("  <-- weak" if self.passed < self.total else "")
        return f"  {self.name:34} {self.fixture:18} {self.ratio:>5}{mark}"


def repeat(name: str, tmp_root: Path, times: int | None = None) -> list[EvalRun]:
    """Plan the same fixture several times.

    A run lost to quota is dropped rather than counted as a failure: scoring
    2-of-3 when the third never happened would report a judgement problem that
    is really a budget one.
    """
    runs = []
    for attempt in range(times or REPEATS):
        try:
            runs.append(run(name, tmp_root, attempt))
        except QuotaExhausted:
            break
        except Exception as exc:  # noqa: BLE001 - a failed run is a failed sample
            # One bad run must not destroy every score. This lives in a
            # session fixture, so an exception here took out all six scores
            # across all four fixtures -- twelve runs of evidence discarded
            # because one proposal failed schema validation.
            #
            # A model that returns something unusable has failed the judgement.
            # That is a data point, not a reason to stop measuring.
            failures.setdefault(name, []).append(f"attempt {attempt}: {exc}")
    return runs


# Runs that raised rather than returning a plan, kept so the report can say how
# many samples were lost and why.
failures: dict[str, list[str]] = {}


def models_used(runs: list[EvalRun]) -> str:
    """Every distinct model behind a set of runs, so a blended score says so."""
    seen = sorted({run.planned_by.replace("openai/", "") for run in runs})
    return ", ".join(seen)


def score(label: str, fixture: str, runs: list[EvalRun], holds) -> Score:
    """Count how often `holds(run)` is true, keeping the failures readable."""
    failures = []
    passed = 0
    for index, one in enumerate(runs):
        try:
            ok = bool(holds(one))
        except Exception as exc:  # noqa: BLE001 - a raising predicate is a failure
            ok = False
            failures.append(f"run {index}: {type(exc).__name__}: {exc}")
        else:
            if not ok:
                failures.append(f"run {index}: {one.summary().strip()}")
        passed += int(ok)
    return Score(label, fixture, passed, len(runs), tuple(failures))
