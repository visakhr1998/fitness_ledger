"""Natural-language goal intake.

Two things are being pinned here, and only one of them is about parsing.

The first is that the **red-flag check is not the model's decision**. It runs on
the raw text before a transport is ever built, so there is no arrangement of
words that reaches the model and comes back as a training goal. The test that
matters most in this file is the one asserting the model is never called.

The second is that model output goes through `Goal` like everything else. A
proposal is not trusted because it came back in a tool call.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from fitness_ledger import intake, llm
from fitness_ledger.config import Config
from guardrails import assert_no_directive_health_language


def config() -> Config:
    return replace(Config.load(), llm_provider="gemini", gemini_api_key="test-key")


class Stub:
    """A transport that returns one prepared turn and records that it was used."""

    def __init__(self, turn: llm.Turn) -> None:
        self._turn = turn
        self.asked: list[str] = []

    def ask(self, question: str) -> None:
        self.asked.append(question)

    def record(self, results) -> None:  # pragma: no cover - never reached here
        raise AssertionError("intake takes one turn and does not dispatch tools")

    async def turn(self) -> llm.Turn:
        return self._turn


def proposal_turn(**arguments) -> llm.Turn:
    return llm.Turn(
        text="",
        tool_calls=[llm.ToolCall(id="1", name="propose", arguments=dict(arguments))],
    )


def run(monkeypatch, turn, text):
    """Parse `text` against a stubbed model, returning (proposal, stub)."""
    stub = Stub(turn)
    monkeypatch.setattr(llm, "build", lambda *a, **k: stub)
    return asyncio.run(intake.parse(config(), text, today="2026-08-29")), stub


# --- the safety trigger ----------------------------------------------------


def test_a_red_flag_never_reaches_the_model(monkeypatch):
    """The point of a deterministic trigger: no model call happens at all.

    A model can be argued out of firing a safety rule. This asserts there is no
    conversation to have -- `llm.build` would raise if it were touched.
    """

    def explode(*args, **kwargs):
        raise AssertionError("the model must not be called after a red flag")

    monkeypatch.setattr(llm, "build", explode)
    result = asyncio.run(
        intake.parse(
            config(),
            "My Achilles tendon feels like it's snapping during heavy squats",
        )
    )

    assert result["safety"] == ["snapping"]
    assert result["goals"] == []
    assert result["constraints"] == []
    assert result["message"] == intake.SAFETY_REFERRAL


def test_the_referral_is_a_constant_not_generated_text():
    # If this were model output it would drift, and the one reply that must not
    # soften into advice is this one.
    assert "physiotherapist or doctor" in intake.SAFETY_REFERRAL
    assert "not created any goals" in intake.SAFETY_REFERRAL


def test_an_ache_the_user_manages_is_not_a_red_flag():
    """The line is an ache someone is managing versus a symptom nobody should be.

    "My knee hurts on Wednesdays" is a scheduling fact the user has already
    adapted to, and it becomes a constraint. Refusing on every mention of
    discomfort teaches people not to mention it.
    """
    assert intake.red_flags("my knee hurts on Wednesdays") == []
    assert intake.red_flags("legs are sore after Tuesday") == []
    assert intake.red_flags("my bench press is stuck at 80kg") == []


@pytest.mark.parametrize(
    "text",
    [
        "my shoulder pops every rep",
        "hand goes numb when I deadlift",
        "knee gave way on the stairs",
        "ankle is swollen",
        "felt a sharp pain in my back",
    ],
)
def test_red_flags_fire_on_symptoms_that_need_a_clinician(text):
    assert intake.red_flags(text) != []


@pytest.mark.parametrize(
    "text",
    [
        # give way / give out, every inflection of both particles
        "my knee gives way when I squat",
        "my knee give way under load",
        "my knee giving way on the stairs",
        "my knee gave way on the stairs",
        "my knee gives out when I squat",
        "my knee giving out under load",
        "my knee gave out on the stairs",
        # swell, every inflection plus the irregular participle
        "my knee swells up after squats",
        "my knee swelled after squats",
        "there is swelling in my knee",
        "my ankle is swollen",
        # the same class, found by auditing the rest of the list
        "my knee locks up mid-rep",
        "my knee locked up mid-rep",
        "my foot tingles after long runs",
        "tingling in my foot after long runs",
        "I heard a pop in my shoulder",
        "I felt a snap in my hamstring",
    ],
)
def test_every_inflection_of_a_red_flag_fires(text):
    """The bug this file exists to prevent a repeat of.

    A UAT pass on 2026-09-04 walked through two entries: "gives way" and
    "swells" matched nothing, so the text reached the model and came back as
    something to plan around. The list held "giving way"/"gave way" and
    "swollen"/"swelling", and the test above uses exactly those forms -- which
    is why it passed while the guard was open.

    Parameterised over the whole family rather than one form each, because a
    guard that only catches the phrasing its author thought of is not a
    deterministic guard.
    """
    assert intake.red_flags(text) != [], f"red flag missed: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        # A personal record, not an injury. This is why "broke" is deliberately
        # not a red flag beside "broken" -- see the note in intake.py.
        "I broke my bench PR last week",
        "broke through a plateau on squats",
        # Ordinary soreness the user is managing, not a symptom to refer on.
        "I get sore knees after long runs",
        "my knee hurts on Wednesdays",
        "legs are always tight the day after",
    ],
)
def test_training_vocabulary_does_not_trip_the_guard(text):
    """The cost of widening the patterns is false positives, and a referral
    fired at someone celebrating a PR teaches them to stop using the box."""
    assert intake.red_flags(text) == [], f"false positive: {text!r}"


def test_the_referral_does_not_read_as_medical_instruction():
    # It declines to plan and points elsewhere. It must not diagnose or
    # prescribe on the way past.
    for phrase in ("you should", "i recommend", "overtraining", "deload"):
        assert phrase not in intake.SAFETY_REFERRAL.lower()


# --- extraction ------------------------------------------------------------


def test_the_headline_case_becomes_two_goals_and_a_constraint(monkeypatch):
    """The example the feature exists for."""
    result, stub = run(
        monkeypatch,
        proposal_turn(
            goals=[
                {"type": "race_time", "subject": "5k", "target_value": 1320},
                {"type": "strength_1rm", "subject": "Bench Press", "target_value": 80},
            ],
            constraints=[{"weekday": 2, "kind": "no_high_impact", "reason": "knee"}],
        ),
        "5k under 22 mins, bench stuck at 80kg, knee hurts on Wednesdays",
    )

    assert [g["type"] for g in result["goals"]] == ["race_time", "strength_1rm"]
    assert result["constraints"][0]["weekday_name"] == "Wednesday"
    assert stub.asked == ["5k under 22 mins, bench stuck at 80kg, knee hurts on Wednesdays"]


def test_an_invalid_goal_is_reported_not_silently_dropped(monkeypatch):
    # A race goal with no distance is exactly what a model gets wrong, and a
    # vanished goal reads as the app ignoring you.
    result, _ = run(
        monkeypatch,
        proposal_turn(
            goals=[
                {"type": "race_time", "target_value": 14400},
                {"type": "consistency", "target_value": 4},
            ]
        ),
        "sub 4 marathon, train 4 times a week",
    )

    assert [g["type"] for g in result["goals"]] == ["consistency"]
    assert len(result["rejected"]) == 1
    assert "needs a subject" in result["rejected"][0]


def test_a_bad_constraint_is_rejected_by_the_model_layer(monkeypatch):
    result, _ = run(
        monkeypatch,
        proposal_turn(goals=[], constraints=[{"weekday": 9, "kind": "no_lifting"}]),
        "no lifting on funday",
    )
    assert result["constraints"] == []
    assert "weekday must be" in result["rejected"][0]


def test_nothing_extracted_still_returns_the_full_envelope(monkeypatch):
    # The UI must never branch on which keys exist.
    result, _ = run(monkeypatch, proposal_turn(goals=[]), "I like training")
    assert set(result) == {
        "goals", "constraints", "unclear", "rejected", "safety", "message",
    }


def test_no_tool_call_shows_the_models_prose_rather_than_an_empty_panel(monkeypatch):
    result, _ = run(
        monkeypatch,
        llm.Turn(text="I could not find a target in that."),
        "hello",
    )
    assert result["goals"] == []
    assert result["message"] == "I could not find a target in that."


def test_unclear_items_survive_rather_than_becoming_invented_goals(monkeypatch):
    # "Stuck at 80kg and want more" names no target. Forcing it into a goal
    # would be the model inventing a number.
    result, _ = run(
        monkeypatch,
        proposal_turn(goals=[], unclear=["wants to get stronger, no number given"]),
        "I want to get stronger",
    )
    assert result["goals"] == []
    assert result["unclear"] == ["wants to get stronger, no number given"]


# --- what the user is shown before saving ----------------------------------


def test_a_race_goal_is_described_as_a_clock_not_seconds():
    # Nobody can confirm "14400" is right.
    assert intake.describe_goal(
        {"type": "race_time", "subject": "marathon", "target_value": 14400}
    ) == "marathon in 4:00:00"
    assert intake.describe_goal(
        {"type": "race_time", "subject": "5k", "target_value": 1320}
    ) == "5k in 22:00"


def test_a_constraint_is_described_in_words():
    assert intake.describe_constraint(
        {"weekday": 2, "kind": "no_high_impact", "reason": "knee"}
    ) == "Wednesdays: no running or jumping (knee)"


def test_nothing_shown_to_the_user_directs_their_health():
    """The descriptions and the referral all pass the coach's guardrail."""
    assert_no_directive_health_language(
        intake.describe_constraint({"weekday": 2, "kind": "no_high_impact", "reason": "knee"}),
        context="constraint description",
    )
    assert_no_directive_health_language(
        intake.describe_goal({"type": "strength_1rm", "subject": "Bench", "target_value": 100}),
        context="goal description",
    )


def test_the_prompt_forbids_the_model_from_proposing_volumes():
    # Danger zone 1: the model extracts intent, never programming. Allocation
    # already makes this structurally impossible downstream; saying it here
    # stops a "train 20 sets a week" goal being offered in the first place.
    prompt = intake.build_system_prompt("2026-08-29")
    assert "Never propose training volumes" in prompt
    assert "You extract intent, not programming" in prompt
