import importlib
import json
import time
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.google_auth import get_google_access_token

module = importlib.import_module("src.zubot.tools.kernel.google_auth")


def _write_config(path: Path, payload: dict):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def configured_google_oauth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    token_path = tmp_path / "google_token.json"
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "tool_profiles": {
                "user_specific": {
                    "google_oauth": {
                        "token_path": str(token_path),
                        "client_id": "test-client-id",
                        "client_secret": "test-client-secret",
                        "refresh_token": "refresh-config-token",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
                        "timeout_sec": 12,
                    }
                }
            }
        },
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return {"token_path": token_path, "config_path": config_path}


def test_get_google_access_token_missing_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    _write_config(config_path, {"tool_profiles": {"user_specific": {}}})
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()

    out = get_google_access_token()
    assert out["ok"] is False
    assert out["source"] == "google_oauth_error"
    assert out["error_code"] == "config_missing"


def test_get_google_access_token_uses_cached_token(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    token_path = configured_google_oauth["token_path"]
    token_path.write_text(
        json.dumps(
            {
                "access_token": "cached-token",
                "refresh_token": "refresh-token",
                "expires_at_epoch": int(time.time()) + 3600,
                "expires_at": "2030-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    def should_not_refresh(settings, refresh_token):
        raise AssertionError("refresh should not be called when token is valid")

    monkeypatch.setattr(module, "_refresh_access_token", should_not_refresh)
    out = get_google_access_token()
    assert out["ok"] is True
    assert out["source"] == "google_oauth_cache"
    assert out["access_token"] == "cached-token"


def test_get_google_access_token_refreshes_expired_token(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    token_path = configured_google_oauth["token_path"]
    token_path.write_text(
        json.dumps(
            {
                "access_token": "old-token",
                "refresh_token": "refresh-from-file",
                "expires_at_epoch": int(time.time()) - 10,
            }
        ),
        encoding="utf-8",
    )

    def fake_refresh(settings, refresh_token):
        assert refresh_token == "refresh-from-file"
        return {
            "ok": True,
            "access_token": "new-token",
            "expires_in": 1800,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(module, "_refresh_access_token", fake_refresh)
    out = get_google_access_token()
    assert out["ok"] is True
    assert out["source"] == "google_oauth_refreshed"
    assert out["access_token"] == "new-token"

    persisted = json.loads(token_path.read_text(encoding="utf-8"))
    assert persisted["access_token"] == "new-token"
    assert persisted["refresh_token"] == "refresh-from-file"
    assert isinstance(persisted["expires_at_epoch"], int)


def test_get_google_access_token_refreshes_when_token_file_missing(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    token_path = configured_google_oauth["token_path"]
    assert token_path.exists() is False

    def fake_refresh(settings, refresh_token):
        assert refresh_token == "refresh-config-token"
        return {
            "ok": True,
            "access_token": "fresh-token",
            "expires_in": 1200,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(module, "_refresh_access_token", fake_refresh)
    out = get_google_access_token()
    assert out["ok"] is True
    assert out["access_token"] == "fresh-token"
    assert token_path.exists() is True


def test_get_google_access_token_invalid_grant(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    def fake_refresh(settings, refresh_token):
        return {
            "ok": False,
            "error": "Refresh token is invalid or expired. Re-authentication is required.",
            "error_code": "invalid_grant",
        }

    monkeypatch.setattr(module, "_refresh_access_token", fake_refresh)
    out = get_google_access_token(force_refresh=True)
    assert out["ok"] is False
    assert out["error_code"] == "invalid_grant"
    assert "Re-authentication" in out["error"]


def test_get_google_access_token_invalid_refresh_shape(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    class _FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=10):
        return _FakeResponse({"expires_in": 1200})

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    settings = module._google_oauth_settings()
    out = module._refresh_access_token(settings, "refresh-config-token")
    assert out["ok"] is False
    assert out["error_code"] == "invalid_response"


def test_get_google_access_token_force_refresh_bypasses_cache(configured_google_oauth, monkeypatch: pytest.MonkeyPatch):
    token_path = configured_google_oauth["token_path"]
    token_path.write_text(
        json.dumps(
            {
                "access_token": "cached-token",
                "refresh_token": "refresh-from-file",
                "expires_at_epoch": int(time.time()) + 3600,
                "expires_at": "2030-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    called = {"value": False}

    def fake_refresh(settings, refresh_token):
        called["value"] = True
        return {
            "ok": True,
            "access_token": "forced-token",
            "expires_in": 1200,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(module, "_refresh_access_token", fake_refresh)
    out = get_google_access_token(force_refresh=True)
    assert called["value"] is True
    assert out["ok"] is True
    assert out["access_token"] == "forced-token"
