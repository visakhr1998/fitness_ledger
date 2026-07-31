"""The dashboard must show data right up to today.

Regression cover for a real bug: every dashboard panel used week-based windows,
and `last-N-weeks` means N *complete* weeks. With today mid-week that silently
hid the newest days -- the runs table stopped five days ago and looked like a
sync failure. Trailing baselines still want complete weeks; recent-activity
views do not.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import ExerciseTemplate
from fitness_ledger.queries import dashboard, parse_window, volume_trend
from fitness_ledger.volume import week_start

TODAY = date.today()


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "recency.db", 0) as repository:
        repository.upsert_templates([
            ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell")
        ])
        # A session and a run every day for the last 10 days, including today.
        for offset in range(0, 10):
            day = TODAY - timedelta(days=offset)
            repository.upsert_workout({
                "id": f"w{offset}",
                "title": "Session",
                "start_time": day.isoformat() + "T12:00:00+00:00",
                "end_time": day.isoformat() + "T13:00:00+00:00",
                "exercises": [{
                    "index": 0, "title": "Bench Press", "exercise_template_id": "BENCH",
                    "sets": [{"index": 0, "type": "normal", "weight_kg": 80, "reps": 8}],
                }],
            })
            repository.upsert_runs([{
                "id": f"r{offset}",
                "start_time": day.isoformat() + "T07:00:00+00:00",
                "exercise_type": "RUNNING",
                "distance_m": 5000.0,
                "active_duration_s": 1800.0,
            }])
            repository.upsert_health_daily([
                (day.isoformat(), "sleep_minutes_asleep", 420.0, "min", ""),
                (day.isoformat(), "steps", 9000.0, None, ""),
            ])
        yield repository


@pytest.fixture()
def config():
    return replace(Config.load(), local_utc_offset_minutes=0)


def test_dashboard_runs_include_today(repo, config):
    dates = {row["date"] for row in dashboard(repo, config)["runs"]["runs"]}
    assert TODAY.isoformat() in dates, "the runs panel must reach today"
    assert (TODAY - timedelta(days=1)).isoformat() in dates


def test_dashboard_health_includes_today(repo, config):
    days = {row["date"] for row in dashboard(repo, config)["health"]["days"]}
    assert TODAY.isoformat() in days, "the recovery panel must reach today"


def test_dashboard_trend_includes_the_current_week(repo, config):
    weeks = dashboard(repo, config)["trend"]["weeks"]
    current = week_start(TODAY, config.week_starts_on).isoformat()

    assert weeks[-1]["week_starting"] == current
    assert weeks[-1]["partial"] is True
    assert all(w["partial"] is False for w in weeks[:-1])


def test_trend_still_excludes_the_current_week_by_default(repo, config):
    # Trailing averages must not be diluted by a part-finished week.
    weeks = volume_trend(repo, config, weeks=4)["weeks"]
    current = week_start(TODAY, config.week_starts_on).isoformat()

    assert all(w["week_starting"] != current for w in weeks)
    assert all(w["partial"] is False for w in weeks)


def test_last_n_weeks_semantics_are_unchanged(config):
    # The insight rules depend on this meaning complete weeks; pin it.
    start, end = parse_window("last-4-weeks", TODAY, config.week_starts_on)
    current = week_start(TODAY, config.week_starts_on)

    assert end == current
    assert (end - start).days == 28


def test_day_windows_reach_today(config):
    start, end = parse_window("last-28-days", TODAY, config.week_starts_on)
    assert end == TODAY + timedelta(days=1)
    assert start == TODAY - timedelta(days=28)
