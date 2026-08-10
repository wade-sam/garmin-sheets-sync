"""Canonical records owned by the ingestion service."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Self

from garmin_sheets_sync.errors import SchemaError

DASHBOARD_ACTIVITY_URL = "https://connect.garmin.com/modern/activities"


def _required(mapping: dict[str, Any], key: str, record: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise SchemaError(f"{record} is missing required field {key!r}")
    return mapping[key]


def _number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise SchemaError(f"{field} must be finite")
    return number


def parse_timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, bool):
        raise SchemaError(f"{field} must be an RFC3339 string or epoch timestamp")
    if isinstance(value, int | float):
        seconds = float(value) / 1000 if abs(float(value)) >= 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise SchemaError(f"{field} contains an invalid epoch timestamp") from exc
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be an RFC3339 string or epoch timestamp")
    normalized = value.strip().replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaError(f"{field} contains an invalid timestamp") from exc


def format_timestamp(value: datetime) -> str:
    formatted = value.isoformat(timespec="seconds")
    return formatted.replace("+00:00", "Z")


def timestamp_key(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return format_timestamp(value)


@dataclass(frozen=True, slots=True)
class WeightMeasurement:
    measured_at: datetime
    weight_kg: float
    body_fat_percent: float | None = None
    skeletal_muscle_mass_kg: float | None = None
    bone_mass_kg: float | None = None
    body_water_percent: float | None = None
    bmi: float | None = None
    source: str = "Garmin"

    @property
    def key(self) -> str:
        return timestamp_key(self.measured_at)

    @classmethod
    def from_fixture(cls, value: dict[str, Any]) -> Self:
        return cls(
            measured_at=parse_timestamp(
                _required(value, "measured_at", "weight measurement"), "measured_at"
            ),
            weight_kg=_positive_number(
                _number(_required(value, "weight_kg", "weight measurement"), "weight_kg"),
                "weight_kg",
            ),
            body_fat_percent=_number(
                value.get("body_fat_percent"), "body_fat_percent", optional=True
            ),
            skeletal_muscle_mass_kg=_number(
                value.get("skeletal_muscle_mass_kg"),
                "skeletal_muscle_mass_kg",
                optional=True,
            ),
            bone_mass_kg=_number(value.get("bone_mass_kg"), "bone_mass_kg", optional=True),
            body_water_percent=_number(
                value.get("body_water_percent"), "body_water_percent", optional=True
            ),
            bmi=_number(value.get("bmi"), "bmi", optional=True),
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["measured_at"] = format_timestamp(self.measured_at)
        return result


def _positive_number(value: float | None, field: str) -> float:
    assert value is not None
    if value <= 0:
        raise SchemaError(f"{field} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class DailyActivity:
    date: date
    steps: int
    active_calories: float

    @property
    def key(self) -> str:
        return self.date.isoformat()

    @classmethod
    def from_fixture(cls, value: dict[str, Any]) -> Self:
        raw_date = _required(value, "date", "daily activity")
        if not isinstance(raw_date, str):
            raise SchemaError("daily activity date must be an ISO date")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise SchemaError("daily activity date must be an ISO date") from exc
        raw_steps = _required(value, "steps", "daily activity")
        if isinstance(raw_steps, bool) or not isinstance(raw_steps, int) or raw_steps < 0:
            raise SchemaError("steps must be a non-negative integer")
        active_calories = _number(
            _required(value, "active_calories", "daily activity"), "active_calories"
        )
        assert active_calories is not None
        if active_calories < 0:
            raise SchemaError("active_calories must be non-negative")
        return cls(date=parsed_date, steps=raw_steps, active_calories=active_calories)

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "steps": self.steps,
            "active_calories": self.active_calories,
        }


@dataclass(frozen=True, slots=True)
class Activity:
    activity_id: str
    name: str
    activity_type: str
    started_at: datetime
    duration_seconds: float
    distance_meters: float | None
    connect_url: str = DASHBOARD_ACTIVITY_URL
    calories_kcal: float | None = None
    average_heart_rate_bpm: float | None = None
    max_heart_rate_bpm: float | None = None

    @property
    def key(self) -> str:
        return self.activity_id

    @classmethod
    def from_fixture(cls, value: dict[str, Any]) -> Self:
        raw_id = _required(value, "activity_id", "activity")
        if isinstance(raw_id, bool) or not isinstance(raw_id, str | int) or not str(raw_id).strip():
            raise SchemaError("activity_id must be a non-empty string or integer")
        name = _required(value, "name", "activity")
        activity_type = _required(value, "type", "activity")
        if not isinstance(name, str) or not name:
            raise SchemaError("activity name must be a non-empty string")
        if not isinstance(activity_type, str) or not activity_type:
            raise SchemaError("activity type must be a non-empty string")
        duration = _number(
            _required(value, "duration_seconds", "activity"), "duration_seconds"
        )
        assert duration is not None
        if duration < 0:
            raise SchemaError("duration_seconds must be non-negative")
        distance = _number(value.get("distance_meters"), "distance_meters", optional=True)
        if distance is not None and distance < 0:
            raise SchemaError("distance_meters must be non-negative")
        calories = _number(value.get("calories_kcal"), "calories_kcal", optional=True)
        if calories is not None and calories < 0:
            raise SchemaError("calories_kcal must be non-negative")
        average_heart_rate = _number(
            value.get("average_heart_rate_bpm"),
            "average_heart_rate_bpm",
            optional=True,
        )
        max_heart_rate = _number(
            value.get("max_heart_rate_bpm"), "max_heart_rate_bpm", optional=True
        )
        if average_heart_rate is not None and average_heart_rate < 0:
            raise SchemaError("average_heart_rate_bpm must be non-negative")
        if max_heart_rate is not None and max_heart_rate < 0:
            raise SchemaError("max_heart_rate_bpm must be non-negative")
        raw_url = value.get("connect_url")
        if raw_url is not None and not isinstance(raw_url, str):
            raise SchemaError("connect_url must be a string or null")
        return cls(
            activity_id=str(raw_id),
            name=name,
            activity_type=activity_type,
            started_at=parse_timestamp(
                _required(value, "started_at", "activity"), "started_at"
            ),
            duration_seconds=duration,
            distance_meters=distance,
            connect_url=raw_url or DASHBOARD_ACTIVITY_URL,
            calories_kcal=calories,
            average_heart_rate_bpm=average_heart_rate,
            max_heart_rate_bpm=max_heart_rate,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "name": self.name,
            "type": self.activity_type,
            "started_at": format_timestamp(self.started_at),
            "duration_seconds": self.duration_seconds,
            "distance_meters": self.distance_meters,
            "calories_kcal": self.calories_kcal,
            "average_heart_rate_bpm": self.average_heart_rate_bpm,
            "max_heart_rate_bpm": self.max_heart_rate_bpm,
            "connect_url": self.connect_url,
        }


@dataclass(frozen=True, slots=True)
class DateWindow:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise SchemaError("start date must not be after end date")


@dataclass(frozen=True, slots=True)
class IngestionBatch:
    weights: tuple[WeightMeasurement, ...]
    daily_activity: tuple[DailyActivity, ...]
    activities: tuple[Activity, ...]

    def __post_init__(self) -> None:
        for label, records in (
            ("weight measurements", self.weights),
            ("daily activity summaries", self.daily_activity),
            ("activities", self.activities),
        ):
            seen: set[str] = set()
            for record in records:
                if record.key in seen:
                    raise SchemaError(f"input contains duplicate {label} key {record.key!r}")
                seen.add(record.key)
