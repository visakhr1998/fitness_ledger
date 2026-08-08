"""Run and Gym section queries.

Reads from the local cache and hands the frontend numbers that are already
computed. Kept separate from queries.py, which serves the v0.2 CLI, so neither
grows into a grab bag -- but both sit on the same rules engine, so the CLI and
the dashboard cannot disagree.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean
from typing import Any

from . import icons, vitals as vitals_module
from .config import Config
from .db import SQLiteRepository
from .models import WORKING_SET_TYPES
from .progression import RepRange, progression_state, stalled
from .queries import describe_window, get_targets, parse_window, rep_ranges
from .volume import best_set_per_session, compute_volume, coverage

MAJOR_MUSCLES = [
    "chest", "lats", "upper_back", "shoulders", "biceps", "triceps",
    "quadriceps", "hamstrings", "glutes", "calves", "abdominals",
]


def resolve_window(
    config: Config, window: str | None, start: str | None, end: str | None
) -> tuple[date, date]:
    """A preset name, or an explicit from/to pair from the custom range picker."""
    if start and end:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        return first, last + timedelta(days=1)
    return parse_window(window or "last-90-days", week_starts_on=config.week_starts_on)


def bucket_size(start: date, end: date) -> str:
    """Day, week or month buckets, chosen so a chart never shows 300 bars."""
    days = (end - start).days
    if days <= 31:
        return "day"
    if days <= 180:
        return "week"
    return "month"


def bucket_key(day: date, size: str) -> str:
    if size == "day":
        return day.isoformat()
    if size == "week":
        return (day - timedelta(days=day.weekday())).isoformat()
    return day.replace(day=1).isoformat()


def _bucket(rows: list[tuple[date, float]], size: str) -> list[dict[str, Any]]:
    """Sum values into buckets, keeping empty buckets out rather than faking zeros."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for day, value in rows:
        key = bucket_key(day, size)
        totals[key] = totals.get(key, 0.0) + value
        counts[key] = counts.get(key, 0) + 1
    return [
        {"bucket": key, "total": round(totals[key], 2), "count": counts[key]}
        for key in sorted(totals)
    ]


# --- Run -------------------------------------------------------------------


def run_section(
    repo: SQLiteRepository,
    config: Config,
    window: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Everything the Run screen shows."""
    first, last = resolve_window(config, window, start, end)
    size = bucket_size(first, last)

    metrics = repo.get_run_metrics(first, last)
    reliable = [row for row in metrics if row["reliable"]]
    excluded = [
        {
            "date": row["local_date"],
            "reason": row["unreliable_reason"],
            "tracked_m": round(row["actual_distance_m"] or 0),
        }
        for row in metrics
        if not row["reliable"]
    ]

    aei_points = [
        {
            "date": row["local_date"],
            "aei": round(row["aei"], 4),
            "actual_distance_km": round((row["actual_distance_m"] or 0) / 1000.0, 2),
            "adjusted_distance_km": round((row["adjusted_distance_m"] or 0) / 1000.0, 2),
            "grade_ratio": (
                round(row["adjusted_distance_m"] / row["actual_distance_m"], 3)
                if row["actual_distance_m"]
                else None
            ),
            "avg_heart_rate": round(row["avg_heart_rate"]) if row["avg_heart_rate"] else None,
            "total_beats": round(row["total_beats"]) if row["total_beats"] else None,
        }
        for row in reliable
        if row["aei"] is not None
    ]

    runs = [run for run in repo.get_runs(first, last) if run.exercise_type == "RUNNING"]
    hr_values = [run.avg_heart_rate for run in runs if run.avg_heart_rate]

    latest = aei_points[-1]["aei"] if aei_points else None
    previous = _previous_period_aei(repo, config, first, last)

    return {
        "window": describe_window(first, last),
        "start": first.isoformat(),
        "end": last.isoformat(),
        "bucket": size,
        "aei": {
            "latest": latest,
            "previous_period_mean": previous,
            "delta": (round(latest - previous, 4) if latest and previous else None),
            "mean": round(fmean(p["aei"] for p in aei_points), 4) if aei_points else None,
            "points": aei_points,
            "excluded": excluded,
        },
        "runs": {
            "count": len(runs),
            "total_km": round(sum((run.distance_m or 0) / 1000.0 for run in runs), 2),
            "avg_heart_rate": round(fmean(hr_values)) if hr_values else None,
            # One row per run, oldest first, nothing filtered out. Every chart on
            # the Run screen reads this: bucketing by day made each bar a date
            # total, which is not what a reader of a run chart expects, and the
            # heart-rate series used to drop runs that had no heart rate rather
            # than showing the gap.
            "list": [
                {
                    "date": run.local_date.isoformat(),
                    "distance_km": round((run.distance_m or 0) / 1000.0, 2),
                    "duration_s": run.active_duration_s,
                    "avg_heart_rate": (
                        round(run.avg_heart_rate) if run.avg_heart_rate else None
                    ),
                }
                for run in runs
            ],
        },
    }


def _previous_period_aei(
    repo: SQLiteRepository, config: Config, start: date, end: date
) -> float | None:
    """Mean AEI over the equally-sized period immediately before this one."""
    span = end - start
    rows = [
        row
        for row in repo.get_run_metrics(start - span, start)
        if row["reliable"] and row["aei"] is not None
    ]
    return round(fmean(row["aei"] for row in rows), 4) if rows else None


def vitals_card(repo: SQLiteRepository, config: Config) -> dict[str, Any]:
    """Age, body metrics, VO2 max and heart-rate zones."""
    settings = repo.get_settings()
    today = date.today()
    lookback = today - timedelta(days=365)

    def latest(metric: str) -> float | None:
        rows = repo.get_health_daily(metric, lookback, today + timedelta(days=1))
        return rows[-1][1] if rows else None

    weight = _as_float(settings.get("weight_kg")) or latest("weight_kg")
    resting = latest("resting_heart_rate")
    level_rows = repo.conn.execute(
        """SELECT unit FROM health_daily WHERE metric = 'cardio_fitness_level'
            ORDER BY local_date DESC LIMIT 1"""
    ).fetchone()

    built = vitals_module.build(
        # Age is synced from the Google Health profile into settings, where the
        # user can also override it.
        age=_as_int(settings.get("age")),
        height_cm=_as_float(settings.get("height_cm")) or latest("height_cm"),
        weight_kg=weight,
        resting_hr=resting,
        vo2_max=latest("vo2_max"),
        cardio_fitness_level=level_rows["unit"] if level_rows else None,
        sex=settings.get("sex"),
        max_hr_override=_as_float(settings.get("max_heart_rate")),
        observed_max_hr=repo.max_observed_heart_rate(),
    )
    payload = built.as_dict()
    # What the card is missing, so the UI can point at the settings form instead
    # of showing a blank figure.
    payload["needs"] = [
        key for key, present in (("sex", built.sex), ("age", built.age)) if not present
    ]
    return payload


# --- Gym -------------------------------------------------------------------


def gym_section(
    repo: SQLiteRepository,
    config: Config,
    window: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Muscle distribution and tonnage for the Gym screen."""
    first, last = resolve_window(config, window, start, end)
    size = bucket_size(first, last)

    sets = repo.get_sets(first, last)
    templates = repo.get_templates()
    rollup = compute_volume(
        sets, templates, first, last,
        secondary_weight=config.secondary_weight,
        count_warmup_sets=config.count_warmup_sets,
    )
    rows = coverage(rollup, get_targets(repo))
    by_muscle = {row["muscle_group"]: row for row in rows}

    peak = max((by_muscle.get(m, {}).get("effective_sets", 0) for m in MAJOR_MUSCLES), default=0)
    radar = [
        {
            "muscle_group": muscle,
            "effective_sets": by_muscle.get(muscle, {}).get("effective_sets", 0.0),
            "target_sets": by_muscle.get(muscle, {}).get("target_sets", 0.0),
            # Normalised for the radar's own geometry; the table twin shows real sets.
            "share": round(
                (by_muscle.get(muscle, {}).get("effective_sets", 0.0) / peak) if peak else 0.0, 3
            ),
        }
        for muscle in MAJOR_MUSCLES
    ]

    tonnage_rows: list[tuple[date, float]] = []
    for entry in sets:
        if entry.set_type in WORKING_SET_TYPES:
            tonnage_rows.append((entry.local_date, entry.tonnage_kg))
    buckets = _bucket(tonnage_rows, size)
    mean_tonnage = round(fmean([b["total"] for b in buckets]), 1) if buckets else 0.0

    return {
        "window": describe_window(first, last),
        "start": first.isoformat(),
        "end": last.isoformat(),
        "bucket": size,
        "radar": radar,
        "coverage": rows,
        "tonnage": {
            "buckets": [{**b, "above_average": b["total"] >= mean_tonnage} for b in buckets],
            "mean": mean_tonnage,
            "total": round(sum(b["total"] for b in buckets), 1),
        },
        "workouts": rollup.workouts,
        "working_sets": rollup.working_sets,
    }


def exercise_catalog(repo: SQLiteRepository, only_logged: bool = False) -> list[dict[str, Any]]:
    """Every exercise, with its icon and whether the user actually trains it."""
    templates = repo.get_templates()
    logged: dict[str, int] = {}
    for row in repo.conn.execute(
        """SELECT exercise_template_id AS id, COUNT(*) AS n
             FROM workout_sets WHERE exercise_template_id IS NOT NULL
            GROUP BY exercise_template_id"""
    ):
        logged[row["id"]] = row["n"]

    out = []
    for template in templates.values():
        if only_logged and template.id not in logged:
            continue
        out.append(
            {
                "id": template.id,
                "title": template.title,
                "primary_muscle_group": template.primary_muscle_group,
                "secondary_muscle_groups": list(template.secondary_muscle_groups),
                "equipment": template.equipment_category,
                "icon": icons.icon_for(template.title, template.primary_muscle_group),
                "logged_sets": logged.get(template.id, 0),
            }
        )
    # Exercises the user actually does come first; the rest alphabetically.
    out.sort(key=lambda row: (-row["logged_sets"], row["title"]))
    return out


def exercise_detail(
    repo: SQLiteRepository,
    config: Config,
    template_id: str,
    window: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """1RM trend, progression state and volume for a single exercise."""
    first, last = resolve_window(config, window, start, end)
    templates = repo.get_templates()
    template = templates.get(template_id)
    if template is None:
        return {"error": f"unknown exercise template {template_id}"}

    sets = repo.get_sets(first, last)
    sessions = best_set_per_session(sets, template_id)
    overrides = rep_ranges(repo, config)
    default = RepRange(config.rep_range_low, config.rep_range_high)

    state = progression_state(
        sets, template_id, template.title,
        rep_range=overrides.get(template_id, default),
        equipment_category=template.equipment_category,
    )

    size = bucket_size(first, last)
    volume_rows = [
        (entry.local_date, 1.0)
        for entry in sets
        if entry.exercise_template_id == template_id and entry.set_type in WORKING_SET_TYPES
    ]
    tonnage_rows = [
        (entry.local_date, entry.tonnage_kg)
        for entry in sets
        if entry.exercise_template_id == template_id and entry.set_type in WORKING_SET_TYPES
    ]

    first_e1rm = sessions[0]["e1rm"] if sessions else None
    last_e1rm = sessions[-1]["e1rm"] if sessions else None

    return {
        "exercise": {
            "id": template.id,
            "title": template.title,
            "icon": icons.icon_for(template.title, template.primary_muscle_group),
            "primary_muscle_group": template.primary_muscle_group,
            "secondary_muscle_groups": list(template.secondary_muscle_groups),
            "equipment": template.equipment_category,
        },
        "window": describe_window(first, last),
        "bucket": size,
        "one_rep_max": {
            "points": [
                {
                    "date": entry["date"].isoformat(),
                    "estimated_1rm_kg": entry["e1rm"],
                    "weight_kg": entry["weight_kg"],
                    "reps": entry["reps"],
                }
                for entry in sessions
            ],
            "latest": last_e1rm,
            "change_kg": (
                round(last_e1rm - first_e1rm, 1) if first_e1rm and last_e1rm else None
            ),
        },
        "progression": {**state.as_dict(), "stalled": stalled(sets, template_id)},
        "volume": {
            "sets_per_bucket": _bucket(volume_rows, size),
            "tonnage_per_bucket": _bucket(tonnage_rows, size),
            "total_sets": int(sum(value for _, value in volume_rows)),
            "total_tonnage_kg": round(sum(value for _, value in tonnage_rows), 1),
        },
        "sessions": len({day for day, _ in volume_rows}),
    }


def _as_float(raw: object) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_int(raw: object) -> int | None:
    value = _as_float(raw)
    return int(value) if value is not None else None
