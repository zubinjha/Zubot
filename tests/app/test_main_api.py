import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_session_init_endpoint():
    res = client.post("/api/session/init", json={"session_id": "api-init"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["initialized"] is True


def test_session_reset_endpoint():
    res = client.post("/api/session/reset", json={"session_id": "api-reset"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["reset"] is True


class _FakeWorkerManager:
    def spawn_worker(self, **kwargs):
        return {"ok": True, "worker": {"worker_id": "worker_test", "title": kwargs.get("title"), "status": "queued"}}

    def cancel_worker(self, worker_id: str):
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "cancelled"}}

    def reset_worker_context(self, worker_id: str):
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "done"}}

    def message_worker(self, worker_id: str, message: str, model_tier: str = "medium"):
        return {
            "ok": True,
            "worker": {"worker_id": worker_id, "status": "queued"},
            "message": message,
            "model_tier": model_tier,
        }

    def get_worker(self, worker_id: str):
        return {"ok": True, "worker": {"worker_id": worker_id, "status": "done"}}

    def list_workers(self):
        return {"ok": True, "workers": [], "runtime": {"running_count": 0, "queued_count": 0}}


def test_worker_endpoints(monkeypatch):
    monkeypatch.setattr("app.main.get_worker_manager", lambda: _FakeWorkerManager())

    spawn = client.post(
        "/api/workers/spawn",
        json={"title": "Research", "instructions": "Look up X", "model_tier": "low"},
    )
    assert spawn.status_code == 200
    assert spawn.json()["ok"] is True
    worker_id = spawn.json()["worker"]["worker_id"]

    listed = client.get("/api/workers")
    assert listed.status_code == 200
    assert listed.json()["ok"] is True

    got = client.get(f"/api/workers/{worker_id}")
    assert got.status_code == 200
    assert got.json()["ok"] is True

    msg = client.post(f"/api/workers/{worker_id}/message", json={"message": "continue"})
    assert msg.status_code == 200
    assert msg.json()["ok"] is True

    reset = client.post(f"/api/workers/{worker_id}/reset-context")
    assert reset.status_code == 200
    assert reset.json()["ok"] is True

    cancel = client.post(f"/api/workers/{worker_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["ok"] is True
