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


def test_control_panel_delegates_to_central(monkeypatch):
    monkeypatch.setattr("src.zubot.core.control_panel.get_central_service", lambda: _FakeCentral())
    panel = ControlPanel()
    assert panel.status()["ok"] is True
    assert panel.start()["running"] is True
    assert panel.stop()["running"] is False
    assert panel.enqueue_task(task_id="task_a")["profile_id"] == "task_a"
    assert panel.enqueue_agentic_task(task_name="Research", instructions="Research X")["run_id"] == "trun_agentic_1"
    assert panel.kill_run(run_id="run_1")["run_id"] == "run_1"
    assert panel.execute_sql(sql="SELECT 1 AS ok;")["rows"][0]["ok"] == 1

