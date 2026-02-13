"""Central service loop for scheduled task-agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4

from .config_loader import load_config
from .daily_memory import append_daily_memory_entry, local_day_str
from .memory_index import enqueue_day_summary_job, increment_day_message_count
from .memory_manager import MemoryManager, MemoryManagerSettings
from .memory_summary_worker import get_memory_summary_worker
from .task_agent_runner import TaskAgentRunner
from .task_scheduler_store import TaskSchedulerStore, resolve_scheduler_db_path


@dataclass(slots=True)
class CentralServiceSettings:
    enabled: bool = False
    poll_interval_sec: int = 60
    task_runner_concurrency: int = 2
    scheduler_db_path: str = "memory/central/zubot_core.db"
    worker_slot_reserve_for_workers: int = 2
    run_history_retention_days: int = 30
    run_history_max_rows: int = 5000
    memory_manager_sweep_interval_sec: int = 12 * 60 * 60
    memory_manager_completion_debounce_sec: int = 5 * 60
    queue_warning_threshold: int = 25
    running_age_warning_sec: int = 1800


def _load_central_settings() -> CentralServiceSettings:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}

    central = cfg.get("central_service") if isinstance(cfg, dict) else None
    if not isinstance(central, dict):
        return CentralServiceSettings()

    enabled = bool(central.get("enabled", False))
    poll = central.get("poll_interval_sec", 60)
    conc = central.get("task_runner_concurrency", 2)
    db_path = central.get("scheduler_db_path", "memory/central/zubot_core.db")
    reserve = central.get("worker_slot_reserve_for_workers", 2)
    retention_days = central.get("run_history_retention_days", 30)
    history_max_rows = central.get("run_history_max_rows", 5000)
    memory_sweep_sec = central.get("memory_manager_sweep_interval_sec", 12 * 60 * 60)
    memory_debounce_sec = central.get("memory_manager_completion_debounce_sec", 5 * 60)
    queue_warning = central.get("queue_warning_threshold", 25)
    running_age_warning = central.get("running_age_warning_sec", 1800)

    return CentralServiceSettings(
        enabled=enabled,
        poll_interval_sec=int(poll) if isinstance(poll, int) and poll > 0 else 60,
        task_runner_concurrency=int(conc) if isinstance(conc, int) and conc > 0 else 2,
        scheduler_db_path=str(db_path) if isinstance(db_path, str) and db_path.strip() else "memory/central/zubot_core.db",
        worker_slot_reserve_for_workers=int(reserve) if isinstance(reserve, int) and reserve >= 0 else 2,
        run_history_retention_days=int(retention_days) if isinstance(retention_days, int) and retention_days >= 0 else 30,
        run_history_max_rows=int(history_max_rows) if isinstance(history_max_rows, int) and history_max_rows >= 0 else 5000,
        memory_manager_sweep_interval_sec=int(memory_sweep_sec) if isinstance(memory_sweep_sec, int) and memory_sweep_sec > 0 else 12 * 60 * 60,
        memory_manager_completion_debounce_sec=int(memory_debounce_sec) if isinstance(memory_debounce_sec, int) and memory_debounce_sec > 0 else 5 * 60,
        queue_warning_threshold=int(queue_warning) if isinstance(queue_warning, int) and queue_warning >= 0 else 25,
        running_age_warning_sec=int(running_age_warning) if isinstance(running_age_warning, int) and running_age_warning >= 0 else 1800,
    )


def _load_task_profiles() -> dict[str, dict[str, Any]]:
    try:
        cfg = load_config()
    except Exception:
        return {}

    out: dict[str, dict[str, Any]] = {}

    predefined_root = cfg.get("pre_defined_tasks") if isinstance(cfg, dict) else None
    predefined_tasks = predefined_root.get("tasks") if isinstance(predefined_root, dict) else None
    if isinstance(predefined_tasks, dict):
        for task_id, payload in predefined_tasks.items():
            if not isinstance(task_id, str) or not isinstance(payload, dict):
                continue
            out[task_id] = {
                "name": payload.get("name") or task_id,
                "entrypoint_path": payload.get("entrypoint_path"),
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
        if state in {"running", "queued"} and description:
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
        self._runner = TaskAgentRunner()
        self._memory_manager = MemoryManager()
        self._lock = RLock()
        self._stop_event = Event()
        self._loop_thread: Thread | None = None
        self._active_threads: dict[str, Thread] = {}
        self._active_descriptions: dict[str, str] = {}
        self._events: list[dict[str, Any]] = []

    @staticmethod
    def _utc_now_iso() -> str:
        from datetime import UTC, datetime

        return datetime.now(tz=UTC).isoformat()

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

    @staticmethod
    def _is_high_signal_task_memory_event(event_type: str) -> bool:
        return event_type in {"run_queued", "run_finished", "run_failed", "run_blocked"}

    def _log_task_agent_event(self, *, event_type: str, profile_id: str, run_id: str, detail: str) -> None:
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
        self._record_event(
            event_type="task_agent_event",
            payload={"event_type": event_type, "profile_id": profile_id, "run_id": run_id, "detail": detail},
            forward_to_user=True,
        )

    def _memory_manager_settings(self) -> MemoryManagerSettings:
        return MemoryManagerSettings(
            sweep_interval_sec=self._settings.memory_manager_sweep_interval_sec,
            completion_debounce_sec=self._settings.memory_manager_completion_debounce_sec,
        )

    def _run_housekeeping(self, *, on_completion: bool = False) -> dict[str, Any]:
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
        return {"ok": True, "prune": prune, "memory": memory}

    def _refresh_settings(self) -> CentralServiceSettings:
        settings = _load_central_settings()
        with self._lock:
            self._settings = settings
            resolved = resolve_scheduler_db_path(settings.scheduler_db_path)
            if str(self._store.db_path) != str(resolved):
                # Reload store if db path changed.
                self._store = TaskSchedulerStore(db_path=settings.scheduler_db_path)
        return settings

    def start(self) -> dict[str, Any]:
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
        with self._lock:
            self._loop_thread = None
        return {"ok": True, "running": False}

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            wait_for = max(1, self._settings.poll_interval_sec)
            self._stop_event.wait(timeout=wait_for)

    def _execute_claimed_run(self, claimed: dict[str, Any]) -> None:
        run_id = str(claimed.get("run_id") or "")
        profile_id = str(claimed.get("profile_id") or "")
        payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
        self._log_task_agent_event(event_type="run_started", profile_id=profile_id, run_id=run_id, detail="started")

        try:
            result = self._runner.run_profile(profile_id=profile_id, payload=payload)
            status = str(result.get("status") or "done")
            if status not in {"done", "failed", "blocked"}:
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
            self._log_task_agent_event(event_type="run_finished", profile_id=profile_id, run_id=run_id, detail=detail)
        except Exception as exc:
            self._store.complete_run(run_id=run_id, status="failed", summary=None, error=str(exc))
            self._log_task_agent_event(
                event_type="run_failed",
                profile_id=profile_id,
                run_id=run_id,
                detail=f"error={str(exc)[:180]}",
            )
        finally:
            with self._lock:
                self._active_descriptions.pop(run_id, None)
                self._active_threads.pop(run_id, None)
            self._run_housekeeping(on_completion=True)

    def _dispatch_available(self) -> dict[str, Any]:
        started = 0
        while True:
            with self._lock:
                if len(self._active_threads) >= self._settings.task_runner_concurrency:
                    break

            claimed = self._store.claim_next_run()
            if claimed is None:
                break

            run_id = str(claimed.get("run_id") or "")
            profile_id = str(claimed.get("profile_id") or "")
            payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
            desc = self._runner.describe_run(profile_id=profile_id, payload=payload)

            thread = Thread(target=self._execute_claimed_run, args=(claimed,), daemon=True)
            with self._lock:
                self._active_descriptions[run_id] = desc
                self._active_threads[run_id] = thread
            thread.start()
            started += 1

        return {"ok": True, "started": started}

    def tick(self) -> dict[str, Any]:
        settings = self._refresh_settings()
        enqueue = self._store.enqueue_due_runs()
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
                )
        dispatch = self._dispatch_available()
        housekeeping = self._run_housekeeping(on_completion=False)
        return {
            "ok": True,
            "settings": {
                "enabled": settings.enabled,
                "poll_interval_sec": settings.poll_interval_sec,
                "task_runner_concurrency": settings.task_runner_concurrency,
                "scheduler_db_path": settings.scheduler_db_path,
                "worker_slot_reserve_for_workers": settings.worker_slot_reserve_for_workers,
                "run_history_retention_days": settings.run_history_retention_days,
                "run_history_max_rows": settings.run_history_max_rows,
                "memory_manager_sweep_interval_sec": settings.memory_manager_sweep_interval_sec,
                "memory_manager_completion_debounce_sec": settings.memory_manager_completion_debounce_sec,
                "queue_warning_threshold": settings.queue_warning_threshold,
                "running_age_warning_sec": settings.running_age_warning_sec,
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
                )
        self._dispatch_available()
        return out

    def list_defined_tasks(self) -> dict[str, Any]:
        tasks = _load_task_profiles()
        out: list[dict[str, Any]] = []
        for task_id, payload in sorted(tasks.items(), key=lambda item: item[0]):
            out.append(
                {
                    "task_id": task_id,
                    "name": str(payload.get("name") or task_id),
                    "entrypoint_path": payload.get("entrypoint_path"),
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
            historical = [r for r in profile_runs if r.get("status") in {"done", "failed", "blocked"}]

            current_run = running[0] if running else (queued[0] if queued else None)
            current_run_id = str(current_run.get("run_id")) if isinstance(current_run, dict) else None

            if running:
                state = "running"
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

        warnings: list[str] = []
        if counts["queued_count"] >= settings.queue_warning_threshold > 0:
            warnings.append("queue_depth_high")
        longest_running = metrics.get("longest_running_age_sec")
        if isinstance(longest_running, (int, float)) and longest_running >= settings.running_age_warning_sec > 0:
            warnings.append("running_task_stale")

        return {
            "ok": True,
            "service": {
                "running": running,
                "enabled_in_config": settings.enabled,
                "poll_interval_sec": settings.poll_interval_sec,
                "task_runner_concurrency": settings.task_runner_concurrency,
                "scheduler_db_path": str(self._store.db_path),
                "worker_slot_reserve_for_workers": settings.worker_slot_reserve_for_workers,
                "run_history_retention_days": settings.run_history_retention_days,
                "run_history_max_rows": settings.run_history_max_rows,
                "memory_manager_sweep_interval_sec": settings.memory_manager_sweep_interval_sec,
                "memory_manager_completion_debounce_sec": settings.memory_manager_completion_debounce_sec,
                "queue_warning_threshold": settings.queue_warning_threshold,
                "running_age_warning_sec": settings.running_age_warning_sec,
            },
            "runtime": {
                "queued_count": counts["queued_count"],
                "running_count": counts["running_count"],
                "active_task_threads": active,
                "task_event_buffer_count": event_buffer_count,
                **metrics,
                "warnings": warnings,
            },
            "task_agents": self._check_in_payload(),
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
