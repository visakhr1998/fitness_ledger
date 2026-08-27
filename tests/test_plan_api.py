"""The plan write path: generate, decide, and turn a session into a routine.

The generate half is background work over a model, so what is tested here is
the wiring around it -- that a request starts a job and never blocks, that a
failure reaches the callout rather than a traceback, and that nothing reaches
Hevy on the way. The model itself is covered by the eval suite.

The approval half is where care matters: **Hevy has no delete endpoint**, so
these pin the two properties that keep an accidental write impossible --
approving a plan makes no outbound call at all, and writing a session still
goes through the existing propose -> diff -> confirm surface.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fitness_ledger import api
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import (
    ExerciseTemplate,
    Plan,
    PlannedExercise,
    PlannedSession,
    VolumeTarget,
)

MONDAY = date.today() - timedelta(days=date.today().weekday()) + timedelta(days=7)


def iso(day: date) -> str:
    return day.isoformat() + "T12:00:00+00:00"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "plan-api.db"
    monkeypatch.setattr(api, "_config", replace(api._config, db_path=db))
    # Module state, so one test's run would otherwise be visible to the next.
    monkeypatch.setattr(
        api, "_plan_state", {"status": "idle", "week": None, "plan_id": None, "error": None}
    )

    with SQLiteRepository(db, 120) as repo:
        repo.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
            ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", ("biceps",), "barbell"),
        ])
        repo.set_targets([VolumeTarget("chest", 14, 2), VolumeTarget("upper_back", 14, 2)])
        repo.upsert_workout({
            "id": "w1",
            "title": "Session",
            "start_time": iso(date.today() - timedelta(days=7)),
            "end_time": iso(date.today() - timedelta(days=7)),
            "exercises": [{
                "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                "sets": [{"index": 0, "type": "normal", "weight_kg": 80, "reps": 8}],
            }],
        })
    return TestClient(api.app)


def store_plan(client, **overrides) -> int:
    """A two-session week with uneven set counts, so a flattened write shows."""
    plan = Plan(
        week_start=MONDAY,
        sessions=(
            PlannedSession(
                local_date=MONDAY,
                kind="lift",
                focus="upper",
                exercises=(
                    PlannedExercise("BENCH", "Bench Press", 5, ("chest",)),
                    PlannedExercise("ROW", "Barbell Row", 7, ("upper_back",)),
                ),
            ),
            PlannedSession(local_date=MONDAY + timedelta(days=2), kind="run", distance_km=8.0),
        ),
        rationale="chest and back are short",
        **overrides,
    )
    with SQLiteRepository(api._config.db_path, 120) as repo:
        return repo.add_plan(plan).id


# --- generating --------------------------------------------------------------


def test_generating_returns_immediately_rather_than_blocking(client, monkeypatch):
    """~3 model requests and tens of seconds, so a plain request would hang."""
    started: list[str | None] = []

    async def fake_run(week):
        started.append(week)

    monkeypatch.setattr(api, "_run_plan", fake_run)
    res = client.post("/api/plan")

    assert res.status_code == 200
    assert res.json() == {"status": "started"}
    assert started == [None]


def test_a_second_request_does_not_start_a_second_run(client, monkeypatch):
    calls: list[str | None] = []

    async def fake_run(week):
        calls.append(week)

    monkeypatch.setattr(api, "_run_plan", fake_run)
    monkeypatch.setitem(api._plan_state, "status", "running")

    body = client.post("/api/plan").json()

    assert body["status"] == "running"
    assert calls == [], "a run was already in flight; starting another spends quota twice"


def test_a_malformed_week_is_rejected_before_any_work_starts(client, monkeypatch):
    async def fake_run(week):  # pragma: no cover - must never be reached
        raise AssertionError("started a run for an unparseable week")

    monkeypatch.setattr(api, "_run_plan", fake_run)
    res = client.post("/api/plan", params={"week": "next tuesday"})

    assert res.status_code == 400
    assert "YYYY-MM-DD" in res.json()["detail"]


def test_status_starts_idle_and_is_pollable(client):
    body = client.get("/api/plan/status").json()
    assert body["status"] == "idle"
    assert body["plan_id"] is None


# --- deciding ----------------------------------------------------------------


def test_approving_a_plan_writes_nothing_outside_this_app(client, monkeypatch):
    """The plan-level decision is a ledger state change. Hevy is a later,
    separate step -- if approving a week wrote routines, an accidental click
    would be unrecoverable, because Hevy cannot delete."""
    def explode(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("approving a plan called out to Hevy")

    monkeypatch.setattr(api.writeback, "build_routine", explode)

    plan_id = store_plan(client)
    res = client.put(f"/api/plan/{plan_id}", json={"status": "approved"})

    assert res.status_code == 200
    assert res.json() == {"id": plan_id, "status": "approved"}
    assert client.get("/api/plan").json()["plan"]["status"] == "approved"


def test_a_plan_can_be_rejected(client):
    plan_id = store_plan(client)
    assert client.put(f"/api/plan/{plan_id}", json={"status": "rejected"}).status_code == 200
    assert client.get("/api/plan").json()["plan"]["status"] == "rejected"


def test_deciding_twice_is_a_conflict_not_a_silent_overwrite(client):
    plan_id = store_plan(client)
    client.put(f"/api/plan/{plan_id}", json={"status": "approved"})

    res = client.put(f"/api/plan/{plan_id}", json={"status": "rejected"})

    assert res.status_code == 409
    assert "already approved" in res.json()["detail"]


def test_superseded_is_not_a_decision_a_user_can_make(client):
    """It is what storage does when a revision replaces a plan."""
    plan_id = store_plan(client)
    res = client.put(f"/api/plan/{plan_id}", json={"status": "superseded"})

    assert res.status_code == 400


def test_deciding_an_unknown_plan_is_a_404(client):
    assert client.put("/api/plan/9999", json={"status": "approved"}).status_code == 404


# --- a session into a routine ------------------------------------------------


def test_the_routine_carries_the_allocated_set_counts(client):
    """5 and 7, not three of each. Those counts are why `planning.py` exists;
    flattening them here would discard the allocation the week was built on."""
    plan_id = store_plan(client)

    body = client.post(
        f"/api/plan/{plan_id}/routine", json={"session_date": MONDAY.isoformat()}
    ).json()

    counts = {
        exercise["exercise_template_id"]: len(exercise["sets"])
        for exercise in body["payload"]["exercises"]
    }
    assert counts == {"BENCH": 5, "ROW": 7}


def test_proposing_a_routine_stores_it_for_review_and_writes_nothing(client):
    plan_id = store_plan(client)

    body = client.post(
        f"/api/plan/{plan_id}/routine", json={"session_date": MONDAY.isoformat()}
    ).json()

    assert body["status"] == "proposed"
    assert body["diff"]["rows"], "a write with no diff is exactly what must not exist"

    logged = client.get("/api/writeback").json()
    assert [row["status"] for row in logged] == ["proposed"]
    assert logged[0]["hevy_id"] is None


def test_a_run_day_has_no_routine_to_write(client):
    """Hevy routines are lifting. A run session is not a 404 by accident."""
    plan_id = store_plan(client)
    run_day = (MONDAY + timedelta(days=2)).isoformat()

    res = client.post(f"/api/plan/{plan_id}/routine", json={"session_date": run_day})

    assert res.status_code == 404
    assert "no lifting session" in res.json()["detail"]


def test_a_day_outside_the_plan_is_a_404(client):
    plan_id = store_plan(client)
    res = client.post(
        f"/api/plan/{plan_id}/routine",
        json={"session_date": (MONDAY + timedelta(days=5)).isoformat()},
    )
    assert res.status_code == 404


def test_a_malformed_session_date_is_a_400(client):
    plan_id = store_plan(client)
    res = client.post(f"/api/plan/{plan_id}/routine", json={"session_date": "monday"})
    assert res.status_code == 400


# --- goals, targets, availability --------------------------------------------


def test_goals_round_trip(client):
    created = client.post(
        "/api/goals", json={"type": "strength_1rm", "subject": "Bench Press", "target_value": 100}
    ).json()

    body = client.get("/api/goals").json()
    assert [goal["id"] for goal in body["goals"]] == [created["id"]]
    assert body["goals"][0]["target_value"] == 100


def test_a_strength_goal_without_a_subject_is_rejected_by_the_model(client):
    """Validated in models.py so the API and CLI reject the same things."""
    res = client.post("/api/goals", json={"type": "strength_1rm", "target_value": 100})

    assert res.status_code == 400
    assert "subject" in res.json()["detail"]


def test_an_unknown_goal_type_is_a_400_not_a_stored_row(client):
    res = client.post("/api/goals", json={"type": "vibes", "target_value": 1})

    assert res.status_code == 400
    assert client.get("/api/goals").json()["goals"] == []


def test_closing_a_goal_takes_it_out_of_the_active_list(client):
    goal_id = client.post(
        "/api/goals", json={"type": "consistency", "target_value": 4}
    ).json()["id"]

    assert client.put(f"/api/goals/{goal_id}", json={"status": "achieved"}).status_code == 200
    assert client.get("/api/goals").json()["goals"] == []
    assert client.get("/api/goals", params={"include_inactive": True}).json()["goals"]


def test_the_running_target_can_be_set_from_the_api(client):
    """Without it the priority ranking's third rank has nothing to measure."""
    assert client.get("/api/goals").json()["running_target"] is None

    client.put("/api/running-target", json={"distance_km_per_week": 25, "sessions_per_week": 3})

    assert client.get("/api/goals").json()["running_target"] == {
        "distance_km_per_week": 25.0,
        "sessions_per_week": 3,
    }


def test_declaring_a_day_lost_no_longer_needs_a_terminal(client):
    day = (MONDAY + timedelta(days=1)).isoformat()

    client.put("/api/availability", json={"date": day, "reason": "travelling"})
    body = client.get("/api/availability", params={"week": MONDAY.isoformat()}).json()

    assert [entry["date"] for entry in body["unavailable"]] == [day]
    assert body["unavailable"][0]["reason"] == "travelling"
    assert body["unavailable"][0]["source"] == "declared"


def test_only_exceptions_are_stored_so_an_untouched_week_is_empty(client):
    body = client.get("/api/availability", params={"week": MONDAY.isoformat()}).json()
    assert body["unavailable"] == []


def test_a_day_can_be_handed_back(client):
    day = (MONDAY + timedelta(days=1)).isoformat()
    client.put("/api/availability", json={"date": day})

    assert client.delete(f"/api/availability/{day}").json()["cleared"] is True
    assert client.get("/api/availability", params={"week": MONDAY.isoformat()}).json()[
        "unavailable"
    ] == []


def test_clearing_a_day_that_was_never_lost_is_not_an_error(client):
    res = client.delete(f"/api/availability/{MONDAY.isoformat()}")
    assert res.status_code == 200
    assert res.json()["cleared"] is False
