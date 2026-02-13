"""Capacity policy helpers for task-agent initiated worker dispatch."""

from __future__ import annotations


def can_dispatch_task_agent_worker(*, running_count: int, max_concurrent_workers: int, reserve_for_workers: int) -> bool:
    """Return True when task-agent dispatch may consume a worker slot.

    A hard reserve keeps `reserve_for_workers` slots available for direct worker usage.
    """
    if max_concurrent_workers <= 0:
        return False
    reserve = max(0, reserve_for_workers)
    usable = max(0, max_concurrent_workers - reserve)
    return running_count < usable
