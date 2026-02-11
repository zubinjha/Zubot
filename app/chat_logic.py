"""Minimal chat routing logic for local UI testing."""

from __future__ import annotations

from typing import Any

from src.zubot.core.context_assembler import assemble_messages
from src.zubot.core.context_loader import load_context_bundle
from src.zubot.core.llm_client import call_llm
from src.zubot.tools.kernel.time import get_current_time
from src.zubot.tools.kernel.weather import get_today_weather, get_weather_24hr, get_week_outlook


def _contains_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)


def handle_chat_message(message: str, *, allow_llm_fallback: bool = True) -> dict[str, Any]:
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

    lowered = text.lower()

    if "time" in lowered:
        payload = get_current_time()
        return {
            "ok": True,
            "reply": f"Current local time: {payload['human_local']}",
            "route": "direct_tool.time",
            "data": payload,
            "error": None,
        }

    if "weather" in lowered or "forecast" in lowered:
        if _contains_any(lowered, ["24", "24hr", "24-hour", "hourly"]):
            payload = get_weather_24hr()
            return {
                "ok": True,
                "reply": "Here is the 24-hour weather outlook.",
                "route": "direct_tool.weather_24hr",
                "data": payload,
                "error": None,
            }
        if _contains_any(lowered, ["week", "weekly", "7 day", "7-day"]):
            payload = get_week_outlook()
            return {
                "ok": True,
                "reply": "Here is the 7-day weather outlook.",
                "route": "direct_tool.week_outlook",
                "data": payload,
                "error": None,
            }

        payload = get_today_weather()
        return {
            "ok": True,
            "reply": "Here is today's weather summary.",
            "route": "direct_tool.today_weather",
            "data": payload,
            "error": None,
        }

    if allow_llm_fallback:
        context_bundle = load_context_bundle(query=text, max_supplemental_files=2)
        assembled = assemble_messages(
            context_bundle=context_bundle,
            recent_events=[{"event_type": "user_message", "payload": {"text": text}}],
        )
        llm_result = call_llm(messages=assembled["messages"])
        if llm_result.get("ok"):
            reply = llm_result.get("text") or "(No text returned.)"
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
                    },
                },
                "error": None,
            }
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
