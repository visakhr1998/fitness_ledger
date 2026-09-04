"""What the coach is allowed to know.

The tools are thin, so most of these are contract tests rather than behaviour
tests. Two matter more than the rest:

- no tool returns individual sets, because the moment one does, the agent can
  total them and "no number was invented" stops being true without anything
  failing;
- no tool signature mentions the repository, because ADK builds each tool's
  JSON schema from the signature and would choke on an object it cannot
  describe -- at runtime, on the free tier, mid-plan.
"""

from __future__ import annotations

import inspect
import json
import typing
from dataclasses import replace
from datetime import date, timedelta

import pytest

from fitness_ledger.coach.tools import build_tools, tool_names
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
    """A repo with enough history for the wrappers to return something real."""
    config = replace(Config.load(), db_path=tmp_path / "coach.db")
    with SQLiteRepository(config.db_path, 120) as repo:
        repo.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
            ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", ("biceps",), "barbell"),
            ExerciseTemplate("NEVER", "Sled Push", "weight_reps", "quadriceps", (), "machine"),
        ])
        repo.set_targets([VolumeTarget("chest", 14, 2), VolumeTarget("upper_back", 14, 2)])
        for weeks_ago in range(1, 5):
            day = TODAY - timedelta(weeks=weeks_ago)
            repo.upsert_workout({
                "id": f"w{weeks_ago}", "title": "Session",
                "start_time": iso(day), "end_time": iso(day),
                "exercises": [
                    {
                        "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                        "sets": [
                            {"index": 0, "type": "warmup", "weight_kg": 40, "reps": 10},
                            {"index": 1, "type": "normal", "weight_kg": 80, "reps": 8},
                        ],
                    },
                    {
                        "index": 1, "title": "Barbell Row", "exercise_template_id": "ROW",
                        "sets": [{"index": 0, "type": "normal", "weight_kg": 60, "reps": 10}],
                    },
                ],
            })
        yield repo, config


@pytest.fixture()
def tools(bound):
    repo, config = bound
    return {tool.__name__: tool for tool in build_tools(repo, config)}


# --- the contract ----------------------------------------------------------


def test_no_tool_returns_individual_sets(tools):
    """The load-bearing one. Raw sets are the path back to invented numbers."""
    forbidden = ("get_sets", "get_workout_sets", "raw_sets", "get_workouts")
    assert not [name for name in tools if name in forbidden]
    assert not [name for name in tools if "set" in name and "target" not in name]


def test_no_signature_mentions_the_repository(tools):
    # ADK generates each tool's schema from the signature. A repo or Config
    # parameter fails at runtime, mid-plan, rather than here.
    #
    # get_type_hints rather than param.annotation: `from __future__ import
    # annotations` makes annotations strings, so the naive check passes a
    # string where a type was meant and proves nothing.
    for name, tool in tools.items():
        params = inspect.signature(tool).parameters
        assert "repo" not in params and "config" not in params, name

        hints = typing.get_type_hints(tool)
        for param_name in params:
            assert hints[param_name] in (str, int, bool), f"{name}.{param_name}"


def test_adk_builds_a_schema_with_the_expected_parameters(tools):
    """The contract that actually matters: ADK can describe every tool.

    A tool whose parameters don't reach the model isn't broken loudly -- it
    silently runs on defaults forever, so the coach could only ever ask about
    this week and nothing would fail.

    Note ADK 2.6.2 puts the schema in `parameters_json_schema`; the legacy
    `parameters` field is None and reading it suggests, wrongly, that no tool
    takes arguments.
    """
    pytest.importorskip("google.adk", reason="coach extra not installed")
    from google.adk.tools import FunctionTool

    expected = {
        "get_volume_vs_target": {"window"},
        "get_neglected": {"window", "limit"},
        "get_exercise_pool": {"muscle_group", "logged_only"},
        "get_recent_runs": {"window"},
        "get_recovery_signals": {"window"},
        "get_availability": {"week_start"},
    }
    for name, wanted in expected.items():
        declaration = FunctionTool(func=tools[name])._get_declaration()
        schema = declaration.parameters_json_schema or {}
        assert set(schema.get("properties") or {}) == wanted, name
        assert declaration.description, name


def test_every_tool_documents_itself(tools):
    # The docstring is the description the model reads. A tool without one is
    # a tool it will use wrongly.
    for name, tool in tools.items():
        assert tool.__doc__ and len(tool.__doc__.strip()) > 40, name


def test_every_argument_is_documented(tools):
    for name, tool in tools.items():
        params = [p for p in inspect.signature(tool).parameters]
        if params:
            assert "Args:" in tool.__doc__, name


def test_the_tool_list_is_stable(bound):
    repo, config = bound
    assert tool_names(repo, config) == [
        "get_volume_vs_target",
        "get_neglected",
        "get_progression_state",
        "get_exercise_pool",
        "get_recent_runs",
        "get_recovery_signals",
        "get_insights",
        "get_goals",
        "get_availability",
        "get_previous_plan",
    ]


def test_every_result_is_json_serialisable(tools):
    # Whatever the model receives has to survive the wire.
    for name, tool in tools.items():
        json.dumps(tool(), default=str)


# --- the wrappers ----------------------------------------------------------


def test_volume_reports_the_deficit_not_the_sets(tools):
    result = tools["get_volume_vs_target"]("last-4-weeks")
    chest = next(m for m in result["muscles"] if m["muscle_group"] == "chest")

    assert "sets_deficit" in chest and "target_sets" in chest
    # Trimmed: the agent chooses between muscles, it does not render a chart.
    assert set(chest) == {
        "muscle_group", "effective_sets", "target_sets",
        "sets_deficit", "frequency", "target_frequency",
    }


def test_the_pool_can_be_filtered_by_muscle(tools):
    chest = tools["get_exercise_pool"]("chest")
    assert [row["title"] for row in chest] == ["Bench Press"]


def test_the_filter_matches_secondary_muscles_too(tools):
    # Bench trains triceps as a secondary; a coach filling a triceps deficit
    # should see it.
    assert "Bench Press" in [row["title"] for row in tools["get_exercise_pool"]("triceps")]


def test_the_pool_carries_the_equipment_it_claims_to(tools):
    """The key the catalog emits, not this repo's name for the column.

    `exercise_catalog` returns "equipment"; the wrapper read
    "equipment_category" and so handed the agent None for every exercise --
    the same key mismatch PR #35 fixed in `sync.py`. Asserted against the
    value, because a `.get()` on the wrong key fails by returning None rather
    than by raising.
    """
    bench = next(row for row in tools["get_exercise_pool"]() if row["title"] == "Bench Press")
    assert bench["equipment"] == "barbell"


def test_the_pool_defaults_to_exercises_actually_trained(tools):
    titles = [row["title"] for row in tools["get_exercise_pool"]()]
    assert "Sled Push" not in titles  # in the catalog, never logged
    assert "Bench Press" in titles


def test_goals_and_running_target_come_back_together(bound, tools):
    repo, _ = bound
    repo.add_goal(Goal(type="strength_1rm", target_value=100, subject="Bench Press"))
    repo.set_running_target(RunningTarget(distance_km_per_week=25, sessions_per_week=3))

    result = tools["get_goals"]()
    assert result["goals"][0]["subject"] == "Bench Press"
    assert result["running_target"]["distance_km_per_week"] == 25


def test_a_missing_running_target_is_null_not_zero(tools):
    # So the coach says "running has no target" rather than protecting 0 km.
    assert tools["get_goals"]()["running_target"] is None


def test_availability_defaults_to_next_week(tools):
    result = tools["get_availability"]()
    start = date.fromisoformat(result["week_start"])

    assert start.weekday() == 0  # Monday
    assert start > TODAY


def test_availability_lists_only_the_exceptions(bound, tools):
    repo, _ = bound
    monday = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=7)
    repo.set_availability(Availability(monday, reason="work"))

    result = tools["get_availability"](monday.isoformat())
    assert len(result["unavailable"]) == 1
    assert result["unavailable"][0]["reason"] == "work"
    # The agent must not read absence as unknown.
    assert "not listed are available" in result["note"]


def test_declared_and_inferred_survive_to_the_agent(bound, tools):
    repo, _ = bound
    monday = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=7)
    repo.set_availability(Availability(monday, reason="guess", source="inferred"))

    entry = tools["get_availability"](monday.isoformat())["unavailable"][0]
    assert entry["source"] == "inferred"


def test_previous_plan_is_honest_about_not_existing_yet(tools):
    result = tools["get_previous_plan"]()
    assert result["available"] is False
    assert "no previous plan" in result["reason"]
