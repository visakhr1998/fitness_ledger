"""FastAPI backend.

Every endpoint is a thin wrapper over queries.py -- no computation happens here,
so the API and the CLI can never disagree about a number. Read-only apart from
settings, an explicitly triggered sync, and approval-gated Hevy write-back.
"""

from __future__ import annotations

import json
from datetime import date
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


@app.get("/api/plan")
def get_plan(week: str | None = Query(None)) -> dict[str, Any]:
    """The stored plan for a week, or the most recent one.

    Deliberately takes no window argument. A plan is one specific week, so the
    dashboard's time-horizon filter has nothing to say about it -- the Week tab
    sits outside the filter for the same reason the coach strip does.

    Read-only for now. Generating and approving land with day 10, which already
    owns the write-back wiring.
    """
    try:
        parsed = date.fromisoformat(week) if week else None
    except ValueError as exc:
        raise HTTPException(400, f"Unrecognised week {week!r}; expected YYYY-MM-DD") from exc

    with repo() as repository:
        return sections.plan_section(repository, _config, parsed.isoformat() if parsed else None)


@app.get("/api/insights")
def get_insights(section: str | None = Query(None)) -> dict[str, Any]:
    """Findings for one screen, plus the scope they were found over.

    The scope travels with them because the strip obeys the section tabs but
    deliberately not the time-horizon filter, and reporting the window beats
    letting the UI name a period of its own that would drift from
    `insight_window` the first time either changed.
    """
    if section is not None and section not in {"run", "gym"}:
        raise HTTPException(400, f"Unknown section {section!r}; expected 'run' or 'gym'")

    start, end = queries.insight_window()
    with repo() as repository:
        return {
            "section": section,
            "window": queries.describe_window(start, end),
            "weeks": queries.INSIGHT_LOOKBACK_WEEKS,
            "insights": queries.insight_report(repository, _config, section),
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


# --- the plan write path ----------------------------------------------------
# Generating is ~3 model requests and tens of seconds, so it follows /api/sync's
# background-task-plus-status-poll rather than inventing a second mechanism.
#
# Approving a *plan* changes nothing outside this app. Writing a session to Hevy
# is a separate, explicit step through the existing propose -> diff -> confirm
# flow, because Hevy has no delete endpoint and the diff is what makes the write
# deliberate. Two entry points to one flow is how `ledger sync` came to run
# three of five steps (#16), so the plan reuses that surface rather than growing
# a second one.

_plan_state: dict[str, Any] = {"status": "idle", "week": None, "plan_id": None, "error": None}


async def _run_plan(week: str | None) -> None:
    from .coach import CoachUnavailable

    _plan_state.update({"status": "running", "week": week, "plan_id": None, "error": None})
    try:
        from .coach.agent import propose_week
        from .coach.assembler import assemble

        with repo() as repository:
            result = await propose_week(
                repository, _config, date.fromisoformat(week) if week else None
            )
            if not result.get("proposal"):
                raise RuntimeError("the coach returned no proposal")
            assembled = assemble(repository, _config, result, persist=True)

        plan = assembled["plan"]
        _plan_state.update(
            {
                "status": "done",
                "week": plan.week_start.isoformat(),
                "plan_id": plan.id,
                "problems": assembled["problems"],
                "planned_by": result.get("planned_by"),
                "fell_back": bool(result.get("fell_back_from")),
            }
        )
    except CoachUnavailable as exc:
        # A missing extra is a setup problem, not a failure of the coach, and
        # the callout should be able to say which.
        _plan_state.update({"status": "unavailable", "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - surface the failure to the callout
        _plan_state.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


@app.post("/api/plan")
async def start_plan(background: BackgroundTasks, week: str | None = Query(None)) -> dict[str, Any]:
    """Generate a week in the background. Nothing reaches Hevy from here."""
    if week is not None:
        try:
            date.fromisoformat(week)
        except ValueError as exc:
            raise HTTPException(400, f"Unrecognised week {week!r}; expected YYYY-MM-DD") from exc

    if _plan_state["status"] == "running":
        return {"status": "running", "detail": "a plan is already being generated"}

    background.add_task(_run_plan, week)
    return {"status": "started"}


@app.get("/api/plan/status")
def plan_status() -> dict[str, Any]:
    return _plan_state


class PlanDecision(BaseModel):
    status: str = Field(description="approved or rejected")


@app.put("/api/plan/{plan_id}")
def decide_plan(plan_id: int, decision: PlanDecision) -> dict[str, Any]:
    """Accept or reject a proposed week. A ledger state change, no Hevy call.

    Only these two transitions are offered: `superseded` is what storage does
    when a revision replaces a plan, not something a user declares.
    """
    if decision.status not in {"approved", "rejected"}:
        raise HTTPException(400, "status must be approved or rejected")

    with repo() as repository:
        plan = repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(404, f"no plan {plan_id}")
        if plan.status != "proposed":
            raise HTTPException(409, f"plan {plan_id} is already {plan.status}")
        repository.set_plan_status(plan_id, decision.status)

    return {"id": plan_id, "status": decision.status}


class SessionRoutine(BaseModel):
    session_date: str = Field(description="which planned day to write, YYYY-MM-DD")
    title: str = Field(default="", max_length=120)


@app.post("/api/plan/{plan_id}/routine")
def propose_plan_routine(plan_id: int, request: SessionRoutine) -> dict[str, Any]:
    """Draft a Hevy routine from one planned lifting session. No Hevy call here.

    The set counts are the allocator's, carried through rather than flattened to
    a default -- they are the whole reason `planning.py` exists, and a routine
    that quietly wrote three of everything would discard the allocation the week
    was built on.
    """
    try:
        day = date.fromisoformat(request.session_date)
    except ValueError as exc:
        raise HTTPException(400, f"Unrecognised date {request.session_date!r}") from exc

    with repo() as repository:
        plan = repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(404, f"no plan {plan_id}")

        sessions = [s for s in plan.sessions if s.kind == "lift" and s.local_date == day]
        if not sessions:
            raise HTTPException(404, f"plan {plan_id} has no lifting session on {day}")

        session = sessions[0]
        counts = {e.exercise_template_id: e.sets for e in session.exercises}
        title = request.title.strip() or f"{session.focus or 'Session'} - {day}"

        proposal = writeback.build_routine(
            repository,
            _config,
            title,
            [e.exercise_template_id for e in session.exercises],
            sets_by_exercise=counts,
        )
        if not proposal.exercises:
            raise HTTPException(400, "none of that session's exercises exist in Hevy")

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
        "plan_id": plan_id,
        "session_date": day.isoformat(),
    }


# --- goals, targets and availability ----------------------------------------
# Without these the running target cannot be set from the UI, and declaring a
# day lost -- the thing meant to trigger a replan -- stays a terminal command.


@app.get("/api/goals")
def get_goals(include_inactive: bool = Query(False)) -> dict[str, Any]:
    with repo() as repository:
        target = repository.get_running_target()
        return {
            "goals": [
                {
                    "id": goal.id,
                    "type": goal.type,
                    "subject": goal.subject,
                    "target_value": goal.target_value,
                    "target_date": goal.target_date.isoformat() if goal.target_date else None,
                    "status": goal.status,
                }
                for goal in repository.get_goals(include_inactive=include_inactive)
            ],
            "running_target": target.as_dict() if target else None,
            # Constraints ship with goals rather than earning a round trip:
            # the home screen shows them together and `dashboard` set the
            # precedent of one request per screen.
            "constraints": [c.as_dict() for c in repository.get_constraints()],
        }


class NewGoal(BaseModel):
    type: str
    target_value: float
    subject: str | None = None
    target_date: str | None = None


@app.post("/api/goals")
def add_goal(request: NewGoal) -> dict[str, Any]:
    from .models import Goal

    try:
        goal = Goal(
            type=request.type,
            target_value=request.target_value,
            subject=request.subject,
            target_date=date.fromisoformat(request.target_date) if request.target_date else None,
        )
    except ValueError as exc:
        # Model validation, so every entry point rejects exactly the same things.
        raise HTTPException(400, str(exc)) from exc

    with repo() as repository:
        stored = repository.add_goal(goal)
    return {"id": stored.id, "status": stored.status}


class GoalDecision(BaseModel):
    status: str = Field(description="achieved or abandoned")


@app.put("/api/goals/{goal_id}")
def close_goal(goal_id: int, decision: GoalDecision) -> dict[str, Any]:
    if decision.status not in {"achieved", "abandoned"}:
        raise HTTPException(400, "status must be achieved or abandoned")
    with repo() as repository:
        if not repository.set_goal_status(goal_id, decision.status):
            raise HTTPException(404, f"no goal {goal_id}")
    return {"id": goal_id, "status": decision.status}


class NewConstraint(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0 Monday to 6 Sunday")
    kind: str
    reason: str | None = None


@app.post("/api/constraints")
def add_constraint(request: NewConstraint) -> dict[str, Any]:
    """A standing weekday restriction. Distinct from availability, which
    records a specific date that was lost."""
    from .models import RecurringConstraint

    try:
        constraint = RecurringConstraint(
            weekday=request.weekday, kind=request.kind, reason=request.reason
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    with repo() as repository:
        stored = repository.add_constraint(constraint)
    return stored.as_dict()


@app.delete("/api/constraints/{constraint_id}")
def delete_constraint(constraint_id: int) -> dict[str, Any]:
    with repo() as repository:
        if not repository.delete_constraint(constraint_id):
            raise HTTPException(404, f"no constraint {constraint_id}")
    return {"id": constraint_id, "deleted": True}


class IntakeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/intake")
async def parse_intake(request: IntakeRequest) -> dict[str, Any]:
    """Turn a description of someone's goals into a *proposal*.

    Writes nothing. The UI confirms what comes back and then posts to
    /api/goals, /api/constraints and /api/running-target, so the model never
    stands between the user and their own record.
    """
    from .intake import parse

    try:
        return await parse(_config, request.text)
    except RuntimeError as exc:
        # llm.ProviderError: nothing configured, or configured wrong. The panel
        # shows this text, so it has to say what to do about it.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class NewRunningTarget(BaseModel):
    distance_km_per_week: float = Field(ge=0)
    sessions_per_week: int = Field(default=2, ge=0, le=14)


@app.put("/api/running-target")
def set_running_target(request: NewRunningTarget) -> dict[str, Any]:
    """Without a running target the priority ranking's third rank has nothing to
    measure, so running cannot be protected when the week is tight."""
    from .models import RunningTarget

    target = RunningTarget(
        distance_km_per_week=request.distance_km_per_week,
        sessions_per_week=request.sessions_per_week,
    )
    with repo() as repository:
        repository.set_running_target(target)
    return target.as_dict()


@app.get("/api/availability")
def get_availability(week: str | None = Query(None)) -> dict[str, Any]:
    """The declared exceptions for a week. A day with no row is available."""
    from datetime import timedelta

    from .coach.context import next_monday

    try:
        monday = date.fromisoformat(week) if week else next_monday()
    except ValueError as exc:
        raise HTTPException(400, f"Unrecognised week {week!r}; expected YYYY-MM-DD") from exc

    with repo() as repository:
        entries = repository.get_availability(monday, monday + timedelta(days=7))

    return {
        "week_start": monday.isoformat(),
        "unavailable": [entries[day].as_dict() for day in sorted(entries)],
    }


class DayOff(BaseModel):
    date: str
    reason: str | None = None


@app.put("/api/availability")
def set_availability(request: DayOff) -> dict[str, Any]:
    """Declare a day lost. This is what triggers a replan."""
    from .models import Availability

    try:
        day = date.fromisoformat(request.date)
    except ValueError as exc:
        raise HTTPException(400, f"Unrecognised date {request.date!r}") from exc

    entry = Availability(local_date=day, reason=request.reason)
    with repo() as repository:
        repository.set_availability(entry)
    return entry.as_dict()


@app.delete("/api/availability/{day}")
def clear_availability(day: str) -> dict[str, Any]:
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(400, f"Unrecognised date {day!r}") from exc

    with repo() as repository:
        cleared = repository.clear_availability(parsed)
    return {"date": parsed.isoformat(), "cleared": cleared}


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
