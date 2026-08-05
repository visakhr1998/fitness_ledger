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
    {"strength_1rm", "running_volume", "running_aei", "consistency"}
)
GOAL_STATUSES = frozenset({"active", "achieved", "abandoned"})

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
    target_value: float
    subject: str | None = None  # exercise name for strength goals, else None
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
