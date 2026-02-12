import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src.zubot.core.agent_types import SessionEvent
from src.zubot.core.session_store import (
    append_session_events,
    cleanup_session_logs_older_than,
    load_session_events,
    session_log_path,
)


def test_session_store_append_and_load(tmp_path: Path):
    session_id = "sess_123"
    event = SessionEvent(
        session_id=session_id,
        event_type="user_message",
        payload={"text": "hello"},
        source="user",
    )
    append_session_events(session_id, [event], root=tmp_path)

    path = session_log_path(session_id, root=tmp_path)
    assert path.exists()

    loaded = load_session_events(session_id, root=tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["event_type"] == "user_message"


def test_cleanup_session_logs_older_than(tmp_path: Path):
    old_path = session_log_path("old", root=tmp_path)
    new_path = session_log_path("new", root=tmp_path)
    old_path.write_text("{}", encoding="utf-8")
    new_path.write_text("{}", encoding="utf-8")

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=10)).timestamp()
    new_ts = (now - timedelta(days=1)).timestamp()
    old_path.touch()
    new_path.touch()
    # Set deterministic mtimes for retention behavior.
    os.utime(old_path, (old_ts, old_ts))
    os.utime(new_path, (new_ts, new_ts))

    result = cleanup_session_logs_older_than(days=7, root=tmp_path, now=now)
    assert result["ok"]
    assert result["removed_count"] == 1
    assert "old.jsonl" in result["removed_files"]
    assert not old_path.exists()
    assert new_path.exists()
