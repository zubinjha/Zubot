import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.core.llm_client import call_llm
from urllib.error import URLError


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def configured_openrouter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "config.json"
    _write_config(
        path,
        {
            "default_model_alias": "medium",
            "model_providers": {"openrouter": {"apikey": "KEY_123"}},
            "models": {
                "gpt5_mini": {
                    "alias": "medium",
                    "provider": "openrouter",
                    "endpoint": "openai/gpt-5-mini",
                    "max_output_tokens": 128000,
                }
            },
        },
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(path))
    clear_config_cache()
    return path


def test_call_llm_resolves_and_invokes_provider(configured_openrouter, monkeypatch):
    def fake_call_openrouter(**kwargs):
        assert kwargs["model"] == "openai/gpt-5-mini"
        assert kwargs["api_key"] == "KEY_123"
        assert kwargs["max_output_tokens"] is None
        return {
            "ok": True,
            "provider": "openrouter",
            "model": kwargs["model"],
            "text": "hello",
            "tool_calls": None,
            "finish_reason": "stop",
            "usage": {"total_tokens": 10},
            "raw": {},
            "error": None,
        }

    monkeypatch.setattr("src.zubot.core.llm_client.call_openrouter", fake_call_openrouter)
    result = call_llm(messages=[{"role": "user", "content": "Hi"}])
    assert result["ok"]
    assert result["provider"] == "openrouter"
    assert result["text"] == "hello"


def test_call_llm_unsupported_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "config.json"
    _write_config(
        path,
        {
            "default_model_alias": "medium",
            "model_providers": {"other": {"apikey": "x"}},
            "models": {"m": {"alias": "medium", "provider": "other", "endpoint": "x"}},
        },
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(path))
    clear_config_cache()

    result = call_llm(messages=[{"role": "user", "content": "Hi"}])
    assert not result["ok"]
    assert "Unsupported provider" in result["error"]


def test_call_llm_retries_transient_openrouter_error(configured_openrouter, monkeypatch):
    attempts = {"n": 0}

    def flaky_call_openrouter(**kwargs):
        _ = kwargs
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise URLError("temporary failure in name resolution")
        return {
            "ok": True,
            "provider": "openrouter",
            "model": "openai/gpt-5-mini",
            "text": "hello",
            "tool_calls": None,
            "finish_reason": "stop",
            "usage": {"total_tokens": 10},
            "raw": {},
            "error": None,
        }

    monkeypatch.setattr("src.zubot.core.llm_client.call_openrouter", flaky_call_openrouter)
    monkeypatch.setattr("src.zubot.core.llm_client.sleep", lambda _s: None)
    result = call_llm(messages=[{"role": "user", "content": "Hi"}])
    assert result["ok"] is True
    assert attempts["n"] == 3


def test_call_llm_does_not_retry_non_retryable_error(configured_openrouter, monkeypatch):
    attempts = {"n": 0}

    def bad_request_openrouter(**kwargs):
        _ = kwargs
        attempts["n"] += 1
        raise ValueError("invalid payload")

    monkeypatch.setattr("src.zubot.core.llm_client.call_openrouter", bad_request_openrouter)
    monkeypatch.setattr("src.zubot.core.llm_client.sleep", lambda _s: None)
    result = call_llm(messages=[{"role": "user", "content": "Hi"}])
    assert result["ok"] is False
    assert "invalid payload" in result["error"]
    assert attempts["n"] == 1
