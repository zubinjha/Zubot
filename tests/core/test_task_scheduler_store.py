from datetime import UTC, datetime, timedelta

from src.zubot.core.task_scheduler_store import TaskSchedulerStore


def _now() -> datetime:
    return datetime.now(tz=UTC)


def test_store_sync_and_list_schedules(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    out = store.sync_schedules(
        [
            {
                "schedule_id": "sched_a",
                "profile_id": "profile_a",
                "enabled": True,
                "run_frequency_minutes": 10,
            }
        ]
    )
    assert out["ok"] is True
    assert out["upserted"] == 1

    schedules = store.list_schedules()
    assert len(schedules) == 1
    assert schedules[0]["schedule_id"] == "sched_a"
    assert schedules[0]["profile_id"] == "profile_a"
    assert schedules[0]["mode"] == "frequency"


def test_enqueue_due_runs_and_dedupe(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_a",
                "profile_id": "profile_a",
                "enabled": True,
                "run_frequency_minutes": 30,
            }
        ]
    )

    first = store.enqueue_due_runs(now=_now())
    second = store.enqueue_due_runs(now=_now())

    assert first["ok"] is True
    assert first["enqueued"] == 1
    assert second["ok"] is True
    # second enqueue should be deduped while a queued/running run exists
    assert second["enqueued"] == 0


def test_claim_and_complete_run_updates_schedule_status(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_a",
                "profile_id": "profile_a",
                "enabled": True,
                "run_frequency_minutes": 30,
            }
        ]
    )
    store.enqueue_due_runs(now=_now())

    claimed = store.claim_next_run()
    assert claimed is not None
    assert claimed["status"] == "running"

    completed = store.complete_run(run_id=claimed["run_id"], status="done", summary="ok", error=None)
    assert completed["ok"] is True

    schedules = store.list_schedules()
    assert schedules[0]["last_status"] == "done"
    assert schedules[0]["last_summary"] == "ok"
    assert schedules[0]["last_successful_run_at"] is not None


def test_manual_enqueue_and_list_runs(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    manual = store.enqueue_manual_run(profile_id="profile_manual", description="manual trigger")
    assert manual["ok"] is True

    runs = store.list_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["profile_id"] == "profile_manual"
    assert runs[0]["status"] == "queued"
    assert runs[0]["payload"]["trigger"] == "manual"


def test_runtime_counts(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.enqueue_manual_run(profile_id="profile_manual")
    counts = store.runtime_counts()
    assert counts["queued_count"] == 1
    assert counts["running_count"] == 0

    claimed = store.claim_next_run()
    assert claimed is not None
    counts_after = store.runtime_counts()
    assert counts_after["queued_count"] == 0
    assert counts_after["running_count"] == 1


def test_prune_runs_keeps_recent_history_and_active_runs(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.enqueue_manual_run(profile_id="profile_a")
    claimed = store.claim_next_run()
    assert claimed is not None
    store.complete_run(run_id=claimed["run_id"], status="done", summary="ok")

    # second finished run
    store.enqueue_manual_run(profile_id="profile_a")
    claimed_2 = store.claim_next_run()
    assert claimed_2 is not None
    store.complete_run(run_id=claimed_2["run_id"], status="failed", summary=None, error="boom")

    # keep one queued run that should not be pruned
    store.enqueue_manual_run(profile_id="profile_a")

    old_now = _now() + timedelta(days=90)
    pruned = store.prune_runs(max_age_days=30, max_history_rows=1, now=old_now)
    assert pruned["ok"] is True
    assert pruned["deleted_runs"] >= 1

    runs = store.list_runs(limit=20)
    statuses = {run["status"] for run in runs}
    assert "queued" in statuses


def test_run_history_tracks_completed_runs(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_hist",
                "profile_id": "profile_hist",
                "enabled": True,
                "run_frequency_minutes": 30,
            }
        ]
    )
    store.enqueue_due_runs(now=_now())
    claimed = store.claim_next_run()
    assert claimed is not None
    completed = store.complete_run(run_id=claimed["run_id"], status="done", summary="done")
    assert completed["ok"] is True

    history = store.list_run_history(limit=10)
    assert len(history) == 1
    assert history[0]["run_id"] == claimed["run_id"]
    assert history[0]["status"] == "done"
    assert history[0]["summary"] == "done"


def test_calendar_schedule_runs_once_when_crossing_target_time(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_cal",
                "profile_id": "profile_cal",
                "enabled": True,
                "mode": "calendar",
                "timezone": "UTC",
                "time_of_day": "02:00",
                "days_of_week": ["thu", "fri", "sat", "sun", "mon", "tue", "wed"],
                "catch_up_window_minutes": 180,
            }
        ]
    )

    now = datetime(2026, 2, 13, 2, 10, tzinfo=UTC)
    first = store.enqueue_due_runs(now=now)
    second = store.enqueue_due_runs(now=now)

    assert first["ok"] is True
    assert first["enqueued"] == 1
    assert second["ok"] is True
    assert second["enqueued"] == 0


def test_calendar_schedule_respects_catch_up_window(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_cal",
                "profile_id": "profile_cal",
                "enabled": True,
                "mode": "calendar",
                "timezone": "UTC",
                "time_of_day": "02:00",
            }
        ]
    )

    # More than default 180 minutes after 02:00 should miss the catch-up window.
    now = datetime(2026, 2, 13, 5, 45, tzinfo=UTC)
    out = store.enqueue_due_runs(now=now)
    assert out["ok"] is True
    assert out["enqueued"] == 0


def test_calendar_schedule_days_of_week_filter(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_cal",
                "profile_id": "profile_cal",
                "enabled": True,
                "mode": "calendar",
                "timezone": "UTC",
                "time_of_day": "02:00",
                "days_of_week": ["mon"],
                "catch_up_window_minutes": 180,
            }
        ]
    )

    # Friday should not match Monday-only schedule.
    friday = datetime(2026, 2, 13, 2, 5, tzinfo=UTC)
    out = store.enqueue_due_runs(now=friday)
    assert out["ok"] is True
    assert out["enqueued"] == 0

    # Monday should match.
    monday = datetime(2026, 2, 16, 2, 5, tzinfo=UTC)
    out_mon = store.enqueue_due_runs(now=monday)
    assert out_mon["ok"] is True
    assert out_mon["enqueued"] == 1


def test_calendar_schedule_contract_fields_roundtrip(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_contract",
                "profile_id": "profile_contract",
                "enabled": True,
                "mode": "calendar",
                "timezone": "America/New_York",
                "time_of_day": "02:00",
                "days_of_week": ["mon", "wed", "fri"],
            }
        ]
    )
    schedules = store.list_schedules()
    assert len(schedules) == 1
    sched = schedules[0]
    assert sched["mode"] == "calendar"
    assert sched["timezone"] == "America/New_York"
    assert sched["time_of_day"] == "02:00"
    assert sched["days_of_week"] == ["mon", "wed", "fri"]
    assert isinstance(sched["run_times"], list)
    assert len(sched["run_times"]) == 1
    assert sched["run_times"][0]["time_of_day"] == "02:00"
    assert sched["run_times"][0]["timezone"] == "America/New_York"
    assert sched["run_times"][0]["days_of_week"] == ["mon", "wed", "fri"]
