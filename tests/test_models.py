from datetime import date

import pytest

from garmin_sheets_sync.errors import SchemaError
from garmin_sheets_sync.models import (
    Activity,
    DailyActivity,
    IngestionBatch,
    WeightMeasurement,
)


def test_equivalent_aware_timestamps_have_the_same_weight_key() -> None:
    first = WeightMeasurement.from_fixture(
        {"measured_at": "2026-08-08T07:00:00+02:00", "weight_kg": 82.4}
    )
    second = WeightMeasurement.from_fixture(
        {"measured_at": "2026-08-08T05:00:00Z", "weight_kg": 82.4}
    )

    assert first.key == second.key == "2026-08-08T05:00:00Z"


def test_weight_allows_missing_secondary_fields() -> None:
    measurement = WeightMeasurement.from_fixture(
        {"measured_at": "2026-08-08T07:00:00Z", "weight_kg": 82.4}
    )

    assert measurement.body_fat_percent is None
    assert measurement.skeletal_muscle_mass_kg is None
    assert measurement.bone_mass_kg is None
    assert measurement.body_water_percent is None
    assert measurement.bmi is None


@pytest.mark.parametrize("steps", [-1, 1.5, True])
def test_daily_activity_rejects_invalid_steps(steps: object) -> None:
    with pytest.raises(SchemaError, match="steps"):
        DailyActivity.from_fixture(
            {"date": "2026-08-08", "steps": steps, "active_calories": 10}
        )


def test_activity_uses_dashboard_when_direct_url_is_absent() -> None:
    activity = Activity.from_fixture(
        {
            "activity_id": "12345678901234567",
            "name": "Run",
            "type": "running",
            "started_at": "2026-08-09T06:30:00+02:00",
            "duration_seconds": 120,
            "distance_meters": None,
            "connect_url": None,
        }
    )

    assert activity.activity_id == "12345678901234567"
    assert activity.started_at.date() == date(2026, 8, 9)
    assert activity.connect_url == "https://connect.garmin.com/modern/activities"
    assert activity.calories_kcal is None
    assert activity.average_heart_rate_bpm is None
    assert activity.max_heart_rate_bpm is None


def test_activity_parses_calorie_and_heart_rate_summaries() -> None:
    activity = Activity.from_fixture(
        {
            "activity_id": "123",
            "name": "Hike",
            "type": "hiking",
            "started_at": "2026-08-09T12:11:13Z",
            "duration_seconds": 8012.5,
            "distance_meters": 8625.1,
            "calories_kcal": 980,
            "average_heart_rate_bpm": 126,
            "max_heart_rate_bpm": 164,
        }
    )

    assert activity.calories_kcal == 980
    assert activity.average_heart_rate_bpm == 126
    assert activity.max_heart_rate_bpm == 164


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("calories_kcal", -1),
        ("average_heart_rate_bpm", -1),
        ("max_heart_rate_bpm", -1),
        ("calories_kcal", float("nan")),
        ("average_heart_rate_bpm", float("inf")),
        ("max_heart_rate_bpm", "160"),
    ),
)
def test_activity_rejects_invalid_summary_metrics(field: str, invalid: object) -> None:
    value = {
        "activity_id": "123",
        "name": "Hike",
        "type": "hiking",
        "started_at": "2026-08-09T12:11:13Z",
        "duration_seconds": 100,
        "distance_meters": 500,
        field: invalid,
    }

    with pytest.raises(SchemaError, match=field):
        Activity.from_fixture(value)


def test_batch_rejects_duplicate_canonical_keys() -> None:
    first = WeightMeasurement.from_fixture(
        {"measured_at": "2026-08-08T07:00:00+02:00", "weight_kg": 82.4}
    )
    duplicate = WeightMeasurement.from_fixture(
        {"measured_at": "2026-08-08T05:00:00Z", "weight_kg": 82.5}
    )

    with pytest.raises(SchemaError, match="duplicate weight measurements"):
        IngestionBatch(
            weights=(first, duplicate), daily_activity=(), activities=()
        )
