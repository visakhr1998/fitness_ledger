"""The coach's shape and its instructions.

Nothing here calls a model. What can be checked without one is whether the
agents are *able* to break the rules -- and the most important rule is enforced
by the output schemas having nowhere to put a violation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from fitness_ledger.coach.agent import (
    ADK_INTERNAL_CALLS,
    RUNNING_INSTRUCTION,
    STRENGTH_INSTRUCTION,
    PlannedExercise,
    PlannedSession,
    RunningProposal,
    RunSession,
    StrengthProposal,
    merge_proposals,
    running_summary,
    strength_days,
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


def test_no_proposal_has_anywhere_to_put_a_set_count():
    """The locked decision, enforced by shape rather than by asking.

    The agents choose exercises and days; the assembler computes sets from the
    deficit. A rule the model *could* break is one that eventually gets broken,
    so there is no field for it -- in either planner's schema.
    """
    for model in (PlannedExercise, PlannedSession, StrengthProposal, RunSession, RunningProposal):
        for name in model.model_fields:
            assert "set" not in name.lower(), f"{model.__name__}.{name}"
            assert "rep" not in name.lower(), f"{model.__name__}.{name}"


def test_no_proposal_has_anywhere_to_put_a_weight():
    # Weights come from progression state at assembly, for the same reason.
    for model in (PlannedExercise, PlannedSession, RunSession):
        assert not [n for n in model.model_fields if "weight" in n.lower() or "kg" in n.lower()]


def test_the_lifting_schema_cannot_carry_a_distance():
    """A distance on a lifting session would be a run the assembler never sees
    as one, and the split exists precisely to keep the two apart."""
    assert not [n for n in PlannedSession.model_fields if "distance" in n.lower()]


def test_the_running_schema_cannot_carry_exercises():
    assert "exercises" not in RunSession.model_fields


def test_an_exercise_must_carry_the_id_that_hevy_needs():
    # A title alone cannot be written back.
    assert PlannedExercise.model_fields["exercise_template_id"].is_required()


def test_both_planners_have_their_own_trade_offs_field():
    # Buried in prose, "what I gave up" is unreadable at a glance and
    # unassertable in the eval.
    assert "trade_offs" in StrengthProposal.model_fields
    assert "trade_offs" in RunningProposal.model_fields


def test_a_minimal_proposal_validates():
    proposal = StrengthProposal(
        sessions=[
            PlannedSession(
                session_date="2026-08-10",
                exercises=[PlannedExercise(exercise_template_id="BENCH", title="Bench Press")],
            )
        ],
        rationale="Chest was 8 sets short.",
    )
    assert proposal.trade_offs == ""


# --- the instructions -------------------------------------------------------


def test_the_priority_ranking_is_stated_in_order():
    positions = [
        STRENGTH_INSTRUCTION.index("volume per muscle group"),
        STRENGTH_INSTRUCTION.index("full-body coverage"),
        STRENGTH_INSTRUCTION.index("runs on track"),
        STRENGTH_INSTRUCTION.index("session count"),
    ]
    assert positions == sorted(positions)


def test_the_no_arithmetic_rule_is_stated_to_both():
    assert "Never do arithmetic" in STRENGTH_INSTRUCTION
    assert "Never do arithmetic" in RUNNING_INSTRUCTION


def test_the_strength_planner_cannot_choose_its_own_window():
    """Once an instruction, now a fact: the tool is not offered at all.

    Left to itself it called get_volume_vs_target with 'this-week' -- a
    barely-started week where every muscle reads as a full target short -- and
    planned against that. Being told not to was not enough; three of ten runs
    did it anyway, which is why the instruction now describes the absence
    rather than forbidding the act.
    """
    flat = " ".join(STRENGTH_INSTRUCTION.split())
    assert "no tool to recompute it" in flat
    assert "part-finished week" in flat


def test_the_health_boundary_is_stated_to_both():
    for instruction in (STRENGTH_INSTRUCTION, RUNNING_INSTRUCTION):
        assert "may direct training" in instruction
        assert "may not direct health" in instruction


def test_the_strength_instruction_injects_the_precomputed_deficit():
    # If a key stops being written to state, ADK raises at run time rather than
    # silently sending the model an empty deficit.
    for key in ("{week_start}", "{training_days}", "{goals}", "{deficit_summary}"):
        assert key in STRENGTH_INSTRUCTION


def test_the_running_planner_is_told_lifting_comes_first():
    flat = " ".join(RUNNING_INSTRUCTION.split())
    assert "lifting week is already fixed" in flat
    assert "rather than displacing lifting" in flat


def test_the_running_planner_must_not_invent_a_target():
    """Without a target there is nothing to protect and nothing to plan toward,
    and a made-up distance would look exactly like a real one."""
    flat = " ".join(RUNNING_INSTRUCTION.split())
    assert "no running target, return no sessions" in flat
    assert "Do not invent a distance" in flat


# --- what the running planner is shown --------------------------------------


def test_strength_days_renders_the_shape_not_the_exercise_ids():
    """The running planner needs the shape of the week, not a wall of ids."""
    rendered = strength_days(
        {
            "strength_proposal": {
                "sessions": [
                    {
                        "session_date": "2026-08-10",
                        "focus": "upper",
                        "exercises": [{"exercise_template_id": "BENCH"}, {"exercise_template_id": "ROW"}],
                    }
                ]
            }
        }
    )

    assert "2026-08-10" in rendered and "upper" in rendered and "2 exercises" in rendered
    assert "BENCH" not in rendered


def test_strength_days_says_so_when_nothing_was_planned():
    assert "no lifting sessions" in strength_days({})


def test_running_summary_survives_an_empty_ledger():
    assert "no runs recorded" in running_summary({})


# --- merging ----------------------------------------------------------------


def strength_half():
    return {
        "sessions": [
            {"session_date": "2026-08-12", "focus": "lower", "exercises": [{"exercise_template_id": "SQ"}]},
            {"session_date": "2026-08-10", "focus": "upper", "exercises": [{"exercise_template_id": "BP"}]},
        ],
        "rationale": "chest and quads were short",
        "trade_offs": "calves untouched",
    }


def running_half():
    return {
        "sessions": [{"session_date": "2026-08-11", "focus": "easy", "distance_km": 6.0}],
        "rationale": "target is 12 km across two runs",
        "trade_offs": "",
    }


def test_merging_orders_the_week_by_date():
    merged = merge_proposals(strength_half(), running_half())

    assert [s["session_date"] for s in merged["sessions"]] == [
        "2026-08-10", "2026-08-11", "2026-08-12"
    ]


def test_merging_tags_each_session_with_its_kind():
    merged = merge_proposals(strength_half(), running_half())
    kinds = {s["session_date"]: s["kind"] for s in merged["sessions"]}

    assert kinds == {"2026-08-10": "lift", "2026-08-11": "run", "2026-08-12": "lift"}


def test_merging_keeps_the_two_rationales_apart():
    """When the planners disagree about what the week is for, that is worth
    being able to read."""
    merged = merge_proposals(strength_half(), running_half())

    assert "Lifting: chest and quads were short" in merged["rationale"]
    assert "Running: target is 12 km across two runs" in merged["rationale"]


def test_merging_omits_a_half_that_said_nothing():
    merged = merge_proposals(strength_half(), running_half())

    assert merged["trade_offs"] == "Lifting: calves untouched"


def test_merging_survives_a_planner_returning_nothing():
    """No running target means no running proposal, which is a valid week."""
    merged = merge_proposals(strength_half(), None)

    assert len(merged["sessions"]) == 2
    assert all(s["kind"] == "lift" for s in merged["sessions"])
    assert merge_proposals(None, None) == {"sessions": [], "rationale": "", "trade_offs": ""}


def test_merging_carries_the_day_but_not_a_distance():
    """The merge is structural: days and focus, never a number.

    A distance used to travel from the model through the merge into the stored
    plan unchanged -- the one figure in a week that the agent supplied and the
    assembler accepted, which the design forbids for sets, reps and weights.
    `planning.allocate` derives it from the stored RunningTarget now.
    """
    merged = merge_proposals(None, running_half())

    session = merged["sessions"][0]
    assert session["session_date"] == "2026-08-11"
    assert session["focus"] == "easy"
    assert "distance_km" not in session


# --- the trace -------------------------------------------------------------


def test_adk_plumbing_is_not_counted_as_a_tool_call():
    # set_model_response is how ADK delivers structured output. Counting it
    # would make every trajectory look like it made an extra fetch.
    assert "set_model_response" in ADK_INTERNAL_CALLS


# --- assembly --------------------------------------------------------------


def test_the_planners_run_in_order_behind_the_context_reader(bound):
    """Running placement depends on strength placement, so the order is the
    design rather than an accident of construction."""
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    coach = build_coach(repo, config, date(2026, 8, 10))

    assert [child.name for child in coach.sub_agents] == [
        "context_reader", "strength_planner", "running_planner"
    ]


def test_neither_planner_can_wander_off(bound):
    # Sequential delegation is the design; an agent that can transfer will
    # occasionally decide to run the other planner itself.
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    _, strength, running = build_coach(repo, config).sub_agents

    for planner in (strength, running):
        assert planner.disallow_transfer_to_parent
        assert planner.disallow_transfer_to_peers

    assert strength.output_schema is StrengthProposal
    assert running.output_schema is RunningProposal


def test_each_planner_writes_its_own_state_key(bound):
    """The assembler reads both; sharing one key would make the second
    planner silently overwrite the first."""
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    _, strength, running = build_coach(repo, config).sub_agents

    assert strength.output_key == "strength_proposal"
    assert running.output_key == "running_proposal"


def test_the_running_planner_has_no_tools(bound):
    """Everything it needs is already in state, and the free tier allows five
    requests a minute -- each tool round trip is another one."""
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    _, strength, running = build_coach(repo, config).sub_agents

    assert running.tools == []
    assert strength.tools, "the strength planner still needs the exercise pool"


def test_an_exercise_may_name_the_id_field_either_way():
    """The pool once returned "id" while the schema demanded
    "exercise_template_id", and the model copied the name it was shown -- one
    wrong key threw away a whole week's plan at validation."""
    assert PlannedExercise(id="ABC", title="Bench").exercise_template_id == "ABC"
    assert (
        PlannedExercise(exercise_template_id="XYZ", title="Row").exercise_template_id
        == "XYZ"
    )


def test_the_explicit_field_wins_over_the_synonym():
    exercise = PlannedExercise(exercise_template_id="RIGHT", id="WRONG", title="Row")
    assert exercise.exercise_template_id == "RIGHT"


def test_both_planners_sample_deterministically(bound):
    """A flaky eval is worse than none: it teaches you to ignore failures.

    At default temperature the same ledger yields a different week each run, so
    a failing assertion could mean the prompt regressed or the sampler wandered,
    with no way to tell which.
    """
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import PLANNER_TEMPERATURE, build_coach

    repo, config = bound
    _, strength, running = build_coach(repo, config).sub_agents

    assert PLANNER_TEMPERATURE == 0.0
    for planner in (strength, running):
        assert planner.generate_content_config.temperature == PLANNER_TEMPERATURE


def test_the_strength_planner_cannot_recompute_the_deficit(bound):
    """Three of ten runs called get_volume_vs_target despite being told not to.

    Removing the tool beats repeating the instruction: choosing its own window
    is how the planner once planned against a fabricated shortfall, and a rule
    the model *can* break is one that eventually gets broken. Everything it
    would have fetched is already injected.
    """
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from fitness_ledger.coach.agent import build_coach

    repo, config = bound
    _, strength, _ = build_coach(repo, config).sub_agents

    names = {getattr(t, "__name__", getattr(getattr(t, "func", None), "__name__", "")) for t in strength.tools}
    assert names == {"get_progression_state"}
    assert "get_volume_vs_target" not in names
    # `get_neglected` was removed from the catalog outright on 2026-09-04 --
    # no agent held it and `gather_context` never called it -- so asserting
    # its absence here would now pass whatever the tool list did.
    # get_exercise_pool went the same way on 2026-09-02. While it existed, the
    # pool reached the planner only if the model chose to ask -- Gemini asked,
    # DeepSeek did not, and the same code produced a usable week on one
    # provider and 26 invented ids on the other.
    assert "get_exercise_pool" not in names


def test_the_instruction_carries_the_exercise_pool():
    """The bug this file exists to prevent a repeat of.

    STRENGTH_INSTRUCTION had placeholders for the deficit, goals, days, week
    and continuity -- and none for the pool. `covering_pool` was built and
    written to session state, the assembler validated against it, and the
    planner was told to fetch it with a tool. On 2026-09-02 DeepSeek made no
    tool calls, invented ids like `bench_press`, and all 26 exercises failed
    validation: a full week that could not be written to Hevy.

    Asserting on the placeholder rather than on model behaviour, because the
    behaviour differed by provider -- Gemini made the call and papered over the
    gap for months.
    """
    import re

    from fitness_ledger.coach.agent import STRENGTH_INSTRUCTION

    placeholders = set(re.findall(r"{(\w+)}", STRENGTH_INSTRUCTION))
    assert "pool_summary" in placeholders

    # And it must not send the planner back to a tool that no longer exists.
    assert "get_exercise_pool" not in STRENGTH_INSTRUCTION


def test_the_instruction_says_ids_are_opaque_codes():
    # The failure mode was plausible-looking slugs, not obvious nonsense, so
    # the instruction names the shape of a real id.
    from fitness_ledger.coach.agent import STRENGTH_INSTRUCTION

    assert "exercise_template_id" in STRENGTH_INSTRUCTION
    assert "79D0BB3A" in STRENGTH_INSTRUCTION


def test_every_instruction_placeholder_is_published_to_state(bound):
    """A placeholder with no state key renders literally and silently.

    ADK substitutes `{key}` from session state. A typo, or a key the context
    reader forgets to write, leaves the model reading "{pool_summary}" as text
    -- which looks like a prompt-engineering problem and is really a wiring
    one.
    """
    import re

    from fitness_ledger.coach.agent import STRENGTH_INSTRUCTION
    from fitness_ledger.coach.context import (
        continuity_summary,
        deficit_summary,
        gather_context,
        pool_summary,
        training_days,
    )

    repo, config = bound
    state = gather_context(repo, config)
    state["training_days"] = training_days(state)
    state["deficit_summary"] = deficit_summary(state)
    state["continuity_summary"] = continuity_summary(state)
    state["pool_summary"] = pool_summary(state)

    missing = set(re.findall(r"{(\w+)}", STRENGTH_INSTRUCTION)) - set(state)
    assert not missing, f"instruction reads state keys nothing writes: {sorted(missing)}"
