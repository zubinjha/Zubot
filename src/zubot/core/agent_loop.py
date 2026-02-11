"""Main-agent loop scaffold with deterministic stop conditions."""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable

from .agent_types import SessionEvent, TaskEnvelope
from .session_store import append_session_events
from .sub_agent_runner import SubAgentRunner
from .token_estimator import compute_budget, estimate_payload_tokens, get_model_token_limits

Planner = Callable[[dict[str, Any]], dict[str, Any]]
Executor = Callable[[dict[str, Any]], dict[str, Any]]


class AgentLoop:
    """Single-turn loop runner for a user-facing main agent."""

    def __init__(
        self,
        *,
        planner: Planner,
        action_executor: Executor,
        sub_agent_runner: SubAgentRunner | None = None,
    ) -> None:
        self._planner = planner
        self._execute = action_executor
        self._sub_agent_runner = sub_agent_runner

    def ingest_user_input(self, session_id: str, text: str) -> SessionEvent:
        return SessionEvent(
            session_id=session_id,
            event_type="user_message",
            payload={"text": text},
            source="user",
        )

    def assemble_context(
        self,
        *,
        base_context: dict[str, Any] | None,
        events: list[SessionEvent],
        recent_n: int = 12,
    ) -> dict[str, Any]:
        recent_events = [event.to_dict() for event in events[-recent_n:]]
        return {
            "base_context": base_context or {},
            "recent_events": recent_events,
            "event_count": len(events),
        }

    def plan_next_action(self, assembled_context: dict[str, Any]) -> dict[str, Any]:
        return self._planner(assembled_context)

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return self._execute(action)

    @staticmethod
    def _build_task_from_action(action: dict[str, Any]) -> TaskEnvelope:
        task_payload = action.get("task")
        instructions = ""
        if isinstance(task_payload, str):
            instructions = task_payload
        elif isinstance(task_payload, dict):
            txt = task_payload.get("instructions")
            if isinstance(txt, str):
                instructions = txt
        if not instructions:
            txt = action.get("instructions")
            if isinstance(txt, str):
                instructions = txt
        if not instructions:
            raise ValueError("spawn_sub_agent action requires task instructions.")

        model_tier = action.get("model_tier", "medium")
        if model_tier not in {"low", "medium", "high"}:
            model_tier = "medium"

        return TaskEnvelope.create(
            instructions=instructions,
            model_tier=model_tier,  # type: ignore[arg-type]
            requested_by="main_agent",
            tool_access=list(action.get("tool_access") or []),
            skill_access=list(action.get("skill_access") or []),
            metadata={"origin_action": action},
        )

    def observe_result(self, session_id: str, result: dict[str, Any]) -> SessionEvent:
        event_type = "tool_result"
        if result.get("type") == "worker_result":
            event_type = "worker_complete"
        return SessionEvent(
            session_id=session_id,
            event_type=event_type,
            payload=result,
            source="main_agent",
        )

    def respond_or_continue(self, result: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        if isinstance(result.get("final_response"), str):
            return True, result["final_response"], "final_response"
        if result.get("needs_user_input"):
            return True, None, "needs_user_input"
        return False, None, None

    def run_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        base_context: dict[str, Any] | None = None,
        model: str | None = None,
        max_context_tokens: int | None = None,
        reserved_output_tokens: int | None = None,
        max_steps: int = 8,
        max_tool_calls: int = 6,
        timeout_sec: float = 20.0,
        persist_events: bool = False,
        events_base_dir: str = "memory/sessions",
    ) -> dict[str, Any]:
        events: list[SessionEvent] = [self.ingest_user_input(session_id, user_text)]
        tool_calls = 0
        started = monotonic()
        effective_max_context = max_context_tokens
        effective_reserved_output = reserved_output_tokens
        if model is not None and (effective_max_context is None or effective_reserved_output is None):
            limits = get_model_token_limits(model)
            if effective_max_context is None:
                effective_max_context = limits["max_context_tokens"]
            if effective_reserved_output is None:
                effective_reserved_output = limits["max_output_tokens"]

        for step in range(1, max_steps + 1):
            if monotonic() - started > timeout_sec:
                return self._stop_payload(
                    session_id,
                    events,
                    "timeout_budget_exhausted",
                    step,
                    tool_calls,
                    persist_events=persist_events,
                    events_base_dir=events_base_dir,
                )

            context = self.assemble_context(base_context=base_context, events=events)
            if effective_max_context is not None and effective_reserved_output is not None:
                token_estimate = estimate_payload_tokens(context)
                budget = compute_budget(
                    input_tokens=token_estimate,
                    max_context_tokens=effective_max_context,
                    reserved_output_tokens=effective_reserved_output,
                )
                events.append(
                    SessionEvent(
                        session_id=session_id,
                        event_type="system",
                        payload={
                            "step": step,
                            "type": "token_budget",
                            "model": model,
                            "budget": budget,
                        },
                        source="main_agent",
                    )
                )
                if not budget["within_budget"]:
                    return self._stop_payload(
                        session_id,
                        events,
                        "context_budget_exhausted",
                        step,
                        tool_calls,
                        persist_events=persist_events,
                        events_base_dir=events_base_dir,
                    )

            action = self.plan_next_action(context)
            events.append(
                SessionEvent(
                    session_id=session_id,
                    event_type="system",
                    payload={"step": step, "action": action},
                    source="main_agent",
                )
            )

            action_kind = action.get("kind")
            if action_kind == "tool":
                if tool_calls >= max_tool_calls:
                    return self._stop_payload(
                        session_id,
                        events,
                        "tool_call_budget_exhausted",
                        step,
                        tool_calls,
                        persist_events=persist_events,
                        events_base_dir=events_base_dir,
                    )
                tool_calls += 1
                events.append(
                    SessionEvent(
                        session_id=session_id,
                        event_type="tool_call",
                        payload=action,
                        source="main_agent",
                    )
                )
            elif action_kind == "spawn_sub_agent":
                events.append(
                    SessionEvent(
                        session_id=session_id,
                        event_type="worker_spawn",
                        payload=action,
                        source="main_agent",
                    )
                )

            if action_kind == "spawn_sub_agent" and self._sub_agent_runner is not None:
                task = self._build_task_from_action(action)
                worker_out = self._sub_agent_runner.run_task(task)
                worker_result = worker_out.get("result", {})
                if not isinstance(worker_result, dict):
                    worker_result = {}
                status = worker_result.get("status")
                result = {
                    "type": "worker_result",
                    "task_id": worker_result.get("task_id", task.task_id),
                    "status": status,
                    "summary": worker_result.get("summary"),
                    "artifacts": worker_result.get("artifacts", []),
                    "error": worker_result.get("error"),
                    "trace": worker_result.get("trace", []),
                    "needs_user_input": status == "needs_user_input",
                    "final_response": worker_result.get("summary") if status == "success" else None,
                    "worker_ok": bool(worker_out.get("ok")),
                }
            else:
                result = self.execute_action(action)
            events.append(self.observe_result(session_id, result))
            should_stop, response, stop_reason = self.respond_or_continue(result)
            if should_stop:
                if response:
                    events.append(
                        SessionEvent(
                            session_id=session_id,
                            event_type="assistant_message",
                            payload={"text": response},
                            source="main_agent",
                        )
                    )
                payload = {
                    "ok": True,
                    "stop_reason": stop_reason,
                    "response": response,
                    "steps": step,
                    "tool_calls": tool_calls,
                    "events": [event.to_dict() for event in events],
                }
                if persist_events:
                    append_session_events(session_id, events, base_dir=events_base_dir)
                return payload

        return self._stop_payload(
            session_id,
            events,
            "step_budget_exhausted",
            max_steps,
            tool_calls,
            persist_events=persist_events,
            events_base_dir=events_base_dir,
        )

    def _stop_payload(
        self,
        session_id: str,
        events: list[SessionEvent],
        reason: str,
        step: int,
        tool_calls: int,
        *,
        persist_events: bool,
        events_base_dir: str,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "stop_reason": reason,
            "response": None,
            "steps": step,
            "tool_calls": tool_calls,
            "events": [event.to_dict() for event in events],
        }
        if persist_events:
            append_session_events(session_id, events, base_dir=events_base_dir)
        return payload
