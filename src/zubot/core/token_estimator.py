"""Heuristic token estimation and context budgeting utilities."""

from __future__ import annotations

import json
from typing import Any

from .config_loader import get_model_config, load_config


def estimate_text_tokens(text: str) -> int:
    """Approximate token count for plain text."""
    if not text:
        return 0
    # Conservative heuristic for English-ish text.
    return max(1, int(len(text) / 3.6))


def estimate_payload_tokens(payload: Any) -> int:
    if isinstance(payload, str):
        return estimate_text_tokens(payload)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return estimate_text_tokens(serialized)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += 6  # per-message framing overhead
        total += estimate_payload_tokens(message)
    return total


def get_model_token_limits(model: str | None = None) -> dict[str, int]:
    payload = load_config()
    _, model_cfg = get_model_config(model, payload)
    max_context = int(model_cfg.get("max_context_tokens", 0))
    max_output = int(model_cfg.get("max_output_tokens", 0))
    if max_context <= 0:
        raise ValueError("Model max_context_tokens must be configured as a positive integer.")
    if max_output <= 0:
        raise ValueError("Model max_output_tokens must be configured as a positive integer.")
    return {
        "max_context_tokens": max_context,
        "max_output_tokens": max_output,
    }


def compute_budget(
    *,
    input_tokens: int,
    max_context_tokens: int,
    reserved_output_tokens: int,
) -> dict[str, Any]:
    if max_context_tokens <= 0:
        raise ValueError("max_context_tokens must be > 0")
    if reserved_output_tokens < 0:
        raise ValueError("reserved_output_tokens must be >= 0")

    available_for_input = max(0, max_context_tokens - reserved_output_tokens)
    remaining_input_tokens = max(0, available_for_input - input_tokens)
    fill_ratio = input_tokens / max_context_tokens

    level = "ok"
    if fill_ratio >= 0.95:
        level = "critical"
    elif fill_ratio >= 0.85:
        level = "high"
    elif fill_ratio >= 0.70:
        level = "medium"

    return {
        "input_tokens": input_tokens,
        "max_context_tokens": max_context_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "available_for_input": available_for_input,
        "remaining_input_tokens": remaining_input_tokens,
        "fill_ratio": fill_ratio,
        "fill_level": level,
        "within_budget": input_tokens <= available_for_input,
    }
