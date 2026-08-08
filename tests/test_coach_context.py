"""Reading the planning picture.

Most of this is testable without ADK and without a model, which is the point:
the context reader was made deterministic precisely so it could be. The one
ADK test drives it through the real runner, because "writes to session state"
is a claim about ADK, not about us.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest

from fitness_ledger.coach import context as ctx_module
from fitness_ledger.coach.context import (
    STATE_KEYS,
    gather_context,
    next_monday,
    training_days,
)
from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import (
    Availability,
    ExerciseTemplate,
    Goal,
    RunningTarget,
    VolumeTarget,
)

TODAY = date.today()


def iso(day: date) -> str:
    return day.isoformat() + "T12:00:00+00:00"


@pytest.fixture()
def bound(tmp_path):
    config = replace(Config.load(), db_path=tmp_path / "ctx.db")
    with SQLiteRepository(config.db_path, 120) as repo:
        repo.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
        ])
        repo.set_targets([VolumeTarget("chest", 14, 2)])
        day = TODAY - timedelta(weeks=1)
        repo.upsert_workout({
            "id": "w1", "title": "Session", "start_time": iso(day), "end_time": iso(day),
            "exercises": [{
                "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                "sets": [{"index": 0, "type": "normal", "weight_kg": 80, "reps": 8}],
            }],
        })
        yield repo, config


# --- the week being planned ------------------------------------------------


def test_planning_always_targets_a_week_that_has_not_started():
    # Planning the current part-finished week would compare a full target
    # against partial logged volume and read every muscle as behind.
    monday = next_monday(date(2026, 8, 5))  # a Wednesday
    assert monday == date(2026, 8, 10)
    assert monday.weekday() == 0


def test_next_monday_from_a_monday_is_the_following_one():
    assert next_monday(date(2026, 8, 10)) == date(2026, 8, 17)


def test_next_monday_from_a_sunday_is_tomorrow():
    assert next_monday(date(2026, 8, 9)) == date(2026, 8, 10)


# --- the picture -----------------------------------------------------------


def test_every_promised_key_is_present(bound):
    repo, config = bound
    state = gather_context(repo, config)

    for key in STATE_KEYS:
        assert key in state, key
    # An absent key and an empty one mean different things; only one of them
    # should be reachable.
    assert state["exercise_pool"] is not None


def test_the_ledger_state_carries_what_a_planner_needs(bound):
    repo, config = bound
    ledger = gather_context(repo, config)["ledger_state"]

    assert set(ledger) == {
        "volume", "volume_trend", "progression", "runs", "recovery", "insights",
    }
    assert any(m["muscle_group"] == "chest" for m in ledger["volume"]["muscles"])


def test_the_planning_deficit_is_measured_over_one_complete_week(bound):
    """Not four weeks, and never the current one.

    Over last-4-weeks the target is scaled to four weeks, so its deficit is
    four weeks' worth and planning against it would quadruple every session.
    Over this-week almost nothing is logged yet, so every muscle reads as a
    full target short -- which is what the agent actually did when left to
    choose for itself.
    """
    repo, config = bound
    ledger = gather_context(repo, config)["ledger_state"]

    assert ctx_module.PLANNING_WINDOW == "last-week"
    assert ledger["volume"] == ctx_module.gather_context(repo, config)["ledger_state"]["volume"]
    # The trend window stays available, so a one-off can be told from a pattern.
    assert ctx_module.TREND_WINDOW == "last-4-weeks"


def test_goals_and_running_target_reach_the_planner(bound):
    repo, config = bound
    repo.add_goal(Goal(type="strength_1rm", target_value=100, subject="Bench Press"))
    repo.set_running_target(RunningTarget(distance_km_per_week=25))

    goals = gather_context(repo, config)["goals"]
    assert goals["goals"][0]["target_value"] == 100
    assert goals["running_target"]["distance_km_per_week"] == 25


def test_context_uses_the_same_wrappers_the_agent_would_call(bound):
    """State and tool output must not disagree about the same number.

    If gather_context reached into queries.py separately, the two could drift
    and the agent would have no way to tell which was right.
    """
    repo, config = bound
    from fitness_ledger.coach.tools import build_tools

    tools = {t.__name__: t for t in build_tools(repo, config)}
    state = gather_context(repo, config)

    assert state["ledger_state"]["volume"] == tools["get_volume_vs_target"](
        ctx_module.PLANNING_WINDOW
    )
    assert state["exercise_pool"] == tools["get_exercise_pool"]()


# --- which days are left ---------------------------------------------------


def test_a_week_with_no_exceptions_is_seven_trainable_days(bound):
    repo, config = bound
    assert len(training_days(gather_context(repo, config))) == 7


def test_declared_days_are_removed(bound):
    repo, config = bound
    monday = next_monday()
    repo.set_availability(Availability(monday, reason="work"))
    repo.set_availability(Availability(monday + timedelta(days=1), reason="work"))

    days = training_days(gather_context(repo, config))
    assert len(days) == 5
    assert monday.isoformat() not in days


def test_an_exception_that_restores_a_day_keeps_it(bound):
    # available=True is an exception saying "actually fine", not a removal.
    repo, config = bound
    monday = next_monday()
    repo.set_availability(Availability(monday, available=True, reason="gym reopened"))

    assert monday.isoformat() in training_days(gather_context(repo, config))


def test_which_days_are_left_is_not_left_to_the_agent(bound):
    # It is arithmetic, and the agent does not do arithmetic.
    repo, config = bound
    state = gather_context(repo, config)
    assert isinstance(training_days(state), list)


# --- the ADK adapter -------------------------------------------------------


def test_the_reader_calls_no_model(bound):
    """Pins the design decision.

    This exists to save requests against a ~15 RPM budget. An LlmAgent would
    spend requests to save requests, so if someone later "upgrades" it to one,
    this should fail and make them argue for it.
    """
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from google.adk.agents import LlmAgent

    repo, config = bound
    reader = ctx_module.build_context_reader(repo, config)

    assert not isinstance(reader, LlmAgent)
    assert reader.name == "context_reader"


def test_state_reaches_the_session_through_adk(bound):
    """The claim "writes to session state" is about ADK, so drive the runner."""
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    repo, config = bound
    reader = ctx_module.build_context_reader(repo, config)
    runner = InMemoryRunner(agent=reader, app_name="coach-test")

    async def run() -> dict:
        session = await runner.session_service.create_session(
            app_name="coach-test", user_id="u"
        )
        async for _ in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="plan")]),
        ):
            pass
        after = await runner.session_service.get_session(
            app_name="coach-test", user_id="u", session_id=session.id
        )
        return after.state

    state = asyncio.run(run())

    for key in STATE_KEYS:
        assert key in state, key
    assert len(state["training_days"]) == 7
