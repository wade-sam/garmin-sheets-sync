"""Delegated Microsoft authentication and personal OneDrive file transport."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, NoReturn, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from garmin_sheets_sync.adapters.onedrive_xlsx_destination import RemoteFile
from garmin_sheets_sync.errors import (
    ConfigurationError,
    RemoteFileChangedError,
    SyncError,
)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
CONSUMER_AUTHORITY = "https://login.microsoftonline.com/consumers"
ONEDRIVE_SCOPES = ("Files.ReadWrite",)
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024


class AccessTokenProvider(Protocol):
    def acquire_silent(self) -> str: ...


class PersistentMsalTokenProvider:
    """Acquire delegated tokens from a credential cache owned by one personal account."""

    def __init__(
        self,
        client_id: str,
        cache_file: Path,
        *,
        authority: str = CONSUMER_AUTHORITY,
    ) -> None:
        if not client_id.strip():
            raise ConfigurationError("ONEDRIVE_CLIENT_ID must not be blank")
        self._client_id = client_id
        self._cache_file = cache_file
        self._authority = authority

    def acquire_silent(self) -> str:
        app, cache = self._application()
        accounts = app.get_accounts()
        if len(accounts) != 1:
            detail = "no cached account" if not accounts else "multiple cached accounts"
            raise ConfigurationError(
                f"OneDrive authentication requires exactly one account ({detail}); "
                "run 'garmin-sheets-sync onedrive-auth'"
            )
        result = app.acquire_token_silent(list(ONEDRIVE_SCOPES), account=accounts[0])
        self._save_cache(cache)
        return self._access_token(result, interactive=False)

    def authenticate_device_code(self, output: Callable[[str], None] = print) -> None:
        app, cache = self._application()
        flow = app.initiate_device_flow(scopes=list(ONEDRIVE_SCOPES))
        message = flow.get("message") if isinstance(flow, dict) else None
        if not isinstance(message, str) or "user_code" not in flow:
            raise ConfigurationError("Microsoft did not start the OneDrive device login")
        output(message)
        result = app.acquire_token_by_device_flow(flow)
        self._access_token(result, interactive=True)
        self._save_cache(cache)

    def _application(self) -> tuple[Any, Any]:
        try:
            import msal
        except ImportError as exc:
            raise ConfigurationError(
                "OneDrive authentication support is not installed; install the 'live' extra"
            ) from exc

        cache = msal.SerializableTokenCache()
        if self._cache_file.exists():
            if not self._cache_file.is_file():
                raise ConfigurationError(
                    f"OneDrive token cache is not a regular file: {self._cache_file}"
                )
            try:
                self._cache_file.parent.chmod(0o700)
                self._cache_file.chmod(0o600)
                cache.deserialize(self._cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigurationError("OneDrive token cache cannot be read") from exc
        app = msal.PublicClientApplication(
            self._client_id,
            authority=self._authority,
            token_cache=cache,
        )
        return app, cache

    def _save_cache(self, cache: Any) -> None:
        if not cache.has_state_changed:
            return
        parent = self._cache_file.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            parent.chmod(0o700)
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{self._cache_file.name}.",
                delete=False,
            ) as temporary:
                temporary.write(cache.serialize())
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            temporary_path.replace(self._cache_file)
            self._cache_file.chmod(0o600)
        except OSError as exc:
            raise ConfigurationError("OneDrive token cache cannot be saved") from exc

    @staticmethod
    def _access_token(result: Any, *, interactive: bool) -> str:
        if isinstance(result, dict) and isinstance(result.get("access_token"), str):
            return str(result["access_token"])
        action = "complete" if interactive else "refresh"
        code = result.get("error") if isinstance(result, dict) else None
        suffix = f" ({code})" if isinstance(code, str) else ""
        raise ConfigurationError(f"Microsoft could not {action} OneDrive authentication{suffix}")


class GraphOneDriveStorage:
    """Download and conditionally replace an existing OneDrive file."""

    def __init__(
        self,
        token_provider: AccessTokenProvider,
        *,
        timeout_seconds: float = 60,
        retry_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retry_attempts < 1:
            raise ConfigurationError("OneDrive retry attempts must be at least one")
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts
        self._sleep = sleep
        self._items_by_path: dict[str, str] = {}

    def download(self, path: str) -> RemoteFile:
        encoded_path = quote(path.strip("/"), safe="/")
        metadata = self._graph_json(
            "GET",
            f"{GRAPH_ROOT}/me/drive/root:/{encoded_path}"
            "?select=id,name,size,eTag,file,@microsoft.graph.downloadUrl",
        )
        item_id = self._required_string(metadata, "id", "OneDrive workbook metadata")
        etag = self._required_string(metadata, "eTag", "OneDrive workbook metadata")
        name = self._required_string(metadata, "name", "OneDrive workbook metadata")
        download_url = self._required_string(
            metadata, "@microsoft.graph.downloadUrl", "OneDrive workbook metadata"
        )
        size = metadata.get("size")
        if "file" not in metadata or not isinstance(size, int) or size < 0:
            raise ConfigurationError(f"OneDrive path {path!r} is not a file")
        if not name.lower().endswith(".xlsx"):
            raise ConfigurationError(f"OneDrive path {path!r} is not an .xlsx workbook")
        if size > MAX_WORKBOOK_BYTES:
            raise ConfigurationError(
                "OneDrive workbook is larger than the "
                f"{MAX_WORKBOOK_BYTES // 1024 // 1024} MiB limit"
            )
        content = self._absolute_bytes("GET", download_url)
        if len(content) > MAX_WORKBOOK_BYTES:
            raise ConfigurationError("OneDrive workbook download exceeded the size limit")
        if len(content) != size:
            raise RemoteFileChangedError(
                "OneDrive workbook changed or was incomplete while being downloaded"
            )
        self._items_by_path[path] = item_id
        return RemoteFile(content=content, etag=etag)

    def replace(self, path: str, content: bytes, expected_etag: str) -> None:
        item_id = self._items_by_path.get(path)
        if item_id is None:
            raise ConfigurationError("OneDrive workbook must be downloaded before it is replaced")
        if not content or len(content) > MAX_WORKBOOK_BYTES:
            raise ConfigurationError("generated OneDrive workbook has an invalid size")

        try:
            session = self._graph_json(
                "POST",
                f"{GRAPH_ROOT}/me/drive/items/{quote(item_id, safe='')}/createUploadSession",
                headers={"If-Match": expected_etag},
                body=json.dumps(
                    {
                        # The personal Graph API returns invalidRequest when
                        # fileSize is supplied while updating an existing item.
                        # Its documented deferred sourceUrl commit is also no
                        # longer accepted, so this one-chunk upload commits
                        # automatically when the byte transfer completes.
                        "item": {
                            "@microsoft.graph.conflictBehavior": "replace",
                        },
                    }
                ).encode(),
            )
        except RemoteFileChangedError as exc:
            raise RemoteFileChangedError(
                "OneDrive workbook changed or is locked while creating the "
                f"upload session; no replacement was made ({exc})"
            ) from exc
        except SyncError as exc:
            raise SyncError(f"OneDrive upload-session creation failed: {exc}") from exc
        upload_url = self._required_string(session, "uploadUrl", "OneDrive upload session")
        try:
            response = self._absolute_json(
                "PUT",
                upload_url,
                headers={
                    "Content-Length": str(len(content)),
                    "Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}",
                },
                body=content,
            )
        except RemoteFileChangedError as exc:
            self._cancel_upload_session(upload_url)
            raise RemoteFileChangedError(
                "OneDrive workbook changed or is locked during byte transfer; "
                f"no replacement was made ({exc})"
            ) from exc
        except SyncError as exc:
            if self._replacement_landed(item_id, content):
                return
            self._cancel_upload_session(upload_url)
            raise SyncError(f"OneDrive upload-byte transfer failed: {exc}") from exc
        uploaded_id = self._required_string(
            response, "id", "OneDrive automatic-commit response"
        )
        if uploaded_id != item_id:
            raise SyncError("OneDrive replaced an unexpected file")

    def _cancel_upload_session(self, upload_url: str) -> None:
        with suppress(SyncError):
            self._request("DELETE", upload_url, {}, None, retry=False)

    def _graph_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        token = self._token_provider.acquire_silent()
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        return self._request_json(method, url, request_headers, body, retry=retry)

    def _replacement_landed(self, item_id: str, expected_content: bytes) -> bool:
        try:
            metadata = self._graph_json(
                "GET",
                f"{GRAPH_ROOT}/me/drive/items/{quote(item_id, safe='')}"
                "?select=id,size,@microsoft.graph.downloadUrl",
            )
            size = metadata.get("size")
            download_url = self._required_string(
                metadata,
                "@microsoft.graph.downloadUrl",
                "OneDrive replacement metadata",
            )
            if size != len(expected_content):
                return False
            return self._absolute_bytes("GET", download_url) == expected_content
        except SyncError:
            return False

    def _absolute_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        return self._request_json(method, url, headers, body, retry=False)

    def _absolute_bytes(self, method: str, url: str) -> bytes:
        return self._request(method, url, {}, None, retry=True)

    def _request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        retry: bool,
    ) -> dict[str, Any]:
        raw = self._request(method, url, headers, body, retry=retry)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SyncError("OneDrive returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise SyncError("OneDrive returned an unexpected JSON response")
        return value

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        retry: bool,
    ) -> bytes:
        attempts = self._retry_attempts if retry else 1
        for attempt in range(1, attempts + 1):
            request = Request(url, data=body, headers=headers, method=method)
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                    raw = response.read(MAX_WORKBOOK_BYTES + 1)
                    if not isinstance(raw, bytes):
                        raise SyncError("OneDrive returned an unexpected response body")
                    return raw
            except HTTPError as exc:
                if exc.code in {409, 412, 423}:
                    raise RemoteFileChangedError(
                        "OneDrive workbook changed or is locked "
                        f"(HTTP {exc.code}); no replacement was made"
                    ) from exc
                if retry and self._retryable(exc.code) and attempt < attempts:
                    self._sleep(self._retry_delay(exc, attempt))
                    continue
                self._raise_http_error(exc)
            except (TimeoutError, URLError) as exc:
                if retry and attempt < attempts:
                    self._sleep(float(2 ** (attempt - 1)))
                    continue
                raise SyncError("OneDrive request failed before completion") from exc
        raise AssertionError("request retry loop exhausted")

    @staticmethod
    def _retryable(status: int) -> bool:
        return status == 429 or status in {500, 502, 503, 504}

    @staticmethod
    def _retry_delay(error: HTTPError, attempt: int) -> float:
        raw = error.headers.get("Retry-After")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
        return float(2 ** (attempt - 1))

    @staticmethod
    def _raise_http_error(error: HTTPError) -> NoReturn:
        if error.code == 404:
            raise ConfigurationError("OneDrive workbook was not found") from error
        if error.code in {401, 403}:
            raise ConfigurationError(
                "OneDrive authorization failed; run 'garmin-sheets-sync onedrive-auth'"
            ) from error
        graph_code = None
        graph_message = None
        try:
            document = json.loads(error.read(64 * 1024))
            graph_error = document.get("error") if isinstance(document, dict) else None
            candidate = graph_error.get("code") if isinstance(graph_error, dict) else None
            if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                graph_code = candidate
            candidate_message = (
                graph_error.get("message") if isinstance(graph_error, dict) else None
            )
            if isinstance(candidate_message, str):
                graph_message = " ".join(candidate_message.split())[:300]
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        details = ": ".join(
            detail for detail in (graph_code, graph_message) if detail
        )
        suffix = f" ({details})" if details else ""
        raise SyncError(
            f"OneDrive request failed with HTTP {error.code}{suffix}"
        ) from error

    @staticmethod
    def _required_string(value: dict[str, Any], key: str, context: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result:
            raise SyncError(f"{context} is missing {key!r}")
        return result
