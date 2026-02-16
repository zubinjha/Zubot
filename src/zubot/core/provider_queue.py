"""Provider-level serialized call queues for rate-limited integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from random import uniform
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable


@dataclass
class _GroupState:
    lock: Lock = field(default_factory=Lock)
    meta_lock: Lock = field(default_factory=Lock)
    pending: int = 0
    in_flight: bool = False
    calls_total: int = 0
    calls_success: int = 0
    calls_failed: int = 0
    last_error: str | None = None
    last_started_mono: float | None = None
    last_finished_mono: float | None = None
    wait_sec_total: float = 0.0
    wait_sec_max: float = 0.0
    wait_sec_last: float = 0.0


_GROUPS: dict[str, _GroupState] = {}
_GROUPS_LOCK = Lock()


def _positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _group_state(group: str) -> _GroupState:
    with _GROUPS_LOCK:
        state = _GROUPS.get(group)
        if isinstance(state, _GroupState):
            return state
        state = _GroupState()
        _GROUPS[group] = state
        return state


def provider_queue_stats(group: str) -> dict[str, Any]:
    state = _group_state(group)
    with state.meta_lock:
        avg_wait_sec = float(state.wait_sec_total / state.calls_total) if state.calls_total > 0 else 0.0
        return {
            "group": group,
            "pending": int(state.pending),
            "in_flight": bool(state.in_flight),
            "calls_total": int(state.calls_total),
            "calls_success": int(state.calls_success),
            "calls_failed": int(state.calls_failed),
            "last_error": state.last_error,
            "wait_sec_last": float(state.wait_sec_last),
            "wait_sec_max": float(state.wait_sec_max),
            "wait_sec_avg": avg_wait_sec,
        }


def execute_provider_call(
    *,
    group: str,
    fn: Callable[[], Any],
    min_interval_sec: float = 0.0,
    jitter_sec: float = 0.0,
    max_retries: int = 0,
    retry_backoff_sec: float = 0.0,
    is_retryable: Callable[[Exception], bool] | None = None,
) -> dict[str, Any]:
    state = _group_state(group)
    started_wait = monotonic()
    with state.meta_lock:
        state.pending += 1

    try:
        with state.lock:
            with state.meta_lock:
                state.pending = max(0, int(state.pending) - 1)
                state.in_flight = True
                state.calls_total += 1

            now = monotonic()
            last_finished = state.last_finished_mono
            min_interval = _positive_float(min_interval_sec)
            jitter = _positive_float(jitter_sec)
            if isinstance(last_finished, float) and min_interval > 0:
                wait_more = min_interval - max(0.0, now - last_finished)
                if wait_more > 0:
                    sleep(wait_more)
            if jitter > 0:
                sleep(uniform(0.0, jitter))

            wait_sec = max(0.0, monotonic() - started_wait)
            with state.meta_lock:
                state.wait_sec_last = wait_sec
                state.wait_sec_total += wait_sec
                state.wait_sec_max = max(state.wait_sec_max, wait_sec)
            attempt = 0
            while True:
                with state.meta_lock:
                    state.last_started_mono = monotonic()
                try:
                    value = fn()
                    with state.meta_lock:
                        state.calls_success += 1
                        state.last_error = None
                        state.last_finished_mono = monotonic()
                    return {
                        "ok": True,
                        "value": value,
                        "queue": {
                            "group": group,
                            "wait_sec": wait_sec,
                            "attempt": attempt + 1,
                        },
                    }
                except Exception as exc:  # noqa: PERF203
                    should_retry = bool(attempt < max(0, int(max_retries)))
                    if should_retry and callable(is_retryable):
                        should_retry = bool(is_retryable(exc))
                    if not should_retry:
                        with state.meta_lock:
                            state.calls_failed += 1
                            state.last_error = str(exc)
                            state.last_finished_mono = monotonic()
                        return {
                            "ok": False,
                            "error": str(exc),
                            "queue": {
                                "group": group,
                                "wait_sec": wait_sec,
                                "attempt": attempt + 1,
                            },
                        }
                    delay = _positive_float(retry_backoff_sec)
                    if delay > 0:
                        sleep(delay * (2 ** attempt))
                    attempt += 1
    finally:
        with state.meta_lock:
            state.in_flight = False
