"""Central service loop for scheduled task-agent runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4

from .central_db_queue import CentralDbQueue
from .config_loader import load_config
from .daily_memory import append_daily_memory_entry, local_day_str
from .memory_index import enqueue_day_summary_job, increment_day_message_count
from .memory_manager import MemoryManager, MemoryManagerSettings
from .memory_summary_worker import get_memory_summary_worker
from .task_heartbeat import TaskHeartbeat
from .task_agent_runner import TaskAgentRunner
from .task_scheduler_store import TaskSchedulerStore, resolve_scheduler_db_path


@dataclass(slots=True)
class CentralServiceSettings:
    enabled: bool = False
    poll_interval_sec: int = 3600
    heartbeat_poll_interval_sec: int = 3600
    task_runner_concurrency: int = 2
    scheduler_db_path: str = "memory/central/zubot_core.db"
    run_history_retention_days: int = 30
    run_history_max_rows: int = 5000
    memory_manager_sweep_interval_sec: int = 12 * 60 * 60
    memory_manager_completion_debounce_sec: int = 5 * 60
    queue_warning_threshold: int = 25
    running_age_warning_sec: int = 1800
    db_queue_busy_timeout_ms: int = 5000
    db_queue_default_max_rows: int = 500
    waiting_for_user_timeout_sec: int = 24 * 60 * 60


def _load_central_settings() -> CentralServiceSettings:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}

    central = cfg.get("central_service") if isinstance(cfg, dict) else None
    if not isinstance(central, dict):
        return CentralServiceSettings()

    enabled = bool(central.get("enabled", False))
    heartbeat_poll = central.get("heartbeat_poll_interval_sec")
    poll = heartbeat_poll if isinstance(heartbeat_poll, int) else central.get("poll_interval_sec", 3600)
    conc = central.get("task_runner_concurrency", 2)
    db_path = central.get("scheduler_db_path", "memory/central/zubot_core.db")
    retention_days = central.get("run_history_retention_days", 30)
    history_max_rows = central.get("run_history_max_rows", 5000)
    memory_sweep_sec = central.get("memory_manager_sweep_interval_sec", 12 * 60 * 60)
    memory_debounce_sec = central.get("memory_manager_completion_debounce_sec", 5 * 60)
    queue_warning = central.get("queue_warning_threshold", 25)
    running_age_warning = central.get("running_age_warning_sec", 1800)
    db_queue_busy_timeout = central.get("db_queue_busy_timeout_ms", 5000)
    db_queue_max_rows = central.get("db_queue_default_max_rows", 500)
    waiting_timeout = central.get("waiting_for_user_timeout_sec", 24 * 60 * 60)

    return CentralServiceSettings(
        enabled=enabled,
        poll_interval_sec=int(poll) if isinstance(poll, int) and poll > 0 else 3600,
        heartbeat_poll_interval_sec=int(poll) if isinstance(poll, int) and poll > 0 else 3600,
        task_runner_concurrency=int(conc) if isinstance(conc, int) and conc > 0 else 2,
        scheduler_db_path=str(db_path) if isinstance(db_path, str) and db_path.strip() else "memory/central/zubot_core.db",
        run_history_retention_days=int(retention_days) if isinstance(retention_days, int) and retention_days >= 0 else 30,
        run_history_max_rows=int(history_max_rows) if isinstance(history_max_rows, int) and history_max_rows >= 0 else 5000,
        memory_manager_sweep_interval_sec=int(memory_sweep_sec) if isinstance(memory_sweep_sec, int) and memory_sweep_sec > 0 else 12 * 60 * 60,
        memory_manager_completion_debounce_sec=int(memory_debounce_sec) if isinstance(memory_debounce_sec, int) and memory_debounce_sec > 0 else 5 * 60,
        queue_warning_threshold=int(queue_warning) if isinstance(queue_warning, int) and queue_warning >= 0 else 25,
        running_age_warning_sec=int(running_age_warning) if isinstance(running_age_warning, int) and running_age_warning >= 0 else 1800,
        db_queue_busy_timeout_ms=int(db_queue_busy_timeout)
        if isinstance(db_queue_busy_timeout, int) and db_queue_busy_timeout > 0
        else 5000,
        db_queue_default_max_rows=int(db_queue_max_rows) if isinstance(db_queue_max_rows, int) and db_queue_max_rows > 0 else 500,
        waiting_for_user_timeout_sec=int(waiting_timeout) if isinstance(waiting_timeout, int) and waiting_timeout >= 0 else 24 * 60 * 60,
    )


def _load_task_profiles() -> dict[str, dict[str, Any]]:
    try:
        cfg = load_config()
    except Exception:
        return {}

    out: dict[str, dict[str, Any]] = {}

    profiles_root = cfg.get("task_profiles") if isinstance(cfg, dict) else None
    if not isinstance(profiles_root, dict):
        profiles_root = cfg.get("pre_defined_tasks") if isinstance(cfg, dict) else None
    profiles = profiles_root.get("tasks") if isinstance(profiles_root, dict) else None
    if isinstance(profiles, dict):
        for task_id, payload in profiles.items():
            if not isinstance(task_id, str) or not isinstance(payload, dict):
                continue
            kind = str(payload.get("kind") or "script").strip().lower()
            if kind not in {"script", "agentic", "interactive_wrapper"}:
                kind = "script"
            out[task_id] = {
                "name": payload.get("name") or task_id,
                "kind": kind,
                "entrypoint_path": payload.get("entrypoint_path"),
                "resources_path": payload.get("resources_path"),
                "module": payload.get("module"),
                "queue_group": payload.get("queue_group"),
                "timeout_sec": payload.get("timeout_sec"),
                "retry_policy": payload.get("retry_policy"),
            }

    return out


def _sanitize_task_id(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in raw.strip())


def summarize_task_agent_check_in(task_agents: list[dict[str, Any]]) -> str:
    """Return a concise text summary for main-agent check-ins."""
    if not task_agents:
        return "No task-agent profiles are configured."

    parts: list[str] = []
    for item in task_agents:
        profile_id = str(item.get("profile_id") or "unknown")
        name = str(item.get("name") or profile_id)
        state = str(item.get("state") or "free")
        description = str(item.get("current_description") or "").strip()
        queue_position = item.get("queue_position")
        last_result = item.get("last_result") if isinstance(item.get("last_result"), dict) else None
        last_status = str(last_result.get("status")) if isinstance(last_result, dict) and last_result.get("status") else None

        state_desc = state
        if state == "queued" and isinstance(queue_position, int):
            state_desc = f"queued (position {queue_position})"
        if state in {"running", "queued", "waiting_for_user"} and description:
            parts.append(f"{name}: {state_desc}; {description}")
            continue
        if last_status:
            parts.append(f"{name}: {state_desc}; last result {last_status}")
            continue
        parts.append(f"{name}: {state_desc}")

    return " | ".join(parts)


class CentralService:
    """Single-process scheduler + queue consumer."""

    def __init__(self) -> None:
        settings = _load_central_settings()
        self._settings = settings
        self._store = TaskSchedulerStore(db_path=settings.scheduler_db_path)
        self._heartbeat = TaskHeartbeat(store=self._store)
        self._db_queue = CentralDbQueue(
            db_path=self._store.db_path,
            busy_timeout_ms=settings.db_queue_busy_timeout_ms,
        )
        self._runner = TaskAgentRunner()
        self._memory_manager = MemoryManager()
        self._lock = RLock()
        self._stop_event = Event()
        self._loop_thread: Thread | None = None
        self._active_threads: dict[str, Thread] = {}
        self._active_descriptions: dict[str, str] = {}
        self._cancel_events: dict[str, Event] = {}
        self._run_to_slot: dict[str, str] = {}
        self._task_slots: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._sync_task_slots(settings.task_runner_concurrency)

    @staticmethod
    def _utc_now_iso() -> str:
        from datetime import UTC, datetime

        return datetime.now(tz=UTC).isoformat()

    @staticmethod
    def _parse_iso_utc(value: Any) -> Any | None:
        if not isinstance(value, str) or not value.strip():
            return None
        from datetime import UTC, datetime

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            return None

    @staticmethod
    def _future_iso(*, timeout_sec: int) -> str | None:
        safe_timeout = int(timeout_sec) if isinstance(timeout_sec, int) else 0
        if safe_timeout <= 0:
            return None
        from datetime import UTC, datetime, timedelta

        return (datetime.now(tz=UTC) + timedelta(seconds=safe_timeout)).isoformat()

    def _record_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        forward_to_user: bool = True,
    ) -> None:
        event = {
            "event_id": f"tevt_{uuid4().hex}",
            "type": event_type,
            "timestamp": self._utc_now_iso(),
            "payload": payload or {},
            "forward_to_user": forward_to_user,
            "forwarded": False,
        }
        with self._lock:
            self._events.append(event)
            if len(self._events) > 500:
                self._events = self._events[-500:]

    def _recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(200, int(limit)))
        with self._lock:
            recent = self._events[-safe_limit:]
            return [
                {
                    "event_id": event["event_id"],
                    "type": event["type"],
                    "timestamp": event["timestamp"],
                    "payload": event.get("payload", {}),
                    "forward_to_user": bool(event.get("forward_to_user", False)),
                    "forwarded": bool(event.get("forwarded", False)),
                }
                for event in recent
            ]

    def _sync_task_slots(self, target_concurrency: int) -> None:
        safe_target = max(1, int(target_concurrency))
        slot_ids = [f"slot_{idx}" for idx in range(1, safe_target + 1)]
        with self._lock:
            for slot_id in slot_ids:
                slot = self._task_slots.get(slot_id)
                if not isinstance(slot, dict):
                    self._task_slots[slot_id] = {
                        "slot_id": slot_id,
                        "enabled": True,
                        "state": "free",
                        "run_id": None,
                        "task_id": None,
                        "task_name": None,
                        "started_at": None,
                        "updated_at": self._utc_now_iso(),
                        "last_result": None,
                    }
                else:
                    slot["enabled"] = True

            for slot_id, slot in list(self._task_slots.items()):
                if slot_id in slot_ids:
                    continue
                if str(slot.get("state") or "free") == "busy":
                    slot["enabled"] = False
                else:
                    self._task_slots.pop(slot_id, None)

    def _acquire_free_slot_locked(self) -> str | None:
        for slot_id in sorted(self._task_slots):
            slot = self._task_slots.get(slot_id)
            if not isinstance(slot, dict):
                continue
            if not bool(slot.get("enabled", True)):
                continue
            if str(slot.get("state") or "free") != "free":
                continue
            slot["state"] = "allocating"
            slot["updated_at"] = self._utc_now_iso()
            return slot_id
        return None

    def _assign_slot_locked(self, *, slot_id: str, run_id: str, task_id: str, task_name: str) -> None:
        slot = self._task_slots.get(slot_id)
        if not isinstance(slot, dict):
            return
        now = self._utc_now_iso()
        slot["state"] = "busy"
        slot["run_id"] = run_id
        slot["task_id"] = task_id
        slot["task_name"] = task_name
        slot["started_at"] = now
        slot["updated_at"] = now
        self._run_to_slot[run_id] = slot_id

    def _unreserve_slot_locked(self, *, slot_id: str) -> None:
        slot = self._task_slots.get(slot_id)
        if not isinstance(slot, dict):
            return
        if str(slot.get("state") or "") != "allocating":
            return
        slot["state"] = "free"
        slot["updated_at"] = self._utc_now_iso()

    def _release_slot_locked(
        self,
        *,
        run_id: str,
        final_status: str | None = None,
        final_summary: str | None = None,
        final_error: str | None = None,
    ) -> None:
        slot_id = self._run_to_slot.pop(run_id, None)
        if not slot_id:
            return
        slot = self._task_slots.get(slot_id)
        if not isinstance(slot, dict):
            return
        now = self._utc_now_iso()
        slot["state"] = "free"
        slot["run_id"] = None
        slot["task_id"] = None
        slot["task_name"] = None
        slot["started_at"] = None
        slot["updated_at"] = now
        if final_status:
            slot["last_result"] = {
                "status": final_status,
                "summary": final_summary,
                "error": final_error,
                "finished_at": now,
            }
        if not bool(slot.get("enabled", True)) and str(slot.get("state") or "") == "free":
            self._task_slots.pop(slot_id, None)

    def _task_slot_payload(self) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for slot_id in sorted(self._task_slots):
                slot = self._task_slots[slot_id]
                state_raw = str(slot.get("state") or "free")
                state = "busy" if state_raw == "allocating" else state_raw
                out.append(
                    {
                        "slot_id": slot_id,
                        "enabled": bool(slot.get("enabled", True)),
                        "state": state,
                        "run_id": slot.get("run_id"),
                        "task_id": slot.get("task_id"),
                        "task_name": slot.get("task_name"),
                        "started_at": slot.get("started_at"),
                        "updated_at": slot.get("updated_at"),
                        "last_result": slot.get("last_result"),
                    }
                )
        return out

    @staticmethod
    def _is_high_signal_task_memory_event(event_type: str) -> bool:
        return event_type in {"run_queued", "run_finished", "run_failed", "run_blocked", "run_waiting", "run_resumed"}

    @staticmethod
    def _extract_run_status_from_detail(detail: str) -> str | None:
        match = re.search(r"\bstatus=(done|failed|blocked|waiting_for_user)\b", detail)
        if match:
            return str(match.group(1))
        return None

    @staticmethod
    def _progress_status_for_event(*, event_type: str, detail: str, run_status: str | None) -> str:
        normalized = (run_status or "").strip().lower() or None
        if normalized is None:
            normalized = CentralService._extract_run_status_from_detail(detail)

        if event_type == "run_queued":
            return "queued"
        if event_type == "run_started":
            return "running"
        if event_type == "run_progress":
            return "progress"
        if event_type == "run_failed":
            return "failed"
        if event_type == "run_blocked":
            low = detail.lower()
            if "killed_by" in low or "killed" in low:
                return "killed"
            return "failed"
        if event_type == "run_waiting":
            return "waiting_for_user"
        if event_type == "run_resumed":
            return "queued"
        if event_type == "run_finished":
            if normalized == "done":
                return "completed"
            if normalized == "failed":
                return "failed"
            if normalized == "blocked":
                low = detail.lower()
                if "killed_by" in low or "killed" in low:
                    return "killed"
                return "failed"
            if normalized == "waiting_for_user":
                return "waiting_for_user"
            return "completed"
        return "progress"

    def _log_task_agent_event(
        self,
        *,
        event_type: str,
        profile_id: str,
        run_id: str,
        detail: str,
        run_status: str | None = None,
        progress_message: str | None = None,
        percent: int | None = None,
        slot_id: str | None = None,
        origin: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        text = f"{event_type} profile={profile_id} run_id={run_id} {detail}".strip()
        day = local_day_str()
        if self._is_high_signal_task_memory_event(event_type):
            append_daily_memory_entry(
                day_str=day,
                session_id="central_service",
                kind="task_agent_event",
                text=text,
                layer="raw",
            )
            increment_day_message_count(day=day, amount=1)
            enqueue_day_summary_job(day=day, reason=f"task_agent:{event_type}")
            worker = get_memory_summary_worker()
            worker.start()
            worker.kick()

        task_profiles = _load_task_profiles()
        run_row = self._store.get_run(run_id=run_id)
        task_name = str(task_profiles.get(profile_id, {}).get("name") or profile_id)
        if isinstance(run_row, dict):
            payload = run_row.get("payload")
            if isinstance(payload, dict):
                payload_name = payload.get("task_name")
                if isinstance(payload_name, str) and payload_name.strip():
                    task_name = payload_name.strip()
        progress_payload = {
            "event_type": event_type,
            "task_id": profile_id,
            "task_name": task_name,
            "run_id": run_id,
            "slot_id": slot_id,
            "status": self._progress_status_for_event(
                event_type=event_type,
                detail=detail,
                run_status=run_status,
            ),
            "message": (
                progress_message.strip()[:240]
                if isinstance(progress_message, str) and progress_message.strip()
                else detail[:240]
            ),
            "percent": int(percent) if isinstance(percent, int) and 0 <= int(percent) <= 100 else None,
            "started_at": run_row.get("started_at") if isinstance(run_row, dict) else None,
            "updated_at": self._utc_now_iso(),
            "finished_at": run_row.get("finished_at") if isinstance(run_row, dict) else None,
            "origin": origin,
            "detail": detail,
        }
        if isinstance(extra_payload, dict):
            progress_payload.update(extra_payload)
        self._record_event(
            event_type="task_agent_event",
            payload=progress_payload,
            forward_to_user=True,
        )

    def _expire_waiting_runs(self) -> dict[str, Any]:
        timeout_sec = int(self._settings.waiting_for_user_timeout_sec)
        if timeout_sec == 0:
            return {"ok": True, "expired_count": 0, "expired_run_ids": []}

        from datetime import UTC, datetime, timedelta

        now_dt = datetime.now(tz=UTC)
        expired: list[str] = []
        for row in self._store.list_runs(limit=500):
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") != "waiting_for_user":
                continue

            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            waiting = payload.get("waiting") if isinstance(payload.get("waiting"), dict) else {}
            expires_at_raw = waiting.get("expires_at")
            expires_at = self._parse_iso_utc(expires_at_raw)
            if expires_at is None:
                waiting_since = self._parse_iso_utc(waiting.get("waiting_since"))
                if waiting_since is None:
                    continue
                expires_at = waiting_since + timedelta(seconds=timeout_sec)
            if now_dt < expires_at:
                continue

            run_id = str(row.get("run_id") or "").strip()
            if not run_id:
                continue
            out = self._store.complete_run(
                run_id=run_id,
                status="blocked",
                summary=None,
                error="waiting_for_user_timeout",
            )
            if not out.get("ok"):
                continue
            expired.append(run_id)
            profile_id = str(row.get("profile_id") or "unknown")
            self._log_task_agent_event(
                event_type="run_blocked",
                profile_id=profile_id,
                run_id=run_id,
                detail=f"status=blocked reason=waiting_for_user_timeout request_id={str(waiting.get('request_id') or '')[:80]}",
                run_status="blocked",
                origin=str(payload.get("origin") or payload.get("trigger") or "manual"),
                extra_payload={
                    "request_id": waiting.get("request_id"),
                    "question": waiting.get("question"),
                    "context": waiting.get("context") if isinstance(waiting.get("context"), dict) else {},
                    "expires_at": expires_at.isoformat(),
                },
            )

        return {"ok": True, "expired_count": len(expired), "expired_run_ids": expired}

    def _memory_manager_settings(self) -> MemoryManagerSettings:
        return MemoryManagerSettings(
            sweep_interval_sec=self._settings.memory_manager_sweep_interval_sec,
            completion_debounce_sec=self._settings.memory_manager_completion_debounce_sec,
        )

    def _run_housekeeping(self, *, on_completion: bool = False) -> dict[str, Any]:
        waiting_expiry = self._expire_waiting_runs()
        prune = self._store.prune_runs(
            max_age_days=self._settings.run_history_retention_days,
            max_history_rows=self._settings.run_history_max_rows,
        )
        if on_completion:
            memory = self._memory_manager.maybe_completion_sweep(settings=self._memory_manager_settings())
        else:
            memory = self._memory_manager.maybe_periodic_sweep(settings=self._memory_manager_settings())
        finalized_count = int(memory.get("finalized_count") or 0) if isinstance(memory, dict) else 0
        if finalized_count > 0:
            self._record_event(
                event_type="memory_manager_sweep",
                payload={
                    "finalized_count": finalized_count,
                    "finalized_days": memory.get("finalized_days"),
                    "trigger": "completion" if on_completion else "periodic",
                },
                forward_to_user=False,
            )
        return {"ok": True, "waiting_expiry": waiting_expiry, "prune": prune, "memory": memory}

    def _refresh_settings(self) -> CentralServiceSettings:
        settings = _load_central_settings()
        with self._lock:
            self._settings = settings
            resolved = resolve_scheduler_db_path(settings.scheduler_db_path)
            if str(self._store.db_path) != str(resolved):
                # Reload store if db path changed.
                self._store = TaskSchedulerStore(db_path=settings.scheduler_db_path)
                self._heartbeat = TaskHeartbeat(store=self._store)
                self._db_queue.stop()
                self._db_queue = CentralDbQueue(
                    db_path=self._store.db_path,
                    busy_timeout_ms=settings.db_queue_busy_timeout_ms,
                )
            elif self._db_queue.health().get("busy_timeout_ms") != settings.db_queue_busy_timeout_ms:
                self._db_queue.stop()
                self._db_queue = CentralDbQueue(
                    db_path=self._store.db_path,
                    busy_timeout_ms=settings.db_queue_busy_timeout_ms,
                )
            self._sync_task_slots(settings.task_runner_concurrency)
        return settings

    def start(self) -> dict[str, Any]:
        self._db_queue.start()
        with self._lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                return {"ok": True, "running": True, "already_running": True}
            self._stop_event.clear()
            self._loop_thread = Thread(target=self._run_loop, daemon=True, name="zubot-central-service")
            self._loop_thread.start()
        return {"ok": True, "running": True, "already_running": False}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._loop_thread
            self._stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._db_queue.stop()
        with self._lock:
            self._loop_thread = None
        return {"ok": True, "running": False}

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            wait_for = max(1, self._settings.heartbeat_poll_interval_sec)
            self._stop_event.wait(timeout=wait_for)

    def _execute_claimed_run(self, claimed: dict[str, Any]) -> None:
        run_id = str(claimed.get("run_id") or "")
        profile_id = str(claimed.get("profile_id") or "")
        payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
        origin = str(payload.get("origin") or payload.get("trigger") or "manual")
        with self._lock:
            slot_id = self._run_to_slot.get(run_id)
        with self._lock:
            cancel_event = self._cancel_events.get(run_id)
        self._log_task_agent_event(
            event_type="run_started",
            profile_id=profile_id,
            run_id=run_id,
            detail="started",
            slot_id=slot_id,
            origin=origin,
        )
        self._log_task_agent_event(
            event_type="run_progress",
            profile_id=profile_id,
            run_id=run_id,
            detail="progress=10 message=task execution started",
            percent=10,
            progress_message="task execution started",
            slot_id=slot_id,
            origin=origin,
        )

        try:
            result = self._runner.run_profile(profile_id=profile_id, payload=payload, cancel_event=cancel_event)
            status = str(result.get("status") or "done")
            if status not in {"done", "failed", "blocked", "waiting_for_user"}:
                status = "failed"
            summary = result.get("summary") if isinstance(result.get("summary"), str) else None
            error = result.get("error") if isinstance(result.get("error"), str) else None
            retryable_error = bool(result.get("retryable_error", False))
            attempts_used = result.get("attempts_used") if isinstance(result.get("attempts_used"), int) else None
            attempts_configured = (
                result.get("attempts_configured")
                if isinstance(result.get("attempts_configured"), int)
                else None
            )
            waiting_payload: dict[str, Any] = {}
            if status == "waiting_for_user":
                question = result.get("question") if isinstance(result.get("question"), str) else None
                wait_ctx = result.get("wait_context") if isinstance(result.get("wait_context"), dict) else None
                wait_timeout_raw = result.get("wait_timeout_sec")
                wait_timeout_sec = (
                    int(wait_timeout_raw)
                    if isinstance(wait_timeout_raw, int) and wait_timeout_raw >= 0
                    else int(self._settings.waiting_for_user_timeout_sec)
                )
                wait_mark = self._store.mark_waiting_for_user(
                    run_id=run_id,
                    question=question,
                    wait_context=wait_ctx,
                    requested_by=str(payload.get("requested_by") or "main_agent"),
                    expires_at=self._future_iso(timeout_sec=wait_timeout_sec),
                )
                waiting_payload = wait_mark.get("waiting") if isinstance(wait_mark.get("waiting"), dict) else {}
            else:
                self._store.complete_run(run_id=run_id, status=status, summary=summary, error=error)
            detail = f"status={status}"
            if summary:
                detail += f" summary={summary[:160]}"
            if error:
                detail += f" error={error[:160]}"
            if status in {"failed", "blocked"}:
                detail += f" retryable_error={retryable_error}"
                if attempts_used is not None:
                    detail += f" attempts_used={attempts_used}"
                if attempts_configured is not None:
                    detail += f" attempts_configured={attempts_configured}"
            if status == "waiting_for_user":
                self._log_task_agent_event(
                    event_type="run_waiting",
                    profile_id=profile_id,
                    run_id=run_id,
                    detail=detail,
                    run_status=status,
                    slot_id=slot_id,
                    origin=origin,
                    extra_payload={
                        "request_id": waiting_payload.get("request_id"),
                        "question": waiting_payload.get("question"),
                        "context": waiting_payload.get("context")
                        if isinstance(waiting_payload.get("context"), dict)
                        else {},
                        "expires_at": waiting_payload.get("expires_at"),
                    },
                )
            else:
                self._log_task_agent_event(
                    event_type="run_finished",
                    profile_id=profile_id,
                    run_id=run_id,
                    detail=detail,
                    run_status=status,
                    slot_id=slot_id,
                    origin=origin,
                )
            final_status = status
            final_summary = summary
            final_error = error
        except Exception as exc:
            self._store.complete_run(run_id=run_id, status="failed", summary=None, error=str(exc))
            self._log_task_agent_event(
                event_type="run_failed",
                profile_id=profile_id,
                run_id=run_id,
                detail=f"error={str(exc)[:180]}",
                slot_id=slot_id,
                origin=origin,
            )
            final_status = "failed"
            final_summary = None
            final_error = str(exc)
        finally:
            with self._lock:
                self._active_descriptions.pop(run_id, None)
                self._active_threads.pop(run_id, None)
                self._cancel_events.pop(run_id, None)
                self._release_slot_locked(
                    run_id=run_id,
                    final_status=final_status,
                    final_summary=final_summary,
                    final_error=final_error,
                )
            self._run_housekeeping(on_completion=True)

    def _dispatch_available(self) -> dict[str, Any]:
        started = 0
        task_profiles = _load_task_profiles()
        while True:
            with self._lock:
                if len(self._active_threads) >= self._settings.task_runner_concurrency:
                    break
                slot_id = self._acquire_free_slot_locked()
                if slot_id is None:
                    break

            claimed = self._store.claim_next_run()
            if claimed is None:
                with self._lock:
                    self._unreserve_slot_locked(slot_id=slot_id)
                break

            run_id = str(claimed.get("run_id") or "")
            profile_id = str(claimed.get("profile_id") or "")
            payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
            desc = self._runner.describe_run(profile_id=profile_id, payload=payload)
            payload_name = payload.get("task_name") if isinstance(payload.get("task_name"), str) else None
            task_name = (
                payload_name.strip()
                if isinstance(payload_name, str) and payload_name.strip()
                else str(task_profiles.get(profile_id, {}).get("name") or profile_id)
            )

            thread = Thread(target=self._execute_claimed_run, args=(claimed,), daemon=True)
            with self._lock:
                self._active_descriptions[run_id] = desc
                self._cancel_events[run_id] = Event()
                self._active_threads[run_id] = thread
                self._assign_slot_locked(slot_id=slot_id, run_id=run_id, task_id=profile_id, task_name=task_name)
            thread.start()
            started += 1

        return {"ok": True, "started": started}

    def tick(self) -> dict[str, Any]:
        settings = self._refresh_settings()
        enqueue = self._heartbeat.enqueue_due_runs()
        queued_runs = enqueue.get("runs") if isinstance(enqueue, dict) else None
        if isinstance(queued_runs, list):
            for row in queued_runs:
                if not isinstance(row, dict):
                    continue
                run_id = str(row.get("run_id") or "").strip()
                profile_id = str(row.get("profile_id") or "").strip()
                if not run_id or not profile_id:
                    continue
                self._log_task_agent_event(
                    event_type="run_queued",
                    profile_id=profile_id,
                    run_id=run_id,
                    detail="trigger=scheduled",
                    origin="scheduled",
                )
        dispatch = self._dispatch_available()
        housekeeping = self._run_housekeeping(on_completion=False)
        return {
            "ok": True,
            "settings": {
                "enabled": settings.enabled,
                "poll_interval_sec": settings.poll_interval_sec,
                "heartbeat_poll_interval_sec": settings.heartbeat_poll_interval_sec,
                "task_runner_concurrency": settings.task_runner_concurrency,
                "scheduler_db_path": settings.scheduler_db_path,
                "run_history_retention_days": settings.run_history_retention_days,
                "run_history_max_rows": settings.run_history_max_rows,
                "memory_manager_sweep_interval_sec": settings.memory_manager_sweep_interval_sec,
                "memory_manager_completion_debounce_sec": settings.memory_manager_completion_debounce_sec,
                "queue_warning_threshold": settings.queue_warning_threshold,
                "running_age_warning_sec": settings.running_age_warning_sec,
                "db_queue_busy_timeout_ms": settings.db_queue_busy_timeout_ms,
                "db_queue_default_max_rows": settings.db_queue_default_max_rows,
                "waiting_for_user_timeout_sec": settings.waiting_for_user_timeout_sec,
            },
            "enqueue": enqueue,
            "dispatch": dispatch,
            "housekeeping": housekeeping,
        }

    def trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        out = self._store.enqueue_manual_run(profile_id=profile_id, description=description)
        if out.get("ok"):
            run_id = str(out.get("run_id") or "").strip()
            clean_profile_id = str(profile_id or "").strip()
            if run_id and clean_profile_id:
                detail = "trigger=manual"
                if isinstance(description, str) and description.strip():
                    detail += f" description={description.strip()[:120]}"
                self._log_task_agent_event(
                    event_type="run_queued",
                    profile_id=clean_profile_id,
                    run_id=run_id,
                    detail=detail,
                    origin="manual",
                )
        self._dispatch_available()
        return out

    def enqueue_agentic_task(
        self,
        *,
        task_name: str,
        instructions: str,
        requested_by: str = "main_agent",
        model_tier: str = "medium",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        timeout_sec: int = 180,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out = self._store.enqueue_agentic_run(
            task_name=task_name,
            instructions=instructions,
            requested_by=requested_by,
            model_tier=model_tier,
            tool_access=tool_access,
            skill_access=skill_access,
            timeout_sec=timeout_sec,
            metadata=metadata,
        )
        if out.get("ok"):
            run_id = str(out.get("run_id") or "").strip()
            if run_id:
                self._log_task_agent_event(
                    event_type="run_queued",
                    profile_id="agentic_task",
                    run_id=run_id,
                    detail=f"trigger=agentic requested_by={requested_by} task_name={str(task_name or '').strip()[:80]}",
                    origin="agentic",
                )
        self._dispatch_available()
        return out

    def kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            return {"ok": False, "error": "run_id is required."}

        row = self._store.get_run(run_id=clean_run_id)
        if row is None:
            return {"ok": False, "error": "run not found"}

        profile_id = str(row.get("profile_id") or "unknown")
        status = str(row.get("status") or "")
        if status in {"done", "failed", "blocked"}:
            return {"ok": True, "run_id": clean_run_id, "status": status, "already_terminal": True}

        if status in {"queued", "waiting_for_user"}:
            out = self._store.cancel_run(run_id=clean_run_id, reason=f"killed_by_user:{requested_by}")
            if out.get("ok"):
                self._log_task_agent_event(
                    event_type="run_blocked",
                    profile_id=profile_id,
                    run_id=clean_run_id,
                    detail=f"killed_by={requested_by} state={status}",
                    origin=str(
                        row.get("payload", {}).get("origin")
                        or row.get("payload", {}).get("trigger")
                        or "manual"
                    )
                    if isinstance(row.get("payload"), dict)
                    else "manual",
                )
            self._run_housekeeping(on_completion=True)
            return out

        if status == "running":
            with self._lock:
                cancel_event = self._cancel_events.get(clean_run_id)
                slot_id = self._run_to_slot.get(clean_run_id)
            if cancel_event is None:
                return {"ok": False, "error": "run is not currently managed by active executor"}
            cancel_event.set()
            self._log_task_agent_event(
                event_type="run_blocked",
                profile_id=profile_id,
                run_id=clean_run_id,
                detail=f"killed_by={requested_by} state=running cancel_requested=true",
                slot_id=slot_id,
                origin=str(
                    row.get("payload", {}).get("origin")
                    or row.get("payload", {}).get("trigger")
                    or "manual"
                )
                if isinstance(row.get("payload"), dict)
                else "manual",
            )
            return {
                "ok": True,
                "run_id": clean_run_id,
                "status": "running",
                "cancel_requested": True,
                "already_terminal": False,
            }

        return {"ok": False, "error": f"unsupported run status `{status}`"}

    def list_waiting_runs(self, *, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        runs: list[dict[str, Any]] = []
        for row in self._store.list_runs(limit=max(500, safe_limit)):
            if str(row.get("status") or "") != "waiting_for_user":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            waiting = payload.get("waiting") if isinstance(payload.get("waiting"), dict) else {}
            runs.append(
                {
                    **row,
                    "request_id": waiting.get("request_id"),
                    "question": waiting.get("question"),
                    "context": waiting.get("context") if isinstance(waiting.get("context"), dict) else {},
                    "expires_at": waiting.get("expires_at"),
                }
            )
        return {"ok": True, "runs": runs[:safe_limit], "count": len(runs)}

    def resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent") -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        clean_response = str(user_response or "").strip()
        clean_requested_by = str(requested_by or "main_agent").strip() or "main_agent"
        if not clean_run_id:
            return {"ok": False, "error": "run_id is required."}
        if not clean_response:
            return {"ok": False, "error": "user_response is required."}

        row = self._store.get_run(run_id=clean_run_id)
        if row is None:
            return {"ok": False, "error": "run not found"}
        if str(row.get("status") or "") != "waiting_for_user":
            return {"ok": False, "error": "run is not waiting for user input"}

        out = self._store.resume_waiting_run(
            run_id=clean_run_id,
            user_response=clean_response,
            requested_by=clean_requested_by,
        )
        if out.get("ok"):
            profile_id = str(row.get("profile_id") or "unknown")
            waiting = out.get("waiting") if isinstance(out.get("waiting"), dict) else {}
            self._log_task_agent_event(
                event_type="run_resumed",
                profile_id=profile_id,
                run_id=clean_run_id,
                detail=f"resumed_by={clean_requested_by}",
                origin=str(
                    row.get("payload", {}).get("origin")
                    or row.get("payload", {}).get("trigger")
                    or "manual"
                )
                if isinstance(row.get("payload"), dict)
                else "manual",
                extra_payload={
                    "request_id": waiting.get("request_id"),
                    "question": waiting.get("question"),
                    "context": waiting.get("context") if isinstance(waiting.get("context"), dict) else {},
                    "expires_at": waiting.get("expires_at"),
                },
            )
            self._dispatch_available()
        return out

    def upsert_task_state(
        self,
        *,
        task_id: str,
        state_key: str,
        value: dict[str, Any],
        updated_by: str = "task_runtime",
    ) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_key = str(state_key or "").strip()
        if not clean_task_id:
            return {"ok": False, "error": "task_id is required."}
        if not clean_key:
            return {"ok": False, "error": "state_key is required."}

        value_json = json.dumps(value if isinstance(value, dict) else {})
        return self.execute_sql(
            sql=(
                "INSERT INTO task_state_kv(task_id, state_key, value_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?) "
                "ON CONFLICT(task_id, state_key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at, updated_by = excluded.updated_by;"
            ),
            params=[clean_task_id, clean_key, value_json, str(updated_by or "task_runtime")],
            read_only=False,
        )

    def get_task_state(self, *, task_id: str, state_key: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_key = str(state_key or "").strip()
        if not clean_task_id or not clean_key:
            return {"ok": False, "error": "task_id and state_key are required."}
        out = self.execute_sql(
            sql="SELECT task_id, state_key, value_json, updated_at, updated_by FROM task_state_kv WHERE task_id = ? AND state_key = ?;",
            params=[clean_task_id, clean_key],
            read_only=True,
            max_rows=1,
        )
        rows = out.get("rows") if isinstance(out.get("rows"), list) else []
        row = rows[0] if rows else None
        if not isinstance(row, dict):
            return {"ok": True, "task_id": clean_task_id, "state_key": clean_key, "value": None}
        try:
            value = json.loads(str(row.get("value_json") or "{}"))
        except Exception:
            value = {}
        return {
            "ok": True,
            "task_id": clean_task_id,
            "state_key": clean_key,
            "value": value if isinstance(value, dict) else {},
            "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by"),
        }

    def mark_task_item_seen(
        self,
        *,
        task_id: str,
        provider: str,
        item_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_provider = str(provider or "").strip()
        clean_item_key = str(item_key or "").strip()
        if not clean_task_id or not clean_provider or not clean_item_key:
            return {"ok": False, "error": "task_id, provider, and item_key are required."}
        meta_json = json.dumps(metadata if isinstance(metadata, dict) else {})
        return self.execute_sql(
            sql=(
                "INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1) "
                "ON CONFLICT(task_id, provider, item_key) DO UPDATE SET "
                "metadata_json = excluded.metadata_json, last_seen_at = excluded.last_seen_at, seen_count = task_seen_items.seen_count + 1;"
            ),
            params=[clean_task_id, clean_provider, clean_item_key, meta_json],
            read_only=False,
        )

    def has_task_item_seen(self, *, task_id: str, provider: str, item_key: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_provider = str(provider or "").strip()
        clean_item_key = str(item_key or "").strip()
        if not clean_task_id or not clean_provider or not clean_item_key:
            return {"ok": False, "error": "task_id, provider, and item_key are required."}
        out = self.execute_sql(
            sql=(
                "SELECT seen_count, first_seen_at, last_seen_at "
                "FROM task_seen_items WHERE task_id = ? AND provider = ? AND item_key = ?;"
            ),
            params=[clean_task_id, clean_provider, clean_item_key],
            read_only=True,
            max_rows=1,
        )
        rows = out.get("rows") if isinstance(out.get("rows"), list) else []
        if not rows:
            return {"ok": True, "seen": False, "seen_count": 0}
        row = rows[0] if isinstance(rows[0], dict) else {}
        return {
            "ok": True,
            "seen": True,
            "seen_count": int(row.get("seen_count") or 0),
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
        }

    def list_defined_tasks(self) -> dict[str, Any]:
        tasks = _load_task_profiles()
        out: list[dict[str, Any]] = []
        for task_id, payload in sorted(tasks.items(), key=lambda item: item[0]):
            kind = str(payload.get("kind") or "script")
            out.append(
                {
                    "task_id": task_id,
                    "name": str(payload.get("name") or task_id),
                    "kind": kind,
                    "entrypoint_path": payload.get("entrypoint_path"),
                    "module": payload.get("module"),
                    "resources_path": payload.get("resources_path"),
                    "queue_group": payload.get("queue_group"),
                    "timeout_sec": payload.get("timeout_sec"),
                    "retry_policy": payload.get("retry_policy"),
                }
            )
        return {"ok": True, "tasks": out}

    def upsert_schedule(
        self,
        *,
        schedule_id: str | None,
        task_id: str,
        enabled: bool,
        mode: str,
        execution_order: int,
        run_frequency_minutes: int | None = None,
        timezone: str | None = None,
        run_times: list[str] | None = None,
        days_of_week: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return {"ok": False, "error": "task_id is required."}
        tasks = _load_task_profiles()
        if clean_task_id not in tasks:
            return {"ok": False, "error": f"Unknown task_id `{clean_task_id}`."}

        clean_schedule_id = str(schedule_id or "").strip()
        if not clean_schedule_id:
            clean_schedule_id = f"sched_{_sanitize_task_id(clean_task_id)}_{uuid4().hex[:8]}"

        mode_value = str(mode or "frequency").strip().lower()
        if mode_value == "interval":
            mode_value = "frequency"
        if mode_value not in {"frequency", "calendar"}:
            return {"ok": False, "error": "mode must be `frequency` or `calendar`."}

        payload: dict[str, Any] = {
            "schedule_id": clean_schedule_id,
            "profile_id": clean_task_id,
            "enabled": bool(enabled),
            "mode": mode_value,
            "execution_order": int(execution_order) if execution_order >= 0 else 100,
        }

        if mode_value == "frequency":
            if not isinstance(run_frequency_minutes, int) or run_frequency_minutes <= 0:
                return {"ok": False, "error": "run_frequency_minutes must be > 0 for frequency mode."}
            payload["run_frequency_minutes"] = int(run_frequency_minutes)
            payload["run_times"] = []
            payload["days_of_week"] = []
        else:
            clean_timezone = str(timezone or "America/New_York").strip() or "America/New_York"
            clean_run_times = [str(item).strip() for item in (run_times or []) if isinstance(item, str) and str(item).strip()]
            if not clean_run_times:
                return {"ok": False, "error": "run_times is required for calendar mode."}
            payload["timezone"] = clean_timezone
            payload["run_times"] = clean_run_times
            payload["days_of_week"] = [str(item).strip().lower() for item in (days_of_week or []) if isinstance(item, str)]

        out = self._store.upsert_schedule(payload)
        if not out.get("ok"):
            return out
        return {"ok": True, "schedule_id": clean_schedule_id}

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return self._store.delete_schedule(schedule_id=schedule_id)

    def _check_in_payload(self) -> list[dict[str, Any]]:
        profiles = _load_task_profiles()
        runs = self._store.list_runs(limit=500)

        queued_runs = [r for r in runs if r.get("status") == "queued"]
        queued_sorted = sorted(queued_runs, key=lambda item: str(item.get("queued_at") or ""))
        queue_position = {str(run.get("run_id")): idx + 1 for idx, run in enumerate(queued_sorted)}

        out: list[dict[str, Any]] = []
        for profile_id, profile in sorted(profiles.items()):
            name = str(profile.get("name") or profile_id)
            profile_runs = [r for r in runs if str(r.get("profile_id") or "") == profile_id]
            running = [r for r in profile_runs if r.get("status") == "running"]
            queued = [r for r in profile_runs if r.get("status") == "queued"]
            waiting = [r for r in profile_runs if r.get("status") == "waiting_for_user"]
            historical = [r for r in profile_runs if r.get("status") in {"done", "failed", "blocked"}]

            current_run = (
                running[0]
                if running
                else (waiting[0] if waiting else (queued[0] if queued else None))
            )
            current_run_id = str(current_run.get("run_id")) if isinstance(current_run, dict) else None

            if running:
                state = "running"
            elif waiting:
                state = "waiting_for_user"
            elif queued:
                state = "queued"
            else:
                state = "free"

            description = None
            if current_run_id and current_run_id in self._active_descriptions:
                description = self._active_descriptions[current_run_id]
            elif isinstance(current_run, dict):
                payload = current_run.get("payload") if isinstance(current_run.get("payload"), dict) else {}
                description = self._runner.describe_run(profile_id=profile_id, payload=payload)
                if state == "waiting_for_user":
                    waiting = payload.get("waiting") if isinstance(payload.get("waiting"), dict) else {}
                    question = waiting.get("question")
                    if isinstance(question, str) and question.strip():
                        description = f"waiting for user: {question.strip()[:140]}"

            last_result = None
            if historical:
                latest = historical[0]
                last_result = {
                    "status": latest.get("status"),
                    "summary": latest.get("summary"),
                    "error": latest.get("error"),
                    "finished_at": latest.get("finished_at"),
                }

            out.append(
                {
                    "profile_id": profile_id,
                    "name": name,
                    "state": state,
                    "current_run_id": current_run_id,
                    "current_description": description,
                    "started_at": current_run.get("started_at") if isinstance(current_run, dict) else None,
                    "queue_position": queue_position.get(current_run_id) if state == "queued" and current_run_id else None,
                    "waiting_question": (
                        current_run.get("payload", {}).get("waiting", {}).get("question")
                        if state == "waiting_for_user"
                        and isinstance(current_run, dict)
                        and isinstance(current_run.get("payload"), dict)
                        and isinstance(current_run.get("payload", {}).get("waiting"), dict)
                        else None
                    ),
                    "last_result": last_result,
                }
            )

        return out

    def status(self) -> dict[str, Any]:
        settings = self._refresh_settings()
        counts = self._store.runtime_counts()
        metrics = self._store.runtime_metrics()
        with self._lock:
            running = self._loop_thread is not None and self._loop_thread.is_alive()
            active = len(self._active_threads)
            event_buffer_count = len(self._events)
        db_queue_health = self._db_queue.health()
        slots = self._task_slot_payload()
        busy_slots = sum(1 for slot in slots if slot.get("state") == "busy")
        free_slots = sum(1 for slot in slots if slot.get("state") == "free" and bool(slot.get("enabled", True)))
        disabled_slots = sum(1 for slot in slots if not bool(slot.get("enabled", True)))

        warnings: list[str] = []
        if counts["queued_count"] >= settings.queue_warning_threshold > 0:
            warnings.append("queue_depth_high")
        longest_running = metrics.get("longest_running_age_sec")
        if isinstance(longest_running, (int, float)) and longest_running >= settings.running_age_warning_sec > 0:
            warnings.append("running_task_stale")

        runs = self._store.list_runs(limit=200)
        active_runs = [row for row in runs if str(row.get("status") or "") == "running"]
        queued_preview = [row for row in runs if str(row.get("status") or "") == "queued"][:10]
        waiting_preview = [row for row in runs if str(row.get("status") or "") == "waiting_for_user"][:10]

        return {
            "ok": True,
            "service": {
                "running": running,
                "enabled_in_config": settings.enabled,
                "poll_interval_sec": settings.poll_interval_sec,
                "heartbeat_poll_interval_sec": settings.heartbeat_poll_interval_sec,
                "task_runner_concurrency": settings.task_runner_concurrency,
                "scheduler_db_path": str(self._store.db_path),
                "run_history_retention_days": settings.run_history_retention_days,
                "run_history_max_rows": settings.run_history_max_rows,
                "memory_manager_sweep_interval_sec": settings.memory_manager_sweep_interval_sec,
                "memory_manager_completion_debounce_sec": settings.memory_manager_completion_debounce_sec,
                "queue_warning_threshold": settings.queue_warning_threshold,
                "running_age_warning_sec": settings.running_age_warning_sec,
                "db_queue_busy_timeout_ms": settings.db_queue_busy_timeout_ms,
                "db_queue_default_max_rows": settings.db_queue_default_max_rows,
                "waiting_for_user_timeout_sec": settings.waiting_for_user_timeout_sec,
            },
            "runtime": {
                "queued_count": counts["queued_count"],
                "running_count": counts["running_count"],
                "waiting_count": counts.get("waiting_count", 0),
                "active_task_threads": active,
                "task_slot_busy_count": busy_slots,
                "task_slot_free_count": free_slots,
                "task_slot_disabled_count": disabled_slots,
                "task_event_buffer_count": event_buffer_count,
                **metrics,
                "warnings": warnings,
                "active_runs": active_runs,
                "queued_runs_preview": queued_preview,
                "waiting_runs_preview": waiting_preview,
            },
            "task_agents": self._check_in_payload(),
            "task_slots": slots,
            "db_queue": db_queue_health,
        }

    def list_schedules(self) -> dict[str, Any]:
        return {"ok": True, "schedules": self._store.list_schedules()}

    def list_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "runs": self._store.list_runs(limit=limit)}

    def metrics(self) -> dict[str, Any]:
        status = self.status()
        if not isinstance(status, dict):
            return {"ok": False, "source": "central_service", "error": "status_unavailable"}
        return {
            "ok": bool(status.get("ok")),
            "source": "central_service_metrics",
            "service": status.get("service"),
            "runtime": status.get("runtime"),
            "recent_events": self._recent_events(limit=20),
        }

    def execute_sql(
        self,
        *,
        sql: str,
        params: Any = None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        settings = self._refresh_settings()
        safe_max_rows = (
            int(max_rows)
            if isinstance(max_rows, int) and max_rows > 0
            else int(settings.db_queue_default_max_rows)
        )
        out = self._db_queue.execute(
            sql=sql,
            params=params,
            read_only=read_only,
            timeout_sec=timeout_sec,
            max_rows=safe_max_rows,
        )
        out["source"] = "central_db_queue"
        return out

    def list_forward_events(self, *, consume: bool = True) -> dict[str, Any]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for event in self._events:
                if not event.get("forward_to_user"):
                    continue
                if event.get("forwarded"):
                    continue
                out.append(
                    {
                        "event_id": event["event_id"],
                        "type": event["type"],
                        "timestamp": event["timestamp"],
                        "payload": event.get("payload", {}),
                    }
                )
                if consume:
                    event["forwarded"] = True
        return {"ok": True, "events": out, "count": len(out), "consumed": consume}


_CENTRAL_SERVICE: CentralService | None = None


def get_central_service() -> CentralService:
    global _CENTRAL_SERVICE
    if _CENTRAL_SERVICE is None:
        _CENTRAL_SERVICE = CentralService()
    return _CENTRAL_SERVICE
