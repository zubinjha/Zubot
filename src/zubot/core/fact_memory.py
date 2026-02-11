"""Lightweight durable fact extraction from conversation events."""

from __future__ import annotations

import re
from typing import Any

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def extract_facts_from_text(text: str) -> dict[str, str]:
    lowered = text.lower().strip()
    facts: dict[str, str] = {}

    m = re.search(
        r"\bmy name is\s+([a-zA-Z][a-zA-Z .'-]{1,80}?)(?:[,.!?]| and\b|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        facts["user_name"] = _norm(m.group(1))

    m = re.search(
        r"\bcall me\s+([a-zA-Z][a-zA-Z .'-]{1,80}?)(?:[,.!?]| and\b|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        facts["preferred_name"] = _norm(m.group(1))

    m = re.search(
        r"\bi live in\s+([a-zA-Z0-9 ,.'-]{2,120}?)(?:[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        facts["home_location"] = _norm(m.group(1))

    m = re.search(r"\bmy timezone is\s+([A-Za-z_/\-+0-9:]{2,80})", text, re.IGNORECASE)
    if m:
        facts["timezone"] = _norm(m.group(1))

    if "i prefer " in lowered:
        pref = text[lowered.find("i prefer ") + len("i prefer ") :].strip()
        if pref:
            facts["preference_note"] = _norm(pref[:200])

    return facts


def extract_facts_from_events(
    events: list[dict[str, Any]],
    *,
    existing_facts: dict[str, str] | None = None,
    max_facts: int = 20,
) -> dict[str, str]:
    facts: dict[str, str] = dict(existing_facts or {})

    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "user_message":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        for key, value in extract_facts_from_text(text).items():
            facts[key] = value

    if len(facts) <= max_facts:
        return facts

    # Keep a stable subset when max_facts is exceeded.
    keys = sorted(facts.keys())[:max_facts]
    return {key: facts[key] for key in keys}
