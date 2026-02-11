from src.zubot.core.summary_memory import build_rolling_summary


def test_build_rolling_summary_returns_existing_when_no_overflow():
    result = build_rolling_summary(existing_summary="abc", overflow_events=[])
    assert result == "abc"


def test_build_rolling_summary_appends_compacted_history():
    events = [{"event_type": "user_message", "payload": {"text": "hello there"}}]
    result = build_rolling_summary(existing_summary="seed", overflow_events=events, max_chars=500)
    assert result is not None
    assert "seed" in result
    assert "CompactedHistory" in result
