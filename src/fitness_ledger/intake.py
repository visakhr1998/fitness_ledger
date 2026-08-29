"""Messy English in, proposed goals out.

Setting a goal here used to mean knowing that `strength_1rm` needs a
`--subject` and that a race time is stored in seconds. That is a schema, not a
question anyone asks themselves, and it kept goals to the CLI while the API and
its TypeScript client sat unused. This module is the model layer that closes
that gap: it reads a paragraph and proposes the structured records behind it.

Three properties hold it to the same line as the rest of the app:

- **It proposes; it never saves.** `parse` returns a proposal the user confirms
  in the UI, which then calls the existing goal and constraint endpoints. Same
  shape as Hevy write-back, for the same reason: nothing this app writes should
  be something the user did not look at first.

- **Structured output is a tool call, not JSON in prose.** The model is given
  one tool and its arguments *are* the result. Asking for a JSON reply instead
  is how the coach's fallback provider intermittently returns a markdown-fenced
  block that fails to parse -- a failure this app has already met once and does
  not need a second copy of.

- **Nothing skips validation.** Every proposed goal is built through
  :class:`Goal`, so model output is rejected by exactly the checks the CLI and
  the API use. What is rejected is reported, never silently dropped.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from typing import Any

from . import llm
from .config import Config
from .models import (
    CONSTRAINT_KINDS,
    GOAL_TYPES,
    RACE_DISTANCES_KM,
    WEEKDAY_NAMES,
    Goal,
    RecurringConstraint,
)

# Signs that belong to a clinician rather than a training plan. Deliberately
# *narrow*: these are red flags -- mechanical failure, nerve symptoms, a joint
# that will not hold -- and not the ordinary vocabulary of training.
#
# "My knee hurts on Wednesdays" is not on this list on purpose. It is a fact
# the user has already adapted to and wants planned around, so it becomes a
# constraint. A system that refuses every mention of discomfort is one people
# learn to route around by not mentioning it, which leaves the app knowing less
# about the body it is planning for. The line is between an ache someone is
# managing and a symptom nobody should be managing alone.
RED_FLAG_PATTERNS = (
    r"snapp?(?:ing|ed|s)",
    r"\bpop(?:s|ping|ped)\b",
    r"tearing",
    r"\btorn\b",
    r"\btear\b",
    r"sharp pain",
    r"stabbing",
    r"shooting pain",
    r"numb(?:ness)?",
    r"tingling",
    r"pins and needles",
    r"giving way",
    r"gave way",
    r"gave out",
    r"can.?t bear weight",
    r"cannot bear weight",
    r"can.?t walk",
    r"cannot walk",
    r"swollen",
    r"swelling",
    r"locked up",
    r"locking",
    r"dislocat",
    r"fracture",
    r"\bbroken\b",
)

# Returned verbatim when a red flag fires. A constant rather than model output
# because this is the one reply whose wording must not vary: it is the app
# declining to turn a symptom into a training goal, and a model asked to
# rephrase it would eventually soften it into advice.
SAFETY_REFERRAL = (
    "That reads like something to get looked at rather than planned around, so "
    "I have not created any goals from it.\n\n"
    "This app is a training ledger, not a clinician -- it has no way to tell a "
    "strain from something that needs imaging, and guessing is not a service it "
    "should offer. Please take it to a physiotherapist or doctor.\n\n"
    "Once you know what you are dealing with, come back and set the goal, or add "
    "a standing constraint for the days or movements you need to avoid."
)


def red_flags(text: str) -> list[str]:
    """Red-flag phrases in `text`, empty when there are none.

    Runs over the raw input before the model sees it, and returns the matches
    rather than a bool so the caller can say what tripped. Deterministic on
    purpose: a model can be argued out of firing a safety rule, and this one
    must not be arguable. That makes it locked core, not fluid margin.
    """
    lowered = text.lower()
    found: list[str] = []
    for pattern in RED_FLAG_PATTERNS:
        found.extend(match.group(0) for match in re.finditer(pattern, lowered))
    return found


SYSTEM_PROMPT = """You turn a person's description of their training goals into \
structured records. You have exactly one tool and you must call it.

Rules:
- Extract only what the person actually said. Do not invent a goal, a date, or a
  number they did not give. An empty list is a correct answer.
- Never propose training volumes, set counts or weekly mileage as a goal. Those
  are computed elsewhere from the goal. You extract intent, not programming.
- If something is stated but you cannot map it to a field, put it in `unclear`
  rather than forcing it into a goal.
- Do not comment on, explain, or give advice about health, injuries, pain or
  recovery. You are not asked to interpret any of it.
- But a day or a movement the person says does not work for them is a
  *scheduling fact*, and you should record it as a constraint. "My knee hurts on
  Wednesdays" is a Wednesday no_high_impact constraint with their own words as
  the reason. Recording that is not commenting on it -- say nothing about the
  knee beyond repeating what they told you.

Goal types:
- race_time: a target finish time for a race. `subject` is the distance, one of
  {race_distances}. `target_value` is the target time in SECONDS -- convert
  "under 4 hours" to 14400, "sub 22 minutes" to 1320.
- strength_1rm: a target one-rep max for one lift. `subject` is the exercise
  name as the person said it. `target_value` is kilograms; convert from pounds
  if needed. "Stuck at 80kg and want more" names no target, so it is `unclear`.
- running_volume: a target weekly distance in kilometres.
- running_aei: a target aerobic efficiency index. Rare; only if named.
- consistency: a target number of sessions per week.

Constraints are standing weekly restrictions, not one-off missed days.
`weekday` is 0 for Monday through 6 for Sunday. `kind` is one of \
{constraint_kinds}:
- no_high_impact: no running or jumping that day
- no_lifting: no resistance training that day
- no_intervals: easy running only, no hard efforts

Today is {today}."""


def build_tool() -> dict[str, Any]:
    """The single tool whose arguments are the extraction result.

    Declared in Anthropic's schema like every other tool in this app; `llm.py`
    translates it for whichever provider is configured.
    """
    return {
        "name": "propose",
        "description": (
            "Record the goals and constraints found in the person's description. "
            "Call this exactly once, even if you found nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goals": {
                    "type": "array",
                    "description": "One entry per goal actually stated.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": sorted(GOAL_TYPES)},
                            "subject": {
                                "type": "string",
                                "description": (
                                    "exercise name for strength_1rm, race distance "
                                    "for race_time, omitted otherwise"
                                ),
                            },
                            "target_value": {
                                "type": "number",
                                "description": "seconds for race_time, kg for strength_1rm",
                            },
                            "target_date": {
                                "type": "string",
                                "description": "YYYY-MM-DD, only if a date was given",
                            },
                        },
                        "required": ["type", "target_value"],
                    },
                },
                "constraints": {
                    "type": "array",
                    "description": "Standing weekly restrictions the person described.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "weekday": {
                                "type": "integer",
                                "description": "0 Monday to 6 Sunday",
                            },
                            "kind": {"type": "string", "enum": sorted(CONSTRAINT_KINDS)},
                            "reason": {"type": "string"},
                        },
                        "required": ["weekday", "kind"],
                    },
                },
                "unclear": {
                    "type": "array",
                    "description": "Anything stated that did not map to a field.",
                    "items": {"type": "string"},
                },
            },
            "required": ["goals"],
        },
    }


def build_system_prompt(today: str) -> str:
    return SYSTEM_PROMPT.format(
        race_distances=", ".join(sorted(RACE_DISTANCES_KM)),
        constraint_kinds=", ".join(sorted(CONSTRAINT_KINDS)),
        today=today,
    )


def _goal_from(raw: dict[str, Any]) -> tuple[Goal | None, str | None]:
    """One proposed goal, or the reason it was refused.

    Built through :class:`Goal` so the model gets no leniency the CLI does not.
    Returning the message rather than raising lets one bad entry be reported
    without discarding the good ones beside it.
    """
    try:
        target_date = raw.get("target_date")
        return (
            Goal(
                type=str(raw.get("type", "")),
                target_value=float(raw.get("target_value", 0)),
                subject=raw.get("subject") or None,
                target_date=date_cls.fromisoformat(target_date) if target_date else None,
            ),
            None,
        )
    except (ValueError, TypeError) as exc:
        return None, f"{raw!r}: {exc}"


def _constraint_from(raw: dict[str, Any]) -> tuple[RecurringConstraint | None, str | None]:
    try:
        return (
            RecurringConstraint(
                weekday=int(raw.get("weekday", -1)),
                kind=str(raw.get("kind", "")),
                reason=raw.get("reason") or None,
            ),
            None,
        )
    except (ValueError, TypeError) as exc:
        return None, f"{raw!r}: {exc}"


def empty_proposal(**overrides: Any) -> dict[str, Any]:
    """The proposal envelope. One shape for every outcome, so the UI never has
    to branch on which fields exist."""
    proposal: dict[str, Any] = {
        "goals": [],
        "constraints": [],
        "unclear": [],
        "rejected": [],
        "safety": None,
        "message": "",
    }
    proposal.update(overrides)
    return proposal


async def parse(config: Config, text: str, today: str | None = None) -> dict[str, Any]:
    """Read a description of someone's goals and propose the records behind it.

    Returns a proposal. Nothing is written -- the caller confirms, then posts to
    the existing goal and constraint endpoints.

    The red-flag check runs first and short-circuits: when it fires the model is
    never called, so there is no path on which a symptom becomes a training
    goal.
    """
    flags = red_flags(text)
    if flags:
        return empty_proposal(safety=flags, message=SAFETY_REFERRAL)

    system = build_system_prompt(today or date_cls.today().isoformat())
    transport = llm.build(config, system, [build_tool()])
    transport.ask(text)
    turn = await transport.turn()

    call = next((c for c in turn.tool_calls if c.name == "propose"), None)
    if call is None:
        # No tool call means nothing was extracted. The model's prose is the
        # most useful thing left, so it is shown rather than an empty panel.
        return empty_proposal(
            message=turn.text
            or "Nothing in that mapped to a goal. Try naming a target and a number."
        )

    args = call.arguments or {}
    goals: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    rejected: list[str] = []

    for raw in args.get("goals") or []:
        goal, problem = _goal_from(raw if isinstance(raw, dict) else {})
        if goal:
            goals.append(goal.as_dict())
        else:
            rejected.append(problem or "")

    for raw in args.get("constraints") or []:
        constraint, problem = _constraint_from(raw if isinstance(raw, dict) else {})
        if constraint:
            constraints.append(constraint.as_dict())
        else:
            rejected.append(problem or "")

    return empty_proposal(
        goals=goals,
        constraints=constraints,
        unclear=[str(item) for item in (args.get("unclear") or [])],
        rejected=rejected,
    )


def describe_goal(goal: dict[str, Any]) -> str:
    """One line a person can check before saving.

    The UI shows this rather than the raw record, because
    "race_time / marathon / 14400" is not something anyone can confirm is right.
    """
    kind = goal.get("type")
    subject = goal.get("subject") or ""
    value = float(goal.get("target_value") or 0)

    if kind == "race_time":
        total = int(value)
        hours, minutes, seconds = total // 3600, (total % 3600) // 60, total % 60
        clock = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
        return f"{subject.replace('_', ' ')} in {clock}"
    if kind == "strength_1rm":
        return f"{subject} one-rep max of {value:g} kg"
    if kind == "running_volume":
        return f"{value:g} km a week"
    if kind == "consistency":
        return f"{value:g} sessions a week"
    if kind == "running_aei":
        return f"aerobic efficiency index of {value:g}"
    return f"{kind} {subject} {value:g}".strip()


def describe_constraint(constraint: dict[str, Any]) -> str:
    labels = {
        "no_high_impact": "no running or jumping",
        "no_lifting": "no lifting",
        "no_intervals": "easy running only",
    }
    weekday = constraint.get("weekday")
    name = WEEKDAY_NAMES[weekday] if isinstance(weekday, int) and 0 <= weekday <= 6 else "?"
    label = labels.get(constraint.get("kind", ""), constraint.get("kind", ""))
    reason = f" ({constraint['reason']})" if constraint.get("reason") else ""
    return f"{name}s: {label}{reason}"
