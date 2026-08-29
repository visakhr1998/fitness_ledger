"""Goals, the running target, and availability.

These are what the coach plans toward. Nothing here computes anything — the
value is in the invariants: a goal type that doesn't exist must not be
storable, an abandoned goal must not vanish, and a day nobody mentioned must
read as available rather than unknown.
"""

from __future__ import annotations

from datetime import date

import pytest

from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import (
    Availability,
    Goal,
    RecurringConstraint,
    RunningTarget,
)

MONDAY = date(2026, 8, 10)


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "goals.db", 120) as repository:
        yield repository


# --- Goal validation -------------------------------------------------------


def test_a_goal_type_that_does_not_exist_is_rejected():
    # Caught in the model rather than the CLI, so every entry point gets it.
    with pytest.raises(ValueError, match="unknown goal type"):
        Goal(type="get_swole", target_value=100)


def test_an_unknown_status_is_rejected():
    with pytest.raises(ValueError, match="unknown goal status"):
        Goal(type="consistency", target_value=4, status="maybe")


def test_a_strength_goal_without_an_exercise_is_meaningless():
    # "get to 100 kg" on nothing in particular can never be evaluated.
    with pytest.raises(ValueError, match="needs a subject"):
        Goal(type="strength_1rm", target_value=100)


def test_a_running_goal_needs_no_subject():
    goal = Goal(type="running_volume", target_value=25)
    assert goal.subject is None
    assert goal.is_active


# --- Goal storage ----------------------------------------------------------


def test_a_stored_goal_comes_back_with_an_id_and_a_timestamp(repo):
    stored = repo.add_goal(Goal(type="strength_1rm", target_value=100, subject="Bench Press"))

    assert stored.id is not None
    assert stored.created_at is not None
    assert stored.subject == "Bench Press"


def test_only_active_goals_are_returned_by_default(repo):
    keep = repo.add_goal(Goal(type="running_volume", target_value=25))
    drop = repo.add_goal(Goal(type="consistency", target_value=4))
    repo.set_goal_status(drop.id, "abandoned")

    assert [g.id for g in repo.get_goals()] == [keep.id]


def test_an_abandoned_goal_is_kept_not_deleted(repo):
    # It is part of why earlier plans looked the way they did.
    goal = repo.add_goal(Goal(type="consistency", target_value=4))
    repo.set_goal_status(goal.id, "abandoned")

    everything = repo.get_goals(include_inactive=True)
    assert [g.status for g in everything] == ["abandoned"]


def test_setting_a_status_reports_whether_it_matched(repo):
    goal = repo.add_goal(Goal(type="running_aei", target_value=1.2))

    assert repo.set_goal_status(goal.id, "achieved") is True
    assert repo.set_goal_status(9999, "achieved") is False


def test_a_bad_status_cannot_be_written(repo):
    goal = repo.add_goal(Goal(type="running_aei", target_value=1.2))
    with pytest.raises(ValueError):
        repo.set_goal_status(goal.id, "nearly")


def test_target_date_survives_a_round_trip(repo):
    repo.add_goal(
        Goal(type="running_volume", target_value=30, target_date=date(2026, 12, 1))
    )
    assert repo.get_goals()[0].target_date == date(2026, 12, 1)


# --- running target --------------------------------------------------------


def test_no_running_target_is_none_not_a_default(repo):
    # A zero or an assumed default would let the coach silently "protect"
    # running against a number the user never chose.
    assert repo.get_running_target() is None


def test_running_target_round_trip(repo):
    repo.set_running_target(RunningTarget(distance_km_per_week=25, sessions_per_week=3))
    target = repo.get_running_target()

    assert target.distance_km_per_week == 25
    assert target.sessions_per_week == 3


def test_running_target_defaults_to_two_sessions(repo):
    repo.set_setting(repo.RUNNING_DISTANCE_KEY, "20")
    assert repo.get_running_target().sessions_per_week == 2


def test_a_running_target_can_be_cleared(repo):
    repo.set_running_target(RunningTarget(distance_km_per_week=25))
    repo.set_running_target(None)
    assert repo.get_running_target() is None


# --- availability ----------------------------------------------------------


def test_a_day_nobody_mentioned_is_available(repo):
    # Absence means available. Only exceptions are stored, so the user never
    # has to declare a normal week.
    found = repo.get_availability(MONDAY, date(2026, 8, 17))
    assert found == {}


def test_declaring_a_day_lost(repo):
    repo.set_availability(Availability(MONDAY, reason="work"))
    entry = repo.get_availability(MONDAY, date(2026, 8, 17))[MONDAY]

    assert entry.available is False
    assert entry.reason == "work"
    assert entry.source == "declared"


def test_declared_and_inferred_stay_distinguishable(repo):
    # A stated fact and this app's guess must not blur -- the coach explains
    # itself differently for each.
    repo.set_availability(Availability(MONDAY, reason="work", source="declared"))
    repo.set_availability(
        Availability(date(2026, 8, 11), reason="no logged session", source="inferred")
    )
    found = repo.get_availability(MONDAY, date(2026, 8, 17))

    assert found[MONDAY].source == "declared"
    assert found[date(2026, 8, 11)].source == "inferred"


def test_an_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="unknown availability source"):
        Availability(MONDAY, source="vibes")


def test_redeclaring_a_day_corrects_it_rather_than_duplicating(repo):
    repo.set_availability(Availability(MONDAY, reason="work"))
    repo.set_availability(Availability(MONDAY, reason="actually a holiday"))
    found = repo.get_availability(MONDAY, date(2026, 8, 17))

    assert len(found) == 1
    assert found[MONDAY].reason == "actually a holiday"


def test_clearing_returns_a_day_to_available(repo):
    repo.set_availability(Availability(MONDAY, reason="work"))

    assert repo.clear_availability(MONDAY) is True
    assert repo.get_availability(MONDAY, date(2026, 8, 17)) == {}
    assert repo.clear_availability(MONDAY) is False


def test_the_window_is_closed_open_like_every_other_query(repo):
    repo.set_availability(Availability(MONDAY, reason="in"))
    repo.set_availability(Availability(date(2026, 8, 17), reason="out"))

    found = repo.get_availability(MONDAY, date(2026, 8, 17))
    assert list(found) == [MONDAY]


# --- race goals ------------------------------------------------------------
#
# "Sub-4 marathon" is the shape people state running goals in, and until
# `race_time` existed it had nowhere to live -- running_volume says how far a
# week, never how fast on one day.


def test_a_race_goal_needs_a_distance():
    # "Under four hours" is meaningless until you say four hours of what, and
    # every pace derived from it divides by the distance.
    with pytest.raises(ValueError, match="needs a subject"):
        Goal(type="race_time", target_value=14400)


def test_a_race_distance_outside_the_known_set_is_rejected():
    with pytest.raises(ValueError, match="unknown race distance"):
        Goal(type="race_time", subject="ultra", target_value=14400)


def test_a_race_goal_needs_a_positive_time():
    with pytest.raises(ValueError, match="target time in seconds"):
        Goal(type="race_time", subject="marathon", target_value=0)


def test_a_race_goal_round_trips(repo):
    stored = repo.add_goal(Goal(type="race_time", subject="marathon", target_value=14400))
    (loaded,) = [g for g in repo.get_goals() if g.type == "race_time"]
    assert loaded.id == stored.id
    assert loaded.subject == "marathon"
    # Seconds, not a clock string: storage keeps the number that arithmetic
    # needs and formatting happens at the edge.
    assert loaded.target_value == 14400


# --- recurring constraints -------------------------------------------------


def test_a_constraint_kind_that_does_not_exist_is_rejected():
    with pytest.raises(ValueError, match="unknown constraint kind"):
        RecurringConstraint(weekday=2, kind="no_burpees")


def test_a_weekday_outside_the_week_is_rejected():
    with pytest.raises(ValueError, match="weekday must be"):
        RecurringConstraint(weekday=7, kind="no_lifting")


def test_a_constraint_narrows_a_day_rather_than_removing_it():
    # A knee that dislikes running is no reason to skip bench. This is the
    # whole reason `kind` exists instead of a boolean.
    knee = RecurringConstraint(weekday=2, kind="no_high_impact")
    assert knee.forbids_running() is True
    assert knee.forbids_lifting() is False


def test_restating_a_constraint_updates_it_rather_than_duplicating(repo):
    # The intake parser can propose a constraint the user already has. Failing
    # that would make a correct re-statement look like a fault.
    first = repo.add_constraint(RecurringConstraint(weekday=2, kind="no_high_impact", reason="knee"))
    again = repo.add_constraint(
        RecurringConstraint(weekday=2, kind="no_high_impact", reason="knee, still")
    )
    assert again.id == first.id
    assert [c.reason for c in repo.get_constraints()] == ["knee, still"]


def test_a_constraint_can_be_deleted_unlike_a_goal(repo):
    # An abandoned goal explains why past plans looked as they did. A knee that
    # stopped hurting is not a fact about last month's training.
    stored = repo.add_constraint(RecurringConstraint(weekday=0, kind="no_lifting"))
    assert repo.delete_constraint(stored.id) is True
    assert repo.get_constraints() == []
    assert repo.delete_constraint(stored.id) is False
