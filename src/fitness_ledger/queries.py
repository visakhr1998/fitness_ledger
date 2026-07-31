"""Question-shaped reads over the local cache.

These are the functions the CLI calls today and the model layer calls as tools at
the end of v0.1. Keeping them here means the model never recomputes anything --
it receives the result of a deterministic function and puts it into words.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from .config import Config
from .db import SQLiteRepository
from .insights import detect
from .models import VolumeTarget
from .progression import RepRange, main_lifts, progression_state, stalled
from .volume import (
    best_set_per_session,
    compute_volume,
    coverage,
    default_targets,
    unmapped_templates,
    week_start,
    weekly_series,
)


class WindowError(ValueError):
    pass


def parse_window(
    spec: str, today: date | None = None, week_starts_on: int = 0
) -> tuple[date, date]:
    """Resolve a window phrase to a closed-open [start, end) date pair.

    Accepts: this-week, last-week, last-N-weeks, last-N-days, today, yesterday,
    a month as 2026-07, or an explicit 2026-07-01:2026-07-31 range.
    """
    today = today or date.today()
    text = spec.strip().lower().replace("_", "-").replace(" ", "-")
    current = week_start(today, week_starts_on)

    if text in {"this-week", "week"}:
        return current, current + timedelta(days=7)
    if text == "last-week":
        return current - timedelta(days=7), current
    if text == "today":
        return today, today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1), today

    match = re.fullmatch(r"last-(\d+)-weeks?", text)
    if match:
        weeks = int(match.group(1))
        # Complete weeks only, so a trailing average is not diluted by a
        # part-finished current week.
        return current - timedelta(days=7 * weeks), current

    match = re.fullmatch(r"last-(\d+)-days?", text)
    if match:
        days = int(match.group(1))
        return today - timedelta(days=days), today + timedelta(days=1)

    match = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1)
        return start, end

    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})", text)
    if match:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2)) + timedelta(days=1)
        return start, end

    raise WindowError(
        f"Unrecognised window {spec!r}. Try: this-week, last-week, last-4-weeks, "
        "last-30-days, 2026-07, or 2026-07-01:2026-07-31."
    )


def describe_window(start: date, end: date) -> str:
    last_day = end - timedelta(days=1)
    if start == last_day:
        return start.isoformat()
    return f"{start.isoformat()} to {last_day.isoformat()}"


def get_targets(repo: SQLiteRepository) -> dict[str, VolumeTarget]:
    """Stored targets, falling back to the literature defaults."""
    stored = repo.get_targets()
    return stored or default_targets()


def volume_report(
    repo: SQLiteRepository, config: Config, window: str = "last-week"
) -> dict[str, Any]:
    """Effective sets per muscle group for a window, with target comparison."""
    start, end = parse_window(window, week_starts_on=config.week_starts_on)
    sets = repo.get_sets(start, end)
    templates = repo.get_templates()
    rollup = compute_volume(
        sets,
        templates,
        start,
        end,
        secondary_weight=config.secondary_weight,
        count_warmup_sets=config.count_warmup_sets,
    )
    missing = unmapped_templates(sets, templates)
    return {
        "window": describe_window(start, end),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "workouts": rollup.workouts,
        "working_sets": rollup.working_sets,
        "muscles": [
            {
                "muscle_group": muscle.muscle_group,
                "effective_sets": round(muscle.effective_sets, 2),
                "primary_sets": muscle.primary_sets,
                "secondary_sets": muscle.secondary_sets,
                "frequency": muscle.frequency,
                "tonnage_kg": round(muscle.tonnage_kg, 1),
            }
            for muscle in rollup.sorted_muscles()
        ],
        "coverage": coverage(rollup, get_targets(repo)),
        "unmapped_exercises": missing,
    }


def muscle_volume(
    repo: SQLiteRepository, config: Config, muscle_group: str, window: str = "last-week"
) -> dict[str, Any]:
    """Volume for one muscle group -- the 'how much chest last week' question."""
    report = volume_report(repo, config, window)
    muscle = muscle_group.strip().lower().replace(" ", "_")
    detail = next(
        (m for m in report["muscles"] if m["muscle_group"] == muscle),
        {
            "muscle_group": muscle,
            "effective_sets": 0.0,
            "primary_sets": 0,
            "secondary_sets": 0,
            "frequency": 0,
            "tonnage_kg": 0.0,
        },
    )
    # Targets come from the coverage rows so they are scaled to the window.
    scaled = next((c for c in report["coverage"] if c["muscle_group"] == muscle), {})
    return {
        "window": report["window"],
        **detail,
        "sets_per_week": scaled.get("sets_per_week"),
        "target_sets": scaled.get("target_sets"),
        "target_sets_per_week": scaled.get("target_sets_per_week"),
        "target_frequency": scaled.get("target_frequency"),
        "weeks": scaled.get("weeks", 1.0),
    }


def neglected(
    repo: SQLiteRepository, config: Config, window: str = "last-week", limit: int = 5
) -> list[dict[str, Any]]:
    """Muscle groups furthest below target -- 'what have I been neglecting'."""
    report = volume_report(repo, config, window)
    return [row for row in report["coverage"] if row["sets_deficit"] > 0][:limit]


def volume_trend(
    repo: SQLiteRepository,
    config: Config,
    weeks: int = 8,
    muscle_group: str | None = None,
    include_current: bool = False,
) -> dict[str, Any]:
    """Week-by-week effective sets, for the whole body or one muscle group.

    ``include_current`` adds the in-progress week, flagged ``partial``. It is off
    by default because a part-finished week would drag down any trailing average
    computed from this series -- but a dashboard that omits it looks like the
    data stopped days ago.
    """
    current_week = week_start(date.today(), config.week_starts_on)
    start = current_week - timedelta(days=7 * weeks)
    end = current_week + timedelta(days=7) if include_current else current_week
    sets = repo.get_sets(start, end)
    templates = repo.get_templates()
    series = weekly_series(
        sets,
        templates,
        start,
        end,
        secondary_weight=config.secondary_weight,
        count_warmup_sets=config.count_warmup_sets,
        week_starts_on=config.week_starts_on,
    )
    muscle = muscle_group.strip().lower().replace(" ", "_") if muscle_group else None
    rows = []
    for rollup in series:
        partial = rollup.start == current_week
        if muscle:
            entry = rollup.get(muscle)
            rows.append(
                {
                    "week_starting": rollup.start.isoformat(),
                    "effective_sets": round(entry.effective_sets, 2),
                    "frequency": entry.frequency,
                    "workouts": rollup.workouts,
                    "partial": partial,
                }
            )
        else:
            rows.append(
                {
                    "week_starting": rollup.start.isoformat(),
                    "effective_sets": round(
                        sum(m.effective_sets for m in rollup.muscles.values()), 2
                    ),
                    "working_sets": rollup.working_sets,
                    "workouts": rollup.workouts,
                    "partial": partial,
                }
            )
    return {"muscle_group": muscle, "weeks": rows}


def find_exercise(repo: SQLiteRepository, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Fuzzy-match an exercise name to templates the user has actually logged."""
    templates = repo.get_templates()
    logged = {
        row[0]
        for row in repo.conn.execute(
            "SELECT DISTINCT exercise_template_id FROM workout_sets"
        )
        if row[0]
    }
    needle = query.strip().lower()
    scored = []
    for template in templates.values():
        title = template.title.lower()
        if needle in title:
            score = 0.9 + 0.1 * (len(needle) / max(len(title), 1))
        else:
            score = SequenceMatcher(None, needle, title).ratio()
        if template.id in logged:
            score += 0.15  # prefer lifts the user actually does
        if score > 0.45:
            scored.append((score, template))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "id": template.id,
            "title": template.title,
            "primary_muscle_group": template.primary_muscle_group,
            "secondary_muscle_groups": list(template.secondary_muscle_groups),
            "equipment": template.equipment_category,
            "logged": template.id in logged,
        }
        for _, template in scored[:limit]
    ]


def exercise_progress(
    repo: SQLiteRepository, config: Config, exercise: str, weeks: int = 12
) -> dict[str, Any]:
    """Estimated 1RM per session for one lift -- 'am I progressing on bench'."""
    matches = find_exercise(repo, exercise, limit=1)
    if not matches:
        return {"error": f"No exercise matching {exercise!r} found in the catalog."}
    template = matches[0]
    end = date.today() + timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    sets = repo.get_sets(start, end)
    sessions = best_set_per_session(sets, template["id"])
    first, last = (sessions[0], sessions[-1]) if sessions else (None, None)
    return {
        "exercise": template["title"],
        "exercise_template_id": template["id"],
        "window": describe_window(start, end),
        "sessions": [
            {
                "date": entry["date"].isoformat(),
                "weight_kg": entry["weight_kg"],
                "reps": entry["reps"],
                "estimated_1rm_kg": entry["e1rm"],
            }
            for entry in sessions
        ],
        "change_kg": (
            round(last["e1rm"] - first["e1rm"], 1) if first and last and first is not last else 0.0
        ),
    }


def run_log(repo: SQLiteRepository, config: Config, window: str = "last-4-weeks") -> dict[str, Any]:
    """Runs in a window, with pace."""
    start, end = parse_window(window, week_starts_on=config.week_starts_on)
    runs = [run for run in repo.get_runs(start, end) if run.exercise_type == "RUNNING"]
    rows = []
    for run in runs:
        pace = run.pace_seconds_per_km
        rows.append(
            {
                "date": run.local_date.isoformat(),
                "distance_km": round((run.distance_m or 0) / 1000.0, 2),
                "duration_min": round((run.active_duration_s or 0) / 60.0, 1),
                "pace_per_km": _format_pace(pace),
                "avg_heart_rate": run.avg_heart_rate,
            }
        )
    total_km = sum(row["distance_km"] for row in rows)
    return {
        "window": describe_window(start, end),
        "runs": rows,
        "count": len(rows),
        "total_km": round(total_km, 2),
    }


def health_summary(
    repo: SQLiteRepository, config: Config, window: str = "last-2-weeks"
) -> dict[str, Any]:
    """Daily sleep, resting heart rate and steps for a window."""
    start, end = parse_window(window, week_starts_on=config.week_starts_on)
    metrics = {
        "sleep_minutes_asleep": "sleep_minutes_asleep",
        "resting_heart_rate": "resting_heart_rate",
        "steps": "steps",
    }
    days: dict[str, dict[str, Any]] = {}
    for label, metric in metrics.items():
        for day, value in repo.get_health_daily(metric, start, end):
            days.setdefault(day.isoformat(), {"date": day.isoformat()})[label] = value
    return {
        "window": describe_window(start, end),
        "days": [days[key] for key in sorted(days)],
    }


def rep_ranges(repo: SQLiteRepository, config: Config) -> dict[str, RepRange]:
    """Per-exercise overrides on top of the configured default."""
    return {
        template_id: RepRange(low, high)
        for template_id, (low, high) in repo.get_rep_ranges().items()
    }


def progression_report(
    repo: SQLiteRepository, config: Config, weeks: int = 12, limit: int = 8
) -> list[dict[str, Any]]:
    """Double-progression state for the lifts trained most often."""
    end = date.today() + timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    sets = repo.get_sets(start, end)
    templates = repo.get_templates()
    overrides = rep_ranges(repo, config)
    default = RepRange(config.rep_range_low, config.rep_range_high)

    rows = []
    for template_id, title in main_lifts(sets, limit=limit):
        template = templates.get(template_id)
        state = progression_state(
            sets,
            template_id,
            title,
            rep_range=overrides.get(template_id, default),
            equipment_category=template.equipment_category if template else None,
        )
        row = state.as_dict()
        row["stalled"] = stalled(sets, template_id)
        rows.append(row)
    return rows


def insight_report(repo: SQLiteRepository, config: Config) -> list[dict[str, Any]]:
    """Run every detection rule against the cache."""
    today = date.today()
    start = today - timedelta(weeks=12)
    end = today + timedelta(days=1)
    default = RepRange(config.rep_range_low, config.rep_range_high)
    overrides = rep_ranges(repo, config)

    found = detect(
        repo.get_sets(start, end),
        repo.get_templates(),
        get_targets(repo),
        repo.get_sleep_minutes(start, end),
        today,
        secondary_weight=config.secondary_weight,
        count_warmup_sets=config.count_warmup_sets,
        week_starts_on=config.week_starts_on,
        rep_ranges={**{k: default for k in overrides}, **overrides},
    )
    return [insight.as_dict() for insight in found]


def strength_progress(
    repo: SQLiteRepository, config: Config, weeks: int = 16, limit: int = 6
) -> dict[str, Any]:
    """Estimated 1RM series per main lift -- the dashboard's primary view."""
    end = date.today() + timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    sets = repo.get_sets(start, end)

    series = []
    for template_id, title in main_lifts(sets, limit=limit):
        sessions = best_set_per_session(sets, template_id)
        if len(sessions) < 2:
            continue
        first, last = sessions[0], sessions[-1]
        series.append(
            {
                "exercise_template_id": template_id,
                "exercise": title,
                "points": [
                    {
                        "date": entry["date"].isoformat(),
                        "estimated_1rm_kg": entry["e1rm"],
                        "weight_kg": entry["weight_kg"],
                        "reps": entry["reps"],
                    }
                    for entry in sessions
                ],
                "change_kg": round(last["e1rm"] - first["e1rm"], 1),
                "latest_e1rm_kg": last["e1rm"],
            }
        )
    return {"window": describe_window(start, end), "series": series}


def dashboard(repo: SQLiteRepository, config: Config) -> dict[str, Any]:
    """Everything the front page needs, in one round trip."""
    return {
        "generated_at": date.today().isoformat(),
        "strength": strength_progress(repo, config),
        "volume": volume_report(repo, config, "this-week"),
        "last_week": volume_report(repo, config, "last-week"),
        "trend": volume_trend(repo, config, weeks=8, include_current=True),
        "insights": insight_report(repo, config),
        "progression": progression_report(repo, config),
        # Day-based windows, so these run right up to today. Week-based ones
        # would stop at the last complete week and look like stale data.
        "runs": run_log(repo, config, "last-28-days"),
        "health": health_summary(repo, config, "last-14-days"),
    }


def _format_pace(seconds_per_km: float | None) -> str | None:
    if not seconds_per_km:
        return None
    minutes, seconds = divmod(int(round(seconds_per_km)), 60)
    return f"{minutes}:{seconds:02d}/km"
