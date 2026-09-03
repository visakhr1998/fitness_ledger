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
from .models import WORKING_SET_TYPES, Goal, Plan, VolumeTarget
from .planning import Adherence, Preferences, adherence
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

    # Months are approximated as 30-day steps rather than calendar months: the
    # filter means "roughly this much history", and calendar arithmetic would
    # make the window length depend on which month you happen to open the app in.
    match = re.fullmatch(r"last-(\d+)-months?", text)
    if match:
        return today - timedelta(days=30 * int(match.group(1))), today + timedelta(days=1)

    match = re.fullmatch(r"last-(\d+)-hours?", text)
    if match:
        # Buckets are never finer than a day, so an hours window still resolves
        # to whole days -- it just means "today" for anything under 24h.
        hours = int(match.group(1))
        return today - timedelta(days=max(hours // 24, 0)), today + timedelta(days=1)

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


INSIGHT_LOOKBACK_WEEKS = 12


def insight_window(today: date | None = None) -> tuple[date, date]:
    """The span the detection rules read from.

    Deliberately **not** the dashboard's time-horizon filter. Each rule defines
    its own window inside this span -- volume_drop compares complete weeks,
    coverage_gap uses eight, recovery_flag a week against a four-week baseline --
    and squeezing them into a seven-day filter would silence them rather than
    rescope them: a rule evaluated against one part-finished week fires on
    nothing. That is the two window vocabularies rule in CLAUDE.md.

    Exported so the UI can state the scope it is actually showing instead of
    naming a period of its own and drifting from this one.
    """
    today = today or date.today()
    return today - timedelta(weeks=INSIGHT_LOOKBACK_WEEKS), today + timedelta(days=1)


# Preference keys in user_settings. Absent means "no opinion", and the defaults
# in planning.Preferences apply.
PLANNING_SETTING_KEYS = {
    "max_sets_per_session": "max_sets_per_session",
    "min_sets_per_exercise": "min_sets_per_exercise",
    "max_sets_per_exercise": "max_sets_per_exercise",
    "min_rest_days_same_muscle": "min_rest_days_same_muscle",
}
ALLOW_RUN_AFTER_LEGS_KEY = "allow_run_after_leg_day"


def planning_preferences(repo: SQLiteRepository) -> Preferences:
    """The hard constraints a plan must respect, from settings.

    Lives here rather than in `coach/` because the dashboard reads it too, and
    the dashboard must not import the optional coach extra.
    """
    settings = repo.get_settings()
    values: dict[str, Any] = {}
    for field, key in PLANNING_SETTING_KEYS.items():
        raw = settings.get(key)
        if raw not in (None, ""):
            try:
                values[field] = int(float(raw))
            except (TypeError, ValueError):
                # A malformed setting should not take the planner down; the
                # default is a safe week, not a wrong one.
                continue

    raw_run = settings.get(ALLOW_RUN_AFTER_LEGS_KEY)
    if raw_run not in (None, ""):
        values["allow_run_after_leg_day"] = str(raw_run).strip().lower() not in {
            "0", "false", "no", "off"
        }
    return Preferences(**values)


def plan_adherence(repo: SQLiteRepository, plan: Plan | None) -> Adherence:
    """How much of a stored plan was actually trained.

    The gathering half: reads the logged week out of the cache and hands it to
    the pure comparison in planning.py. Everything below the read is arithmetic
    the rules engine owns.
    """
    if plan is None or not plan.sessions:
        return Adherence(week_start=date.min)

    start = min(session.local_date for session in plan.sessions)
    end = max(session.local_date for session in plan.sessions) + timedelta(days=1)

    logged_by_day: dict[date, dict[str, int]] = {}
    for entry in repo.get_sets(start, end):
        if entry.set_type not in WORKING_SET_TYPES or not entry.exercise_template_id:
            continue
        day = logged_by_day.setdefault(entry.local_date, {})
        day[entry.exercise_template_id] = day.get(entry.exercise_template_id, 0) + 1

    run_days = {
        run.local_date
        for run in repo.get_runs(start, end)
        if run.exercise_type in {"RUNNING", "TREADMILL"}
    }
    return adherence(plan.sessions, logged_by_day, run_days, today=date.today())


def insight_report(
    repo: SQLiteRepository, config: Config, section: str | None = None
) -> list[dict[str, Any]]:
    """Run every detection rule against the cache.

    ``section`` is "run" or "gym"; None returns everything, which is what the
    CLI and the coach want.
    """
    today = date.today()
    start, end = insight_window(today)
    default = RepRange(config.rep_range_low, config.rep_range_high)
    overrides = rep_ranges(repo, config)

    # Only reliable runs carry an AEI worth trending; an excluded one already
    # says why on the Run screen and should not also skew a comparison.
    aei_series = [
        (date.fromisoformat(row["local_date"]), row["aei"])
        for row in repo.get_run_metrics(start, end)
        if row["reliable"] and row["aei"] is not None
    ]

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
        runs=repo.get_runs(start, end),
        running_target=repo.get_running_target(),
        aei_series=aei_series,
        section=section,
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


# --- how close is a goal ----------------------------------------------------


def goal_progress(repo: SQLiteRepository, config: Config, goal: Goal) -> dict[str, Any]:
    """Where a goal stands, computed here and never by a model.

    The chat dock explains this number; it does not derive it. That is the same
    division `chat.py` already enforces -- the model picks a tool and phrases
    what comes back -- and it is why the figure can also be shown on the goal
    card without a model request at all.

    Every branch returns the same shape so the caller never has to know which
    kind of goal it is holding. `current` is None when the goal cannot be
    measured yet rather than 0, because "no data" and "no progress" are
    different answers and a progress bar would show them identically.
    """
    measured = {
        "goal_id": goal.id,
        "type": goal.type,
        "subject": goal.subject,
        "target": goal.target_value,
        "current": None,
        "fraction": None,
        "unit": "",
        "window": "",
        "detail": "",
        "measurable": True,
    }

    if goal.type == "strength_1rm" and goal.subject:
        report = exercise_progress(repo, config, goal.subject, weeks=12)
        if report.get("error"):
            return {**measured, "measurable": False, "detail": report["error"]}
        sessions = report.get("sessions") or []
        current = sessions[-1]["estimated_1rm_kg"] if sessions else None
        return {
            **measured,
            "subject": report["exercise"],
            "current": current,
            "fraction": _fraction(current, goal.target_value),
            "unit": "kg",
            "window": report["window"],
            "detail": (
                f"estimated 1RM from {len(sessions)} sessions, "
                f"{report['change_kg']:+g} kg over the window"
                if sessions
                else "nothing logged for this lift in the last 12 weeks"
            ),
        }

    if goal.type == "running_volume":
        report = run_log(repo, config, "last-week")
        return {
            **measured,
            "current": report["total_km"],
            "fraction": _fraction(report["total_km"], goal.target_value),
            "unit": "km",
            "window": report["window"],
            "detail": f"{report['count']} runs last week",
        }

    if goal.type == "running_aei":
        start, end = parse_window("last-12-weeks", week_starts_on=config.week_starts_on)
        # Reliable runs only, for the reason the insight rules use the same
        # filter: an excluded run already says why on the Run screen and should
        # not also move a goal.
        series = [
            row["aei"]
            for row in repo.get_run_metrics(start, end)
            if row["reliable"] and row["aei"] is not None
        ]
        current = round(series[-1], 3) if series else None
        return {
            **measured,
            "current": current,
            "fraction": _fraction(current, goal.target_value),
            "unit": "m/beat",
            "window": describe_window(start, end),
            "detail": (
                f"latest of {len(series)} reliable runs"
                if series
                else "no reliable runs in the window"
            ),
        }

    if goal.type == "consistency":
        start, end = parse_window("last-4-weeks", week_starts_on=config.week_starts_on)
        workouts = repo.get_workouts(start, end)
        weeks = max(((end - start).days) // 7, 1)
        current = round(len(workouts) / weeks, 1)
        return {
            **measured,
            "current": current,
            "fraction": _fraction(current, goal.target_value),
            "unit": "sessions/week",
            "window": describe_window(start, end),
            "detail": f"{len(workouts)} sessions over {weeks} weeks",
        }

    if goal.type == "race_time":
        # Deliberately not guessed. Turning training data into a predicted race
        # time needs VDOT, which does not exist here yet, and an invented
        # figure on a goal card would be believed.
        return {
            **measured,
            "measurable": False,
            "unit": "s",
            "detail": (
                "Predicting a race time needs a pace model, which this app does "
                "not have yet. Logged runs are on the Run screen."
            ),
        }

    return {**measured, "measurable": False, "detail": "no measure for this goal type"}


def _fraction(current: float | None, target: float) -> float | None:
    """How far along, clamped to [0, 1]. None when there is nothing to compare.

    Higher-is-better throughout, which is true of every measurable goal type
    here. A race time is lower-is-better and is exactly the type reported as
    not measurable, so this does not silently invert one.
    """
    if current is None or not target:
        return None
    return round(max(0.0, min(current / target, 1.0)), 3)
