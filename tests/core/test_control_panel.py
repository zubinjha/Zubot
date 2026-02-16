from __future__ import annotations

from typing import Any

from src.zubot.core.control_panel import ControlPanel


class _FakeCentral:
    def status(self) -> dict[str, Any]:
        return {"ok": True, "service": {"running": False}}

    def start(self) -> dict[str, Any]:
        return {"ok": True, "running": True}

    def stop(self) -> dict[str, Any]:
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

    def enqueue_agentic_task(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "run_id": "trun_agentic_1", **kwargs}

    def kill_run(self, *, run_id: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return {"ok": True, "run_id": run_id, "requested_by": requested_by}

    def execute_sql(self, **kwargs) -> dict[str, Any]:
        return {"ok": True, "rows": [{"ok": 1}], **kwargs}

    def list_waiting_runs(self, *, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "runs": [{"run_id": "run_wait_1"}], "count": 1, "limit": limit}

    def resume_run(self, *, run_id: str, user_response: str, requested_by: str = "main_agent") -> dict[str, Any]:
        return {"ok": True, "run_id": run_id, "user_response": user_response, "requested_by": requested_by}

    def upsert_task_state(self, *, task_id: str, state_key: str, value: dict, updated_by: str = "task_runtime") -> dict[str, Any]:
        return {"ok": True, "task_id": task_id, "state_key": state_key, "value": value, "updated_by": updated_by}

    def get_task_state(self, *, task_id: str, state_key: str) -> dict[str, Any]:
        return {"ok": True, "task_id": task_id, "state_key": state_key, "value": {"cursor": 1}}

    def mark_task_item_seen(self, *, task_id: str, provider: str, item_key: str, metadata: dict | None = None) -> dict[str, Any]:
        return {"ok": True, "task_id": task_id, "provider": provider, "item_key": item_key, "metadata": metadata or {}}

    def has_task_item_seen(self, *, task_id: str, provider: str, item_key: str) -> dict[str, Any]:
        return {"ok": True, "seen": True, "seen_count": 1}


def test_control_panel_delegates_to_central(monkeypatch):
    monkeypatch.setattr("src.zubot.core.control_panel.get_central_service", lambda: _FakeCentral())
    panel = ControlPanel()
    assert panel.status()["ok"] is True
    assert panel.start()["running"] is True
    assert panel.stop()["running"] is False
    assert panel.enqueue_task(task_id="task_a")["profile_id"] == "task_a"
    assert panel.upsert_task_profile(task_id="task_new")["task_id"] == "task_new"
    assert panel.delete_task_profile(task_id="task_new")["deleted"] == 1
    assert panel.enqueue_agentic_task(task_name="Research", instructions="Research X")["run_id"] == "trun_agentic_1"
    assert panel.kill_run(run_id="run_1")["run_id"] == "run_1"
    assert panel.list_waiting_runs(limit=5)["count"] == 1
    assert panel.resume_run(run_id="run_wait_1", user_response="continue")["ok"] is True
    assert panel.execute_sql(sql="SELECT 1 AS ok;")["rows"][0]["ok"] == 1
    assert panel.upsert_task_state(task_id="task_a", state_key="cursor", value={"x": 1})["ok"] is True
    assert panel.get_task_state(task_id="task_a", state_key="cursor")["value"]["cursor"] == 1
    assert panel.mark_task_item_seen(task_id="task_a", provider="indeed", item_key="job_1")["ok"] is True
    assert panel.has_task_item_seen(task_id="task_a", provider="indeed", item_key="job_1")["seen"] is True
