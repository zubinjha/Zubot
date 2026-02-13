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

    def trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        return {"ok": True, "profile_id": profile_id, "description": description}


class _FakeWorker:
    def spawn_worker(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "worker": {"worker_id": "w1", "title": kwargs.get("title", "")}}

    def cancel_worker(self, worker_id: str) -> dict[str, Any]:
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "cancelled"}}

    def reset_worker_context(self, worker_id: str) -> dict[str, Any]:
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "done"}}

    def message_worker(self, *, worker_id: str, message: str, model_tier: str = "medium") -> dict[str, Any]:
        return {"ok": True, "worker": {"worker_id": worker_id}, "message": message, "model_tier": model_tier}

    def get_worker(self, worker_id: str) -> dict[str, Any]:
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "done"}}

    def list_workers(self) -> dict[str, Any]:
        return {"ok": True, "workers": [], "runtime": {"running_count": 0, "queued_count": 0}}


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
    monkeypatch.setattr("src.zubot.runtime.service.get_worker_manager", lambda: _FakeWorker())
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=True, source="daemon")
    assert out["ok"] is True
    assert out["central_started"] is True
    assert central.start_calls == 1


def test_runtime_service_start_in_client_mode_does_not_start_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=False)
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_worker_manager", lambda: _FakeWorker())
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=False, source="app")
    assert out["ok"] is True
    assert out["central_started"] is False
    assert central.start_calls == 0


def test_runtime_service_stop_stops_running_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=True)
    monkeypatch.setattr("src.zubot.runtime.service.get_central_service", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_worker_manager", lambda: _FakeWorker())
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
    monkeypatch.setattr("src.zubot.runtime.service.get_worker_manager", lambda: _FakeWorker())
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
