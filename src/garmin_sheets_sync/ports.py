"""Protocols and result types at the application's adapter boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from garmin_sheets_sync.models import DateWindow, IngestionBatch


class Source(Protocol):
    name: str

    def fetch(self, window: DateWindow) -> IngestionBatch: ...


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def __add__(self, other: UpsertCounts) -> UpsertCounts:
        return UpsertCounts(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
        )


@dataclass(frozen=True, slots=True)
class SyncReport:
    weights: UpsertCounts
    daily_activity: UpsertCounts
    activities: UpsertCounts
    completed_at: datetime

    @property
    def total(self) -> UpsertCounts:
        return self.weights + self.daily_activity + self.activities


class Destination(Protocol):
    name: str

    def sync(self, batch: IngestionBatch, completed_at: datetime) -> SyncReport: ...


@dataclass(frozen=True, slots=True)
class FailureContext:
    source: str
    destination: str
    window: DateWindow
    error_type: str
    message: str


class AlertSink(Protocol):
    def notify_failure(self, context: FailureContext) -> None: ...
