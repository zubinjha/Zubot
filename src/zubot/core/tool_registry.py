"""Central registry for callable tool contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

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
from src.zubot.core.worker_manager import get_worker_manager

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


def _create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="spawn_worker",
            handler=lambda **kwargs: get_worker_manager().spawn_worker(
                title=str(kwargs.get("title") or ""),
                instructions=str(kwargs.get("instructions") or ""),
                model_tier=str(kwargs.get("model_tier") or "medium"),
                tool_access=list(kwargs.get("tool_access") or []),
                skill_access=list(kwargs.get("skill_access") or []),
                preload_files=list(kwargs.get("preload_files") or []),
                metadata=dict(kwargs.get("metadata") or {}),
            ),
            category="orchestration",
            description="Spawn a worker task with title + instructions (max 3 concurrent).",
            parameters={
                "title": {"type": "string", "required": True},
                "instructions": {"type": "string", "required": True},
                "model_tier": {"type": "string", "required": False},
                "tool_access": {"type": "array", "required": False},
                "skill_access": {"type": "array", "required": False},
                "preload_files": {"type": "array", "required": False},
                "metadata": {"type": "object", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="message_worker",
            handler=lambda **kwargs: get_worker_manager().message_worker(
                worker_id=str(kwargs.get("worker_id") or ""),
                message=str(kwargs.get("message") or ""),
                model_tier=str(kwargs.get("model_tier") or "medium"),
            ),
            category="orchestration",
            description="Queue a follow-up message/task for an existing worker.",
            parameters={
                "worker_id": {"type": "string", "required": True},
                "message": {"type": "string", "required": True},
                "model_tier": {"type": "string", "required": False},
            },
        )
    )
    registry.register(
        ToolSpec(
            name="cancel_worker",
            handler=lambda **kwargs: get_worker_manager().cancel_worker(str(kwargs.get("worker_id") or "")),
            category="orchestration",
            description="Cancel a queued/running worker and clear pending tasks.",
            parameters={"worker_id": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="reset_worker_context",
            handler=lambda **kwargs: get_worker_manager().reset_worker_context(str(kwargs.get("worker_id") or "")),
            category="orchestration",
            description="Reset a non-running worker's scoped context session.",
            parameters={"worker_id": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="get_worker",
            handler=lambda **kwargs: get_worker_manager().get_worker(str(kwargs.get("worker_id") or "")),
            category="orchestration",
            description="Get state for one worker by id.",
            parameters={"worker_id": {"type": "string", "required": True}},
        )
    )
    registry.register(
        ToolSpec(
            name="list_workers",
            handler=lambda **_kwargs: get_worker_manager().list_workers(),
            category="orchestration",
            description="List all workers and runtime queue counts.",
            parameters={},
        )
    )
    registry.register(
        ToolSpec(
            name="list_worker_events",
            handler=lambda **kwargs: get_worker_manager().list_forward_events(
                consume=bool(kwargs.get("consume", True))
            ),
            category="orchestration",
            description="List worker events to forward through main agent.",
            parameters={"consume": {"type": "boolean", "required": False}},
        )
    )
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
