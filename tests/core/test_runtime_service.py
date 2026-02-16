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

    def upsert_task_profile(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "task_id": kwargs.get("task_id") or "task_new"}

    def delete_task_profile(self, *, task_id: str) -> dict[str, Any]:
        return {"ok": True, "task_id": task_id, "deleted": 1}

    def upsert_schedule(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "schedule_id": kwargs.get("schedule_id") or "sched_new"}

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        return {"ok": True, "schedule_id": schedule_id, "deleted": 1}

    def trigger_profile(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        return {"ok": True, "profile_id": profile_id, "description": description}

    def enqueue_task(self, *, task_id: str, description: str | None = None) -> dict[str, Any]:
        return {"ok": True, "profile_id": task_id, "description": description}

    def enqueue_agentic_task(
        self,
        *,
        task_name: str,
        instructions: str,
        requested_by: str = "main_agent",
        model_tier: str = "medium",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        timeout_sec: int = 180,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "run_id": "trun_agentic_1",
            "task_name": task_name,
            "instructions": instructions,
            "requested_by": requested_by,
            "model_tier": model_tier,
            "tool_access": tool_access or [],
            "skill_access": skill_access or [],
            "timeout_sec": timeout_sec,
            "metadata": metadata or {},
        }

    def kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return {"ok": True, "run_id": run_id, "requested_by": requested_by}

    def list_waiting_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "runs": [{"run_id": "run_wait_1"}], "count": 1, "limit": limit}

    def resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return {
            "ok": True,
            "run_id": run_id,
            "user_response": user_response,
            "requested_by": requested_by,
            "resumed": True,
        }

    def execute_sql(
        self,
        *,
        sql: str,
        params: Any = None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int | None = None,
        ) -> dict[str, Any]:
        return {
            "ok": True,
            "sql": sql,
            "params": params,
            "read_only": read_only,
            "timeout_sec": timeout_sec,
            "max_rows": max_rows,
            "rows": [{"ok": 1}],
        }

    def upsert_task_state(
        self,
        *,
        task_id: str,
        state_key: str,
        value: dict[str, Any],
        updated_by: str = "task_runtime",
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "task_id": task_id,
            "state_key": state_key,
            "value": value,
            "updated_by": updated_by,
        }

    def get_task_state(self, *, task_id: str, state_key: str) -> dict[str, Any]:
        return {"ok": True, "task_id": task_id, "state_key": state_key, "value": {"v": 1}}

    def mark_task_item_seen(
        self,
        *,
        task_id: str,
        provider: str,
        item_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "task_id": task_id,
            "provider": provider,
            "item_key": item_key,
            "metadata": metadata or {},
        }

    def has_task_item_seen(self, *, task_id: str, provider: str, item_key: str) -> dict[str, Any]:
        return {"ok": True, "seen": True, "seen_count": 2}


class _FakeMemoryWorker:
    def start(self) -> dict[str, Any]:
        return {"ok": True}

    def stop(self) -> dict[str, Any]:
        return {"ok": True}

    def kick(self) -> dict[str, Any]:
        return {"ok": True}


def test_runtime_service_start_respects_central_enable_flag(monkeypatch):
    central = _FakeCentral(enabled=True, running=False)
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=True, source="daemon")
    assert out["ok"] is True
    assert out["central_started"] is True
    assert central.start_calls == 1


def test_runtime_service_start_in_client_mode_does_not_start_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=False)
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.start(start_central_if_enabled=False, source="app")
    assert out["ok"] is True
    assert out["central_started"] is False
    assert central.start_calls == 0


def test_runtime_service_stop_stops_running_central(monkeypatch):
    central = _FakeCentral(enabled=True, running=True)
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
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

    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: _FakeCentral())
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
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    tasks = svc.central_list_defined_tasks()
    assert tasks["ok"] is True
    assert tasks["tasks"][0]["task_id"] == "task_a"

    upsert_task = svc.central_upsert_task_profile(task_id="task_new", name="Task New")
    assert upsert_task["ok"] is True
    assert upsert_task["task_id"] == "task_new"

    delete_task = svc.central_delete_task_profile(task_id="task_new")
    assert delete_task["ok"] is True
    assert delete_task["deleted"] == 1

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
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())

    svc = RuntimeService()
    out = svc.health()
    assert out["ok"] is True
    assert "task_runtime" in out


def test_runtime_service_delegates_run_kill(monkeypatch):
    central = _FakeCentral()
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())
    svc = RuntimeService()
    out = svc.central_kill_run(run_id="run_1", requested_by="ui")
    assert out["ok"] is True
    assert out["run_id"] == "run_1"


def test_runtime_service_delegates_agentic_enqueue_and_sql(monkeypatch):
    central = _FakeCentral()
    monkeypatch.setattr("src.zubot.runtime.service.get_control_panel", lambda: central)
    monkeypatch.setattr("src.zubot.runtime.service.get_memory_summary_worker", lambda: _FakeMemoryWorker())
    svc = RuntimeService()
    enq = svc.central_enqueue_agentic_task(
        task_name="Research",
        instructions="Research X",
        requested_by="ui",
        model_tier="medium",
        tool_access=[],
        skill_access=[],
        timeout_sec=90,
        metadata={"source": "test"},
    )
    assert enq["ok"] is True
    assert enq["run_id"] == "trun_agentic_1"

    sql = svc.central_execute_sql(sql="SELECT 1 AS ok;", read_only=True, max_rows=5)
    assert sql["ok"] is True
    assert sql["rows"][0]["ok"] == 1

    waiting = svc.central_waiting_runs(limit=5)
    assert waiting["ok"] is True
    assert waiting["runs"][0]["run_id"] == "run_wait_1"

    resume = svc.central_resume_run(run_id="run_wait_1", user_response="continue", requested_by="ui")
    assert resume["ok"] is True
    assert resume["resumed"] is True

    upsert_state = svc.central_upsert_task_state(task_id="t1", state_key="cursor", value={"x": 1}, updated_by="ui")
    assert upsert_state["ok"] is True

    get_state = svc.central_get_task_state(task_id="t1", state_key="cursor")
    assert get_state["ok"] is True
    assert get_state["value"]["v"] == 1

    mark_seen = svc.central_mark_task_item_seen(task_id="t1", provider="indeed", item_key="job1", metadata={"a": 1})
    assert mark_seen["ok"] is True

    has_seen = svc.central_has_task_item_seen(task_id="t1", provider="indeed", item_key="job1")
    assert has_seen["ok"] is True
    assert has_seen["seen"] is True
