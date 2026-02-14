import json
from pathlib import Path
from threading import Event

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
    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args
            _ = kwargs
            self.returncode = 0

        def poll(self):
            return self.returncode

        def communicate(self):
            return ("all good\n", "")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.Popen", _FakePopen)
    out = runner_with_tasks.run_profile(profile_id="script_task", payload={"trigger": "manual"})
    assert out["ok"] is True
    assert out["status"] == "done"
    assert out["model_alias"] == "predefined"
    assert out["summary"] == "all good"


def test_run_profile_predefined_task_failure(monkeypatch: pytest.MonkeyPatch, runner_with_tasks: TaskAgentRunner):
    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args
            _ = kwargs
            self.returncode = 2

        def poll(self):
            return self.returncode

        def communicate(self):
            return ("", "boom")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.Popen", _FakePopen)
    out = runner_with_tasks.run_profile(profile_id="script_task")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert "boom" in str(out["error"])


def test_run_profile_predefined_task_cancelled(monkeypatch: pytest.MonkeyPatch, runner_with_tasks: TaskAgentRunner):
    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args
            _ = kwargs
            self.returncode = None
            self._terminated = False

        def poll(self):
            if self._terminated:
                return 143
            return None

        def terminate(self):
            self._terminated = True
            self.returncode = 143

        def wait(self, timeout=None):
            _ = timeout
            return self.returncode

        def kill(self):
            self._terminated = True
            self.returncode = 137

        def communicate(self):
            return ("", "")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.Popen", _FakePopen)
    cancel = Event()
    cancel.set()
    out = runner_with_tasks.run_profile(profile_id="script_task", cancel_event=cancel)
    assert out["ok"] is False
    assert out["status"] == "blocked"
