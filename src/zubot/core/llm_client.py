"""Provider-agnostic LLM client entrypoint."""

from __future__ import annotations

from typing import Any

from .config_loader import get_model_config, get_provider_config, load_config
from .providers import call_openrouter


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

        try:
            return call_openrouter(
                api_key=api_key,
                model=endpoint,
                messages=messages,
                max_output_tokens=max_output_tokens or model_cfg.get("max_output_tokens"),
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
