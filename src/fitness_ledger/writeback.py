"""Hevy write-back.

The only part of this application that changes anything outside it, so it is
built as propose -> diff -> confirm -> write -> log, and the propose step never
touches Hevy.

Two facts shape the design:

- **Hevy has no delete endpoint.** Anything created here can only be removed by
  hand in the app, so an accidental write is not recoverable in software. The
  diff exists to make the write deliberate.
- **The rules engine already knows what to suggest.** Weights come from
  double-progression state, not from the model, so an approved routine carries
  the same numbers the dashboard shows.

Payload construction and diffing are pure; only the caller talks to the MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .db import SQLiteRepository
from .progression import RepRange, progression_state
from .queries import rep_ranges


@dataclass
class ProposedSet:
    type: str = "normal"
    weight_kg: float | None = None
    reps: int | None = None

    def as_payload(self) -> dict[str, Any]:
        return {"type": self.type, "weight_kg": self.weight_kg, "reps": self.reps}


@dataclass
class ProposedExercise:
    exercise_template_id: str
    title: str
    rest_seconds: int | None = 120
    notes: str | None = None
    sets: list[ProposedSet] = field(default_factory=list)
    rationale: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "exercise_template_id": self.exercise_template_id,
            "rest_seconds": self.rest_seconds,
            "notes": self.notes,
            "sets": [entry.as_payload() for entry in self.sets],
        }


@dataclass
class Proposal:
    title: str
    exercises: list[ProposedExercise]
    notes: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """The exact body hevy_create_routine will receive."""
        return {
            "title": self.title,
            "notes": self.notes,
            "exercises": [exercise.as_payload() for exercise in self.exercises],
        }

    def summary(self) -> str:
        sets = sum(len(exercise.sets) for exercise in self.exercises)
        return f"{self.title} — {len(self.exercises)} exercises, {sets} sets"


def build_routine(
    repo: SQLiteRepository,
    config: Config,
    title: str,
    exercise_ids: list[str],
    sets_per_exercise: int = 3,
) -> Proposal:
    """Draft a routine from current double-progression state.

    Each exercise is loaded at the weight the engine says comes next: the
    suggested step if every set hit the top of its range, otherwise the weight
    currently being worked. Nothing is invented.
    """
    templates = repo.get_templates()
    overrides = rep_ranges(repo, config)
    default = RepRange(config.rep_range_low, config.rep_range_high)

    # 12 weeks of history is enough to establish a working weight without
    # letting a long-abandoned load resurface.
    from datetime import date, timedelta

    end = date.today() + timedelta(days=1)
    sets = repo.get_sets(end - timedelta(weeks=12), end)

    exercises: list[ProposedExercise] = []
    for template_id in exercise_ids:
        template = templates.get(template_id)
        if template is None:
            continue
        rep_range = overrides.get(template_id, default)
        state = progression_state(
            sets, template_id, template.title,
            rep_range=rep_range,
            equipment_category=template.equipment_category,
        )

        if state.ready_to_progress and state.suggested_weight_kg:
            weight = state.suggested_weight_kg
            reps = rep_range.low
            rationale = (
                f"hit {rep_range.high} on every set at "
                f"{state.working_weight_kg:g} kg, so stepping up"
            )
        elif state.working_weight_kg:
            weight = state.working_weight_kg
            reps = max((state.reps_at_working_weight or [rep_range.low])[0], rep_range.low)
            rationale = f"holding {weight:g} kg until the top of the range"
        else:
            weight = None
            reps = rep_range.low
            rationale = "no recent history; weight left blank"

        exercises.append(
            ProposedExercise(
                exercise_template_id=template_id,
                title=template.title,
                sets=[ProposedSet(weight_kg=weight, reps=reps) for _ in range(sets_per_exercise)],
                notes=None,
                rationale=rationale,
            )
        )

    return Proposal(title=title, exercises=exercises)


def diff(proposal: Proposal, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """A reviewable diff. With no existing routine, everything is an addition."""
    rows: list[dict[str, Any]] = []

    current: dict[str, dict[str, Any]] = {}
    if existing:
        for exercise in existing.get("exercises") or []:
            current[exercise.get("exercise_template_id")] = exercise

    for exercise in proposal.exercises:
        before = current.pop(exercise.exercise_template_id, None)
        after_text = _describe(exercise)
        if before is None:
            rows.append({
                "change": "add", "exercise": exercise.title,
                "before": None, "after": after_text, "why": exercise.rationale,
            })
        else:
            before_text = _describe_existing(before)
            rows.append({
                "change": "same" if before_text == after_text else "change",
                "exercise": exercise.title,
                "before": before_text, "after": after_text, "why": exercise.rationale,
            })

    for leftover in current.values():
        rows.append({
            "change": "remove",
            "exercise": leftover.get("title", leftover.get("exercise_template_id")),
            "before": _describe_existing(leftover), "after": None,
            "why": "not in the proposed routine",
        })

    return {
        "rows": rows,
        "added": sum(1 for row in rows if row["change"] == "add"),
        "changed": sum(1 for row in rows if row["change"] == "change"),
        "removed": sum(1 for row in rows if row["change"] == "remove"),
        # Surfaced in the UI: the API cannot undo this.
        "warning": "Hevy has no delete endpoint. Anything created here must be removed by hand in the app.",
    }


def _describe(exercise: ProposedExercise) -> str:
    if not exercise.sets:
        return "no sets"
    first = exercise.sets[0]
    weight = f"{first.weight_kg:g} kg" if first.weight_kg else "bodyweight"
    return f"{len(exercise.sets)} x {first.reps} @ {weight}"


def _describe_existing(exercise: dict[str, Any]) -> str:
    sets = exercise.get("sets") or []
    if not sets:
        return "no sets"
    first = sets[0]
    weight = f"{first.get('weight_kg'):g} kg" if first.get("weight_kg") else "bodyweight"
    return f"{len(sets)} x {first.get('reps')} @ {weight}"
