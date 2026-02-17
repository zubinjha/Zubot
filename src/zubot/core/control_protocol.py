"""Text-encoded control request protocol for approval-gated actions."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any

CONTROL_REQUEST_BEGIN = "[ZUBOT_CONTROL_REQUEST]"
CONTROL_REQUEST_END = "[/ZUBOT_CONTROL_REQUEST]"
_BLOCK_PATTERN = re.compile(
    re.escape(CONTROL_REQUEST_BEGIN) + r"\s*(\{.*?\})\s*" + re.escape(CONTROL_REQUEST_END),
    re.DOTALL,
)
_VALID_ACTIONS = {
    "enqueue_task",
    "enqueue_agentic_task",
    "kill_task_run",
    "query_central_db",
}
_VALID_RISK_LEVELS = {"low", "medium", "high"}


def protocol_instructions() -> str:
    """Short protocol guide suitable for prompts/docs."""
    return (
        "When approval is required, return one block exactly in this format:\n"
        f"{CONTROL_REQUEST_BEGIN}\n"
        '{"action_id":"act_x","action":"enqueue_task","title":"Run task","risk_level":"high","payload":{"task_id":"example"}}\n'
        f"{CONTROL_REQUEST_END}\n"
        "Do not wrap in markdown fences. One JSON object per block."
    )


def _parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def is_expired(expires_at: Any, *, now: datetime | None = None) -> bool:
    at = _parse_iso_utc(expires_at)
    if at is None:
        return False
    now_dt = now or datetime.now(tz=UTC)
    return at <= now_dt


def normalize_control_request(raw: dict[str, Any], *, default_route: str = "llm.main_agent") -> dict[str, Any] | None:
    """Validate and normalize one control request payload."""
    action = str(raw.get("action") or "").strip()
    if action not in _VALID_ACTIONS:
        return None
    action_id = str(raw.get("action_id") or "").strip()
    if not action_id:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    title = str(raw.get("title") or action).strip() or action
    risk_level = str(raw.get("risk_level") or "medium").strip().lower()
    if risk_level not in _VALID_RISK_LEVELS:
        risk_level = "medium"
    requested_by_route = str(raw.get("requested_by_route") or default_route).strip() or default_route
    expires_at = raw.get("expires_at")
    if isinstance(expires_at, str) and expires_at.strip():
        expires_at = expires_at.strip()
    else:
        expires_at = None
    return {
        "action_id": action_id,
        "action": action,
        "title": title,
        "risk_level": risk_level,
        "payload": payload,
        "requested_by_route": requested_by_route,
        "expires_at": expires_at,
    }


def extract_control_requests(text: str, *, default_route: str = "llm.main_agent") -> list[dict[str, Any]]:
    """Extract valid control request blocks from assistant reply text."""
    if not isinstance(text, str) or not text.strip():
        return []
    out: list[dict[str, Any]] = []
    for match in _BLOCK_PATTERN.finditer(text):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        normalized = normalize_control_request(parsed, default_route=default_route)
        if normalized is not None:
            out.append(normalized)
    return out

