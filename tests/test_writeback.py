"""Write-back proposals and diffs.

Hevy has no delete endpoint, so an accidental write cannot be undone in
software. These tests defend the property that matters most: proposing never
writes, and approving requires a specific reviewed proposal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fitness_ledger import api, writeback
from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import ExerciseTemplate

TODAY = date.today()


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "wb.db", 0) as repository:
        repository.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
            ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", (), "barbell"),
        ])
        # Three sessions at 80 kg for 10 reps: top of the default 6-10 range.
        for weeks in (3, 2, 1):
            day = TODAY - timedelta(weeks=weeks)
            repository.upsert_workout({
                "id": f"w{weeks}",
                "title": "Session",
                "start_time": day.isoformat() + "T12:00:00+00:00",
                "end_time": day.isoformat() + "T13:00:00+00:00",
                "exercises": [{
                    "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                    "sets": [
                        {"index": 0, "type": "normal", "weight_kg": 80, "reps": 10},
                        {"index": 1, "type": "normal", "weight_kg": 80, "reps": 10},
                    ],
                }],
            })
        yield repository


@pytest.fixture()
def config():
    return replace(Config.load(), local_utc_offset_minutes=0)


def test_ready_exercise_is_proposed_at_the_stepped_weight(repo, config):
    proposal = writeback.build_routine(repo, config, "Push", ["BENCH"])
    exercise = proposal.exercises[0]

    # Barbell increment is 2.5 kg, and reps drop back to the bottom of the range.
    assert exercise.sets[0].weight_kg == 82.5
    assert exercise.sets[0].reps == 6
    assert "stepping up" in exercise.rationale


def test_exercise_without_history_gets_no_invented_weight(repo, config):
    proposal = writeback.build_routine(repo, config, "Pull", ["ROW"])
    exercise = proposal.exercises[0]

    assert exercise.sets[0].weight_kg is None
    assert "no recent history" in exercise.rationale


def test_unknown_ids_are_dropped_not_guessed(repo, config):
    proposal = writeback.build_routine(repo, config, "Mixed", ["BENCH", "NOPE"])
    assert [e.exercise_template_id for e in proposal.exercises] == ["BENCH"]


def test_sets_per_exercise_is_respected(repo, config):
    proposal = writeback.build_routine(repo, config, "Push", ["BENCH"], sets_per_exercise=5)
    assert len(proposal.exercises[0].sets) == 5


def test_payload_matches_the_hevy_routine_shape(repo, config):
    payload = writeback.build_routine(repo, config, "Push", ["BENCH"]).as_payload()

    assert payload["title"] == "Push"
    exercise = payload["exercises"][0]
    assert exercise["exercise_template_id"] == "BENCH"
    assert set(exercise["sets"][0]) == {"type", "weight_kg", "reps"}


def test_diff_against_nothing_is_all_additions(repo, config):
    proposal = writeback.build_routine(repo, config, "Push", ["BENCH"])
    difference = writeback.diff(proposal)

    assert difference["added"] == 1
    assert difference["removed"] == 0
    assert "no delete endpoint" in difference["warning"]


def test_diff_detects_change_and_removal(repo, config):
    proposal = writeback.build_routine(repo, config, "Push", ["BENCH"])
    existing = {
        "exercises": [
            {"exercise_template_id": "BENCH", "title": "Bench Press",
             "sets": [{"weight_kg": 70, "reps": 8}]},
            {"exercise_template_id": "OLD", "title": "Dropped Lift",
             "sets": [{"weight_kg": 40, "reps": 12}]},
        ]
    }
    difference = writeback.diff(proposal, existing)
    by_exercise = {row["exercise"]: row for row in difference["rows"]}

    assert by_exercise["Bench Press"]["change"] == "change"
    assert by_exercise["Dropped Lift"]["change"] == "remove"
    assert difference["removed"] == 1


# --- API contract ----------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch, repo):
    monkeypatch.setattr(api, "_config", replace(api._config, db_path=repo.db_path))
    return TestClient(api.app)


def test_propose_stores_a_reviewable_proposal_without_writing(client):
    response = client.post(
        "/api/writeback/propose",
        json={"title": "Push day", "exercise_ids": ["BENCH"]},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "proposed"
    assert body["diff"]["added"] == 1
    assert body["payload"]["exercises"][0]["sets"][0]["weight_kg"] == 82.5

    # It is in the audit trail, and still unwritten.
    log = client.get("/api/writeback").json()
    assert log[0]["status"] == "proposed"
    assert log[0]["hevy_id"] is None


def test_propose_rejects_an_empty_selection(client):
    assert client.post("/api/writeback/propose", json={"title": "x", "exercise_ids": []}).status_code == 422
    assert client.post(
        "/api/writeback/propose", json={"title": "x", "exercise_ids": ["NOPE"]}
    ).status_code == 400


def test_approving_an_unknown_proposal_is_a_404(client):
    assert client.post("/api/writeback/9999/approve").status_code == 404
