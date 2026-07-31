"""Storage.

A repository interface sits between the rules engine and SQLite so that moving to
Firestore or a GCS-mounted file at v0.4 is a contained change. Nothing outside
this module knows that persistence is a local file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .models import ExerciseTemplate, Run, SetEntry, VolumeTarget, Workout

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
"""


class Repository(Protocol):
    """What the rules engine and CLI are allowed to assume about storage."""

    def upsert_templates(self, templates: Iterable[ExerciseTemplate]) -> int: ...
    def get_templates(self) -> dict[str, ExerciseTemplate]: ...
    def upsert_workout(self, workout: dict) -> None: ...
    def delete_workout(self, workout_id: str) -> None: ...
    def get_sets(self, start: date, end: date) -> list[SetEntry]: ...
    def get_workouts(self, start: date, end: date) -> list[Workout]: ...
    def upsert_runs(self, runs: Iterable[dict]) -> int: ...
    def get_runs(self, start: date, end: date) -> list[Run]: ...
    def get_targets(self) -> dict[str, VolumeTarget]: ...
    def set_targets(self, targets: Iterable[VolumeTarget]) -> None: ...
    def get_state(self, key: str) -> str | None: ...
    def set_state(self, key: str, value: str) -> None: ...


class SQLiteRepository:
    """SQLite implementation of :class:`Repository`."""

    def __init__(self, db_path: Path, local_utc_offset_minutes: int = 0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_minutes = local_utc_offset_minutes
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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

    def latest_workout_start(self) -> str | None:
        row = self.conn.execute("SELECT MAX(start_time) FROM workouts").fetchone()
        return row[0] if row else None

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
