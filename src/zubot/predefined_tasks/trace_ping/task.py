"""Simple traceable predefined task for end-to-end queue testing."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path


def _safe_json_env(name: str) -> dict:
    raw = os.getenv(name, "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _append_trace_row(resources_dir: Path, row: dict) -> Path:
    state_dir = resources_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    trace_path = state_dir / "run_trace.jsonl"
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return trace_path


def main() -> int:
    task_id = str(os.getenv("ZUBOT_TASK_ID", "trace_ping")).strip() or "trace_ping"
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    profile = _safe_json_env("ZUBOT_TASK_PROFILE_JSON")
    resources_dir_raw = str(os.getenv("ZUBOT_TASK_RESOURCES_DIR", "")).strip()
    resources_dir = Path(resources_dir_raw) if resources_dir_raw else Path.cwd()
    now_iso = datetime.now(tz=UTC).isoformat()
    trace_row = {
        "timestamp_utc": now_iso,
        "task_id": task_id,
        "name": str(profile.get("name") or task_id),
        "trigger": str(payload.get("trigger") or "unknown"),
        "origin": str(payload.get("origin") or "unknown"),
        "description": str(payload.get("description") or ""),
    }
    trace_path = _append_trace_row(resources_dir, trace_row)
    print(f"trace_ping ok at {now_iso} -> {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

