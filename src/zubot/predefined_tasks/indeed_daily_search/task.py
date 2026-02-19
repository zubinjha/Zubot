"""Task entrypoint for indeed_daily_search pipeline."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
from threading import Lock
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


def _format_duration_hhmm(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def _format_local_clock(value: datetime | None) -> str:
    if value is None:
        return "-"
    text = value.strftime("%I:%M %p")
    return text[1:] if text.startswith("0") else text


def _run_log_path(resources_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = resources_dir / "state" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"run-{stamp}.log"


def _progress_callback_from_env(
    *, log_path: Path | None = None, timeout_sec: int | None = None
) -> Callable[[dict[str, Any]], None] | None:
    enabled = str(os.getenv("ZUBOT_TASK_ENABLE_TQDM", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None

    last_print = {"text": "", "at": 0.0, "lines": 0}
    start_wall = datetime.now()
    start_mono = monotonic()
    timeout_wall = (start_wall + timedelta(seconds=timeout_sec)) if isinstance(timeout_sec, int) and timeout_sec > 0 else None
    counters = {"skip_seen": 0, "skip": 0, "recommend_apply": 0, "recommend_maybe": 0}
    seen_progress_events: set[tuple[str, int, int, str, str]] = set()
    use_live_rewrite = sys.stdout.isatty()
    non_seen_intervals_sec: deque[float] = deque(maxlen=10)
    timing_state: dict[str, float | None] = {"last_non_seen_completion_mono": None}
    render_lock = Lock()

    blue = "\033[94m"
    yellow = "\033[93m"
    reset = "\033[0m"

    def _callback(progress: dict[str, Any]) -> None:
        with render_lock:
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
            status = str(progress.get("status_line") or "").strip()
            query_keyword = str(progress.get("query_keyword") or "").strip()
            query_location = str(progress.get("query_location") or "").strip()
            worker_slots_raw = progress.get("worker_slots")

            if not decision and status:
                match = re.search(r"decision=(.*?)\s+outcome=", status)
                if match:
                    decision = match.group(1).strip()

            event_sig = (
                stage,
                int(query_index),
                int(job_index),
                job_key,
                decision.strip().lower(),
            )
            if stage == "process_result" and job_key and (event_sig not in seen_progress_events):
                seen_progress_events.add(event_sig)
                normalized = decision.strip().lower()
                if normalized == "seenskip":
                    counters["skip_seen"] += 1
                elif normalized == "skip":
                    counters["skip"] += 1
                    now_done = monotonic()
                    last_done = timing_state.get("last_non_seen_completion_mono")
                    if isinstance(last_done, (int, float)):
                        non_seen_intervals_sec.append(now_done - float(last_done))
                    timing_state["last_non_seen_completion_mono"] = now_done
                elif normalized == "recommend apply":
                    counters["recommend_apply"] += 1
                    now_done = monotonic()
                    last_done = timing_state.get("last_non_seen_completion_mono")
                    if isinstance(last_done, (int, float)):
                        non_seen_intervals_sec.append(now_done - float(last_done))
                    timing_state["last_non_seen_completion_mono"] = now_done
                elif normalized == "recommend maybe":
                    counters["recommend_maybe"] += 1
                    now_done = monotonic()
                    last_done = timing_state.get("last_non_seen_completion_mono")
                    if isinstance(last_done, (int, float)):
                        non_seen_intervals_sec.append(now_done - float(last_done))
                    timing_state["last_non_seen_completion_mono"] = now_done

            elapsed_sec = max(0.0, monotonic() - start_mono)
            expected_total_sec: float | None = None
            if percent >= 0.1:
                expected_total_sec = elapsed_sec * (100.0 / percent)
            if stage == "done":
                expected_total_sec = elapsed_sec
            projected_end = (start_wall + timedelta(seconds=expected_total_sec)) if expected_total_sec is not None else None

            query_text = "-"
            if query_total > 0 and query_index > 0:
                label = f"{query_index}/{query_total}"
                if query_keyword and query_location:
                    label += f" ({query_keyword}, {query_location})"
                elif query_keyword:
                    label += f" ({query_keyword})"
                elif query_location:
                    label += f" ({query_location})"
                query_text = label

            result_text = "-"
            if job_total > 0 and job_index > 0:
                result_text = f"{job_index}/{job_total}"
                if job_key:
                    result_text += f" ({job_key})"

            if non_seen_intervals_sec:
                avg_last_n = sum(non_seen_intervals_sec) / float(len(non_seen_intervals_sec))
                rate_text = f"last {len(non_seen_intervals_sec)} non-seen avg {avg_last_n:.1f}s"
            else:
                rate_text = "last 0 non-seen avg -"

            worker_lines: list[str] = ["workers: -"]
            if isinstance(worker_slots_raw, list):
                parts: list[str] = []
                for item in worker_slots_raw:
                    if not isinstance(item, dict):
                        continue
                    slot = item.get("slot")
                    state = str(item.get("state") or "").strip() or "unknown"
                    step_label = str(item.get("step_label") or "").strip()
                    step_index_raw = item.get("step_index")
                    step_total_raw = item.get("step_total")
                    try:
                        step_index = int(step_index_raw)
                    except Exception:
                        step_index = 0
                    try:
                        step_total = int(step_total_raw)
                    except Exception:
                        step_total = 0
                    step_suffix = ""
                    if step_total > 0:
                        step_suffix = f" {step_index}/{step_total}"
                    if step_label:
                        step_suffix += f" {step_label}"
                    job = str(item.get("job_key") or "").strip()
                    if job:
                        step_suffix += f" {job[:8]}"
                    if isinstance(slot, int) and slot > 0:
                        parts.append(f"    worker {slot}: {state}{step_suffix}")
                if parts:
                    worker_lines = ["workers:"] + parts

            decisions_line = (
                "decisions: "
                f"skip_seen={counters['skip_seen']} "
                f"skip={counters['skip']} "
                f"recommend_apply={counters['recommend_apply']} "
                f"recommend_maybe={counters['recommend_maybe']}"
            )

            lines_plain: list[str] = [
                f"[{_ts()}] indeed_daily_search",
                f"stage: {stage or 'running'}",
                f"progress: {percent:.1f}%",
                f"time: elapsed {_format_duration_hhmm(elapsed_sec)} / expected {_format_duration_hhmm(expected_total_sec)}",
                f"start/end: {_format_local_clock(start_wall)} -> {_format_local_clock(projected_end)}",
                f"timeout at: {_format_local_clock(timeout_wall)}",
                f"query: {query_text}",
                f"result: {result_text}",
                *worker_lines,
                f"rate: {rate_text}",
                decisions_line,
            ]
            if log_path is not None:
                lines_plain.append(f"log file: {log_path}")
            lines_plain.append("")

            lines: list[str] = [
                lines_plain[0],
                lines_plain[1],
                f"progress: {blue}{percent:.1f}%{reset}",
                (
                    f"time: {yellow}elapsed {_format_duration_hhmm(elapsed_sec)} / "
                    f"expected {_format_duration_hhmm(expected_total_sec)}{reset}"
                ),
                f"start/end: {yellow}{_format_local_clock(start_wall)} -> {_format_local_clock(projected_end)}{reset}",
                f"timeout at: {yellow}{_format_local_clock(timeout_wall)}{reset}",
                f"query: {query_text}",
                f"result: {result_text}",
                *worker_lines,
                f"rate: {rate_text}",
                decisions_line,
            ]
            if log_path is not None:
                lines.append(f"log file: {log_path}")
            lines.append("")

            block = "\n".join(lines)
            now = monotonic()
            if block == last_print["text"] and (now - float(last_print["at"])) < 0.4:
                return
            if use_live_rewrite and int(last_print["lines"]) > 0:
                clear = "\x1b[F\x1b[2K" * int(last_print["lines"])
                sys.stdout.write(clear)
            sys.stdout.write(block)
            sys.stdout.flush()
            last_print["text"] = block
            last_print["at"] = now
            last_print["lines"] = len(lines)
            if log_path is not None:
                try:
                    with log_path.open("a", encoding="utf-8") as fh:
                        fh.write("\n".join(lines_plain))
                        fh.write("\n")
                except Exception:
                    pass

    return _callback


def main() -> int:
    task_id = str(os.getenv("ZUBOT_TASK_ID", "indeed_daily_search")).strip() or "indeed_daily_search"
    payload = _safe_json_env("ZUBOT_TASK_PAYLOAD_JSON")
    local_cfg = _safe_json_env("ZUBOT_TASK_LOCAL_CONFIG_JSON")
    task_profile = _safe_json_env("ZUBOT_TASK_PROFILE_JSON")
    resources_dir_raw = str(os.getenv("ZUBOT_TASK_RESOURCES_DIR", "")).strip()
    resources_dir = Path(resources_dir_raw) if resources_dir_raw else Path(__file__).resolve().parent
    timeout_raw = task_profile.get("timeout_sec") if isinstance(task_profile, dict) else None
    timeout_sec = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else None
    if timeout_sec is None:
        local_timeout = local_cfg.get("task_timeout_sec")
        timeout_sec = int(local_timeout) if isinstance(local_timeout, int) and local_timeout > 0 else 1800

    progress_callback = _progress_callback_from_env(
        log_path=_run_log_path(resources_dir),
        timeout_sec=timeout_sec,
    )
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
