"""Command line interface for v0.1."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import date, timedelta

from . import llm
from .config import Config
from .db import SQLiteRepository
from .mcp_client import hevy_client, health_client
from .models import Availability, Goal, RunningTarget, VolumeTarget
from .queries import (
    describe_window,
    exercise_progress,
    find_exercise,
    get_targets,
    health_summary,
    muscle_volume,
    neglected,
    parse_window,
    run_log,
    volume_report,
    volume_trend,
)
from .sync import sync_exercise_points, sync_health_daily, sync_hevy
from .volume import default_targets


def open_repo(config: Config) -> SQLiteRepository:
    return SQLiteRepository(config.db_path, config.local_utc_offset_minutes)


# --- commands --------------------------------------------------------------


async def cmd_doctor(config: Config, args: argparse.Namespace) -> int:
    ok = True
    try:
        async with hevy_client(config) as hevy:
            user = await hevy.call("hevy_get_user_info")
            name = (user.get("data") or {}).get("name", "?")
            tools = await hevy.list_tools()
            print(f"  hevy          OK  -- account {name}, {len(tools)} tools")
    except Exception as exc:  # noqa: BLE001 - report, don't crash the check
        ok = False
        print(f"  hevy          FAIL -- {exc}")

    try:
        async with health_client(config) as health:
            profile = await health.call("googlehealth_get_profile")
            tools = await health.list_tools()
            age = profile.get("age", "?")
            print(f"  google-health OK  -- profile age {age}, {len(tools)} tools")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  google-health FAIL -- {exc}")

    with open_repo(config) as repo:
        print(f"  database      {config.db_path} ({repo.count_workouts()} workouts cached)")
    provider = llm.resolve_provider(config)
    if provider == "none":
        print("  model         NO PROVIDER (ask disabled; set GEMINI_API_KEY)")
    else:
        print(f"  model         {provider} / {llm.model_name(config)}")
    return 0 if ok else 1


async def cmd_sync(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        if not repo.get_targets():
            repo.set_targets(default_targets().values())
            print("Seeded default volume targets.")

        print("Syncing Hevy...")
        async with hevy_client(config) as hevy:
            result = await sync_hevy(hevy, repo, full=args.full)
        print(f"  mode: {result['mode']}")
        print(f"  exercise templates: {result['templates']}")
        if result["mode"] == "backfill":
            print(f"  workouts fetched: {result['workouts']}")
        else:
            print(f"  workouts updated: {result['updated']}, deleted: {result['deleted']}")
        print(f"  workouts cached: {result['total_workouts']}")

        since = date.today() - timedelta(weeks=args.weeks)
        print(f"Syncing Google Health since {since.isoformat()}...")
        async with health_client(config) as health:
            points = await sync_exercise_points(health, repo, since)
            daily = await sync_health_daily(health, repo, since, date.today() + timedelta(days=1))
        print(f"  exercise data points: {points}")
        for metric, count in daily.items():
            print(f"  {metric}: {count} rows")
    return 0


def cmd_volume(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        report = volume_report(repo, config, args.window)
        if args.json:
            print(json.dumps(report, indent=2))
            return 0

        print(f"Volume -- {report['window']}")
        print(f"{report['workouts']} workouts, {report['working_sets']} working sets\n")
        print(f"{'muscle group':<16}{'sets':>7}{'target':>8}{'  ':<2}{'freq':>5}  {'bar':<22}")
        print("-" * 62)
        for row in report["coverage"]:
            pct = row["pct_of_target"] or 0
            filled = min(20, round(pct / 5))
            bar = "#" * filled + "." * (20 - filled)
            print(
                f"{row['muscle_group']:<16}{row['effective_sets']:>7.1f}"
                f"{row['target_sets']:>8.0f}  {row['frequency']:>3}/{row['target_frequency']}"
                f"  {bar} {pct:>3}%"
            )
        if report["unmapped_exercises"]:
            print("\nUnmapped exercises (not in local catalog -- run sync):")
            for template_id, title in report["unmapped_exercises"].items():
                print(f"  {template_id}  {title}")
    return 0


def cmd_muscle(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        result = muscle_volume(repo, config, args.muscle, args.window)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        target = result["target_sets"]
        weeks = result.get("weeks") or 1.0
        print(f"{result['muscle_group']} -- {result['window']}")
        print(f"  effective sets : {result['effective_sets']:.1f}" + (f" / {target:.0f} target" if target else ""))
        if weeks > 1:
            print(
                f"  per week       : {result['sets_per_week']:.1f}"
                f" / {result['target_sets_per_week']:.0f} target"
            )
        print(f"  primary sets   : {result['primary_sets']}")
        print(f"  secondary sets : {result['secondary_sets']} (counted at {config.secondary_weight})")
        print(f"  frequency      : {result['frequency']} day(s)")
        print(f"  tonnage        : {result['tonnage_kg']:.0f} kg")
    return 0


def cmd_neglected(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        rows = neglected(repo, config, args.window, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        print(f"Furthest below target -- {describe_window(*parse_window(args.window, week_starts_on=config.week_starts_on))}\n")
        for row in rows:
            print(
                f"  {row['muscle_group']:<16} {row['effective_sets']:>5.1f} / {row['target_sets']:>4.0f} sets"
                f"   short by {row['sets_deficit']:>4.1f}   freq {row['frequency']}/{row['target_frequency']}"
            )
    return 0


def cmd_trend(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        result = volume_trend(repo, config, args.weeks, args.muscle)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        label = result["muscle_group"] or "total"
        print(f"Weekly effective sets -- {label}\n")
        peak = max((row["effective_sets"] for row in result["weeks"]), default=0) or 1
        for row in result["weeks"]:
            bar = "#" * round(30 * row["effective_sets"] / peak)
            print(f"  {row['week_starting']}  {row['effective_sets']:>6.1f}  {bar}")
    return 0


def cmd_progress(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        result = exercise_progress(repo, config, args.exercise, args.weeks)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        if "error" in result:
            print(result["error"])
            return 1
        print(f"{result['exercise']} -- {result['window']}\n")
        for row in result["sessions"]:
            print(
                f"  {row['date']}  {row['weight_kg']:>6.1f} kg x {row['reps']:<3}"
                f"  e1RM {row['estimated_1rm_kg']:>6.1f} kg"
            )
        if result["sessions"]:
            print(f"\n  change over window: {result['change_kg']:+.1f} kg estimated 1RM")
    return 0


def cmd_runs(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        result = run_log(repo, config, args.window)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        print(f"Runs -- {result['window']}  ({result['count']} runs, {result['total_km']} km)\n")
        for row in result["runs"]:
            hr = f"{row['avg_heart_rate']:.0f} bpm" if row["avg_heart_rate"] else "-"
            print(
                f"  {row['date']}  {row['distance_km']:>5.2f} km  {row['duration_min']:>5.1f} min"
                f"  {row['pace_per_km'] or '-':>9}  {hr:>8}"
            )
    return 0


def cmd_health(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        result = health_summary(repo, config, args.window)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        print(f"Daily health -- {result['window']}\n")
        print(f"  {'date':<12}{'sleep':>8}{'rest HR':>9}{'steps':>8}")
        for row in result["days"]:
            sleep = row.get("sleep_minutes_asleep")
            sleep_text = f"{int(sleep)//60}h{int(sleep)%60:02d}" if sleep else "-"
            hr = row.get("resting_heart_rate")
            steps = row.get("steps")
            print(
                f"  {row['date']:<12}{sleep_text:>8}"
                f"{(f'{hr:.0f}' if hr else '-'):>9}{(f'{steps:.0f}' if steps else '-'):>8}"
            )
    return 0


def cmd_targets(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        if args.set:
            updates = []
            for item in args.set:
                muscle, _, value = item.partition("=")
                updates.append(VolumeTarget(muscle.strip(), float(value)))
            repo.set_targets(updates)
            print(f"Updated {len(updates)} target(s).")
        targets = get_targets(repo)
        for muscle in sorted(targets):
            target = targets[muscle]
            print(f"  {muscle:<16} {target.sets_per_week:>5.0f} sets/week   x{target.frequency_per_week}  ({target.size_class})")
    return 0


def cmd_goals(config: Config, args: argparse.Namespace) -> int:
    """Show, add, or close goals, plus the weekly running target.

    Goals are what the coach plans toward; volume and running targets are the
    maintenance levels it measures against. Both live here because setting one
    without the other leaves the coach unable to explain a trade-off.
    """
    with open_repo(config) as repo:
        if args.set_running is not None:
            distance, _, sessions = args.set_running.partition("/")
            repo.set_running_target(
                RunningTarget(
                    distance_km_per_week=float(distance),
                    sessions_per_week=int(sessions) if sessions else 2,
                )
            )
            print("Running target updated.")

        if args.add:
            goal_type, _, value = args.add.partition("=")
            goal = repo.add_goal(
                Goal(
                    type=goal_type.strip(),
                    target_value=float(value),
                    subject=args.subject,
                    target_date=date.fromisoformat(args.by) if args.by else None,
                )
            )
            print(f"Added goal #{goal.id}.")

        if args.done is not None:
            status = "abandoned" if args.abandon else "achieved"
            if repo.set_goal_status(args.done, status):
                print(f"Goal #{args.done} marked {status}.")
            else:
                print(f"No goal #{args.done}.")
                return 1

        goals = repo.get_goals(include_inactive=args.all)
        if not goals:
            print("  no goals set - the coach has nothing to plan toward")
        for goal in goals:
            subject = f" {goal.subject}" if goal.subject else ""
            by = f"  by {goal.target_date}" if goal.target_date else ""
            flag = "" if goal.is_active else f"  [{goal.status}]"
            print(f"  #{goal.id:<3} {goal.type}{subject}  ->  {goal.target_value:g}{by}{flag}")

        target = repo.get_running_target()
        if target:
            print(
                f"  running target   {target.distance_km_per_week:g} km/week "
                f"across {target.sessions_per_week} run(s)"
            )
        else:
            # Priority rank 3 is "runs on track"; with no target there is
            # nothing to hold it to.
            print("  running target   not set - running cannot be protected when the week is tight")
    return 0


def cmd_unavailable(config: Config, args: argparse.Namespace) -> int:
    """Declare a day lost, or hand it back. This is what triggers a replan."""
    with open_repo(config) as repo:
        day = date.fromisoformat(args.date)
        if args.clear:
            if repo.clear_availability(day):
                print(f"{day} is available again.")
            else:
                print(f"{day} was not marked unavailable.")
            return 0

        repo.set_availability(Availability(local_date=day, reason=args.reason))
        reason = f" ({args.reason})" if args.reason else ""
        print(f"{day} marked unavailable{reason}.")

        # Show the rest of that week, so the consequence is visible at once.
        monday = day - timedelta(days=day.weekday())
        others = {
            entry_day: entry
            for entry_day, entry in repo.get_availability(monday, monday + timedelta(days=7)).items()
            if entry_day != day
        }
        if others:
            print("  also unavailable this week:")
            for entry_day in sorted(others):
                note = f" - {others[entry_day].reason}" if others[entry_day].reason else ""
                print(f"    {entry_day} [{others[entry_day].source}]{note}")
    return 0


async def cmd_plan(config: Config, args: argparse.Namespace) -> int:
    """Propose a week. Nothing is written anywhere -- this only prints."""
    from .coach import CoachUnavailable

    with open_repo(config) as repo:
        try:
            from .coach.agent import propose_week

            week = date.fromisoformat(args.week) if args.week else None
            result = await propose_week(repo, config, week)
        except CoachUnavailable as exc:
            print(exc)
            return 1

    proposal = result["proposal"]
    if not proposal:
        print("The coach returned no proposal.")
        return 1

    print(f"Proposed week of {result['week_start']}\n")
    for session in proposal["sessions"]:
        label = session.get("focus") or session["kind"]
        print(f"  {session['session_date']}  {label}")
        for exercise in session.get("exercises") or []:
            targets = ", ".join(exercise.get("targets") or []) or "-"
            print(f"      {exercise['title']:<34} {targets}")
        if session.get("distance_km"):
            print(f"      {session['distance_km']:g} km")

    print(f"\n  why: {proposal['rationale']}")
    if proposal.get("trade_offs"):
        print(f"  gave up: {proposal['trade_offs']}")
    if args.trace:
        print("\n  tools called:")
        for step in result["agent_trace"]:
            print(f"    {step['tool']}({step['args']})")

    # Set counts and weights are deliberately absent: they are computed from
    # the deficit, not chosen by the model. That lands with the assembler.
    print("\n  (proposal only -- no sets, no weights, nothing written)")
    return 0


def cmd_exercises(config: Config, args: argparse.Namespace) -> int:
    with open_repo(config) as repo:
        for row in find_exercise(repo, args.query, args.limit):
            mark = "*" if row["logged"] else " "
            secondary = ", ".join(row["secondary_muscle_groups"]) or "-"
            print(f" {mark} {row['id']}  {row['title']}")
            print(f"     primary: {row['primary_muscle_group']}   secondary: {secondary}")
    return 0


def cmd_insights(config: Config, args: argparse.Namespace) -> int:
    from .queries import insight_report

    with open_repo(config) as repo:
        rows = insight_report(repo, config)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("Nothing flagged.")
            return 0
        for row in rows:
            marker = "!" if row["severity"] == "warn" else "-"
            print(f" {marker} [{row['rule']}] {row['message']}")
    return 0


def cmd_progression(config: Config, args: argparse.Namespace) -> int:
    from .queries import progression_report

    with open_repo(config) as repo:
        rows = progression_report(repo, config)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        for row in rows:
            weight = "-" if row["working_weight_kg"] is None else f"{row['working_weight_kg']:g} kg"
            reps = ",".join(str(r) for r in row["reps"]) or "-"
            print(f"  {row['exercise'][:34]:<36}{weight:>9}  reps {reps:<10} [{row['rep_range']}]  {row['verdict']}")
    return 0


def cmd_export(config: Config, args: argparse.Namespace) -> int:
    """Dump every table to JSON.

    Migration readiness: the move to a hosted store later should be a load, not
    a rewrite. Nothing outside db.py knows the data lives in a file, and this
    gives that claim something to stand on.
    """
    with open_repo(config) as repo:
        tables = [
            row[0]
            for row in repo.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        dump = {
            table: [dict(row) for row in repo.conn.execute(f"SELECT * FROM {table}")]
            for table in sorted(tables)
        }

    payload = json.dumps(dump, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Wrote {len(payload):,} bytes to {args.out}")
        for table, rows in dump.items():
            print(f"  {table:<22}{len(rows):>7} rows")
    else:
        print(payload)
    return 0


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    import uvicorn

    print(f"Dashboard on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    uvicorn.run(
        "fitness_ledger.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


async def cmd_ask(config: Config, args: argparse.Namespace) -> int:
    from .chat import answer

    with open_repo(config) as repo:
        try:
            reply = await answer(repo, config, args.question)
        except RuntimeError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
    print(reply)
    return 0


# --- wiring ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Training volume ledger")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check data sources and cache")

    p_sync = sub.add_parser("sync", help="pull Hevy and Google Health into the cache")
    p_sync.add_argument("--full", action="store_true", help="re-backfill all workouts")
    p_sync.add_argument("--weeks", type=int, default=12, help="health history to pull")

    p_volume = sub.add_parser("volume", help="volume vs target per muscle group")
    p_volume.add_argument("--window", default="last-week")

    p_muscle = sub.add_parser("muscle", help="volume for one muscle group")
    p_muscle.add_argument("muscle")
    p_muscle.add_argument("--window", default="last-week")

    p_neglected = sub.add_parser("neglected", help="muscle groups furthest below target")
    p_neglected.add_argument("--window", default="last-week")
    p_neglected.add_argument("--limit", type=int, default=5)

    p_trend = sub.add_parser("trend", help="weekly volume over time")
    p_trend.add_argument("--weeks", type=int, default=8)
    p_trend.add_argument("--muscle", default=None)

    p_progress = sub.add_parser("progress", help="estimated 1RM over time for a lift")
    p_progress.add_argument("exercise")
    p_progress.add_argument("--weeks", type=int, default=12)

    p_runs = sub.add_parser("runs", help="run log")
    p_runs.add_argument("--window", default="last-4-weeks")

    p_health = sub.add_parser("health", help="sleep, resting HR and steps")
    p_health.add_argument("--window", default="last-2-weeks")

    p_targets = sub.add_parser("targets", help="show or set volume targets")
    p_targets.add_argument("--set", nargs="*", metavar="MUSCLE=SETS")

    p_goals = sub.add_parser("goals", help="show or set training goals and the running target")
    p_goals.add_argument(
        "--add", metavar="TYPE=VALUE",
        help="e.g. strength_1rm=100 (with --subject), running_volume=25, consistency=4",
    )
    p_goals.add_argument("--subject", help="exercise name, required for strength_1rm")
    p_goals.add_argument("--by", metavar="YYYY-MM-DD", help="optional target date")
    p_goals.add_argument("--done", type=int, metavar="ID", help="mark a goal achieved")
    p_goals.add_argument("--abandon", action="store_true", help="with --done, mark abandoned instead")
    p_goals.add_argument("--all", action="store_true", help="include achieved and abandoned")
    p_goals.add_argument(
        "--set-running", metavar="KM[/RUNS]",
        help="weekly running target, e.g. 25 or 25/3",
    )

    p_unavailable = sub.add_parser("unavailable", help="declare a day you cannot train")
    p_unavailable.add_argument("date", help="YYYY-MM-DD")
    p_unavailable.add_argument("--reason", help="free text, e.g. work")
    p_unavailable.add_argument("--clear", action="store_true", help="hand the day back")

    p_plan = sub.add_parser("plan", help="propose a training week (needs the coach extra)")
    p_plan.add_argument("--week", metavar="YYYY-MM-DD", help="Monday to plan; default next week")
    p_plan.add_argument("--trace", action="store_true", help="show the tool calls the coach made")

    p_exercises = sub.add_parser("exercises", help="search the exercise catalog")
    p_exercises.add_argument("query")
    p_exercises.add_argument("--limit", type=int, default=5)

    p_ask = sub.add_parser("ask", help="natural language question (needs a model key)")
    p_ask.add_argument("question")

    p_export = sub.add_parser("export", help="dump every table to JSON for migration")
    p_export.add_argument("--out", default=None, help="file to write (default: stdout)")

    sub.add_parser("insights", help="run the detection rules")
    sub.add_parser("progression", help="double progression state per main lift")

    p_serve = sub.add_parser("serve", help="run the dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    return parser


ASYNC_COMMANDS = {"doctor", "sync", "ask", "plan"}

HANDLERS = {
    "doctor": cmd_doctor,
    "sync": cmd_sync,
    "ask": cmd_ask,
    "volume": cmd_volume,
    "muscle": cmd_muscle,
    "neglected": cmd_neglected,
    "trend": cmd_trend,
    "progress": cmd_progress,
    "runs": cmd_runs,
    "health": cmd_health,
    "targets": cmd_targets,
    "goals": cmd_goals,
    "plan": cmd_plan,
    "unavailable": cmd_unavailable,
    "exercises": cmd_exercises,
    "insights": cmd_insights,
    "progression": cmd_progression,
    "serve": cmd_serve,
    "export": cmd_export,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load()
    handler = HANDLERS[args.command]
    if args.command in ASYNC_COMMANDS:
        return asyncio.run(handler(config, args))
    return handler(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
