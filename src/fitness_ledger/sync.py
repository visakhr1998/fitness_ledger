"""Pull Hevy and Google Health data into the local cache.

The dashboard, the analysis passes and the Q&A all read from SQLite; only this
module talks to the MCP servers. First run backfills, later runs go incremental
through Hevy's workout-event feed, which is the only place deletions show up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator

from .db import SQLiteRepository, local_date_of
from .mcp_client import MCPClient, MCPTruncatedError
from .models import ExerciseTemplate

LAST_SYNC_KEY = "hevy_last_sync_at"

# Health metrics worth caching for v0.1 Q&A. Google Health supports daily rollups
# for some types and only per-point listing for others, so both paths exist.
ROLLUP_METRICS = {"steps": "countSum"}
LIST_METRICS = ("sleep", "daily-resting-heart-rate")

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
                equipment_category=item.get("equipment_category"),
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


# --- parsing helpers -------------------------------------------------------
# Google Health returns numbers as strings and durations as "1970s"; converting
# defensively keeps a format surprise from becoming a wrong number.


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
