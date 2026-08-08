"""Week-over-week continuity.

This is what makes the coach a coach rather than a week generator. *"Back is
still short, third week"* needs to know what was said last time and whether it
was followed; without it every week is argued from scratch and a shortfall that
has survived three plans reads exactly like a new one.

The comparison is pure and the rendering is deterministic, so both are tested
without a model.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitness_ledger.coach.assembler import plan_adherence
from fitness_ledger.coach.context import continuity_summary
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import (
    ExerciseTemplate,
    Plan,
    PlannedExercise,
    PlannedSession,
)
from fitness_ledger.planning import Adherence, adherence, adherence_summary
from guardrails import assert_no_directive_health_language

# A week that is safely in the past, so "has this day happened yet" is settled
# and stays settled. The pending cases below supply their own clock.
MON = date(2026, 7, 27)
WED = date(2026, 7, 29)
FRI = date(2026, 7, 31)


def planned_week() -> tuple[PlannedSession, ...]:
    return (
        PlannedSession(
            local_date=MON,
            kind="lift",
            exercises=(
                PlannedExercise("BENCH", "Bench Press", 4, ("chest",)),
                PlannedExercise("ROW", "Barbell Row", 3, ("upper_back",)),
            ),
        ),
        PlannedSession(
            local_date=WED,
            kind="lift",
            exercises=(PlannedExercise("SQUAT", "Squat", 5, ("quadriceps",)),),
        ),
        PlannedSession(local_date=FRI, kind="run", distance_km=6.0),
    )


# --- the comparison ---------------------------------------------------------


def test_a_week_trained_in_full_reads_as_complete():
    result = adherence(
        planned_week(),
        {MON: {"BENCH": 4, "ROW": 3}, WED: {"SQUAT": 5}},
        run_days={FRI},
    )

    assert result.completed == 3
    assert result.planned == 3
    assert result.missed == ()


def test_a_day_with_nothing_logged_is_a_miss():
    result = adherence(planned_week(), {MON: {"BENCH": 4, "ROW": 3}}, run_days={FRI})

    assert result.missed == (WED,)
    assert result.completed == 2


def test_a_session_trained_differently_still_counts_as_trained():
    """Deliberately generous. A session done with other exercises is still a
    session done, and calling it a miss would make the coach nag about a week
    that went fine."""
    result = adherence(planned_week(), {MON: {"SOMETHING_ELSE": 6}}, run_days=set())

    monday = result.sessions[0]
    assert monday.completed
    # ...but what was planned and skipped is still named.
    assert monday.missing == ("Bench Press", "Barbell Row")


def test_a_planned_exercise_that_was_skipped_is_named():
    result = adherence(planned_week(), {MON: {"BENCH": 4}}, run_days=set())

    assert result.sessions[0].missing == ("Barbell Row",)


def test_a_planned_run_needs_a_logged_run_not_a_lifting_session():
    result = adherence(planned_week(), {FRI: {"BENCH": 4}}, run_days=set())

    friday = next(s for s in result.sessions if s.kind == "run")
    assert not friday.completed


def test_sets_are_counted_but_never_mixed_with_effective_sets():
    """A planned set and an effective set are different units -- the volume
    engine credits a secondary muscle at half. Reporting a ratio across the two
    would be precision that is not there, so the comparison stays at session
    and exercise level."""
    result = adherence(planned_week(), {MON: {"BENCH": 6, "ROW": 3}}, run_days=set())

    assert result.planned_sets == 12  # 4 + 3 + 5
    assert result.logged_sets == 9
    assert not hasattr(result, "ratio")


def test_an_empty_plan_compares_to_nothing():
    result = adherence((), {}, set())

    assert result.planned == 0
    assert result.missed == ()


# --- rendering --------------------------------------------------------------


def test_the_summary_states_what_happened():
    result = adherence(planned_week(), {MON: {"BENCH": 4, "ROW": 3}}, run_days={FRI})

    rendered = adherence_summary(result)

    assert "2 of 3 planned sessions" in rendered
    assert WED.isoformat() in rendered


def test_the_summary_survives_having_nothing_to_say():
    assert "no previous plan" in adherence_summary(Adherence(week_start=MON))


def test_the_summary_does_not_reproach():
    """It reaches a model that is about to write a rationale, and 'you missed
    two sessions' is the sort of line that comes back out as a reprimand."""
    result = adherence(planned_week(), {}, run_days=set())

    assert_no_directive_health_language(adherence_summary(result), "adherence summary")


def test_continuity_says_so_on_the_first_ever_week():
    assert "first week" in continuity_summary({"previous_plan": {"available": False}})
    assert "first week" in continuity_summary({})


def test_continuity_reports_the_previous_plan_and_its_fate():
    rendered = continuity_summary(
        {
            "previous_plan": {
                "available": True,
                "week_start": "2026-08-03",
                "status": "superseded",
                "trade_offs": "calves untouched",
                "followed": {
                    "sessions_planned": 4,
                    "sessions_completed": 2,
                    "missed_days": ["2026-08-05", "2026-08-07"],
                },
            }
        }
    )

    assert "2026-08-03" in rendered
    assert "2 of 4" in rendered
    assert "2026-08-05" in rendered
    assert "calves untouched" in rendered


# --- wired to the cache -----------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "continuity.db", 120) as repository:
        repository.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", (), "barbell"),
            ExerciseTemplate("SQUAT", "Squat", "weight_reps", "quadriceps", (), "barbell"),
        ])
        yield repository


def log_session(repo, day: date, template: str, sets: int) -> None:
    repo.upsert_workout({
        "id": f"w-{day}-{template}",
        "title": "Session",
        "start_time": f"{day.isoformat()}T12:00:00+00:00",
        "end_time": f"{day.isoformat()}T13:00:00+00:00",
        "exercises": [
            {
                "index": 0,
                "title": template.title(),
                "exercise_template_id": template,
                "sets": [
                    {"index": i, "type": "normal", "weight_kg": 60, "reps": 8}
                    for i in range(sets)
                ],
            }
        ],
    })


def test_adherence_reads_the_logged_week_out_of_the_cache(repo):
    plan = repo.add_plan(Plan(week_start=MON, sessions=planned_week()))
    log_session(repo, MON, "BENCH", 4)

    result = plan_adherence(repo, plan)

    assert result.completed == 1
    assert WED in result.missed


def test_warmups_do_not_count_as_having_trained(repo):
    """Same rule the volume engine follows; a warmup is not a session."""
    plan = repo.add_plan(Plan(week_start=MON, sessions=planned_week()))
    repo.upsert_workout({
        "id": "warmup-only",
        "title": "Session",
        "start_time": f"{MON.isoformat()}T12:00:00+00:00",
        "end_time": f"{MON.isoformat()}T13:00:00+00:00",
        "exercises": [
            {
                "index": 0, "title": "Bench", "exercise_template_id": "BENCH",
                "sets": [{"index": 0, "type": "warmup", "weight_kg": 20, "reps": 10}],
            }
        ],
    })

    assert MON in plan_adherence(repo, plan).missed


def test_adherence_of_no_plan_is_empty_not_an_error(repo):
    assert plan_adherence(repo, None).planned == 0


def test_the_previous_plan_tool_carries_what_became_of_it(repo):
    from fitness_ledger.coach.tools import build_tools
    from fitness_ledger.config import Config

    repo.add_plan(Plan(week_start=MON, sessions=planned_week()))
    log_session(repo, MON, "BENCH", 4)

    tools = {tool.__name__: tool for tool in build_tools(repo, Config.load())}
    previous = tools["get_previous_plan"]()

    assert previous["available"] is True
    assert previous["followed"]["sessions_planned"] == 3
    assert previous["followed"]["sessions_completed"] == 1
    assert previous["followed"]["missed_days"] == [WED.isoformat(), FRI.isoformat()]


# --- a week that has not happened yet ---------------------------------------


def test_a_future_week_is_pending_not_missed():
    """Plans are written for a week that has not started. Counting those days
    as missed made a freshly stored plan read as a week in which everything was
    skipped -- straight into the prompt for the next one."""
    result = adherence(planned_week(), {}, set(), today=MON - timedelta(days=1))

    assert result.not_started
    assert result.missed == ()
    assert result.planned == 0
    assert len(result.pending) == 3


def test_a_half_finished_week_judges_only_the_days_that_have_passed():
    result = adherence(
        planned_week(), {MON: {"BENCH": 4, "ROW": 3}}, set(), today=WED
    )

    assert result.planned == 1      # Monday only
    assert result.completed == 1
    assert result.missed == ()      # Wednesday has not happened yet
    assert result.pending == (WED, FRI)


def test_today_is_pending_because_the_day_is_not_over():
    result = adherence(planned_week(), {}, set(), today=MON)

    assert MON in result.pending
    assert result.missed == ()


def test_the_summary_says_a_week_has_not_started():
    result = adherence(planned_week(), {}, set(), today=MON - timedelta(days=1))

    rendered = adherence_summary(result)

    assert "has not started" in rendered
    assert "3 sessions ahead" in rendered
    # The words that would have the planner writing around a failure.
    assert "0 of" not in rendered
    assert "nothing logged" not in rendered


def test_continuity_says_a_week_has_not_started():
    rendered = continuity_summary(
        {
            "previous_plan": {
                "available": True,
                "week_start": "2026-08-10",
                "status": "proposed",
                "followed": {"not_started": True, "sessions_ahead": 6},
            }
        }
    )

    assert "has not started" in rendered
    assert "0 of" not in rendered
