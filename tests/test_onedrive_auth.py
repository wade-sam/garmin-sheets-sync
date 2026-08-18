from __future__ import annotations

import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from garmin_sheets_sync.adapters.onedrive_storage import PersistentMsalTokenProvider
from garmin_sheets_sync.errors import ConfigurationError


class FakeCache:
    has_state_changed = True

    def deserialize(self, _serialized: str) -> None:
        return None

    def serialize(self) -> str:
        return "serialized-token-cache"


class FakeApplication:
    accounts: list[dict[str, str]] = []
    silent_result: dict[str, str] = {"access_token": "silent-token"}
    device_result: dict[str, str] = {"access_token": "device-token"}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_accounts(self) -> list[dict[str, str]]:
        return self.accounts

    def acquire_token_silent(
        self,
        _scopes: list[str],
        *,
        account: dict[str, str],
    ) -> dict[str, str]:
        assert account == self.accounts[0]
        return self.silent_result

    def initiate_device_flow(self, *, scopes: list[str]) -> dict[str, str]:
        assert scopes == ["Files.ReadWrite"]
        return {"user_code": "ABCD-EFGH", "message": "Enter device code ABCD-EFGH"}

    def acquire_token_by_device_flow(self, _flow: dict[str, str]) -> dict[str, str]:
        return self.device_result


def _install_fake_msal(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("msal")
    module.SerializableTokenCache = FakeCache  # type: ignore[attr-defined]
    module.PublicClientApplication = FakeApplication  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msal", module)


def test_scheduled_auth_refuses_to_start_interactive_login_without_cached_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_msal(monkeypatch)
    FakeApplication.accounts = []
    provider = PersistentMsalTokenProvider("client-id", tmp_path / "token-cache.json")

    with pytest.raises(ConfigurationError, match="no cached account"):
        provider.acquire_silent()


def test_device_login_persists_private_token_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_msal(monkeypatch)
    messages: list[str] = []
    cache_file = tmp_path / "credentials" / "token-cache.json"
    provider = PersistentMsalTokenProvider("client-id", cache_file)

    provider.authenticate_device_code(messages.append)

    assert messages == ["Enter device code ABCD-EFGH"]
    assert cache_file.read_text() == "serialized-token-cache"
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_file.parent.stat().st_mode) == 0o700


def test_silent_login_repairs_permissions_on_existing_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_msal(monkeypatch)
    monkeypatch.setattr(FakeCache, "has_state_changed", False)
    monkeypatch.setattr(FakeApplication, "accounts", [{"home_account_id": "account"}])
    cache_dir = tmp_path / "credentials"
    cache_dir.mkdir(mode=0o755)
    cache_file = cache_dir / "token-cache.json"
    cache_file.write_text("existing-cache")
    cache_file.chmod(0o644)
    provider = PersistentMsalTokenProvider("client-id", cache_file)

    assert provider.acquire_silent() == "silent-token"
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_file.parent.stat().st_mode) == 0o700
