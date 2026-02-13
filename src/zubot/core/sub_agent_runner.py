"""Sub-agent runtime for executing worker tasks with scoped context."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any, Callable

from .agent_types import TaskEnvelope, WorkerResult
from .context_assembler import assemble_messages
from .context_state import ContextState
from .llm_client import call_llm
from .token_estimator import get_model_token_limits

Planner = Callable[[dict[str, Any]], dict[str, Any]]
Executor = Callable[[dict[str, Any]], dict[str, Any]]
LLMCaller = Callable[..., dict[str, Any]]


class SubAgentRunner:
    """Run one worker task with bounded steps and scoped context."""

    def __init__(
        self,
        *,
        planner: Planner | None = None,
        action_executor: Executor | None = None,
        llm_caller: LLMCaller = call_llm,
    ) -> None:
        self._planner = planner
        self._execute = action_executor
        self._llm = llm_caller

    @staticmethod
    def _as_task(task: TaskEnvelope | dict[str, Any]) -> TaskEnvelope:
        if isinstance(task, TaskEnvelope):
            return task
        return TaskEnvelope(
            task_id=str(task.get("task_id", "")).strip(),
            requested_by=str(task.get("requested_by", "main_agent")).strip(),
            instructions=str(task.get("instructions", "")).strip(),
            model_tier=str(task.get("model_tier", "medium")),  # type: ignore[arg-type]
            tool_access=list(task.get("tool_access") or []),
            skill_access=list(task.get("skill_access") or []),
            deadline_iso=task.get("deadline_iso"),
            metadata=dict(task.get("metadata") or {}),
        )

    @staticmethod
    def _default_model_for_tier(tier: str) -> str:
        return {"low": "low", "medium": "medium", "high": "high"}.get(tier, "medium")

    def run_task(
        self,
        task: TaskEnvelope | dict[str, Any],
        *,
        base_context: dict[str, str] | None = None,
        supplemental_context: dict[str, str] | None = None,
        facts: dict[str, str] | None = None,
        session_summary: str | None = None,
        model: str | None = None,
        max_steps: int = 4,
        max_tool_calls: int = 3,
        timeout_sec: float = 20.0,
        allow_orchestration_tools: bool = False,
    ) -> dict[str, Any]:
        envelope = self._as_task(task)
        started = monotonic()
        tool_calls = 0
        trace: list[str] = []

        context_state = ContextState()
        for path, text in sorted((base_context or {}).items()):
            if isinstance(text, str) and text.strip():
                context_state.upsert_item(
                    f"base:{path}",
                    text,
                    priority="base",
                    metadata={"label": f"BaseContext:{path}"},
                )
        for path, text in sorted((supplemental_context or {}).items()):
            if isinstance(text, str) and text.strip():
                context_state.upsert_item(
                    f"supplemental:{path}",
                    text,
                    priority="supplemental",
                    metadata={"label": f"SupplementalContext:{path}"},
                )
        for key, text in sorted((facts or {}).items()):
            if isinstance(text, str) and text.strip():
                context_state.upsert_item(
                    f"fact:{key}",
                    text,
                    priority="fact",
                    metadata={"label": f"Fact:{key}"},
                )

        recent_events: list[dict[str, Any]] = [
            {
                "event_type": "user_message",
                "payload": {"text": envelope.instructions},
            }
        ]

        effective_model = model or self._default_model_for_tier(envelope.model_tier)
        try:
            limits = get_model_token_limits(effective_model)
        except Exception:
            limits = {"max_context_tokens": 400000, "max_output_tokens": 128000}

        def _bundle_from_state() -> dict[str, Any]:
            bundle = {"base": {}, "supplemental": {}, "facts": {}}
            for item in context_state.all_items():
                if item.source_id.startswith("base:"):
                    key = item.source_id[len("base:") :]
                    bundle["base"][key] = item.content
                elif item.source_id.startswith("supplemental:"):
                    key = item.source_id[len("supplemental:") :]
                    bundle["supplemental"][key] = item.content
                elif item.source_id.startswith("fact:"):
                    key = item.source_id[len("fact:") :]
                    bundle["facts"][key] = item.content
            return bundle

        for step in range(1, max_steps + 1):
            if monotonic() - started > timeout_sec:
                result = WorkerResult(
                    task_id=envelope.task_id,
                    status="failed",
                    summary="Worker timed out before completing task.",
                    error="timeout_budget_exhausted",
                    trace=trace,
                )
                return {"ok": False, "result": result.to_dict(), "events": recent_events}

            assembled = assemble_messages(
                context_bundle=_bundle_from_state(),
                recent_events=recent_events,
                session_summary=session_summary,
                max_context_tokens=limits["max_context_tokens"],
                reserved_output_tokens=limits["max_output_tokens"],
            )
            session_summary = assembled.get("updated_session_summary")
            updated_facts = assembled.get("updated_facts")
            if isinstance(updated_facts, dict):
                for key, val in updated_facts.items():
                    if isinstance(val, str) and val.strip():
                        context_state.upsert_item(
                            f"fact:{key}",
                            val,
                            priority="fact",
                            metadata={"label": f"Fact:{key}"},
                        )

            action: dict[str, Any] | None = None
            if self._planner is not None:
                action = self._planner(
                    {
                        "task": envelope.to_dict(),
                        "step": step,
                        "messages": assembled["messages"],
                        "context_debug": {
                            "kept_context_source_ids": assembled.get("kept_context_source_ids", []),
                            "kept_recent_message_count": assembled.get("kept_recent_message_count", 0),
                            "dropped_recent_event_count": assembled.get("dropped_recent_event_count", 0),
                        },
                    }
                )
            else:
                llm_result, final_text, executed_tools, loop_error = self._run_llm_with_tools(
                    messages=assembled["messages"],
                    model=effective_model,
                    max_steps=max_steps,
                    max_tool_calls=max_tool_calls,
                    allowed_tools=envelope.tool_access,
                    allow_orchestration_tools=allow_orchestration_tools,
                )
                if loop_error is not None:
                    result = WorkerResult(
                        task_id=envelope.task_id,
                        status="failed",
                        summary="Worker LLM call failed.",
                        error=loop_error,
                        trace=trace,
                    )
                    return {"ok": False, "result": result.to_dict(), "events": recent_events}

                result = WorkerResult(
                    task_id=envelope.task_id,
                    status="success",
                    summary=final_text or "(No summary returned.)",
                    artifacts=[
                        {"type": "llm_output", "data": llm_result},
                        {"type": "tool_execution", "data": executed_tools},
                    ],
                    trace=trace,
                )
                return {
                    "ok": True,
                    "result": result.to_dict(),
                    "events": recent_events,
                    "session_summary": session_summary,
                    "facts": {
                        item.source_id[len("fact:") :]: item.content
                        for item in context_state.all_items()
                        if item.source_id.startswith("fact:")
                    },
                }

            if not isinstance(action, dict):
                result = WorkerResult(
                    task_id=envelope.task_id,
                    status="failed",
                    summary="Planner returned invalid action.",
                    error="invalid_action",
                    trace=trace,
                )
                return {"ok": False, "result": result.to_dict(), "events": recent_events}

            trace.append(f"step={step} action={action.get('kind', 'unknown')}")
            kind = action.get("kind")

            if kind == "respond":
                text = str(action.get("text") or "").strip()
                status = "needs_user_input" if action.get("needs_user_input") else "success"
                result = WorkerResult(
                    task_id=envelope.task_id,
                    status=status,
                    summary=text or "(No response text provided.)",
                    artifacts=[{"type": "worker_response", "data": action}],
                    trace=trace,
                )
                return {
                    "ok": True,
                    "result": result.to_dict(),
                    "events": recent_events,
                    "session_summary": session_summary,
                }

            if kind == "tool":
                if tool_calls >= max_tool_calls:
                    result = WorkerResult(
                        task_id=envelope.task_id,
                        status="failed",
                        summary="Worker tool call budget exhausted.",
                        error="tool_call_budget_exhausted",
                        trace=trace,
                    )
                    return {"ok": False, "result": result.to_dict(), "events": recent_events}
                tool_calls += 1
                if self._execute is None:
                    result = WorkerResult(
                        task_id=envelope.task_id,
                        status="failed",
                        summary="No tool executor configured for worker.",
                        error="missing_action_executor",
                        trace=trace,
                    )
                    return {"ok": False, "result": result.to_dict(), "events": recent_events}
                tool_result = self._execute(action)
                recent_events.append({"event_type": "tool_result", "payload": tool_result})
                continue

            if kind == "llm":
                llm_result = self._llm(messages=assembled["messages"], model=effective_model)
                recent_events.append({"event_type": "assistant_message", "payload": {"text": llm_result.get("text")}})
                if llm_result.get("ok"):
                    result = WorkerResult(
                        task_id=envelope.task_id,
                        status="success",
                        summary=str(llm_result.get("text") or "").strip() or "(No summary returned.)",
                        artifacts=[{"type": "llm_output", "data": llm_result}],
                        trace=trace,
                    )
                    return {"ok": True, "result": result.to_dict(), "events": recent_events}
                result = WorkerResult(
                    task_id=envelope.task_id,
                    status="failed",
                    summary="Worker LLM step failed.",
                    error=str(llm_result.get("error") or "llm_error"),
                    trace=trace,
                )
                return {"ok": False, "result": result.to_dict(), "events": recent_events}

            if kind == "continue":
                recent_events.append({"event_type": "system", "payload": {"note": "continue"}})
                continue

            result = WorkerResult(
                task_id=envelope.task_id,
                status="failed",
                summary=f"Unsupported action kind: {kind}",
                error="unsupported_action_kind",
                trace=trace,
            )
            return {"ok": False, "result": result.to_dict(), "events": recent_events}

        result = WorkerResult(
            task_id=envelope.task_id,
            status="failed",
            summary="Worker step budget exhausted before completion.",
            error="step_budget_exhausted",
            trace=trace,
        )
        return {"ok": False, "result": result.to_dict(), "events": recent_events}

    @staticmethod
    def _tool_schemas_for_worker(
        allowed_tools: list[str],
        *,
        allow_orchestration_tools: bool = False,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        from .tool_registry import list_tools

        def _param_schema(meta: dict[str, Any] | None) -> dict[str, Any]:
            kind = "string"
            if isinstance(meta, dict) and isinstance(meta.get("type"), str):
                kind = meta["type"]
            if kind == "array":
                items_type = "string"
                if isinstance(meta, dict) and isinstance(meta.get("items_type"), str):
                    items_type = meta["items_type"]
                return {"type": "array", "items": {"type": items_type}}
            if kind == "object":
                return {"type": "object", "additionalProperties": True}
            if kind in {"string", "number", "integer", "boolean", "null"}:
                return {"type": kind}
            return {"type": "string"}

        allowed_set = {name for name in allowed_tools if isinstance(name, str) and name.strip()}
        schemas: list[dict[str, Any]] = []
        registered_names: set[str] = set()
        for tool in list_tools():
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            category = tool.get("category")
            if not isinstance(name, str) or not name:
                continue
            if category == "orchestration" and not allow_orchestration_tools:
                continue
            if allowed_set and name not in allowed_set:
                continue
            registered_names.add(name)

            properties: dict[str, Any] = {}
            required: list[str] = []
            params = tool.get("parameters")
            if isinstance(params, dict):
                for param_name, meta in params.items():
                    if not isinstance(param_name, str) or not param_name:
                        continue
                    meta_dict = meta if isinstance(meta, dict) else None
                    properties[param_name] = _param_schema(meta_dict)
                    if isinstance(meta, dict) and bool(meta.get("required")):
                        required.append(param_name)

            parameters_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "additionalProperties": False,
            }
            if required:
                parameters_schema["required"] = required

            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(tool.get("description") or ""),
                        "parameters": parameters_schema,
                    },
                }
            )
        return schemas, registered_names

    @staticmethod
    def _parse_tool_call(tool_call: dict[str, Any], idx: int) -> tuple[str | None, dict[str, Any], str]:
        call_id = str(tool_call.get("id") or f"tool_call_{idx}")
        fn = tool_call.get("function")
        if not isinstance(fn, dict):
            return None, {}, call_id

        name = fn.get("name")
        if not isinstance(name, str) or not name:
            return None, {}, call_id

        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            return name, raw_args, call_id
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                return name, {"_raw_arguments": raw_args}, call_id
            if isinstance(parsed, dict):
                return name, parsed, call_id
        return name, {}, call_id

    def _run_llm_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_steps: int,
        max_tool_calls: int,
        allowed_tools: list[str],
        allow_orchestration_tools: bool = False,
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]], str | None]:
        from .tool_registry import invoke_tool

        tool_schemas, registered_names = self._tool_schemas_for_worker(
            allowed_tools,
            allow_orchestration_tools=allow_orchestration_tools,
        )
        working_messages = list(messages)
        executed_tools: list[dict[str, Any]] = []
        tool_calls_used = 0
        last_result: dict[str, Any] | None = None

        for _ in range(max_steps):
            llm_result = self._llm(messages=working_messages, model=model, tools=tool_schemas)
            last_result = llm_result
            if not llm_result.get("ok"):
                return llm_result, "", executed_tools, str(llm_result.get("error") or "llm_error")

            tool_calls = llm_result.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                text = llm_result.get("text")
                if isinstance(text, str):
                    return llm_result, text.strip(), executed_tools, None
                return llm_result, "", executed_tools, None

            working_messages.append(
                {
                    "role": "assistant",
                    "content": llm_result.get("text") or "",
                    "tool_calls": tool_calls,
                }
            )

            for idx, call in enumerate(tool_calls):
                if tool_calls_used >= max_tool_calls:
                    return llm_result, "", executed_tools, "tool_call_budget_exhausted"
                tool_calls_used += 1

                if not isinstance(call, dict):
                    continue
                tool_name, tool_args, tool_call_id = self._parse_tool_call(call, idx=idx)
                if tool_name is None:
                    tool_payload = {
                        "ok": False,
                        "error": "Malformed tool call: missing function name.",
                        "source": "worker_tool_loop",
                    }
                    tool_name = "unknown_tool"
                elif "_raw_arguments" in tool_args:
                    tool_payload = {
                        "ok": False,
                        "error": f"Invalid JSON arguments for `{tool_name}`.",
                        "source": "worker_tool_loop",
                    }
                elif tool_name not in registered_names:
                    tool_payload = {
                        "ok": False,
                        "error": f"Tool `{tool_name}` is not available to this worker.",
                        "source": "worker_tool_loop",
                    }
                else:
                    tool_payload = invoke_tool(tool_name, **tool_args)

                executed_tools.append(
                    {
                        "name": tool_name,
                        "args": tool_args,
                        "result_ok": bool(tool_payload.get("ok", True)),
                        "error": tool_payload.get("error"),
                    }
                )
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": json.dumps(tool_payload, ensure_ascii=False),
                    }
                )

        if isinstance(last_result, dict):
            text = last_result.get("text")
            if isinstance(text, str):
                return last_result, text.strip(), executed_tools, None
            return last_result, "", executed_tools, "worker_tool_loop_step_budget_exhausted"
        return {"ok": False}, "", executed_tools, "worker_tool_loop_step_budget_exhausted"
