"""Synthetic ledgers with known-correct behaviour, for evaluating the coach.

An eval needs a week whose right answer is already known. These build one: a
repository seeded so that "back has been neglected three weeks" or "bench is
stalled" is *true of the data*, not merely asserted in a docstring.

Two rules hold everything together.

**Dates are relative to today, never absolute.** The coach reads `last-week`
through `parse_window`, which resolves against `date.today()`, and plans
`next_monday()`. A fixture pinned to a hard-coded 2026 date would drift out of
those windows and quietly stop testing anything. So every helper counts weeks
back from the current one, and the fixtures mean the same thing whenever they
run.

**A fixture asserts its own premise.** `test_fixtures.py` checks that each one
actually produces the ledger state it claims, because a fixture that has
silently stopped representing "back neglected" turns every eval built on it into
a test of nothing. That half needs no model and is where the value is: the
model-facing assertions can only be as good as the state they run against.

Kept out of the package, like `guardrails.py`: this is test scaffolding, not
behaviour anything ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import (
    Availability,
    ExerciseTemplate,
    Goal,
    RunningTarget,
)
from fitness_ledger.volume import default_targets, week_start

# A small catalog covering every muscle a fixture needs to neglect or train.
# Deliberately not the user's real 461 templates: an eval that depends on the
# catalog it happens to be run against is not reproducible.
CATALOG: tuple[ExerciseTemplate, ...] = (
    ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps", "shoulders"), "barbell"),
    ExerciseTemplate("INCLINE", "Incline Press", "weight_reps", "chest", ("shoulders",), "dumbbell"),
    ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", ("lats", "biceps"), "barbell"),
    ExerciseTemplate("PULLDOWN", "Lat Pulldown", "weight_reps", "lats", ("upper_back", "biceps"), "cable"),
    ExerciseTemplate("SQUAT", "Squat", "weight_reps", "quadriceps", ("glutes",), "barbell"),
    ExerciseTemplate("LEGPRESS", "Leg Press", "weight_reps", "quadriceps", ("glutes", "hamstrings"), "machine"),
    ExerciseTemplate("RDL", "Romanian Deadlift", "weight_reps", "hamstrings", ("glutes", "lower_back"), "barbell"),
    ExerciseTemplate("CALF", "Calf Raise", "weight_reps", "calves", (), "machine"),
    ExerciseTemplate("PRESS", "Overhead Press", "weight_reps", "shoulders", ("triceps",), "barbell"),
    ExerciseTemplate("CURL", "Biceps Curl", "weight_reps", "biceps", ("forearms",), "dumbbell"),
    ExerciseTemplate("PUSHDOWN", "Triceps Pushdown", "weight_reps", "triceps", (), "cable"),
    ExerciseTemplate("CRUNCH", "Crunch", "weight_reps", "abdominals", (), "none"),
    # Covering every default target matters: a muscle with no exercise in the
    # catalog shows as a permanent deficit the coach has no way to close, so
    # "all targets met" could never be true and every fixture would carry the
    # same three phantom gaps.
    ExerciseTemplate("SHRUG", "Shrug", "weight_reps", "traps", (), "barbell"),
    ExerciseTemplate("ABDUCT", "Hip Abduction", "weight_reps", "abductors", ("glutes",), "machine"),
    ExerciseTemplate("ADDUCT", "Hip Adduction", "weight_reps", "adductors", (), "machine"),
)

# Enough sets to clear the default 14/week target when done twice a week.
FULL_SESSION = 8


@dataclass
class Ledger:
    """A builder over one temporary repository.

    Every method returns self so a fixture reads as a description of the week
    it is creating rather than a sequence of statements.
    """

    repo: SQLiteRepository
    today: date = field(default_factory=date.today)

    def __post_init__(self) -> None:
        self.repo.upsert_templates(CATALOG)
        self.repo.set_targets(default_targets().values())

    # --- time ---------------------------------------------------------------

    def monday(self, weeks_ago: int) -> date:
        """Monday of the week `weeks_ago` complete weeks back. 1 = last week."""
        return week_start(self.today, 0) - timedelta(days=7 * weeks_ago)

    # --- lifting ------------------------------------------------------------

    def session(
        self, day: date, sets: dict[str, int], *, weight: float = 60.0, reps: int = 8
    ) -> "Ledger":
        """One logged workout: template id -> working sets."""
        self.repo.upsert_workout({
            "id": f"w-{day.isoformat()}-{'-'.join(sorted(sets))}",
            "title": "Session",
            "start_time": f"{day.isoformat()}T12:00:00+00:00",
            "end_time": f"{day.isoformat()}T13:15:00+00:00",
            "exercises": [
                {
                    "index": index,
                    "title": template_id.title(),
                    "exercise_template_id": template_id,
                    "sets": [
                        {"index": i, "type": "normal", "weight_kg": weight, "reps": reps}
                        for i in range(count)
                    ],
                }
                for index, (template_id, count) in enumerate(sorted(sets.items()))
            ],
        })
        return self

    def routine(
        self,
        weeks: int,
        sets: dict[str, int],
        *,
        days: tuple[int, int] = (0, 3),
        weight: float = 60.0,
        reps: int = 8,
    ) -> "Ledger":
        """The same session, twice a week, for the last `weeks` complete weeks.

        This is the baseline a fixture starts from: a person who trains. What
        each fixture then does is take something *away* from it, because a
        deficit is only meaningful against a habit.
        """
        for week in range(1, weeks + 1):
            for offset in days:
                self.session(self.monday(week) + timedelta(days=offset), sets, weight=weight, reps=reps)
        return self

    def stall(self, template_id: str, *, sessions: int = 3, weight: float = 80.0) -> "Ledger":
        """The same weight and reps for N sessions running -- what `stalled` looks for."""
        for index in range(sessions):
            day = self.monday(1) - timedelta(days=4 * index)
            self.session(day, {template_id: 3}, weight=weight, reps=8)
        return self

    def top_of_range(self, template_id: str, *, sessions: int = 2, reps: int = 10) -> "Ledger":
        """Every working set at the top of the rep range -- ready to progress."""
        for index in range(sessions):
            self.session(
                self.monday(1) + timedelta(days=3 * index),
                {template_id: 3},
                weight=70.0,
                reps=reps,
            )
        return self

    # --- running ------------------------------------------------------------

    def running_target(self, km_per_week: float = 25.0, sessions: int = 3) -> "Ledger":
        self.repo.set_running_target(RunningTarget(km_per_week, sessions))
        return self

    def run(self, day: date, km: float, *, heart_rate: float = 150.0) -> "Ledger":
        self.repo.upsert_runs([{
            "id": f"r-{day.isoformat()}-{km}",
            "start_time": f"{day.isoformat()}T07:00:00+00:00",
            "exercise_type": "RUNNING",
            "distance_m": km * 1000.0,
            "active_duration_s": km * 330.0,
            "avg_heart_rate": heart_rate,
        }])
        return self

    def runs(self, weeks: int, per_week: int, km: float) -> "Ledger":
        for week in range(1, weeks + 1):
            for index in range(per_week):
                self.run(self.monday(week) + timedelta(days=1 + 2 * index), km)
        return self

    # --- recovery -----------------------------------------------------------

    def sleep(self, minutes: float, *, days: int = 28, recent: float | None = None) -> "Ledger":
        """A sleep baseline, optionally with the last three nights shortened.

        `recovery_flag` needs at least seven baseline nights and three recent
        ones, so a fixture that wants the rule to fire has to supply both.
        """
        rows = []
        for offset in range(1, days + 1):
            day = self.today - timedelta(days=offset)
            value = recent if (recent is not None and offset <= 3) else minutes
            rows.append((day.isoformat(), "sleep_minutes_asleep", value, "min", "{}"))
        self.repo.upsert_health_daily(rows)
        return self

    # --- constraints --------------------------------------------------------

    def unavailable(self, *offsets: int, reason: str = "work") -> "Ledger":
        """Days of the week being planned that cannot be trained. 0 = Monday."""
        planned = week_start(self.today, 0) + timedelta(days=7)
        for offset in offsets:
            self.repo.set_availability(
                Availability(planned + timedelta(days=offset), available=False, reason=reason)
            )
        return self

    def goal(self, type: str = "strength_1rm", subject: str = "Bench Press", value: float = 100.0) -> "Ledger":
        self.repo.add_goal(Goal(type=type, target_value=value, subject=subject))
        return self


# --- the fixture weeks ------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    """One synthetic week, and what a correct plan does about it."""

    name: str
    premise: str
    expect: str
    build: Callable[[Ledger], Ledger]


def _trains_everything(ledger: Ledger, weeks: int = 6) -> Ledger:
    """A full-body habit that meets most targets. The control condition."""
    return ledger.routine(
        weeks,
        {"BENCH": FULL_SESSION, "ROW": FULL_SESSION, "SQUAT": FULL_SESSION,
         "RDL": 6, "CALF": 6, "PRESS": 5, "CURL": 4, "PUSHDOWN": 4, "CRUNCH": 6},
    )


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="back_neglected",
        premise="Back has had nothing for three weeks; everything else is trained.",
        expect="Lats and upper back are the largest deficits and the week must include pulling.",
        build=lambda l: l.routine(
            6, {"BENCH": FULL_SESSION, "SQUAT": FULL_SESSION, "CALF": 6, "PRESS": 5}
        ),
    ),
    Fixture(
        name="two_days_lost",
        premise="A normal week, but two days of the planned week are declared unavailable.",
        expect="Volume is protected over session count; trade_offs names what was dropped.",
        build=lambda l: _trains_everything(l).unavailable(1, 3),
    ),
    Fixture(
        name="bench_stalled",
        premise="Bench has not moved in three sessions at the same weight.",
        expect="The stall is visible in progression state; the week should not simply add bench volume.",
        build=lambda l: _trains_everything(l).stall("BENCH"),
    ),
    Fixture(
        name="all_targets_met",
        premise="Every muscle group is at or above target.",
        expect="No deficit to close. The plan is for progression, not more volume.",
        build=lambda l: l.routine(
            6,
            {"BENCH": 10, "ROW": 10, "PULLDOWN": 8, "SQUAT": 10, "LEGPRESS": 8,
             "RDL": 8, "CALF": 8, "PRESS": 8, "CURL": 8, "PUSHDOWN": 8, "CRUNCH": 8,
             "SHRUG": 8, "ABDUCT": 6, "ADDUCT": 6},
            days=(0, 2, 4),
        ),
    ),
    Fixture(
        name="poor_sleep",
        premise="Sleep has been ~2h below a four-week baseline for three nights.",
        expect="Recovery is reported as the user's own history. Never an instruction to rest.",
        build=lambda l: _trains_everything(l).sleep(450, recent=330),
    ),
    Fixture(
        name="no_gym_early_week",
        premise="Monday and Tuesday of the planned week are unavailable.",
        expect="Everything lands Wednesday onward; the week is compressed, not truncated.",
        build=lambda l: _trains_everything(l).unavailable(0, 1),
    ),
    Fixture(
        name="running_behind",
        premise="A 25 km / 3-session target, with one 5 km run logged last week.",
        expect="running_shortfall fires, and the running planner places runs on free days.",
        build=lambda l: _trains_everything(l).running_target(25.0, 3).run(
            l.monday(1) + timedelta(days=1), 5.0
        ),
    ),
    Fixture(
        name="ready_to_progress",
        premise="Every working set of squat at the top of the rep range.",
        expect="Progression state says ready; the plan should load rather than add sets.",
        build=lambda l: _trains_everything(l).top_of_range("SQUAT"),
    ),
    Fixture(
        name="no_history",
        premise="An empty ledger: no workouts, no runs, no targets met.",
        expect="Every muscle reads a full target short. The plan must not invent a history.",
        build=lambda l: l,
    ),
    Fixture(
        name="running_only",
        premise="Runs logged, no lifting at all, and a running target that is met.",
        expect="Every muscle is short; running needs no rescue.",
        build=lambda l: l.running_target(20.0, 2).runs(4, 2, 10.0),
    ),
)

BY_NAME = {fixture.name: fixture for fixture in FIXTURES}


def build(repo: SQLiteRepository, name: str, today: date | None = None) -> Ledger:
    """Seed `repo` with the named fixture."""
    ledger = Ledger(repo, today or date.today())
    return BY_NAME[name].build(ledger)
