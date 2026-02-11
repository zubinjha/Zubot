import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import (
    clear_config_cache,
    get_default_model,
    get_home_location,
    get_model_by_alias,
    get_timezone,
    load_config,
    resolve_config_path,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_config_path_uses_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "custom.json"
    _write_json(config_path, {"ok": True})
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))

    resolved = resolve_config_path()
    assert resolved == config_path.resolve()


def test_load_config_reads_json_file(tmp_path: Path):
    clear_config_cache()
    config_path = tmp_path / "config.json"
    payload = {"timezone": "America/New_York"}
    _write_json(config_path, payload)

    result = load_config(config_path=config_path, use_cache=False)
    assert result == payload


def test_load_config_invalid_json_raises_value_error(tmp_path: Path):
    clear_config_cache()
    config_path = tmp_path / "config.json"
    config_path.write_text("{ invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON in config file"):
        load_config(config_path=config_path, use_cache=False)


def test_helpers_resolve_default_model_and_location():
    config = {
        "timezone": "America/New_York",
        "home_location": {"city": "Worthington"},
        "default_model_alias": "medium",
        "models": {
            "gpt5_mini": {"alias": "medium", "endpoint": "openai/gpt-5-mini"},
            "gpt5": {"alias": "high", "endpoint": "openai/gpt-5"},
        },
    }

    assert get_timezone(config) == "America/New_York"
    assert get_home_location(config) == {"city": "Worthington"}
    assert get_model_by_alias("high", config)[0] == "gpt5"
    default_model_id, default_model = get_default_model(config)
    assert default_model_id == "gpt5_mini"
    assert default_model["endpoint"] == "openai/gpt-5-mini"
