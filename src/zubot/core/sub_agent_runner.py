"""Sub-agent runtime for executing worker tasks with scoped context."""

from __future__ import annotations

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
                llm_result = self._llm(messages=assembled["messages"], model=effective_model)
                if not llm_result.get("ok"):
                    result = WorkerResult(
                        task_id=envelope.task_id,
                        status="failed",
                        summary="Worker LLM call failed.",
                        error=str(llm_result.get("error") or "llm_error"),
                        trace=trace,
                    )
                    return {"ok": False, "result": result.to_dict(), "events": recent_events}

                text = str(llm_result.get("text") or "").strip()
                result = WorkerResult(
                    task_id=envelope.task_id,
                    status="success",
                    summary=text or "(No summary returned.)",
                    artifacts=[{"type": "llm_output", "data": llm_result}],
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
