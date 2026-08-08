"""What a plan must be true of, checked against real model output.

**Skipped unless `RUN_COACH_EVALS=1`.** Each fixture costs model requests, and a
suite that spends the daily quota every time someone runs `pytest` would get
turned off within a week.

    RUN_COACH_EVALS=1 ./.venv/Scripts/python.exe -m pytest tests/test_eval_coach.py -v

Two layers, and the balance between them is not the one the plan originally
described.

**Trajectory** is now two negative assertions: tools it must never call, and a
ceiling on how many it makes. The original layer — "right tools, sensible order,
no redundant calls" — assumed a busy agent. After the context reader and the
planner split, a good run makes *one* call, so there is no order to assert and
asserting on tool use would reward the waste both were built to remove.

**Outcome** carries the weight. Never on wording: assert on structure and which
facts appear, because wording assertions break on every prompt tweak and teach
nothing.
"""

from __future__ import annotations

import pytest

import eval_harness
import fixtures
from guardrails import assert_no_directive_health_language

pytestmark = pytest.mark.skipif(
    not eval_harness.enabled(),
    reason=f"set {eval_harness.ENABLE_VAR}=1 to run evals; each one spends model requests",
)


@pytest.fixture(scope="session")
def eval_root(tmp_path_factory):
    return tmp_path_factory.mktemp("evals")


def plan_for(name: str, eval_root) -> eval_harness.EvalRun:
    return eval_harness.run(name, eval_root)


# --- trajectory -------------------------------------------------------------


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_it_never_calls_a_forbidden_tool(name, eval_root):
    """The sharp one is get_volume_vs_target. Left to itself the agent called
    it with `this-week` -- a barely-started week where every muscle reads a
    full target short -- and planned against that fabricated shortfall. The
    deficit is injected precisely so it never has to ask."""
    run = plan_for(name, eval_root)

    forbidden = set(run.tools_called) & eval_harness.FORBIDDEN_TOOLS
    assert not forbidden, f"{name} called {sorted(forbidden)}"


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_it_does_not_refetch_what_it_was_given(name, eval_root):
    """A ceiling, not an order. Before the split, runs made seven calls for
    things already in session state."""
    run = plan_for(name, eval_root)

    assert len(run.trace) <= eval_harness.MAX_TOOL_CALLS, run.tools_called


# --- outcome ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [f.name for f in fixtures.FIXTURES if f.name not in {"all_targets_met"}],
)
def test_a_week_with_a_deficit_gets_sessions(name, eval_root):
    run = plan_for(name, eval_root)

    assert run.plan.sessions, f"{name} produced an empty week"
    assert run.plan.total_sets > 0


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_every_proposed_exercise_exists(name, eval_root):
    """One invented id discards the whole week at validation -- it has happened
    once already, when the pool named the field `id` and the schema wanted
    `exercise_template_id`."""
    run = plan_for(name, eval_root)

    catalog = {template.id for template in fixtures.CATALOG}
    invented = run.planned_template_ids - catalog
    assert not invented, f"{name} invented {sorted(invented)}"


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_it_only_uses_days_it_was_allowed(name, eval_root):
    run = plan_for(name, eval_root)

    allowed = set(run.context["training_days"]) if run.context.get("training_days") else None
    if allowed is None:
        pytest.skip("no availability constraint in this fixture")
    used = {session.local_date.isoformat() for session in run.plan.sessions}
    assert used <= allowed, f"{name} planned on {sorted(used - allowed)}"


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_no_session_breaks_the_ceiling(name, eval_root):
    """The assembler trims to fit, so a violation here means allocation is
    wrong rather than the model being greedy."""
    run = plan_for(name, eval_root)

    over = [p for p in run.problems if "over the" in p and "in a session" in p]
    assert not over, over


def test_a_neglected_muscle_is_actually_trained(eval_root):
    """The core promise. Back has had nothing for three weeks; a plan that does
    not touch it has failed regardless of how well it reads."""
    run = plan_for("back_neglected", eval_root)

    back = {"lats", "upper_back"}
    assert run.planned_muscles & back, sorted(run.planned_muscles)


def test_the_rationale_names_the_binding_constraint(eval_root):
    """Facts, not wording: does the muscle it was told about appear at all."""
    run = plan_for("back_neglected", eval_root)

    worst = eval_harness.worst_deficits(run.context)
    lowered = run.rationale.lower()
    named = [m for m in worst if m.replace("_", " ") in lowered or m in lowered]
    assert named, f"rationale mentions none of {worst}: {run.rationale!r}"


def test_a_met_target_does_not_invent_work(eval_root):
    """Nothing is short. The plan should be about progression, and must not
    manufacture a deficit to justify volume."""
    run = plan_for("all_targets_met", eval_root)

    assert not eval_harness.deficits(run.context)
    assert run.plan.total_sets <= 60, run.plan.total_sets


def test_lost_days_are_respected_and_named(eval_root):
    """Volume is protected over session count, and trade_offs says what went."""
    run = plan_for("two_days_lost", eval_root)

    lost = set(run.context["availability"]["unavailable"][0]["date"].split())
    used = {session.local_date.isoformat() for session in run.plan.sessions}
    assert not (used & lost)
    assert run.plan.trade_offs.strip(), "a squeezed week gave up nothing?"


def test_running_is_planned_when_there_is_a_target(eval_root):
    run = plan_for("running_behind", eval_root)

    assert run.plan.run_sessions, "a 25 km target produced no runs"


def test_running_is_not_invented_without_a_target(eval_root):
    """Without a target there is nothing to protect and nothing to plan
    toward, and a made-up distance looks exactly like a real one."""
    run = plan_for("back_neglected", eval_root)

    assert not run.plan.run_sessions


# --- the guardrail ----------------------------------------------------------


@pytest.mark.parametrize("name", [f.name for f in fixtures.FIXTURES])
def test_the_plan_does_not_direct_health(name, eval_root):
    """Specified for day one and impossible until now: nothing had ever
    asserted that a *generated* plan is clean, only that the helper works.

    Planning invites prescription far harder than reporting did. The line is
    narrow -- the coach may direct training, never health -- and `poor_sleep`
    is the fixture built to push on it.
    """
    run = plan_for(name, eval_root)

    assert_no_directive_health_language(run.rationale, f"{name} rationale")
    assert_no_directive_health_language(run.plan.trade_offs, f"{name} trade_offs")


# --- a readable record ------------------------------------------------------


def test_report(eval_root, capsys):
    """Not an assertion so much as the artifact day 9 reads.

    Runs last, so every fixture is already cached; prints one line each.
    """
    lines = [plan_for(f.name, eval_root).summary() for f in fixtures.FIXTURES]
    with capsys.disabled():
        print("\n\n" + "\n".join(lines) + "\n")
