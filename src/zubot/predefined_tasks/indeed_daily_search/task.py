"""Task entrypoint for indeed_daily_search pipeline."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any, Callable

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


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _progress_callback_from_env() -> Callable[[dict[str, Any]], None] | None:
    enabled = str(os.getenv("ZUBOT_TASK_ENABLE_TQDM", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    last_print = {"text": "", "at": 0.0}
    blue = "\033[94m"
    red = "\033[91m"
    reset = "\033[0m"

    def _callback(progress: dict[str, Any]) -> None:
        stage = str(progress.get("stage") or "").strip().lower()
        percent_raw = progress.get("overall_percent", progress.get("total_percent"))
        try:
            percent = float(percent_raw)
        except Exception:
            percent = 0.0
        percent = max(0.0, min(100.0, percent))

        query_index = int(progress.get("query_index") or 0)
        query_total = int(progress.get("query_total") or 0)
        job_index = int(progress.get("job_index") or 0)
        job_total = int(progress.get("job_total") or 0)
        job_key = str(progress.get("job_key") or "").strip()
        decision = str(progress.get("decision") or "").strip()
        job_url = str(progress.get("job_url") or "").strip()
        local_path = str(progress.get("cover_letter_local_path") or "").strip()
        status = str(progress.get("status_line") or "").strip()

        # Print one per-job block only after decision/outcome exists.
        if stage == "process" and job_index > 0:
            return

        if not decision and stage == "process_result" and status:
            match = re.search(r"decision=(.*?)\s+outcome=", status)
            if match:
                decision = match.group(1).strip()

        lines: list[str] = [f"[{_ts()}] [progress] {blue}{percent:.1f}%{reset} ({stage or 'running'})"]

        summary_parts: list[str] = []
        if query_total > 0 and query_index > 0:
            summary_parts.append(f"query {query_index}/{query_total}")
        if job_total > 0 and job_index > 0:
            result_part = f"result {job_index}/{job_total}"
            if job_key:
                result_part += f" ({job_key})"
            summary_parts.append(result_part)
        if summary_parts:
            lines.append(" | ".join(summary_parts))
        elif status:
            lines.append(status)

        if local_path:
            lines.append(f"local_cover_letter_path: {local_path}")
        if decision:
            lines.append(f"decision: {red}{decision}{reset}")
        if job_url:
            lines.append(f"url: {job_url}")
        lines.append("")

        block = "\n".join(lines)
        now = monotonic()
        if block == last_print["text"] and (now - float(last_print["at"])) < 1.0:
            return
        print(block, flush=True)
        last_print["text"] = block
        last_print["at"] = now

    return _callback


def main() -> int:
    task_id = str(os.getenv("ZUBOT_TASK_ID", "indeed_daily_search")).strip() or "indeed_daily_search"
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    local_cfg = _safe_json_env("ZUBOT_TASK_LOCAL_CONFIG_JSON")
    resources_dir_raw = str(os.getenv("ZUBOT_TASK_RESOURCES_DIR", "")).strip()
    resources_dir = Path(resources_dir_raw) if resources_dir_raw else Path(__file__).resolve().parent
    progress_callback = _progress_callback_from_env()
    out = run_pipeline(
        task_id=task_id,
        payload=payload,
        local_config=local_cfg,
        resources_dir=resources_dir,
        progress_callback=progress_callback,
    )
    if not out.get("ok"):
        print(f"indeed_daily_search failed: {out.get('error')}")
        return 1
    print(out.get("summary") or "indeed_daily_search completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
