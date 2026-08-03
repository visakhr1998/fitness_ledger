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
from .models import VolumeTarget
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


ASYNC_COMMANDS = {"doctor", "sync", "ask"}

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
