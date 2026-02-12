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
