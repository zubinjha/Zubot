import json
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
                "memory": {"autoload_summary_days": 3},
                "default_model_alias": "medium",
                "model_providers": {"openrouter": {"apikey": "x"}},
                "models": {
                    "gpt5_mini": {"alias": "medium", "endpoint": "openai/gpt-5-mini", "provider": "openrouter"},
                    "gpt5_nano": {"alias": "low", "endpoint": "openai/gpt-5-nano", "provider": "openrouter"},
                },
                "task_agents": {
                    "profiles": {
                        "profile_a": {
                            "name": "Profile A",
                            "instructions_template": "First line\nSecond line",
                            "model_alias": "medium",
                            "tool_access": ["get_current_time"],
                            "skill_access": ["skill_demo"],
                            "preload_files": ["context/custom.md"],
                        },
                        "profile_long": {
                            "name": "Long Profile",
                            "instructions_template": "x" * 240,
                            "model_alias": "high",
                            "tool_access": [],
                            "skill_access": [],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(cfg_path))
    clear_config_cache()
    return cfg_path


class _FakeSubRunner:
    def __init__(self, *, ok: bool = True, status: str = "success", summary: str = "ok", error: str | None = None) -> None:
        self.ok = ok
        self.status = status
        self.summary = summary
        self.error = error
        self.calls: list[dict] = []

    def run_task(self, task, **kwargs):  # noqa: ANN001
        self.calls.append({"task": task, "kwargs": kwargs})
        return {
            "ok": self.ok,
            "result": {
                "task_id": task.task_id,
                "status": self.status,
                "summary": self.summary,
                "artifacts": [],
                "error": self.error,
                "trace": [],
            },
        }


@pytest.fixture()
def runner_with_profiles(cfg_path: Path) -> TaskAgentRunner:
    fake_runner = _FakeSubRunner(ok=True, status="success", summary="profile run ok", error=None)
    return TaskAgentRunner(runner=fake_runner)


def test_describe_run_for_missing_profile(runner_with_profiles: TaskAgentRunner):
    out = runner_with_profiles.describe_run(profile_id="missing")
    assert "not defined" in out


def test_describe_run_uses_template_and_normalizes_newlines(runner_with_profiles: TaskAgentRunner):
    out = runner_with_profiles.describe_run(profile_id="profile_a")
    assert out.startswith("Profile A:")
    assert "\n" not in out
    assert "First line Second line" in out


def test_describe_run_truncates_long_template(runner_with_profiles: TaskAgentRunner):
    out = runner_with_profiles.describe_run(profile_id="profile_long")
    assert out.startswith("Long Profile:")
    assert out.endswith("...")


def test_run_profile_missing_profile_returns_failed(runner_with_profiles: TaskAgentRunner):
    out = runner_with_profiles.run_profile(profile_id="missing")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert out["summary"] is None
    assert "not found" in str(out["error"])


def test_run_profile_happy_path(runner_with_profiles: TaskAgentRunner):
    out = runner_with_profiles.run_profile(profile_id="profile_a")
    assert out["ok"] is True
    assert out["status"] == "done"
    assert out["error"] is None or isinstance(out["error"], str)
    assert "Profile A:" in str(out["current_description"])


def test_run_profile_applies_model_tools_skills_and_context(monkeypatch: pytest.MonkeyPatch, cfg_path: Path):
    fake_runner = _FakeSubRunner(ok=True, status="success", summary="profile run ok", error=None)
    runner = TaskAgentRunner(runner=fake_runner)

    def fake_load_base_context(*, files=None, **_kwargs):
        if files == ["context/custom.md"]:
            return {"context/custom.md": "custom context"}
        return {path: f"loaded:{path}" for path in (files or [])}

    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_base_context", fake_load_base_context)
    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_recent_daily_memory", lambda days=2: {"memory/daily/summary/today.md": f"days={days}"})

    out = runner.run_profile(profile_id="profile_a", payload={"trigger": "manual", "description": "manual run"})
    assert out["ok"] is True
    assert out["status"] == "done"
    assert out["model_alias"] == "medium"
    assert out["used_tool_access"] == ["get_current_time"]
    assert out["used_skill_access"] == ["skill_demo"]

    call = fake_runner.calls[0]
    task = call["task"]
    kwargs = call["kwargs"]
    assert task.requested_by == "task_agent:profile_a"
    assert task.model_tier == "medium"
    assert task.tool_access == ["get_current_time"]
    assert task.skill_access == ["skill_demo"]
    assert "manual run" in task.instructions
    assert kwargs["model"] == "medium"
    assert kwargs["allow_orchestration_tools"] is True
    assert "context/TASK_AGENT.md" in kwargs["base_context"]
    assert "context/TASK_SOUL.md" in kwargs["base_context"]
    assert "context/custom.md" in kwargs["supplemental_context"]
    assert "memory/daily/summary/today.md" in kwargs["supplemental_context"]


def test_run_profile_rewrites_spawn_worker_tool_for_reserve_safety(monkeypatch: pytest.MonkeyPatch, cfg_path: Path):
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["task_agents"]["profiles"]["profile_a"]["tool_access"] = ["spawn_worker", "get_current_time"]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    clear_config_cache()

    fake_runner = _FakeSubRunner(ok=True, status="success", summary="ok", error=None)
    runner = TaskAgentRunner(runner=fake_runner)
    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_base_context", lambda **_kwargs: {})
    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_recent_daily_memory", lambda **_kwargs: {})

    out = runner.run_profile(profile_id="profile_a")
    assert out["ok"] is True
    assert "spawn_task_agent_worker" in out["used_tool_access"]
    assert "spawn_worker" not in out["used_tool_access"]


def test_run_profile_invalid_model_alias_returns_failed(monkeypatch: pytest.MonkeyPatch, cfg_path: Path):
    runner = TaskAgentRunner()
    out = runner.run_profile(profile_id="profile_long")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert "invalid model alias" in str(out["error"])


def test_run_profile_maps_needs_user_input_to_blocked(monkeypatch: pytest.MonkeyPatch, cfg_path: Path):
    fake_runner = _FakeSubRunner(ok=True, status="needs_user_input", summary="Need data", error=None)
    runner = TaskAgentRunner(runner=fake_runner)
    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_base_context", lambda **_kwargs: {})
    monkeypatch.setattr("src.zubot.core.task_agent_runner.load_recent_daily_memory", lambda **_kwargs: {})
    out = runner.run_profile(profile_id="profile_a")
    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert out["summary"] == "Need data"
