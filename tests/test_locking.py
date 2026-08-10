from pathlib import Path

import pytest

from garmin_sheets_sync.errors import ConcurrentRunError
from garmin_sheets_sync.locking import RunLock


def test_second_run_cannot_acquire_same_lock(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"

    with RunLock(path), pytest.raises(ConcurrentRunError), RunLock(path):
        pass
