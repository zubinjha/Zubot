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


def test_task_profile_crud(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    upsert = store.upsert_task_profile(
        {
            "task_id": "task_a",
            "name": "Task A",
            "kind": "script",
            "entrypoint_path": "src/zubot/tasks/task_a/task.py",
            "timeout_sec": 120,
            "enabled": True,
            "source": "test",
        }
    )
    assert upsert["ok"] is True
    assert upsert["task_id"] == "task_a"

    listed = store.list_task_profiles()
    assert len(listed) == 1
    assert listed[0]["task_id"] == "task_a"
    assert listed[0]["name"] == "Task A"

    got = store.get_task_profile(task_id="task_a")
    assert got is not None
    assert got["task_id"] == "task_a"

    deleted = store.delete_task_profile(task_id="task_a")
    assert deleted["ok"] is True
    assert deleted["deleted"] == 1


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


def test_frequency_misfire_queue_latest_enqueues_latest_fire(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_latest",
                "profile_id": "profile_latest",
                "enabled": True,
                "run_frequency_minutes": 10,
                "misfire_policy": "queue_latest",
                "next_run_at": datetime(2026, 2, 16, 0, 0, tzinfo=UTC).isoformat(),
            }
        ]
    )
    out = store.enqueue_due_runs(now=datetime(2026, 2, 16, 0, 35, tzinfo=UTC))
    assert out["ok"] is True
    assert out["enqueued"] == 1
    runs = store.list_runs(limit=5)
    assert runs[0]["planned_fire_at"] == datetime(2026, 2, 16, 0, 30, tzinfo=UTC).isoformat()
    schedules = store.list_schedules()
    assert schedules[0]["next_run_at"] == datetime(2026, 2, 16, 0, 40, tzinfo=UTC).isoformat()


def test_frequency_misfire_skip_advances_cursor_without_enqueue(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.sync_schedules(
        [
            {
                "schedule_id": "sched_skip",
                "profile_id": "profile_skip",
                "enabled": True,
                "run_frequency_minutes": 10,
                "misfire_policy": "skip",
                "next_run_at": datetime(2026, 2, 16, 0, 0, tzinfo=UTC).isoformat(),
            }
        ]
    )
    out = store.enqueue_due_runs(now=datetime(2026, 2, 16, 0, 35, tzinfo=UTC))
    assert out["ok"] is True
    assert out["enqueued"] == 0
    schedules = store.list_schedules()
    assert schedules[0]["last_planned_run_at"] == datetime(2026, 2, 16, 0, 30, tzinfo=UTC).isoformat()
    assert schedules[0]["next_run_at"] == datetime(2026, 2, 16, 0, 40, tzinfo=UTC).isoformat()


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


def test_enqueue_agentic_run_payload_shape(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    out = store.enqueue_agentic_run(
        task_name="Background Research",
        instructions="Research topic X",
        requested_by="ui",
        model_tier="medium",
        tool_access=["web_search"],
        skill_access=[],
        timeout_sec=120,
        metadata={"source": "test"},
    )
    assert out["ok"] is True
    runs = store.list_runs(limit=5)
    assert runs
    row = runs[0]
    assert row["profile_id"] == "agentic_task"
    assert row["payload"]["run_kind"] == "agentic"
    assert row["payload"]["instructions"] == "Research topic X"


def test_runtime_counts(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.enqueue_manual_run(profile_id="profile_manual")
    counts = store.runtime_counts()
    assert counts["queued_count"] == 1
    assert counts["running_count"] == 0
    assert counts["waiting_count"] == 0

    claimed = store.claim_next_run()
    assert claimed is not None
    counts_after = store.runtime_counts()
    assert counts_after["queued_count"] == 0
    assert counts_after["running_count"] == 1
    assert counts_after["waiting_count"] == 0


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


def test_mode_switch_calendar_to_frequency_clears_calendar_rows(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.upsert_schedule(
        {
            "schedule_id": "sched_switch",
            "profile_id": "profile_switch",
            "enabled": True,
            "mode": "calendar",
            "execution_order": 10,
            "run_times": ["02:00"],
            "timezone": "America/New_York",
            "days_of_week": ["mon", "tue"],
        }
    )
    first = [x for x in store.list_schedules() if x["schedule_id"] == "sched_switch"][0]
    assert first["run_times"]
    assert first["days_of_week"] == ["mon", "tue"]

    store.upsert_schedule(
        {
            "schedule_id": "sched_switch",
            "profile_id": "profile_switch",
            "enabled": True,
            "mode": "frequency",
            "execution_order": 10,
            "run_frequency_minutes": 120,
        }
    )
    second = [x for x in store.list_schedules() if x["schedule_id"] == "sched_switch"][0]
    assert second["mode"] == "frequency"
    assert second["run_times"] == []
    assert second["days_of_week"] == []


def test_delete_schedule_cascades_child_rows(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    store.upsert_schedule(
        {
            "schedule_id": "sched_delete",
            "profile_id": "profile_delete",
            "enabled": True,
            "mode": "calendar",
            "execution_order": 10,
            "run_times": ["02:00", "13:30"],
            "timezone": "America/New_York",
            "days_of_week": ["mon", "wed"],
        }
    )

    with store._connect() as conn:  # noqa: SLF001 - test-only direct inspection
        before_times = conn.execute(
            "SELECT COUNT(*) AS c FROM defined_tasks_run_times WHERE schedule_id = 'sched_delete';"
        ).fetchone()
        before_days = conn.execute(
            "SELECT COUNT(*) AS c FROM defined_tasks_days_of_week WHERE schedule_id = 'sched_delete';"
        ).fetchone()
    assert int(before_times["c"]) == 2
    assert int(before_days["c"]) == 2

    out = store.delete_schedule(schedule_id="sched_delete")
    assert out["ok"] is True
    assert out["deleted"] == 1

    with store._connect() as conn:  # noqa: SLF001 - test-only direct inspection
        after_times = conn.execute(
            "SELECT COUNT(*) AS c FROM defined_tasks_run_times WHERE schedule_id = 'sched_delete';"
        ).fetchone()
        after_days = conn.execute(
            "SELECT COUNT(*) AS c FROM defined_tasks_days_of_week WHERE schedule_id = 'sched_delete';"
        ).fetchone()
    assert int(after_times["c"]) == 0
    assert int(after_days["c"]) == 0


def test_waiting_resume_state_machine(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    enq = store.enqueue_manual_run(profile_id="profile_wait")
    assert enq["ok"] is True
    run_id = str(enq["run_id"])

    claimed = store.claim_next_run()
    assert claimed is not None
    assert claimed["run_id"] == run_id
    assert claimed["status"] == "running"

    marked = store.mark_waiting_for_user(
        run_id=run_id,
        question="Which option?",
        wait_context={"choices": ["a", "b"]},
        requested_by="ui",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    assert marked["ok"] is True
    assert marked["status"] == "waiting_for_user"
    assert marked["waiting"]["request_id"].startswith("wait_")
    assert marked["waiting"]["expires_at"] == "2030-01-01T00:00:00+00:00"

    row_wait = store.get_run(run_id=run_id)
    assert row_wait is not None
    assert row_wait["status"] == "waiting_for_user"
    assert row_wait["payload"]["waiting"]["question"] == "Which option?"
    counts = store.runtime_counts()
    assert counts["waiting_count"] == 1
    metrics = store.runtime_metrics()
    assert metrics["longest_waiting_age_sec"] is not None

    resumed = store.resume_waiting_run(run_id=run_id, user_response="choose a", requested_by="ui")
    assert resumed["ok"] is True
    assert resumed["status"] == "queued"
    assert resumed["resumed"] is True
    assert resumed["waiting"]["state"] == "resumed"

    row_resume = store.get_run(run_id=run_id)
    assert row_resume is not None
    assert row_resume["status"] == "queued"
    assert row_resume["payload"]["resume_response"] == "choose a"
    assert row_resume["payload"]["resume_history"][-1]["response"] == "choose a"


def test_cancel_waiting_run_becomes_blocked(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    enq = store.enqueue_manual_run(profile_id="profile_wait")
    run_id = str(enq["run_id"])
    _ = store.claim_next_run()
    _ = store.mark_waiting_for_user(run_id=run_id, question="Need input")

    out = store.cancel_run(run_id=run_id, reason="killed_by_user")
    assert out["ok"] is True
    assert out["status"] == "blocked"

    row = store.get_run(run_id=run_id)
    assert row is not None
    assert row["status"] == "blocked"


def test_job_applications_table_schema_matches_sheet_columns(tmp_path):
    store = TaskSchedulerStore(db_path=tmp_path / "scheduler.sqlite3")
    with store._connect() as conn:  # noqa: SLF001 - test-only inspection
        rows = conn.execute("PRAGMA table_info(job_applications);").fetchall()
    columns = [str(row["name"]) for row in rows]
    assert columns == [
        "job_key",
        "company",
        "job_title",
        "location",
        "date_found",
        "date_applied",
        "status",
        "pay_range",
        "job_link",
        "source",
        "cover_letter",
        "notes",
        "created_at",
        "updated_at",
    ]
