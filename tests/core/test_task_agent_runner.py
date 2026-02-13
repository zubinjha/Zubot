import json
import subprocess
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.core.task_agent_runner import TaskAgentRunner


@pytest.fixture()
def cfg_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "pre_defined_tasks": {
                    "tasks": {
                        "script_task": {
                            "name": "Script Task",
                            "entrypoint_path": "src/zubot/predefined_tasks/indeed_daily_search.py",
                            "args": ["--dry-run"],
                            "timeout_sec": 120,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(cfg_path))
    clear_config_cache()
    return cfg_path


@pytest.fixture()
def runner_with_tasks(cfg_path: Path) -> TaskAgentRunner:
    _ = cfg_path
    return TaskAgentRunner()


def test_describe_run_for_missing_task(runner_with_tasks: TaskAgentRunner):
    out = runner_with_tasks.describe_run(profile_id="missing")
    assert "not defined" in out


def test_describe_run_for_predefined_task(runner_with_tasks: TaskAgentRunner):
    out = runner_with_tasks.describe_run(profile_id="script_task")
    assert "Script Task:" in out
    assert "src/zubot/predefined_tasks/indeed_daily_search.py" in out


def test_run_profile_missing_task_returns_failed(runner_with_tasks: TaskAgentRunner):
    out = runner_with_tasks.run_profile(profile_id="missing")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert "not found" in str(out["error"])


def test_run_profile_predefined_task_success(monkeypatch: pytest.MonkeyPatch, runner_with_tasks: TaskAgentRunner):
    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        _ = kwargs
        return subprocess.CompletedProcess(args=["python"], returncode=0, stdout="all good\n", stderr="")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.run", fake_run)
    out = runner_with_tasks.run_profile(profile_id="script_task", payload={"trigger": "manual"})
    assert out["ok"] is True
    assert out["status"] == "done"
    assert out["model_alias"] == "predefined"
    assert out["summary"] == "all good"


def test_run_profile_predefined_task_failure(monkeypatch: pytest.MonkeyPatch, runner_with_tasks: TaskAgentRunner):
    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = args
        _ = kwargs
        return subprocess.CompletedProcess(args=["python"], returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.run", fake_run)
    out = runner_with_tasks.run_profile(profile_id="script_task")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert "boom" in str(out["error"])
