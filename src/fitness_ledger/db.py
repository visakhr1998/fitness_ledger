"""Storage.

A repository interface sits between the rules engine and SQLite so that moving to
Firestore or a GCS-mounted file at v0.4 is a contained change. Nothing outside
this module knows that persistence is a local file.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import (
    GOAL_STATUSES,
    PLAN_STATUSES,
    Availability,
    ExerciseTemplate,
    Goal,
    Plan,
    PlannedExercise,
    PlannedSession,
    RecurringConstraint,
    Run,
    RunningTarget,
    SetEntry,
    VolumeTarget,
    Workout,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS exercise_templates (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL,
    type                    TEXT,
    primary_muscle_group    TEXT,
    secondary_muscle_groups TEXT NOT NULL DEFAULT '[]',
    equipment_category      TEXT,
    is_custom               INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workouts (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    description TEXT,
    routine_id  TEXT,
    start_time  TEXT NOT NULL,
    end_time    TEXT,
    local_date  TEXT NOT NULL,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_workouts_local_date ON workouts(local_date);

CREATE TABLE IF NOT EXISTS workout_sets (
    workout_id           TEXT NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_index       INTEGER NOT NULL,
    set_index            INTEGER NOT NULL,
    exercise_template_id TEXT,
    exercise_title       TEXT,
    exercise_notes       TEXT,
    superset_id          INTEGER,
    set_type             TEXT NOT NULL DEFAULT 'normal',
    weight_kg            REAL,
    reps                 INTEGER,
    distance_meters      INTEGER,
    duration_seconds     INTEGER,
    rpe                  REAL,
    PRIMARY KEY (workout_id, exercise_index, set_index)
);
CREATE INDEX IF NOT EXISTS idx_sets_template ON workout_sets(exercise_template_id);

CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    start_time          TEXT NOT NULL,
    end_time            TEXT,
    local_date          TEXT NOT NULL,
    exercise_type       TEXT,
    distance_m          REAL,
    active_duration_s   REAL,
    calories_kcal       REAL,
    steps               INTEGER,
    avg_heart_rate      REAL,
    active_zone_minutes INTEGER,
    raw                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_local_date ON runs(local_date);

CREATE TABLE IF NOT EXISTS health_daily (
    local_date TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    unit       TEXT,
    raw        TEXT,
    PRIMARY KEY (local_date, metric)
);

CREATE TABLE IF NOT EXISTS volume_targets (
    muscle_group       TEXT PRIMARY KEY,
    sets_per_week      REAL NOT NULL,
    frequency_per_week INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Rep ranges are intent, and a logged set does not record intent, so they are
-- configuration rather than something inferred from history.
CREATE TABLE IF NOT EXISTS exercise_progression (
    exercise_template_id TEXT PRIMARY KEY,
    rep_low              INTEGER NOT NULL,
    rep_high             INTEGER NOT NULL
);

-- One AEI record per run. method_version is part of the identity of the value:
-- a figure computed under a different binning is not comparable, so a version
-- bump invalidates rows rather than mixing them into the same series.
CREATE TABLE IF NOT EXISTS run_metrics (
    run_id               TEXT PRIMARY KEY,
    local_date           TEXT NOT NULL,
    actual_distance_m    REAL,
    adjusted_distance_m  REAL,
    avg_heart_rate       REAL,
    total_beats          REAL,
    aei                  REAL,
    method_version       INTEGER NOT NULL,
    computed_at          TEXT NOT NULL,
    reported_distance_m  REAL,
    track_coverage       REAL,
    reliable             INTEGER NOT NULL DEFAULT 1,
    unreliable_reason    TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_metrics_date ON run_metrics(local_date);

-- The 25 m bins behind each AEI. Keeping these means changing the method
-- recomputes locally instead of re-downloading ~1.2 MB of TCX per run.
CREATE TABLE IF NOT EXISTS run_segments (
    run_id      TEXT NOT NULL REFERENCES run_metrics(run_id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    distance_m  REAL NOT NULL,
    grade       REAL NOT NULL,
    heart_rate  REAL,
    seconds     REAL NOT NULL,
    PRIMARY KEY (run_id, idx)
);

-- Things only the user can tell us: sex for BMR, and overrides that beat a formula.
CREATE TABLE IF NOT EXISTS user_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- What the user is training toward. Distinct from volume_targets, which is a
-- weekly maintenance level: a target says "keep chest at 14 sets", a goal says
-- "get bench to 100 kg". The coach reads targets to find the deficit and goals
-- to decide which deficits matter.
CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,
    subject      TEXT,
    target_value REAL NOT NULL,
    target_date  TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    supersedes   INTEGER
);

-- Only exceptions are recorded: a day with no row is available. Declaring a
-- day lost is what triggers a replan, so this table is normally near-empty.
CREATE TABLE IF NOT EXISTS availability (
    local_date TEXT PRIMARY KEY,
    available  INTEGER NOT NULL DEFAULT 0,
    reason     TEXT,
    source     TEXT NOT NULL DEFAULT 'declared'
);

-- Standing weekday restrictions, unlike `availability` which records specific
-- dates that were lost. "No high impact on Wednesdays" outlives any one week,
-- so it is a rule rather than an exception. UNIQUE keeps a weekday from
-- collecting duplicates of the same restriction.
CREATE TABLE IF NOT EXISTS recurring_constraints (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday  INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    reason   TEXT,
    UNIQUE (weekday, kind)
);

-- Every write-back proposal, approved or not. Hevy has no delete endpoint, so
-- this is the only record of what this app caused to exist.
CREATE TABLE IF NOT EXISTS writeback_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    summary      TEXT,
    payload_json TEXT NOT NULL,
    diff_json    TEXT,
    status       TEXT NOT NULL DEFAULT 'proposed',
    hevy_id      TEXT,
    approved_at  TEXT,
    error        TEXT
);

-- Proposed training weeks. Append-only: a revision is a new row that
-- supersedes the old one, never an edit. Why a week looked the way it did is
-- part of the record, and the coach reads the previous plan to tell a new
-- shortfall from one that has persisted.
CREATE TABLE IF NOT EXISTS plans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start       TEXT NOT NULL,
    generated_at     TEXT NOT NULL,
    sessions_json    TEXT NOT NULL,
    rationale        TEXT,
    trade_offs       TEXT,
    status           TEXT NOT NULL DEFAULT 'proposed',
    supersedes       INTEGER,
    agent_trace_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_plans_week ON plans(week_start, id);
"""


class SQLiteRepository:
    """Every read and write in the app, in one place.

    There was a `Repository` typing protocol here, meant as the seam a v0.4
    move off local SQLite would swap behind. Deleted 2026-09-04: nothing ever
    annotated against it, so nothing type-checked it, and it had drifted to 27
    methods against this class's 82. It was a second list to keep in agreement
    -- the shape this repo rejects in `sync_all` and in the AEI beats
    definition -- and it could not have been honest anyway, because `cli`,
    `queries` and `sections` reach into `.conn` and run SQL no interface can
    express.

    What actually contains that move is this file: no code outside it builds a
    path or opens a connection. Re-introduce an interface when there is a
    second implementation to conform to it, and retire the `.conn` reach-ins
    first.
    """

    def __init__(self, db_path: Path, local_utc_offset_minutes: int = 0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_minutes = local_utc_offset_minutes
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._add_missing_columns()
        self.conn.commit()

    def _add_missing_columns(self) -> None:
        """Bring an existing database up to the current schema.

        CREATE TABLE IF NOT EXISTS silently skips a table that already exists, so
        columns added later never appear without this.
        """
        additions = {
            "goals": [("supersedes", "INTEGER")],
            "run_metrics": [
                ("reported_distance_m", "REAL"),
                ("track_coverage", "REAL"),
                ("reliable", "INTEGER NOT NULL DEFAULT 1"),
                ("unreliable_reason", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue
            for name, definition in columns:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SQLiteRepository":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- time helpers ------------------------------------------------------

    def to_local_date(self, iso_timestamp: str) -> date:
        """Local calendar day for an ISO timestamp, using the configured offset."""
        return local_date_of(iso_timestamp, self.offset_minutes)

    # --- exercise templates ------------------------------------------------

    def upsert_templates(self, templates: Iterable[ExerciseTemplate]) -> int:
        rows = [
            (
                t.id,
                t.title,
                t.type,
                t.primary_muscle_group,
                json.dumps(list(t.secondary_muscle_groups)),
                t.equipment_category,
                int(t.is_custom),
            )
            for t in templates
        ]
        self.conn.executemany(
            """INSERT INTO exercise_templates
                 (id, title, type, primary_muscle_group, secondary_muscle_groups,
                  equipment_category, is_custom)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 type=excluded.type,
                 primary_muscle_group=excluded.primary_muscle_group,
                 secondary_muscle_groups=excluded.secondary_muscle_groups,
                 equipment_category=excluded.equipment_category,
                 is_custom=excluded.is_custom""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_templates(self) -> dict[str, ExerciseTemplate]:
        cur = self.conn.execute("SELECT * FROM exercise_templates")
        out: dict[str, ExerciseTemplate] = {}
        for row in cur:
            out[row["id"]] = ExerciseTemplate(
                id=row["id"],
                title=row["title"],
                type=row["type"],
                primary_muscle_group=row["primary_muscle_group"],
                secondary_muscle_groups=tuple(
                    json.loads(row["secondary_muscle_groups"] or "[]")
                ),
                equipment_category=row["equipment_category"],
                is_custom=bool(row["is_custom"]),
            )
        return out

    # --- workouts ----------------------------------------------------------

    def upsert_workout(self, workout: dict) -> None:
        """Insert or replace one Hevy workout payload and all of its sets."""
        workout_id = workout["id"]
        start_time = workout["start_time"]
        self.conn.execute(
            """INSERT INTO workouts
                 (id, title, description, routine_id, start_time, end_time,
                  local_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 description=excluded.description,
                 routine_id=excluded.routine_id,
                 start_time=excluded.start_time,
                 end_time=excluded.end_time,
                 local_date=excluded.local_date,
                 updated_at=excluded.updated_at""",
            (
                workout_id,
                workout.get("title"),
                workout.get("description"),
                workout.get("routine_id"),
                start_time,
                workout.get("end_time"),
                self.to_local_date(start_time).isoformat(),
                workout.get("created_at"),
                workout.get("updated_at"),
            ),
        )
        # Sets are replaced wholesale: Hevy updates rewrite a workout's contents.
        self.conn.execute("DELETE FROM workout_sets WHERE workout_id = ?", (workout_id,))
        rows = []
        for ex_idx, exercise in enumerate(workout.get("exercises") or []):
            exercise_index = exercise.get("index", ex_idx)
            for set_idx, entry in enumerate(exercise.get("sets") or []):
                rows.append(
                    (
                        workout_id,
                        exercise_index,
                        entry.get("index", set_idx),
                        exercise.get("exercise_template_id"),
                        exercise.get("title"),
                        exercise.get("notes"),
                        exercise.get("superset_id") or exercise.get("supersets_id"),
                        entry.get("type") or "normal",
                        entry.get("weight_kg"),
                        entry.get("reps"),
                        entry.get("distance_meters"),
                        entry.get("duration_seconds"),
                        entry.get("rpe"),
                    )
                )
        if rows:
            self.conn.executemany(
                """INSERT OR REPLACE INTO workout_sets
                     (workout_id, exercise_index, set_index, exercise_template_id,
                      exercise_title, exercise_notes, superset_id, set_type,
                      weight_kg, reps, distance_meters, duration_seconds, rpe)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        self.conn.commit()

    def delete_workout(self, workout_id: str) -> None:
        self.conn.execute("DELETE FROM workout_sets WHERE workout_id = ?", (workout_id,))
        self.conn.execute("DELETE FROM workouts WHERE id = ?", (workout_id,))
        self.conn.commit()

    def get_sets(self, start: date, end: date) -> list[SetEntry]:
        cur = self.conn.execute(
            """SELECT s.*, w.local_date
                 FROM workout_sets s
                 JOIN workouts w ON w.id = s.workout_id
                WHERE w.local_date >= ? AND w.local_date < ?
                ORDER BY w.local_date, s.exercise_index, s.set_index""",
            (start.isoformat(), end.isoformat()),
        )
        return [
            SetEntry(
                workout_id=row["workout_id"],
                local_date=date.fromisoformat(row["local_date"]),
                exercise_template_id=row["exercise_template_id"] or "",
                exercise_title=row["exercise_title"] or "",
                set_type=row["set_type"] or "normal",
                weight_kg=row["weight_kg"],
                reps=row["reps"],
                rpe=row["rpe"],
            )
            for row in cur
        ]

    def get_workouts(self, start: date, end: date) -> list[Workout]:
        cur = self.conn.execute(
            """SELECT * FROM workouts
                WHERE local_date >= ? AND local_date < ?
                ORDER BY start_time""",
            (start.isoformat(), end.isoformat()),
        )
        return [
            Workout(
                id=row["id"],
                title=row["title"] or "",
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=(
                    datetime.fromisoformat(row["end_time"])
                    if row["end_time"]
                    else datetime.fromisoformat(row["start_time"])
                ),
                local_date=date.fromisoformat(row["local_date"]),
                description=row["description"],
                routine_id=row["routine_id"],
            )
            for row in cur
        ]

    def count_workouts(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]

    # --- runs and health ---------------------------------------------------

    def upsert_runs(self, runs: Iterable[dict]) -> int:
        rows = []
        for run in runs:
            rows.append(
                (
                    run["id"],
                    run["start_time"],
                    run.get("end_time"),
                    self.to_local_date(run["start_time"]).isoformat(),
                    run.get("exercise_type"),
                    run.get("distance_m"),
                    run.get("active_duration_s"),
                    run.get("calories_kcal"),
                    run.get("steps"),
                    run.get("avg_heart_rate"),
                    run.get("active_zone_minutes"),
                    json.dumps(run.get("raw", {})),
                )
            )
        self.conn.executemany(
            """INSERT OR REPLACE INTO runs
                 (id, start_time, end_time, local_date, exercise_type, distance_m,
                  active_duration_s, calories_kcal, steps, avg_heart_rate,
                  active_zone_minutes, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_runs(self, start: date, end: date) -> list[Run]:
        cur = self.conn.execute(
            """SELECT * FROM runs
                WHERE local_date >= ? AND local_date < ?
                ORDER BY start_time""",
            (start.isoformat(), end.isoformat()),
        )
        return [
            Run(
                id=row["id"],
                start_time=datetime.fromisoformat(row["start_time"]),
                local_date=date.fromisoformat(row["local_date"]),
                exercise_type=row["exercise_type"] or "",
                distance_m=row["distance_m"],
                active_duration_s=row["active_duration_s"],
                calories_kcal=row["calories_kcal"],
                steps=row["steps"],
                avg_heart_rate=row["avg_heart_rate"],
                active_zone_minutes=row["active_zone_minutes"],
            )
            for row in cur
        ]

    def upsert_health_daily(self, rows: Iterable[tuple[str, str, float | None, str | None, str]]) -> int:
        rows = list(rows)
        self.conn.executemany(
            """INSERT OR REPLACE INTO health_daily (local_date, metric, value, unit, raw)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_health_daily(self, metric: str, start: date, end: date) -> list[tuple[date, float | None]]:
        cur = self.conn.execute(
            """SELECT local_date, value FROM health_daily
                WHERE metric = ? AND local_date >= ? AND local_date < ?
                ORDER BY local_date""",
            (metric, start.isoformat(), end.isoformat()),
        )
        return [(date.fromisoformat(r["local_date"]), r["value"]) for r in cur]

    # --- targets and sync state -------------------------------------------

    def get_targets(self) -> dict[str, VolumeTarget]:
        cur = self.conn.execute("SELECT * FROM volume_targets")
        return {
            row["muscle_group"]: VolumeTarget(
                muscle_group=row["muscle_group"],
                sets_per_week=row["sets_per_week"],
                frequency_per_week=row["frequency_per_week"],
            )
            for row in cur
        }

    def set_targets(self, targets: Iterable[VolumeTarget]) -> None:
        self.conn.executemany(
            """INSERT INTO volume_targets (muscle_group, sets_per_week, frequency_per_week)
               VALUES (?, ?, ?)
               ON CONFLICT(muscle_group) DO UPDATE SET
                 sets_per_week=excluded.sets_per_week,
                 frequency_per_week=excluded.frequency_per_week""",
            [(t.muscle_group, t.sets_per_week, t.frequency_per_week) for t in targets],
        )
        self.conn.commit()

    # --- run metrics (AEI) -------------------------------------------------

    def upsert_run_metrics(
        self,
        run_id: str,
        metrics,
        segments,
        *,
        reported_distance_m: float | None = None,
        reliable: bool = True,
        unreliable_reason: str | None = None,
        track_coverage: float | None = None,
    ) -> None:
        """Store one AEI record and the bins it came from."""
        self.conn.execute(
            """INSERT INTO run_metrics
                 (run_id, local_date, actual_distance_m, adjusted_distance_m,
                  avg_heart_rate, total_beats, aei, method_version, computed_at,
                  reported_distance_m, track_coverage, reliable, unreliable_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 local_date=excluded.local_date,
                 actual_distance_m=excluded.actual_distance_m,
                 adjusted_distance_m=excluded.adjusted_distance_m,
                 avg_heart_rate=excluded.avg_heart_rate,
                 total_beats=excluded.total_beats,
                 aei=excluded.aei,
                 method_version=excluded.method_version,
                 computed_at=excluded.computed_at,
                 reported_distance_m=excluded.reported_distance_m,
                 track_coverage=excluded.track_coverage,
                 reliable=excluded.reliable,
                 unreliable_reason=excluded.unreliable_reason""",
            (
                run_id,
                metrics.local_date.isoformat(),
                metrics.actual_distance_m,
                metrics.adjusted_distance_m,
                metrics.avg_heart_rate,
                metrics.total_beats,
                metrics.aei,
                metrics.method_version,
                datetime.now(timezone.utc).isoformat(),
                reported_distance_m,
                track_coverage,
                int(reliable),
                unreliable_reason,
            ),
        )
        self.conn.execute("DELETE FROM run_segments WHERE run_id = ?", (run_id,))
        self.conn.executemany(
            """INSERT INTO run_segments (run_id, idx, distance_m, grade, heart_rate, seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (run_id, s.index, s.distance_m, s.grade, s.heart_rate, s.seconds)
                for s in segments
            ],
        )
        self.conn.commit()

    def get_run_metrics(self, start: date, end: date) -> list[dict]:
        cur = self.conn.execute(
            """SELECT * FROM run_metrics
                WHERE local_date >= ? AND local_date < ?
                ORDER BY local_date""",
            (start.isoformat(), end.isoformat()),
        )
        return [dict(row) for row in cur]

    def run_ids_needing_metrics(self, method_version: int) -> list[tuple[str, str, float | None]]:
        """Runs with no AEI, or one computed under a superseded method."""
        cur = self.conn.execute(
            """SELECT r.id, r.local_date, r.distance_m
                 FROM runs r
                 LEFT JOIN run_metrics m ON m.run_id = r.id
                WHERE r.exercise_type = 'RUNNING'
                  AND (m.run_id IS NULL OR m.method_version <> ?)
                ORDER BY r.start_time DESC""",
            (method_version,),
        )
        return [(row["id"], row["local_date"], row["distance_m"]) for row in cur]

    def get_run_segments(self, run_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM run_segments WHERE run_id = ? ORDER BY idx", (run_id,)
        )
        return [dict(row) for row in cur]

    def max_observed_heart_rate(self) -> float | None:
        row = self.conn.execute("SELECT MAX(avg_heart_rate) FROM runs").fetchone()
        return row[0] if row and row[0] else None

    # --- user settings -----------------------------------------------------

    def get_settings(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.conn.execute("SELECT * FROM user_settings")}

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM user_settings WHERE key = ?", (key,))
        else:
            self.conn.execute(
                """INSERT INTO user_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )
        self.conn.commit()

    # --- goals ---------------------------------------------------------------

    # The running target is a single pair of numbers, not a per-row collection
    # like volume_targets, so it lives in user_settings rather than earning a
    # table of its own.
    RUNNING_DISTANCE_KEY = "running_distance_km_per_week"
    RUNNING_SESSIONS_KEY = "running_sessions_per_week"

    def add_goal(self, goal: Goal) -> Goal:
        """Insert a goal and return it with the assigned id."""
        created = goal.created_at or datetime.now(timezone.utc)
        cur = self.conn.execute(
            """INSERT INTO goals (type, subject, target_value, target_date, status,
                                  created_at, supersedes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                goal.type,
                goal.subject,
                goal.target_value,
                goal.target_date.isoformat() if goal.target_date else None,
                goal.status,
                created.isoformat(),
                goal.supersedes,
            ),
        )
        self.conn.commit()
        return replace(goal, id=cur.lastrowid, created_at=created)

    def get_goals(self, include_inactive: bool = False) -> list[Goal]:
        sql = "SELECT * FROM goals"
        if not include_inactive:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY created_at DESC, id DESC"
        return [self._goal(row) for row in self.conn.execute(sql)]

    def revise_goal(self, goal_id: int, revision: Goal) -> Goal | None:
        """Replace a goal with a new one that points back at it.

        Editing appends rather than overwrites, following `add_plan`. A goal is
        an input to past planning decisions -- the coach reads active goals when
        it writes a rationale -- so changing one in place would silently rewrite
        the recorded reason a past week looked as it did.

        Returns None when there is nothing to revise, so the caller can 404
        rather than quietly inserting an orphan.
        """
        existing = self.conn.execute(
            "SELECT id FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        if existing is None:
            return None
        self.conn.execute(
            "UPDATE goals SET status = 'abandoned' WHERE id = ?", (goal_id,)
        )
        return self.add_goal(replace(revision, supersedes=goal_id, status="active"))

    def set_goal_status(self, goal_id: int, status: str) -> bool:
        """Mark a goal achieved or abandoned. Goals are never deleted -- an
        abandoned goal is part of why later plans looked the way they did."""
        if status not in GOAL_STATUSES:
            raise ValueError(f"unknown goal status {status!r}")
        cur = self.conn.execute(
            "UPDATE goals SET status = ? WHERE id = ?", (status, goal_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _goal(row: sqlite3.Row) -> Goal:
        return Goal(
            id=row["id"],
            type=row["type"],
            subject=row["subject"],
            target_value=row["target_value"],
            target_date=date.fromisoformat(row["target_date"]) if row["target_date"] else None,
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            supersedes=row["supersedes"] if "supersedes" in row.keys() else None,
        )

    def get_running_target(self) -> RunningTarget | None:
        """None means running has no target, so it cannot be protected in the
        priority ranking -- the coach has to say so rather than assume one."""
        settings = self.get_settings()
        distance = settings.get(self.RUNNING_DISTANCE_KEY)
        if distance is None:
            return None
        sessions = settings.get(self.RUNNING_SESSIONS_KEY)
        return RunningTarget(
            distance_km_per_week=float(distance),
            sessions_per_week=int(sessions) if sessions else 2,
        )

    def set_running_target(self, target: RunningTarget | None) -> None:
        if target is None:
            self.set_setting(self.RUNNING_DISTANCE_KEY, None)
            self.set_setting(self.RUNNING_SESSIONS_KEY, None)
            return
        self.set_setting(self.RUNNING_DISTANCE_KEY, str(target.distance_km_per_week))
        self.set_setting(self.RUNNING_SESSIONS_KEY, str(target.sessions_per_week))

    # --- availability --------------------------------------------------------

    def set_availability(self, entry: Availability) -> None:
        """Record a day as lost (or restore it). Re-declaring a day overwrites,
        so the user can correct a mistake without a delete command."""
        self.conn.execute(
            """INSERT INTO availability (local_date, available, reason, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(local_date) DO UPDATE SET
                   available=excluded.available,
                   reason=excluded.reason,
                   source=excluded.source""",
            (entry.local_date.isoformat(), int(entry.available), entry.reason, entry.source),
        )
        self.conn.commit()

    # --- plans -------------------------------------------------------------

    def add_plan(self, plan: Plan) -> Plan:
        """Store a plan, superseding any earlier one for the same week.

        Append-only by construction: the previous plan for that week is marked
        `superseded` and the new row records which one it replaced, so the
        history of what was proposed and why survives a replan.
        """
        generated = plan.generated_at or datetime.now(timezone.utc)
        previous = self.latest_plan(plan.week_start)
        supersedes = plan.supersedes
        if previous is not None and previous.id is not None:
            supersedes = supersedes or previous.id
            if previous.status in {"proposed", "approved"}:
                self.conn.execute(
                    "UPDATE plans SET status = 'superseded' WHERE id = ?", (previous.id,)
                )

        cur = self.conn.execute(
            """INSERT INTO plans (week_start, generated_at, sessions_json, rationale,
                                  trade_offs, status, supersedes, agent_trace_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.week_start.isoformat(),
                generated.isoformat(),
                json.dumps([session.as_dict() for session in plan.sessions]),
                plan.rationale,
                plan.trade_offs,
                plan.status,
                supersedes,
                json.dumps(list(plan.agent_trace)),
            ),
        )
        self.conn.commit()
        return replace(
            plan, id=cur.lastrowid, generated_at=generated, supersedes=supersedes
        )

    def get_plan(self, plan_id: int) -> Plan | None:
        row = self.conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._plan(row) if row else None

    def latest_plan(self, week_start: date | None = None) -> Plan | None:
        """The most recent plan, for one week or across all of them."""
        if week_start is None:
            row = self.conn.execute(
                "SELECT * FROM plans ORDER BY week_start DESC, id DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM plans WHERE week_start = ? ORDER BY id DESC LIMIT 1",
                (week_start.isoformat(),),
            ).fetchone()
        return self._plan(row) if row else None

    def set_plan_status(self, plan_id: int, status: str) -> bool:
        if status not in PLAN_STATUSES:
            raise ValueError(f"unknown plan status {status!r}")
        cur = self.conn.execute(
            "UPDATE plans SET status = ? WHERE id = ?", (status, plan_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _plan(row: sqlite3.Row) -> Plan:
        sessions = tuple(
            PlannedSession(
                local_date=date.fromisoformat(session["date"]),
                kind=session["kind"],
                focus=session.get("focus", ""),
                distance_km=session.get("distance_km"),
                exercises=tuple(
                    PlannedExercise(
                        exercise_template_id=exercise["exercise_template_id"],
                        title=exercise["title"],
                        sets=exercise["sets"],
                        targets=tuple(exercise.get("targets") or []),
                    )
                    for exercise in session.get("exercises") or []
                ),
            )
            for session in json.loads(row["sessions_json"])
        )
        return Plan(
            id=row["id"],
            week_start=date.fromisoformat(row["week_start"]),
            generated_at=datetime.fromisoformat(row["generated_at"]),
            sessions=sessions,
            rationale=row["rationale"] or "",
            trade_offs=row["trade_offs"] or "",
            status=row["status"],
            supersedes=row["supersedes"],
            agent_trace=tuple(json.loads(row["agent_trace_json"] or "[]")),
        )

    def clear_availability(self, day: date) -> bool:
        """Forget an exception entirely, returning the day to available."""
        cur = self.conn.execute(
            "DELETE FROM availability WHERE local_date = ?", (day.isoformat(),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_availability(self, start: date, end: date) -> dict[date, Availability]:
        """Exceptions in a closed-open window, keyed by day. Days absent from
        the result are available -- callers must not treat a missing key as
        unknown."""
        rows = self.conn.execute(
            "SELECT * FROM availability WHERE local_date >= ? AND local_date < ? ORDER BY local_date",
            (start.isoformat(), end.isoformat()),
        )
        entries = [
            Availability(
                local_date=date.fromisoformat(row["local_date"]),
                available=bool(row["available"]),
                reason=row["reason"],
                source=row["source"],
            )
            for row in rows
        ]
        return {entry.local_date: entry for entry in entries}

    # --- recurring constraints ------------------------------------------------

    def add_constraint(self, constraint: RecurringConstraint) -> RecurringConstraint:
        """Store a standing restriction, or return the existing one unchanged.

        Re-adding the same weekday and kind is a no-op rather than an error:
        the intake parser can propose a constraint the user already has, and
        failing that would make a correct re-statement look like a fault.
        """
        cur = self.conn.execute(
            """INSERT INTO recurring_constraints (weekday, kind, reason)
               VALUES (?, ?, ?)
               ON CONFLICT(weekday, kind) DO UPDATE SET reason=excluded.reason""",
            (constraint.weekday, constraint.kind, constraint.reason),
        )
        self.conn.commit()
        # Always read the id back rather than trusting `cur.lastrowid`. On the
        # DO UPDATE path SQLite leaves last_insert_rowid untouched, so it holds
        # whatever was last inserted on this connection -- a goal, typically --
        # and the row is truthy, so the fallback below never ran. Re-adding an
        # existing constraint then returned an id belonging to another table's
        # row, and deleting it 404'd or hit the wrong constraint.
        row = self.conn.execute(
            "SELECT id FROM recurring_constraints WHERE weekday = ? AND kind = ?",
            (constraint.weekday, constraint.kind),
        ).fetchone()
        return replace(constraint, id=row["id"] if row else None)

    def get_constraints(self) -> list[RecurringConstraint]:
        rows = self.conn.execute(
            "SELECT * FROM recurring_constraints ORDER BY weekday, kind"
        )
        return [
            RecurringConstraint(
                id=row["id"],
                weekday=row["weekday"],
                kind=row["kind"],
                reason=row["reason"],
            )
            for row in rows
        ]

    def delete_constraint(self, constraint_id: int) -> bool:
        """Constraints are deletable, unlike goals. A goal you abandoned is
        part of why past plans looked the way they did; a knee that stopped
        hurting is not a fact about last month's training."""
        cur = self.conn.execute(
            "DELETE FROM recurring_constraints WHERE id = ?", (constraint_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- write-back audit --------------------------------------------------

    def record_proposal(self, kind: str, summary: str, payload: dict, diff: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO writeback_log (proposed_at, kind, summary, payload_json, diff_json, status)
               VALUES (?, ?, ?, ?, ?, 'proposed')""",
            (
                datetime.now(timezone.utc).isoformat(),
                kind,
                summary,
                json.dumps(payload),
                json.dumps(diff),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_proposal(self, proposal_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM writeback_log WHERE id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def claim_proposal(self, proposal_id: int) -> bool:
        """Take a proposal for writing, atomically. True if this caller won it.

        Hevy has no delete endpoint, so a proposal must be written at most once.
        The approve path used to check `status == 'proposed'` and only mark the
        row *after* awaiting the Hevy call, which left a 1-2 second window --
        the time the stdio subprocess takes to spawn and answer -- in which two
        requests both saw `proposed` and both created a routine.

        The status guard lives in the UPDATE so SQLite decides the winner: the
        loser gets rowcount 0 and stops before touching Hevy.
        """
        cur = self.conn.execute(
            """UPDATE writeback_log
                  SET status = 'writing', approved_at = ?
                WHERE id = ? AND status = 'proposed'""",
            (datetime.now(timezone.utc).isoformat(), proposal_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def mark_proposal(
        self, proposal_id: int, status: str, hevy_id: str | None = None, error: str | None = None
    ) -> None:
        self.conn.execute(
            """UPDATE writeback_log
                  SET status = ?, hevy_id = ?, error = ?, approved_at = ?
                WHERE id = ?""",
            (status, hevy_id, error, datetime.now(timezone.utc).isoformat(), proposal_id),
        )
        self.conn.commit()

    def list_proposals(self, limit: int = 50) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM writeback_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur]

    def get_rep_ranges(self) -> dict[str, tuple[int, int]]:
        cur = self.conn.execute("SELECT * FROM exercise_progression")
        return {row["exercise_template_id"]: (row["rep_low"], row["rep_high"]) for row in cur}

    def set_rep_range(self, exercise_template_id: str, low: int, high: int) -> None:
        self.conn.execute(
            """INSERT INTO exercise_progression (exercise_template_id, rep_low, rep_high)
               VALUES (?, ?, ?)
               ON CONFLICT(exercise_template_id) DO UPDATE SET
                 rep_low=excluded.rep_low, rep_high=excluded.rep_high""",
            (exercise_template_id, low, high),
        )
        self.conn.commit()

    def get_sleep_minutes(self, start: date, end: date) -> dict[date, float]:
        """Sleep minutes keyed by the morning woken, for the recovery rule."""
        return {
            day: minutes
            for day, minutes in self.get_health_daily("sleep_minutes_asleep", start, end)
            if minutes is not None
        }

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO sync_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )
        self.conn.commit()


def local_date_of(iso_timestamp: str, offset_minutes: int) -> date:
    """Calendar day an instant falls on, in the user's local offset.

    Hevy returns UTC timestamps; which day a late-evening session belongs to
    depends on the local offset, so week bucketing has to go through here.
    """
    text = iso_timestamp.replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone(timedelta(minutes=offset_minutes))).date()
