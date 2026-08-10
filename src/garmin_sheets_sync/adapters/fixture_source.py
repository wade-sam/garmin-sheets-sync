"""Stable normalized fixture input for offline development."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from garmin_sheets_sync.errors import SchemaError
from garmin_sheets_sync.models import (
    Activity,
    DailyActivity,
    DateWindow,
    IngestionBatch,
    WeightMeasurement,
)


class FixtureSource:
    name = "fixture"

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch(self, window: DateWindow) -> IngestionBatch:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaError(f"could not read fixture {self._path}: {exc}") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise SchemaError("fixture schema_version must be 1")

        weights = tuple(
            item
            for item in self._parse_list(document, "weights", WeightMeasurement.from_fixture)
            if window.start <= item.measured_at.date() <= window.end
        )
        daily = tuple(
            item
            for item in self._parse_list(
                document, "daily_activity", DailyActivity.from_fixture
            )
            if window.start <= item.date <= window.end
        )
        activities = tuple(
            item
            for item in self._parse_list(document, "activities", Activity.from_fixture)
            if window.start <= item.started_at.date() <= window.end
        )
        return IngestionBatch(weights=weights, daily_activity=daily, activities=activities)

    @staticmethod
    def _parse_list(document: dict[str, Any], key: str, parser: Any) -> list[Any]:
        values = document.get(key)
        if not isinstance(values, list):
            raise SchemaError(f"fixture field {key!r} must be a list")
        parsed: list[Any] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise SchemaError(f"fixture {key}[{index}] must be an object")
            try:
                parsed.append(parser(value))
            except SchemaError as exc:
                raise SchemaError(f"fixture {key}[{index}]: {exc}") from exc
        return parsed
