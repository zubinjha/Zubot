"""Task entrypoint scaffold for indeed_daily_search."""

from __future__ import annotations

import json
import os


def _safe_json_env(name: str) -> dict:
    raw = os.getenv(name, "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    local_cfg = _safe_json_env("ZUBOT_TASK_LOCAL_CONFIG_JSON")
    trigger = str(payload.get("trigger") or "scheduled")
    cursor = local_cfg.get("cursor")
    if cursor is not None:
        print(f"indeed_daily_search placeholder completed (trigger={trigger}, cursor={cursor})")
    else:
        print(f"indeed_daily_search placeholder completed (trigger={trigger})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
