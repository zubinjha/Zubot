"""Provider-agnostic LLM client entrypoint."""

from __future__ import annotations

import socket
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError

from .config_loader import get_model_config, get_provider_config, load_config
from .providers import call_openrouter


def _is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        # Retry transient upstream failures/rate limits.
        return int(exc.code) in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror):
            return True
        if isinstance(reason, TimeoutError):
            return True
        if isinstance(reason, OSError):
            return True
        # Some URLError reasons come through as strings.
        if isinstance(reason, str):
            low = reason.lower()
            return "timed out" in low or "temporary failure" in low or "name resolution" in low
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


def _call_openrouter_with_retry(
    *,
    attempts: int,
    base_backoff_ms: int,
    **kwargs: Any,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    safe_attempts = max(1, int(attempts))
    safe_backoff = max(0, int(base_backoff_ms))
    for attempt in range(1, safe_attempts + 1):
        try:
            return call_openrouter(**kwargs)
        except Exception as exc:  # pragma: no cover - exercised via call_llm test path
            last_exc = exc
            if attempt >= safe_attempts or not _is_retryable_provider_error(exc):
                raise
            if safe_backoff > 0:
                sleep((safe_backoff * attempt) / 1000.0)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter retry loop exited unexpectedly.")


def call_llm(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Resolve model/provider from config and execute one model call."""
    payload = load_config()
    model_id, model_cfg = get_model_config(model, payload)
    provider_name = model_cfg.get("provider")
    if not isinstance(provider_name, str) or not provider_name:
        return {
            "ok": False,
            "provider": None,
            "model": model_id,
            "text": None,
            "tool_calls": None,
            "finish_reason": None,
            "usage": None,
            "raw": None,
            "error": f"Model '{model_id}' missing provider.",
        }

    provider_cfg = get_provider_config(provider_name, payload)
    endpoint = model_cfg.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return {
            "ok": False,
            "provider": provider_name,
            "model": model_id,
            "text": None,
            "tool_calls": None,
            "finish_reason": None,
            "usage": None,
            "raw": None,
            "error": f"Model '{model_id}' missing endpoint.",
        }

    if provider_name == "openrouter":
        api_key = provider_cfg.get("apikey")
        if not isinstance(api_key, str) or not api_key:
            return {
                "ok": False,
                "provider": provider_name,
                "model": model_id,
                "text": None,
                "tool_calls": None,
                "finish_reason": None,
                "usage": None,
                "raw": None,
                "error": "OpenRouter API key missing.",
            }

        provider_timeout = timeout_sec
        if provider_timeout is None:
            configured = provider_cfg.get("timeout_sec")
            provider_timeout = int(configured) if configured is not None else 30
        retry_attempts_raw = provider_cfg.get("retry_attempts")
        retry_attempts = int(retry_attempts_raw) if retry_attempts_raw is not None else 3
        retry_backoff_raw = provider_cfg.get("retry_backoff_ms")
        retry_backoff_ms = int(retry_backoff_raw) if retry_backoff_raw is not None else 400

        try:
            return _call_openrouter_with_retry(
                attempts=retry_attempts,
                base_backoff_ms=retry_backoff_ms,
                api_key=api_key,
                model=endpoint,
                messages=messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                tools=tools,
                timeout_sec=provider_timeout,
                base_url=provider_cfg.get("base_url", None)
                or "https://openrouter.ai/api/v1/chat/completions",
                referer=provider_cfg.get("referer"),
                app_title=provider_cfg.get("app_title"),
            )
        except Exception as exc:
            return {
                "ok": False,
                "provider": provider_name,
                "model": model_id,
                "text": None,
                "tool_calls": None,
                "finish_reason": None,
                "usage": None,
                "raw": None,
                "error": str(exc),
            }

    return {
        "ok": False,
        "provider": provider_name,
        "model": model_id,
        "text": None,
        "tool_calls": None,
        "finish_reason": None,
        "usage": None,
        "raw": None,
        "error": f"Unsupported provider '{provider_name}'.",
    }
