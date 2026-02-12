from src.zubot.core.memory_index import (
    ensure_memory_index_schema,
    get_day_status,
    get_days_pending_summary,
    increment_day_message_count,
    mark_day_finalized,
    mark_day_summarized,
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
