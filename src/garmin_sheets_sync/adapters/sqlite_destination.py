"""Persistent local destination used for offline development and tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from garmin_sheets_sync.models import IngestionBatch, format_timestamp
from garmin_sheets_sync.ports import SyncReport, UpsertCounts


class SqliteDestination:
    name = "sqlite"

    def __init__(self, path: Path) -> None:
        self._path = path

    def sync(self, batch: IngestionBatch, completed_at: datetime) -> SyncReport:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            self._create_schema(connection)
            weights = self._upsert(
                connection,
                "weight_log",
                "measured_at",
                (
                    "measured_at",
                    "weight_kg",
                    "body_fat_percent",
                    "skeletal_muscle_mass_kg",
                    "bone_mass_kg",
                    "body_water_percent",
                    "bmi",
                    "source",
                ),
                (
                    (
                        record.key,
                        record.weight_kg,
                        record.body_fat_percent,
                        record.skeletal_muscle_mass_kg,
                        record.bone_mass_kg,
                        record.body_water_percent,
                        record.bmi,
                        record.source,
                    )
                    for record in batch.weights
                ),
            )
            daily = self._upsert(
                connection,
                "daily_activity",
                "date",
                ("date", "steps", "active_calories"),
                (
                    (record.key, record.steps, record.active_calories)
                    for record in batch.daily_activity
                ),
            )
            activities = self._upsert(
                connection,
                "activities",
                "activity_id",
                (
                    "activity_id",
                    "name",
                    "activity_type",
                    "started_at",
                    "duration_seconds",
                    "distance_meters",
                    "calories_kcal",
                    "average_heart_rate_bpm",
                    "max_heart_rate_bpm",
                    "connect_url",
                ),
                (
                    (
                        record.key,
                        record.name,
                        record.activity_type,
                        format_timestamp(record.started_at),
                        record.duration_seconds,
                        record.distance_meters,
                        record.calories_kcal,
                        record.average_heart_rate_bpm,
                        record.max_heart_rate_bpm,
                        record.connect_url,
                    )
                    for record in batch.activities
                ),
            )
            connection.execute(
                """
                INSERT INTO metadata (key, value) VALUES ('last_successful_sync', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (format_timestamp(completed_at),),
            )
        return SyncReport(
            weights=weights,
            daily_activity=daily,
            activities=activities,
            completed_at=completed_at,
        )

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS weight_log (
                measured_at TEXT PRIMARY KEY,
                weight_kg REAL NOT NULL,
                body_fat_percent REAL,
                skeletal_muscle_mass_kg REAL,
                bone_mass_kg REAL,
                body_water_percent REAL,
                bmi REAL,
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_activity (
                date TEXT PRIMARY KEY,
                steps INTEGER NOT NULL,
                active_calories REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activities (
                activity_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                distance_meters REAL,
                calories_kcal REAL,
                average_heart_rate_bpm REAL,
                max_heart_rate_bpm REAL,
                connect_url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        existing_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(activities)")
        }
        for column in (
            "calories_kcal",
            "average_heart_rate_bpm",
            "max_heart_rate_bpm",
        ):
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE activities ADD COLUMN {column} REAL"  # noqa: S608
                )

    @staticmethod
    def _upsert(
        connection: sqlite3.Connection,
        table: str,
        key_column: str,
        columns: tuple[str, ...],
        records: Iterable[tuple[Any, ...]],
    ) -> UpsertCounts:
        counts = UpsertCounts()
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(
            f"{column} = excluded.{column}" for column in columns if column != key_column
        )
        for record in records:
            existing = connection.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE {key_column} = ?",  # noqa: S608
                (record[0],),
            ).fetchone()
            if existing is None:
                connection.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
                    record,
                )
                counts = counts + UpsertCounts(inserted=1)
            elif tuple(existing) == record:
                counts = counts + UpsertCounts(unchanged=1)
            else:
                connection.execute(
                    f"""
                    INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})
                    ON CONFLICT({key_column}) DO UPDATE SET {assignments}
                    """,  # noqa: S608
                    record,
                )
                counts = counts + UpsertCounts(updated=1)
        return counts
