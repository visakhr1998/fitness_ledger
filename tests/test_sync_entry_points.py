"""Both entry points must run the same sync.

The CLI and the dashboard each used to list the sync steps themselves, and the
CLI list was two short: vitals and run metrics ran only from the UI. AEI
therefore existed or did not depending on which entry point you used, and a
METHOD_VERSION bump did not apply from the command line at all (#16).

These tests pin the shared definition rather than the two call sites, so adding
a sixth step cannot leave one caller behind.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace

import pytest

from fitness_ledger import api, cli, sync
from fitness_ledger.db import SQLiteRepository


class FakeClient:
    """Stands in for an MCPClient; never opens a subprocess."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture()
def recorded(monkeypatch):
    """Stub every step so nothing touches the network, and record the order."""
    calls: list[str] = []

    def step(name, result):
        async def run(*args, **kwargs):
            calls.append(name)
            return result

        return run

    monkeypatch.setattr(sync, "sync_hevy", step("hevy", {"mode": "incremental"}))
    monkeypatch.setattr(sync, "sync_exercise_points", step("exercise points", 7))
    monkeypatch.setattr(sync, "sync_health_daily", step("daily health", {"steps": 3}))
    monkeypatch.setattr(sync, "sync_vitals", step("vitals", {"vo2_max": 1}))
    monkeypatch.setattr(sync, "sync_run_metrics", step("run metrics", {"recomputed": 2}))
    monkeypatch.setattr("fitness_ledger.mcp_client.hevy_client", lambda config: FakeClient())
    monkeypatch.setattr("fitness_ledger.mcp_client.health_client", lambda config: FakeClient())
    return calls


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "sync.db", 120) as repository:
        yield repository


def test_sync_all_runs_every_step_in_order(recorded, repo):
    asyncio.run(sync.sync_all(object(), repo))
    assert recorded == list(sync.SYNC_STEPS)


def test_the_cli_runs_every_step(recorded, repo, tmp_path, monkeypatch):
    """The actual #16 regression: `ledger sync` skipped vitals and run metrics."""
    monkeypatch.setattr(cli, "open_repo", lambda config: _Passthrough(repo))
    config = replace(api._config, db_path=tmp_path / "sync.db")

    exit_code = asyncio.run(
        cli.cmd_sync(config, argparse.Namespace(full=False, weeks=12))
    )

    assert exit_code == 0
    assert recorded == list(sync.SYNC_STEPS)


def test_the_dashboard_runs_every_step(recorded, repo, monkeypatch):
    monkeypatch.setattr(api, "repo", lambda: _Passthrough(repo))

    asyncio.run(api._run_sync(12))

    assert recorded == list(sync.SYNC_STEPS)
    assert api._sync_state["status"] == "done"
    assert [entry["name"] for entry in api._sync_state["steps"]] == list(sync.SYNC_STEPS)


def test_a_step_failure_is_reported_not_swallowed(recorded, repo, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("health server said no")

    monkeypatch.setattr(sync, "sync_vitals", boom)
    monkeypatch.setattr(api, "repo", lambda: _Passthrough(repo))

    asyncio.run(api._run_sync(12))

    assert api._sync_state["status"] == "error"
    assert "health server said no" in api._sync_state["error"]
    # The steps before the failure still count as done.
    assert recorded == ["hevy", "exercise points", "daily health"]


class _Passthrough:
    """Hands back an already-open repo without closing it on exit."""

    def __init__(self, repository):
        self.repository = repository

    def __enter__(self):
        return self.repository

    def __exit__(self, *exc):
        return False


# --- the exercise catalogue -------------------------------------------------
# Hevy names this field `equipment`; this repo calls it `equipment_category`.
# Reading only our own name stored NULL for all 461 templates, so
# `progression.increment_for` fell back to 2.5 kg for every exercise -- and
# nothing tested it, which is how it survived from v0.1.


class CatalogueClient:
    """Returns one page of templates in Hevy's real payload shape."""

    def __init__(self, items):
        self.items = items

    async def call(self, tool, arguments=None):
        assert tool == "hevy_list_exercise_templates"
        return {"items": self.items, "has_more": False}


def test_equipment_is_read_from_the_field_hevy_actually_sends(tmp_path):
    from fitness_ledger.sync import sync_exercise_templates

    # Verbatim shape from a live hevy_list_exercise_templates response.
    client = CatalogueClient([
        {
            "id": "79D0BB3A",
            "title": "Bench Press (Barbell)",
            "type": "weight_reps",
            "primary_muscle_group": "chest",
            "secondary_muscle_groups": ["triceps", "shoulders"],
            "equipment": "barbell",
            "is_custom": False,
        },
        {
            "id": "29083183",
            "title": "Chin Up",
            "type": "reps_only",
            "primary_muscle_group": "lats",
            "secondary_muscle_groups": [],
            "equipment": "none",
            "is_custom": False,
        },
    ])

    with SQLiteRepository(tmp_path / "catalogue.db", 120) as repo:
        assert asyncio.run(sync_exercise_templates(client, repo)) == 2
        stored = {t.id: t.equipment_category for t in repo.get_templates().values()}

    assert stored["79D0BB3A"] == "barbell"
    # The row that matters: "none" must survive as itself, because it is what
    # makes a bodyweight increment 0.0 rather than the 2.5 kg default.
    assert stored["29083183"] == "none"


def test_the_older_field_name_is_still_accepted(tmp_path):
    # The payload has carried each name at different times; reading only one is
    # what caused the bug in the first place.
    from fitness_ledger.sync import sync_exercise_templates

    client = CatalogueClient([
        {"id": "X", "title": "Thing", "equipment_category": "machine"},
    ])
    with SQLiteRepository(tmp_path / "legacy.db", 120) as repo:
        asyncio.run(sync_exercise_templates(client, repo))
        assert repo.get_templates()["X"].equipment_category == "machine"
