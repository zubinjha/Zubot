from datetime import UTC, datetime

import src.zubot.core.daily_memory as daily_memory
from src.zubot.core.daily_memory import (
    append_daily_memory_entry,
    ensure_daily_memory_file,
    ensure_daily_memory_schema,
    list_day_raw_entries,
    load_recent_daily_memory,
    write_daily_summary_snapshot,
)
from src.zubot.core.memory_index import memory_index_path


def test_ensure_and_append_daily_memory(tmp_path):
    day = datetime(2026, 2, 12)
    created = ensure_daily_memory_file(day=day, root=tmp_path, layer="raw")
    assert "memory/daily/raw/2026-02-12.md" in str(created)

    write = append_daily_memory_entry(
        text="completed task",
        session_id="sess_a",
        kind="turn",
        root=tmp_path,
        layer="raw",
    )
    assert write["ok"] is True
    rows = list_day_raw_entries(day=daily_memory.local_day_str(now=datetime.now(UTC)), root=tmp_path)
    assert any("completed task" in row["text"] for row in rows)


def test_load_recent_daily_memory_returns_existing_files(tmp_path):
    write_daily_summary_snapshot(text="- test", root=tmp_path)

    loaded = load_recent_daily_memory(days=1, root=tmp_path)
    assert any(key.startswith("memory/db/summary/") for key in loaded)


def test_load_recent_daily_memory_uses_raw_fallback_when_summary_missing(tmp_path):
    append_daily_memory_entry(
        text="hello route=llm.main_agent",
        session_id="sess_a",
        kind="user",
        day_str=daily_memory.local_day_str(),
        root=tmp_path,
        layer="raw",
    )

    loaded = load_recent_daily_memory(days=1, root=tmp_path)
    keys = list(loaded.keys())
    assert len(keys) == 1
    assert keys[0].endswith("#raw_fallback")
    assert "trimmed raw fallback" in loaded[keys[0]]


def test_write_daily_summary_snapshot_replaces_file(tmp_path):
    day = datetime.now(UTC)
    day_key = day.strftime("%Y-%m-%d")
    write_daily_summary_snapshot(text="- first summary", day=day, root=tmp_path, session_id="s1")
    write_daily_summary_snapshot(text="- second summary", day=day, root=tmp_path, session_id="s2")
    loaded = load_recent_daily_memory(days=1, root=tmp_path)
    key = f"memory/db/summary/{day_key}.md"
    text = loaded[key]
    assert "second summary" in text
    assert "first summary" not in text


def test_append_daily_memory_entry_uses_event_time_for_timestamp_when_day_str(monkeypatch, tmp_path):
    fixed = datetime(2026, 2, 12, 15, 4, 5)
    monkeypatch.setattr(daily_memory, "_now_local", lambda: fixed)
    write = append_daily_memory_entry(
        text="timestamp check",
        session_id="sess_a",
        kind="user",
        day_str="2026-02-12",
        root=tmp_path,
        layer="raw",
    )
    assert write["ok"] is True
    assert "[15:04:05]" in write["entry"]


def test_daily_memory_is_db_backed(tmp_path):
    append_daily_memory_entry(text="db row", kind="user", root=tmp_path, layer="raw")
    db_path = memory_index_path(root=tmp_path)
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM daily_memory_events;").fetchone()
    assert row is not None
    assert int(row[0]) >= 1


def test_legacy_daily_files_are_imported_into_db(tmp_path):
    raw_dir = tmp_path / "memory" / "daily" / "raw"
    summary_dir = tmp_path / "memory" / "daily" / "summary"
    raw_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.joinpath("2026-02-10.md").write_text(
        "# Daily Raw 2026-02-10\n\n- [10:00:00] [user] (s1) hello route=llm.main_agent\n",
        encoding="utf-8",
    )
    summary_dir.joinpath("2026-02-10.md").write_text(
        "# Daily Summary 2026-02-10\n\n- imported summary\n",
        encoding="utf-8",
    )

    ensure_daily_memory_schema(root=tmp_path)
    rows = list_day_raw_entries(day="2026-02-10", root=tmp_path)
    assert len(rows) == 1
    loaded = load_recent_daily_memory(days=10000, root=tmp_path)
    assert "memory/db/summary/2026-02-10.md" in loaded
