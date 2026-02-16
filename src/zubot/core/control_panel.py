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

    def upsert_task_profile(
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
        return get_central_service().upsert_task_profile(
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

    def delete_task_profile(self, *, task_id: str) -> dict[str, Any]:
        return get_central_service().delete_task_profile(task_id=task_id)

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

    def list_waiting_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return get_central_service().list_waiting_runs(limit=limit)

    def resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return get_central_service().resume_run(
            run_id=run_id,
            user_response=user_response,
            requested_by=requested_by,
        )

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

    def upsert_task_state(
        self,
        *,
        task_id: str,
        state_key: str,
        value: dict[str, Any],
        updated_by: str = "task_runtime",
    ) -> dict[str, Any]:
        return get_central_service().upsert_task_state(
            task_id=task_id,
            state_key=state_key,
            value=value,
            updated_by=updated_by,
        )

    def get_task_state(self, *, task_id: str, state_key: str) -> dict[str, Any]:
        return get_central_service().get_task_state(task_id=task_id, state_key=state_key)

    def mark_task_item_seen(
        self,
        *,
        task_id: str,
        provider: str,
        item_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return get_central_service().mark_task_item_seen(
            task_id=task_id,
            provider=provider,
            item_key=item_key,
            metadata=metadata,
        )

    def has_task_item_seen(self, *, task_id: str, provider: str, item_key: str) -> dict[str, Any]:
        return get_central_service().has_task_item_seen(task_id=task_id, provider=provider, item_key=item_key)


_CONTROL_PANEL: ControlPanel | None = None


def get_control_panel() -> ControlPanel:
    global _CONTROL_PANEL
    if _CONTROL_PANEL is None:
        _CONTROL_PANEL = ControlPanel()
    return _CONTROL_PANEL
