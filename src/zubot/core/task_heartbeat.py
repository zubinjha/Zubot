"""Heartbeat scheduler tick for queueing due task runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .task_scheduler_store import TaskSchedulerStore


@dataclass(slots=True)
class HeartbeatTickResult:
    ok: bool
    enqueued: int
    runs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "enqueued": self.enqueued, "runs": list(self.runs)}


class TaskHeartbeat:
    """Queue due runs without owning execution or dispatch."""

    def __init__(self, *, store: TaskSchedulerStore) -> None:
        self._store = store

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(tz=UTC)

    def enqueue_due_runs(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else self._utc_now()
        out = self._store.enqueue_due_runs(now=now_dt)
        if not isinstance(out, dict):
            return HeartbeatTickResult(ok=False, enqueued=0, runs=[]).to_dict()
        runs = out.get("runs")
        return HeartbeatTickResult(
            ok=bool(out.get("ok")),
            enqueued=int(out.get("enqueued") or 0),
            runs=runs if isinstance(runs, list) else [],
        ).to_dict()

