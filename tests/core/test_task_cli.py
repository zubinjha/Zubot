from __future__ import annotations

from pathlib import Path

import pytest

from src.zubot.daemon import task_cli


def test_resolve_profile_prefers_registered():
    profile = {"task_id": "trace_ping", "kind": "script", "entrypoint_path": "x.py"}
    out = task_cli._resolve_profile_definition(
        task_id="trace_ping",
        registered_profiles={"trace_ping": profile},
    )
    assert out == profile


def test_resolve_profile_falls_back_to_local_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = tmp_path / "repo"
    entrypoint = repo_root / "src" / "zubot" / "predefined_tasks" / "trace_ping" / "task.py"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(task_cli, "_repo_root", lambda: repo_root)

    out = task_cli._resolve_profile_definition(
        task_id="trace_ping",
        registered_profiles={},
    )
    assert isinstance(out, dict)
    assert out["entrypoint_path"] == "src/zubot/predefined_tasks/trace_ping/task.py"
    assert out["resources_path"] == "src/zubot/predefined_tasks/trace_ping"


def test_main_run_executes_runner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(task_cli, "_load_registered_profiles", lambda: {"trace_ping": {"task_id": "trace_ping", "kind": "script"}})
    monkeypatch.setattr(task_cli, "_ensure_profile_registered", lambda profile: None)

    called: dict[str, object] = {}

    class _FakeRunner:
        def run_profile(self, **kwargs):
            called.update(kwargs)
            return {"ok": True, "status": "done", "summary": "ok"}

    monkeypatch.setattr(task_cli, "TaskAgentRunner", _FakeRunner)
    rc = task_cli.main(["run", "trace_ping", "--payload-json", '{"trigger":"manual"}'])
    assert rc == 0
    assert called["profile_id"] == "trace_ping"
