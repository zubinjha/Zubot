from __future__ import annotations

from threading import Event
from time import sleep
from typing import Any

from src.zubot.core.worker_manager import WorkerManager


class _BlockingRunner:
    def __init__(self, gate: Event) -> None:
        self._gate = gate

    def run_task(self, task, **_kwargs) -> dict[str, Any]:  # noqa: ANN001
        self._gate.wait(timeout=2.0)
        return {
            "ok": True,
            "result": {
                "task_id": task.task_id,
                "status": "success",
                "summary": f"done: {task.instructions}",
                "artifacts": [],
                "error": None,
                "trace": [],
            },
            "session_summary": "worker summary",
            "facts": {"last_task": task.instructions},
        }


def test_worker_manager_enforces_cap_and_queues():
    gate = Event()
    manager = WorkerManager(runner=_BlockingRunner(gate), max_concurrent_workers=3)

    ids: list[str] = []
    for idx in range(4):
        out = manager.spawn_worker(title=f"t{idx}", instructions=f"task {idx}")
        assert out["ok"] is True
        ids.append(out["worker"]["worker_id"])

    listed = manager.list_workers()
    assert listed["runtime"]["running_count"] == 3
    assert listed["runtime"]["queued_count"] == 1

    gate.set()
    assert manager.wait_for_idle(timeout_sec=2.0) is True
    for wid in ids:
        worker = manager.get_worker(wid)
        assert worker["ok"] is True
        assert worker["worker"]["status"] == "done"


def test_worker_manager_cancel_queued_worker():
    gate = Event()
    manager = WorkerManager(runner=_BlockingRunner(gate), max_concurrent_workers=1)
    first = manager.spawn_worker(title="first", instructions="run first")
    second = manager.spawn_worker(title="second", instructions="queued second")
    second_id = second["worker"]["worker_id"]

    cancelled = manager.cancel_worker(second_id)
    assert cancelled["ok"] is True
    assert cancelled["worker"]["status"] == "cancelled"

    gate.set()
    assert manager.wait_for_idle(timeout_sec=2.0) is True


def test_worker_manager_message_and_reset_context():
    class _Runner:
        def run_task(self, task, **_kwargs):  # noqa: ANN001
            return {
                "ok": True,
                "result": {
                    "task_id": task.task_id,
                    "status": "success",
                    "summary": "ok",
                    "artifacts": [],
                    "error": None,
                    "trace": [],
                },
                "session_summary": "summary",
                "facts": {"foo": "bar"},
            }

    manager = WorkerManager(runner=_Runner(), max_concurrent_workers=1)
    spawned = manager.spawn_worker(title="w", instructions="first")
    wid = spawned["worker"]["worker_id"]
    assert manager.wait_for_idle(timeout_sec=1.0) is True

    message = manager.message_worker(worker_id=wid, message="second")
    assert message["ok"] is True
    assert manager.wait_for_idle(timeout_sec=1.0) is True

    worker_before_reset = manager.get_worker(wid)["worker"]
    assert worker_before_reset["session_summary_present"] is False
    assert worker_before_reset["fact_count"] == 0

    reset = manager.reset_worker_context(wid)
    assert reset["ok"] is True
    worker_after_reset = manager.get_worker(wid)["worker"]
    assert worker_after_reset["session_summary_present"] is False
    assert worker_after_reset["fact_count"] == 0

    # Allow async state to settle if needed in CI.
    sleep(0.01)


def test_worker_context_preload_is_applied_once(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_load_base_context(*, files=None, **_kwargs):
        if files == ["context/KERNEL.md"]:
            return {"context/KERNEL.md": "kernel"}
        return {"context/custom.md": "custom preload"}

    class _Runner:
        def run_task(self, task, **kwargs):  # noqa: ANN001
            captured["supplemental"] = kwargs.get("supplemental_context")
            return {
                "ok": True,
                "result": {
                    "task_id": task.task_id,
                    "status": "success",
                    "summary": "ok",
                    "artifacts": [],
                    "error": None,
                    "trace": [],
                },
            }

    monkeypatch.setattr("src.zubot.core.worker_manager.load_base_context", fake_load_base_context)
    manager = WorkerManager(runner=_Runner(), max_concurrent_workers=1)
    out = manager.spawn_worker(title="preload", instructions="task", preload_files=["context/custom.md"])
    assert out["ok"] is True
    assert manager.wait_for_idle(timeout_sec=1.0) is True
    assert captured["supplemental"] == {"context/custom.md": "custom preload"}


def test_worker_context_disposed_on_cancel():
    gate = Event()
    manager = WorkerManager(runner=_BlockingRunner(gate), max_concurrent_workers=1)
    spawned = manager.spawn_worker(title="cancel-me", instructions="work")
    wid = spawned["worker"]["worker_id"]
    out = manager.cancel_worker(wid)
    assert out["ok"] is True
    worker = manager.get_worker(wid)["worker"]
    assert worker["status"] in {"cancelled", "running"}
    gate.set()
    assert manager.wait_for_idle(timeout_sec=2.0) is True
    worker = manager.get_worker(wid)["worker"]
    assert worker["status"] == "cancelled"
    assert worker["session_summary_present"] is False
    assert worker["fact_count"] == 0


def test_worker_forward_events_are_consumed_once():
    class _Runner:
        def run_task(self, task, **_kwargs):  # noqa: ANN001
            return {
                "ok": True,
                "result": {
                    "task_id": task.task_id,
                    "status": "success",
                    "summary": "done",
                    "artifacts": [],
                    "error": None,
                    "trace": [],
                },
            }

    manager = WorkerManager(runner=_Runner(), max_concurrent_workers=1)
    out = manager.spawn_worker(title="events", instructions="work")
    assert out["ok"] is True
    assert manager.wait_for_idle(timeout_sec=1.0) is True

    first = manager.list_forward_events(consume=True)
    assert first["ok"] is True
    assert first["count"] >= 1
    second = manager.list_forward_events(consume=True)
    assert second["ok"] is True
    assert second["count"] == 0
