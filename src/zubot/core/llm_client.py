"""Provider-agnostic LLM client entrypoint."""

from __future__ import annotations

import socket
import re
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError

from .config_loader import get_model_config, get_provider_config, load_config
from .providers import call_openrouter

DEFAULT_RETRY_BACKOFF_SCHEDULE_SEC = (1.0, 3.0, 5.0)


class _ProviderCallError(Exception):
    def __init__(
        self,
        *,
        cause: Exception,
        attempts_used: int,
        attempts_configured: int,
        retryable_error: bool,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts_used = attempts_used
        self.attempts_configured = attempts_configured
        self.retryable_error = retryable_error


def _exception_chain(exc: Exception) -> list[Exception]:
    chain: list[Exception] = []
    seen: set[int] = set()
    current: Exception | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        nxt = current.__cause__ if isinstance(current.__cause__, Exception) else None
        if nxt is None:
            nxt = current.__context__ if isinstance(current.__context__, Exception) else None
        current = nxt
    return chain


def _http_code_from_text(text: str) -> int | None:
    match = re.search(r"\bHTTP\s+(\d{3})\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _is_retryable_provider_error(exc: Exception) -> bool:
    for current in _exception_chain(exc):
        if isinstance(current, HTTPError):
            # Retry transient upstream failures/rate limits.
            return int(current.code) in {408, 425, 429, 500, 502, 503, 504}
        if isinstance(current, URLError):
            reason = getattr(current, "reason", None)
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
        if isinstance(current, TimeoutError):
            return True
        code = _http_code_from_text(str(current))
        if code is not None:
            return code in {408, 425, 429, 500, 502, 503, 504}
    return False


def _call_openrouter_with_retry(
    *,
    attempts: int,
    backoff_schedule_sec: list[float],
    **kwargs: Any,
) -> tuple[dict[str, Any], int]:
    last_exc: Exception | None = None
    safe_attempts = max(1, int(attempts))
    schedule = [float(max(0.0, val)) for val in backoff_schedule_sec]
    for attempt in range(1, safe_attempts + 1):
        try:
            return call_openrouter(**kwargs), attempt
        except Exception as exc:  # pragma: no cover - exercised via call_llm test path
            last_exc = exc
            retryable = _is_retryable_provider_error(exc)
            if attempt >= safe_attempts or not retryable:
                raise _ProviderCallError(
                    cause=exc,
                    attempts_used=attempt,
                    attempts_configured=safe_attempts,
                    retryable_error=retryable,
                ) from exc
            delay = schedule[min(attempt - 1, len(schedule) - 1)] if schedule else 0.0
            if delay > 0:
                sleep(delay)
    if last_exc is not None:
        raise _ProviderCallError(
            cause=last_exc,
            attempts_used=safe_attempts,
            attempts_configured=safe_attempts,
            retryable_error=_is_retryable_provider_error(last_exc),
        ) from last_exc
    raise RuntimeError("OpenRouter retry loop exited unexpectedly.")


def _coerce_retry_schedule_sec(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return [float(val) for val in DEFAULT_RETRY_BACKOFF_SCHEDULE_SEC]
    out: list[float] = []
    for val in raw:
        if isinstance(val, (int, float)) and float(val) >= 0:
            out.append(float(val))
    if out:
        return out
    return [float(val) for val in DEFAULT_RETRY_BACKOFF_SCHEDULE_SEC]


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
        retry_schedule_sec = _coerce_retry_schedule_sec(provider_cfg.get("retry_backoff_schedule_sec"))
        retry_attempts_raw = provider_cfg.get("retry_attempts")
        if isinstance(retry_attempts_raw, int) and retry_attempts_raw > 0:
            retry_attempts = max(retry_attempts_raw, len(retry_schedule_sec) + 1)
        else:
            retry_attempts = len(retry_schedule_sec) + 1

        try:
            response, attempts_used = _call_openrouter_with_retry(
                attempts=retry_attempts,
                backoff_schedule_sec=retry_schedule_sec,
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
            if isinstance(response, dict):
                payload = dict(response)
                payload["attempts_used"] = attempts_used
                payload["attempts_configured"] = retry_attempts
                payload["retryable_error"] = False
                payload["retry_backoff_schedule_sec"] = retry_schedule_sec
                return payload
            return response
        except _ProviderCallError as exc:
            return {
                "ok": False,
                "provider": provider_name,
                "model": model_id,
                "text": None,
                "tool_calls": None,
                "finish_reason": None,
                "usage": None,
                "raw": None,
                "error": str(exc.cause),
                "attempts_used": int(exc.attempts_used),
                "attempts_configured": int(exc.attempts_configured),
                "retryable_error": bool(exc.retryable_error),
                "retry_backoff_schedule_sec": retry_schedule_sec,
            }
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
                "attempts_used": 1,
                "attempts_configured": retry_attempts,
                "retryable_error": _is_retryable_provider_error(exc),
                "retry_backoff_schedule_sec": retry_schedule_sec,
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
