"""What the coach is allowed to know.

Every tool wraps the existing query layer. None of them computes anything: the
agent decides *what to do*, the rules engine says *where things stand*. This is
the same rule `chat.py` follows, and it matters more here, because planning
feels like judgment and the temptation to let the model "just work out" a
deficit is correspondingly stronger.

**There is deliberately no tool that returns individual sets.** Given raw sets
the agent would eventually total them, and the guarantee that no number in the
output was invented would die quietly, with nothing failing to announce it.

Returns are trimmed. The agent needs enough to choose between exercises, not
every field the dashboard renders -- and on a free tier the context window is
a budget, not a formality.

Docstrings here are the tool descriptions the model reads. They are written for
it, not for us.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from .. import queries, sections
from ..config import Config
from ..db import SQLiteRepository

# Enough to choose an exercise without shipping the whole 461-template catalog.
MAX_POOL = 60


def build_tools(repo: SQLiteRepository, config: Config) -> list[Callable[..., Any]]:
    """Bind the query layer into callables ADK can build a schema from.

    Closures rather than methods: ADK generates each tool's schema from the
    signature, so the repo and config must not appear in it.
    """

    def get_volume_vs_target(window: str = "this-week") -> dict[str, Any]:
        """Effective sets per muscle group against the weekly target.

        This is the deficit the plan works from. Use it before deciding what a
        session should contain.

        Args:
            window: this-week, last-week, last-4-weeks, or YYYY-MM-DD:YYYY-MM-DD.
                Note last-N-weeks means N *complete* weeks, excluding the
                part-finished current one.
        """
        report = queries.volume_report(repo, config, window)
        return {
            "window": report["window"],
            "workouts": report["workouts"],
            "working_sets": report["working_sets"],
            "muscles": [
                {
                    "muscle_group": row["muscle_group"],
                    "effective_sets": row["effective_sets"],
                    "target_sets": row["target_sets"],
                    "sets_deficit": row["sets_deficit"],
                    "frequency": row["frequency"],
                    "target_frequency": row["target_frequency"],
                }
                for row in report["coverage"]
            ],
        }

    def get_neglected(window: str = "last-week", limit: int = 5) -> list[dict[str, Any]]:
        """Muscle groups furthest below target, worst first.

        A shortcut for the common case; the same numbers as
        get_volume_vs_target, already filtered and sorted.

        Args:
            window: as get_volume_vs_target.
            limit: how many to return.
        """
        return queries.neglected(repo, config, window, limit)

    def get_progression_state() -> list[dict[str, Any]]:
        """Per lift: the working weight, and whether it is ready to go up.

        Use this to choose the load for an exercise. Never estimate a weight --
        if a lift is not listed here, it has no recent history and the weight
        should be left for the user to fill in.
        """
        return queries.progression_report(repo, config)

    def get_exercise_pool(muscle_group: str = "", logged_only: bool = True) -> list[dict[str, Any]]:
        """Exercises available to plan with, and the muscles each trains.

        Only propose exercises that appear here. Anything else does not exist
        in the user's Hevy catalog and cannot be written back.

        Args:
            muscle_group: optional filter, e.g. chest. Matches primary or
                secondary. Empty returns everything.
            logged_only: True (default) restricts to exercises the user
                actually trains, which is almost always what you want.
        """
        pool = sections.exercise_catalog(repo, only_logged=logged_only)
        if muscle_group:
            wanted = muscle_group.strip().lower()
            pool = [
                row
                for row in pool
                if row["primary_muscle_group"] == wanted
                or wanted in (row["secondary_muscle_groups"] or ())
            ]
        return [
            {
                # Named as the proposal schema names it, not as the catalog
                # does. Shown "id", the model copies "id" into a field called
                # exercise_template_id and the whole proposal fails validation.
                "exercise_template_id": row["id"],
                "title": row["title"],
                "primary_muscle_group": row["primary_muscle_group"],
                "secondary_muscle_groups": list(row["secondary_muscle_groups"] or ()),
                "equipment": row.get("equipment_category"),
                "logged_sets": row.get("logged_sets", 0),
            }
            for row in pool[:MAX_POOL]
        ]

    def get_recent_runs(window: str = "last-4-weeks") -> dict[str, Any]:
        """Runs in a window: distance, pace, heart rate, and AEI where reliable.

        AEI is grade-adjusted metres per heart beat, and is only comparable
        between runs of similar length. Some runs are excluded for unreliable
        GPS; say so rather than ignoring them.

        Args:
            window: as get_volume_vs_target.
        """
        return queries.run_log(repo, config, window)

    def get_recovery_signals(window: str = "last-2-weeks") -> dict[str, Any]:
        """Sleep, resting heart rate and steps per day.

        Report these as the user's own history. Do not turn them into an
        instruction: this app does not tell anyone to rest, skip a session, or
        train differently on health grounds.

        Args:
            window: as get_volume_vs_target.
        """
        return queries.health_summary(repo, config, window)

    def get_insights() -> list[dict[str, Any]]:
        """What the detection rules currently flag -- drops, gaps, stalls."""
        return queries.insight_report(repo, config)

    def get_goals() -> dict[str, Any]:
        """Active goals, and the weekly running target.

        Goals say where the user is going; targets say what maintaining looks
        like. A null running_target means running has no target, so it cannot
        be protected when the week is squeezed -- say so rather than assuming
        a number.
        """
        target = repo.get_running_target()
        return {
            "goals": [goal.as_dict() for goal in repo.get_goals()],
            "running_target": target.as_dict() if target else None,
        }

    def get_availability(week_start: str = "") -> dict[str, Any]:
        """Days in a week the user cannot train.

        Only exceptions are recorded: any day not listed is available. A
        'declared' entry is a fact the user stated; an 'inferred' one is this
        app's guess from a missed session, and should be described as such.

        Args:
            week_start: Monday of the week, YYYY-MM-DD. Empty means next week.
        """
        if week_start:
            start = date.fromisoformat(week_start)
        else:
            today = date.today()
            start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        end = start + timedelta(days=7)
        entries = repo.get_availability(start, end)
        return {
            "week_start": start.isoformat(),
            "unavailable": [entries[day].as_dict() for day in sorted(entries)],
            "note": "days not listed are available",
        }

    def get_previous_plan() -> dict[str, Any]:
        """Last week's plan, and how much of it was actually trained.

        Use it for continuity -- whether a shortfall is new or has persisted --
        and to avoid repeating a suggestion that was already declined.
        """
        # The most recent plan of any week. When replanning a week that already
        # has one -- after a day is declared lost -- that is the superseded plan
        # for this same week, which is the right thing to reference. Telling
        # "last week's plan" from "this week's earlier attempt" is day 7's job,
        # along with matching it against what was actually trained.
        plan = repo.latest_plan()
        if plan is None:
            return {
                "available": False,
                "reason": "no previous plan has been generated yet",
            }
        from .assembler import plan_adherence

        followed = plan_adherence(repo, plan)
        return {
            "available": True,
            "week_start": plan.week_start.isoformat(),
            "status": plan.status,
            "rationale": plan.rationale,
            "trade_offs": plan.trade_offs,
            # What happened to it, not just what it said. A shortfall that
            # survived a week the user trained in full means something
            # different from one that survived a week they missed.
            "followed": {
                "not_started": followed.not_started,
                "sessions_planned": followed.planned,
                "sessions_completed": followed.completed,
                "missed_days": [day.isoformat() for day in followed.missed],
                "sessions_ahead": len(followed.pending),
            },
            # Sessions trimmed to what continuity needs: which days held what,
            # and how much. The full record stays in the plans table.
            "sessions": [
                {
                    "date": session.local_date.isoformat(),
                    "kind": session.kind,
                    "focus": session.focus,
                    "exercises": [
                        {"title": exercise.title, "sets": exercise.sets}
                        for exercise in session.exercises
                    ],
                    "distance_km": session.distance_km,
                }
                for session in plan.sessions
            ],
        }

    return [
        get_volume_vs_target,
        get_neglected,
        get_progression_state,
        get_exercise_pool,
        get_recent_runs,
        get_recovery_signals,
        get_insights,
        get_goals,
        get_availability,
        get_previous_plan,
    ]


def tool_names(repo: SQLiteRepository, config: Config) -> list[str]:
    """Names only -- used by tests and by the trajectory eval."""
    return [tool.__name__ for tool in build_tools(repo, config)]
