"""Google Sheets destination that updates only service-owned cells."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from garmin_sheets_sync.errors import ConfigurationError, SchemaError
from garmin_sheets_sync.models import IngestionBatch, format_timestamp
from garmin_sheets_sync.ports import SyncReport, UpsertCounts

WEIGHT_HEADERS = (
    "Measurement Timestamp",
    "Weight (kg)",
    "Body Fat (%)",
    "Skeletal Muscle Mass (kg)",
    "Bone Mass (kg)",
    "Body Water (%)",
    "BMI",
    "Source",
)
DAILY_HEADERS = ("Date", "Steps", "Active Calories")
ACTIVITY_HEADERS = (
    "Activity ID",
    "Activity Name",
    "Activity Type",
    "Start Time",
    "Duration (seconds)",
    "Distance (meters)",
    "Calories (kcal)",
    "Average Heart Rate (bpm)",
    "Max Heart Rate (bpm)",
    "Garmin Connect Link",
)


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _equivalent(existing: Any, expected: Any) -> bool:
    if expected is None:
        return existing in (None, "")
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        try:
            return float(existing) == float(expected)
        except (TypeError, ValueError):
            return False
    return str(existing) == str(expected)


class GoogleSheetsDestination:
    name = "google"

    def __init__(
        self,
        spreadsheet: Any,
        *,
        settings_tab: str = "Settings",
        last_success_cell: str = "B2",
    ) -> None:
        self._spreadsheet = spreadsheet
        self._settings_tab = settings_tab
        self._last_success_cell = last_success_cell

    @classmethod
    def from_service_account(
        cls,
        credentials_file: Path,
        sheet_id: str,
        **kwargs: Any,
    ) -> GoogleSheetsDestination:
        try:
            import gspread
        except ImportError as exc:
            raise ConfigurationError(
                "Google Sheets support is not installed; install the 'live' extra"
            ) from exc
        if not credentials_file.is_file():
            raise ConfigurationError(
                f"Google service account file does not exist: {credentials_file}"
            )
        client = gspread.service_account(filename=str(credentials_file))
        return cls(client.open_by_key(sheet_id), **kwargs)

    def sync(self, batch: IngestionBatch, completed_at: datetime) -> SyncReport:
        weights = self._upsert_tab(
            "Weight Log",
            WEIGHT_HEADERS,
            "Measurement Timestamp",
            [
                {
                    "Measurement Timestamp": item.key,
                    "Weight (kg)": item.weight_kg,
                    "Body Fat (%)": item.body_fat_percent,
                    "Skeletal Muscle Mass (kg)": item.skeletal_muscle_mass_kg,
                    "Bone Mass (kg)": item.bone_mass_kg,
                    "Body Water (%)": item.body_water_percent,
                    "BMI": item.bmi,
                    "Source": item.source,
                }
                for item in batch.weights
            ],
            protect_manual_source=True,
        )
        daily = self._upsert_tab(
            "Garmin Daily Activity",
            DAILY_HEADERS,
            "Date",
            [
                {
                    "Date": item.key,
                    "Steps": item.steps,
                    "Active Calories": item.active_calories,
                }
                for item in batch.daily_activity
            ],
        )
        activities = self._upsert_tab(
            "Garmin Activities",
            ACTIVITY_HEADERS,
            "Activity ID",
            [
                {
                    "Activity ID": item.key,
                    "Activity Name": item.name,
                    "Activity Type": item.activity_type,
                    "Start Time": format_timestamp(item.started_at),
                    "Duration (seconds)": item.duration_seconds,
                    "Distance (meters)": item.distance_meters,
                    "Calories (kcal)": item.calories_kcal,
                    "Average Heart Rate (bpm)": item.average_heart_rate_bpm,
                    "Max Heart Rate (bpm)": item.max_heart_rate_bpm,
                    "Garmin Connect Link": item.connect_url,
                }
                for item in batch.activities
            ],
        )
        settings = self._worksheet(self._settings_tab)
        settings.update(
            [[format_timestamp(completed_at)]],
            self._last_success_cell,
            value_input_option="RAW",
        )
        return SyncReport(
            weights=weights,
            daily_activity=daily,
            activities=activities,
            completed_at=completed_at,
        )

    def _worksheet(self, title: str) -> Any:
        try:
            return self._spreadsheet.worksheet(title)
        except Exception as exc:
            raise SchemaError(f"required Google Sheet tab {title!r} is unavailable") from exc

    def _upsert_tab(
        self,
        title: str,
        required_headers: tuple[str, ...],
        key_header: str,
        records: list[dict[str, Any]],
        *,
        protect_manual_source: bool = False,
    ) -> UpsertCounts:
        worksheet = self._worksheet(title)
        values = worksheet.get_all_values()
        if not values:
            raise SchemaError(f"Google Sheet tab {title!r} has no header row")
        header_row = values[0]
        populated_headers = [header for header in header_row if header]
        if len(populated_headers) != len(set(populated_headers)):
            raise SchemaError(f"Google Sheet tab {title!r} has duplicate headers")
        header_columns = {
            header: index + 1 for index, header in enumerate(header_row) if header
        }
        missing = [header for header in required_headers if header not in header_columns]
        if missing:
            raise SchemaError(
                f"Google Sheet tab {title!r} is missing headers: {', '.join(missing)}"
            )

        key_column = header_columns[key_header]
        existing_by_key: dict[str, tuple[int, list[Any]]] = {}
        for row_number, row in enumerate(values[1:], start=2):
            key = str(row[key_column - 1]).strip() if len(row) >= key_column else ""
            if not key:
                continue
            if key in existing_by_key:
                raise SchemaError(
                    f"Google Sheet tab {title!r} contains duplicate key {key!r}"
                )
            existing_by_key[key] = (row_number, row)

        changes: list[dict[str, Any]] = []
        counts = UpsertCounts()
        next_row = max(2, len(values) + 1)
        for record in records:
            key = str(record[key_header])
            existing = existing_by_key.get(key)
            if existing is None:
                row_number = next_row
                next_row += 1
                current_row: list[Any] = []
                counts = counts + UpsertCounts(inserted=1)
            else:
                row_number, current_row = existing
                if protect_manual_source:
                    source_column = header_columns["Source"]
                    existing_source = (
                        current_row[source_column - 1]
                        if len(current_row) >= source_column
                        else ""
                    )
                    if existing_source != "Garmin":
                        raise SchemaError(
                            f"refusing to overwrite non-Garmin Weight Log row for {key!r}"
                        )
                changed = any(
                    not _equivalent(
                        current_row[header_columns[header] - 1]
                        if len(current_row) >= header_columns[header]
                        else "",
                        record[header],
                    )
                    for header in required_headers
                )
                counts = counts + (
                    UpsertCounts(updated=1) if changed else UpsertCounts(unchanged=1)
                )
                if not changed:
                    continue

            for header in required_headers:
                column = _column_name(header_columns[header])
                changes.append(
                    {
                        "range": f"{column}{row_number}",
                        "values": [[record[header] if record[header] is not None else ""]],
                    }
                )

        if changes:
            worksheet.batch_update(changes, value_input_option="RAW")
        return counts
