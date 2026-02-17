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
                "pre_defined_tasks": {
                    "tasks": {
                        "profile_a": {
                            "name": "Profile A",
                            "entrypoint_path": "src/zubot/predefined_tasks/indeed_daily_search/task.py",
                            "args": [],
                            "timeout_sec": 120,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(cfg_path))
    clear_config_cache()
    class _FakeMemoryWorker:
        def start(self):
            return {"ok": True}

        def kick(self):
            return {"ok": True}

        def stop(self):
            return {"ok": True}

    monkeypatch.setattr("src.zubot.core.central_service.get_memory_summary_worker", lambda: _FakeMemoryWorker())
    return {"db_path": db_path}


def test_central_service_status_has_checkin_payload(configured_central):
    service = CentralService()
    out = service.status()
    assert out["ok"] is True
    assert out["service"]["enabled_in_config"] is False
    assert isinstance(out["task_agents"], list)
    assert out["task_agents"][0]["profile_id"] == "profile_a"
    assert isinstance(out["task_slots"], list)
    assert len(out["task_slots"]) == 2


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
                lambda *, profile_id, payload=None, cancel_event=None: {
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


def test_kill_run_cancels_running_task(configured_central):
    service = CentralService()

    class _BlockingRunner:
        @staticmethod
        def describe_run(*, profile_id, payload=None):
            return f"{profile_id}: blocked run"

        @staticmethod
        def run_profile(*, profile_id, payload=None, cancel_event=None):
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return {
                        "ok": False,
                        "status": "blocked",
                        "summary": None,
                        "error": "killed_by_user",
                        "current_description": "killed",
                    }
                time.sleep(0.02)
            return {
                "ok": True,
                "status": "done",
                "summary": "ok",
                "error": None,
                "current_description": "done",
            }

    service._runner = _BlockingRunner()  # noqa: SLF001
    trigger = service.trigger_profile(profile_id="profile_a", description="manual")
    assert trigger["ok"] is True
    run_id = str(trigger["run_id"])

    kill = service.kill_run(run_id=run_id, requested_by="test")
    assert kill["ok"] is True

    deadline = time.time() + 2.0
    terminal_status = None
    while time.time() < deadline:
        runs = service.list_runs(limit=10)["runs"]
        current = next((row for row in runs if row.get("run_id") == run_id), None)
        if current and current.get("status") in {"blocked", "failed", "done"}:
            terminal_status = current.get("status")
            if terminal_status == "blocked":
                break
        time.sleep(0.05)

    assert terminal_status == "blocked"


def test_list_schedules_reads_from_db(configured_central):
    service = CentralService()
    service._store.sync_schedules(  # noqa: SLF001
        [
            {
                "schedule_id": "sched_a",
                "profile_id": "profile_a",
                "enabled": True,
                "run_frequency_minutes": 999999,
            }
        ]
    )
    out = service.list_schedules()
    assert out["ok"] is True
    assert out["schedules"][0]["schedule_id"] == "sched_a"


def test_defined_tasks_and_schedule_crud(configured_central):
    service = CentralService()
    tasks = service.list_defined_tasks()
    assert tasks["ok"] is True
    assert tasks["tasks"][0]["task_id"] == "profile_a"

    create_profile = service.upsert_task_profile(
        task_id="profile_b",
        name="Profile B",
        kind="script",
        entrypoint_path="src/zubot/tasks/profile_b/task.py",
        timeout_sec=60,
        source="test",
    )
    assert create_profile["ok"] is True
    tasks_after = service.list_defined_tasks()
    ids_after = {row["task_id"] for row in tasks_after["tasks"]}
    assert "profile_b" in ids_after

    upsert = service.upsert_schedule(
        schedule_id="sched_crud",
        task_id="profile_a",
        enabled=True,
        mode="calendar",
        execution_order=50,
        timezone="America/New_York",
        run_times=["02:00", "14:00"],
        days_of_week=["mon", "wed"],
    )
    assert upsert["ok"] is True

    listed = service.list_schedules()
    row = [item for item in listed["schedules"] if item["schedule_id"] == "sched_crud"][0]
    assert row["task_id"] == "profile_a"
    assert row["days_of_week"] == ["mon", "wed"]

    deleted = service.delete_schedule(schedule_id="sched_crud")
    assert deleted["ok"] is True
    assert deleted["deleted"] == 1

    deleted_profile = service.delete_task_profile(task_id="profile_b")
    assert deleted_profile["ok"] is True
    assert deleted_profile["deleted"] == 1


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
        def run_profile(*, profile_id, payload=None, cancel_event=None):
            _ = cancel_event
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
    service.stop()


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
                lambda *, profile_id, payload=None, cancel_event=None: {
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
    progress_events = [event for event in metrics["recent_events"] if event.get("type") == "task_agent_event"]
    assert progress_events
    payload = progress_events[-1].get("payload", {})
    assert isinstance(payload, dict)
    assert payload.get("task_id") == "profile_a"
    assert payload.get("run_id")
    assert payload.get("status") in {"queued", "running", "progress", "completed", "failed", "killed"}


def test_status_emits_queue_pressure_warning(configured_central):
    service = CentralService()
    gate = Event()

    class _BlockingRunner:
        @staticmethod
        def describe_run(*, profile_id, payload=None):
            return f"{profile_id}: blocked"

        @staticmethod
        def run_profile(*, profile_id, payload=None, cancel_event=None):
            _ = cancel_event
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
    service.stop()
    assert warned is True


def test_status_includes_active_and_queue_run_views(configured_central):
    service = CentralService()
    service._settings.task_runner_concurrency = 1  # noqa: SLF001
    gate = Event()

    class _BlockingRunner:
        @staticmethod
        def describe_run(*, profile_id, payload=None):
            return f"{profile_id}: blocked"

        @staticmethod
        def run_profile(*, profile_id, payload=None, cancel_event=None):
            _ = cancel_event
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

    deadline = time.time() + 1.5
    observed = None
    while time.time() < deadline:
        runtime = service.status().get("runtime", {})
        if runtime.get("running_count", 0) >= 1 and runtime.get("queued_count", 0) >= 1:
            observed = runtime
            break
        time.sleep(0.02)

    gate.set()
    service.stop()
    assert observed is not None
    assert isinstance(observed.get("active_runs"), list)
    assert isinstance(observed.get("queued_runs_preview"), list)


def test_enqueue_agentic_task_queues_and_runs(configured_central):
    service = CentralService()
    service._runner = type(  # noqa: SLF001
        "_FakeRunner",
        (),
        {
            "describe_run": staticmethod(lambda *, profile_id, payload=None: f"{profile_id}: {payload.get('task_name', 'agentic')}"),
            "run_profile": staticmethod(
                lambda *, profile_id, payload=None, cancel_event=None: {
                    "ok": True,
                    "status": "done",
                    "summary": f"agentic:{payload.get('instructions', '')[:20]}",
                    "error": None,
                    "current_description": "done",
                }
            ),
        },
    )()
    out = service.enqueue_agentic_task(
        task_name="Research",
        instructions="Research XYZ and summarize",
        requested_by="ui",
        model_tier="medium",
        tool_access=[],
        skill_access=[],
        timeout_sec=60,
        metadata={"source": "test"},
    )
    assert out["ok"] is True

    deadline = time.time() + 2.0
    done = None
    while time.time() < deadline:
        runs = service.list_runs(limit=10)["runs"]
        if runs and runs[0]["status"] in {"done", "failed", "blocked"}:
            done = runs[0]
            break
        time.sleep(0.05)

    assert done is not None
    assert done["payload"]["run_kind"] == "agentic"
    assert done["status"] == "done"


def test_execute_sql_uses_db_queue(configured_central):
    service = CentralService()
    out = service.execute_sql(sql="SELECT 1 AS ok;", read_only=True, max_rows=5)
    assert out["ok"] is True
    assert out["source"] == "central_db_queue"
    assert out["rows"][0]["ok"] == 1


def test_waiting_run_resume_flow(configured_central):
    service = CentralService()
    service._runner = type(  # noqa: SLF001
        "_FakeRunner",
        (),
        {
            "describe_run": staticmethod(lambda *, profile_id, payload=None: f"{profile_id}: waiting"),
            "run_profile": staticmethod(
                lambda *, profile_id, payload=None, cancel_event=None: (
                    {
                        "ok": True,
                        "status": "done",
                        "summary": "resumed and completed",
                        "error": None,
                        "current_description": "done",
                    }
                    if payload and payload.get("resume_response")
                    else {
                        "ok": True,
                        "status": "waiting_for_user",
                        "summary": "Need your preference.",
                        "question": "Need your preference.",
                        "error": None,
                        "current_description": "waiting",
                    }
                )
            ),
        },
    )()

    trigger = service.enqueue_agentic_task(
        task_name="Ask User",
        instructions="Ask a follow up",
        requested_by="ui",
        model_tier="medium",
        tool_access=[],
        skill_access=[],
        timeout_sec=60,
        metadata={},
    )
    assert trigger["ok"] is True
    run_id = trigger["run_id"]

    deadline = time.time() + 2.0
    waiting = None
    while time.time() < deadline:
        row = service._store.get_run(run_id=run_id)  # noqa: SLF001
        if isinstance(row, dict) and row.get("status") == "waiting_for_user":
            waiting = row
            break
        time.sleep(0.05)

    assert waiting is not None
    waiting_meta = waiting["payload"]["waiting"]
    assert isinstance(waiting_meta.get("request_id"), str)
    assert waiting_meta.get("request_id", "").startswith("wait_")
    assert waiting_meta.get("question") == "Need your preference."
    assert isinstance(waiting_meta.get("expires_at"), str)
    waiting_runs = service.list_waiting_runs(limit=10)
    assert waiting_runs["ok"] is True
    waiting_row = next((item for item in waiting_runs["runs"] if item.get("run_id") == run_id), None)
    assert waiting_row is not None
    assert waiting_row["request_id"] == waiting_meta["request_id"]
    assert waiting_row["question"] == "Need your preference."

    resumed = service.resume_run(run_id=run_id, user_response="Use option A", requested_by="ui")
    assert resumed["ok"] is True

    deadline_done = time.time() + 2.0
    done = None
    while time.time() < deadline_done:
        row = service._store.get_run(run_id=run_id)  # noqa: SLF001
        if isinstance(row, dict) and row.get("status") in {"done", "failed", "blocked"}:
            done = row
            break
        time.sleep(0.05)
    assert done is not None
    assert done["status"] == "done"
    resumed_events = [
        event
        for event in service.metrics().get("recent_events", [])
        if event.get("type") == "task_agent_event"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("event_type") == "run_resumed"
    ]
    assert resumed_events
    assert resumed_events[-1]["payload"].get("request_id") == waiting_meta["request_id"]


def test_task_state_and_seen_helpers(configured_central):
    service = CentralService()
    upsert = service.upsert_task_state(
        task_id="profile_a",
        state_key="cursor",
        value={"page": 3},
        updated_by="test",
    )
    assert upsert["ok"] is True
    state = service.get_task_state(task_id="profile_a", state_key="cursor")
    assert state["ok"] is True
    assert state["value"]["page"] == 3

    seen0 = service.has_task_item_seen(task_id="profile_a", provider="indeed", item_key="job_1")
    assert seen0["ok"] is True
    assert seen0["seen"] is False
    mark = service.mark_task_item_seen(
        task_id="profile_a",
        provider="indeed",
        item_key="job_1",
        metadata={"title": "SE"},
    )
    assert mark["ok"] is True
    seen1 = service.has_task_item_seen(task_id="profile_a", provider="indeed", item_key="job_1")
    assert seen1["ok"] is True
    assert seen1["seen"] is True


def test_waiting_runs_expire_to_blocked(configured_central):
    service = CentralService()
    trigger = service._store.enqueue_manual_run(profile_id="profile_a", description="expire test")  # noqa: SLF001
    assert trigger["ok"] is True
    run_id = str(trigger["run_id"])
    _ = service._store.claim_next_run()  # noqa: SLF001
    marked = service._store.mark_waiting_for_user(  # noqa: SLF001
        run_id=run_id,
        question="Need reply",
        requested_by="ui",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert marked["ok"] is True

    expiry = service._expire_waiting_runs()  # noqa: SLF001
    assert expiry["ok"] is True
    assert run_id in expiry["expired_run_ids"]

    row = service._store.get_run(run_id=run_id)  # noqa: SLF001
    assert row is not None
    assert row["status"] == "blocked"
