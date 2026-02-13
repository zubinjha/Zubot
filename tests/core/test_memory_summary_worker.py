from src.zubot.core.memory_summary_worker import MemorySummaryWorker, MemorySummaryWorkerSettings


def test_memory_summary_worker_start_kick_stop(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr("src.zubot.core.memory_summary_worker.ensure_memory_index_schema", lambda: None)

    def fake_process_pending_summary_jobs(*, max_jobs=1, session_id="memory_summary_worker", root=None):
        _ = (max_jobs, session_id, root)
        calls["n"] += 1
        return {"ok": True, "processed": 0, "completed": 0, "failed": 0, "jobs": []}

    monkeypatch.setattr(
        "src.zubot.core.memory_summary_worker.process_pending_summary_jobs",
        fake_process_pending_summary_jobs,
    )

    worker = MemorySummaryWorker()
    started = worker.start(settings=MemorySummaryWorkerSettings(poll_interval_sec=999, max_jobs_per_tick=1))
    assert started["ok"] is True
    assert started["running"] is True
    worker.kick()
    # Allow one wake cycle.
    import time

    deadline = time.time() + 0.5
    while time.time() < deadline and calls["n"] == 0:
        time.sleep(0.01)
    assert calls["n"] >= 1
    stopped = worker.stop()
    assert stopped["ok"] is True
