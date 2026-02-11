"""Rolling summary helpers for compacting older conversation events."""

from __future__ import annotations

from typing import Any


def _event_line(event: dict[str, Any]) -> str | None:
    event_type = event.get("event_type")
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    if event_type in {"user_message", "assistant_message"}:
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            prefix = "user" if event_type == "user_message" else "assistant"
            return f"- {prefix}: {text.strip()[:220]}"

    if event_type == "tool_result":
        return "- tool_result captured"
    if event_type == "worker_complete":
        return "- worker_result captured"
    return None


def summarize_events(events: list[dict[str, Any]], *, max_lines: int = 12) -> str:
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        line = _event_line(event)
        if not line:
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines).strip()


def build_rolling_summary(
    *,
    existing_summary: str | None,
    overflow_events: list[dict[str, Any]],
    max_chars: int = 2500,
) -> str | None:
    if not overflow_events:
        return existing_summary

    chunk = summarize_events(overflow_events)
    if not chunk:
        return existing_summary

    if existing_summary and existing_summary.strip():
        merged = f"{existing_summary.strip()}\n\n[CompactedHistory]\n{chunk}"
    else:
        merged = f"[CompactedHistory]\n{chunk}"

    if len(merged) <= max_chars:
        return merged

    # Keep tail-most summary content; newest compaction is appended last.
    return merged[-max_chars:]
