import sqlite3

from src.zubot.core.daily_memory import append_daily_memory_entry, load_recent_daily_memory
from src.zubot.core.daily_summary_pipeline import process_pending_summary_jobs, summarize_day_from_raw
from src.zubot.core.memory_index import (
    enqueue_day_summary_job,
    get_day_status,
    increment_day_message_count,
    memory_index_path,
)


def test_summarize_day_from_raw_reads_full_day_entries(tmp_path):
    day = "2026-02-13"
    append_daily_memory_entry(
        day_str=day,
        session_id="s1",
        kind="user",
        text="implemented scheduler route=llm.main_agent",
        layer="raw",
        root=tmp_path,
    )
    append_daily_memory_entry(
        day_str=day,
        session_id="s1",
        kind="main_agent",
        text="added tests and docs route=llm.main_agent",
        layer="raw",
        root=tmp_path,
    )
    increment_day_message_count(day=day, amount=2, root=tmp_path)

    out = summarize_day_from_raw(day=day, reason="test_run", session_id="t", root=tmp_path)
    assert out["ok"] is True
    assert out["summary_entries"] == 2

    loaded = load_recent_daily_memory(days=10000, root=tmp_path)
    text = loaded["memory/db/summary/2026-02-13.md"]
    assert "Summary reason: test_run" in text
    status = get_day_status(day=day, root=tmp_path)
    assert status is not None
    assert status["messages_since_last_summary"] == 0


def test_process_pending_summary_jobs_runs_queued_item(tmp_path):
    day = "2026-02-13"
    append_daily_memory_entry(
        day_str=day,
        session_id="s1",
        kind="tool_event",
        text="ran upload flow route=llm.main_agent",
        layer="raw",
        root=tmp_path,
    )
    increment_day_message_count(day=day, amount=1, root=tmp_path)
    enqueue_day_summary_job(day=day, reason="chat_turn", root=tmp_path)

    out = process_pending_summary_jobs(max_jobs=2, root=tmp_path)
    assert out["ok"] is True
    assert out["processed"] == 1
    assert out["completed"] == 1

    db_path = memory_index_path(root=tmp_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM memory_summary_jobs ORDER BY job_id DESC LIMIT 1;"
        ).fetchone()
    assert row is not None
    assert row[0] == "done"
