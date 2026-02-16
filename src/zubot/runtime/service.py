"""Shared runtime ownership facade for daemon/app entrypoints."""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any

from src.zubot.core.control_panel import get_control_panel
from src.zubot.core.memory_summary_worker import get_memory_summary_worker


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
        return {
            "ok": True,
            "source": "runtime_service",
            "runtime": {
                "started": self._started,
                "last_start_source": self._last_start_source,
                "last_stop_source": self._last_stop_source,
            },
            "central": status.get("service") if isinstance(status, dict) else {},
            "task_runtime": status.get("runtime") if isinstance(status, dict) else {},
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

    def restart_session_context(self, *, session_id: str = "default", history_limit: int | None = None) -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.restart_session_context(session_id, history_limit=history_limit)

    def session_context_snapshot(self, *, session_id: str = "default") -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.get_session_context_snapshot(session_id)

    def session_history(self, *, session_id: str = "default", limit: int = 100) -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.get_session_history(session_id, limit=limit)

    def clear_session_history(self, *, session_id: str = "default") -> dict[str, Any]:
        mod = self._chat_logic_module()
        return mod.clear_session_history(session_id)

    def central_status(self) -> dict[str, Any]:
        return get_control_panel().status()

    def central_start(self) -> dict[str, Any]:
        return get_control_panel().start()

    def central_stop(self) -> dict[str, Any]:
        return get_control_panel().stop()

    def central_schedules(self) -> dict[str, Any]:
        return get_control_panel().list_schedules()

    def central_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return get_control_panel().list_runs(limit=limit)

    def central_metrics(self) -> dict[str, Any]:
        return get_control_panel().metrics()

    def central_trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        return get_control_panel().enqueue_task(task_id=profile_id, description=description)

    def central_enqueue_agentic_task(
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
        return get_control_panel().enqueue_agentic_task(
            task_name=task_name,
            instructions=instructions,
            requested_by=requested_by,
            model_tier=model_tier,
            tool_access=tool_access,
            skill_access=skill_access,
            timeout_sec=timeout_sec,
            metadata=metadata,
        )

    def central_kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return get_control_panel().kill_run(run_id=run_id, requested_by=requested_by)

    def central_waiting_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return get_control_panel().list_waiting_runs(limit=limit)

    def central_resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return get_control_panel().resume_run(
            run_id=run_id,
            user_response=user_response,
            requested_by=requested_by,
        )

    def central_execute_sql(
        self,
        *,
        sql: str,
        params: Any = None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        return get_control_panel().execute_sql(
            sql=sql,
            params=params,
            read_only=read_only,
            timeout_sec=timeout_sec,
            max_rows=max_rows,
        )

    def central_upsert_task_state(
        self,
        *,
        task_id: str,
        state_key: str,
        value: dict[str, Any],
        updated_by: str = "task_runtime",
    ) -> dict[str, Any]:
        return get_control_panel().upsert_task_state(
            task_id=task_id,
            state_key=state_key,
            value=value,
            updated_by=updated_by,
        )

    def central_get_task_state(self, *, task_id: str, state_key: str) -> dict[str, Any]:
        return get_control_panel().get_task_state(task_id=task_id, state_key=state_key)

    def central_mark_task_item_seen(
        self,
        *,
        task_id: str,
        provider: str,
        item_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return get_control_panel().mark_task_item_seen(
            task_id=task_id,
            provider=provider,
            item_key=item_key,
            metadata=metadata,
        )

    def central_has_task_item_seen(self, *, task_id: str, provider: str, item_key: str) -> dict[str, Any]:
        return get_control_panel().has_task_item_seen(task_id=task_id, provider=provider, item_key=item_key)

    def central_list_defined_tasks(self) -> dict[str, Any]:
        return get_control_panel().list_defined_tasks()

    def central_upsert_task_profile(
        self,
        *,
        task_id: str,
        name: str | None = None,
        kind: str = "script",
        entrypoint_path: str | None = None,
        module: str | None = None,
        resources_path: str | None = None,
        queue_group: str | None = None,
        timeout_sec: int | None = None,
        retry_policy: dict[str, Any] | None = None,
        enabled: bool = True,
        source: str = "ui",
    ) -> dict[str, Any]:
        return get_control_panel().upsert_task_profile(
            task_id=task_id,
            name=name,
            kind=kind,
            entrypoint_path=entrypoint_path,
            module=module,
            resources_path=resources_path,
            queue_group=queue_group,
            timeout_sec=timeout_sec,
            retry_policy=retry_policy,
            enabled=enabled,
            source=source,
        )

    def central_delete_task_profile(self, *, task_id: str) -> dict[str, Any]:
        return get_control_panel().delete_task_profile(task_id=task_id)

    def central_upsert_schedule(
        self,
        *,
        schedule_id: str | None,
        task_id: str,
        enabled: bool,
        mode: str,
        execution_order: int,
        misfire_policy: str = "queue_latest",
        run_frequency_minutes: int | None = None,
        timezone: str | None = None,
        run_times: list[str] | None = None,
        days_of_week: list[str] | None = None,
    ) -> dict[str, Any]:
        return get_control_panel().upsert_schedule(
            schedule_id=schedule_id,
            task_id=task_id,
            enabled=enabled,
            mode=mode,
            execution_order=execution_order,
            misfire_policy=misfire_policy,
            run_frequency_minutes=run_frequency_minutes,
            timezone=timezone,
            run_times=run_times,
            days_of_week=days_of_week,
        )

    def central_delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return get_control_panel().delete_schedule(schedule_id=schedule_id)


_RUNTIME_SERVICE: RuntimeService | None = None


def get_runtime_service() -> RuntimeService:
    global _RUNTIME_SERVICE
    if _RUNTIME_SERVICE is None:
        _RUNTIME_SERVICE = RuntimeService()
    return _RUNTIME_SERVICE
