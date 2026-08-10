"""Command-line entry point for one-shot sync runs."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from garmin_sheets_sync.adapters.alerts import (
    LogAlertSink,
    SmtpAlertSink,
    SmtpSettings,
)
from garmin_sheets_sync.adapters.fixture_source import FixtureSource
from garmin_sheets_sync.adapters.garmin_source import GarminConnectSource
from garmin_sheets_sync.adapters.google_sheets_destination import (
    GoogleSheetsDestination,
)
from garmin_sheets_sync.adapters.sqlite_destination import SqliteDestination
from garmin_sheets_sync.errors import ConfigurationError
from garmin_sheets_sync.locking import RunLock
from garmin_sheets_sync.models import DateWindow
from garmin_sheets_sync.ports import AlertSink, Destination, FailureContext, Source
from garmin_sheets_sync.service import SyncService

logger = logging.getLogger(__name__)


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected an integer greater than zero")
    return parsed


def _boolean(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"invalid boolean value {value!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-sheets-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync", help="fetch and upsert one date window")
    sync.add_argument(
        "--source",
        choices=("fixture", "garmin"),
        default=_env("SYNC_SOURCE", "fixture"),
    )
    sync.add_argument(
        "--destination",
        choices=("sqlite", "google"),
        default=_env("SYNC_DESTINATION", "sqlite"),
    )
    sync.add_argument("--start", type=_iso_date, help="inclusive start date")
    sync.add_argument("--end", type=_iso_date, help="inclusive end date")
    sync.add_argument(
        "--lookback-days",
        type=_positive_int,
        default=_positive_int(_env("SYNC_LOOKBACK_DAYS", "3") or "3"),
        help="rolling window used when --start is omitted (default: 3)",
    )
    sync.add_argument(
        "--fixture",
        type=Path,
        default=Path(_env("FIXTURE_PATH", "fixtures/sample.json") or "fixtures/sample.json"),
    )
    sync.add_argument(
        "--database",
        type=Path,
        default=Path(_env("SQLITE_PATH", ".local/garmin-sync.db") or ".local/garmin-sync.db"),
    )
    sync.add_argument(
        "--lock-file",
        type=Path,
        default=Path(_env("SYNC_LOCK_FILE", ".local/sync.lock") or ".local/sync.lock"),
    )
    sync.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=_env("LOG_LEVEL", "INFO"),
    )
    return parser


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise ConfigurationError(f"required environment variable {name} is not set")
    return value


def _build_source(args: argparse.Namespace) -> Source:
    if args.source == "fixture":
        return FixtureSource(args.fixture)
    return GarminConnectSource.login(
        _require_env("GARMIN_EMAIL"),
        _require_env("GARMIN_PASSWORD"),
        Path(_env("GARMIN_TOKEN_DIR", "~/.garminconnect") or "~/.garminconnect"),
        attempts=_positive_int(_env("GARMIN_RETRY_ATTEMPTS", "3") or "3"),
        base_delay_seconds=float(_env("GARMIN_RETRY_BASE_SECONDS", "10") or "10"),
        max_delay_seconds=float(_env("GARMIN_RETRY_MAX_SECONDS", "60") or "60"),
        activity_url_template=_env("GARMIN_ACTIVITY_URL_TEMPLATE") or None,
    )


def _build_destination(args: argparse.Namespace) -> Destination:
    if args.destination == "sqlite":
        return SqliteDestination(args.database)
    return GoogleSheetsDestination.from_service_account(
        Path(_require_env("GOOGLE_SERVICE_ACCOUNT_FILE")),
        _require_env("GOOGLE_SHEET_ID"),
        settings_tab=_env("GOOGLE_SETTINGS_TAB", "Settings") or "Settings",
        last_success_cell=_env("GOOGLE_LAST_SUCCESS_CELL", "B2") or "B2",
    )


def _build_alert_sink() -> AlertSink:
    mode = (_env("ALERT_MODE", "log") or "log").lower()
    if mode in {"log", "platform"}:
        return LogAlertSink()
    if mode != "smtp":
        raise ConfigurationError("ALERT_MODE must be 'log', 'platform', or 'smtp'")
    return SmtpAlertSink(
        SmtpSettings(
            host=_require_env("SMTP_HOST"),
            port=int(_env("SMTP_PORT", "587") or "587"),
            starttls=_boolean(_env("SMTP_STARTTLS"), default=True),
            username=_env("SMTP_USERNAME") or None,
            password=_env("SMTP_PASSWORD") or None,
            sender=_require_env("ALERT_EMAIL_FROM"),
            recipient=_require_env("ALERT_EMAIL_TO"),
        )
    )


def _window(args: argparse.Namespace) -> DateWindow:
    end = args.end or date.today()
    start = args.start or (end - timedelta(days=args.lookback_days - 1))
    return DateWindow(start=start, end=end)


def _validate_alert_mode(args: argparse.Namespace) -> None:
    mode = (_env("ALERT_MODE", "log") or "log").lower()
    is_live_run = args.source == "garmin" or args.destination == "google"
    if is_live_run and mode == "log":
        raise ConfigurationError(
            "live runs require ALERT_MODE=smtp or explicit ALERT_MODE=platform"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    alert_sink: AlertSink = LogAlertSink()
    window: DateWindow | None = None
    try:
        window = _window(args)
        alert_sink = _build_alert_sink()
        _validate_alert_mode(args)
        with RunLock(args.lock_file):
            source = _build_source(args)
            destination = _build_destination(args)
            SyncService(source, destination).run(window)
        return 0
    except Exception as exc:
        failure_window = window or DateWindow(start=date.today(), end=date.today())
        context = FailureContext(
            source=args.source,
            destination=args.destination,
            window=failure_window,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        try:
            alert_sink.notify_failure(context)
        except Exception:
            logger.exception("failure_alert_delivery_failed")
        return 1
