from __future__ import annotations

import sys
from threading import Event

from src.zubot.daemon.main import main, run_daemon


class _FakeRuntime:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.stops: list[dict] = []

    def start(self, **kwargs):
        self.starts.append(kwargs)
        return {"ok": True}

    def stop(self, **kwargs):
        self.stops.append(kwargs)
        return {"ok": True}


def test_run_daemon_no_app_start_and_stop(monkeypatch):
    fake = _FakeRuntime()
    stop_event = Event()
    stop_event.set()
    monkeypatch.setattr("src.zubot.daemon.main.get_runtime_service", lambda: fake)

    out = run_daemon(with_app=False, stop_event=stop_event)
    assert out == 0
    assert len(fake.starts) == 1
    assert fake.starts[0]["source"] == "daemon"
    assert fake.starts[0]["start_central_if_enabled"] is True
    assert len(fake.stops) == 1
    assert fake.stops[0]["source"] == "daemon"


def test_run_daemon_with_app_invokes_uvicorn(monkeypatch):
    fake = _FakeRuntime()
    calls = {"run": 0}

    class _FakeUvicorn:
        @staticmethod
        def run(*args, **kwargs):
            _ = args, kwargs
            calls["run"] += 1
            return None

    monkeypatch.setattr("src.zubot.daemon.main.get_runtime_service", lambda: fake)
    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)

    out = run_daemon(with_app=True, host="127.0.0.1", port=9000)
    assert out == 0
    assert calls["run"] == 1
    assert len(fake.starts) == 1
    assert len(fake.stops) == 1


def test_main_parses_no_app_args(monkeypatch):
    fake = _FakeRuntime()
    stop_event = Event()
    stop_event.set()
    monkeypatch.setattr("src.zubot.daemon.main.get_runtime_service", lambda: fake)
    monkeypatch.setattr(
        "src.zubot.daemon.main.run_daemon",
        lambda **kwargs: run_daemon(with_app=False, stop_event=stop_event, **{k: v for k, v in kwargs.items() if k != "with_app"}),
    )

    out = main(["--no-app", "--tick-sec", "0.1"])
    assert out == 0
