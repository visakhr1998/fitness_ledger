"""Pull Hevy and Google Health data into the local cache.

The dashboard, the analysis passes and the Q&A all read from SQLite; only this
module talks to the MCP servers. First run backfills, later runs go incremental
through Hevy's workout-event feed, which is the only place deletions show up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable

from . import aei
from .db import SQLiteRepository, local_date_of
from .mcp_client import MCPClient, MCPError, MCPTruncatedError
from .models import ExerciseTemplate
from .tcx import parse_tcx

LAST_SYNC_KEY = "hevy_last_sync_at"

# Health metrics worth caching for v0.1 Q&A. Google Health supports daily
# rollups for some types and only per-point listing for others, so both paths
# exist -- this is the rollup half. The listed types (sleep, resting heart
# rate) name their own `data_type` in `_sync_sleep` and `_sync_resting_hr`;
# there was a `LIST_METRICS` tuple here that named them a second time and that
# nothing read, removed 2026-09-04.
ROLLUP_METRICS = {"steps": "countSum"}

# googlehealth_daily_rollup rejects any range longer than its page size, and the
# page size itself stops working somewhere below 100. These two values are a
# proven-safe pair; a wider window comes back as "Invalid argument", not as data.
ROLLUP_MAX_DAYS = 40
ROLLUP_PAGE_SIZE = 50


def _date_chunks(start: date, end: date, days: int) -> list[tuple[date, date]]:
    """Split [start, end) into closed-open chunks of at most ``days``."""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=days), end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


# --- Hevy ------------------------------------------------------------------


async def sync_exercise_templates(hevy: MCPClient, repo: SQLiteRepository) -> int:
    """Cache the exercise catalog. This is what maps a set to muscle groups."""
    total = 0
    page = 1
    while True:
        payload = await hevy.call(
            "hevy_list_exercise_templates", {"params": {"page": page, "page_size": 100}}
        )
        items = payload.get("items") or []
        if not items:
            break
        repo.upsert_templates(
            ExerciseTemplate(
                id=item["id"],
                title=item.get("title", ""),
                type=item.get("type", ""),
                primary_muscle_group=item.get("primary_muscle_group") or "",
                secondary_muscle_groups=tuple(item.get("secondary_muscle_groups") or []),
                # Hevy calls this `equipment`; we call it `equipment_category`.
                # Reading only our own name meant every row stored NULL, and
                # `progression.increment_for` fell back to 2.5 kg for
                # everything -- suggesting 2.5 kg be added to a Pull Up, where
                # the table says 0.0. Both names are read because the payload
                # has carried each at different times.
                equipment_category=item.get("equipment") or item.get("equipment_category"),
                is_custom=bool(item.get("is_custom")),
            )
            for item in items
        )
        total += len(items)
        if not payload.get("has_more"):
            break
        page = payload.get("next_page") or page + 1
    return total


async def backfill_workouts(
    hevy: MCPClient, repo: SQLiteRepository, max_pages: int | None = None
) -> int:
    """Page through the whole workout history, newest first."""
    total = 0
    page = 1
    while True:
        payload = await hevy.call(
            "hevy_list_workouts", {"params": {"page": page, "page_size": 10}}
        )
        items = payload.get("items") or []
        for workout in items:
            repo.upsert_workout(workout)
        total += len(items)
        if not payload.get("has_more"):
            break
        if max_pages is not None and page >= max_pages:
            break
        page = payload.get("next_page") or page + 1
    return total


async def sync_workout_events(
    hevy: MCPClient, repo: SQLiteRepository, since: str
) -> tuple[int, int]:
    """Apply updates and deletions recorded since ``since``.

    Deleted workouts vanish from the list endpoint without trace, so an
    incremental sync that only reads hevy_list_workouts would keep stale sessions
    forever and overcount volume.
    """
    updated = deleted = 0
    page = 1
    while True:
        payload = await hevy.call(
            "hevy_list_workout_events",
            {"params": {"since": since, "page": page, "page_size": 10}},
        )
        items = payload.get("items") or []
        for event in items:
            if event.get("type") == "deleted":
                event_id = event.get("id")
                if event_id:
                    repo.delete_workout(event_id)
                    deleted += 1
            else:
                workout = event.get("workout")
                if workout:
                    repo.upsert_workout(workout)
                    updated += 1
        if not payload.get("has_more"):
            break
        page = payload.get("next_page") or page + 1
    return updated, deleted


async def sync_hevy(
    hevy: MCPClient, repo: SQLiteRepository, *, full: bool = False
) -> dict[str, Any]:
    """Templates plus workouts. Backfills on first run, incremental afterwards."""
    started_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {}

    result["templates"] = await sync_exercise_templates(hevy, repo)

    last_sync = repo.get_state(LAST_SYNC_KEY)
    if full or not last_sync or repo.count_workouts() == 0:
        result["mode"] = "backfill"
        result["workouts"] = await backfill_workouts(hevy, repo)
    else:
        result["mode"] = "incremental"
        updated, deleted = await sync_workout_events(hevy, repo, last_sync)
        result["updated"] = updated
        result["deleted"] = deleted

    # Rewind slightly: Hevy's event feed is keyed on server-side update time, and
    # a sync that starts exactly where the last one ended can straddle a write.
    watermark = started_at - timedelta(minutes=5)
    repo.set_state(LAST_SYNC_KEY, watermark.isoformat().replace("+00:00", "Z"))
    result["total_workouts"] = repo.count_workouts()
    return result


# --- Google Health ---------------------------------------------------------


async def _pages(
    health: MCPClient,
    tool: str,
    base_args: dict[str, Any],
    page_size: int,
    max_pages: int = 500,
) -> AsyncIterator[dict[str, Any]]:
    """Walk a paginated Google Health endpoint, following nextPageToken.

    Two server behaviours are handled here rather than at every call site:
    oversized responses come back truncated, so the page is retried at half the
    size; and a page can be empty while still carrying a next-page token, so
    only a missing token ends the walk.
    """
    token: str | None = None
    size = page_size

    for _ in range(max_pages):
        args = {**base_args, "page_size": size, "response_format": "json"}
        if token:
            args["page_token"] = token
        try:
            payload = await health.call(tool, args)
        except MCPTruncatedError:
            if size <= 1:
                raise
            size = max(1, size // 2)
            continue

        yield payload
        token = payload.get("nextPageToken")
        if not token:
            return


async def sync_exercise_points(
    health: MCPClient, repo: SQLiteRepository, since: date
) -> int:
    """Cache exercise data points (runs, walks, gym sessions) back to ``since``."""
    runs: list[dict[str, Any]] = []

    async for payload in _pages(
        health, "googlehealth_list_data_points", {"data_type": "exercise"}, page_size=10
    ):
        points = payload.get("dataPoints") or []
        reached_start = False
        for point in points:
            exercise = point.get("exercise") or {}
            interval = exercise.get("interval") or {}
            start_time = interval.get("startTime")
            if not start_time:
                continue
            offset_minutes = _offset_minutes(interval.get("startUtcOffset"))
            local_day = local_date_of(start_time, offset_minutes)
            if local_day < since:
                reached_start = True
                continue

            metrics = exercise.get("metricsSummary") or {}
            runs.append(
                {
                    "id": _point_id(point, start_time),
                    "start_time": start_time,
                    "end_time": interval.get("endTime"),
                    "exercise_type": exercise.get("exerciseType"),
                    "distance_m": _mm_to_m(metrics.get("distanceMillimeters")),
                    "active_duration_s": _seconds(exercise.get("activeDuration")),
                    "calories_kcal": _f(metrics.get("caloriesKcal")),
                    "steps": _i(metrics.get("steps")),
                    "avg_heart_rate": _f(metrics.get("averageHeartRateBeatsPerMinute")),
                    "active_zone_minutes": _i(metrics.get("activeZoneMinutes")),
                    "raw": {"displayName": exercise.get("displayName")},
                }
            )

        if reached_start:
            break

    if runs:
        repo.upsert_runs(runs)
    return len(runs)


async def sync_health_daily(
    health: MCPClient, repo: SQLiteRepository, start: date, end: date
) -> dict[str, int]:
    """Daily metrics: rollup where supported, per-point listing where not."""
    counts: dict[str, int] = {}

    for metric, field in ROLLUP_METRICS.items():
        rows = []
        for chunk_start, chunk_end in _date_chunks(start, end, ROLLUP_MAX_DAYS):
            async for payload in _pages(
                health,
                "googlehealth_daily_rollup",
                {
                    "data_type": metric,
                    "start_date": chunk_start.isoformat(),
                    "end_date": chunk_end.isoformat(),
                },
                page_size=ROLLUP_PAGE_SIZE,
            ):
                for bucket in payload.get("rollupDataPoints") or []:
                    day = _civil_date(bucket.get("civilStartTime"))
                    value = _f((bucket.get(_camel(metric)) or {}).get(field))
                    if day and value is not None:
                        rows.append((day.isoformat(), metric, value, None, ""))
        counts[metric] = repo.upsert_health_daily(rows)

    counts["sleep"] = await _sync_sleep(health, repo, start, end)
    counts["resting_hr"] = await _sync_resting_hr(health, repo, start, end)
    return counts


async def _sync_sleep(
    health: MCPClient, repo: SQLiteRepository, start: date, end: date
) -> int:
    """Store sleep summaries, attributed to the morning the user woke up.

    A date can carry several sleep records -- a nap flagged as main sleep, or the
    same night from two sources. The longest one wins, so a 40-minute fragment
    cannot overwrite a full night.
    """
    best: dict[str, dict[str, float | None]] = {}

    # Sleep points carry a full stage-by-stage breakdown, so only a few fit in
    # one response.
    async for payload in _pages(
        health, "googlehealth_list_data_points", {"data_type": "sleep"}, page_size=4
    ):
        points = payload.get("dataPoints") or []
        reached_start = False
        for point in points:
            sleep = point.get("sleep") or {}
            if not (sleep.get("metadata") or {}).get("mainSleep", True):
                continue
            interval = sleep.get("interval") or {}
            end_time = interval.get("endTime")
            if not end_time:
                continue
            day = local_date_of(end_time, _offset_minutes(interval.get("endUtcOffset")))
            if day < start:
                reached_start = True
                continue
            if day >= end:
                continue

            summary = sleep.get("summary") or {}
            stages = {
                stage.get("type"): _f(stage.get("minutes"))
                for stage in summary.get("stagesSummary") or []
            }
            iso = day.isoformat()
            asleep = _f(summary.get("minutesAsleep")) or 0.0
            if asleep <= (best.get(iso, {}).get("sleep_minutes_asleep") or -1):
                continue
            best[iso] = {
                "sleep_minutes_asleep": asleep,
                "sleep_minutes_awake": _f(summary.get("minutesAwake")),
                "sleep_minutes_deep": stages.get("DEEP"),
                "sleep_minutes_rem": stages.get("REM"),
            }

        if reached_start:
            break

    rows = [
        (iso, metric, value, "min", "")
        for iso, metrics in best.items()
        for metric, value in metrics.items()
    ]
    return repo.upsert_health_daily(rows) if rows else 0


async def _sync_resting_hr(
    health: MCPClient, repo: SQLiteRepository, start: date, end: date
) -> int:
    rows: list[tuple[str, str, float | None, str | None, str]] = []

    async for payload in _pages(
        health,
        "googlehealth_list_data_points",
        {"data_type": "daily-resting-heart-rate"},
        page_size=50,
    ):
        points = payload.get("dataPoints") or []
        reached_start = False
        for point in points:
            entry = point.get("dailyRestingHeartRate") or {}
            day = _civil_date({"date": entry.get("date")})
            if not day:
                continue
            if day < start:
                reached_start = True
                continue
            if day >= end:
                continue
            rows.append(
                (day.isoformat(), "resting_heart_rate", _f(entry.get("beatsPerMinute")), "bpm", "")
            )

        if reached_start:
            break

    return repo.upsert_health_daily(rows) if rows else 0


async def sync_run_metrics(
    health: MCPClient, repo: SQLiteRepository, *, limit: int | None = None
) -> dict[str, int]:
    """Compute AEI for runs that do not yet have it at the current method.

    One TCX export is ~1.2 MB, so this is deliberately incremental: a run is
    fetched once, its 25 m bins are stored, and a later method change recomputes
    from those bins instead of downloading again.
    """
    pending = repo.run_ids_needing_metrics(aei.METHOD_VERSION)
    if limit is not None:
        pending = pending[:limit]

    computed = recomputed = failed = unreliable = 0
    for run_id, local_date, reported_distance in pending:
        # Prefer replaying stored bins over re-fetching the export.
        stored = repo.get_run_segments(run_id)
        if stored:
            segments = [
                aei.Segment(
                    index=row["idx"],
                    distance_m=row["distance_m"],
                    grade=row["grade"],
                    heart_rate=row["heart_rate"],
                    seconds=row["seconds"],
                )
                for row in stored
            ]
            actual = sum(s.distance_m for s in segments)
            metrics = aei.from_segments(segments, date.fromisoformat(local_date), actual)
            ok, reason, coverage = aei.reliability(actual, reported_distance)
            repo.upsert_run_metrics(
                run_id, metrics, segments,
                reported_distance_m=reported_distance,
                reliable=ok, unreliable_reason=reason, track_coverage=coverage,
            )
            recomputed += 1
            unreliable += 0 if ok else 1
            continue

        try:
            payload = await health.call(
                "googlehealth_export_exercise_tcx", {"data_point_id": run_id}
            )
        except MCPError:
            failed += 1
            continue

        points = parse_tcx(_tcx_text(payload))
        if len(points) < 2:
            failed += 1
            continue

        metrics = aei.compute(points, date.fromisoformat(local_date))
        ok, reason, coverage = aei.reliability(metrics.actual_distance_m, reported_distance)
        repo.upsert_run_metrics(
            run_id, metrics, aei.segment_run(points),
            reported_distance_m=reported_distance,
            reliable=ok, unreliable_reason=reason, track_coverage=coverage,
        )
        computed += 1
        unreliable += 0 if ok else 1

    return {
        "computed": computed,
        "recomputed": recomputed,
        "failed": failed,
        "unreliable": unreliable,
    }


async def sync_vitals(health: MCPClient, repo: SQLiteRepository) -> dict[str, Any]:
    """Cache height, weight and VO2 max as daily health rows.

    Weight arrives from more than one source -- Hevy via Health Connect and a
    manual Fitbit entry disagreed by 2.8 kg -- so it goes through reconcile
    rather than whichever page happened to come back first.
    """
    out: dict[str, Any] = {}

    # Age lives on the profile, not in a data stream. Seed it into settings so
    # the vitals card has it; the user can override it there.
    try:
        profile = await health.call("googlehealth_get_profile")
        age = profile.get("age") if isinstance(profile, dict) else None
        if age and not repo.get_settings().get("age"):
            repo.set_setting("age", str(int(age)))
        out["age"] = age
    except MCPError as exc:
        out["age"] = f"error: {exc}"

    for metric, data_type, extract in (
        ("weight_kg", "weight", lambda e: _f(e.get("weightGrams")) and _f(e.get("weightGrams")) / 1000.0),
        ("height_cm", "height", lambda e: _f(e.get("heightMillimeters")) and _f(e.get("heightMillimeters")) / 10.0),
        ("vo2_max", "daily-vo2-max", lambda e: _f(e.get("vo2Max"))),
    ):
        rows: list[tuple[str, str, float | None, str | None, str]] = []
        tool = "googlehealth_reconcile" if data_type == "weight" else "googlehealth_list_data_points"
        try:
            async for payload in _pages(health, tool, {"data_type": data_type}, page_size=25, max_pages=4):
                for point in payload.get("dataPoints") or []:
                    entry = point.get(_camel(data_type)) or {}
                    day = _civil_date({"date": (entry.get("date") or (entry.get("sampleTime") or {}).get("civilTime", {}).get("date"))})
                    value = extract(entry)
                    if day and value is not None:
                        rows.append((day.isoformat(), metric, value, None, ""))
                        if data_type == "daily-vo2-max":
                            level = entry.get("cardioFitnessLevel")
                            if level:
                                rows.append((day.isoformat(), "cardio_fitness_level", None, level, ""))
        except MCPError as exc:
            out[metric] = f"error: {exc}"
            continue
        out[metric] = repo.upsert_health_daily(rows) if rows else 0

    return out


# --- parsing helpers -------------------------------------------------------
# Google Health returns numbers as strings and durations as "1970s"; converting
# defensively keeps a format surprise from becoming a wrong number.


def _tcx_text(payload: Any) -> str:
    """Pull the TCX document out of an export response.

    The server wraps it as {"tcxData": "<xml…>"}; older shapes returned the
    markup directly, and an unparseable payload falls through as {"text": …}.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("tcxData", "tcx", "text", "data"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    parsed = _f(value)
    return int(parsed) if parsed is not None else None


def _seconds(value: Any) -> float | None:
    if isinstance(value, str) and value.endswith("s"):
        return _f(value[:-1])
    return _f(value)


def _mm_to_m(value: Any) -> float | None:
    parsed = _f(value)
    return parsed / 1000.0 if parsed is not None else None


def _offset_minutes(value: Any) -> int:
    seconds = _seconds(value)
    return int(seconds // 60) if seconds is not None else 0


# --- the whole sync, in one place ------------------------------------------


SYNC_STEPS = ("hevy", "exercise points", "daily health", "vitals", "run metrics")


async def sync_all(
    config: Any,
    repo: SQLiteRepository,
    *,
    weeks: int = 12,
    full: bool = False,
    on_step: Callable[[str, Any], None] | None = None,
) -> None:
    """Run every sync step, in SYNC_STEPS order, for every entry point.

    The CLI and the dashboard each used to list the steps themselves, and the
    CLI list was two short: vitals and run metrics ran only when someone pressed
    Sync in the UI. So AEI existed or did not depending on which entry point you
    happened to use, and a METHOD_VERSION bump -- the documented way to change
    the AEI method without re-downloading 1.2 MB per run -- silently did not
    apply from the command line (#16).

    One definition, one order, one place to add the next step. ``on_step``
    receives each step's name and its return value, so a caller can print it or
    push it onto a status callout without knowing what the steps are.
    """
    from .mcp_client import health_client, hevy_client

    report = on_step or (lambda name, detail: None)

    async with hevy_client(config) as hevy:
        report("hevy", await sync_hevy(hevy, repo, full=full))

    since = date.today() - timedelta(weeks=weeks)
    async with health_client(config) as health:
        report("exercise points", await sync_exercise_points(health, repo, since))
        report(
            "daily health",
            await sync_health_daily(health, repo, since, date.today() + timedelta(days=1)),
        )
        report("vitals", await sync_vitals(health, repo))
        # Last: it is the only step that can need a fresh TCX download, and it
        # reads the exercise points the earlier steps just cached.
        report("run metrics", await sync_run_metrics(health, repo))


def _civil_date(civil: dict | None) -> date | None:
    if not civil:
        return None
    parts = civil.get("date") or {}
    try:
        return date(int(parts["year"]), int(parts["month"]), int(parts["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _point_id(point: dict, fallback: str) -> str:
    name = point.get("name") or ""
    return name.rsplit("/", 1)[-1] if "/" in name else fallback


def _camel(kebab: str) -> str:
    head, *rest = kebab.split("-")
    return head + "".join(part.capitalize() for part in rest)
