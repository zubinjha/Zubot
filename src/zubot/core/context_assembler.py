"""Assemble model-ready messages from context + session state."""

from __future__ import annotations

from typing import Any

from .context_policy import select_items_for_budget
from .context_state import ContextItem
from .fact_memory import extract_facts_from_events
from .summary_memory import build_rolling_summary
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


def _compact_recent_events(
    recent_events: list[dict[str, Any]],
    *,
    max_recent_tokens: int,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Keep newest recent events that fit budget; return dropped older events."""
    if max_recent_tokens <= 0:
        return [], recent_events

    candidates: list[tuple[dict[str, Any], dict[str, str], int]] = []
    for event in recent_events:
        if not isinstance(event, dict):
            continue
        msg = _event_to_message(event)
        if not msg:
            continue
        msg_tokens = estimate_messages_tokens([msg])
        candidates.append((event, msg, msg_tokens))

    if not candidates:
        return [], []

    kept_reversed: list[dict[str, str]] = []
    used = 0
    first_kept_index = len(candidates)

    for idx in range(len(candidates) - 1, -1, -1):
        _, msg, msg_tokens = candidates[idx]
        if used + msg_tokens <= max_recent_tokens:
            kept_reversed.append(msg)
            used += msg_tokens
            first_kept_index = idx
        else:
            break

    kept_messages = list(reversed(kept_reversed))
    dropped_events = [candidates[i][0] for i in range(first_kept_index)]
    return kept_messages, dropped_events


def assemble_messages(
    *,
    context_bundle: dict[str, Any],
    recent_events: list[dict[str, Any]],
    session_summary: str | None = None,
    max_context_tokens: int | None = None,
    reserved_output_tokens: int | None = None,
) -> dict[str, Any]:
    """Build ordered message list and apply optional budget-aware trimming."""
    system_items: list[ContextItem] = []
    recent_messages: list[dict[str, str]] = []
    dropped_recent_events: list[dict[str, Any]] = []

    base = context_bundle.get("base", {})
    if isinstance(base, dict):
        for path in sorted(base.keys()):
            text = base[path]
            if isinstance(text, str) and text.strip():
                system_items.append(
                    ContextItem(
                        source_id=f"base:{path}",
                        content=text,
                        priority="base",
                        metadata={"label": f"BaseContext:{path}"},
                    )
                )

    supplemental = context_bundle.get("supplemental", {})
    if isinstance(supplemental, dict):
        for path in sorted(supplemental.keys()):
            text = supplemental[path]
            if isinstance(text, str) and text.strip():
                system_items.append(
                    ContextItem(
                        source_id=f"supplemental:{path}",
                        content=text,
                        priority="supplemental",
                        metadata={"label": f"SupplementalContext:{path}"},
                    )
                )

    existing_facts = context_bundle.get("facts", {})
    if not isinstance(existing_facts, dict):
        existing_facts = {}
    updated_facts = extract_facts_from_events(recent_events, existing_facts=existing_facts)
    if isinstance(updated_facts, dict):
        for key in sorted(updated_facts.keys()):
            text = updated_facts[key]
            if isinstance(text, str) and text.strip():
                system_items.append(
                    ContextItem(
                        source_id=f"fact:{key}",
                        content=text,
                        priority="fact",
                        metadata={"label": f"Fact:{key}"},
                    )
                )

    summary_text = session_summary.strip() if isinstance(session_summary, str) else None
    if summary_text:
        system_items.append(
            ContextItem(
                source_id="summary:session",
                content=summary_text,
                priority="summary",
                metadata={"label": "SessionSummary"},
            )
        )

    for event in recent_events:
        if isinstance(event, dict):
            msg = _event_to_message(event)
            if msg:
                recent_messages.append(msg)

    selected_system_items = list(system_items)
    budget = None
    removed_optional = 0

    if max_context_tokens is not None and reserved_output_tokens is not None:
        available_for_input = max(0, max_context_tokens - reserved_output_tokens)

        latest_user_query = ""
        for event in reversed(recent_events):
            if isinstance(event, dict) and event.get("event_type") == "user_message":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    text = payload.get("text")
                    if isinstance(text, str):
                        latest_user_query = text
                        break

        # First pass: keep high-value system context under total budget.
        selection = select_items_for_budget(
            system_items,
            max_input_tokens=available_for_input,
            query=latest_user_query,
        )
        selected_system_items = selection["kept"]
        removed_optional = sum(
            1 for item in selection["dropped"] if item.priority == "supplemental"
        )

        selected_system_tokens = sum(int(item.token_estimate) for item in selected_system_items)
        recent_budget = max(0, available_for_input - selected_system_tokens)
        recent_messages, dropped_recent_events = _compact_recent_events(
            recent_events,
            max_recent_tokens=recent_budget,
        )

        updated_summary = build_rolling_summary(
            existing_summary=summary_text,
            overflow_events=dropped_recent_events,
        )
        if updated_summary != summary_text:
            summary_text = updated_summary
            # Replace (or add) rolling summary as a single item.
            selected_system_items = [
                item for item in selected_system_items if item.source_id != "summary:session"
            ]
            if summary_text:
                selected_system_items.append(
                    ContextItem(
                        source_id="summary:session",
                        content=summary_text,
                        priority="summary",
                        metadata={"label": "SessionSummary"},
                    )
                )
                # Re-run selection once with updated summary included.
                reselection = select_items_for_budget(
                    [*selected_system_items],
                    max_input_tokens=available_for_input,
                    query=latest_user_query,
                )
                selected_system_items = reselection["kept"]
                removed_optional = sum(
                    1 for item in reselection["dropped"] if item.priority == "supplemental"
                )
                selected_system_tokens = sum(int(item.token_estimate) for item in selected_system_items)
                recent_budget = max(0, available_for_input - selected_system_tokens)
                recent_messages, dropped_recent_events = _compact_recent_events(
                    recent_events,
                    max_recent_tokens=recent_budget,
                )
    system_messages = [item.to_prompt_message() for item in selected_system_items]
    messages = [*system_messages, *recent_messages]
    token_estimate = estimate_messages_tokens(messages)

    if max_context_tokens is not None and reserved_output_tokens is not None:
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
        "kept_context_source_ids": [item.source_id for item in selected_system_items],
        "kept_recent_message_count": len(recent_messages),
        "dropped_recent_event_count": len(dropped_recent_events),
        "updated_session_summary": summary_text,
        "updated_facts": updated_facts,
    }
