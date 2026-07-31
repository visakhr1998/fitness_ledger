"""Double progression state per exercise.

Double progression: hold the weight until every working set reaches the top of
the rep range, then add load and drop back to the bottom of the range.

Rep ranges are not recorded in a Hevy workout -- a logged set knows what was
done, not what was intended -- so they are configuration with a global default
and per-exercise overrides. Inferring them from history looks clever and is
wrong: a heavy top set followed by a back-off set is indistinguishable from a
failed range attempt.

Like volume.py this is pure: dataclasses in, dataclasses out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import WORKING_SET_TYPES, SetEntry

# Load steps that are actually achievable given typical plate and pin sizes.
INCREMENT_BY_EQUIPMENT: dict[str, float] = {
    "barbell": 2.5,
    "dumbbell": 2.0,
    "machine": 5.0,
    "kettlebell": 4.0,
    "plate": 2.5,
    "resistance_band": 0.0,
    "suspension": 0.0,
    "none": 0.0,
    "other": 2.5,
}
DEFAULT_INCREMENT = 2.5


@dataclass(frozen=True)
class RepRange:
    low: int = 6
    high: int = 10

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"rep range {self.low}-{self.high} is inverted")


@dataclass
class SessionSets:
    """The working sets of one exercise on one day."""

    day: date
    weight_kg: float | None
    sets: list[SetEntry]

    @property
    def reps(self) -> list[int]:
        return [entry.reps for entry in self.sets if entry.reps is not None]

    @property
    def top_weight(self) -> float:
        return max((entry.weight_kg or 0.0) for entry in self.sets) if self.sets else 0.0

    @property
    def total_reps(self) -> int:
        return sum(self.reps)


@dataclass
class ProgressionState:
    """Where one exercise sits in its double-progression cycle."""

    exercise_template_id: str
    exercise_title: str
    rep_range: RepRange
    last_session: date | None = None
    working_weight_kg: float | None = None
    reps_at_working_weight: list[int] | None = None
    sessions_at_weight: int = 0
    ready_to_progress: bool = False
    suggested_weight_kg: float | None = None
    verdict: str = "no data"

    def as_dict(self) -> dict[str, object]:
        return {
            "exercise_template_id": self.exercise_template_id,
            "exercise": self.exercise_title,
            "rep_range": f"{self.rep_range.low}-{self.rep_range.high}",
            "last_session": self.last_session.isoformat() if self.last_session else None,
            "working_weight_kg": self.working_weight_kg,
            "reps": self.reps_at_working_weight or [],
            "sessions_at_weight": self.sessions_at_weight,
            "ready_to_progress": self.ready_to_progress,
            "suggested_weight_kg": self.suggested_weight_kg,
            "verdict": self.verdict,
        }


def group_sessions(sets: list[SetEntry], exercise_template_id: str) -> list[SessionSets]:
    """Working sets for one exercise, grouped by day, oldest first."""
    by_day: dict[date, list[SetEntry]] = {}
    for entry in sets:
        if entry.exercise_template_id != exercise_template_id:
            continue
        if entry.set_type not in WORKING_SET_TYPES:
            continue
        by_day.setdefault(entry.local_date, []).append(entry)
    return [
        SessionSets(day=day, weight_kg=None, sets=by_day[day]) for day in sorted(by_day)
    ]


def increment_for(equipment_category: str | None) -> float:
    if not equipment_category:
        return DEFAULT_INCREMENT
    return INCREMENT_BY_EQUIPMENT.get(equipment_category, DEFAULT_INCREMENT)


def progression_state(
    sets: list[SetEntry],
    exercise_template_id: str,
    exercise_title: str,
    rep_range: RepRange | None = None,
    equipment_category: str | None = None,
) -> ProgressionState:
    """Assess the most recent session against the rep range.

    Only sets at the session's top weight count toward the decision: a back-off
    set at a lighter load says nothing about whether the working weight is ready
    to go up.
    """
    rep_range = rep_range or RepRange()
    state = ProgressionState(
        exercise_template_id=exercise_template_id,
        exercise_title=exercise_title,
        rep_range=rep_range,
    )

    sessions = group_sessions(sets, exercise_template_id)
    if not sessions:
        return state

    latest = sessions[-1]
    top_weight = latest.top_weight
    top_sets = [entry for entry in latest.sets if (entry.weight_kg or 0.0) == top_weight]
    reps = [entry.reps for entry in top_sets if entry.reps is not None]

    state.last_session = latest.day
    state.working_weight_kg = top_weight or None
    state.reps_at_working_weight = reps

    # How long this working weight has been held, counting back from the latest.
    sessions_at_weight = 0
    for session in reversed(sessions):
        if session.top_weight == top_weight:
            sessions_at_weight += 1
        else:
            break
    state.sessions_at_weight = sessions_at_weight

    if not reps:
        state.verdict = "no working sets recorded"
        return state

    if min(reps) >= rep_range.high:
        state.ready_to_progress = True
        step = increment_for(equipment_category)
        state.suggested_weight_kg = round(top_weight + step, 2) if step else None
        state.verdict = (
            f"top of range on all {len(reps)} sets -- add {step:g} kg"
            if step
            else "top of range on all sets -- add reps or a set"
        )
    elif min(reps) < rep_range.low:
        state.verdict = f"below range ({min(reps)} < {rep_range.low}) -- hold or drop weight"
    else:
        state.verdict = "in range -- add reps before adding weight"

    return state


def stalled(
    sets: list[SetEntry], exercise_template_id: str, sessions_required: int = 3
) -> bool:
    """True when neither load nor total reps improved over the last N sessions.

    Both are checked because adding a rep at the same weight is progress even
    though the working weight has not moved.
    """
    sessions = group_sessions(sets, exercise_template_id)
    if len(sessions) < sessions_required:
        return False

    recent = sessions[-sessions_required:]
    baseline = recent[0]
    for session in recent[1:]:
        improved_load = session.top_weight > baseline.top_weight
        improved_reps = (
            session.top_weight == baseline.top_weight
            and session.total_reps > baseline.total_reps
        )
        if improved_load or improved_reps:
            return False
    return True


def main_lifts(
    sets: list[SetEntry], limit: int = 8, min_sessions: int = 3
) -> list[tuple[str, str]]:
    """The exercises worth tracking progression on, by working-set count.

    Occasional accessories produce noisy stall and progression signals, so an
    exercise has to appear across several sessions before it qualifies.
    """
    counts: dict[str, int] = {}
    titles: dict[str, str] = {}
    days: dict[str, set[date]] = {}
    for entry in sets:
        if entry.set_type not in WORKING_SET_TYPES or not entry.exercise_template_id:
            continue
        key = entry.exercise_template_id
        counts[key] = counts.get(key, 0) + 1
        titles[key] = entry.exercise_title
        days.setdefault(key, set()).add(entry.local_date)

    ranked = sorted(
        (key for key in counts if len(days[key]) >= min_sessions),
        key=lambda key: counts[key],
        reverse=True,
    )
    return [(key, titles[key]) for key in ranked[:limit]]
