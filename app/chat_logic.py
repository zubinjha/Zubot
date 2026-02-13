"""Minimal chat routing logic for local UI testing."""

from __future__ import annotations

import json
from time import monotonic
from dataclasses import dataclass, field
from typing import Any

from src.zubot.core.agent_types import SessionEvent
from src.zubot.core.context_assembler import assemble_messages
from src.zubot.core.context_loader import load_context_bundle
from src.zubot.core.daily_summary_pipeline import summarize_day_from_raw
from src.zubot.core.daily_memory import (
    append_daily_memory_entry,
    load_recent_daily_memory,
    local_day_str,
)
from src.zubot.core.llm_client import call_llm
from src.zubot.core.token_estimator import estimate_text_tokens
from src.zubot.core.memory_index import (
    enqueue_day_summary_job,
    ensure_memory_index_schema,
    get_days_pending_summary,
    increment_day_message_count,
)
from src.zubot.core.memory_summary_worker import get_memory_summary_worker
from src.zubot.core.session_store import append_session_events
from src.zubot.core.tool_registry import invoke_tool, list_tools
from src.zubot.core.config_loader import load_config
from src.zubot.core.central_service import get_central_service
from src.zubot.core.worker_manager import get_worker_manager

MAX_RECENT_EVENTS = 60
MAX_TOOL_LOOP_STEPS = 4
SUMMARY_MAX_INPUT_TOKENS = 4000
SUMMARY_MAX_RECURSION_DEPTH = 6
DEFAULT_SESSION_TTL_MINUTES = 12 * 60
DEFAULT_MAX_ACTIVE_SESSIONS = 24


@dataclass
class SessionRuntime:
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    session_summary: str | None = None
    facts: dict[str, str] = field(default_factory=dict)
    preloaded_daily_context: dict[str, str] = field(default_factory=dict)
    last_touched_mono: float = field(default_factory=monotonic)


_SESSIONS: dict[str, SessionRuntime] = {}


def _autoload_summary_days() -> int:
    try:
        cfg = load_config()
    except Exception:
        return 2
    memory_cfg = cfg.get("memory")
    if isinstance(memory_cfg, dict):
        value = memory_cfg.get("autoload_summary_days")
        if isinstance(value, int) and value > 0:
            return value
    return 2


def _session_retention_policy() -> tuple[int, int]:
    ttl_minutes = DEFAULT_SESSION_TTL_MINUTES
    max_sessions = DEFAULT_MAX_ACTIVE_SESSIONS
    try:
        cfg = load_config()
    except Exception:
        return ttl_minutes, max_sessions
    memory_cfg = cfg.get("memory")
    if not isinstance(memory_cfg, dict):
        return ttl_minutes, max_sessions
    ttl_val = memory_cfg.get("session_ttl_minutes")
    max_val = memory_cfg.get("max_active_sessions")
    if isinstance(ttl_val, int) and ttl_val > 0:
        ttl_minutes = ttl_val
    if isinstance(max_val, int) and max_val > 0:
        max_sessions = max_val
    return ttl_minutes, max_sessions


def _realtime_summary_turn_threshold() -> int:
    try:
        cfg = load_config()
    except Exception:
        return 1
    memory_cfg = cfg.get("memory")
    if not isinstance(memory_cfg, dict):
        return 1
    value = memory_cfg.get("realtime_summary_turn_threshold")
    if isinstance(value, int) and value > 0:
        return value
    return 1


def _prune_sessions() -> None:
    ttl_minutes, max_sessions = _session_retention_policy()
    now_mono = monotonic()
    ttl_sec = ttl_minutes * 60

    stale_ids = [
        sid
        for sid, runtime in _SESSIONS.items()
        if now_mono - float(runtime.last_touched_mono) > ttl_sec
    ]
    for sid in stale_ids:
        _SESSIONS.pop(sid, None)

    if len(_SESSIONS) <= max_sessions:
        return
    ordered = sorted(_SESSIONS.items(), key=lambda item: float(item[1].last_touched_mono))
    overflow = len(_SESSIONS) - max_sessions
    for sid, _runtime in ordered[:overflow]:
        _SESSIONS.pop(sid, None)


def _get_session(session_id: str) -> SessionRuntime:
    ensure_memory_index_schema()
    get_memory_summary_worker().start()
    _prune_sessions()
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        runtime = SessionRuntime(preloaded_daily_context=load_recent_daily_memory(days=_autoload_summary_days()))
        _SESSIONS[session_id] = runtime
        _prune_sessions()
        runtime = _SESSIONS.get(session_id) or runtime
    runtime.last_touched_mono = monotonic()
    return runtime


def _refresh_daily_context(runtime: SessionRuntime) -> None:
    runtime.preloaded_daily_context = load_recent_daily_memory(days=_autoload_summary_days())


def initialize_session_context(session_id: str) -> dict[str, Any]:
    runtime = _get_session(session_id)
    _refresh_daily_context(runtime)
    today = local_day_str()
    finalized_days: list[str] = []
    for pending in get_days_pending_summary(before_day=today):
        day = str(pending.get("day") or "").strip()
        if not day:
            continue
        out = summarize_day_from_raw(
            day=day,
            reason="session_initialize_finalize",
            session_id=session_id,
            finalize=True,
        )
        if out.get("ok"):
            finalized_days.append(day)

    return {
        "ok": True,
        "session_id": session_id,
        "initialized": True,
        "welcome": "Session initialized. Context and recent daily memory are loaded.",
        "preload": {
            "daily_files_loaded": sorted(runtime.preloaded_daily_context.keys()),
            "daily_files_count": len(runtime.preloaded_daily_context),
            "recent_event_count": len(runtime.recent_events),
            "has_summary": bool(runtime.session_summary),
            "fact_count": len(runtime.facts),
            "auto_finalized_days": finalized_days,
        },
    }


def reset_session_context(session_id: str) -> dict[str, Any]:
    _flush_daily_summary(session_id=session_id, reason="session_reset")
    _SESSIONS.pop(session_id, None)
    return {
        "ok": True,
        "session_id": session_id,
        "reset": True,
        "note": "Session context reset. Daily memory remains persisted.",
    }


def _append_session_event(runtime: SessionRuntime, event: dict[str, Any]) -> None:
    runtime.recent_events.append(event)
    if len(runtime.recent_events) > MAX_RECENT_EVENTS:
        runtime.recent_events = runtime.recent_events[-MAX_RECENT_EVENTS:]


def _clean_text(value: str, *, max_chars: int = 2000) -> str:
    return " ".join(value.strip().split())[:max_chars]


def _is_high_signal_worker_event(event_type: str, payload: Any) -> bool:
    e = event_type.strip().lower()
    if any(token in e for token in ("complete", "done", "fail", "error", "blocked", "cancel")):
        return True
    if isinstance(payload, dict):
        status = str(payload.get("status", "")).strip().lower()
        if status in {"done", "failed", "blocked", "cancelled"}:
            return True
        if payload.get("error"):
            return True
    return False


def _is_high_signal_task_agent_event(event_type: str, payload: Any) -> bool:
    e = event_type.strip().lower()
    if e in {"run_finished", "run_failed", "run_blocked"}:
        return True
    if isinstance(payload, dict):
        status = str(payload.get("status", "")).strip().lower()
        if status in {"done", "failed", "blocked"}:
            return True
        nested = payload.get("event_type")
        if isinstance(nested, str) and nested.strip().lower() in {"run_finished", "run_failed", "run_blocked"}:
            return True
    return False


def _log_daily_transcript_event(
    *,
    session_id: str,
    day: str,
    speaker: str,
    text: str,
    route: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    clean = _clean_text(text)
    if not clean:
        return
    _ = route
    _ = metadata

    append_daily_memory_entry(
        day_str=day,
        session_id=session_id,
        kind=speaker,
        text=clean,
        layer="raw",
    )


def _log_daily_turn(
    session_id: str,
    *,
    route: str,
    user_text: str,
    reply: str,
    tool_execution: list[dict[str, Any]] | None = None,
    worker_events: list[dict[str, Any]] | None = None,
    task_agent_events: list[dict[str, Any]] | None = None,
    system_events: list[str] | None = None,
) -> None:
    _ = _get_session(session_id)
    day = local_day_str()
    trimmed_user = _clean_text(user_text)
    trimmed_reply = _clean_text(reply)

    _log_daily_transcript_event(
        session_id=session_id,
        day=day,
        speaker="user",
        text=trimmed_user,
        route=route,
    )

    tools_used = [
        str(item.get("name"))
        for item in (tool_execution or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    _log_daily_transcript_event(
        session_id=session_id,
        day=day,
        speaker="main_agent",
        text=trimmed_reply,
        route=route,
        metadata={"tools": ",".join(tools_used) if tools_used else None},
    )

    for event in worker_events or []:
        if not isinstance(event, dict):
            continue
        worker_id = str(event.get("worker_id") or "worker?")
        event_type = str(event.get("type") or "worker_event")
        payload = event.get("payload")
        if not _is_high_signal_worker_event(event_type, payload):
            continue
        payload_text = json.dumps(payload, ensure_ascii=False) if payload is not None else "{}"
        worker_text = f"{event_type} from {worker_id}: {payload_text}"
        _log_daily_transcript_event(
            session_id=session_id,
            day=day,
            speaker="worker_event",
            text=worker_text,
            route=route,
            metadata={"worker_id": worker_id, "event_type": event_type},
        )

    for event in task_agent_events or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "task_agent_event")
        payload = event.get("payload")
        if not _is_high_signal_task_agent_event(event_type, payload):
            continue
        payload_text = json.dumps(payload, ensure_ascii=False) if payload is not None else "{}"
        task_text = f"{event_type}: {payload_text}"
        _log_daily_transcript_event(
            session_id=session_id,
            day=day,
            speaker="task_agent_event",
            text=task_text,
            route=route,
            metadata={"event_type": event_type},
        )

    _ = system_events  # system chatter is intentionally excluded from long-term daily memory

    status = increment_day_message_count(day=day, amount=1)
    pending = int(status.get("messages_since_last_summary") or 0)
    if pending >= _realtime_summary_turn_threshold():
        enqueue_day_summary_job(day=day, reason=f"chat_turn:{route}")
        get_memory_summary_worker().kick()


def _flush_daily_summary(*, session_id: str, reason: str) -> dict[str, Any]:
    day = local_day_str()
    out = summarize_day_from_raw(day=day, reason=reason, session_id=session_id, finalize=False)
    if out.get("ok"):
        return {"ok": True, "writes": [{"ok": True, "path": out.get("status")}], "summary": out}
    return {"ok": False, "writes": [{"ok": False, "error": out.get("error")}], "summary": out}


def _entry_to_line(entry: dict[str, Any]) -> str:
    speaker = str(entry.get("speaker", "unknown"))
    text = str(entry.get("text", ""))
    return f"- [{speaker}] {text}"


def _entries_token_estimate(entries: list[dict[str, Any]]) -> int:
    text = "\n".join(_entry_to_line(entry) for entry in entries)
    return estimate_text_tokens(text)


def _split_entries_recursive(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mid = max(1, len(entries) // 2)
    return entries[:mid], entries[mid:]


def _summarize_entries_batch(entries: list[dict[str, Any]]) -> str:
    def _narrative_fallback(lines: list[dict[str, Any]]) -> str:
        if not lines:
            return (
                "- What user wanted: no clear request captured.\n"
                "- Key decisions: none recorded.\n"
                "- What was executed: no concrete actions recorded.\n"
                "- Final state: no stable outcome captured."
            )

        user_msgs = [str(item.get("text", "")).strip() for item in lines if str(item.get("speaker", "")).strip().lower() == "user"]
        agent_msgs = [str(item.get("text", "")).strip() for item in lines if str(item.get("speaker", "")).strip().lower() == "main_agent"]
        task_msgs = [
            str(item.get("text", "")).strip()
            for item in lines
            if str(item.get("speaker", "")).strip().lower() in {"task_agent_event", "worker_event"}
        ]
        key_user = "; ".join([msg for msg in user_msgs[:2] if msg]) or "no clear request captured."
        key_agent = "; ".join([msg for msg in agent_msgs[:2] if msg]) or "no explicit recommendation recorded."
        key_tasks = "; ".join([msg for msg in task_msgs[-2:] if msg]) or "no concrete task lifecycle events recorded."
        final_state = next((msg for msg in reversed(agent_msgs) if msg), "no stable outcome captured.")
        return (
            f"- What user wanted: {key_user}\n"
            f"- Key decisions: {key_agent}\n"
            f"- What was executed: {key_tasks}\n"
            f"- Final state: {final_state}"
        )

    def _is_low_signal(entry: dict[str, Any]) -> bool:
        speaker = str(entry.get("speaker", "")).strip().lower()
        text = " ".join(str(entry.get("text", "")).strip().lower().split())

        if speaker not in {"user", "main_agent", "task_agent_event", "worker_event"}:
            return True
        if len(text) < 8:
            return True
        if speaker in {"worker_event", "task_agent_event"} and len(text) < 20:
            return True

        low_signal_markers = {
            "thanks",
            "thank you",
            "ok",
            "okay",
            "cool",
            "nice",
            "yes",
            "no",
            "sounds good",
            "got it",
        }
        return text in low_signal_markers

    signal_entries = [entry for entry in entries if not _is_low_signal(entry)]
    entries_for_summary = signal_entries or entries

    raw_lines = "\n".join(
        _entry_to_line(entry) for entry in entries_for_summary
    )[:12000]
    prompt = (
        "Summarize this raw daily transcript into concise narrative memory bullets.\n"
        "Transcript format:\n"
        "- [user] text from human\n"
        "- [main_agent] assistant reply\n"
        "- [worker_event] worker-to-main event payload\n"
        "- [task_agent_event] central scheduler/task-agent lifecycle event\n"
        "- Other entries may exist; ignore low-signal/internal noise.\n\n"
        "Requirements:\n"
        "- Use this exact 4-bullet structure:\n"
        "  - What user wanted\n"
        "  - Key decisions\n"
        "  - What was executed\n"
        "  - Final state\n"
        "- Focus on meaningful work only: what was done conceptually, how it was done, and the outcome.\n"
        "- Do not include idle chat, acknowledgments, or repetitive low-signal exchanges.\n"
        "- Include decisions, design choices, and concrete progress state.\n"
        "- Mention worker activity only when it materially changed progress.\n"
        "- Do not include routes, internal metadata, tool call traces, or telemetry counts.\n"
        "- Include next step only if explicit.\n"
        "- Keep it concise and factual.\n\n"
        f"Transcript:\n{raw_lines}"
    )
    llm = call_llm(
        model="low",
        max_output_tokens=220,
        messages=[
            {"role": "system", "content": "You write compact, practical memory summaries."},
            {"role": "user", "content": prompt},
        ],
    )
    if llm.get("ok") and isinstance(llm.get("text"), str) and llm["text"].strip():
        model_summary = " ".join(llm["text"].strip().split())
        return model_summary

    return _narrative_fallback(entries_for_summary)


def _summarize_turns_recursive(entries: list[dict[str, Any]], *, depth: int = 0) -> str:
    if not entries:
        return "- No daily transcript entries to summarize."
    if depth >= SUMMARY_MAX_RECURSION_DEPTH:
        return _summarize_entries_batch(entries)

    estimated = _entries_token_estimate(entries)
    if estimated <= SUMMARY_MAX_INPUT_TOKENS or len(entries) <= 4:
        return _summarize_entries_batch(entries)

    left, right = _split_entries_recursive(entries)
    left_summary = _summarize_turns_recursive(left, depth=depth + 1)
    right_summary = _summarize_turns_recursive(right, depth=depth + 1)

    merge_entries = [
        {"speaker": "segment_summary", "route": "summary.segment", "text": f"segment_left: {left_summary}"},
        {"speaker": "segment_summary", "route": "summary.segment", "text": f"segment_right: {right_summary}"},
    ]
    return _summarize_entries_batch(merge_entries)


def _summarize_turns_with_low_model(turns: list[dict[str, Any]]) -> str:
    """Compatibility wrapper for tests/callers."""
    return _summarize_turns_recursive(turns)


def _persist_session_turn(session_id: str, *, user_text: str, reply: str, route: str) -> None:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    memory_cfg = cfg.get("memory")
    enabled = bool(memory_cfg.get("session_event_logging_enabled")) if isinstance(memory_cfg, dict) else False
    if not enabled:
        return

    events = [
        SessionEvent(
            session_id=session_id,
            event_type="user_message",
            payload={"text": user_text, "route": route},
            source="user",
        ),
        SessionEvent(
            session_id=session_id,
            event_type="assistant_message",
            payload={"text": reply, "route": route},
            source="main_agent",
        ),
    ]
    append_session_events(session_id, events)


def _tool_schemas_for_llm() -> list[dict[str, Any]]:
    """Convert registry metadata into OpenAI-compatible tool schemas."""
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

    schemas: list[dict[str, Any]] = []
    for tool in list_tools():
        properties: dict[str, Any] = {}
        required: list[str] = []
        params = tool.get("parameters")
        if isinstance(params, dict):
            for name, meta in params.items():
                if not isinstance(name, str) or not name:
                    continue
                meta_dict = meta if isinstance(meta, dict) else None
                properties[name] = _param_schema(meta_dict)
                if isinstance(meta, dict) and bool(meta.get("required")):
                    required.append(name)

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
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": parameters_schema,
                },
            }
        )
    return schemas


def _worker_runtime_snapshot_text() -> str:
    try:
        payload = get_worker_manager().list_workers()
    except Exception:
        return "workers unavailable"
    if not payload.get("ok"):
        return "workers unavailable"
    workers = payload.get("workers")
    runtime = payload.get("runtime")
    if not isinstance(workers, list) or not isinstance(runtime, dict):
        return "workers unavailable"

    lines = [
        (
            "workers_runtime "
            f"running={runtime.get('running_count', 0)} "
            f"queued={runtime.get('queued_count', 0)} "
            f"max={runtime.get('max_concurrent_workers', 3)}"
        )
    ]
    for worker in workers[:3]:
        if not isinstance(worker, dict):
            continue
        lines.append(
            (
                f"- {worker.get('worker_id', 'worker?')} "
                f"title={worker.get('title', 'untitled')} "
                f"status={worker.get('status', 'unknown')} "
                f"cancel_requested={worker.get('cancel_requested', False)}"
            )
        )
    return "\n".join(lines)


def _load_forwardable_worker_events() -> list[dict[str, Any]]:
    try:
        payload = get_worker_manager().list_forward_events(consume=True)
    except Exception:
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, dict):
            out.append(event)
    return out


def _load_forwardable_task_agent_events() -> list[dict[str, Any]]:
    try:
        payload = get_central_service().list_forward_events(consume=True)
    except Exception:
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, dict):
            out.append(event)
    return out


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
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    max_steps: int = MAX_TOOL_LOOP_STEPS,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Run model/tool loop until final assistant content is returned."""
    tool_schemas = _tool_schemas_for_llm()
    working_messages = list(messages)
    executed_tools: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None

    for _ in range(max_steps):
        llm_result = call_llm(messages=working_messages, tools=tool_schemas, model=model)
        last_result = llm_result
        if not llm_result.get("ok"):
            return llm_result, "I could not reach the LLM provider.", executed_tools

        tool_calls = llm_result.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            text = llm_result.get("text")
            if isinstance(text, str) and text.strip():
                return llm_result, text, executed_tools
            return llm_result, "(No text returned.)", executed_tools

        working_messages.append(
            {
                "role": "assistant",
                "content": llm_result.get("text") or "",
                "tool_calls": tool_calls,
            }
        )

        for idx, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            tool_name, tool_args, tool_call_id = _parse_tool_call(call, idx=idx)
            if tool_name is None:
                tool_payload = {
                    "ok": False,
                    "error": "Malformed tool call: missing function name.",
                    "source": "tool_registry",
                }
                tool_name = "unknown_tool"
            elif "_raw_arguments" in tool_args:
                tool_payload = {
                    "ok": False,
                    "error": f"Invalid JSON arguments for `{tool_name}`.",
                    "source": "tool_registry",
                    "raw_arguments": tool_args["_raw_arguments"],
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

    if last_result and last_result.get("ok"):
        text = last_result.get("text")
        if isinstance(text, str) and text.strip():
            return last_result, text, executed_tools
    fallback = {
        "ok": False,
        "provider": None,
        "model": None,
        "text": None,
        "tool_calls": None,
        "finish_reason": None,
        "usage": None,
        "raw": None,
        "error": "Tool loop exceeded max steps.",
    }
    return fallback, "I could not complete the tool workflow in time.", executed_tools


def handle_chat_message(
    message: str,
    *,
    allow_llm_fallback: bool = True,
    session_id: str = "default",
) -> dict[str, Any]:
    """Handle one user message via the LLM + tool loop."""
    text = message.strip()
    if not text:
        return {
            "ok": False,
            "reply": "Please enter a message.",
            "route": "validation",
            "data": None,
            "error": "empty_message",
        }

    runtime = _get_session(session_id)
    _refresh_daily_context(runtime)

    if allow_llm_fallback:
        context_bundle = load_context_bundle(query=text, max_supplemental_files=2)
        if runtime.preloaded_daily_context:
            context_bundle.setdefault("supplemental", {})
            context_bundle["supplemental"] = {
                **context_bundle.get("supplemental", {}),
                **runtime.preloaded_daily_context,
            }
        if runtime.facts:
            context_bundle["facts"] = dict(runtime.facts)

        forwarded_worker_events = _load_forwardable_worker_events()
        forwarded_task_agent_events = _load_forwardable_task_agent_events()
        worker_runtime_text = _worker_runtime_snapshot_text()
        turn_events = [
            *runtime.recent_events,
            {"event_type": "system", "payload": {"worker_runtime": worker_runtime_text}},
            *[
                {"event_type": "system", "payload": {"worker_event": event}}
                for event in forwarded_worker_events
            ],
            *[
                {"event_type": "system", "payload": {"task_agent_event": event}}
                for event in forwarded_task_agent_events
            ],
            {"event_type": "user_message", "payload": {"text": text}},
        ]
        assembled = assemble_messages(
            context_bundle=context_bundle,
            recent_events=turn_events,
            session_summary=runtime.session_summary,
        )
        runtime.session_summary = assembled.get("updated_session_summary")
        updated_facts = assembled.get("updated_facts")
        if isinstance(updated_facts, dict):
            runtime.facts = {k: v for k, v in updated_facts.items() if isinstance(v, str)}

        llm_result, reply, executed_tools = _run_llm_with_tools(messages=assembled["messages"])
        if llm_result.get("ok"):
            _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
            _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
            _log_daily_turn(
                session_id,
                route="llm.main_agent",
                user_text=text,
                reply=reply,
                tool_execution=executed_tools,
                worker_events=forwarded_worker_events,
                task_agent_events=forwarded_task_agent_events,
                system_events=[
                    f"worker_runtime: {worker_runtime_text}",
                    (
                        "forwarded_events "
                        f"worker={len(forwarded_worker_events)} "
                        f"task_agent={len(forwarded_task_agent_events)}"
                    ),
                ],
            )
            _persist_session_turn(session_id, user_text=text, reply=reply, route="llm.main_agent")
            return {
                "ok": True,
                "reply": reply,
                "route": "llm.main_agent",
                "data": {
                    **llm_result,
                    "tool_execution": executed_tools,
                    "context_debug": {
                        "base_files_loaded": sorted(context_bundle.get("base", {}).keys()),
                        "supplemental_files_loaded": sorted(context_bundle.get("supplemental", {}).keys()),
                        "assembled_message_count": len(assembled["messages"]),
                        "assembled_token_estimate": assembled["token_estimate"],
                        "session_id": session_id,
                        "kept_recent_message_count": assembled.get("kept_recent_message_count"),
                        "dropped_recent_event_count": assembled.get("dropped_recent_event_count"),
                        "forwarded_worker_events_injected": sum(
                            1
                            for evt in turn_events
                            if evt.get("event_type") == "system"
                            and isinstance(evt.get("payload"), dict)
                            and "worker_event" in evt["payload"]
                        ),
                        "forwarded_task_agent_events_injected": sum(
                            1
                            for evt in turn_events
                            if evt.get("event_type") == "system"
                            and isinstance(evt.get("payload"), dict)
                            and "task_agent_event" in evt["payload"]
                        ),
                    },
                },
                "error": None,
            }
        _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
        _log_daily_turn(
            session_id,
            route="llm.error_fallback",
            user_text=text,
            reply="provider_unavailable",
        )
        _persist_session_turn(
            session_id,
            user_text=text,
            reply="I could not reach the LLM provider.",
            route="llm.error_fallback",
        )
        return {
            "ok": True,
            "reply": (
                "I could not reach the LLM provider. "
                "Please retry in a moment."
            ),
            "route": "llm.error_fallback",
            "data": llm_result,
            "error": llm_result.get("error"),
        }

    return {
        "ok": True,
        "reply": "LLM fallback is disabled for this request.",
        "route": "direct_fallback",
        "data": None,
        "error": None,
    }
