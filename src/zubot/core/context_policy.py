"""Context scoring and budget-aware selection policy."""

from __future__ import annotations

import re
from typing import Any

from .context_state import ContextItem

_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")

PRIORITY_SCORES = {
    "base": 120,
    "summary": 95,
    "fact": 90,
    "artifact": 75,
    "recent": 70,
    "supplemental": 40,
}


def _query_tokens(query: str) -> set[str]:
    return set(_TOKEN_RE.findall(query.lower()))


def score_context_item(
    item: ContextItem,
    *,
    query: str = "",
    current_turn: int | None = None,
) -> int:
    score = PRIORITY_SCORES.get(item.priority, 30)
    if item.pinned:
        score += 1000

    if current_turn is not None and item.last_used_turn is not None:
        age = max(0, current_turn - item.last_used_turn)
        score += max(0, 25 - age)

    q_tokens = _query_tokens(query)
    if q_tokens:
        haystack = f"{item.source_id} {item.content[:2500]}".lower()
        relevance_hits = sum(1 for token in q_tokens if token in haystack)
        score += min(40, relevance_hits * 8)

    return score


def select_items_for_budget(
    items: list[ContextItem],
    *,
    max_input_tokens: int,
    query: str = "",
    current_turn: int | None = None,
    required_priorities: set[str] | None = None,
) -> dict[str, Any]:
    if max_input_tokens < 0:
        raise ValueError("max_input_tokens must be >= 0")

    required = required_priorities or {"base", "summary", "fact"}
    required_items: list[ContextItem] = []
    optional_items: list[ContextItem] = []

    for item in items:
        if item.pinned or item.priority in required:
            required_items.append(item)
        else:
            optional_items.append(item)

    required_items.sort(key=lambda item: item.source_id)
    optional_items.sort(
        key=lambda item: (
            -score_context_item(item, query=query, current_turn=current_turn),
            item.source_id,
        )
    )

    kept: list[ContextItem] = []
    dropped: list[ContextItem] = []
    total_tokens = 0

    for item in required_items:
        kept.append(item)
        total_tokens += max(0, int(item.token_estimate))

    for item in optional_items:
        item_tokens = max(0, int(item.token_estimate))
        if total_tokens + item_tokens <= max_input_tokens:
            kept.append(item)
            total_tokens += item_tokens
        else:
            dropped.append(item)

    within_budget = total_tokens <= max_input_tokens
    over_budget_by = max(0, total_tokens - max_input_tokens)
    return {
        "kept": kept,
        "dropped": dropped,
        "kept_source_ids": [item.source_id for item in kept],
        "dropped_source_ids": [item.source_id for item in dropped],
        "input_tokens": total_tokens,
        "max_input_tokens": max_input_tokens,
        "within_budget": within_budget,
        "over_budget_by": over_budget_by,
    }
