"""Repository round-trips, using a Hevy-shaped payload."""

from __future__ import annotations

from datetime import date

import pytest

from fitness_ledger.db import SQLiteRepository, local_date_of
from fitness_ledger.models import ExerciseTemplate, VolumeTarget

WORKOUT = {
    "id": "w-1",
    "title": "Late night workout",
    "description": "",
    "routine_id": None,
    "start_time": "2026-07-28T18:55:11+00:00",
    "end_time": "2026-07-28T20:03:55+00:00",
    "created_at": "2026-07-28T20:04:03.321Z",
    "updated_at": "2026-07-28T20:04:03.321Z",
    "exercises": [
        {
            "index": 0,
            "title": "Chest Press (Machine)",
            "notes": "",
            "exercise_template_id": "7EB3F7C3",
            "supersets_id": None,
            "sets": [
                {"index": 0, "type": "warmup", "weight_kg": 25, "reps": 10, "rpe": None},
                {"index": 1, "type": "normal", "weight_kg": 54.3, "reps": 4, "rpe": 9},
            ],
        },
        {
            "index": 1,
            "title": "Lat Pulldown (Machine)",
            "notes": "felt strong",
            "exercise_template_id": "473CF5B8",
            "supersets_id": None,
            "sets": [{"index": 0, "type": "normal", "weight_kg": 52, "reps": 4, "rpe": None}],
        },
    ],
}


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "test.db", local_utc_offset_minutes=120) as repository:
        yield repository


def test_workout_round_trip(repo):
    repo.upsert_workout(WORKOUT)
    sets = repo.get_sets(date(2026, 7, 27), date(2026, 8, 3))

    assert len(sets) == 3
    assert repo.count_workouts() == 1
    assert {entry.set_type for entry in sets} == {"warmup", "normal"}
    assert sets[0].exercise_title == "Chest Press (Machine)"
    assert sets[1].rpe == 9


def test_upsert_replaces_sets_rather_than_appending(repo):
    repo.upsert_workout(WORKOUT)
    edited = {**WORKOUT, "exercises": WORKOUT["exercises"][:1]}
    repo.upsert_workout(edited)

    sets = repo.get_sets(date(2026, 7, 27), date(2026, 8, 3))
    assert len(sets) == 2, "editing a workout must not leave orphaned sets behind"
    assert repo.count_workouts() == 1


def test_delete_workout_removes_its_sets(repo):
    repo.upsert_workout(WORKOUT)
    repo.delete_workout("w-1")

    assert repo.count_workouts() == 0
    assert repo.get_sets(date(2026, 7, 1), date(2026, 8, 3)) == []


def test_workout_is_bucketed_by_local_day(repo):
    # 23:30 UTC on the 28th is 01:30 local on the 29th at +2h.
    late = {**WORKOUT, "id": "w-2", "start_time": "2026-07-28T23:30:00+00:00"}
    repo.upsert_workout(late)

    assert repo.get_workouts(date(2026, 7, 29), date(2026, 7, 30))[0].id == "w-2"
    assert repo.get_workouts(date(2026, 7, 28), date(2026, 7, 29)) == []


def test_local_date_of_handles_offsets_and_z_suffix():
    assert local_date_of("2026-07-28T23:30:00Z", 120) == date(2026, 7, 29)
    assert local_date_of("2026-07-28T23:30:00Z", 0) == date(2026, 7, 28)
    assert local_date_of("2026-07-29T01:30:00+00:00", -180) == date(2026, 7, 28)


def test_templates_round_trip_and_upsert(repo):
    repo.upsert_templates([ExerciseTemplate("A", "Bench", "weight_reps", "chest", ("triceps",))])
    repo.upsert_templates([ExerciseTemplate("A", "Bench Press", "weight_reps", "chest", ("triceps", "shoulders"))])

    templates = repo.get_templates()
    assert len(templates) == 1
    assert templates["A"].title == "Bench Press"
    assert templates["A"].secondary_muscle_groups == ("triceps", "shoulders")


def test_targets_and_sync_state(repo):
    repo.set_targets([VolumeTarget("chest", 14), VolumeTarget("biceps", 10)])
    repo.set_targets([VolumeTarget("chest", 16)])

    targets = repo.get_targets()
    assert targets["chest"].sets_per_week == 16
    assert targets["biceps"].sets_per_week == 10

    assert repo.get_state("missing") is None
    repo.set_state("hevy_last_sync_at", "2026-07-30T12:00:00Z")
    assert repo.get_state("hevy_last_sync_at") == "2026-07-30T12:00:00Z"


def test_runs_round_trip(repo):
    repo.upsert_runs(
        [
            {
                "id": "r-1",
                "start_time": "2026-07-29T05:15:17Z",
                "end_time": "2026-07-29T05:48:11Z",
                "exercise_type": "RUNNING",
                "distance_m": 4123.508,
                "active_duration_s": 1970.0,
                "avg_heart_rate": 135.0,
            }
        ]
    )
    runs = repo.get_runs(date(2026, 7, 27), date(2026, 8, 3))

    assert len(runs) == 1
    assert runs[0].pace_seconds_per_km == pytest.approx(477.75, abs=0.1)
