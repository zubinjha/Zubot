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

    def restart_session_context(self, *, session_id: str = "default", history_limit: int | None = None):
        return {
            "ok": True,
            "restarted": True,
            "session_id": session_id,
            "history_limit": history_limit,
        }

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

    def session_history(self, *, session_id: str = "default", limit: int = 100):
        return {
            "ok": True,
            "session_id": session_id,
            "limit": limit,
            "entries": [
                {"event_id": 1, "event_time": "2026-02-16T00:00:00+00:00", "role": "user", "content": "hello"},
                {"event_id": 2, "event_time": "2026-02-16T00:00:01+00:00", "role": "assistant", "content": "hi"},
            ],
        }

    def clear_session_history(self, *, session_id: str = "default"):
        return {"ok": True, "session_id": session_id, "deleted_chat_messages": 2}

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

    def central_upsert_task_profile(
        self,
        *,
        task_id: str,
        name: str | None = None,
        kind: str = "script",
        entrypoint_path: str | None = None,
        module: str | None = None,
        resources_path: str | None = None,
        queue_group: str | None = None,
        timeout_sec: int | None = None,
        retry_policy: dict | None = None,
        enabled: bool = True,
        source: str = "ui",
    ):
        return {
            "ok": True,
            "task_id": task_id,
            "name": name or task_id,
            "kind": kind,
            "entrypoint_path": entrypoint_path,
            "module": module,
            "resources_path": resources_path,
            "queue_group": queue_group,
            "timeout_sec": timeout_sec,
            "retry_policy": retry_policy or {},
            "enabled": enabled,
            "source": source,
        }

    def central_delete_task_profile(self, *, task_id: str):
        return {"ok": True, "task_id": task_id, "deleted": 1}

    def central_upsert_schedule(
        self,
        *,
        schedule_id: str | None,
        task_id: str,
        enabled: bool,
        mode: str,
        execution_order: int,
        misfire_policy: str = "queue_latest",
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
            "misfire_policy": misfire_policy,
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

    def central_waiting_runs(self, *, limit: int = 50):
        return {"ok": True, "runs": [{"run_id": "run_wait_1"}], "count": 1, "limit": limit}

    def central_resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent"):
        return {
            "ok": True,
            "run_id": run_id,
            "user_response": user_response,
            "requested_by": requested_by,
            "resumed": True,
        }

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

    def central_upsert_task_state(self, *, task_id: str, state_key: str, value: dict, updated_by: str = "task_runtime"):
        return {"ok": True, "task_id": task_id, "state_key": state_key, "value": value, "updated_by": updated_by}

    def central_get_task_state(self, *, task_id: str, state_key: str):
        return {"ok": True, "task_id": task_id, "state_key": state_key, "value": {"v": 1}}

    def central_mark_task_item_seen(self, *, task_id: str, provider: str, item_key: str, metadata: dict | None = None):
        return {"ok": True, "task_id": task_id, "provider": provider, "item_key": item_key, "metadata": metadata or {}}

    def central_has_task_item_seen(self, *, task_id: str, provider: str, item_key: str):
        return {"ok": True, "seen": True, "seen_count": 1}


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


def test_session_restart_context_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/session/restart_context", json={"session_id": "api-restart", "history_limit": 42})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["restarted"] is True
    assert body["session_id"] == "api-restart"
    assert body["history_limit"] == 42


def test_session_context_endpoint(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    res = client.post("/api/session/context", json={"session_id": "ctx-1"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["session_id"] == "ctx-1"
    assert body["snapshot"]["session_id"] == "ctx-1"

    hist = client.get("/api/session/history?session_id=ctx-1&limit=5")
    assert hist.status_code == 200
    hbody = hist.json()
    assert hbody["ok"] is True
    assert hbody["session_id"] == "ctx-1"
    assert len(hbody["entries"]) == 2

    cleared = client.post("/api/session/history/clear", json={"session_id": "ctx-1"})
    assert cleared.status_code == 200
    cbody = cleared.json()
    assert cbody["ok"] is True
    assert cbody["session_id"] == "ctx-1"


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

    upsert_task = client.post(
        "/api/central/tasks",
        json={
            "task_id": "task_new",
            "name": "Task New",
            "kind": "script",
            "entrypoint_path": "src/zubot/tasks/task_new/task.py",
            "timeout_sec": 120,
            "enabled": True,
        },
    )
    assert upsert_task.status_code == 200
    assert upsert_task.json()["ok"] is True
    assert upsert_task.json()["task_id"] == "task_new"

    delete_task = client.delete("/api/central/tasks/task_new")
    assert delete_task.status_code == 200
    assert delete_task.json()["ok"] is True

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

    waiting = client.get("/api/central/runs/waiting?limit=5")
    assert waiting.status_code == 200
    assert waiting.json()["ok"] is True
    assert waiting.json()["runs"][0]["run_id"] == "run_wait_1"

    resumed = client.post("/api/central/runs/run_wait_1/resume", json={"user_response": "continue", "requested_by": "ui"})
    assert resumed.status_code == 200
    assert resumed.json()["ok"] is True
    assert resumed.json()["resumed"] is True

    sql = client.post("/api/central/sql", json={"sql": "SELECT 1 AS ok;", "read_only": True, "max_rows": 10})
    assert sql.status_code == 200
    assert sql.json()["ok"] is True
    assert sql.json()["row_count"] == 1

    state_upsert = client.post(
        "/api/central/task-state/upsert",
        json={"task_id": "t1", "state_key": "cursor", "value": {"x": 1}, "updated_by": "ui"},
    )
    assert state_upsert.status_code == 200
    assert state_upsert.json()["ok"] is True

    state_get = client.post("/api/central/task-state/get", json={"task_id": "t1", "state_key": "cursor"})
    assert state_get.status_code == 200
    assert state_get.json()["ok"] is True
    assert state_get.json()["value"]["v"] == 1

    seen_mark = client.post(
        "/api/central/task-seen/mark",
        json={"task_id": "t1", "provider": "indeed", "item_key": "job_1", "metadata": {"title": "SE"}},
    )
    assert seen_mark.status_code == 200
    assert seen_mark.json()["ok"] is True

    seen_has = client.post("/api/central/task-seen/has", json={"task_id": "t1", "provider": "indeed", "item_key": "job_1"})
    assert seen_has.status_code == 200
    assert seen_has.json()["ok"] is True
    assert seen_has.json()["seen"] is True

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


def test_control_approval_flow(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    action_text = (
        "Need approval to run task.\n"
        "[ZUBOT_CONTROL_REQUEST]\n"
        '{"action_id":"act_approve_1","action":"enqueue_task","title":"Run task","risk_level":"high","payload":{"task_id":"task_a"}}\n'
        "[/ZUBOT_CONTROL_REQUEST]"
    )
    ingested = client.post(
        "/api/control/ingest",
        json={"session_id": "default", "assistant_text": action_text, "route": "llm.main_agent"},
    )
    assert ingested.status_code == 200
    ibody = ingested.json()
    assert ibody["ok"] is True
    assert ibody["count"] == 1

    pending = client.get("/api/control/pending?session_id=default")
    assert pending.status_code == 200
    pbody = pending.json()
    assert pbody["ok"] is True
    assert pbody["count"] == 1
    assert pbody["pending"][0]["action_id"] == "act_approve_1"

    approved = client.post("/api/control/approve", json={"action_id": "act_approve_1", "approved_by": "tester"})
    assert approved.status_code == 200
    abody = approved.json()
    assert abody["ok"] is True
    assert abody["action"]["status"] == "approved"
    assert abody["execution"]["result"]["ok"] is True

    pending_after = client.get("/api/control/pending?session_id=default")
    assert pending_after.status_code == 200
    assert pending_after.json()["count"] == 0


def test_control_deny_flow(monkeypatch):
    monkeypatch.setattr("app.main.get_runtime_service", lambda: _FakeRuntimeService())
    action_text = (
        "Need approval to stop run.\n"
        "[ZUBOT_CONTROL_REQUEST]\n"
        '{"action_id":"act_deny_1","action":"kill_task_run","title":"Kill stuck run","risk_level":"high","payload":{"run_id":"run_x"}}\n'
        "[/ZUBOT_CONTROL_REQUEST]"
    )
    ingested = client.post(
        "/api/control/ingest",
        json={"session_id": "default", "assistant_text": action_text, "route": "llm.main_agent"},
    )
    assert ingested.status_code == 200
    assert ingested.json()["ok"] is True

    denied = client.post("/api/control/deny", json={"action_id": "act_deny_1", "denied_by": "tester", "reason": "not_now"})
    assert denied.status_code == 200
    dbody = denied.json()
    assert dbody["ok"] is True
    assert dbody["action"]["status"] == "denied"

    pending = client.get("/api/control/pending?session_id=default")
    assert pending.status_code == 200
    assert pending.json()["count"] == 0
