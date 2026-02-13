"""Background worker for queued daily-summary jobs."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, RLock, Thread
from typing import Any

from .config_loader import load_config
from .daily_summary_pipeline import process_pending_summary_jobs
from .memory_index import ensure_memory_index_schema


@dataclass(slots=True)
class MemorySummaryWorkerSettings:
    poll_interval_sec: int = 15
    max_jobs_per_tick: int = 1


class MemorySummaryWorker:
    """Owns a single daemon thread that drains summary jobs."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self._settings = MemorySummaryWorkerSettings()
        self._last_result: dict[str, Any] | None = None

    @staticmethod
    def _load_settings_from_config() -> MemorySummaryWorkerSettings:
        try:
            cfg = load_config()
        except Exception:
            return MemorySummaryWorkerSettings()
        memory = cfg.get("memory") if isinstance(cfg, dict) else None
        if not isinstance(memory, dict):
            return MemorySummaryWorkerSettings()

        poll = memory.get("summary_worker_poll_sec")
        max_jobs = memory.get("summary_worker_max_jobs_per_tick")
        return MemorySummaryWorkerSettings(
            poll_interval_sec=int(poll) if isinstance(poll, int) and poll > 0 else 15,
            max_jobs_per_tick=int(max_jobs) if isinstance(max_jobs, int) and max_jobs > 0 else 1,
        )

    def start(self, *, settings: MemorySummaryWorkerSettings | None = None) -> dict[str, Any]:
        ensure_memory_index_schema()
        with self._lock:
            if settings is not None:
                self._settings = settings
            else:
                self._settings = self._load_settings_from_config()
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "running": True, "already_running": True}
            self._stop.clear()
            self._wake.clear()
            self._thread = Thread(target=self._run_loop, daemon=True, name="zubot-memory-summary-worker")
            self._thread.start()
        return {"ok": True, "running": True, "already_running": False}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
        return {"ok": True, "running": False}

    def kick(self) -> dict[str, Any]:
        self._wake.set()
        return {"ok": True, "kicked": True}

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            settings = {
                "poll_interval_sec": int(self._settings.poll_interval_sec),
                "max_jobs_per_tick": int(self._settings.max_jobs_per_tick),
            }
            last_result = dict(self._last_result or {})
        return {"ok": True, "running": running, "settings": settings, "last_result": last_result}

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=max(1, int(self._settings.poll_interval_sec)))
            self._wake.clear()
            if self._stop.is_set():
                break
            out = process_pending_summary_jobs(max_jobs=max(1, int(self._settings.max_jobs_per_tick)))
            with self._lock:
                self._last_result = out


_MEMORY_SUMMARY_WORKER: MemorySummaryWorker | None = None


def get_memory_summary_worker() -> MemorySummaryWorker:
    global _MEMORY_SUMMARY_WORKER
    if _MEMORY_SUMMARY_WORKER is None:
        _MEMORY_SUMMARY_WORKER = MemorySummaryWorker()
    return _MEMORY_SUMMARY_WORKER
