"""Control Panel facade for central orchestration services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .central_service import get_central_service
from .llm_client import call_llm


@dataclass(slots=True)
class ControlPanel:
    """Thin orchestration boundary for queue/runtime/LLM helpers."""

    def llm_call(self, **kwargs: Any) -> dict[str, Any]:
        return call_llm(**kwargs)

    def status(self) -> dict[str, Any]:
        return get_central_service().status()

    def start(self) -> dict[str, Any]:
        return get_central_service().start()

    def stop(self) -> dict[str, Any]:
        return get_central_service().stop()

    def list_schedules(self) -> dict[str, Any]:
        return get_central_service().list_schedules()

    def list_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return get_central_service().list_runs(limit=limit)

    def metrics(self) -> dict[str, Any]:
        return get_central_service().metrics()

    def list_defined_tasks(self) -> dict[str, Any]:
        return get_central_service().list_defined_tasks()

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

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return get_central_service().delete_schedule(schedule_id=schedule_id)

    def enqueue_task(self, *, task_id: str, description: str | None = None) -> dict[str, Any]:
        return get_central_service().trigger_profile(profile_id=task_id, description=description)

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
        return get_central_service().enqueue_agentic_task(
            task_name=task_name,
            instructions=instructions,
            requested_by=requested_by,
            model_tier=model_tier,
            tool_access=tool_access,
            skill_access=skill_access,
            timeout_sec=timeout_sec,
            metadata=metadata,
        )

    def kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return get_central_service().kill_run(run_id=run_id, requested_by=requested_by)

    def execute_sql(
        self,
        *,
        sql: str,
        params: Any = None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        return get_central_service().execute_sql(
            sql=sql,
            params=params,
            read_only=read_only,
            timeout_sec=timeout_sec,
            max_rows=max_rows,
        )


_CONTROL_PANEL: ControlPanel | None = None


def get_control_panel() -> ControlPanel:
    global _CONTROL_PANEL
    if _CONTROL_PANEL is None:
        _CONTROL_PANEL = ControlPanel()
    return _CONTROL_PANEL

