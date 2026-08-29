"""Domain model.

Deliberately plain dataclasses: the rules engine operates on these, never on
sqlite rows or Hevy JSON, so the math can be unit-tested without a database or a
network call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


# Set types Hevy records. Warmups are excluded from effective-set counting by
# default -- see Config.count_warmup_sets.
WORKING_SET_TYPES = frozenset({"normal", "failure", "dropset"})

LARGE_MUSCLES = frozenset(
    {"chest", "lats", "upper_back", "quadriceps", "hamstrings", "glutes", "shoulders"}
)


@dataclass(frozen=True)
class ExerciseTemplate:
    """A Hevy exercise, and the muscles it is credited to."""

    id: str
    title: str
    type: str
    primary_muscle_group: str
    secondary_muscle_groups: tuple[str, ...] = ()
    equipment_category: str | None = None
    is_custom: bool = False


@dataclass(frozen=True)
class SetEntry:
    """One logged set, flattened to what the volume math needs."""

    workout_id: str
    local_date: date
    exercise_template_id: str
    exercise_title: str
    set_type: str
    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None

    @property
    def tonnage_kg(self) -> float:
        if self.weight_kg is None or self.reps is None:
            return 0.0
        return self.weight_kg * self.reps


@dataclass(frozen=True)
class Workout:
    id: str
    title: str
    start_time: datetime
    end_time: datetime
    local_date: date
    description: str | None = None
    routine_id: str | None = None


@dataclass(frozen=True)
class Run:
    """An exercise data point from Google Health, kept for the run log."""

    id: str
    start_time: datetime
    local_date: date
    exercise_type: str
    distance_m: float | None = None
    active_duration_s: float | None = None
    calories_kcal: float | None = None
    steps: int | None = None
    avg_heart_rate: float | None = None
    active_zone_minutes: int | None = None

    @property
    def pace_seconds_per_km(self) -> float | None:
        if not self.distance_m or not self.active_duration_s:
            return None
        return self.active_duration_s / (self.distance_m / 1000.0)


@dataclass(frozen=True)
class VolumeTarget:
    muscle_group: str
    sets_per_week: float
    frequency_per_week: int = 2

    @property
    def size_class(self) -> str:
        return "large" if self.muscle_group in LARGE_MUSCLES else "small"


# --- what the coach plans toward -------------------------------------------

GOAL_TYPES = frozenset(
    {"strength_1rm", "running_volume", "running_aei", "consistency", "race_time"}
)
GOAL_STATUSES = frozenset({"active", "achieved", "abandoned"})

# Race distances a `race_time` goal can name, in kilometres. A closed set
# rather than a free number because the pace work that reads these needs a
# distance it can look up, and "sub-4 marathon" is the shape people state
# goals in. The values are the real race distances, not rounded.
RACE_DISTANCES_KM = {
    "5k": 5.0,
    "10k": 10.0,
    "half_marathon": 21.0975,
    "marathon": 42.195,
}

# A declared unavailability is a fact the user stated. An inferred one is this
# app's guess from a planned session with no logged match. The coach must be
# able to tell them apart, because it should explain itself differently: "you
# said you were away" is not "it looks like you missed this".
AVAILABILITY_SOURCES = frozenset({"declared", "inferred"})


@dataclass(frozen=True)
class Goal:
    """Something the user is training toward.

    Distinct from a :class:`VolumeTarget`, which is a weekly maintenance level.
    A target says "keep chest at 14 sets a week"; a goal says "get bench to
    100 kg". The coach needs both: targets define the deficit it works from,
    goals decide which deficits are worth prioritising.
    """

    type: str
    target_value: float  # kg for strength, km for volume, *seconds* for race_time
    subject: str | None = None  # exercise for strength, race distance for race_time
    target_date: date | None = None
    status: str = "active"
    id: int | None = None  # assigned by storage
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        # Validated here rather than at the CLI so every entry point gets it. A
        # typo'd type would otherwise store a goal nothing ever reads.
        if self.type not in GOAL_TYPES:
            raise ValueError(
                f"unknown goal type {self.type!r}; expected one of {sorted(GOAL_TYPES)}"
            )
        if self.status not in GOAL_STATUSES:
            raise ValueError(
                f"unknown goal status {self.status!r}; expected one of {sorted(GOAL_STATUSES)}"
            )
        if self.type == "strength_1rm" and not self.subject:
            raise ValueError("a strength_1rm goal needs a subject (the exercise)")
        # A race goal without a distance is not a goal: "under four hours" is
        # meaningless until you say four hours of what, and every pace derived
        # from it needs the distance to divide by.
        if self.type == "race_time":
            if not self.subject:
                raise ValueError(
                    "a race_time goal needs a subject (the race distance); "
                    f"expected one of {sorted(RACE_DISTANCES_KM)}"
                )
            if self.subject not in RACE_DISTANCES_KM:
                raise ValueError(
                    f"unknown race distance {self.subject!r}; "
                    f"expected one of {sorted(RACE_DISTANCES_KM)}"
                )
            if self.target_value <= 0:
                raise ValueError("a race_time goal needs a target time in seconds")

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "subject": self.subject,
            "target_value": self.target_value,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RunningTarget:
    """Weekly running maintenance level.

    Deliberately shaped like :class:`VolumeTarget` -- an amount plus a
    frequency -- so the existing window-scaling logic applies unchanged: four
    weeks of runs are compared against four weeks of target.

    Without this the priority ranking's third rank ("runs on track") has
    nothing to measure, and so cannot be protected when the week is squeezed.
    """

    distance_km_per_week: float
    sessions_per_week: int = 2

    def as_dict(self) -> dict:
        return {
            "distance_km_per_week": self.distance_km_per_week,
            "sessions_per_week": self.sessions_per_week,
        }


@dataclass(frozen=True)
class Availability:
    """Whether a given day can be trained on.

    Only exceptions are stored. A day with no row is available, so declaring
    availability is never required -- the user only ever records the days they
    lose. `local_date` rather than `date` to match every other date-bearing
    model here, all of which are calendar days at the configured offset.
    """

    local_date: date
    available: bool = False
    reason: str | None = None
    source: str = "declared"

    def __post_init__(self) -> None:
        if self.source not in AVAILABILITY_SOURCES:
            raise ValueError(
                f"unknown availability source {self.source!r}; "
                f"expected one of {sorted(AVAILABILITY_SOURCES)}"
            )

    def as_dict(self) -> dict:
        return {
            "date": self.local_date.isoformat(),
            "available": self.available,
            "reason": self.reason,
            "source": self.source,
        }


# What a standing restriction forbids. Typed rather than free text because the
# planner has to act on it: "no high impact on Wednesdays" must stop a run
# being placed there while still allowing a lift, and prose cannot be checked.
CONSTRAINT_KINDS = frozenset({"no_high_impact", "no_lifting", "no_intervals"})

WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


@dataclass(frozen=True)
class RecurringConstraint:
    """A standing restriction on one weekday, every week.

    Deliberately *not* :class:`Availability`, which records a specific date the
    user lost. The two look similar and mean opposite things: a lost Thursday
    is an exception to correct for once, while "my knee hurts on Wednesdays" is
    a rule that outlives any particular week. Collapsing them would make a
    standing medical restriction indistinguishable from a work meeting, and the
    planner should treat those differently -- one is negotiable and one is not.

    A constraint narrows what a day can hold; it does not remove the day. That
    is why `kind` exists rather than a boolean: a knee that dislikes running is
    no reason to skip bench.
    """

    weekday: int  # 0 = Monday, matching date.weekday()
    kind: str
    reason: str | None = None
    id: int | None = None  # assigned by storage

    def __post_init__(self) -> None:
        if self.kind not in CONSTRAINT_KINDS:
            raise ValueError(
                f"unknown constraint kind {self.kind!r}; "
                f"expected one of {sorted(CONSTRAINT_KINDS)}"
            )
        if not 0 <= self.weekday <= 6:
            raise ValueError(f"weekday must be 0 (Monday) to 6 (Sunday), got {self.weekday}")

    @property
    def weekday_name(self) -> str:
        return WEEKDAY_NAMES[self.weekday]

    def forbids_running(self) -> bool:
        return self.kind in {"no_high_impact", "no_intervals"}

    def forbids_lifting(self) -> bool:
        return self.kind == "no_lifting"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "weekday": self.weekday,
            "weekday_name": self.weekday_name,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass
class MuscleVolume:
    """Computed volume for one muscle group over one window."""

    muscle_group: str
    effective_sets: float = 0.0
    primary_sets: int = 0
    secondary_sets: int = 0
    tonnage_kg: float = 0.0
    days: set[date] = field(default_factory=set)

    @property
    def frequency(self) -> int:
        return len(self.days)


@dataclass(frozen=True)
class Insight:
    """Something the analysis pass noticed, for the user to interpret.

    Insights are surfaced, never acted on: nothing downstream changes training
    because one of these fired.
    """

    rule: str
    severity: str  # "warn" | "info"
    subject: str  # muscle group or exercise this is about
    message: str
    detected_at: date
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "detected_at": self.detected_at.isoformat(),
            "data": self.data,
        }


@dataclass
class VolumeRollup:
    """Effective sets per muscle group across a closed-open date window."""

    start: date
    end: date  # exclusive
    muscles: dict[str, MuscleVolume] = field(default_factory=dict)
    working_sets: int = 0
    workouts: int = 0

    def get(self, muscle_group: str) -> MuscleVolume:
        return self.muscles.get(muscle_group, MuscleVolume(muscle_group))

    def sorted_muscles(self) -> list[MuscleVolume]:
        return sorted(
            self.muscles.values(), key=lambda m: m.effective_sets, reverse=True
        )


# --- what the coach produces ------------------------------------------------

PLAN_STATUSES = frozenset({"proposed", "approved", "superseded", "rejected"})
SESSION_KINDS = frozenset({"lift", "run"})


@dataclass(frozen=True)
class PlannedExercise:
    """One exercise in a planned session, with its set count.

    The agent's own output type carries no set count -- it has nowhere to put
    one, on purpose. This is the assembled side: the number here was computed
    from the tool-reported deficit, never proposed by a model.
    """

    exercise_template_id: str
    title: str
    sets: int
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.sets < 1:
            raise ValueError(
                f"{self.title!r} planned with {self.sets} sets; an exercise with no "
                "sets should be dropped from the session instead"
            )

    def as_dict(self) -> dict:
        return {
            "exercise_template_id": self.exercise_template_id,
            "title": self.title,
            "sets": self.sets,
            "targets": list(self.targets),
        }


@dataclass(frozen=True)
class PlannedSession:
    """One day of the planned week."""

    local_date: date
    kind: str
    focus: str = ""
    exercises: tuple[PlannedExercise, ...] = ()
    distance_km: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in SESSION_KINDS:
            raise ValueError(
                f"unknown session kind {self.kind!r}; expected one of {sorted(SESSION_KINDS)}"
            )

    @property
    def total_sets(self) -> int:
        return sum(exercise.sets for exercise in self.exercises)

    @property
    def muscles(self) -> frozenset[str]:
        return frozenset(
            muscle for exercise in self.exercises for muscle in exercise.targets
        )

    def as_dict(self) -> dict:
        return {
            "date": self.local_date.isoformat(),
            "kind": self.kind,
            "focus": self.focus,
            "exercises": [exercise.as_dict() for exercise in self.exercises],
            "distance_km": self.distance_km,
            "total_sets": self.total_sets,
        }


@dataclass(frozen=True)
class Plan:
    """One proposed training week. Append-only.

    A revision is a new row that `supersedes` the old one, never an edit: the
    reason a week looked the way it did is part of the record, and the coach
    reads the previous plan to tell a new shortfall from a persistent one.

    `trade_offs` is its own field rather than prose buried in `rationale`,
    because when the week is squeezed something loses and that should be
    readable at a glance. `agent_trace` is stored from the first plan onward --
    it cannot be reconstructed afterwards and the trajectory eval needs it.
    """

    week_start: date
    sessions: tuple[PlannedSession, ...] = ()
    rationale: str = ""
    trade_offs: str = ""
    status: str = "proposed"
    supersedes: int | None = None
    agent_trace: tuple[dict, ...] = ()
    id: int | None = None
    generated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in PLAN_STATUSES:
            raise ValueError(
                f"unknown plan status {self.status!r}; expected one of {sorted(PLAN_STATUSES)}"
            )

    @property
    def total_sets(self) -> int:
        return sum(session.total_sets for session in self.sessions)

    @property
    def lift_sessions(self) -> tuple[PlannedSession, ...]:
        return tuple(s for s in self.sessions if s.kind == "lift")

    @property
    def run_sessions(self) -> tuple[PlannedSession, ...]:
        return tuple(s for s in self.sessions if s.kind == "run")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "week_start": self.week_start.isoformat(),
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "status": self.status,
            "supersedes": self.supersedes,
            "rationale": self.rationale,
            "trade_offs": self.trade_offs,
            "sessions": [session.as_dict() for session in self.sessions],
            "total_sets": self.total_sets,
            "agent_trace": list(self.agent_trace),
        }
