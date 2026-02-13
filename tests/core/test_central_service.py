import json
import time
from pathlib import Path
from threading import Event, Lock

import pytest

from src.zubot.core.central_service import CentralService, summarize_task_agent_check_in
from src.zubot.core.config_loader import clear_config_cache


@pytest.fixture()
def configured_central(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "zubot_core.db"
    cfg_path.write_text(
        json.dumps(
            {
                "central_service": {
                    "enabled": False,
                    "poll_interval_sec": 1,
                    "task_runner_concurrency": 2,
                    "scheduler_db_path": str(db_path),
                    "worker_slot_reserve_for_workers": 2,
                    "queue_warning_threshold": 1,
                    "running_age_warning_sec": 0,
                },
                "task_agents": {
                    "profiles": {
                        "profile_a": {
                            "name": "Profile A",
                            "instructions_template": "Do profile A work",
                            "model_alias": "medium",
                            "tool_access": [],
                            "skill_access": [],
                        }
                    },
                    "schedules": [
                        {
                            "schedule_id": "sched_a",
                            "profile_id": "profile_a",
                            "enabled": True,
                            "run_frequency_minutes": 999999,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(cfg_path))
    clear_config_cache()
    return {"db_path": db_path}


def test_central_service_status_has_checkin_payload(configured_central):
    service = CentralService()
    out = service.status()
    assert out["ok"] is True
    assert out["service"]["enabled_in_config"] is False
    assert isinstance(out["task_agents"], list)
    assert out["task_agents"][0]["profile_id"] == "profile_a"


def test_trigger_profile_runs_and_updates_last_result(configured_central, monkeypatch: pytest.MonkeyPatch):
    service = CentralService()
    emitted: list[dict] = []
    import src.zubot.core.central_service as central_service_module

    # capture task-agent memory ingestion events without writing local files
    monkeypatch.setattr(
        central_service_module,
        "append_daily_memory_entry",
        lambda **kwargs: emitted.append(kwargs) or {"ok": True},
    )
    service._runner = type(  # noqa: SLF001
        "_FakeRunner",
        (),
        {
            "describe_run": staticmethod(lambda *, profile_id, payload=None: f"{profile_id}: fake run"),
            "run_profile": staticmethod(
                lambda *, profile_id, payload=None: {
                    "ok": True,
                    "status": "done",
                    "summary": f"{profile_id} done",
                    "error": None,
                    "current_description": f"{profile_id}: fake run",
                }
            ),
        },
    )()

    trigger = service.trigger_profile(profile_id="profile_a", description="manual")
    assert trigger["ok"] is True

    deadline = time.time() + 2.0
    status = None
    while time.time() < deadline:
        runs = service.list_runs(limit=10)["runs"]
        if runs and runs[0]["status"] in {"done", "failed", "blocked"}:
            status = runs[0]["status"]
            break
        time.sleep(0.05)

    assert status == "done"

    check_in = service.status()["task_agents"]
    profile = [item for item in check_in if item["profile_id"] == "profile_a"][0]
    assert profile["last_result"] is not None
    assert profile["last_result"]["status"] == "done"
    assert any(item.get("kind") == "task_agent_event" for item in emitted)


def test_start_stop_lifecycle(configured_central):
    service = CentralService()
    started = service.start()
    assert started["ok"] is True

    running = service.status()["service"]["running"]
    assert running is True

    stopped = service.stop()
    assert stopped["ok"] is True
    assert service.status()["service"]["running"] is False


def test_list_schedules_syncs_from_config(configured_central):
    service = CentralService()
    out = service.list_schedules()
    assert out["ok"] is True
    assert out["schedules"][0]["schedule_id"] == "sched_a"


def test_central_service_concurrency_respects_setting(configured_central):
    service = CentralService()
    gate = Event()
    active = {"count": 0, "peak": 0}
    guard = Lock()

    class _BlockingRunner:
        @staticmethod
        def describe_run(*, profile_id, payload=None):
            return f"{profile_id}: blocked run"

        @staticmethod
        def run_profile(*, profile_id, payload=None):
            with guard:
                active["count"] += 1
                active["peak"] = max(active["peak"], active["count"])
            gate.wait(timeout=2.0)
            with guard:
                active["count"] -= 1
            return {
                "ok": True,
                "status": "done",
                "summary": "ok",
                "error": None,
                "current_description": "done",
            }

    service._runner = _BlockingRunner()  # noqa: SLF001

    service.trigger_profile(profile_id="profile_a", description="r1")
    service.trigger_profile(profile_id="profile_a", description="r2")
    service.trigger_profile(profile_id="profile_a", description="r3")

    deadline = time.time() + 1.5
    observed = None
    while time.time() < deadline:
        runtime = service.status()["runtime"]
        if runtime["running_count"] == 2 and runtime["queued_count"] >= 1:
            observed = runtime
            break
        time.sleep(0.02)

    assert observed is not None
    assert active["peak"] <= 2
    gate.set()


def test_summarize_task_agent_check_in_text():
    out = summarize_task_agent_check_in(
        [
            {
                "profile_id": "profile_a",
                "name": "Profile A",
                "state": "running",
                "current_description": "Profile A: running search job.",
                "queue_position": None,
                "last_result": None,
            },
            {
                "profile_id": "profile_b",
                "name": "Profile B",
                "state": "free",
                "current_description": None,
                "queue_position": None,
                "last_result": {"status": "done", "summary": "ok", "error": None},
            },
        ]
    )
    assert "Profile A: running; Profile A: running search job." in out
    assert "Profile B: free; last result done" in out


def test_metrics_include_recent_events(configured_central):
    service = CentralService()
    service._runner = type(  # noqa: SLF001
        "_FakeRunner",
        (),
        {
            "describe_run": staticmethod(lambda *, profile_id, payload=None: f"{profile_id}: fake run"),
            "run_profile": staticmethod(
                lambda *, profile_id, payload=None: {
                    "ok": True,
                    "status": "done",
                    "summary": f"{profile_id} done",
                    "error": None,
                    "current_description": f"{profile_id}: fake run",
                }
            ),
        },
    )()
    trigger = service.trigger_profile(profile_id="profile_a", description="manual")
    assert trigger["ok"] is True

    deadline = time.time() + 2.0
    while time.time() < deadline:
        runs = service.list_runs(limit=10)["runs"]
        if runs and runs[0]["status"] == "done":
            break
        time.sleep(0.05)

    metrics = service.metrics()
    assert metrics["ok"] is True
    assert isinstance(metrics.get("recent_events"), list)
    assert any(event.get("type") == "task_agent_event" for event in metrics["recent_events"])


def test_status_emits_queue_pressure_warning(configured_central):
    service = CentralService()
    gate = Event()

    class _BlockingRunner:
        @staticmethod
        def describe_run(*, profile_id, payload=None):
            return f"{profile_id}: blocked"

        @staticmethod
        def run_profile(*, profile_id, payload=None):
            gate.wait(timeout=2.0)
            return {
                "ok": True,
                "status": "done",
                "summary": "ok",
                "error": None,
                "current_description": "done",
            }

    service._runner = _BlockingRunner()  # noqa: SLF001
    service.trigger_profile(profile_id="profile_a", description="r1")
    service.trigger_profile(profile_id="profile_a", description="r2")
    service.trigger_profile(profile_id="profile_a", description="r3")

    deadline = time.time() + 1.5
    warned = False
    while time.time() < deadline:
        status = service.status()
        warnings = status.get("runtime", {}).get("warnings", [])
        if "queue_depth_high" in warnings:
            warned = True
            break
        time.sleep(0.02)

    gate.set()
    assert warned is True
