from __future__ import annotations

import json
from collections.abc import Iterator
from email.message import Message
from io import BytesIO
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from garmin_sheets_sync.adapters import onedrive_storage
from garmin_sheets_sync.adapters.onedrive_storage import GraphOneDriveStorage
from garmin_sheets_sync.errors import RemoteFileChangedError, SyncError


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    def acquire_silent(self) -> str:
        self.calls += 1
        return "secret-access-token"


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._content


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value).encode()


def _headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def _request_json_body(request: Request) -> dict[str, Any]:
    assert isinstance(request.data, bytes)
    value = json.loads(request.data)
    assert isinstance(value, dict)
    return value


def _install_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[bytes | Exception],
) -> list[Request]:
    requests: list[Request] = []
    queued: Iterator[bytes | Exception] = iter(responses)

    def fake_urlopen(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout == 3
        requests.append(request)
        response = next(queued)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    monkeypatch.setattr(onedrive_storage, "urlopen", fake_urlopen)
    return requests


def _metadata(workbook: bytes) -> bytes:
    return _json_bytes(
        {
            "id": "workbook-item-id",
            "name": "RP Cut.xlsx",
            "size": len(workbook),
            "eTag": '"etag-1"',
            "file": {},
            "@microsoft.graph.downloadUrl": "https://download.example/preauthenticated",
        }
    )


def test_storage_downloads_and_conditionally_replaces_existing_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = b"pretend-xlsx"
    requests = _install_responses(
        monkeypatch,
        [
            _metadata(workbook),
            workbook,
            _json_bytes({"uploadUrl": "https://upload.example/preauthenticated"}),
            _json_bytes({"id": "workbook-item-id"}),
        ],
    )
    token_provider = FakeTokenProvider()
    storage = GraphOneDriveStorage(token_provider, timeout_seconds=3, sleep=lambda _: None)

    remote = storage.download("/Apps/Garmin Sheets/RP #1.xlsx")
    storage.replace("/Apps/Garmin Sheets/RP #1.xlsx", b"updated-xlsx", remote.etag)

    assert remote.content == workbook
    assert remote.etag == '"etag-1"'
    assert token_provider.calls == 2
    assert "Apps/Garmin%20Sheets/RP%20%231.xlsx" in requests[0].full_url
    assert _headers(requests[0])["authorization"] == "Bearer secret-access-token"
    assert "authorization" not in _headers(requests[1])
    assert requests[2].method == "POST"
    assert _headers(requests[2])["if-match"] == '"etag-1"'
    assert _headers(requests[2])["authorization"] == "Bearer secret-access-token"
    assert "deferCommit" not in _request_json_body(requests[2])
    assert "fileSize" not in _request_json_body(requests[2])["item"]
    assert requests[3].method == "PUT"
    assert _headers(requests[3])["content-range"] == "bytes 0-11/12"
    assert "authorization" not in _headers(requests[3])
    assert len(requests) == 4


def test_storage_updates_workbook_stored_at_drive_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = b"pretend-xlsx"
    requests = _install_responses(
        monkeypatch,
        [
            _metadata(workbook),
            workbook,
            _json_bytes({"uploadUrl": "https://upload.example/preauthenticated"}),
            _json_bytes({"id": "workbook-item-id"}),
        ],
    )
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        retry_attempts=1,
        sleep=lambda _: None,
    )

    remote = storage.download("/Sam Diet.xlsx")
    storage.replace("/Sam Diet.xlsx", b"updated-xlsx", remote.etag)

    assert "root:/Sam%20Diet.xlsx" in requests[0].full_url
    assert requests[3].full_url == "https://upload.example/preauthenticated"


def test_storage_aborts_when_upload_session_rejects_stale_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = b"pretend-xlsx"
    conflict = HTTPError(
        "https://graph.microsoft.com/upload-session",
        412,
        "Precondition Failed",
        Message(),
        BytesIO(),
    )
    requests = _install_responses(monkeypatch, [_metadata(workbook), workbook, conflict])
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        sleep=lambda _: None,
    )
    remote = storage.download("/Apps/Garmin/RP Cut.xlsx")

    with pytest.raises(RemoteFileChangedError, match="changed or is locked"):
        storage.replace("/Apps/Garmin/RP Cut.xlsx", b"updated-xlsx", remote.etag)

    assert len(requests) == 3


def test_storage_refuses_download_that_does_not_match_metadata_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _install_responses(
        monkeypatch,
        [_metadata(b"expected-size"), b"short"],
    )
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        sleep=lambda _: None,
    )

    with pytest.raises(RemoteFileChangedError, match="incomplete"):
        storage.download("/Apps/Garmin/RP Cut.xlsx")

    assert len(requests) == 2


def test_storage_aborts_when_automatic_commit_rejects_stale_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = b"pretend-xlsx"
    conflict = HTTPError(
        "https://upload.example/preauthenticated",
        412,
        "Precondition Failed",
        Message(),
        BytesIO(),
    )
    requests = _install_responses(
        monkeypatch,
        [
            _metadata(workbook),
            workbook,
            _json_bytes({"uploadUrl": "https://upload.example/preauthenticated"}),
            conflict,
            b"",
        ],
    )
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        sleep=lambda _: None,
    )
    remote = storage.download("/Apps/Garmin/RP Cut.xlsx")

    with pytest.raises(RemoteFileChangedError, match="changed or is locked"):
        storage.replace("/Apps/Garmin/RP Cut.xlsx", b"updated-xlsx", remote.etag)

    assert len(requests) == 5
    assert requests[4].method == "DELETE"
    assert requests[4].full_url == "https://upload.example/preauthenticated"
    assert "authorization" not in _headers(requests[4])


def test_storage_cancels_session_when_byte_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = b"pretend-xlsx"
    transfer_error = HTTPError(
        "https://upload.example/preauthenticated",
        400,
        "Bad Request",
        Message(),
        BytesIO(),
    )
    metadata_error = HTTPError(
        "https://graph.microsoft.com/item",
        500,
        "Internal Server Error",
        Message(),
        BytesIO(),
    )
    requests = _install_responses(
        monkeypatch,
        [
            _metadata(workbook),
            workbook,
            _json_bytes({"uploadUrl": "https://upload.example/preauthenticated"}),
            transfer_error,
            metadata_error,
            b"",
        ],
    )
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        retry_attempts=1,
        sleep=lambda _: None,
    )
    remote = storage.download("/Sam Diet.xlsx")

    with pytest.raises(SyncError, match="upload-byte transfer failed"):
        storage.replace("/Sam Diet.xlsx", b"updated-xlsx", remote.etag)

    assert requests[5].method == "DELETE"
    assert "authorization" not in _headers(requests[5])


def test_storage_confirms_content_after_lost_automatic_commit_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = b"pretend-xlsx"
    updated = b"updated-xlsx"
    lost_response = HTTPError(
        "https://upload.example/preauthenticated",
        500,
        "Internal Server Error",
        Message(),
        BytesIO(),
    )
    committed_metadata = _json_bytes(
        {
            "id": "workbook-item-id",
            "size": len(updated),
            "@microsoft.graph.downloadUrl": "https://download.example/committed",
        }
    )
    requests = _install_responses(
        monkeypatch,
        [
            _metadata(original),
            original,
            _json_bytes({"uploadUrl": "https://upload.example/preauthenticated"}),
            lost_response,
            committed_metadata,
            updated,
        ],
    )
    storage = GraphOneDriveStorage(
        FakeTokenProvider(),
        timeout_seconds=3,
        sleep=lambda _: None,
    )
    remote = storage.download("/Apps/Garmin/RP Cut.xlsx")

    storage.replace("/Apps/Garmin/RP Cut.xlsx", updated, remote.etag)

    assert len(requests) == 6
