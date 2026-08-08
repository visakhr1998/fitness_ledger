"""API contract tests against a seeded temporary database.

The API is a thin wrapper over queries.py, so these check wiring and error
handling rather than re-testing the maths.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fitness_ledger import api, queries
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import ExerciseTemplate, VolumeTarget

TODAY = date.today()


def iso(day: date) -> str:
    return day.isoformat() + "T12:00:00+00:00"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    # Config is frozen by design, so point the module at a replacement rather
    # than mutating the live one.
    monkeypatch.setattr(api, "_config", replace(api._config, db_path=db))

    with SQLiteRepository(db, 120) as repo:
        repo.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
            ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", ("biceps",), "barbell"),
        ])
        repo.set_targets([VolumeTarget("chest", 14, 2), VolumeTarget("upper_back", 14, 2)])
        for weeks_ago in range(1, 7):
            day = TODAY - timedelta(weeks=weeks_ago)
            repo.upsert_workout({
                "id": f"w{weeks_ago}",
                "title": "Session",
                "start_time": iso(day),
                "end_time": iso(day),
                "exercises": [
                    {
                        "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                        "sets": [
                            {"index": 0, "type": "warmup", "weight_kg": 40, "reps": 10},
                            {"index": 1, "type": "normal", "weight_kg": 80, "reps": 8},
                            {"index": 2, "type": "normal", "weight_kg": 80, "reps": 8},
                        ],
                    },
                    {
                        "index": 1, "title": "Barbell Row", "exercise_template_id": "ROW",
                        "sets": [{"index": 0, "type": "normal", "weight_kg": 60, "reps": 10}],
                    },
                ],
            })
    return TestClient(api.app)


def test_healthcheck_reports_cache_size(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["workouts_cached"] == 6


def test_dashboard_returns_every_section(client):
    body = client.get("/api/dashboard").json()
    assert set(body) >= {
        "strength", "volume", "last_week", "trend", "insights", "progression", "runs", "health",
    }


def test_volume_endpoint_excludes_warmups(client):
    body = client.get("/api/volume", params={"window": "last-week"}).json()
    chest = next(m for m in body["muscles"] if m["muscle_group"] == "chest")
    assert chest["effective_sets"] == 2.0
    assert body["working_sets"] == 3


def test_insights_report_the_scope_they_were_found_over(client):
    """The coach strip obeys neither the section tabs nor the filter (#14).

    It says so, using the window the rules actually read rather than a period
    named in the UI, so the label cannot drift from `insight_window`.
    """
    body = client.get("/api/insights").json()

    assert set(body) == {"window", "weeks", "insights"}
    assert body["weeks"] == queries.INSIGHT_LOOKBACK_WEEKS
    assert isinstance(body["insights"], list)

    start, end = queries.insight_window()
    assert body["window"] == queries.describe_window(start, end)
    # The span is the rules', not the dashboard's default.
    assert (end - start).days == queries.INSIGHT_LOOKBACK_WEEKS * 7 + 1


def test_insight_window_ignores_the_time_horizon_filter(client):
    """Passing a filter must not narrow it: a rule on one part-finished week
    fires on nothing, so scaling these to 7 days would silence them."""
    plain = client.get("/api/insights").json()
    filtered = client.get("/api/insights", params={"window": "last-7-days"}).json()

    assert filtered["window"] == plain["window"]


def test_bad_window_is_a_400_not_a_500(client):
    res = client.get("/api/volume", params={"window": "whenever"})
    assert res.status_code == 400
    assert "Unrecognised window" in res.json()["detail"]


def test_targets_round_trip(client):
    res = client.put("/api/targets", json=[{"muscle_group": "chest", "sets_per_week": 18, "frequency_per_week": 3}])
    assert res.json() == {"updated": 1}

    targets = {t["muscle_group"]: t for t in client.get("/api/targets").json()}
    assert targets["chest"]["sets_per_week"] == 18
    assert targets["chest"]["frequency_per_week"] == 3


def test_target_validation_rejects_nonsense(client):
    assert client.put("/api/targets", json=[{"muscle_group": "chest", "sets_per_week": -5}]).status_code == 422


def test_rep_range_rejects_inverted_range(client):
    res = client.put("/api/rep-ranges", json={"exercise_template_id": "BENCH", "rep_low": 12, "rep_high": 6})
    assert res.status_code == 400


def test_rep_range_changes_progression_verdict(client):
    before = {r["exercise_template_id"]: r for r in client.get("/api/progression").json()}
    assert before["BENCH"]["ready_to_progress"] is False  # 8 reps, default range 6-10

    client.put("/api/rep-ranges", json={"exercise_template_id": "BENCH", "rep_low": 6, "rep_high": 8})
    after = {r["exercise_template_id"]: r for r in client.get("/api/progression").json()}
    assert after["BENCH"]["ready_to_progress"] is True


def test_insights_flag_the_stalled_lift(client):
    rules = {i["rule"] for i in client.get("/api/insights").json()["insights"]}
    assert "stall" in rules


def test_strength_series_has_points(client):
    body = client.get("/api/strength").json()
    bench = next(s for s in body["series"] if s["exercise_template_id"] == "BENCH")
    assert len(bench["points"]) == 6
    assert bench["points"][0]["date"] < bench["points"][-1]["date"]


def test_index_serves_the_dashboard(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Fitness ledger" in res.text
