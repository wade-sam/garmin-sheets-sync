"""Non-blocking process lock used to prevent overlapping sheet upserts."""

from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import TextIO

from garmin_sheets_sync.errors import ConcurrentRunError


class RunLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: TextIO | None = None

    def __enter__(self) -> RunLock:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._path.open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ConcurrentRunError(f"another sync holds lock {self._path}") from exc
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        handle = self._handle
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None
