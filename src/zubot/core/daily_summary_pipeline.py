"""Queued daily-summary generation from DB-backed raw memory events."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config_loader import load_config
from .daily_memory import list_day_raw_entries, local_day_str, write_daily_summary_snapshot
from .llm_client import call_llm
from .memory_index import (
    claim_next_day_summary_job,
    complete_day_summary_job,
    mark_day_summarized,
)
from .token_estimator import estimate_text_tokens

SUMMARY_MAX_INPUT_TOKENS = 4000
SUMMARY_MAX_RECURSION_DEPTH = 6


def _clean_text(value: str, *, max_chars: int = 2000) -> str:
    return " ".join(value.strip().split())[:max_chars]


def _daily_summary_model_enabled() -> bool:
    try:
        cfg = load_config()
    except Exception:
        return False
    memory_cfg = cfg.get("memory")
    if not isinstance(memory_cfg, dict):
        return False
    return bool(memory_cfg.get("daily_summary_use_model", False))


def _entry_to_line(entry: dict[str, Any]) -> str:
    speaker = str(entry.get("speaker", "unknown"))
    route = str(entry.get("route", "unknown"))
    text = str(entry.get("text", ""))
    return f"- [{speaker}] route={route} text={text}"


def _entries_token_estimate(entries: list[dict[str, Any]]) -> int:
    text = "\n".join(_entry_to_line(entry) for entry in entries)
    return estimate_text_tokens(text)


def _split_entries_recursive(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mid = max(1, len(entries) // 2)
    return entries[:mid], entries[mid:]


def _parse_route_from_text(text: str) -> str:
    for token in text.split():
        if token.startswith("route="):
            route = token[6:].strip()
            if route:
                return route
    return "daily.raw"


def _load_day_raw_entries(*, day: str, root: Path | None = None) -> list[dict[str, Any]]:
    rows = list_day_raw_entries(day=day, root=root)
    out: list[dict[str, Any]] = []
    for row in rows:
        kind = _clean_text(str(row.get("kind") or "unknown"), max_chars=80) or "unknown"
        text = _clean_text(str(row.get("text") or ""), max_chars=4000)
        if not text:
            continue
        out.append({"day": day, "speaker": kind, "route": _parse_route_from_text(text), "text": text})
    return out


def _is_low_signal(entry: dict[str, Any]) -> bool:
    route = str(entry.get("route", "")).strip().lower()
    speaker = str(entry.get("speaker", "")).strip().lower()
    text = " ".join(str(entry.get("text", "")).strip().lower().split())

    # Keep user/main/task interactions as primary memory signal.
    if speaker in {"user", "main_agent", "task_agent_event"}:
        return False

    # Tool/system chatter is usually low signal unless there is an explicit failure.
    if speaker in {"system", "tool_event"}:
        return ("error=" not in text) and ("ok=false" not in text) and ("failed" not in text)

    if speaker == "worker_event":
        return not any(token in text for token in ("failed", "error", "blocked", "completed", "done"))

    if route.startswith("llm.error_fallback"):
        return True
    if len(text) < 24:
        return True

    low_signal_markers = {
        "thanks",
        "thank you",
        "ok",
        "okay",
        "cool",
        "nice",
        "yes",
        "no",
        "sounds good",
        "got it",
    }
    return any(marker in text for marker in low_signal_markers)


def _summarize_entries_batch(entries: list[dict[str, Any]]) -> str:
    signal_entries = [entry for entry in entries if not _is_low_signal(entry)]
    entries_for_summary = signal_entries or entries

    route_counts: dict[str, int] = {}
    for entry in entries_for_summary:
        route = str(entry.get("route", "unknown"))
        route_counts[route] = route_counts.get(route, 0) + 1

    raw_lines = "\n".join(_entry_to_line(entry) for entry in entries_for_summary)[:12000]
    prompt = (
        "Summarize this raw daily transcript into compact daily memory bullets.\n"
        "Transcript format:\n"
        "- [user] text from human\n"
        "- [main_agent] assistant reply\n"
        "- [worker_event] worker-to-main event payload\n"
        "- [task_agent_event] central scheduler/task-agent lifecycle event\n"
        "- [tool_event] significant tool or integration event\n"
        "- [system] orchestration/runtime status event\n\n"
        "Requirements:\n"
        "- Focus on meaningful work only: what was done conceptually, how it was done, and the outcome.\n"
        "- Do not include idle chat, acknowledgments, or repetitive low-signal exchanges.\n"
        "- Include decisions, design choices, and concrete progress state.\n"
        "- Mention worker activity only when it materially changed progress.\n"
        "- Include next step only if explicit.\n"
        "- Keep it concise and factual.\n\n"
        f"Transcript:\n{raw_lines}"
    )
    if _daily_summary_model_enabled():
        llm = call_llm(
            model="low",
            max_output_tokens=220,
            messages=[
                {"role": "system", "content": "You write compact, practical memory summaries."},
                {"role": "user", "content": prompt},
            ],
        )
        if llm.get("ok") and isinstance(llm.get("text"), str) and llm["text"].strip():
            return " ".join(llm["text"].strip().split())

    route_summary = ", ".join(f"{route} x{count}" for route, count in sorted(route_counts.items()))
    highlights = "; ".join(
        f"{entry.get('speaker', 'unknown')}='{str(entry.get('text', ''))[:90]}' -> {entry.get('route', 'unknown')}"
        for entry in entries_for_summary[-3:]
    )
    return (
        f"- Signal entries: {len(entries_for_summary)} of {len(entries)}\n"
        f"- Routes: {route_summary}\n"
        f"- Highlights: {highlights}"
    )


def summarize_entries(entries: list[dict[str, Any]], *, depth: int = 0) -> str:
    if not entries:
        return "- No daily transcript entries to summarize."
    if not _daily_summary_model_enabled():
        return _summarize_entries_batch(entries)
    if depth >= SUMMARY_MAX_RECURSION_DEPTH:
        return _summarize_entries_batch(entries)

    estimated = _entries_token_estimate(entries)
    if estimated <= SUMMARY_MAX_INPUT_TOKENS or len(entries) <= 4:
        return _summarize_entries_batch(entries)

    left, right = _split_entries_recursive(entries)
    left_summary = summarize_entries(left, depth=depth + 1)
    right_summary = summarize_entries(right, depth=depth + 1)
    merge_entries = [
        {"speaker": "segment_summary", "route": "summary.segment", "text": f"segment_left: {left_summary}"},
        {"speaker": "segment_summary", "route": "summary.segment", "text": f"segment_right: {right_summary}"},
    ]
    return _summarize_entries_batch(merge_entries)


def summarize_day_from_raw(
    *,
    day: str,
    reason: str,
    session_id: str = "memory_summary",
    finalize: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    entries = _load_day_raw_entries(day=day, root=root)
    summary_text = summarize_entries(entries)
    write = write_daily_summary_snapshot(
        day_str=day,
        session_id=session_id,
        text=(
            f"- Summary reason: {reason}\n"
            f"- Day event entries: {len(entries)}\n"
            f"{summary_text}"
        ),
        root=root,
    )
    if not write.get("ok"):
        return {
            "ok": False,
            "source": "daily_summary_pipeline",
            "day": day,
            "error": write.get("error") or "summary_write_failed",
        }
    status = mark_day_summarized(day=day, summarized_messages=len(entries), finalize=finalize, root=root)
    return {
        "ok": True,
        "source": "daily_summary_pipeline",
        "day": day,
        "summary_entries": len(entries),
        "summary_text": summary_text,
        "status": status,
        "finalize": finalize,
    }


def process_pending_summary_jobs(
    *,
    max_jobs: int = 1,
    session_id: str = "memory_summary_worker",
    root: Path | None = None,
) -> dict[str, Any]:
    safe_max = max(1, int(max_jobs))
    processed = 0
    completed = 0
    failed = 0
    jobs: list[dict[str, Any]] = []
    today = local_day_str()

    while processed < safe_max:
        claimed = claim_next_day_summary_job(root=root)
        if claimed is None:
            break

        processed += 1
        day = str(claimed.get("day") or "").strip()
        reason = str(claimed.get("reason") or "queued")
        job_id = int(claimed.get("job_id") or 0)
        should_finalize = bool(day and day < today)

        try:
            out = summarize_day_from_raw(
                day=day,
                reason=f"queued:{reason}",
                session_id=session_id,
                finalize=should_finalize,
                root=root,
            )
            if out.get("ok"):
                complete_day_summary_job(job_id=job_id, ok=True, root=root)
                completed += 1
            else:
                err = str(out.get("error") or "summary_failed")
                complete_day_summary_job(job_id=job_id, ok=False, error=err, root=root)
                failed += 1
            jobs.append({"job_id": job_id, "day": day, "ok": bool(out.get("ok")), "error": out.get("error")})
        except Exception as exc:
            complete_day_summary_job(job_id=job_id, ok=False, error=str(exc), root=root)
            failed += 1
            jobs.append({"job_id": job_id, "day": day, "ok": False, "error": str(exc)})

    return {
        "ok": True,
        "source": "daily_summary_pipeline",
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "jobs": jobs,
    }
