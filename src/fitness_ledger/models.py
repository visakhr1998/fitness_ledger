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
