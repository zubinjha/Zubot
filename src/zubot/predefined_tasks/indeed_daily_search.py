"""Placeholder predefined task script for daily Indeed workflow."""

from __future__ import annotations

import json
import os


def main() -> int:
    raw_payload = os.getenv("ZUBOT_TASK_PAYLOAD_JSON", "{}")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = {}

    trigger = str(payload.get("trigger") or "scheduled")
    description = str(payload.get("description") or "").strip()

    if description:
        print(f"indeed_daily_search placeholder completed (trigger={trigger}, description={description})")
    else:
        print(f"indeed_daily_search placeholder completed (trigger={trigger})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
