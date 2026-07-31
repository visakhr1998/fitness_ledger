"""Rules engine: volume, frequency, and progression state.

This module is deterministic and has no dependencies on the database, the MCP
clients, or a model. Everything downstream is a view over what is computed here,
so this is the part that must be correct.

    volume[muscle] = SUM(working sets where muscle is primary)
                   + secondary_weight * SUM(working sets where muscle is secondary)

    frequency[muscle] = count of distinct local dates where volume[muscle] > 0
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .models import (
    WORKING_SET_TYPES,
    ExerciseTemplate,
    MuscleVolume,
    SetEntry,
    VolumeRollup,
    VolumeTarget,
)

# Literature-informed starting points, in effective sets per week. These are
# defaults to be adjusted by the user, not prescriptions.
DEFAULT_TARGETS: dict[str, float] = {
    "chest": 14,
    "lats": 14,
    "upper_back": 14,
    "shoulders": 12,
    "quadriceps": 14,
    "hamstrings": 12,
    "glutes": 12,
    "biceps": 10,
    "triceps": 10,
    "abdominals": 10,
    "calves": 10,
    "traps": 8,
    "lower_back": 8,
    "forearms": 6,
    "abductors": 6,
    "adductors": 6,
}

# Muscle groups Hevy uses that are not strength-volume targets.
NON_VOLUME_MUSCLES = frozenset({"cardio", "full_body", "other", "neck"})


def default_targets() -> dict[str, VolumeTarget]:
    return {
        muscle: VolumeTarget(muscle, sets, frequency_per_week=2)
        for muscle, sets in DEFAULT_TARGETS.items()
    }


def is_working_set(entry: SetEntry, count_warmup_sets: bool = False) -> bool:
    """Warmups do not count as effective volume unless explicitly enabled."""
    if count_warmup_sets:
        return True
    return entry.set_type in WORKING_SET_TYPES


def week_start(day: date, week_starts_on: int = 0) -> date:
    """Start of the week containing ``day``. week_starts_on: 0 = Monday."""
    return day - timedelta(days=(day.weekday() - week_starts_on) % 7)


def week_window(day: date, week_starts_on: int = 0) -> tuple[date, date]:
    """Closed-open [start, end) window for the week containing ``day``."""
    start = week_start(day, week_starts_on)
    return start, start + timedelta(days=7)


def compute_volume(
    sets: list[SetEntry],
    templates: dict[str, ExerciseTemplate],
    start: date,
    end: date,
    *,
    secondary_weight: float = 0.5,
    count_warmup_sets: bool = False,
) -> VolumeRollup:
    """Effective sets per muscle group over the closed-open window [start, end).

    Sets whose exercise template is unknown are skipped rather than guessed at;
    ``unmapped_templates`` reports them so a sync gap shows up as a gap instead of
    silently deflating a muscle group's volume.
    """
    rollup = VolumeRollup(start=start, end=end)
    workout_ids: set[str] = set()

    for entry in sets:
        if not (start <= entry.local_date < end):
            continue
        if not is_working_set(entry, count_warmup_sets):
            continue
        template = templates.get(entry.exercise_template_id)
        if template is None:
            continue

        rollup.working_sets += 1
        workout_ids.add(entry.workout_id)

        primary = template.primary_muscle_group
        if primary and primary not in NON_VOLUME_MUSCLES:
            muscle = rollup.muscles.setdefault(primary, MuscleVolume(primary))
            muscle.effective_sets += 1.0
            muscle.primary_sets += 1
            muscle.tonnage_kg += entry.tonnage_kg
            muscle.days.add(entry.local_date)

        for secondary in template.secondary_muscle_groups:
            if not secondary or secondary in NON_VOLUME_MUSCLES:
                continue
            if secondary == primary:
                continue
            muscle = rollup.muscles.setdefault(secondary, MuscleVolume(secondary))
            muscle.effective_sets += secondary_weight
            muscle.secondary_sets += 1
            muscle.tonnage_kg += entry.tonnage_kg * secondary_weight
            muscle.days.add(entry.local_date)

    rollup.workouts = len(workout_ids)
    return rollup


def unmapped_templates(
    sets: list[SetEntry], templates: dict[str, ExerciseTemplate]
) -> dict[str, str]:
    """Template ids present in logged sets but missing from the local catalog."""
    missing: dict[str, str] = {}
    for entry in sets:
        if entry.exercise_template_id not in templates:
            missing[entry.exercise_template_id] = entry.exercise_title
    return missing


def window_weeks(rollup: VolumeRollup) -> float:
    """Length of a rollup's window in weeks, floored at one week."""
    days = (rollup.end - rollup.start).days
    return max(days / 7.0, 1.0)


def coverage(
    rollup: VolumeRollup, targets: dict[str, VolumeTarget]
) -> list[dict[str, object]]:
    """Volume and frequency against target, one row per targeted muscle group.

    Targets are per week, so they are scaled to the window's length: comparing a
    four-week total against a one-week target would report 257% of target for
    what is actually 64% of it.
    """
    weeks = window_weeks(rollup)
    rows: list[dict[str, object]] = []
    for muscle, target in targets.items():
        actual = rollup.get(muscle)
        scaled_target = target.sets_per_week * weeks
        rows.append(
            {
                "muscle_group": muscle,
                "effective_sets": round(actual.effective_sets, 2),
                "sets_per_week": round(actual.effective_sets / weeks, 2),
                "target_sets": round(scaled_target, 1),
                "target_sets_per_week": target.sets_per_week,
                "sets_deficit": round(max(0.0, scaled_target - actual.effective_sets), 2),
                "pct_of_target": (
                    round(100 * actual.effective_sets / scaled_target)
                    if scaled_target
                    else None
                ),
                "frequency": actual.frequency,
                "target_frequency": round(target.frequency_per_week * weeks),
                "tonnage_kg": round(actual.tonnage_kg, 1),
                "weeks": round(weeks, 2),
            }
        )
    rows.sort(key=lambda r: (r["pct_of_target"] is None, r["pct_of_target"]))
    return rows


def weekly_series(
    sets: list[SetEntry],
    templates: dict[str, ExerciseTemplate],
    start: date,
    end: date,
    *,
    secondary_weight: float = 0.5,
    count_warmup_sets: bool = False,
    week_starts_on: int = 0,
) -> list[VolumeRollup]:
    """One rollup per week across [start, end), oldest first."""
    series: list[VolumeRollup] = []
    cursor = week_start(start, week_starts_on)
    while cursor < end:
        nxt = cursor + timedelta(days=7)
        series.append(
            compute_volume(
                sets,
                templates,
                cursor,
                nxt,
                secondary_weight=secondary_weight,
                count_warmup_sets=count_warmup_sets,
            )
        )
        cursor = nxt
    return series


# --- progression -----------------------------------------------------------


def estimate_1rm(weight_kg: float, reps: int, formula: str = "epley") -> float:
    """Estimated one-rep max. Both formulas degrade badly above ~12 reps."""
    if reps <= 0 or weight_kg <= 0:
        return 0.0
    if reps == 1:
        return weight_kg
    if formula == "brzycki":
        if reps >= 37:
            return 0.0
        return weight_kg * 36.0 / (37.0 - reps)
    return weight_kg * (1.0 + reps / 30.0)


def best_set_per_session(
    sets: list[SetEntry], exercise_template_id: str, formula: str = "epley"
) -> list[dict[str, object]]:
    """Top estimated-1RM working set per day for one exercise, oldest first."""
    by_day: dict[date, dict[str, object]] = {}
    for entry in sets:
        if entry.exercise_template_id != exercise_template_id:
            continue
        if entry.set_type not in WORKING_SET_TYPES:
            continue
        if entry.weight_kg is None or entry.reps is None:
            continue
        e1rm = estimate_1rm(entry.weight_kg, entry.reps, formula)
        current = by_day.get(entry.local_date)
        if current is None or e1rm > current["e1rm"]:
            by_day[entry.local_date] = {
                "date": entry.local_date,
                "weight_kg": entry.weight_kg,
                "reps": entry.reps,
                "e1rm": round(e1rm, 1),
            }
    return [by_day[day] for day in sorted(by_day)]
