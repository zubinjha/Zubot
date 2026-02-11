from pathlib import Path

from src.zubot.core.agent_types import SessionEvent
from src.zubot.core.session_store import append_session_events, load_session_events, session_log_path


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
