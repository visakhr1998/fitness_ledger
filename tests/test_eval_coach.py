"""What a plan must be true of, checked against real model output.

**Skipped unless `RUN_COACH_EVALS=1`.** Each fixture costs model requests, and a
suite that spent the daily quota every time someone ran `pytest` would be turned
off within a week.

    RUN_COACH_EVALS=1 ./.venv/Scripts/python.exe -m pytest tests/test_eval_coach.py -v

## Why this file has two halves

Temperature 0 does not make this provider repeatable. Measured on one fixture
minutes apart: one run made a single tool call and trained the neglected muscle,
another made five and did not. So every assertion here is a coin flip with an
unknown bias, and a single sample cannot tell "always works" from "worked once".

The suite is therefore split by what actually varies:

**Gates** run once and pass or fail. They cover things the schema and the
instruction constrain tightly enough that a failure is a real bug even if it
happens once -- an invented exercise id, a session on an unavailable day,
directive health language.

**Scores** run several times and report k-of-N. They cover judgement: whether
the neglected muscle got trained, whether the rationale named the binding
constraint. These *are* the variable ones, and a flaky gate is worse than no
gate because it teaches you to ignore red. The only thing a score fails on is
zero-for-N, which is not drift.

Nothing here asserts on wording. Structure and which facts appear, because
wording assertions break on every prompt tweak and teach nothing.

## What is deliberately absent

Session ceilings and set allocation. `planning.py` enforces those and
`test_planning.py` covers them without a model -- an eval that re-checks what
deterministic code already guarantees spends requests to learn nothing.
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

ALL = [f.name for f in fixtures.FIXTURES]

# The fixtures whose *judgement* is worth measuring repeatedly. Not all ten:
# each repeat costs model requests, and these are the ones with a right answer
# a plan can visibly miss.
JUDGED = ("back_neglected", "two_days_lost", "running_behind", "all_targets_met")


@pytest.fixture(scope="session")
def eval_root(tmp_path_factory):
    return tmp_path_factory.mktemp("evals")


def once(name: str, eval_root) -> eval_harness.EvalRun:
    """One planned week, or a skip if the API refused.

    A quota failure is not a behavioural finding. Reporting "the coach planned
    on a forbidden day" when it never planned at all sends someone debugging a
    prompt over a rate limit -- which is exactly what the first full run of this
    suite did, five fixtures in.
    """
    try:
        return eval_harness.run(name, eval_root)
    except eval_harness.QuotaExhausted as exc:
        pytest.skip(str(exc))


# --- gates: one run, pass or fail -------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_it_never_calls_a_forbidden_tool(name, eval_root):
    """The sharp one is get_volume_vs_target. Left to itself the agent called it
    with `this-week` -- a barely-started week where every muscle reads a full
    target short -- and planned against that fabricated shortfall. The deficit
    is injected precisely so it never has to ask."""
    run = once(name, eval_root)

    forbidden = set(run.tools_called) & eval_harness.FORBIDDEN_TOOLS
    assert not forbidden, f"{name} called {sorted(forbidden)}"


@pytest.mark.parametrize("name", ALL)
def test_every_proposed_exercise_exists(name, eval_root):
    """One invented id discards the whole week at validation -- it happened once
    already, when the pool named the field `id` and the schema wanted
    `exercise_template_id`."""
    run = once(name, eval_root)

    catalog = {template.id for template in fixtures.CATALOG}
    invented = run.planned_template_ids - catalog
    assert not invented, f"{name} invented {sorted(invented)}"


@pytest.mark.parametrize("name", ALL)
def test_it_only_uses_days_it_was_allowed(name, eval_root):
    run = once(name, eval_root)

    allowed = set(run.context.get("training_days") or [])
    if not allowed:
        pytest.skip("no availability constraint in this fixture")
    used = {session.local_date.isoformat() for session in run.plan.sessions}
    assert used <= allowed, f"{name} planned on {sorted(used - allowed)}"


@pytest.mark.parametrize("name", ALL)
def test_the_plan_does_not_direct_health(name, eval_root):
    """Specified for day one and impossible until there were fixtures: nothing
    had ever asserted that a *generated* plan is clean, only that the helper
    works.

    Planning invites prescription far harder than reporting did. The line is
    narrow -- the coach may direct training, never health -- and `poor_sleep` is
    the fixture built to push on it.
    """
    run = once(name, eval_root)

    assert_no_directive_health_language(run.rationale, f"{name} rationale")
    assert_no_directive_health_language(run.plan.trade_offs, f"{name} trade_offs")


@pytest.mark.parametrize("name", [n for n in ALL if n != "all_targets_met"])
def test_a_week_with_a_deficit_gets_sessions(name, eval_root):
    run = once(name, eval_root)

    assert run.plan.sessions, f"{name} produced an empty week"
    assert run.plan.total_sets > 0


def test_running_is_not_invented_without_a_target(eval_root):
    """The instruction is explicit: no target means no sessions. Without one
    there is nothing to protect and nothing to plan toward, and a made-up
    distance looks exactly like a real one."""
    run = once("back_neglected", eval_root)

    assert not run.plan.run_sessions


# --- scores: several runs, reported as k-of-N -------------------------------


@pytest.fixture(scope="session")
def judged(eval_root):
    """Every judged fixture, planned REPEATS times. The expensive fixture."""
    return {name: eval_harness.repeat(name, eval_root) for name in JUDGED}


def scored(judged, name: str) -> list[eval_harness.EvalRun]:
    """The runs that completed, or a skip if none did.

    Scoring 2-of-3 when the third never happened would report a judgement
    problem that is really a budget one.
    """
    runs = judged.get(name) or []
    if not runs:
        pytest.skip(f"{name}: no runs completed within quota")
    return runs


def report(scores: list[eval_harness.Score]) -> str:
    return "\n".join(score.line() for score in scores)


def test_judgement_scores(judged, capsys):
    """The scored half. Reports k-of-N and fails only on zero-for-N.

    A judgement that holds 2 times in 3 is information, and gating on it would
    throw that away in both directions -- green hiding the failure, red hiding
    the successes. Zero for N is different: that is not drift, it is broken.
    """
    scores = [
        eval_harness.score(
            "trains the neglected muscle",
            "back_neglected",
            scored(judged, "back_neglected"),
            lambda run: run.planned_muscles & {"lats", "upper_back"},
        ),
        eval_harness.score(
            "rationale names a real shortfall",
            "back_neglected",
            scored(judged, "back_neglected"),
            lambda run: any(
                muscle.replace("_", " ") in run.rationale.lower()
                or muscle in run.rationale.lower()
                for muscle in eval_harness.worst_deficits(run.context)
            ),
        ),
        eval_harness.score(
            "does not refetch injected context",
            "back_neglected",
            scored(judged, "back_neglected"),
            lambda run: len(run.trace) <= eval_harness.MAX_TOOL_CALLS,
        ),
        eval_harness.score(
            "squeezed week states a trade-off",
            "two_days_lost",
            scored(judged, "two_days_lost"),
            lambda run: bool(run.plan.trade_offs.strip()),
        ),
        eval_harness.score(
            "plans runs when a target exists",
            "running_behind",
            scored(judged, "running_behind"),
            lambda run: bool(run.plan.run_sessions),
        ),
        eval_harness.score(
            "met target does not invent volume",
            "all_targets_met",
            scored(judged, "all_targets_met"),
            lambda run: run.plan.total_sets <= 60,
        ),
    ]

    with capsys.disabled():
        print(f"\n\nSCORES over {eval_harness.REPEATS} runs each\n{report(scores)}\n")
        for score in scores:
            for failure in score.failures:
                print(f"    {score.name}: {failure}")
        print()

    never = [score for score in scores if score.never]
    assert not never, "never held in any run: " + ", ".join(s.name for s in never)


def test_gate_report(eval_root, capsys):
    """One line per fixture, for reading a whole gate pass at a glance."""
    lines = []
    for name in ALL:
        try:
            lines.append(eval_harness.run(name, eval_root).summary())
        except eval_harness.QuotaExhausted:
            lines.append(f'{name:20} -- not run (quota)')
    with capsys.disabled():
        print("\n\nGATE RUNS\n" + "\n".join(lines) + "\n")
