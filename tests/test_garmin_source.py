import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from garmin_sheets_sync.adapters.garmin_source import (
    GarminConnectSource,
    parse_activities,
    parse_body_composition,
    parse_daily_summary,
)
from garmin_sheets_sync.errors import ConfigurationError, SchemaError
from garmin_sheets_sync.models import DateWindow


def test_parse_body_composition_converts_grams_and_keeps_missing_secondary_fields() -> None:
    records = parse_body_composition(
        {
            "dateWeightList": [
                {
                    "samplePk": 1720435190064,
                    "calendarDate": "2024-07-08",
                    "weight": 82372.0,
                    "bmi": None,
                    "bodyFat": None,
                    "bodyWater": None,
                    "boneMass": None,
                    "muscleMass": None,
                    "timestampGMT": 1720435137000,
                },
                {
                    "weight": 82000,
                    "bmi": 24.2,
                    "bodyFat": 18.1,
                    "bodyWater": 59.2,
                    "boneMass": 3600,
                    "muscleMass": 33800,
                    "timestampGMT": 1720470000000,
                },
            ]
        }
    )

    assert records[0].weight_kg == pytest.approx(82.372)
    assert records[0].body_fat_percent is None
    assert records[1].skeletal_muscle_mass_kg == pytest.approx(33.8)
    assert records[1].bone_mass_kg == pytest.approx(3.6)


def test_parse_body_composition_fails_loudly_on_missing_timestamp() -> None:
    with pytest.raises(SchemaError, match="timestampGMT"):
        parse_body_composition({"dateWeightList": [{"weight": 82000}]})


def test_parse_daily_summary_retains_zero_values() -> None:
    record = parse_daily_summary(
        {"calendarDate": "2026-08-09", "totalSteps": 0, "activeKilocalories": 0}
    )

    assert record.steps == 0
    assert record.active_calories == 0


def test_parse_multiple_activities_and_optional_distance() -> None:
    records = parse_activities(
        [
            {
                "activityId": 19876543210,
                "activityName": "Morning Run",
                "startTimeLocal": "2026-04-21 06:30:00",
                "startTimeGMT": "2026-04-21 04:30:00",
                "activityType": {"typeId": 1, "typeKey": "running"},
                "duration": 2400.0,
                "distance": 6200.0,
                "calories": 512.0,
                "averageHR": 148.0,
                "maxHR": 172.0,
            },
            {
                "activityId": 19876543211,
                "activityName": "Strength",
                "startTimeLocal": "2026-04-21 18:00:00",
                "startTimeGMT": "2026-04-21 16:00:00",
                "activityType": {"typeKey": "strength_training"},
                "duration": 3600.0,
            },
        ]
    )

    assert [record.activity_id for record in records] == ["19876543210", "19876543211"]
    assert records[1].distance_meters is None
    assert records[1].calories_kcal is None
    assert records[1].average_heart_rate_bpm is None
    assert records[1].max_heart_rate_bpm is None
    assert records[0].calories_kcal == 512
    assert records[0].average_heart_rate_bpm == 148
    assert records[0].max_heart_rate_bpm == 172
    assert records[0].connect_url.endswith("/modern/activities")
    assert records[0].started_at.isoformat() == "2026-04-21T04:30:00+00:00"


class TransientError(Exception):
    pass


class FakeClient:
    def __init__(self) -> None:
        self.body_calls = 0
        self.activity_calls = 0

    def get_body_composition(self, start: str, end: str) -> dict[str, Any]:
        self.body_calls += 1
        if self.body_calls == 1:
            raise TransientError("429")
        return {"dateWeightList": []}

    def get_user_summary(self, current: str) -> dict[str, Any]:
        return {"calendarDate": current, "totalSteps": 10, "activeKilocalories": 5}

    def get_activities_by_date(
        self, start: str, end: str, *, sortorder: str
    ) -> list[dict[str, Any]]:
        self.activity_calls += 1
        return []


def test_source_retries_transient_failure_with_bounded_backoff() -> None:
    client = FakeClient()
    delays: list[float] = []
    source = GarminConnectSource(
        client,
        retryable_exceptions=(TransientError,),
        attempts=3,
        base_delay_seconds=1,
        sleeper=delays.append,
        jitter=lambda: 0,
    )

    batch = source.fetch(DateWindow(date(2026, 8, 9), date(2026, 8, 9)))

    assert len(batch.daily_activity) == 1
    assert client.body_calls == 2
    assert client.activity_calls == 1
    assert delays == [1]


def test_activity_url_template_requires_placeholder() -> None:
    with pytest.raises(ConfigurationError, match="activity_id"):
        GarminConnectSource(
            FakeClient(),
            retryable_exceptions=(TransientError,),
            activity_url_template="https://example.test/activity",
        )


def test_failed_login_restores_last_known_good_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeConnectionError(Exception):
        pass

    credentials: list[tuple[str | None, str | None]] = []

    class FakeGarmin:
        def __init__(
            self, email: str | None, password: str | None, *, retry_attempts: int
        ) -> None:
            credentials.append((email, password))

        def login(self, tokenstore: str) -> None:
            (Path(tokenstore) / "garmin_tokens.json").write_bytes(b"partial replacement")
            raise FakeConnectionError("login failed")

    fake_module = ModuleType("garminconnect")
    fake_module.Garmin = FakeGarmin  # type: ignore[attr-defined]
    fake_module.GarminConnectAuthenticationError = type(  # type: ignore[attr-defined]
        "FakeAuthenticationError", (Exception,), {}
    )
    fake_module.GarminConnectConnectionError = FakeConnectionError  # type: ignore[attr-defined]
    fake_module.GarminConnectNotFoundError = type(  # type: ignore[attr-defined]
        "FakeNotFoundError", (FakeConnectionError,), {}
    )
    fake_module.GarminConnectTooManyRequestsError = type(  # type: ignore[attr-defined]
        "FakeTooManyRequestsError", (Exception,), {}
    )
    monkeypatch.setitem(sys.modules, "garminconnect", fake_module)
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    token_file = token_dir / "garmin_tokens.json"
    token_file.write_bytes(b"last known good")

    with pytest.raises(FakeConnectionError):
        GarminConnectSource.login(
            "user@example.test", "secret", token_dir, attempts=1
        )

    assert token_file.read_bytes() == b"last known good"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert credentials == [(None, None)]
