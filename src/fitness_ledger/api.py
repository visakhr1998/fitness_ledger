"""FastAPI backend.

Every endpoint is a thin wrapper over queries.py -- no computation happens here,
so the API and the CLI can never disagree about a number. Read-only apart from
settings, an explicitly triggered sync, and approval-gated Hevy write-back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import queries, sections, writeback
from .config import Config
from .db import SQLiteRepository
from .models import VolumeTarget
from .queries import WindowError

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Fitness ledger", version="0.3.0")
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
def get_trend(
    weeks: int = Query(8, ge=1, le=52),
    muscle_group: str | None = None,
    include_current: bool = Query(True, description="include the in-progress week, flagged partial"),
) -> dict[str, Any]:
    with repo() as repository:
        return queries.volume_trend(repository, _config, weeks, muscle_group, include_current)


@app.get("/api/strength")
def get_strength(weeks: int = Query(16, ge=1, le=104)) -> dict[str, Any]:
    with repo() as repository:
        return queries.strength_progress(repository, _config, weeks)


@app.get("/api/progression")
def get_progression() -> list[dict[str, Any]]:
    with repo() as repository:
        return queries.progression_report(repository, _config)


@app.get("/api/insights")
def get_insights() -> dict[str, Any]:
    """Findings, plus the scope they were found over.

    The scope travels with them because the coach strip sits under the
    time-horizon filter and obeys neither it nor the section tabs. It reports
    the window rather than letting the UI name a period of its own, which would
    drift from `insight_window` the first time either changed.
    """
    start, end = queries.insight_window()
    with repo() as repository:
        return {
            "window": queries.describe_window(start, end),
            "weeks": queries.INSIGHT_LOOKBACK_WEEKS,
            "insights": queries.insight_report(repository, _config),
        }


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


# --- v0.3 Run / Gym sections ----------------------------------------------


@app.get("/api/run")
def run_section(
    window: str | None = Query(None, description="preset, e.g. last-30-days"),
    start: str | None = Query(None, description="custom range start, YYYY-MM-DD"),
    end: str | None = Query(None, description="custom range end, YYYY-MM-DD"),
) -> dict[str, Any]:
    with repo() as repository:
        return _guard(sections.run_section, repository, _config, window, start, end)


@app.get("/api/gym")
def gym_section(
    window: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict[str, Any]:
    with repo() as repository:
        return _guard(sections.gym_section, repository, _config, window, start, end)


@app.get("/api/vitals")
def vitals() -> dict[str, Any]:
    with repo() as repository:
        return sections.vitals_card(repository, _config)


@app.get("/api/exercises")
def exercises(only_logged: bool = Query(False)) -> list[dict[str, Any]]:
    with repo() as repository:
        return sections.exercise_catalog(repository, only_logged)


@app.get("/api/exercises/{template_id}")
def exercise_detail(
    template_id: str,
    window: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict[str, Any]:
    with repo() as repository:
        result = _guard(
            sections.exercise_detail, repository, _config, template_id, window, start, end
        )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class SettingsUpdate(BaseModel):
    """Only what a formula cannot derive, plus overrides that beat one."""

    sex: str | None = Field(default=None, pattern="^(male|female|)$")
    age: int | None = Field(default=None, ge=10, le=120)
    height_cm: float | None = Field(default=None, ge=50, le=260)
    weight_kg: float | None = Field(default=None, ge=20, le=400)
    max_heart_rate: float | None = Field(default=None, ge=100, le=230)


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    with repo() as repository:
        return repository.get_settings()


@app.put("/api/settings")
def write_settings(update: SettingsUpdate) -> dict[str, Any]:
    """Empty string clears a value; omitted fields are left alone."""
    with repo() as repository:
        for key, value in update.model_dump(exclude_unset=True).items():
            repository.set_setting(key, None if value in (None, "") else str(value))
        return repository.get_settings()


# --- sync with progress -----------------------------------------------------
# The UI shows a status callout, so sync runs in the background and reports
# state rather than blocking a request for the length of a TCX download.

_sync_state: dict[str, Any] = {"status": "idle", "steps": [], "error": None}


def _sync_step(name: str, detail: Any = None) -> None:
    _sync_state["steps"].append({"name": name, "detail": detail})


async def _run_sync(weeks: int) -> None:
    from .sync import sync_all

    _sync_state.update({"status": "running", "steps": [], "error": None})
    try:
        with repo() as repository:
            await sync_all(_config, repository, weeks=weeks, on_step=_sync_step)
        _sync_state["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - surface the failure to the callout
        _sync_state["status"] = "error"
        _sync_state["error"] = f"{type(exc).__name__}: {exc}"


@app.post("/api/sync")
async def start_sync(
    background: BackgroundTasks, weeks: int = Query(12, ge=1, le=104)
) -> dict[str, Any]:
    if _sync_state["status"] == "running":
        return {"status": "running", "detail": "a sync is already in progress"}
    background.add_task(_run_sync, weeks)
    return {"status": "started"}


@app.get("/api/sync/status")
def sync_status() -> dict[str, Any]:
    return _sync_state


# --- write-back -------------------------------------------------------------
# propose -> diff -> confirm -> write -> log. The propose step never calls Hevy,
# and nothing writes without a separate approval of a specific proposal id.


class RoutineProposal(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    exercise_ids: list[str] = Field(min_length=1, max_length=20)
    sets_per_exercise: int = Field(default=3, ge=1, le=10)


@app.post("/api/writeback/propose")
def propose_routine(request: RoutineProposal) -> dict[str, Any]:
    """Draft a routine and store it for review. No Hevy call happens here."""
    with repo() as repository:
        proposal = writeback.build_routine(
            repository, _config, request.title, request.exercise_ids, request.sets_per_exercise
        )
        if not proposal.exercises:
            raise HTTPException(status_code=400, detail="none of those exercise ids exist")
        difference = writeback.diff(proposal)
        proposal_id = repository.record_proposal(
            "routine", proposal.summary(), proposal.as_payload(), difference
        )
    return {
        "id": proposal_id,
        "summary": proposal.summary(),
        "payload": proposal.as_payload(),
        "diff": difference,
        "status": "proposed",
    }


@app.post("/api/writeback/{proposal_id}/approve")
async def approve_routine(proposal_id: int) -> dict[str, Any]:
    """Write an already-reviewed proposal to Hevy. Irreversible via the API."""
    from .mcp_client import MCPError, hevy_client

    with repo() as repository:
        stored = repository.get_proposal(proposal_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"no proposal {proposal_id}")
        if stored["status"] != "proposed":
            raise HTTPException(
                status_code=409,
                detail=f"proposal {proposal_id} is already {stored['status']}",
            )

        payload = json.loads(stored["payload_json"])
        try:
            async with hevy_client(_config) as hevy:
                created = await hevy.call("hevy_create_routine", {"params": payload})
        except MCPError as exc:
            repository.mark_proposal(proposal_id, "failed", error=str(exc))
            raise HTTPException(status_code=502, detail=f"Hevy rejected it: {exc}") from exc

        hevy_id = None
        if isinstance(created, dict):
            body = created.get("routine") or created
            if isinstance(body, list) and body:
                body = body[0]
            hevy_id = body.get("id") if isinstance(body, dict) else None
        repository.mark_proposal(proposal_id, "written", hevy_id=hevy_id)

    return {"id": proposal_id, "status": "written", "hevy_id": hevy_id}


@app.get("/api/writeback")
def list_writebacks(limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
    """Audit trail. Hevy cannot delete, so this is the record of what we caused."""
    with repo() as repository:
        return repository.list_proposals(limit)


class ChatRequest(BaseModel):
    question: str
    section: str = "run"


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, str]:
    """Natural-language questions over the cached data.

    The model receives computed state through tools and is told not to do
    arithmetic, so a wrong number cannot originate here.
    """
    from .chat import answer

    with repo() as repository:
        try:
            reply = await answer(repository, _config, request.question)
        except RuntimeError as exc:
            # Covers llm.ProviderError (nothing configured, or configured wrong).
            # The dock shows this text, so it has to say what to do about it.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"reply": reply}


@app.get("/health")
def healthcheck() -> JSONResponse:
    with repo() as repository:
        return JSONResponse({"status": "ok", "workouts_cached": repository.count_workouts()})


# --- built frontend ---------------------------------------------------------
# Vite emits dist/index.html plus dist/assets/*. Mounting the assets directory
# lets the default base path work, so the same build serves from the dev proxy
# and from here unchanged.

DIST = WEB_DIR / "dist"

if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
def index() -> FileResponse:
    built = DIST / "index.html"
    if built.exists():
        return FileResponse(built)
    raise HTTPException(
        status_code=503,
        detail="Frontend is not built. Run `npm run build` in frontend/.",
    )
