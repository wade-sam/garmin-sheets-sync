import re
from datetime import UTC, date, datetime
from typing import Any

import pytest

from garmin_sheets_sync.adapters.google_sheets_destination import (
    ACTIVITY_HEADERS,
    DAILY_HEADERS,
    WEIGHT_HEADERS,
    GoogleSheetsDestination,
)
from garmin_sheets_sync.errors import SchemaError
from garmin_sheets_sync.models import (
    Activity,
    DailyActivity,
    IngestionBatch,
    WeightMeasurement,
    parse_timestamp,
)


def _column_number(name: str) -> int:
    result = 0
    for character in name:
        result = (result * 26) + ord(character) - 64
    return result


class FakeWorksheet:
    def __init__(self, values: list[list[Any]]) -> None:
        self.values = values
        self.batch_calls: list[list[dict[str, Any]]] = []
        self.updates: list[tuple[list[list[Any]], str]] = []

    def get_all_values(self) -> list[list[Any]]:
        return self.values

    def batch_update(self, changes: list[dict[str, Any]], **kwargs: Any) -> None:
        self.batch_calls.append(changes)
        for change in changes:
            match = re.fullmatch(r"([A-Z]+)([0-9]+)", change["range"])
            assert match
            column = _column_number(match.group(1))
            row = int(match.group(2))
            while len(self.values) < row:
                self.values.append([])
            while len(self.values[row - 1]) < column:
                self.values[row - 1].append("")
            self.values[row - 1][column - 1] = change["values"][0][0]

    def update(self, values: list[list[Any]], cell: str, **kwargs: Any) -> None:
        self.updates.append((values, cell))


class FakeSpreadsheet:
    def __init__(self, tabs: dict[str, FakeWorksheet]) -> None:
        self.tabs = tabs

    def worksheet(self, title: str) -> FakeWorksheet:
        return self.tabs[title]


def _batch() -> IngestionBatch:
    return IngestionBatch(
        weights=(
            WeightMeasurement(
                measured_at=parse_timestamp("2026-08-09T06:00:00Z", "test"),
                weight_kg=82.4,
            ),
        ),
        daily_activity=(DailyActivity(date(2026, 8, 9), 1000, 123),),
        activities=(
            Activity(
                activity_id="12345678901234567",
                name="Run",
                activity_type="running",
                started_at=parse_timestamp("2026-08-09T07:00:00Z", "test"),
                duration_seconds=120,
                distance_meters=500,
                calories_kcal=42,
                average_heart_rate_bpm=135,
                max_heart_rate_bpm=162,
            ),
        ),
    )


def _destination() -> tuple[GoogleSheetsDestination, dict[str, FakeWorksheet]]:
    tabs = {
        "Weight Log": FakeWorksheet([list(WEIGHT_HEADERS)]),
        "Garmin Daily Activity": FakeWorksheet([list(DAILY_HEADERS)]),
        "Garmin Activities": FakeWorksheet([list(ACTIVITY_HEADERS)]),
        "Settings": FakeWorksheet([["Last Successful Sync", ""]]),
    }
    return GoogleSheetsDestination(FakeSpreadsheet(tabs)), tabs


def test_google_destination_targets_owned_cells_and_reruns_without_duplicates() -> None:
    destination, tabs = _destination()
    completed = datetime(2026, 8, 9, 20, tzinfo=UTC)

    first = destination.sync(_batch(), completed)
    second = destination.sync(_batch(), completed)

    assert first.total.inserted == 3
    assert second.total.unchanged == 3
    assert len(tabs["Weight Log"].values) == 2
    assert len(tabs["Garmin Daily Activity"].values) == 2
    assert len(tabs["Garmin Activities"].values) == 2
    activity_row = tabs["Garmin Activities"].values[1]
    assert activity_row[ACTIVITY_HEADERS.index("Calories (kcal)")] == 42
    assert activity_row[ACTIVITY_HEADERS.index("Average Heart Rate (bpm)")] == 135
    assert activity_row[ACTIVITY_HEADERS.index("Max Heart Rate (bpm)")] == 162
    assert all(
        re.fullmatch(r"[A-Z]+[0-9]+", change["range"])
        for tab in tabs.values()
        for call in tab.batch_calls
        for change in call
    )
    assert tabs["Settings"].updates[-1] == ([["2026-08-09T20:00:00Z"]], "B2")


def test_google_destination_refuses_to_replace_manual_weight() -> None:
    destination, tabs = _destination()
    tabs["Weight Log"].values.append(
        ["2026-08-09T06:00:00Z", 82.5, "", "", "", "", "", "manual"]
    )

    with pytest.raises(SchemaError, match="non-Garmin"):
        destination.sync(_batch(), datetime(2026, 8, 9, 20, tzinfo=UTC))


def test_google_destination_requires_activity_metric_headers() -> None:
    destination, tabs = _destination()
    tabs["Garmin Activities"].values[0] = [
        header
        for header in ACTIVITY_HEADERS
        if header
        not in {
            "Calories (kcal)",
            "Average Heart Rate (bpm)",
            "Max Heart Rate (bpm)",
        }
    ]

    with pytest.raises(SchemaError) as error:
        destination.sync(_batch(), datetime(2026, 8, 9, 20, tzinfo=UTC))

    assert "Calories (kcal)" in str(error.value)
    assert "Average Heart Rate (bpm)" in str(error.value)
    assert "Max Heart Rate (bpm)" in str(error.value)
    assert tabs["Garmin Activities"].batch_calls == []
    assert tabs["Settings"].updates == []
