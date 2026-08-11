import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from pytest import MonkeyPatch

from garmin_sheets_sync import cli
from garmin_sheets_sync.adapters.fixture_source import FixtureSource
from garmin_sheets_sync.adapters.sqlite_destination import SqliteDestination
from garmin_sheets_sync.cli import main
from garmin_sheets_sync.locking import RunLock
from garmin_sheets_sync.models import DateWindow
from garmin_sheets_sync.service import SyncService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample.json"
COMPLETED_AT = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)


def test_repeated_local_sync_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "sync.db"
    service = SyncService(
        FixtureSource(FIXTURE),
        SqliteDestination(database),
        clock=lambda: COMPLETED_AT,
    )
    window = DateWindow(date(2026, 8, 8), date(2026, 8, 9))

    first = service.run(window)
    second = service.run(window)

    assert first.total.inserted == 6
    assert second.total.inserted == 0
    assert second.total.updated == 0
    assert second.total.unchanged == 6
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM weight_log").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM daily_activity").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM activities").fetchone() == (2,)
        assert connection.execute(
            """
            SELECT calories_kcal, average_heart_rate_bpm, max_heart_rate_bpm
            FROM activities WHERE activity_id = '12345678901234567'
            """
        ).fetchone() == (612.0, 149.0, 176.0)
        assert connection.execute(
            """
            SELECT calories_kcal, average_heart_rate_bpm, max_heart_rate_bpm
            FROM activities WHERE activity_id = '12345678901234568'
            """
        ).fetchone() == (None, None, None)
        assert connection.execute(
            "SELECT value FROM metadata WHERE key = 'last_successful_sync'"
        ).fetchone() == ("2026-08-09T20:00:00Z",)


def test_cli_fixture_mode_needs_no_credentials(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    for name in (
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "GOOGLE_SHEET_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    result = main(
        [
            "sync",
            "--source",
            "fixture",
            "--destination",
            "sqlite",
            "--fixture",
            str(FIXTURE),
            "--database",
            str(tmp_path / "local.db"),
            "--lock-file",
            str(tmp_path / "sync.lock"),
            "--start",
            "2026-08-08",
            "--end",
            "2026-08-09",
        ]
    )

    assert result == 0


def test_cli_worker_needs_no_credentials(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "GARMIN_EMAIL",
        "GARMIN_PASSWORD",
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "GOOGLE_SHEET_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    worker_started = False

    def run_worker() -> int:
        nonlocal worker_started
        worker_started = True
        return 0

    monkeypatch.setattr(cli, "_run_worker", run_worker)

    result = main(["worker"])

    assert result == 0
    assert worker_started is True


def test_cli_acquires_lock_before_building_source(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    lock_file = tmp_path / "sync.lock"

    source_was_built = False

    def unexpected_source_build(args: object) -> object:
        nonlocal source_was_built
        source_was_built = True
        raise AssertionError("source must not be built while another run owns the lock")

    monkeypatch.setattr(cli, "_build_source", unexpected_source_build)
    with RunLock(lock_file):
        result = main(
            [
                "sync",
                "--source",
                "fixture",
                "--destination",
                "sqlite",
                "--fixture",
                str(FIXTURE),
                "--database",
                str(tmp_path / "local.db"),
                "--lock-file",
                str(lock_file),
                "--start",
                "2026-08-08",
                "--end",
                "2026-08-09",
            ]
        )

    assert result == 1
    assert source_was_built is False


def test_sqlite_destination_migrates_legacy_activity_table(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE activities (
                activity_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                distance_meters REAL,
                connect_url TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO activities (
                activity_id, name, activity_type, started_at,
                duration_seconds, distance_meters, connect_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-activity",
                "Legacy Run",
                "running",
                "2026-08-01T06:00:00Z",
                1200,
                4000,
                "https://connect.garmin.com/modern/activities",
            ),
        )

    service = SyncService(
        FixtureSource(FIXTURE),
        SqliteDestination(database),
        clock=lambda: COMPLETED_AT,
    )
    window = DateWindow(date(2026, 8, 8), date(2026, 8, 9))

    first = service.run(window)
    second = service.run(window)

    assert first.activities.inserted == 2
    assert second.total.unchanged == 6
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(activities)")
        }
        assert {
            "calories_kcal",
            "average_heart_rate_bpm",
            "max_heart_rate_bpm",
        } <= columns
        assert connection.execute(
            """
            SELECT calories_kcal, average_heart_rate_bpm, max_heart_rate_bpm
            FROM activities WHERE activity_id = '12345678901234567'
            """
        ).fetchone() == (612.0, 149.0, 176.0)
        assert connection.execute(
            "SELECT name FROM activities WHERE activity_id = 'legacy-activity'"
        ).fetchone() == ("Legacy Run",)


def test_cli_rejects_live_run_with_implicit_log_only_alerting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("ALERT_MODE", "log")

    result = main(
        [
            "sync",
            "--source",
            "garmin",
            "--destination",
            "sqlite",
            "--lock-file",
            str(tmp_path / "sync.lock"),
            "--start",
            "2026-08-09",
            "--end",
            "2026-08-09",
        ]
    )

    assert result == 1
