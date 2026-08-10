"""Garmin Connect source adapter with strict response mapping and bounded retries."""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from garmin_sheets_sync.errors import ConfigurationError, SchemaError
from garmin_sheets_sync.models import (
    DASHBOARD_ACTIVITY_URL,
    Activity,
    DailyActivity,
    DateWindow,
    IngestionBatch,
    WeightMeasurement,
    format_timestamp,
    parse_timestamp,
)

logger = logging.getLogger(__name__)


def _required(mapping: dict[str, Any], key: str, record: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise SchemaError(f"Garmin {record} is missing required field {key!r}")
    return mapping[key]


def _number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"Garmin field {field!r} must be a number")
    return float(value)


def _grams_to_kg(value: Any, field: str, *, optional: bool = False) -> float | None:
    number = _number(value, field, optional=optional)
    return None if number is None else number / 1000


def parse_body_composition(payload: Any) -> tuple[WeightMeasurement, ...]:
    if not isinstance(payload, dict):
        raise SchemaError("Garmin body composition response must be an object")
    raw_records = payload.get("dateWeightList")
    if not isinstance(raw_records, list):
        raise SchemaError("Garmin body composition response is missing 'dateWeightList'")

    records: list[WeightMeasurement] = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise SchemaError(f"Garmin dateWeightList[{index}] must be an object")
        timestamp = parse_timestamp(
            _required(raw, "timestampGMT", "weight measurement"), "timestampGMT"
        )
        weight = _grams_to_kg(
            _required(raw, "weight", "weight measurement"), "weight"
        )
        assert weight is not None
        if weight <= 0:
            raise SchemaError("Garmin weight must be greater than zero")
        records.append(
            WeightMeasurement(
                measured_at=timestamp,
                weight_kg=weight,
                body_fat_percent=_number(raw.get("bodyFat"), "bodyFat", optional=True),
                skeletal_muscle_mass_kg=_grams_to_kg(
                    raw.get("muscleMass"), "muscleMass", optional=True
                ),
                bone_mass_kg=_grams_to_kg(
                    raw.get("boneMass"), "boneMass", optional=True
                ),
                body_water_percent=_number(
                    raw.get("bodyWater"), "bodyWater", optional=True
                ),
                bmi=_number(raw.get("bmi"), "bmi", optional=True),
            )
        )
    return tuple(records)


def parse_daily_summary(payload: Any) -> DailyActivity:
    if not isinstance(payload, dict):
        raise SchemaError("Garmin daily summary response must be an object")
    return DailyActivity.from_fixture(
        {
            "date": _required(payload, "calendarDate", "daily summary"),
            "steps": _required(payload, "totalSteps", "daily summary"),
            "active_calories": _required(
                payload, "activeKilocalories", "daily summary"
            ),
        }
    )


def parse_activities(
    payload: Any, activity_url_template: str | None = None
) -> tuple[Activity, ...]:
    if not isinstance(payload, list):
        raise SchemaError("Garmin activities response must be a list")
    records: list[Activity] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise SchemaError(f"Garmin activities[{index}] must be an object")
        raw_type = _required(raw, "activityType", "activity")
        if not isinstance(raw_type, dict):
            raise SchemaError("Garmin activityType must be an object")
        activity_id = str(_required(raw, "activityId", "activity"))
        started_at = parse_timestamp(
            _required(raw, "startTimeGMT", "activity"), "startTimeGMT"
        )
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        connect_url = (
            activity_url_template.format(activity_id=activity_id)
            if activity_url_template
            else DASHBOARD_ACTIVITY_URL
        )
        records.append(
            Activity.from_fixture(
                {
                    "activity_id": activity_id,
                    "name": _required(raw, "activityName", "activity"),
                    "type": _required(raw_type, "typeKey", "activity type"),
                    "started_at": format_timestamp(started_at),
                    "duration_seconds": _required(raw, "duration", "activity"),
                    "distance_meters": raw.get("distance"),
                    "calories_kcal": raw.get("calories"),
                    "average_heart_rate_bpm": raw.get("averageHR"),
                    "max_heart_rate_bpm": raw.get("maxHR"),
                    "connect_url": connect_url,
                }
            )
        )
    return tuple(records)


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _restore_token(token_file: Path, original: bytes | None) -> None:
    if original is None:
        token_file.unlink(missing_ok=True)
        return
    token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=token_file.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(token_file)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


class GarminConnectSource:
    name = "garmin"

    def __init__(
        self,
        client: Any,
        *,
        retryable_exceptions: tuple[type[BaseException], ...],
        non_retryable_exceptions: tuple[type[BaseException], ...] = (),
        attempts: int = 3,
        base_delay_seconds: float = 10,
        max_delay_seconds: float = 60,
        activity_url_template: str | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if attempts < 1:
            raise ConfigurationError("Garmin retry attempts must be at least 1")
        if activity_url_template and "{activity_id}" not in activity_url_template:
            raise ConfigurationError(
                "GARMIN_ACTIVITY_URL_TEMPLATE must contain '{activity_id}'"
            )
        self._client = client
        self._retryable_exceptions = retryable_exceptions
        self._non_retryable_exceptions = non_retryable_exceptions
        self._attempts = attempts
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._activity_url_template = activity_url_template
        self._sleeper = sleeper
        self._jitter = jitter

    @classmethod
    def login(
        cls,
        email: str,
        password: str,
        token_dir: Path,
        **kwargs: Any,
    ) -> GarminConnectSource:
        try:
            from garminconnect import (
                Garmin,
                GarminConnectAuthenticationError,
                GarminConnectConnectionError,
                GarminConnectNotFoundError,
                GarminConnectTooManyRequestsError,
            )
        except ImportError as exc:
            raise ConfigurationError(
                "live Garmin support is not installed; install the 'live' extra"
            ) from exc

        token_dir = token_dir.expanduser()
        token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_dir.chmod(0o700)
        token_file = token_dir / "garmin_tokens.json"
        original = token_file.read_bytes() if token_file.exists() else None
        # Supplying credentials with cached tokens lets the library silently perform
        # a full login when tokens are rejected. Token-only construction makes that
        # condition fail so the previous file can be restored and an alert sent.
        client = (
            Garmin(None, None, retry_attempts=0)
            if original is not None
            else Garmin(email, password, retry_attempts=0)
        )
        source = cls(
            client,
            retryable_exceptions=(
                GarminConnectTooManyRequestsError,
                GarminConnectConnectionError,
            ),
            non_retryable_exceptions=(
                GarminConnectAuthenticationError,
                GarminConnectNotFoundError,
            ),
            **kwargs,
        )
        try:
            login_result = source._call("login", client.login, str(token_dir))
            if login_result[0] is not None:
                raise ConfigurationError(
                    "Garmin MFA is enabled; unattended login is not configured"
                )
        except Exception:
            _restore_token(token_file, original)
            raise
        if token_file.exists():
            token_file.chmod(0o600)
        return source

    def fetch(self, window: DateWindow) -> IngestionBatch:
        body = self._call(
            "body_composition",
            self._client.get_body_composition,
            window.start.isoformat(),
            window.end.isoformat(),
        )
        daily: list[DailyActivity] = []
        current = window.start
        while current <= window.end:
            raw = self._call(
                f"daily_summary:{current.isoformat()}",
                self._client.get_user_summary,
                current.isoformat(),
            )
            daily.append(parse_daily_summary(raw))
            current += timedelta(days=1)
        activities = self._call(
            "activities",
            self._client.get_activities_by_date,
            window.start.isoformat(),
            window.end.isoformat(),
            sortorder="asc",
        )
        return IngestionBatch(
            weights=parse_body_composition(body),
            daily_activity=tuple(daily),
            activities=parse_activities(activities, self._activity_url_template),
        )

    def _call(self, operation: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        for attempt in range(1, self._attempts + 1):
            try:
                return function(*args, **kwargs)
            except self._non_retryable_exceptions:
                raise
            except self._retryable_exceptions as exc:
                status = _status_code(exc)
                if status is not None and status != 429 and status < 500:
                    raise
                if attempt == self._attempts:
                    raise
                exponential = min(
                    self._max_delay, self._base_delay * (2 ** (attempt - 1))
                )
                delay = exponential * (1 + (self._jitter() * 0.25))
                logger.warning(
                    "garmin_retry operation=%s attempt=%d max_attempts=%d "
                    "delay_seconds=%.1f status=%s",
                    operation,
                    attempt,
                    self._attempts,
                    delay,
                    status,
                )
                self._sleeper(delay)
        raise RuntimeError("Garmin retry loop exited unexpectedly")
