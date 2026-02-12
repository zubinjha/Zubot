from datetime import datetime

from src.zubot.core.daily_memory import (
    append_daily_memory_entry,
    daily_memory_path,
    ensure_daily_memory_file,
    load_recent_daily_memory,
    write_daily_summary_snapshot,
)


def test_ensure_and_append_daily_memory(tmp_path):
    day = datetime(2026, 2, 12)
    created = ensure_daily_memory_file(day=day, root=tmp_path, layer="raw")
    assert created.exists()

    write = append_daily_memory_entry(
        text="completed task",
        session_id="sess_a",
        kind="turn",
        root=tmp_path,
        layer="raw",
    )
    assert write["ok"] is True
    assert "memory/daily/raw" in write["path"]


def test_load_recent_daily_memory_returns_existing_files(tmp_path):
    path = daily_memory_path(root=tmp_path, layer="summary")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Daily Summary {path.stem}\n\n- test\n", encoding="utf-8")

    loaded = load_recent_daily_memory(days=1, root=tmp_path)
    assert path.as_posix() in loaded


def test_write_daily_summary_snapshot_replaces_file(tmp_path):
    day = datetime(2026, 2, 12)
    write_daily_summary_snapshot(text="- first summary", day=day, root=tmp_path)
    write_daily_summary_snapshot(text="- second summary", day=day, root=tmp_path)
    path = daily_memory_path(day=day, root=tmp_path, layer="summary")
    text = path.read_text(encoding="utf-8")
    assert "second summary" in text
    assert "first summary" not in text
