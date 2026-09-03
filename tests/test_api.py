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
    """The strip follows the section tabs but not the filter (#14).

    It reports both, using the window the rules actually read rather than a
    period named in the UI, so the label cannot drift from `insight_window`.
    """
    body = client.get("/api/insights").json()

    assert set(body) == {"section", "window", "weeks", "insights"}
    assert body["section"] is None  # unscoped: every finding
    assert body["weeks"] == queries.INSIGHT_LOOKBACK_WEEKS
    assert isinstance(body["insights"], list)

    start, end = queries.insight_window()
    assert body["window"] == queries.describe_window(start, end)
    # The span is the rules', not the dashboard's default.
    assert (end - start).days == queries.INSIGHT_LOOKBACK_WEEKS * 7 + 1


def test_insights_can_be_scoped_to_one_screen(client):
    """The seeded fixture is all lifting, so Gym has findings and Run does not
    -- which is the honest answer for someone who has logged no runs."""
    everything = client.get("/api/insights").json()["insights"]
    gym = client.get("/api/insights", params={"section": "gym"}).json()
    run = client.get("/api/insights", params={"section": "run"}).json()

    assert gym["section"] == "gym"
    assert {i["rule"] for i in gym["insights"]} <= {
        "volume_drop", "coverage_gap", "stall", "progression_ready", "recovery_flag"
    }
    assert {i["rule"] for i in run["insights"]} <= {
        "running_shortfall", "aei_trend", "recovery_flag"
    }
    # Nothing is lost by splitting; recovery is the only rule on both.
    assert len(gym["insights"]) + len(run["insights"]) >= len(everything)


def test_an_unknown_section_is_a_400_not_a_silent_empty_strip(client):
    res = client.get("/api/insights", params={"section": "cardio"})
    assert res.status_code == 400
    assert "cardio" in res.json()["detail"]


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


def test_the_plan_endpoint_is_not_scoped_by_the_filter(client):
    """A plan is one specific week, so the time-horizon filter has nothing to
    say about it -- passing one must not change the answer."""
    plain = client.get("/api/plan").json()
    filtered = client.get("/api/plan", params={"window": "last-7-days"}).json()

    assert plain == filtered
    assert set(plain) == {"available", "reason", "plan", "problems", "adherence"}


def test_a_malformed_week_is_a_400_not_a_500(client):
    res = client.get("/api/plan", params={"week": "next tuesday"})

    assert res.status_code == 400
    assert "YYYY-MM-DD" in res.json()["detail"]


# --- goals, constraints and intake ------------------------------------------


def test_constraints_ship_with_goals_in_one_round_trip(client):
    # The home screen shows them together, and `dashboard` set the precedent of
    # one request per screen.
    client.post("/api/constraints", json={"weekday": 2, "kind": "no_high_impact", "reason": "knee"})
    body = client.get("/api/goals").json()

    assert body["constraints"][0]["weekday_name"] == "Wednesday"
    assert "goals" in body and "running_target" in body


def test_a_race_goal_can_be_created_over_the_api(client):
    created = client.post(
        "/api/goals",
        json={"type": "race_time", "subject": "marathon", "target_value": 14400},
    )
    assert created.status_code == 200
    stored = client.get("/api/goals").json()["goals"]
    assert [g["subject"] for g in stored if g["type"] == "race_time"] == ["marathon"]


def test_an_invalid_race_goal_is_a_400_not_a_500(client):
    # Model validation, so the API rejects exactly what the CLI rejects.
    response = client.post("/api/goals", json={"type": "race_time", "target_value": 14400})
    assert response.status_code == 400
    assert "needs a subject" in response.json()["detail"]


def test_an_unknown_constraint_kind_is_a_400(client):
    response = client.post("/api/constraints", json={"weekday": 2, "kind": "no_burpees"})
    assert response.status_code == 400


def test_a_weekday_outside_the_week_is_refused_before_the_model_layer(client):
    # Pydantic's ge/le, so the error names the field rather than raising later.
    assert client.post("/api/constraints", json={"weekday": 9, "kind": "no_lifting"}).status_code == 422


def test_a_constraint_can_be_deleted_and_deleting_twice_is_a_404(client):
    created = client.post("/api/constraints", json={"weekday": 0, "kind": "no_lifting"}).json()
    assert client.delete(f"/api/constraints/{created['id']}").status_code == 200
    assert client.delete(f"/api/constraints/{created['id']}").status_code == 404


def test_intake_writes_nothing_even_when_it_finds_goals(client, monkeypatch):
    """The whole contract of the endpoint: it proposes, the user saves.

    A parser that quietly persisted would put the model between the user and
    their own record, which is the thing write-back exists to prevent.
    """
    from fitness_ledger import intake

    async def fake_parse(config, text, today=None):
        return intake.empty_proposal(
            goals=[{"type": "consistency", "subject": None, "target_value": 4,
                    "target_date": None, "status": "active", "id": None, "created_at": None}]
        )

    monkeypatch.setattr(intake, "parse", fake_parse)
    body = client.post("/api/intake", json={"text": "train four times a week"}).json()

    assert len(body["goals"]) == 1
    assert client.get("/api/goals").json()["goals"] == []


def test_intake_returns_the_referral_without_calling_a_model(client):
    # No provider is configured in the test config; a red flag must still work,
    # which is only true because the check runs before the transport is built.
    body = client.post(
        "/api/intake",
        json={"text": "my achilles feels like it's snapping under the bar"},
    ).json()

    assert body["safety"] == ["snapping"]
    assert body["goals"] == []


def test_empty_intake_text_is_refused(client):
    assert client.post("/api/intake", json={"text": ""}).status_code == 422


# --- goal lifecycle and progress --------------------------------------------


def test_editing_a_goal_archives_the_old_one_rather_than_overwriting(client):
    """A goal is an input to past planning decisions.

    The coach reads active goals when it writes a rationale, so rewriting one
    in place would silently change the recorded reason a past week looked as it
    did. PATCH supersedes; the original survives as `abandoned`.
    """
    created = client.post(
        "/api/goals",
        json={"type": "strength_1rm", "subject": "Bench Press", "target_value": 80},
    ).json()

    revised = client.patch(
        f"/api/goals/{created['id']}",
        json={"type": "strength_1rm", "subject": "Bench Press", "target_value": 100},
    )
    assert revised.status_code == 200
    assert revised.json()["supersedes"] == created["id"]

    everything = client.get("/api/goals?include_inactive=true").json()["goals"]
    by_id = {goal["id"]: goal for goal in everything}
    assert by_id[created["id"]]["status"] == "abandoned"
    assert by_id[revised.json()["id"]]["status"] == "active"

    # And the active list shows exactly one.
    active = client.get("/api/goals").json()["goals"]
    assert [goal["target_value"] for goal in active] == [100]


def test_editing_a_goal_that_does_not_exist_is_a_404(client):
    response = client.patch(
        "/api/goals/999", json={"type": "consistency", "target_value": 4}
    )
    assert response.status_code == 404


def test_an_invalid_revision_is_refused_without_archiving_the_original(client):
    # Otherwise a typo would lose the goal and store nothing in its place.
    created = client.post("/api/goals", json={"type": "consistency", "target_value": 4}).json()
    bad = client.patch(f"/api/goals/{created['id']}", json={"type": "race_time", "target_value": 1})
    assert bad.status_code == 400

    still_active = client.get("/api/goals").json()["goals"]
    assert [goal["id"] for goal in still_active] == [created["id"]]


def test_progress_rides_along_with_the_goals_list(client):
    # One request per screen, as `dashboard` established.
    created = client.post("/api/goals", json={"type": "consistency", "target_value": 4}).json()
    body = client.get("/api/goals").json()
    assert str(created["id"]) in body["progress"]
    assert body["progress"][str(created["id"])]["type"] == "consistency"


def test_a_race_goal_reports_that_it_cannot_be_measured_yet(client):
    """Not guessed. Predicting a race time needs a pace model that does not
    exist here, and an invented figure on a goal card would be believed."""
    created = client.post(
        "/api/goals",
        json={"type": "race_time", "subject": "5k", "target_value": 1320},
    ).json()

    progress = client.get(f"/api/goals/{created['id']}/progress").json()
    assert progress["measurable"] is False
    # None, not 0 -- "no data" and "no progress" are different answers.
    assert progress["current"] is None
    assert progress["fraction"] is None


def test_the_composed_question_carries_the_numbers(client):
    """The model explains a figure it was given rather than deriving one."""
    created = client.post("/api/goals", json={"type": "consistency", "target_value": 4}).json()
    body = client.get(f"/api/goals/{created['id']}/progress").json()

    assert "4 sessions a week" in body["question"]
    assert "rather than recomputing" in body["question"]


def test_progress_for_a_goal_that_does_not_exist_is_a_404(client):
    assert client.get("/api/goals/999/progress").status_code == 404
