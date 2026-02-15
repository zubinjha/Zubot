import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class _FakeRuntimeService:
    def health(self):
        return {
            "ok": True,
            "source": "runtime_service",
            "runtime": {"started": True},
            "central": {"running": False},
            "task_runtime": {"running_count": 0, "queued_count": 0},
        }

    def start(self, **kwargs):
        _ = kwargs
        return {"ok": True}

    def chat(self, *, message: str, session_id: str = "default", allow_llm_fallback: bool = True):
        return {"ok": True, "reply": f"echo:{message}", "session_id": session_id, "allow_llm_fallback": allow_llm_fallback}

    def init_session(self, *, session_id: str = "default"):
        return {"ok": True, "initialized": True, "session_id": session_id}

    def reset_session(self, *, session_id: str = "default"):
        return {"ok": True, "reset": True, "session_id": session_id}

    def session_context_snapshot(self, *, session_id: str = "default"):
        return {
            "ok": True,
            "session_id": session_id,
            "snapshot": {
                "session_id": session_id,
                "user_message": "hello",
                "assembled": {"messages": [{"role": "user", "content": "hello"}]},
            },
        }

    def central_status(self):
        return {
            "ok": True,
            "service": {"running": False, "enabled_in_config": False},
            "runtime": {"queued_count": 0, "running_count": 0},
            "task_agents": [],
        }

    def central_start(self):
        return {"ok": True, "running": True}

    def central_stop(self):
        return {"ok": True, "running": False}

    def central_schedules(self):
        return {"ok": True, "schedules": [{"schedule_id": "sched_1"}]}

    def central_runs(self, *, limit: int = 50):
        return {"ok": True, "runs": [{"run_id": "run_1"}], "limit": limit}

    def central_metrics(self):
        return {"ok": True, "runtime": {"queued_count": 0, "warnings": []}}

    def central_list_defined_tasks(self):
        return {"ok": True, "tasks": [{"task_id": "task_a", "name": "Task A"}]}

    def central_upsert_schedule(
        self,
        *,
        schedule_id: str | None,
        task_id: str,
        enabled: bool,
        mode: str,
        execution_order: int,
        run_frequency_minutes: int | None = None,
        timezone: str | None = None,
        run_times: list[str] | None = None,
        days_of_week: list[str] | None = None,
    ):
        return {
            "ok": True,
            "schedule_id": schedule_id or "sched_new",
            "task_id": task_id,
            "enabled": enabled,
            "mode": mode,
            "execution_order": execution_order,
            "run_frequency_minutes": run_frequency_minutes,
            "timezone": timezone,
            "run_times": run_times or [],
            "days_of_week": days_of_week or [],
        }

    def central_delete_schedule(self, *, schedule_id: str):
        return {"ok": True, "schedule_id": schedule_id, "deleted": 1}

    def central_trigger_profile(self, *, profile_id: str, description: str | None = None):
        return {"ok": True, "profile_id": profile_id, "description": description}

    def central_enqueue_agentic_task(
        self,
        *,
        task_name: str,
        instructions: str,
        requested_by: str = "main_agent",
        model_tier: str = "medium",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        timeout_sec: int = 180,
        metadata: dict | None = None,
    ):
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

    def central_kill_run(self, *, run_id: str, requested_by: str = "main_agent"):
        return {"ok": True, "run_id": run_id, "requested_by": requested_by}

    def central_execute_sql(
        self,
        *,
        sql: str,
        params=None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int | None = None,
    ):
        return {
            "ok": True,
            "sql": sql,
            "params": params,
            "read_only": read_only,
            "timeout_sec": timeout_sec,
            "max_rows": max_rows,
            "rows": [{"ok": 1}],
            "row_count": 1,
        }


def test_health_endpoint_uses_runtime_service(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["source"] == "runtime_service"


def test_chat_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/chat", json={"message": "hello", "session_id": "api-chat"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["reply"] == "echo:hello"


def test_session_init_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/session/init", json={"session_id": "api-init"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["initialized"] is True


def test_session_reset_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/session/reset", json={"session_id": "api-reset"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["reset"] is True


def test_session_context_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/session/context", json={"session_id": "ctx-1"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["session_id"] == "ctx-1"
    assert body["snapshot"]["session_id"] == "ctx-1"


def test_central_endpoints(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())

    status = client.get("/api/central/status")
    assert status.status_code == 200
    assert status.json()["ok"] is True

    start = client.post("/api/central/start")
    assert start.status_code == 200
    assert start.json()["running"] is True

    schedules = client.get("/api/central/schedules")
    assert schedules.status_code == 200
    assert schedules.json()["ok"] is True

    runs = client.get("/api/central/runs?limit=10")
    assert runs.status_code == 200
    assert runs.json()["ok"] is True

    metrics = client.get("/api/central/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["ok"] is True

    tasks = client.get("/api/central/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["ok"] is True

    save_sched = client.post(
        "/api/central/schedules",
        json={
            "task_id": "task_a",
            "enabled": True,
            "mode": "frequency",
            "execution_order": 100,
            "run_frequency_minutes": 60,
        },
    )
    assert save_sched.status_code == 200
    assert save_sched.json()["ok"] is True

    del_sched = client.delete("/api/central/schedules/sched_x")
    assert del_sched.status_code == 200
    assert del_sched.json()["ok"] is True

    trigger = client.post("/api/central/trigger/profile_x", json={"description": "manual"})
    assert trigger.status_code == 200
    assert trigger.json()["profile_id"] == "profile_x"

    agentic = client.post(
        "/api/central/agentic/enqueue",
        json={
            "task_name": "Research",
            "instructions": "Research topic X and summarize.",
            "requested_by": "ui",
            "model_tier": "medium",
            "tool_access": [],
            "skill_access": [],
            "timeout_sec": 120,
            "metadata": {"source": "test"},
        },
    )
    assert agentic.status_code == 200
    assert agentic.json()["ok"] is True
    assert agentic.json()["run_id"] == "trun_agentic_1"

    kill_run = client.post("/api/central/runs/run_x/kill", json={"requested_by": "ui"})
    assert kill_run.status_code == 200
    assert kill_run.json()["run_id"] == "run_x"

    sql = client.post("/api/central/sql", json={"sql": "SELECT 1 AS ok;", "read_only": True, "max_rows": 10})
    assert sql.status_code == 200
    assert sql.json()["ok"] is True
    assert sql.json()["row_count"] == 1

    stop = client.post("/api/central/stop")
    assert stop.status_code == 200
    assert stop.json()["running"] is False


def test_startup_hook_initializes_runtime_client_mode(monkeypatch):
    calls: list[dict] = []

    class _StartupRuntime:
        def start(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}

    monkeypatch.setattr("app.main.get_runtime_service", lambda: _StartupRuntime())
    from app.main import _init_runtime_client

    _init_runtime_client()
    assert len(calls) == 1
    assert calls[0]["start_central_if_enabled"] is False
    assert calls[0]["source"] == "app"
