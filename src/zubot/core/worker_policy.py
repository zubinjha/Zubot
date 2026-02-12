"""Policies for main <-> worker event forwarding."""

from __future__ import annotations

from typing import Any


def should_forward_worker_event_to_user(event: dict[str, Any], main_context: dict[str, Any] | None = None) -> bool:
    """v1 policy: always forward worker events to the user via main agent."""
    _ = main_context
    _ = event
    return True
