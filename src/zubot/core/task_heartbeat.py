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
        started_dt = now.astimezone(UTC) if isinstance(now, datetime) else self._utc_now()
        started_iso = started_dt.isoformat()
        try:
            out = self._store.enqueue_due_runs(now=started_dt)
        except Exception as exc:
            finished_iso = self._utc_now().isoformat()
            self._store.record_heartbeat_state(
                started_at=started_iso,
                finished_at=finished_iso,
                status="error",
                enqueued_count=0,
                error=str(exc),
            )
            raise

        if not isinstance(out, dict):
            out = HeartbeatTickResult(ok=False, enqueued=0, runs=[]).to_dict()

        runs = out.get("runs")
        result = HeartbeatTickResult(
            ok=bool(out.get("ok")),
            enqueued=int(out.get("enqueued") or 0),
            runs=runs if isinstance(runs, list) else [],
        ).to_dict()
        self._store.record_heartbeat_state(
            started_at=started_iso,
            finished_at=self._utc_now().isoformat(),
            status="ok" if bool(result.get("ok")) else "error",
            enqueued_count=int(result.get("enqueued") or 0),
            error=None if bool(result.get("ok")) else str(out.get("error") or "heartbeat enqueue failed"),
        )
        return result
