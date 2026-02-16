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


def test_run_profile_agentic_task_uses_sub_agent_runner():
    class _FakeSubRunner:
        def run_task(self, task, **kwargs):  # noqa: ANN001
            _ = kwargs
            assert task["instructions"] == "Research XYZ"
            return {
                "ok": True,
                "result": {"status": "success", "summary": "Research complete.", "error": None},
            }

    runner = TaskAgentRunner(runner=_FakeSubRunner())
    out = runner.run_profile(
        profile_id="agentic_task",
        payload={
            "run_kind": "agentic",
            "task_name": "Research Task",
            "instructions": "Research XYZ",
            "requested_by": "ui",
            "model_tier": "medium",
            "tool_access": ["web_search"],
            "skill_access": [],
            "timeout_sec": 60,
        },
    )
    assert out["ok"] is True
    assert out["status"] == "done"
    assert out["summary"] == "Research complete."


def test_run_profile_agentic_waiting_for_user_maps_status():
    class _FakeSubRunner:
        def run_task(self, task, **kwargs):  # noqa: ANN001
            _ = task
            _ = kwargs
            return {
                "ok": True,
                "result": {
                    "status": "needs_user_input",
                    "summary": "Need your choice.",
                    "error": None,
                    "wait_context": {"choices": ["a", "b"]},
                    "wait_timeout_sec": 30,
                },
            }

    runner = TaskAgentRunner(runner=_FakeSubRunner())
    out = runner.run_profile(
        profile_id="agentic_task",
        payload={
            "run_kind": "agentic",
            "task_name": "Research Task",
            "instructions": "Research XYZ",
        },
    )
    assert out["ok"] is True
    assert out["status"] == "waiting_for_user"
    assert out["question"] == "Need your choice."
    assert out["wait_context"]["choices"] == ["a", "b"]
    assert out["wait_timeout_sec"] == 30


def test_run_profile_script_injects_task_local_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    entrypoint = tmp_path / "scripts" / "task.py"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text("print('ok')\n", encoding="utf-8")

    resources_dir = tmp_path / "tasks" / "script_task"
    resources_dir.mkdir(parents=True, exist_ok=True)
    (resources_dir / "config.json").write_text(json.dumps({"cursor": 3}), encoding="utf-8")

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "task_profiles": {
                    "tasks": {
                        "script_task": {
                            "name": "Script Task",
                            "kind": "script",
                            "entrypoint_path": "scripts/task.py",
                            "resources_path": "tasks/script_task",
                            "args": [],
                            "timeout_sec": 60,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr("src.zubot.core.task_agent_runner._repo_root", lambda: tmp_path)
    clear_config_cache()

    captured: dict[str, dict] = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args
            captured["env"] = kwargs.get("env") or {}
            self.returncode = 0

        def poll(self):
            return self.returncode

        def communicate(self):
            return ("ok\n", "")

    monkeypatch.setattr("src.zubot.core.task_agent_runner.subprocess.Popen", _FakePopen)

    runner = TaskAgentRunner()
    out = runner.run_profile(profile_id="script_task", payload={"trigger": "manual"})
    assert out["ok"] is True
    env = captured["env"]
    assert json.loads(env["ZUBOT_TASK_LOCAL_CONFIG_JSON"]) == {"cursor": 3}
    assert json.loads(env["ZUBOT_TASK_PROFILE_JSON"])["resources_path"] == "tasks/script_task"
    assert env["ZUBOT_TASK_RESOURCES_DIR"] == str(resources_dir.resolve())
    clear_config_cache()
