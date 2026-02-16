import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.core.token_estimator import (
    compute_budget,
    estimate_messages_tokens,
    estimate_text_tokens,
    get_model_token_limits,
)


def test_estimate_text_tokens_non_empty():
    assert estimate_text_tokens("hello world") > 0
    assert estimate_text_tokens("") == 0


def test_estimate_messages_tokens():
    messages = [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]
    assert estimate_messages_tokens(messages) > 0


def test_compute_budget_within_and_over():
    ok_budget = compute_budget(input_tokens=100, max_context_tokens=1000, reserved_output_tokens=200)
    assert ok_budget["within_budget"]
    over_budget = compute_budget(input_tokens=900, max_context_tokens=1000, reserved_output_tokens=200)
    assert not over_budget["within_budget"]


def test_get_model_token_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "default_model_alias": "medium",
                "model_aliases": {"medium": "gpt5_mini", "med": "gpt5_mini"},
                "models": {
                    "gpt5_mini": {
                        "max_context_tokens": 400000,
                        "max_output_tokens": 128000,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()

    limits = get_model_token_limits()
    assert limits["max_context_tokens"] == 400000
    assert limits["max_output_tokens"] == 128000
