"""Task entrypoint for indeed_daily_search pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

try:  # pragma: no cover - runtime import path branch
    from .pipeline import run_pipeline
except Exception:  # pragma: no cover - script-entry fallback
    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.zubot.predefined_tasks.indeed_daily_search.pipeline import run_pipeline


def _safe_json_env(name: str) -> dict:
    raw = os.getenv(name, "{}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    task_id = str(os.getenv("ZUBOT_TASK_ID", "indeed_daily_search")).strip() or "indeed_daily_search"
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    local_cfg = _safe_json_env("ZUBOT_TASK_LOCAL_CONFIG_JSON")
    resources_dir_raw = str(os.getenv("ZUBOT_TASK_RESOURCES_DIR", "")).strip()
    resources_dir = Path(resources_dir_raw) if resources_dir_raw else Path(__file__).resolve().parent
    out = run_pipeline(
        task_id=task_id,
        payload=payload,
        local_config=local_cfg,
        resources_dir=resources_dir,
    )
    if not out.get("ok"):
        print(f"indeed_daily_search failed: {out.get('error')}")
        return 1
    print(out.get("summary") or "indeed_daily_search completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
