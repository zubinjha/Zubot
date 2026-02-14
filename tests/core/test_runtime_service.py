from __future__ import annotations

from typing import Any

from src.zubot.runtime.service import RuntimeService


class _FakeCentral:
    def __init__(self, *, enabled: bool = False, running: bool = False) -> None:
        self.enabled = enabled
        self.running = running
        self.start_calls = 0
        self.stop_calls = 0

    def status(self) -> dict[str, Any]:
        return {"ok": True, "service": {"enabled_in_config": self.enabled, "running": self.running}}

    def start(self) -> dict[str, Any]:
        self.start_calls += 1
        self.running = True
        return {"ok": True, "running": True}

    def stop(self) -> dict[str, Any]:
        self.stop_calls += 1
        self.running = False
        return {"ok": True, "running": False}

    def list_schedules(self) -> dict[str, Any]:
        return {"ok": True, "schedules": []}

    def list_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "runs": [], "limit": limit}

    def metrics(self) -> dict[str, Any]:
        return {"ok": True, "runtime": {"queued_count": 0}}

    def list_defined_tasks(self) -> dict[str, Any]:
        return {"ok": True, "tasks": [{"task_id": "task_a"}]}

    def upsert_schedule(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "schedule_id": kwargs.get("schedule_id") or "sched_new"}

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return {"ok": True, "schedule_id": schedule_id, "deleted": 1}

    def trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        return {"ok": True, "profile_id": profile_id, "description": description}

    def kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return {"ok": True, "run_id": run_id, "requested_by": requested_by}


class _FakeMemoryWorker:
    def start(self) -> dict[str, Any]:
        return {"ok": True}

    def stop(self) -> dict[str, Any]:
        return {"ok": True}

    def kick(self) -> dict[str, Any]:
        return {"ok": True}


def test_runtime_service_start_respects_central_enable_flag(monkeypatch):
    central = _FakeCentral(enabled=True, running=False)
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=True, source="daemon")
    assert out["ok"] is True
    assert out["central_started"] is True
    assert central.start_calls == 1


def test_runtime_service_start_in_client_mode_does_not_start_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=False)
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=False, source="app")
    assert out["ok"] is True
    assert out["central_started"] is False
    assert central.start_calls == 0


def test_runtime_service_stop_stops_running_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=True)
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.stop(source="daemon")
    assert out["ok"] is True
    assert out["central_stopped"] is True
    assert central.stop_calls == 1


def test_runtime_service_delegates_chat_module(monkeypatch):
    class _FakeChat:
        @staticmethod
        def handle_chat_message(message: str, *, allow_llm_fallback: bool, session_id: str) -> dict[str, Any]:
            return {
                "ok": True,
                "reply": f"chat:{message}",
                "allow_llm_fallback": allow_llm_fallback,
                "session_id": session_id,
            }

        @staticmethod
        def initialize_session_context(session_id: str) -> dict[str, Any]:
            return {"ok": True, "initialized": True, "session_id": session_id}

        @staticmethod
        def reset_session_context(session_id: str) -> dict[str, Any]:
            return {"ok": True, "reset": True, "session_id": session_id}

    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: _FakeCentral())
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())
    monkeypatch.setattr(RuntimeService, "_chat_logic_module", staticmethod(lambda: _FakeChat))

    svc = RuntimeService()
    chat = svc.chat(message="hello", session_id="s1")
    assert chat["ok"] is True
    assert chat["reply"] == "chat:hello"
    init = svc.init_session(session_id="s1")
    assert init["initialized"] is True
    reset = svc.reset_session(session_id="s1")
    assert reset["reset"] is True


def test_runtime_service_central_schedule_crud(monkeypatch):
    central = _FakeCentral()
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    tasks = svc.central_list_defined_tasks()
    assert tasks["ok"] is True
    assert tasks["tasks"][0]["task_id"] == "task_a"

    upsert = svc.central_upsert_schedule(
        schedule_id=None,
        task_id="task_a",
        enabled=True,
        mode="frequency",
        execution_order=100,
        run_frequency_minutes=60,
    )
    assert upsert["ok"] is True

    deleted = svc.central_delete_schedule(schedule_id="sched_x")
    assert deleted["ok"] is True
    assert deleted["deleted"] == 1


def test_runtime_service_health_reports_task_runtime(monkeypatch):
    central = _FakeCentral()
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.health()
    assert out["ok"] is True
    assert "task_runtime" in out


def test_runtime_service_delegates_run_kill(monkeypatch):
    central = _FakeCentral()
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())
    svc = RuntimeService()
    out = svc.central_kill_run(run_id="run_1", requested_by="ui")
    assert out["ok"] is True
    assert out["run_id"] == "run_1"
