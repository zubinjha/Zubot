from datetime import UTC, datetime

from src.zubot.core.task_heartbeat import TaskHeartbeat
from src.zubot.core.task_scheduler_store import TaskSchedulerStore


def test_heartbeat_enqueues_due_runs(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_a",
                "profile_id": "profile_a",
                "enabled": True,
                "run_frequency_minutes": 10,
            }
        ]
    )
    heartbeat = TaskHeartbeat(store=store)
    out = heartbeat.enqueue_due_runs(now=datetime(2026, 2, 15, 12, 0, tzinfo=UTC))
    assert out["ok"] is True
    assert out["enqueued"] == 1
    assert isinstance(out["runs"], list)
    hb_state = store.heartbeat_state()
    assert hb_state["ok"] is True
    assert hb_state["state"]["last_heartbeat_status"] == "ok"
