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
