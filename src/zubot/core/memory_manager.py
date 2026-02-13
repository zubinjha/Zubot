"""Background memory finalization helpers for long-running runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any

from .daily_memory import local_day_str
from .daily_summary_pipeline import summarize_day_from_raw
from .memory_index import ensure_memory_index_schema, get_days_pending_summary


@dataclass(slots=True)
class MemoryManagerSettings:
    sweep_interval_sec: int = 12 * 60 * 60
    completion_debounce_sec: int = 5 * 60


class MemoryManager:
    """Periodic and completion-triggered summary/finalization sweeps."""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root
        self._lock = RLock()
        self._last_sweep_mono: float | None = None
        self._last_completion_sweep_mono: float | None = None

    def sweep_pending_previous_days(self, *, session_id: str = "central_service") -> dict[str, Any]:
        ensure_memory_index_schema(root=self._root)
        today = local_day_str()
        pending = get_days_pending_summary(before_day=today, root=self._root)
        finalized_days: list[str] = []

        for day in pending:
            day_key = str(day.get("day") or "").strip()
            if not day_key:
                continue
            out = summarize_day_from_raw(
                day=day_key,
                reason="memory_manager_sweep",
                session_id=session_id,
                finalize=True,
                root=self._root,
            )
            if out.get("ok"):
                finalized_days.append(day_key)

        return {
            "ok": True,
            "source": "memory_manager",
            "finalized_count": len(finalized_days),
            "finalized_days": finalized_days,
        }

    def maybe_periodic_sweep(self, *, settings: MemoryManagerSettings) -> dict[str, Any]:
        now_mono = monotonic()
        with self._lock:
            if self._last_sweep_mono is not None and now_mono - self._last_sweep_mono < max(1, settings.sweep_interval_sec):
                return {"ok": True, "skipped": True, "reason": "interval_not_elapsed"}
            out = self.sweep_pending_previous_days()
            self._last_sweep_mono = now_mono
            return out

    def maybe_completion_sweep(self, *, settings: MemoryManagerSettings) -> dict[str, Any]:
        now_mono = monotonic()
        with self._lock:
            if (
                self._last_completion_sweep_mono is not None
                and now_mono - self._last_completion_sweep_mono < max(1, settings.completion_debounce_sec)
            ):
                return {"ok": True, "skipped": True, "reason": "completion_debounce"}
            out = self.sweep_pending_previous_days()
            self._last_completion_sweep_mono = now_mono
            self._last_sweep_mono = now_mono
            return out
