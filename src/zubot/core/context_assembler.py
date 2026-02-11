"""Assemble model-ready messages from context + session state."""

from __future__ import annotations

from typing import Any

from .token_estimator import compute_budget, estimate_messages_tokens


def _event_to_message(event: dict[str, Any]) -> dict[str, str] | None:
    event_type = event.get("event_type")
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    if event_type == "user_message":
        text = payload.get("text")
        if isinstance(text, str):
            return {"role": "user", "content": text}
    if event_type == "assistant_message":
        text = payload.get("text")
        if isinstance(text, str):
            return {"role": "assistant", "content": text}
    if event_type == "tool_result":
        return {"role": "assistant", "content": f"Tool result: {payload}"}
    if event_type == "worker_complete":
        return {"role": "assistant", "content": f"Worker result: {payload}"}
    return None


def assemble_messages(
    *,
    context_bundle: dict[str, Any],
    recent_events: list[dict[str, Any]],
    session_summary: str | None = None,
    max_context_tokens: int | None = None,
    reserved_output_tokens: int | None = None,
) -> dict[str, Any]:
    """Build ordered message list and apply optional budget-aware trimming."""
    messages: list[dict[str, str]] = []

    base = context_bundle.get("base", {})
    if isinstance(base, dict):
        for path in sorted(base.keys()):
            text = base[path]
            if isinstance(text, str) and text.strip():
                messages.append(
                    {
                        "role": "system",
                        "content": f"[BaseContext:{path}]\n{text}",
                    }
                )

    if isinstance(session_summary, str) and session_summary.strip():
        messages.append({"role": "system", "content": f"[SessionSummary]\n{session_summary}"})

    supplemental = context_bundle.get("supplemental", {})
    optional_messages: list[dict[str, str]] = []
    if isinstance(supplemental, dict):
        for path in sorted(supplemental.keys()):
            text = supplemental[path]
            if isinstance(text, str) and text.strip():
                optional_messages.append(
                    {
                        "role": "system",
                        "content": f"[SupplementalContext:{path}]\n{text}",
                    }
                )

    for event in recent_events:
        if not isinstance(event, dict):
            continue
        msg = _event_to_message(event)
        if msg:
            messages.append(msg)

    # Optional messages are inserted before recent dialog; trim by priority if needed.
    insertion_index = len([m for m in messages if m["role"] == "system"])
    for msg in optional_messages:
        messages.insert(insertion_index, msg)
        insertion_index += 1

    token_estimate = estimate_messages_tokens(messages)
    budget = None
    removed_optional = 0

    if max_context_tokens is not None and reserved_output_tokens is not None:
        budget = compute_budget(
            input_tokens=token_estimate,
            max_context_tokens=max_context_tokens,
            reserved_output_tokens=reserved_output_tokens,
        )
        while optional_messages and not budget["within_budget"]:
            # Remove lowest-priority optional context (last inserted optional message).
            removed = optional_messages.pop()
            messages = [m for m in messages if m is not removed]
            removed_optional += 1
            token_estimate = estimate_messages_tokens(messages)
            budget = compute_budget(
                input_tokens=token_estimate,
                max_context_tokens=max_context_tokens,
                reserved_output_tokens=reserved_output_tokens,
            )

    return {
        "messages": messages,
        "token_estimate": token_estimate,
        "budget": budget,
        "removed_optional_context_messages": removed_optional,
    }
