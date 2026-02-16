"""Central registry for callable tool contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.zubot.core.central_service import get_central_service, summarize_task_agent_check_in
from src.zubot.core.tool_registry_user import register_user_specific_tools
from src.zubot.tools.data.json_tools import read_json, write_json
from src.zubot.tools.data.text_search import search_text
from src.zubot.tools.kernel.filesystem import append_file, list_dir, path_exists, read_file, stat_path, write_file
from src.zubot.tools.kernel.location import get_location
from src.zubot.tools.kernel.time import get_current_time
from src.zubot.tools.kernel.weather import (
    get_future_weather,
    get_today_weather,
    get_weather,
    get_weather_24hr,
    get_week_outlook,
)
from src.zubot.tools.kernel.web_fetch import fetch_url
from src.zubot.tools.kernel.web_search import web_search

ToolHandler = Callable[..., dict[str, Any]]
_TOOLS_WITH_DEFAULT_LOCATION = {
    "get_current_time",
    "get_weather",
    "get_future_weather",
    "get_today_weather",
    "get_weather_24hr",
    "get_week_outlook",
}


@dataclass(frozen=True)
class ToolSpec:
    """Declarative metadata + callable for one tool."""

    name: str
    handler: ToolHandler
    category: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """In-memory registry with deterministic lookup and invocation."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or not isinstance(spec.name, str):
            raise ValueError("Tool name must be a non-empty string.")
        if spec.name in self._tools:
            raise ValueError(f"Tool `{spec.name}` is already registered.")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool `{name}`.") from exc

    def list_specs(self) -> list[ToolSpec]:
        return [self._tools[name] for name in sorted(self._tools)]

    def invoke(self, name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            spec = self.get(name)
        except KeyError as exc:
            return {
                "ok": False,
                "tool_name": name,
                "error": str(exc),
                "source": "tool_registry",
            }

        call_kwargs = dict(kwargs)
        if name in _TOOLS_WITH_DEFAULT_LOCATION and call_kwargs.get("location") is None:
            call_kwargs["location"] = get_location()

        try:
            result = spec.handler(**call_kwargs)
        except TypeError as exc:
            return {
                "ok": False,
                "tool_name": name,
                "error": f"Invalid arguments for `{name}`: {exc}",
                "source": "tool_registry",
            }
        except Exception as exc:  # pragma: no cover - defensive guard
            return {
                "ok": False,
                "tool_name": name,
                "error": f"Tool `{name}` failed: {exc}",
                "source": "tool_registry",
            }

        if isinstance(result, dict):
            return result
        return {
            "ok": False,
            "tool_name": name,
            "error": f"Tool `{name}` returned non-dict output.",
            "source": "tool_registry",
        }


def _get_task_agent_checkin(*, include_runs: bool = False, runs_limit: int = 20) -> dict[str, Any]:
    try:
        service = get_central_service()
        status = service.status()
    except Exception as exc:  # pragma: no cover - defensive guard
        return {"ok": False, "source": "central_service_checkin_error", "error": str(exc)}

    if not isinstance(status, dict) or status.get("ok") is not True:
        return {
            "ok": False,
            "source": "central_service_checkin_error",
            "error": "Failed to retrieve central service status.",
        }

    task_agents = status.get("task_agents") if isinstance(status.get("task_agents"), list) else []
    out: dict[str, Any] = {
        "ok": True,
        "source": "central_service_checkin",
        "summary": summarize_task_agent_check_in(task_agents),
        "service": status.get("service"),
        "runtime": status.get("runtime"),
        "task_agents": task_agents,
    }

    if include_runs:
        safe_limit = max(1, min(200, int(runs_limit)))
        runs_payload = service.list_runs(limit=safe_limit)
        out["runs"] = runs_payload.get("runs") if isinstance(runs_payload, dict) else []

    return out


def _enqueue_task(**kwargs: Any) -> dict[str, Any]:
    task_id = str(kwargs.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id is required.", "source": "tool_registry"}
    description_raw = kwargs.get("description")
    description = str(description_raw).strip() if isinstance(description_raw, str) and description_raw.strip() else None
    return get_central_service().trigger_profile(profile_id=task_id, description=description)


def _enqueue_agentic_task(**kwargs: Any) -> dict[str, Any]:
    instructions = str(kwargs.get("instructions") or "").strip()
    if not instructions:
        return {"ok": False, "error": "instructions is required.", "source": "tool_registry"}
    task_name_raw = kwargs.get("task_name")
    task_name = str(task_name_raw).strip() if isinstance(task_name_raw, str) and task_name_raw.strip() else "Background Research Task"
    requested_by_raw = kwargs.get("requested_by")
    requested_by = str(requested_by_raw).strip() if isinstance(requested_by_raw, str) and requested_by_raw.strip() else "main_agent"
    model_tier_raw = kwargs.get("model_tier")
    model_tier = str(model_tier_raw).strip().lower() if isinstance(model_tier_raw, str) and model_tier_raw.strip() else "medium"
    if model_tier not in {"low", "medium", "high"}:
        model_tier = "medium"
    timeout_raw = kwargs.get("timeout_sec")
    timeout_sec = int(timeout_raw) if isinstance(timeout_raw, int) and timeout_raw > 0 else 180
    tool_access = [str(item).strip() for item in kwargs.get("tool_access", []) if isinstance(item, str) and str(item).strip()]
    skill_access = [str(item).strip() for item in kwargs.get("skill_access", []) if isinstance(item, str) and str(item).strip()]
    metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
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


def _kill_task_run(**kwargs: Any) -> dict[str, Any]:
    run_id = str(kwargs.get("run_id") or "").strip()
    if not run_id:
        return {"ok": False, "error": "run_id is required.", "source": "tool_registry"}
    requested_by = str(kwargs.get("requested_by") or "main_agent").strip() or "main_agent"
    return get_central_service().kill_run(run_id=run_id, requested_by=requested_by)


def _resume_task_run(**kwargs: Any) -> dict[str, Any]:
    run_id = str(kwargs.get("run_id") or "").strip()
    if not run_id:
        return {"ok": False, "error": "run_id is required.", "source": "tool_registry"}
    user_response = str(kwargs.get("user_response") or "").strip()
    if not user_response:
        return {"ok": False, "error": "user_response is required.", "source": "tool_registry"}
    requested_by = str(kwargs.get("requested_by") or "main_agent").strip() or "main_agent"
    return get_central_service().resume_run(
        run_id=run_id,
        user_response=user_response,
        requested_by=requested_by,
    )


def _list_task_runs(**kwargs: Any) -> dict[str, Any]:
    limit_raw = kwargs.get("limit", 50)
    limit = int(limit_raw) if isinstance(limit_raw, int) else 50
    safe_limit = max(1, min(200, limit))
    return get_central_service().list_runs(limit=safe_limit)


def _list_waiting_runs(**kwargs: Any) -> dict[str, Any]:
    limit_raw = kwargs.get("limit", 50)
    limit = int(limit_raw) if isinstance(limit_raw, int) else 50
    safe_limit = max(1, min(200, limit))
    return get_central_service().list_waiting_runs(limit=safe_limit)


def _query_central_db(**kwargs: Any) -> dict[str, Any]:
    sql = str(kwargs.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "sql is required.", "source": "tool_registry"}
    read_only = bool(kwargs.get("read_only", True))
    timeout_raw = kwargs.get("timeout_sec")
    timeout_sec = float(timeout_raw) if isinstance(timeout_raw, (int, float)) and float(timeout_raw) > 0 else 5.0
    max_rows_raw = kwargs.get("max_rows")
    max_rows = int(max_rows_raw) if isinstance(max_rows_raw, int) and max_rows_raw > 0 else None
    params_raw = kwargs.get("params")
    params = params_raw if isinstance(params_raw, (list, dict)) else None
    return get_central_service().execute_sql(
        sql=sql,
        params=params,
        read_only=read_only,
        timeout_sec=timeout_sec,
        max_rows=max_rows,
    )


def _upsert_task_state(**kwargs: Any) -> dict[str, Any]:
    task_id = str(kwargs.get("task_id") or "").strip()
    state_key = str(kwargs.get("state_key") or "").strip()
    value = kwargs.get("value") if isinstance(kwargs.get("value"), dict) else {}
    updated_by = str(kwargs.get("updated_by") or "task_runtime").strip() or "task_runtime"
    return get_central_service().upsert_task_state(
        task_id=task_id,
        state_key=state_key,
        value=value,
        updated_by=updated_by,
    )


def _get_task_state(**kwargs: Any) -> dict[str, Any]:
    task_id = str(kwargs.get("task_id") or "").strip()
    state_key = str(kwargs.get("state_key") or "").strip()
    return get_central_service().get_task_state(task_id=task_id, state_key=state_key)


def _mark_task_item_seen(**kwargs: Any) -> dict[str, Any]:
    task_id = str(kwargs.get("task_id") or "").strip()
    provider = str(kwargs.get("provider") or "").strip()
    item_key = str(kwargs.get("item_key") or "").strip()
    metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    return get_central_service().mark_task_item_seen(
        task_id=task_id,
        provider=provider,
        item_key=item_key,
        metadata=metadata,
    )


def _has_task_item_seen(**kwargs: Any) -> dict[str, Any]:
    task_id = str(kwargs.get("task_id") or "").strip()
    provider = str(kwargs.get("provider") or "").strip()
    item_key = str(kwargs.get("item_key") or "").strip()
    return get_central_service().has_task_item_seen(task_id=task_id, provider=provider, item_key=item_key)


def _create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="enqueue_task",
            handler=_enqueue_task,
            category="orchestration",
            description="Queue a predefined task by task_id for task-agent execution.",
            parameters={
                "task_id": {"type": "string", "required": True},
                "description": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="enqueue_agentic_task",
            handler=_enqueue_agentic_task,
            category="orchestration",
            description="Queue a non-blocking agentic background task with instructions and optional tool scope.",
            parameters={
                "task_name": {"type": "string", "required": False},
                "instructions": {"type": "string", "required": True},
                "requested_by": {"type": "string", "required": False},
                "model_tier": {"type": "string", "required": False},
                "tool_access": {"type": "array", "items_type": "string", "required": False},
                "skill_access": {"type": "array", "items_type": "string", "required": False},
                "timeout_sec": {"type": "integer", "required": False},
                "metadata": {"type": "object", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="kill_task_run",
            handler=_kill_task_run,
            category="orchestration",
            description="Kill/cancel a queued or running task run by run_id.",
            parameters={
                "run_id": {"type": "string", "required": True},
                "requested_by": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="resume_task_run",
            handler=_resume_task_run,
            category="orchestration",
            description="Resume a waiting task run with user-provided response text.",
            parameters={
                "run_id": {"type": "string", "required": True},
                "user_response": {"type": "string", "required": True},
                "requested_by": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="list_task_runs",
            handler=_list_task_runs,
            category="orchestration",
            description="List recent task runs from the central queue store.",
            parameters={"limit": {"type": "integer", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="list_waiting_runs",
            handler=_list_waiting_runs,
            category="orchestration",
            description="List task runs currently waiting for user input.",
            parameters={"limit": {"type": "integer", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="query_central_db",
            handler=_query_central_db,
            category="orchestration",
            description="Execute SQL against central DB through serialized queue (read-only by default).",
            parameters={
                "sql": {"type": "string", "required": True},
                "params": {"type": "object", "required": False},
                "read_only": {"type": "boolean", "required": False},
                "timeout_sec": {"type": "number", "required": False},
                "max_rows": {"type": "integer", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="upsert_task_state",
            handler=_upsert_task_state,
            category="orchestration",
            description="Atomically upsert a task state key/value snapshot.",
            parameters={
                "task_id": {"type": "string", "required": True},
                "state_key": {"type": "string", "required": True},
                "value": {"type": "object", "required": False},
                "updated_by": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="get_task_state",
            handler=_get_task_state,
            category="orchestration",
            description="Get a task state value by task_id/state_key.",
            parameters={
                "task_id": {"type": "string", "required": True},
                "state_key": {"type": "string", "required": True},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="mark_task_item_seen",
            handler=_mark_task_item_seen,
            category="orchestration",
            description="Atomically mark an external item as seen for a task/provider/item key.",
            parameters={
                "task_id": {"type": "string", "required": True},
                "provider": {"type": "string", "required": True},
                "item_key": {"type": "string", "required": True},
                "metadata": {"type": "object", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="has_task_item_seen",
            handler=_has_task_item_seen,
            category="orchestration",
            description="Check if a task has already seen an external item key.",
            parameters={
                "task_id": {"type": "string", "required": True},
                "provider": {"type": "string", "required": True},
                "item_key": {"type": "string", "required": True},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="get_task_agent_checkin",
            handler=lambda **kwargs: _get_task_agent_checkin(
                include_runs=bool(kwargs.get("include_runs", False)),
                runs_limit=kwargs.get("runs_limit", 20),
            ),
            category="orchestration",
            description="Return task-agent check-in status with concise textual summary.",
            parameters={
                "include_runs": {"type": "boolean", "required": False},
                "runs_limit": {"type": "integer", "required": False},
            },
        )
    )
    register_user_specific_tools(registry, ToolSpec)
    registry.register(
        ToolSpec(
            name="get_location",
            handler=get_location,
            category="kernel",
            description="Return normalized user location and timezone context.",
        )
    )
    registry.register(
        ToolSpec(
            name="get_current_time",
            handler=get_current_time,
            category="kernel",
            description="Return current UTC/local time for a location timezone.",
            parameters={"location": {"type": "object", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="get_weather",
            handler=get_weather,
            category="kernel",
            description="Return current weather conditions for a location.",
            parameters={"location": {"type": "object", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="get_future_weather",
            handler=get_future_weather,
            category="kernel",
            description="Return hourly/daily weather forecast horizon.",
            parameters={
                "location": {"type": "object", "required": False},
                "horizon": {"type": "string", "required": False},
                "hours": {"type": "integer", "required": False},
                "days": {"type": "integer", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="get_today_weather",
            handler=get_today_weather,
            category="kernel",
            description="Return compact weather summary for today.",
            parameters={"location": {"type": "object", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="get_weather_24hr",
            handler=get_weather_24hr,
            category="kernel",
            description="Return normalized weather outlook for the next 24 hours.",
            parameters={"location": {"type": "object", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="get_week_outlook",
            handler=get_week_outlook,
            category="kernel",
            description="Return normalized 7-day weather outlook.",
            parameters={"location": {"type": "object", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            handler=read_file,
            category="kernel",
            description="Read a text file with path-policy enforcement.",
            parameters={
                "path": {"type": "string", "required": True},
                "encoding": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="list_dir",
            handler=list_dir,
            category="kernel",
            description="List directory entries with path-policy enforcement.",
            parameters={"path": {"type": "string", "required": False}},
        )
    )
    registry.register(
        ToolSpec(
            name="path_exists",
            handler=path_exists,
            category="kernel",
            description="Check whether a path exists with read-policy enforcement.",
            parameters={"path": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="stat_path",
            handler=stat_path,
            category="kernel",
            description="Return stat metadata for a file or directory.",
            parameters={"path": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            handler=write_file,
            category="kernel",
            description="Write a text file with policy checks.",
            parameters={
                "path": {"type": "string", "required": True},
                "content": {"type": "string", "required": True},
                "mode": {"type": "string", "required": False},
                "create_parents": {"type": "boolean", "required": False},
                "dry_run": {"type": "boolean", "required": False},
                "encoding": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="append_file",
            handler=append_file,
            category="kernel",
            description="Append text to a file with policy checks.",
            parameters={
                "path": {"type": "string", "required": True},
                "content": {"type": "string", "required": True},
                "create_parents": {"type": "boolean", "required": False},
                "dry_run": {"type": "boolean", "required": False},
                "encoding": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="web_search",
            handler=web_search,
            category="kernel",
            description="Search the web using Brave Search API.",
            parameters={
                "query": {"type": "string", "required": True},
                "count": {"type": "integer", "required": False},
                "country": {"type": "string", "required": False},
                "search_lang": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_url",
            handler=fetch_url,
            category="kernel",
            description="Fetch URL content and extract readable text.",
            parameters={"url": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="read_json",
            handler=read_json,
            category="data",
            description="Read and parse JSON from a policy-allowed file path.",
            parameters={"path": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="write_json",
            handler=write_json,
            category="data",
            description="Write JSON to a policy-allowed file path.",
            parameters={
                "path": {"type": "string", "required": True},
                "obj": {"type": "object", "required": True},
                "indent": {"type": "integer", "required": False},
                "sort_keys": {"type": "boolean", "required": False},
                "ensure_ascii": {"type": "boolean", "required": False},
                "create_parents": {"type": "boolean", "required": False},
                "dry_run": {"type": "boolean", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="search_text",
            handler=search_text,
            category="data",
            description="Search text across readable files in repo scope.",
            parameters={
                "query": {"type": "string", "required": True},
                "path_or_glob": {"type": "string", "required": False},
                "case_sensitive": {"type": "boolean", "required": False},
                "max_results": {"type": "integer", "required": False},
            },
        )
    )

    return registry


_DEFAULT_TOOL_REGISTRY = _create_default_registry()


def get_tool_registry() -> ToolRegistry:
    return _DEFAULT_TOOL_REGISTRY


def list_tools(*, category: str | None = None) -> list[dict[str, Any]]:
    specs = get_tool_registry().list_specs()
    out: list[dict[str, Any]] = []
    for spec in specs:
        if category and spec.category != category:
            continue
        out.append(
            {
                "name": spec.name,
                "category": spec.category,
                "description": spec.description,
                "parameters": spec.parameters,
            }
        )
    return out


def invoke_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    return get_tool_registry().invoke(name, **kwargs)
