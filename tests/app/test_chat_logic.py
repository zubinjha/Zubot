import app.chat_logic as chat_logic
from app.chat_logic import handle_chat_message, initialize_session_context, reset_session_context


def test_handle_chat_message_empty():
    result = handle_chat_message("   ", allow_llm_fallback=False)
    assert not result["ok"]
    assert result["route"] == "validation"


def test_handle_chat_message_time_route():
    result = handle_chat_message("what time is it?", allow_llm_fallback=False)
    assert result["ok"]
    assert result["route"] == "direct_tool.time"
    assert "Current local time" in result["reply"]


def test_handle_chat_message_direct_fallback():
    result = handle_chat_message("tell me a joke", allow_llm_fallback=False)
    assert result["ok"]
    assert result["route"] == "direct_fallback"


def test_reset_session_context():
    handle_chat_message("time", allow_llm_fallback=False, session_id="s1")
    reset = reset_session_context("s1")
    assert reset["ok"]
    assert reset["reset"] is True


def test_initialize_session_context():
    out = initialize_session_context("s-init")
    assert out["ok"] is True
    assert out["initialized"] is True
    assert out["session_id"] == "s-init"


def test_handle_chat_message_llm_session_id_in_debug(monkeypatch):
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "hello"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    result = handle_chat_message("who am i", allow_llm_fallback=True, session_id="s-debug")
    assert result["ok"]
    assert result["route"] == "llm.main_agent"
    assert result["data"]["context_debug"]["session_id"] == "s-debug"


def test_handle_chat_message_refreshes_daily_memory_each_turn(monkeypatch):
    calls = {"n": 0}

    def fake_recent(*, days=2):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(chat_logic, "load_recent_daily_memory", fake_recent)
    handle_chat_message("time", allow_llm_fallback=False, session_id="daily-refresh")
    handle_chat_message("time", allow_llm_fallback=False, session_id="daily-refresh")
    assert calls["n"] >= 2


def test_daily_memory_flushes_on_interval_not_every_turn(monkeypatch):
    writes = []

    def fake_append(**kwargs):
        writes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(chat_logic, "append_daily_memory_entry", fake_append)
    session_id = "daily-interval"
    for _ in range(5):
        handle_chat_message("time", allow_llm_fallback=False, session_id=session_id)
    assert len(writes) == 0

    handle_chat_message("time", allow_llm_fallback=False, session_id=session_id)
    assert len(writes) == 1
    assert writes[0]["kind"] == "summary"
    assert "reason=interval" in writes[0]["text"]


def test_daily_memory_flushes_on_session_reset(monkeypatch):
    writes = []

    def fake_append(**kwargs):
        writes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(chat_logic, "append_daily_memory_entry", fake_append)
    session_id = "daily-reset"
    handle_chat_message("time", allow_llm_fallback=False, session_id=session_id)
    reset_session_context(session_id)
    assert len(writes) == 1
    assert "reason=session_reset" in writes[0]["text"]
