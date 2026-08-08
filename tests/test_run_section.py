"""The Run screen payload.

Every chart on that screen reads one array of runs. It used to read three
different shapes -- day-bucketed counts, day-bucketed kilometres, and a
heart-rate list that quietly dropped runs without one -- which is how a period
total came to be captioned "per day" and how a bar chart came to imply that
runs on adjacent bars were a fixed interval apart (#12, #13).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.sections import run_section

TODAY = date.today()


def iso(day: date) -> str:
    return day.isoformat() + "T07:00:00+00:00"


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "run.db", 120) as repository:
        repository.upsert_runs([
            # Deliberately irregular: two days apart, then eleven. An index-based
            # x axis draws those gaps identically.
            {
                "id": "r1", "start_time": iso(TODAY - timedelta(days=13)),
                "exercise_type": "RUNNING", "distance_m": 5000.0,
                "active_duration_s": 1800.0, "avg_heart_rate": 150.0,
            },
            {
                "id": "r2", "start_time": iso(TODAY - timedelta(days=11)),
                "exercise_type": "RUNNING", "distance_m": 3000.0,
                "active_duration_s": 1080.0, "avg_heart_rate": 145.0,
            },
            # No heart rate: counted and listed, never dropped.
            {
                "id": "r3", "start_time": iso(TODAY - timedelta(days=0)),
                "exercise_type": "RUNNING", "distance_m": 7000.0,
                "active_duration_s": 2400.0,
            },
            # Not a run: must not reach the Run screen at all.
            {
                "id": "w1", "start_time": iso(TODAY - timedelta(days=1)),
                "exercise_type": "WALKING", "distance_m": 2000.0,
                "active_duration_s": 1500.0, "avg_heart_rate": 100.0,
            },
        ])
        yield repository


@pytest.fixture()
def config(tmp_path):
    return Config.load()


def test_every_run_appears_once_oldest_first(repo, config):
    runs = run_section(repo, config, "last-30-days")["runs"]

    assert runs["count"] == 3
    assert [row["date"] for row in runs["list"]] == [
        (TODAY - timedelta(days=13)).isoformat(),
        (TODAY - timedelta(days=11)).isoformat(),
        TODAY.isoformat(),
    ]


def test_walks_are_not_runs(repo, config):
    runs = run_section(repo, config, "last-30-days")["runs"]
    assert all(row["distance_km"] != 2.0 for row in runs["list"])


def test_a_run_without_heart_rate_is_still_listed(repo, config):
    """It used to be filtered out, so the count and the chart disagreed."""
    runs = run_section(repo, config, "last-30-days")["runs"]

    assert len(runs["list"]) == runs["count"]
    missing = [row for row in runs["list"] if row["avg_heart_rate"] is None]
    assert len(missing) == 1
    assert missing[0]["distance_km"] == 7.0


def test_duration_is_carried_for_the_tooltip(repo, config):
    """#13: the payload had no run duration, so the tooltip could not show one."""
    runs = run_section(repo, config, "last-30-days")["runs"]
    assert [row["duration_s"] for row in runs["list"]] == [1800.0, 1080.0, 2400.0]


def test_totals_are_period_totals_not_rates(repo, config):
    """The cards read off these directly; #12 captioned them "per day"."""
    runs = run_section(repo, config, "last-30-days")["runs"]

    assert runs["count"] == 3
    assert runs["total_km"] == pytest.approx(15.0)
    # Mean over the runs that have one, not over all three.
    assert runs["avg_heart_rate"] == 148
