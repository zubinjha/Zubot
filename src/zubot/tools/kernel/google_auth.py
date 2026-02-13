"""Google OAuth token lifecycle helpers."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.zubot.core.config_loader import load_config

DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_TIMEOUT_SEC = 15


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_repo_relative_path(raw_path: str | None) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = _repo_root() / path
    return path.resolve()


def _google_oauth_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    config: dict[str, Any] = {}
    profiles = payload.get("tool_profiles")
    if isinstance(profiles, dict):
        user_specific = profiles.get("user_specific")
        if isinstance(user_specific, dict):
            block = user_specific.get("google_oauth")
            if isinstance(block, dict):
                config = block

    token_path = _resolve_repo_relative_path(config.get("token_path"))

    settings = {
        "client_id": config.get("client_id") if isinstance(config.get("client_id"), str) else None,
        "client_secret": config.get("client_secret") if isinstance(config.get("client_secret"), str) else None,
        "refresh_token": config.get("refresh_token") if isinstance(config.get("refresh_token"), str) else None,
        "token_uri": str(config.get("token_uri") or DEFAULT_TOKEN_URI),
        "scopes": config.get("scopes") if isinstance(config.get("scopes"), list) else [],
        "token_path": token_path,
        "timeout_sec": int(config.get("timeout_sec", DEFAULT_TIMEOUT_SEC)),
    }

    missing: list[str] = []
    if not isinstance(config, dict) or not config:
        missing.append("tool_profiles.user_specific.google_oauth")
    if settings["token_path"] is None:
        missing.append("tool_profiles.user_specific.google_oauth.token_path")
    if not settings["client_id"]:
        missing.append("tool_profiles.user_specific.google_oauth.client_id")
    if not settings["client_secret"]:
        missing.append("tool_profiles.user_specific.google_oauth.client_secret")
    if not settings["token_uri"]:
        missing.append("tool_profiles.user_specific.google_oauth.token_uri")

    settings["missing"] = missing
    return settings


def _read_token_file(token_path: Path) -> dict[str, Any]:
    if not token_path.exists():
        return {}
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_token_file_atomic(token_path: Path, token_payload: dict[str, Any]) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = token_path.with_name(f"{token_path.name}.tmp")
    temp_path.write_text(json.dumps(token_payload, indent=2), encoding="utf-8")
    temp_path.replace(token_path)


def _parse_expires_epoch(token_payload: dict[str, Any]) -> int | None:
    raw_epoch = token_payload.get("expires_at_epoch")
    if isinstance(raw_epoch, int):
        return raw_epoch

    raw_iso = token_payload.get("expires_at")
    if isinstance(raw_iso, str) and raw_iso.strip():
        try:
            parsed = datetime.fromisoformat(raw_iso.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(parsed.timestamp())
    return None


def _token_is_usable(token_payload: dict[str, Any], skew_sec: int = 60) -> bool:
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return False
    expires_epoch = _parse_expires_epoch(token_payload)
    if expires_epoch is None:
        return False
    return expires_epoch > int(time.time()) + skew_sec


def _safe_provider_error(http_exc: HTTPError) -> tuple[str, str | None]:
    error_code = "google_oauth_http_error"
    message = f"OAuth token request failed with HTTP {http_exc.code}."

    try:
        body = http_exc.read().decode("utf-8")
        payload = json.loads(body)
        if isinstance(payload, dict):
            raw_code = payload.get("error")
            if isinstance(raw_code, str) and raw_code:
                error_code = raw_code
            raw_desc = payload.get("error_description")
            if isinstance(raw_desc, str) and raw_desc:
                message = raw_desc
    except Exception:
        pass

    if "invalid_grant" in error_code.lower():
        message = "Refresh token is invalid or expired. Re-authentication is required."

    return message, error_code


def _refresh_access_token(settings: dict[str, Any], refresh_token: str) -> dict[str, Any]:
    form = {
        "client_id": settings["client_id"],
        "client_secret": settings["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    scopes = settings.get("scopes")
    if isinstance(scopes, list) and scopes:
        clean_scopes = [scope for scope in scopes if isinstance(scope, str) and scope.strip()]
        if clean_scopes:
            form["scope"] = " ".join(clean_scopes)

    body = urlencode(form).encode("utf-8")
    request = Request(
        settings["token_uri"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings["timeout_sec"]) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        message, error_code = _safe_provider_error(exc)
        return {"ok": False, "error": message, "error_code": error_code}
    except URLError:
        return {"ok": False, "error": "OAuth token request failed due to network error.", "error_code": "network_error"}
    except Exception:
        return {"ok": False, "error": "OAuth token request failed.", "error_code": "request_failed"}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "OAuth token response was not valid JSON.", "error_code": "invalid_response"}

    if not isinstance(payload, dict):
        return {"ok": False, "error": "OAuth token response had invalid shape.", "error_code": "invalid_response"}

    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or not access_token.strip():
        return {"ok": False, "error": "OAuth token response missing access token.", "error_code": "invalid_response"}
    if not isinstance(expires_in, int) or expires_in <= 0:
        return {"ok": False, "error": "OAuth token response missing expires_in.", "error_code": "invalid_response"}

    return {
        "ok": True,
        "access_token": access_token,
        "expires_in": expires_in,
        "refresh_token": payload.get("refresh_token"),
        "token_type": payload.get("token_type"),
        "scope": payload.get("scope"),
    }


def _error_payload(message: str, *, error_code: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "source": "google_oauth_error",
        "error": message,
        "error_code": error_code,
    }


def get_google_access_token(*, force_refresh: bool = False) -> dict[str, Any]:
    settings = _google_oauth_settings()
    missing = settings.get("missing") or []
    if missing:
        return _error_payload(
            "Missing Google OAuth config fields: " + ", ".join(missing),
            error_code="config_missing",
        )

    token_path: Path = settings["token_path"]
    token_state = _read_token_file(token_path)

    if not force_refresh and _token_is_usable(token_state):
        return {
            "ok": True,
            "source": "google_oauth_cache",
            "access_token": token_state.get("access_token"),
            "expires_at": token_state.get("expires_at"),
            "error": None,
        }

    refresh_token = token_state.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        refresh_token = settings.get("refresh_token")

    if not isinstance(refresh_token, str) or not refresh_token.strip():
        return _error_payload(
            "No refresh token available. Configure tool_profiles.user_specific.google_oauth.refresh_token.",
            error_code="refresh_token_missing",
        )

    refreshed = _refresh_access_token(settings, refresh_token)
    if not refreshed.get("ok"):
        return _error_payload(
            str(refreshed.get("error") or "OAuth token refresh failed."),
            error_code=refreshed.get("error_code"),
        )
    if not isinstance(refreshed.get("access_token"), str) or not refreshed.get("access_token"):
        return _error_payload("OAuth token refresh returned invalid payload.", error_code="invalid_response")
    if not isinstance(refreshed.get("expires_in"), int) or int(refreshed["expires_in"]) <= 0:
        return _error_payload("OAuth token refresh returned invalid payload.", error_code="invalid_response")

    expires_epoch = int(time.time()) + int(refreshed["expires_in"])
    expires_at = datetime.fromtimestamp(expires_epoch, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    next_refresh_token = refreshed.get("refresh_token")
    if not isinstance(next_refresh_token, str) or not next_refresh_token.strip():
        next_refresh_token = refresh_token

    next_state = {
        "access_token": refreshed["access_token"],
        "refresh_token": next_refresh_token,
        "token_type": refreshed.get("token_type") or "Bearer",
        "scope": refreshed.get("scope"),
        "expires_at": expires_at,
        "expires_at_epoch": expires_epoch,
        "updated_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    try:
        _write_token_file_atomic(token_path, next_state)
    except OSError:
        return _error_payload("Failed to persist Google token state.", error_code="token_persist_failed")

    return {
        "ok": True,
        "source": "google_oauth_refreshed",
        "access_token": next_state["access_token"],
        "expires_at": next_state["expires_at"],
        "error": None,
    }
