"""FastAPI backend.

Every endpoint is a thin wrapper over queries.py -- no computation happens here,
so the API and the CLI can never disagree about a number. Read-only apart from
target configuration and an explicitly triggered sync; nothing writes to Hevy in
v0.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import queries
from .config import Config
from .db import SQLiteRepository
from .models import VolumeTarget
from .queries import WindowError

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Fitness ledger", version="0.2.0")
_config = Config.load()


def repo() -> SQLiteRepository:
    """One short-lived connection per request; SQLite dislikes sharing across threads."""
    return SQLiteRepository(_config.db_path, _config.local_utc_offset_minutes)


def _guard(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except WindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/dashboard")
def get_dashboard() -> dict[str, Any]:
    """Everything the front page needs, in one round trip."""
    with repo() as repository:
        return queries.dashboard(repository, _config)


@app.get("/api/volume")
def get_volume(window: str = Query("last-week")) -> dict[str, Any]:
    with repo() as repository:
        return _guard(queries.volume_report, repository, _config, window)


@app.get("/api/muscle/{muscle_group}")
def get_muscle(muscle_group: str, window: str = Query("last-week")) -> dict[str, Any]:
    with repo() as repository:
        return _guard(queries.muscle_volume, repository, _config, muscle_group, window)


@app.get("/api/trend")
def get_trend(weeks: int = Query(8, ge=1, le=52), muscle_group: str | None = None) -> dict[str, Any]:
    with repo() as repository:
        return queries.volume_trend(repository, _config, weeks, muscle_group)


@app.get("/api/strength")
def get_strength(weeks: int = Query(16, ge=1, le=104)) -> dict[str, Any]:
    with repo() as repository:
        return queries.strength_progress(repository, _config, weeks)


@app.get("/api/progression")
def get_progression() -> list[dict[str, Any]]:
    with repo() as repository:
        return queries.progression_report(repository, _config)


@app.get("/api/insights")
def get_insights() -> list[dict[str, Any]]:
    with repo() as repository:
        return queries.insight_report(repository, _config)


@app.get("/api/exercise/{exercise}")
def get_exercise(exercise: str, weeks: int = Query(12, ge=1, le=104)) -> dict[str, Any]:
    with repo() as repository:
        return queries.exercise_progress(repository, _config, exercise, weeks)


@app.get("/api/runs")
def get_runs(window: str = Query("last-4-weeks")) -> dict[str, Any]:
    with repo() as repository:
        return _guard(queries.run_log, repository, _config, window)


@app.get("/api/health-metrics")
def get_health(window: str = Query("last-2-weeks")) -> dict[str, Any]:
    with repo() as repository:
        return _guard(queries.health_summary, repository, _config, window)


class TargetUpdate(BaseModel):
    muscle_group: str
    sets_per_week: float = Field(ge=0, le=60)
    frequency_per_week: int = Field(default=2, ge=0, le=7)


@app.get("/api/targets")
def read_targets() -> list[dict[str, Any]]:
    with repo() as repository:
        targets = queries.get_targets(repository)
        return [
            {
                "muscle_group": target.muscle_group,
                "sets_per_week": target.sets_per_week,
                "frequency_per_week": target.frequency_per_week,
                "size_class": target.size_class,
            }
            for target in sorted(targets.values(), key=lambda t: t.muscle_group)
        ]


@app.put("/api/targets")
def write_targets(updates: list[TargetUpdate]) -> dict[str, int]:
    with repo() as repository:
        repository.set_targets(
            VolumeTarget(u.muscle_group, u.sets_per_week, u.frequency_per_week)
            for u in updates
        )
    return {"updated": len(updates)}


class RepRangeUpdate(BaseModel):
    exercise_template_id: str
    rep_low: int = Field(ge=1, le=50)
    rep_high: int = Field(ge=1, le=50)


@app.put("/api/rep-ranges")
def write_rep_range(update: RepRangeUpdate) -> dict[str, str]:
    if update.rep_low > update.rep_high:
        raise HTTPException(status_code=400, detail="rep_low must not exceed rep_high")
    with repo() as repository:
        repository.set_rep_range(update.exercise_template_id, update.rep_low, update.rep_high)
    return {"status": "ok"}


@app.post("/api/sync")
async def trigger_sync(weeks: int = Query(12, ge=1, le=104)) -> dict[str, Any]:
    """Pull fresh data. Explicit: nothing syncs on its own until v0.4."""
    from datetime import date, timedelta

    from .mcp_client import health_client, hevy_client
    from .sync import sync_exercise_points, sync_health_daily, sync_hevy

    with repo() as repository:
        async with hevy_client(_config) as hevy:
            result = await sync_hevy(hevy, repository)
        since = date.today() - timedelta(weeks=weeks)
        async with health_client(_config) as health:
            result["exercise_points"] = await sync_exercise_points(health, repository, since)
            result["health_daily"] = await sync_health_daily(
                health, repository, since, date.today() + timedelta(days=1)
            )
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def healthcheck() -> JSONResponse:
    with repo() as repository:
        return JSONResponse({"status": "ok", "workouts_cached": repository.count_workouts()})


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
