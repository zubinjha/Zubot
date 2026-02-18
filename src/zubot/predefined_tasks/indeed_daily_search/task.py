"""Task entrypoint for indeed_daily_search pipeline."""

from __future__ import annotations

import atexit
from datetime import datetime, timedelta
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


def _fmt_local_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _fmt_hhmm_from_seconds(seconds: float) -> str:
    safe = max(0, int(seconds))
    hours = safe // 3600
    minutes = (safe % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def _progress_callback_from_env(*, resources_dir: Path) -> Callable[[dict[str, Any]], None] | None:
    enabled = str(os.getenv("ZUBOT_TASK_ENABLE_TQDM", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    last_print = {"text": "", "at": 0.0}
    start_wall = datetime.now()
    start_mono = monotonic()
    decision_counters = {
        "skip_seen": 0,
        "skip": 0,
        "recommend_apply": 0,
        "recommend_maybe": 0,
    }
    last_counted_processed_jobs = {"value": -1}
    blue = "\033[94m"
    yellow = "\033[93m"
    reset = "\033[0m"

    logs_dir = resources_dir / "state" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_log_path = logs_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    log_handle = run_log_path.open("a", encoding="utf-8")

    def _close_log() -> None:
        try:
            log_handle.close()
        except Exception:
            pass

    atexit.register(_close_log)

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
        outcome = str(progress.get("outcome") or "").strip().lower()
        processed_jobs = int(progress.get("processed_jobs") or 0)
        job_url = str(progress.get("job_url") or "").strip()
        local_path = str(progress.get("cover_letter_local_path") or "").strip()
        drive_file_id = str(progress.get("cover_letter_drive_file_id") or "").strip()
        drive_folder_id = str(progress.get("cover_letter_drive_folder_id") or "").strip()
        status = str(progress.get("status_line") or "").strip()

        try:
            log_handle.write(
                json.dumps(
                    {
                        "timestamp": _ts(),
                        "stage": stage,
                        "percent": round(percent, 1),
                        "query_index": query_index,
                        "query_total": query_total,
                        "job_index": job_index,
                        "job_total": job_total,
                        "job_key": job_key,
                        "decision": decision,
                        "job_url": job_url,
                        "cover_letter_local_path": local_path,
                        "cover_letter_drive_file_id": drive_file_id,
                        "cover_letter_drive_folder_id": drive_folder_id,
                        "status_line": status,
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
            log_handle.flush()
        except Exception:
            pass

        if not decision and stage == "process_result" and status:
            match = re.search(r"decision=(.*?)\s+outcome=", status)
            if match:
                decision = match.group(1).strip()
        if not outcome and stage == "process_result" and status:
            match = re.search(r"outcome=([a-zA-Z_]+)", status)
            if match:
                outcome = match.group(1).strip().lower()

        if stage == "process_result" and processed_jobs > last_counted_processed_jobs["value"]:
            lowered_decision = decision.lower()
            if lowered_decision == "seenskip" or outcome == "already_seen":
                decision_counters["skip_seen"] += 1
            elif lowered_decision == "skip":
                decision_counters["skip"] += 1
            elif lowered_decision == "recommend apply":
                decision_counters["recommend_apply"] += 1
            elif lowered_decision == "recommend maybe":
                decision_counters["recommend_maybe"] += 1
            last_counted_processed_jobs["value"] = processed_jobs

        now_mono = monotonic()
        elapsed_sec = max(0.0, now_mono - start_mono)
        elapsed_hhmm = _fmt_hhmm_from_seconds(elapsed_sec)
        expected_total_hhmm = "--:--"
        projected_end_text = "--:--"
        if percent > 0.0:
            expected_total_sec = elapsed_sec * (100.0 / percent)
            expected_total_hhmm = _fmt_hhmm_from_seconds(expected_total_sec)
            projected_end = start_wall + timedelta(seconds=expected_total_sec)
            projected_end_text = _fmt_local_time(projected_end)
        start_text = _fmt_local_time(start_wall)

        lines: list[str] = [
            f"[{_ts()}] indeed_daily_search",
            f"stage: {stage or 'running'}",
            f"progress: {blue}{percent:.1f}%{reset}",
            f"time: elapsed {yellow}{elapsed_hhmm}{reset} / expected {yellow}{expected_total_hhmm}{reset}",
            f"start/end: {yellow}{start_text}{reset} -> {yellow}{projected_end_text}{reset}",
            (
                "decisions:"
                f" skip_seen={decision_counters['skip_seen']}"
                f" skip={decision_counters['skip']}"
                f" recommend_apply={decision_counters['recommend_apply']}"
                f" recommend_maybe={decision_counters['recommend_maybe']}"
            ),
            f"log file: {run_log_path}",
        ]
        if local_path:
            lines.append(f"cover letter: {local_path}")
        if drive_file_id:
            lines.append(f"drive file id: {drive_file_id}")
        if drive_folder_id:
            lines.append(f"drive folder id: {drive_folder_id}")
        block = "\n".join(lines)
        if block == last_print["text"] and (now_mono - float(last_print["at"])) < 1.0:
            return
        # Rewrite one live dashboard block (no scrolling history).
        print("\033[2J\033[H" + block, end="\n", flush=True)
        last_print["text"] = block
        last_print["at"] = now_mono

    return _callback


def main() -> int:
    task_id = str(os.getenv("ZUBOT_TASK_ID", "indeed_daily_search")).strip() or "indeed_daily_search"
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    local_cfg = _safe_json_env("ZUBOT_TASK_LOCAL_CONFIG_JSON")
    resources_dir_raw = str(os.getenv("ZUBOT_TASK_RESOURCES_DIR", "")).strip()
    resources_dir = Path(resources_dir_raw) if resources_dir_raw else Path(__file__).resolve().parent
    progress_callback = _progress_callback_from_env(resources_dir=resources_dir)
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
