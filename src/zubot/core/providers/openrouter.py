"""OpenRouter chat-completions adapter."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or str(exc)
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("message") or err.get("code") or detail)
                elif isinstance(err, str):
                    detail = err
                elif isinstance(parsed.get("message"), str):
                    detail = parsed["message"]
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("Provider response must be a JSON object.")
    return parsed


def call_openrouter(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    max_output_tokens: int | None = None,
    temperature: float | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout_sec: int = 30,
    base_url: str = OPENROUTER_CHAT_URL,
    referer: str | None = None,
    app_title: str | None = None,
) -> dict[str, Any]:
    """Call OpenRouter and normalize completion output."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if app_title:
        headers["X-Title"] = app_title

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if max_output_tokens is not None:
        payload["max_tokens"] = int(max_output_tokens)
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if tools:
        payload["tools"] = tools

    raw = _post_json(base_url, headers=headers, payload=payload, timeout_sec=timeout_sec)
    choices = raw.get("choices", [])
    first = choices[0] if isinstance(choices, list) and choices else {}
    if not isinstance(first, dict):
        first = {}
    message = first.get("message")
    if not isinstance(message, dict):
        message = {}

    return {
        "ok": True,
        "provider": "openrouter",
        "model": model,
        "text": message.get("content"),
        "tool_calls": message.get("tool_calls"),
        "finish_reason": first.get("finish_reason"),
        "usage": raw.get("usage"),
        "raw": raw,
        "error": None,
    }
