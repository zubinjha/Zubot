import threading
import time
from uuid import uuid4

from src.zubot.core.provider_queue import execute_provider_call, provider_queue_stats


def _group(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def test_provider_queue_serializes_calls_per_group():
    group = _group("provider_serial")
    guard = threading.Lock()
    active = {"count": 0, "peak": 0}
    outputs: list[dict] = []

    def _call() -> dict:
        with guard:
            active["count"] += 1
            active["peak"] = max(active["peak"], active["count"])
        time.sleep(0.05)
        with guard:
            active["count"] -= 1
        return {"ok": True}

    def _run_one() -> None:
        out = execute_provider_call(group=group, fn=_call)
        outputs.append(out)

    t1 = threading.Thread(target=_run_one)
    t2 = threading.Thread(target=_run_one)
    t1.start()
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert len(outputs) == 2
    assert outputs[0]["ok"] is True
    assert outputs[1]["ok"] is True
    assert active["peak"] == 1
    stats = provider_queue_stats(group)
    assert stats["calls_total"] == 2
    assert stats["calls_success"] == 2
    assert stats["calls_failed"] == 0


def test_provider_queue_retries_retryable_failures():
    group = _group("provider_retry")
    attempts = {"n": 0}

    def _flaky() -> dict:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    out = execute_provider_call(
        group=group,
        fn=_flaky,
        max_retries=3,
        retry_backoff_sec=0.0,
        is_retryable=lambda exc: isinstance(exc, RuntimeError),
    )
    assert out["ok"] is True
    assert attempts["n"] == 3
    assert out["queue"]["attempt"] == 3

    stats = provider_queue_stats(group)
    assert stats["calls_success"] == 1
    assert stats["calls_failed"] == 0


def test_provider_queue_records_wait_metrics_with_min_interval():
    group = _group("provider_wait")
    first = execute_provider_call(group=group, fn=lambda: {"ok": True}, min_interval_sec=0.0)
    second = execute_provider_call(group=group, fn=lambda: {"ok": True}, min_interval_sec=0.05, jitter_sec=0.0)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["queue"]["wait_sec"] >= 0.04

    stats = provider_queue_stats(group)
    assert stats["wait_sec_last"] >= 0.04
    assert stats["wait_sec_max"] >= stats["wait_sec_last"]
    assert stats["wait_sec_avg"] > 0.0
