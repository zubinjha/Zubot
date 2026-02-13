import sqlite3

from src.zubot.core.memory_index import (
    ensure_memory_index_schema,
    get_day_status,
    get_days_pending_summary,
    increment_day_message_count,
    mark_day_finalized,
    mark_day_summarized,
    memory_index_path,
)


def test_memory_index_increment_and_status(tmp_path):
    ensure_memory_index_schema(root=tmp_path)
    out = increment_day_message_count(day="2026-02-12", amount=3, root=tmp_path)
    assert out["messages_since_last_summary"] == 3
    status = get_day_status(day="2026-02-12", root=tmp_path)
    assert status is not None
    assert status["messages_since_last_summary"] == 3


def test_memory_index_summarize_and_finalize(tmp_path):
    increment_day_message_count(day="2026-02-10", amount=5, root=tmp_path)
    summarized = mark_day_summarized(day="2026-02-10", summarized_messages=3, root=tmp_path)
    assert summarized["messages_since_last_summary"] == 0
    final = mark_day_finalized(day="2026-02-10", root=tmp_path)
    assert final["is_finalized"] is True


def test_get_days_pending_summary_before_day(tmp_path):
    increment_day_message_count(day="2026-02-09", amount=2, root=tmp_path)
    increment_day_message_count(day="2026-02-11", amount=1, root=tmp_path)
    pending = get_days_pending_summary(before_day="2026-02-10", root=tmp_path)
    assert len(pending) == 1
    assert pending[0]["day"] == "2026-02-09"


def test_memory_index_path_is_unified_under_central_db(tmp_path):
    path = memory_index_path(root=tmp_path)
    assert str(path).endswith("memory/central/zubot_core.db")


def test_memory_index_migrates_legacy_table_into_unified_db(tmp_path):
    legacy = tmp_path / "memory" / "memory_index.sqlite3"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            """
            CREATE TABLE day_memory_status (
                day TEXT PRIMARY KEY,
                messages_since_last_summary INTEGER NOT NULL DEFAULT 0,
                summaries_count INTEGER NOT NULL DEFAULT 0,
                is_finalized INTEGER NOT NULL DEFAULT 0,
                last_summary_at TEXT,
                last_event_at TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO day_memory_status(day, messages_since_last_summary, summaries_count, is_finalized)
            VALUES('2026-02-09', 2, 1, 0);
            """
        )
        conn.commit()

    ensure_memory_index_schema(root=tmp_path)
    status = get_day_status(day="2026-02-09", root=tmp_path)
    assert status is not None
    assert status["messages_since_last_summary"] == 2
    assert status["summaries_count"] == 1
