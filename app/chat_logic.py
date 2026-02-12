"""Minimal chat routing logic for local UI testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.zubot.core.context_assembler import assemble_messages
from src.zubot.core.context_loader import load_context_bundle
from src.zubot.core.daily_memory import append_daily_memory_entry, load_recent_daily_memory
from src.zubot.core.llm_client import call_llm
from src.zubot.tools.kernel.time import get_current_time
from src.zubot.tools.kernel.weather import get_today_weather, get_weather_24hr, get_week_outlook

MAX_RECENT_EVENTS = 60
DAILY_MEMORY_FLUSH_EVERY_TURNS = 6
DAILY_MEMORY_MAX_BUFFER_ITEMS = 24


@dataclass
class SessionRuntime:
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    session_summary: str | None = None
    facts: dict[str, str] = field(default_factory=dict)
    preloaded_daily_context: dict[str, str] = field(default_factory=dict)
    daily_turn_buffer: list[dict[str, str]] = field(default_factory=list)
    turns_since_daily_flush: int = 0


_SESSIONS: dict[str, SessionRuntime] = {}


def _contains_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def _get_session(session_id: str) -> SessionRuntime:
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        runtime = SessionRuntime(preloaded_daily_context=load_recent_daily_memory(days=2))
        _SESSIONS[session_id] = runtime
    return runtime


def _refresh_daily_context(runtime: SessionRuntime) -> None:
    runtime.preloaded_daily_context = load_recent_daily_memory(days=2)


def initialize_session_context(session_id: str) -> dict[str, Any]:
    runtime = _get_session(session_id)
    _refresh_daily_context(runtime)
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
        },
    }


def reset_session_context(session_id: str) -> dict[str, Any]:
    runtime = _SESSIONS.get(session_id)
    if runtime is not None:
        _flush_daily_summary(runtime, session_id=session_id, reason="session_reset")
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


def _log_daily_turn(session_id: str, *, route: str, user_text: str, reply: str) -> None:
    runtime = _get_session(session_id)
    trimmed_user = " ".join(user_text.strip().split())[:280]
    trimmed_reply = " ".join(reply.strip().split())[:280]
    runtime.daily_turn_buffer.append(
        {
            "route": route,
            "user": trimmed_user,
            "reply": trimmed_reply,
        }
    )
    if len(runtime.daily_turn_buffer) > DAILY_MEMORY_MAX_BUFFER_ITEMS:
        runtime.daily_turn_buffer = runtime.daily_turn_buffer[-DAILY_MEMORY_MAX_BUFFER_ITEMS:]
    runtime.turns_since_daily_flush += 1
    if runtime.turns_since_daily_flush >= DAILY_MEMORY_FLUSH_EVERY_TURNS:
        _flush_daily_summary(runtime, session_id=session_id, reason="interval")


def _flush_daily_summary(runtime: SessionRuntime, *, session_id: str, reason: str) -> dict[str, Any] | None:
    if not runtime.daily_turn_buffer:
        return None

    turns = list(runtime.daily_turn_buffer)
    route_counts: dict[str, int] = {}
    for turn in turns:
        route = turn.get("route", "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1

    route_summary = ", ".join(f"{route} x{count}" for route, count in sorted(route_counts.items()))
    highlights = "; ".join(
        f"user='{turn.get('user', '')[:90]}' -> {turn.get('route', 'unknown')}" for turn in turns[-3:]
    )

    write = append_daily_memory_entry(
        session_id=session_id,
        kind="summary",
        text=f"reason={reason} turns={len(turns)} routes=[{route_summary}] highlights=[{highlights}]",
    )
    runtime.daily_turn_buffer = []
    runtime.turns_since_daily_flush = 0
    return write


def handle_chat_message(
    message: str,
    *,
    allow_llm_fallback: bool = True,
    session_id: str = "default",
) -> dict[str, Any]:
    """Handle one user message with direct-tool routing first."""
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
    lowered = text.lower()

    if "time" in lowered:
        payload = get_current_time()
        reply = f"Current local time: {payload['human_local']}"
        _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
        _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
        _log_daily_turn(session_id, route="direct_tool.time", user_text=text, reply=reply)
        return {
            "ok": True,
            "reply": reply,
            "route": "direct_tool.time",
            "data": payload,
            "error": None,
        }

    if "weather" in lowered or "forecast" in lowered:
        if _contains_any(lowered, ["24", "24hr", "24-hour", "hourly"]):
            payload = get_weather_24hr()
            reply = "Here is the 24-hour weather outlook."
            _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
            _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
            _log_daily_turn(session_id, route="direct_tool.weather_24hr", user_text=text, reply=reply)
            return {
                "ok": True,
                "reply": reply,
                "route": "direct_tool.weather_24hr",
                "data": payload,
                "error": None,
            }
        if _contains_any(lowered, ["week", "weekly", "7 day", "7-day"]):
            payload = get_week_outlook()
            reply = "Here is the 7-day weather outlook."
            _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
            _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
            _log_daily_turn(session_id, route="direct_tool.week_outlook", user_text=text, reply=reply)
            return {
                "ok": True,
                "reply": reply,
                "route": "direct_tool.week_outlook",
                "data": payload,
                "error": None,
            }

        payload = get_today_weather()
        reply = "Here is today's weather summary."
        _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
        _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
        _log_daily_turn(session_id, route="direct_tool.today_weather", user_text=text, reply=reply)
        return {
            "ok": True,
            "reply": reply,
            "route": "direct_tool.today_weather",
            "data": payload,
            "error": None,
        }

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

        turn_events = [*runtime.recent_events, {"event_type": "user_message", "payload": {"text": text}}]
        assembled = assemble_messages(
            context_bundle=context_bundle,
            recent_events=turn_events,
            session_summary=runtime.session_summary,
        )
        runtime.session_summary = assembled.get("updated_session_summary")
        updated_facts = assembled.get("updated_facts")
        if isinstance(updated_facts, dict):
            runtime.facts = {k: v for k, v in updated_facts.items() if isinstance(v, str)}

        llm_result = call_llm(messages=assembled["messages"])
        if llm_result.get("ok"):
            reply = llm_result.get("text") or "(No text returned.)"
            _append_session_event(runtime, {"event_type": "user_message", "payload": {"text": text}})
            _append_session_event(runtime, {"event_type": "assistant_message", "payload": {"text": reply}})
            _log_daily_turn(session_id, route="llm.main_agent", user_text=text, reply=reply)
            return {
                "ok": True,
                "reply": reply,
                "route": "llm.main_agent",
                "data": {
                    **llm_result,
                    "context_debug": {
                        "base_files_loaded": sorted(context_bundle.get("base", {}).keys()),
                        "supplemental_files_loaded": sorted(context_bundle.get("supplemental", {}).keys()),
                        "assembled_message_count": len(assembled["messages"]),
                        "assembled_token_estimate": assembled["token_estimate"],
                        "session_id": session_id,
                        "kept_recent_message_count": assembled.get("kept_recent_message_count"),
                        "dropped_recent_event_count": assembled.get("dropped_recent_event_count"),
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
        return {
            "ok": True,
            "reply": (
                "I could not reach the LLM provider. "
                "I can still handle direct requests like time and weather."
            ),
            "route": "llm.error_fallback",
            "data": llm_result,
            "error": llm_result.get("error"),
        }

    return {
        "ok": True,
        "reply": "I can currently handle direct time/weather requests in this test UI.",
        "route": "direct_fallback",
        "data": None,
        "error": None,
    }
