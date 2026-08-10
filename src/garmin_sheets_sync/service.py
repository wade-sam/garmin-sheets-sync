"""One-shot ingestion orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic

from garmin_sheets_sync.models import DateWindow
from garmin_sheets_sync.ports import Destination, Source, SyncReport

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(
        self,
        source: Source,
        destination: Destination,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._destination = destination
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def run(self, window: DateWindow) -> SyncReport:
        started = monotonic()
        logger.info(
            "sync_started source=%s destination=%s start=%s end=%s",
            self._source.name,
            self._destination.name,
            window.start,
            window.end,
        )
        batch = self._source.fetch(window)
        logger.info(
            "fetch_completed weights=%d daily_activity=%d activities=%d",
            len(batch.weights),
            len(batch.daily_activity),
            len(batch.activities),
        )
        report = self._destination.sync(batch, self._clock())
        totals = report.total
        logger.info(
            "sync_completed inserted=%d updated=%d unchanged=%d duration_seconds=%.3f",
            totals.inserted,
            totals.updated,
            totals.unchanged,
            monotonic() - started,
        )
        return report
