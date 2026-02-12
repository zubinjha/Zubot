"""Worker lifecycle manager with queueing and bounded concurrency."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4

from .agent_types import TaskEnvelope
from .config_loader import get_max_concurrent_workers
from .context_loader import load_base_context
from .sub_agent_runner import SubAgentRunner
from .worker_policy import should_forward_worker_event_to_user

WorkerLifecycleStatus = str

WORKER_BASE_CONTEXT_FILES = ["context/KERNEL.md"]
WORKER_OPERATING_PROMPT = """# WORKER
You are a non-user-facing worker agent.
Focus only on the assigned task and return structured, concise outcomes.
If blocked, clearly report what is missing.
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_worker_status(result: dict[str, Any]) -> WorkerLifecycleStatus:
    if not result:
        return "failed"
    worker_status = str(result.get("status") or "").strip().lower()
    if worker_status in {"success", "needs_user_input"}:
        return "done"
    return "failed"


@dataclass(slots=True)
class WorkerContextSession:
    """Scoped per-worker context memory."""

    base_context: dict[str, str] = field(default_factory=dict)
    supplemental_context: dict[str, str] = field(default_factory=dict)
    facts: dict[str, str] = field(default_factory=dict)
    session_summary: str | None = None


@dataclass(slots=True)
class WorkerRecord:
    """Runtime state for one worker."""

    worker_id: str
    title: str
    status: WorkerLifecycleStatus
    task_envelope: dict[str, Any] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    pending_tasks: list[TaskEnvelope] = field(default_factory=list)
    context_session: WorkerContextSession = field(default_factory=WorkerContextSession)
    cancel_requested: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "title": self.title,
            "status": self.status,
            "task_envelope": self.task_envelope,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "pending_task_count": len(self.pending_tasks),
            "cancel_requested": self.cancel_requested,
            "event_count": len(self.events),
            "session_summary_present": bool(self.context_session.session_summary),
            "fact_count": len(self.context_session.facts),
        }


class WorkerManager:
    """Manage worker spawn, queueing, execution, and state transitions."""

    def __init__(
        self,
        *,
        runner: SubAgentRunner | None = None,
        max_concurrent_workers: int = 3,
    ) -> None:
        if max_concurrent_workers <= 0:
            raise ValueError("max_concurrent_workers must be >= 1")
        self._runner = runner or SubAgentRunner()
        self._max_concurrent_workers = max_concurrent_workers
        self._workers: dict[str, WorkerRecord] = {}
        self._ready_queue: deque[str] = deque()
        self._running_threads: dict[str, Thread] = {}
        self._lock = RLock()
        self._idle_event = Event()
        self._idle_event.set()

    @staticmethod
    def _dispose_worker_context(worker: WorkerRecord) -> None:
        worker.context_session = WorkerContextSession(
            base_context={},
            supplemental_context={},
            facts={},
            session_summary=None,
        )

    def _record_event(
        self,
        worker: WorkerRecord,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        main_context: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "event_id": f"wevt_{uuid4().hex}",
            "worker_id": worker.worker_id,
            "worker_title": worker.title,
            "type": event_type,
            "payload": payload or {},
            "timestamp": _utc_now_iso(),
        }
        event["forward_to_user"] = should_forward_worker_event_to_user(event, main_context)
        event["forwarded"] = False
        worker.events.append(event)

    def _ensure_queued_locked(self, worker_id: str) -> None:
        if worker_id not in self._ready_queue:
            self._ready_queue.append(worker_id)
            self._idle_event.clear()

    def _dispatch_locked(self) -> None:
        while len(self._running_threads) < self._max_concurrent_workers and self._ready_queue:
            worker_id = self._ready_queue.popleft()
            worker = self._workers.get(worker_id)
            if worker is None:
                continue
            if worker.cancel_requested and not worker.pending_tasks:
                worker.status = "cancelled"
                worker.finished_at = _utc_now_iso()
                self._dispose_worker_context(worker)
                continue
            if worker_id in self._running_threads:
                continue
            if not worker.pending_tasks:
                if worker.status == "queued":
                    worker.status = "done"
                    worker.finished_at = _utc_now_iso()
                    self._dispose_worker_context(worker)
                continue

            task = worker.pending_tasks.pop(0)
            worker.status = "running"
            worker.task_envelope = task.to_dict()
            worker.error = None
            if worker.started_at is None:
                worker.started_at = _utc_now_iso()
            self._record_event(worker, event_type="worker_started", payload={"task_id": task.task_id})

            thread = Thread(target=self._run_task, args=(worker_id, task), daemon=True)
            self._running_threads[worker_id] = thread
            thread.start()

        if not self._running_threads and not self._ready_queue:
            self._idle_event.set()

    @staticmethod
    def _build_preload_context(preload_files: list[str]) -> dict[str, str]:
        if not preload_files:
            return {}
        return load_base_context(files=preload_files)

    def _run_task(self, worker_id: str, task: TaskEnvelope) -> None:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                self._running_threads.pop(worker_id, None)
                self._dispatch_locked()
                return
            context_session = worker.context_session
            base_context = {
                **context_session.base_context,
                "runtime/WORKER_OPERATING.md": WORKER_OPERATING_PROMPT,
            }
            supplemental_context = dict(context_session.supplemental_context)
            facts = dict(context_session.facts)
            session_summary = context_session.session_summary

        worker_out = self._runner.run_task(
            task,
            base_context=base_context,
            supplemental_context=supplemental_context,
            facts=facts,
            session_summary=session_summary,
            model=task.model_tier,
        )

        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                self._running_threads.pop(worker_id, None)
                self._dispatch_locked()
                return

            result_payload = worker_out.get("result")
            if not isinstance(result_payload, dict):
                result_payload = {}

            if worker.cancel_requested:
                worker.status = "cancelled"
                worker.finished_at = _utc_now_iso()
                worker.error = "cancel_requested"
                worker.result = None
                worker.task_envelope = None
                worker.pending_tasks.clear()
                self._dispose_worker_context(worker)
                self._record_event(worker, event_type="worker_cancelled")
            else:
                worker.result = result_payload
                worker.error = result_payload.get("error") if isinstance(result_payload.get("error"), str) else None
                worker.status = _normalize_worker_status(result_payload)
                if not worker.pending_tasks:
                    worker.finished_at = _utc_now_iso()
                worker.task_envelope = None

                updated_summary = worker_out.get("session_summary")
                if isinstance(updated_summary, str):
                    worker.context_session.session_summary = updated_summary
                updated_facts = worker_out.get("facts")
                if isinstance(updated_facts, dict):
                    worker.context_session.facts = {
                        key: val for key, val in updated_facts.items() if isinstance(key, str) and isinstance(val, str)
                    }

                worker_status = str(result_payload.get("status") or "").strip().lower()
                if worker_status == "needs_user_input":
                    self._record_event(
                        worker,
                        event_type="worker_needs_user_input",
                        payload={"summary": result_payload.get("summary")},
                    )
                elif worker.status == "failed":
                    self._record_event(
                        worker,
                        event_type="worker_blocked",
                        payload={"error": worker.error or "worker_failed"},
                    )
                else:
                    self._record_event(
                        worker,
                        event_type="worker_completed",
                        payload={"summary": result_payload.get("summary")},
                    )

                if worker.pending_tasks:
                    worker.status = "queued"
                    self._ensure_queued_locked(worker_id)
                else:
                    self._dispose_worker_context(worker)

            self._running_threads.pop(worker_id, None)
            self._dispatch_locked()

    def spawn_worker(
        self,
        *,
        title: str,
        instructions: str,
        model_tier: str = "medium",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        preload_files: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        clean_instructions = instructions.strip()
        if not clean_title:
            return {"ok": False, "error": "title is required"}
        if not clean_instructions:
            return {"ok": False, "error": "instructions are required"}

        task = TaskEnvelope.create(
            instructions=clean_instructions,
            model_tier=model_tier if model_tier in {"low", "medium", "high"} else "medium",  # type: ignore[arg-type]
            requested_by="main_agent",
            tool_access=tool_access or [],
            skill_access=skill_access or [],
            metadata={
                **(metadata or {}),
                "preload_files": list(preload_files or []),
            },
        )
        worker_id = f"worker_{uuid4().hex[:10]}"
        base_context = load_base_context(files=WORKER_BASE_CONTEXT_FILES)
        supplemental = self._build_preload_context(list(preload_files or []))

        record = WorkerRecord(
            worker_id=worker_id,
            title=clean_title,
            status="queued",
            pending_tasks=[task],
            context_session=WorkerContextSession(
                base_context=base_context,
                supplemental_context=supplemental,
            ),
        )
        self._record_event(record, event_type="worker_spawned", payload={"task_id": task.task_id, "title": clean_title})

        with self._lock:
            self._workers[worker_id] = record
            self._ensure_queued_locked(worker_id)
            self._dispatch_locked()
            payload = record.to_dict()
            running_count = len(self._running_threads)
            queued_count = len(self._ready_queue)

        return {
            "ok": True,
            "worker": payload,
            "runtime": {
                "max_concurrent_workers": self._max_concurrent_workers,
                "running_count": running_count,
                "queued_count": queued_count,
            },
        }

    def message_worker(
        self,
        *,
        worker_id: str,
        message: str,
        model_tier: str = "medium",
    ) -> dict[str, Any]:
        clean_message = message.strip()
        if not clean_message:
            return {"ok": False, "error": "message is required", "worker_id": worker_id}
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return {"ok": False, "error": "worker not found", "worker_id": worker_id}
            if worker.status == "cancelled":
                return {"ok": False, "error": "worker is cancelled", "worker_id": worker_id}

            task = TaskEnvelope.create(
                instructions=clean_message,
                model_tier=model_tier if model_tier in {"low", "medium", "high"} else "medium",  # type: ignore[arg-type]
                requested_by="main_agent",
                metadata={"worker_id": worker_id, "message": True},
            )
            worker.pending_tasks.append(task)
            if worker.status in {"done", "failed"}:
                worker.status = "queued"
                worker.finished_at = None
            self._record_event(worker, event_type="worker_message_enqueued", payload={"task_id": task.task_id})
            self._ensure_queued_locked(worker_id)
            self._dispatch_locked()
            return {"ok": True, "worker": worker.to_dict()}

    def cancel_worker(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return {"ok": False, "error": "worker not found", "worker_id": worker_id}

            worker.cancel_requested = True
            worker.pending_tasks.clear()
            if worker_id in self._ready_queue:
                self._ready_queue = deque([wid for wid in self._ready_queue if wid != worker_id])
            if worker.status != "running":
                worker.status = "cancelled"
                worker.finished_at = _utc_now_iso()
                worker.error = "cancel_requested"
                self._dispose_worker_context(worker)
                self._record_event(worker, event_type="worker_cancelled")
            else:
                self._record_event(worker, event_type="worker_cancel_requested")
            self._dispatch_locked()
            return {"ok": True, "worker": worker.to_dict()}

    def reset_worker_context(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return {"ok": False, "error": "worker not found", "worker_id": worker_id}
            if worker.status == "running":
                return {"ok": False, "error": "cannot reset context while worker is running", "worker_id": worker_id}

            worker.context_session = WorkerContextSession(
                base_context=load_base_context(files=WORKER_BASE_CONTEXT_FILES),
                supplemental_context={},
                facts={},
                session_summary=None,
            )
            self._record_event(worker, event_type="worker_context_reset")
            return {"ok": True, "worker": worker.to_dict()}

    def get_worker(self, worker_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return {"ok": False, "error": "worker not found", "worker_id": worker_id}
            return {"ok": True, "worker": worker.to_dict()}

    def list_workers(self) -> dict[str, Any]:
        with self._lock:
            workers = [self._workers[key].to_dict() for key in sorted(self._workers)]
            return {
                "ok": True,
                "workers": workers,
                "runtime": {
                    "max_concurrent_workers": self._max_concurrent_workers,
                    "running_count": len(self._running_threads),
                    "queued_count": len(self._ready_queue),
                    "total_workers": len(self._workers),
                },
            }

    def list_forward_events(self, *, consume: bool = True) -> dict[str, Any]:
        """Return forwardable worker events; optionally consume them."""
        with self._lock:
            out: list[dict[str, Any]] = []
            for worker_id in sorted(self._workers):
                worker = self._workers[worker_id]
                for event in worker.events:
                    if not event.get("forward_to_user"):
                        continue
                    if event.get("forwarded"):
                        continue
                    out.append(
                        {
                            "event_id": event["event_id"],
                            "worker_id": event["worker_id"],
                            "worker_title": event.get("worker_title"),
                            "type": event["type"],
                            "timestamp": event["timestamp"],
                            "payload": event.get("payload", {}),
                        }
                    )
                    if consume:
                        event["forwarded"] = True
            return {"ok": True, "events": out, "count": len(out), "consumed": consume}

    def wait_for_idle(self, timeout_sec: float = 5.0) -> bool:
        """Block until no queued/running workers remain."""
        return self._idle_event.wait(timeout=timeout_sec)


_WORKER_MANAGER: WorkerManager | None = None


def get_worker_manager() -> WorkerManager:
    global _WORKER_MANAGER
    if _WORKER_MANAGER is None:
        try:
            limit = get_max_concurrent_workers()
        except Exception:
            limit = 3
        _WORKER_MANAGER = WorkerManager(max_concurrent_workers=limit)
    return _WORKER_MANAGER
