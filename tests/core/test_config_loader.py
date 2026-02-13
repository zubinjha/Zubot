import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import (
    clear_config_cache,
    get_central_service_config,
    get_default_model,
    get_max_concurrent_workers,
    get_model_config,
    get_model_by_id,
    get_home_location,
    get_model_by_alias,
    get_predefined_task_config,
    get_task_agent_config,
    get_worker_runtime_config,
    get_provider_config,
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
        "agent_loop": {"max_concurrent_workers": 3},
        "central_service": {
            "enabled": False,
            "poll_interval_sec": 90,
            "task_runner_concurrency": 2,
            "scheduler_db_path": "memory/central/zubot_core.db",
            "worker_slot_reserve_for_workers": 2,
            "run_history_retention_days": 14,
            "run_history_max_rows": 1000,
            "memory_manager_sweep_interval_sec": 7200,
            "memory_manager_completion_debounce_sec": 120,
            "queue_warning_threshold": 10,
            "running_age_warning_sec": 600,
        },
        "pre_defined_tasks": {
            "tasks": {
                "profile_a": {
                    "name": "Profile A",
                    "entrypoint_path": "src/zubot/predefined_tasks/indeed_daily_search.py",
                    "args": [],
                    "timeout_sec": 120,
                }
            }
        },
        "default_model_alias": "medium",
        "model_providers": {"openrouter": {"apikey": "x"}},
        "models": {
            "gpt5_mini": {"alias": "medium", "endpoint": "openai/gpt-5-mini", "provider": "openrouter"},
            "gpt5": {"alias": "high", "endpoint": "openai/gpt-5"},
        },
    }

    assert get_timezone(config) == "America/New_York"
    assert get_home_location(config) == {"city": "Worthington"}
    assert get_model_by_alias("high", config)[0] == "gpt5"
    default_model_id, default_model = get_default_model(config)
    assert default_model_id == "gpt5_mini"
    assert default_model["endpoint"] == "openai/gpt-5-mini"
    assert get_model_by_id("gpt5_mini", config)["alias"] == "medium"
    assert get_model_config("gpt5_mini", config)[0] == "gpt5_mini"
    assert get_provider_config("openrouter", config)["apikey"] == "x"
    assert get_max_concurrent_workers(config) == 3
    worker_runtime = get_worker_runtime_config(config)
    assert worker_runtime["max_events_per_worker"] == 200
    assert worker_runtime["completed_worker_retention"] == 200
    central = get_central_service_config(config)
    assert central["enabled"] is False
    assert central["poll_interval_sec"] == 90
    assert central["run_history_retention_days"] == 14
    assert central["run_history_max_rows"] == 1000
    assert central["memory_manager_sweep_interval_sec"] == 7200
    assert central["memory_manager_completion_debounce_sec"] == 120
    assert central["queue_warning_threshold"] == 10
    assert central["running_age_warning_sec"] == 600
    predefined = get_predefined_task_config(config)
    assert "profile_a" in predefined["tasks"]
    compat = get_task_agent_config(config)
    assert "profile_a" in compat["tasks"]
