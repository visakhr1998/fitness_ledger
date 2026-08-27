"""Set allocation and plan validation.

This is where the coach's central guarantee is actually kept: the agent chooses
exercises and days, and *every* number in the finished week is computed here
from the deficit the rules engine reported. `WeekProposal` has nowhere to put a
set count, so there is nothing in these inputs to trust.

Pure, so hand-computable: no repository, no model, no clock.
"""

from __future__ import annotations

from datetime import date

import pytest

from fitness_ledger.planning import (
    MAX_SETS_PER_EXERCISE,
    MIN_SETS_PER_EXERCISE,
    Preferences,
    allocate,
    validate,
)

MON, WED, FRI = date(2026, 8, 10), date(2026, 8, 12), date(2026, 8, 14)


def lift(day: date, *exercises: tuple[str, list[str]], focus: str = "") -> dict:
    return {
        "session_date": day.isoformat(),
        "kind": "lift",
        "focus": focus,
        "exercises": [
            {"exercise_template_id": tid, "title": tid.title(), "targets": targets}
            for tid, targets in exercises
        ],
    }


def run(day: date, km: float = 5.0) -> dict:
    return {"session_date": day.isoformat(), "kind": "run", "distance_km": km}


# --- allocation -------------------------------------------------------------


def test_a_deficit_splits_evenly_across_the_exercises_chosen_for_it():
    """9 sets short, three exercises on it, three each."""
    week = [lift(MON, ("a", ["chest"]), ("b", ["chest"]), ("c", ["chest"]))]

    [session] = allocate(week, {"chest": 9}).sessions

    assert [e.sets for e in session.exercises] == [3, 3, 3]
    assert session.total_sets == 9


def test_an_exercise_serving_two_muscles_takes_the_larger_share_not_the_sum():
    """One set of a press serves chest and triceps at once. Adding the two
    shares would count the same set twice and double the session."""
    week = [lift(MON, ("bench", ["chest", "triceps"]))]

    [session] = allocate(week, {"chest": 4, "triceps": 4}).sessions

    assert session.exercises[0].sets == 4


def test_sets_are_spread_across_the_days_that_serve_a_muscle():
    week = [lift(MON, ("a", ["chest"])), lift(WED, ("b", ["chest"]))]

    monday, wednesday = allocate(week, {"chest": 8}).sessions

    assert monday.exercises[0].sets == 4
    assert wednesday.exercises[0].sets == 4


def test_no_exercise_gets_more_than_the_per_exercise_ceiling():
    """One badly neglected muscle must not swallow a whole day."""
    week = [lift(MON, ("a", ["chest"]))]

    [session] = allocate(week, {"chest": 40}).sessions

    assert session.exercises[0].sets == MAX_SETS_PER_EXERCISE


def test_a_tiny_deficit_still_earns_a_real_exercise():
    """A single set of something is not what anyone meant."""
    week = [lift(MON, ("a", ["chest"]))]

    [session] = allocate(week, {"chest": 1}).sessions

    assert session.exercises[0].sets == MIN_SETS_PER_EXERCISE


def test_an_exercise_no_muscle_needed_is_dropped_not_padded():
    """It was chosen for a muscle that turned out not to be short. Keeping it
    at the minimum would invent volume nobody asked for."""
    week = [lift(MON, ("needed", ["chest"]), ("spare", ["calves"]))]

    [session] = allocate(week, {"chest": 6}).sessions

    assert [e.title for e in session.exercises] == ["Needed"]


def test_the_session_ceiling_is_held():
    week = [lift(MON, ("a", ["chest"]), ("b", ["lats"]), ("c", ["quadriceps"]))]
    prefs = Preferences(max_sets_per_session=10)

    [session] = allocate(week, {"chest": 12, "lats": 12, "quadriceps": 12}, prefs).sessions

    assert session.total_sets <= 10
    # Trimmed from the largest down, so nothing is pushed below the minimum.
    assert all(e.sets >= MIN_SETS_PER_EXERCISE for e in session.exercises)


def test_runs_carry_their_distance_and_no_sets():
    week = [lift(MON, ("a", ["chest"])), run(WED, 8.0)]

    _, running = allocate(week, {"chest": 6}).sessions

    assert running.kind == "run"
    assert running.distance_km == 8.0
    assert running.exercises == ()


# --- what the week could not do --------------------------------------------


def test_a_muscle_no_exercise_serves_is_reported_not_silently_lost():
    week = [lift(MON, ("a", ["chest"]))]

    result = allocate(week, {"chest": 6, "calves": 8}, deficits={"chest": 6, "calves": 8})

    assert result.unplaced == ("calves",)
    assert result.unmet["calves"] == 8.0


def test_a_deficit_too_big_for_the_week_is_reported_as_still_short():
    """The honest half: a week has finite days and finite sets in a day."""
    week = [lift(MON, ("a", ["chest"]))]

    result = allocate(week, {"chest": 20}, deficits={"chest": 20})

    assert result.sessions[0].exercises[0].sets == MAX_SETS_PER_EXERCISE
    assert result.unmet["chest"] == pytest.approx(14.0)


def test_a_met_deficit_reports_nothing_outstanding():
    week = [lift(MON, ("a", ["chest"]), ("b", ["chest"]))]

    result = allocate(week, {"chest": 8})

    assert result.unmet == {}
    assert result.unplaced == ()


def test_rounding_remainders_are_not_reported_as_a_gap():
    """Half a set short is arithmetic, not a shortfall worth naming."""
    week = [lift(MON, ("a", ["chest"]), ("b", ["chest"]))]

    assert allocate(week, {"chest": 7}).unmet == {}


def test_an_empty_week_allocates_nothing_without_complaining():
    assert allocate([], {"chest": 8}).sessions == ()
    assert allocate([], {}).unmet == {}


# --- validation -------------------------------------------------------------


def clean_week():
    return allocate(
        [lift(MON, ("a", ["chest"])), lift(FRI, ("b", ["quadriceps"]))],
        {"chest": 6, "quadriceps": 6},
    ).sessions


def test_a_clean_week_has_nothing_to_say():
    assert validate(clean_week(), pool_ids={"a", "b"}) == []


def test_an_exercise_outside_the_pool_is_caught():
    """It does not exist in the user's Hevy catalog and cannot be written."""
    [problem] = validate(clean_week(), pool_ids={"a"})

    assert "b" in problem and "pool" in problem


def test_a_session_on_a_day_that_is_not_available_is_caught():
    problems = validate(clean_week(), training_days={MON.isoformat()})

    assert any("2026-08-14" in p and "not a training day" in p for p in problems)


def test_the_same_muscle_on_consecutive_days_is_caught():
    week = allocate(
        [lift(MON, ("a", ["chest"])), lift(date(2026, 8, 11), ("b", ["chest"]))],
        {"chest": 8},
    ).sessions

    [problem] = validate(week)

    assert "chest" in problem and "1 day apart" in problem


def test_rest_checking_can_be_turned_off():
    week = allocate(
        [lift(MON, ("a", ["chest"])), lift(date(2026, 8, 11), ("b", ["chest"]))],
        {"chest": 8},
    ).sessions

    assert validate(week, preferences=Preferences(min_rest_days_same_muscle=0)) == []


def test_a_run_after_leg_day_is_caught_only_when_disallowed():
    week = allocate(
        [lift(MON, ("squat", ["quadriceps"])), run(date(2026, 8, 11))],
        {"quadriceps": 6},
    ).sessions

    assert validate(week) == []
    [problem] = validate(week, preferences=Preferences(allow_run_after_leg_day=False))
    assert "run" in problem and "leg session" in problem


def test_validation_reports_every_problem_not_just_the_first():
    """Several can be true at once and the first is not necessarily the most
    useful one to see."""
    week = allocate([lift(MON, ("ghost", ["chest"]))], {"chest": 6}).sessions

    problems = validate(week, pool_ids={"other"}, training_days={FRI.isoformat()})

    assert len(problems) == 2


# --- the target is the amount; the deficit only ranks -----------------------


def test_a_consistent_week_is_not_punished():
    """The bug this replaced: allocation used last week's shortfall as the
    amount, so chest at 10 of a 14-set target got a 4-set week and someone who
    hit the target exactly got a week with nothing in it. A deficit says how
    far behind you fell -- it was never the amount to train."""
    week = [lift(MON, ("a", ["chest"])), lift(WED, ("b", ["chest"]))]

    for trained in (0, 10, 13, 14):
        short = max(14 - trained, 0)
        result = allocate(week, {"chest": 14}, deficits={"chest": short} if short else {})
        assert result.total_sets == 12, f"trained {trained}: {result.total_sets}"


def test_nothing_short_still_earns_a_full_week():
    week = [lift(MON, ("a", ["chest"])), lift(WED, ("b", ["lats"]))]

    result = allocate(week, {"chest": 12, "lats": 12}, deficits={})

    assert result.total_sets > 0
    assert result.unmet == {}


def test_a_muscle_the_week_ignores_gets_nothing_allocated():
    """The agent decides what the week is about. Allocating for a muscle it did
    not choose would be planning on its behalf."""
    week = [lift(MON, ("a", ["chest"]))]

    result = allocate(week, {"chest": 12, "quadriceps": 14})

    assert result.total_sets == 6  # chest only, capped per exercise


def test_the_ceiling_takes_from_whatever_is_least_behind():
    """The priority ranking, applied at the only point the week is forced to
    choose. A set removed from a muscle already on target costs less than one
    removed from a muscle three weeks neglected."""
    week = [lift(MON, ("neglected", ["lats"]), ("fine", ["chest"]))]
    prefs = Preferences(max_sets_per_session=8)

    result = allocate(
        week,
        {"lats": 12, "chest": 12},
        prefs,
        deficits={"lats": 12},  # chest is on target
    )

    by_title = {e.title: e.sets for e in result.sessions[0].exercises}
    assert by_title["Neglected"] > by_title["Fine"]


def test_without_deficits_it_still_allocates():
    """Ranking is optional; the amount is not."""
    week = [lift(MON, ("a", ["chest"]))]

    # One exercise carries the whole 10, clamped to the per-exercise ceiling.
    assert allocate(week, {"chest": 10}).total_sets == MAX_SETS_PER_EXERCISE
