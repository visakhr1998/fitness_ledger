"""The coach's shape and its instruction.

Nothing here calls a model. What can be checked without one is whether the
agent is *able* to break the rules -- and the most important rule is enforced
by the output schema having nowhere to put a violation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from fitness_ledger.coach.agent import (
    ADK_INTERNAL_CALLS,
    INSTRUCTION,
    PlannedExercise,
    PlannedSession,
    WeekProposal,
)
from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository


@pytest.fixture()
def bound(tmp_path):
    config = replace(
        Config.load(), db_path=tmp_path / "agent.db", gemini_api_key="test-key"
    )
    with SQLiteRepository(config.db_path, 120) as repo:
        yield repo, config


# --- the rule made structural ----------------------------------------------


def test_the_proposal_has_nowhere_to_put_a_set_count():
    """The locked decision, enforced by shape rather than by asking.

    The agent chooses exercises and days; the assembler computes sets from the
    deficit. A rule the model *could* break is one that eventually gets
    broken, so there is no field for it.
    """
    for model in (PlannedExercise, PlannedSession, WeekProposal):
        for name in model.model_fields:
            assert "set" not in name.lower(), f"{model.__name__}.{name}"
            assert "rep" not in name.lower(), f"{model.__name__}.{name}"


def test_the_proposal_has_nowhere_to_put_a_weight():
    # Weights come from progression state at assembly, for the same reason.
    for model in (PlannedExercise, PlannedSession):
        assert not [n for n in model.model_fields if "weight" in n.lower() or "kg" in n.lower()]


def test_an_exercise_must_carry_the_id_that_hevy_needs():
    # A title alone cannot be written back.
    assert PlannedExercise.model_fields["exercise_template_id"].is_required()


def test_trade_offs_exists_as_its_own_field():
    # Buried in prose, "what I gave up" is unreadable at a glance and
    # unassertable in the eval.
    assert "trade_offs" in WeekProposal.model_fields


def test_a_minimal_proposal_validates():
    proposal = WeekProposal(
        sessions=[
            PlannedSession(
                session_date="2026-08-10",
                kind="lift",
                exercises=[PlannedExercise(exercise_template_id="BENCH", title="Bench Press")],
            )
        ],
        rationale="Chest was 8 sets short.",
    )
    assert proposal.trade_offs == ""


# --- the instruction -------------------------------------------------------


def test_the_priority_ranking_is_stated_in_order():
    positions = [
        INSTRUCTION.index("volume per muscle group"),
        INSTRUCTION.index("full-body coverage"),
        INSTRUCTION.index("runs on track"),
        INSTRUCTION.index("session count"),
    ]
    assert positions == sorted(positions)


def test_the_no_arithmetic_rule_is_stated():
    assert "Never do arithmetic" in INSTRUCTION


def test_the_agent_is_told_not_to_choose_its_own_window():
    # The failure this fixed: left to itself it called get_volume_vs_target
    # with 'this-week', a barely-started week where every muscle reads as a
    # full target short, then planned against that.
    #
    # Whitespace-normalised: the instruction is hard-wrapped, so a phrase
    # assertion that ignores wrapping survives reflowing the prose.
    flat = " ".join(INSTRUCTION.split())
    assert "do not pick your own window" in flat
    assert "part-finished week" in flat


def test_the_health_boundary_is_stated():
    assert "may direct training" in INSTRUCTION
    assert "may not direct health" in INSTRUCTION


def test_the_instruction_injects_the_precomputed_deficit():
    # If this key stops being written to state, ADK raises at run time rather
    # than silently sending the model an empty deficit.
    for key in ("{week_start}", "{training_days}", "{goals}", "{deficit_summary}"):
        assert key in INSTRUCTION


# --- the trace -------------------------------------------------------------


def test_adk_plumbing_is_not_counted_as_a_tool_call():
    # set_model_response is how ADK delivers structured output. Counting it
    # would make every trajectory look like it made an extra fetch.
    assert "set_model_response" in ADK_INTERNAL_CALLS


# --- assembly --------------------------------------------------------------


def test_the_context_reader_runs_before_the_planner(bound):
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    coach = build_coach(repo, config, date(2026, 8, 10))

    assert [child.name for child in coach.sub_agents] == ["context_reader", "week_planner"]


def test_the_planner_cannot_wander_off(bound):
    # Nothing to hand off to yet, and an agent that can transfer occasionally
    # will.
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    planner = build_coach(repo, config).sub_agents[1]

    assert planner.disallow_transfer_to_parent
    assert planner.disallow_transfer_to_peers
    assert planner.output_schema is WeekProposal
