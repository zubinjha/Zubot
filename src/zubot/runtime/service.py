"""Shared runtime ownership facade for daemon/app entrypoints."""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any

from src.zubot.core.central_service import get_central_service
from src.zubot.core.memory_summary_worker import get_memory_summary_worker
from src.zubot.core.worker_manager import get_worker_manager


class RuntimeService:
    """Single authority for runtime lifecycle + app-facing operations."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._started = False
        self._last_start_source: str | None = None
        self._last_stop_source: str | None = None

    @staticmethod
    def _chat_logic_module() -> Any:
        return import_module("app.chat_logic")

    def start(self, *, start_central_if_enabled: bool = True, source: str = "runtime") -> dict[str, Any]:
        with self._lock:
            already_started = self._started
            self._started = True
            self._last_start_source = source

        get_memory_summary_worker().start()
        central_started = False
        if start_central_if_enabled:
            status = self.central_status()
            service = status.get("service") if isinstance(status, dict) else {}
            enabled = bool(service.get("enabled_in_config")) if isinstance(service, dict) else False
            running = bool(service.get("running")) if isinstance(service, dict) else False
            if enabled and not running:
                out = self.central_start()
                central_started = bool(out.get("ok")) and bool(out.get("running"))

        return {
            "ok": True,
            "source": "runtime_service",
            "already_started": already_started,
            "started": True,
            "start_source": source,
            "central_started": central_started,
        }

    def stop(self, *, stop_central: bool = True, source: str = "runtime") -> dict[str, Any]:
        central_stopped = False
        if stop_central:
            status = self.central_status()
            service = status.get("service") if isinstance(status, dict) else {}
            running = bool(service.get("running")) if isinstance(service, dict) else False
            if running:
                out = self.central_stop()
                central_stopped = bool(out.get("ok"))

        with self._lock:
            self._started = False
            self._last_stop_source = source
        get_memory_summary_worker().stop()

        return {
            "ok": True,
            "source": "runtime_service",
            "stopped": True,
            "stop_source": source,
            "central_stopped": central_stopped,
        }

    def health(self) -> dict[str, Any]:
        status = self.central_status()
        workers = self.list_workers()
        return {
            "ok": True,
            "source": "runtime_service",
            "runtime": {
                "started": self._started,
                "last_start_source": self._last_start_source,
                "last_stop_source": self._last_stop_source,
            },
            "central": status.get("service") if isinstance(status, dict) else {},
            "workers": workers.get("runtime") if isinstance(workers, dict) else {},
        }

    def chat(self, *, message: str, session_id: str = "default", allow_llm_fallback: bool = True) -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.handle_chat_message(message, allow_llm_fallback=allow_llm_fallback, session_id=session_id)

    def init_session(self, *, session_id: str = "default") -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.initialize_session_context(session_id)

    def reset_session(self, *, session_id: str = "default") -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.reset_session_context(session_id)

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
        return get_worker_manager().spawn_worker(
            title=title,
            instructions=instructions,
            model_tier=model_tier,
            tool_access=tool_access or [],
            skill_access=skill_access or [],
            preload_files=preload_files or [],
            metadata=metadata or {},
        )

    def cancel_worker(self, *, worker_id: str) -> dict[str, Any]:
        return get_worker_manager().cancel_worker(worker_id)

    def reset_worker_context(self, *, worker_id: str) -> dict[str, Any]:
        return get_worker_manager().reset_worker_context(worker_id)

    def message_worker(self, *, worker_id: str, message: str, model_tier: str = "medium") -> dict[str, Any]:
        return get_worker_manager().message_worker(worker_id=worker_id, message=message, model_tier=model_tier)

    def get_worker(self, *, worker_id: str) -> dict[str, Any]:
        return get_worker_manager().get_worker(worker_id)

    def list_workers(self) -> dict[str, Any]:
        return get_worker_manager().list_workers()

    def central_status(self) -> dict[str, Any]:
        return get_central_service().status()

    def central_start(self) -> dict[str, Any]:
        return get_central_service().start()

    def central_stop(self) -> dict[str, Any]:
        return get_central_service().stop()

    def central_schedules(self) -> dict[str, Any]:
        return get_central_service().list_schedules()

    def central_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return get_central_service().list_runs(limit=limit)

    def central_metrics(self) -> dict[str, Any]:
        return get_central_service().metrics()

    def central_trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        return get_central_service().trigger_profile(profile_id=profile_id, description=description)

    def central_list_defined_tasks(self) -> dict[str, Any]:
        return get_central_service().list_defined_tasks()

    def central_upsert_schedule(
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
        return get_central_service().upsert_schedule(
            schedule_id=schedule_id,
            task_id=task_id,
            enabled=enabled,
            mode=mode,
            execution_order=execution_order,
            run_frequency_minutes=run_frequency_minutes,
            timezone=timezone,
            run_times=run_times,
            days_of_week=days_of_week,
        )

    def central_delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return get_central_service().delete_schedule(schedule_id=schedule_id)


_RUNTIME_SERVICE: RuntimeService | None = None


def get_runtime_service() -> RuntimeService:
    global _RUNTIME_SERVICE
    if _RUNTIME_SERVICE is None:
        _RUNTIME_SERVICE = RuntimeService()
    return _RUNTIME_SERVICE
