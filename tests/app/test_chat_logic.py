import app.chat_logic as chat_logic
from app.chat_logic import handle_chat_message, initialize_session_context, reset_session_context


def test_handle_chat_message_empty():
    result = handle_chat_message("   ", allow_llm_fallback=False)
    assert not result["ok"]
    assert result["route"] == "validation"


def test_handle_chat_message_time_uses_llm_tool_path(monkeypatch):
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "Current local time: 10:00 AM"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    result = handle_chat_message("what time is it?", allow_llm_fallback=True)
    assert result["ok"]
    assert result["route"] == "llm.main_agent"
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


def test_initialize_session_context_auto_finalizes_prior_days(monkeypatch):
    monkeypatch.setattr(
        chat_logic,
        "get_days_pending_summary",
        lambda **kwargs: [{"day": "2026-02-10", "messages_since_last_summary": 4}],
    )
    writes = []
    marks = []
    monkeypatch.setattr(chat_logic, "write_daily_summary_snapshot", lambda **kwargs: writes.append(kwargs) or {"ok": True})
    monkeypatch.setattr(
        chat_logic,
        "mark_day_summarized",
        lambda **kwargs: marks.append(kwargs) or {"day": kwargs["day"]},
    )
    out = initialize_session_context("auto-fin")
    assert out["preload"]["auto_finalized_days"] == ["2026-02-10"]
    assert writes[0]["day_str"] == "2026-02-10"
    assert marks[0]["finalize"] is True


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


def test_handle_chat_message_llm_tool_loop_executes_tool(monkeypatch):
    calls = {"n": 0}

    def fake_call_llm(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": True,
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_current_time", "arguments": "{}"},
                    }
                ],
            }
        return {"ok": True, "text": "Current local time: 10:00 AM", "tool_calls": None}

    monkeypatch.setattr(chat_logic, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    monkeypatch.setattr(chat_logic, "invoke_tool", lambda name, **kwargs: {"ok": True, "human_local": "10:00 AM"})
    monkeypatch.setattr(
        chat_logic,
        "list_tools",
        lambda **kwargs: [
            {
                "name": "get_current_time",
                "category": "kernel",
                "description": "get time",
                "parameters": {},
            }
        ],
    )

    result = handle_chat_message("please help with this task", allow_llm_fallback=True, session_id="tool-loop")
    assert result["ok"] is True
    assert result["route"] == "llm.main_agent"
    assert "10:00 AM" in result["reply"]
    assert len(result["data"]["tool_execution"]) == 1
    assert result["data"]["tool_execution"][0]["name"] == "get_current_time"


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

    def fake_write(**kwargs):
        writes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(chat_logic, "write_daily_summary_snapshot", fake_write)
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "ok"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    session_id = "daily-interval"
    for _ in range(29):
        handle_chat_message("time", allow_llm_fallback=True, session_id=session_id)
    assert len(writes) == 0

    handle_chat_message("time", allow_llm_fallback=True, session_id=session_id)
    assert len(writes) == 1
    assert "Summary reason: interval" in writes[0]["text"]


def test_daily_memory_flushes_on_session_reset(monkeypatch):
    writes = []

    def fake_write(**kwargs):
        writes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(chat_logic, "write_daily_summary_snapshot", fake_write)
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "ok"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    session_id = "daily-reset"
    handle_chat_message("time", allow_llm_fallback=True, session_id=session_id)
    reset_session_context(session_id)
    assert len(writes) == 1
    assert "Summary reason: session_reset" in writes[0]["text"]


def test_daily_memory_summary_attempts_low_model(monkeypatch):
    calls = {"models": []}

    def fake_call_llm(**kwargs):
        calls["models"].append(kwargs.get("model"))
        return {"ok": True, "text": "Summary: completed weather checks."}

    monkeypatch.setattr(chat_logic, "call_llm", fake_call_llm)
    monkeypatch.setattr(chat_logic, "append_session_events", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    session_id = "summary-low-model"
    for _ in range(30):
        handle_chat_message("time", allow_llm_fallback=True, session_id=session_id)
    assert "low" in calls["models"]


def test_session_events_persist_in_order(monkeypatch):
    captured = []

    def fake_append(session_id, events, **kwargs):
        captured.extend([event.to_dict() for event in events])

    monkeypatch.setattr(chat_logic, "load_config", lambda: {"memory": {"session_event_logging_enabled": True}})
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "ok"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    monkeypatch.setattr(chat_logic, "append_session_events", fake_append)
    handle_chat_message("time", allow_llm_fallback=True, session_id="order-test")
    assert len(captured) == 2
    assert captured[0]["event_type"] == "user_message"
    assert captured[1]["event_type"] == "assistant_message"


def test_daily_summary_prompt_prioritizes_conceptual_progress(monkeypatch):
    captured = {"prompt": None}

    def fake_call_llm(**kwargs):
        messages = kwargs.get("messages", [])
        if isinstance(messages, list) and len(messages) > 1:
            captured["prompt"] = messages[1].get("content")
        return {"ok": True, "text": "Summary bullets."}

    monkeypatch.setattr(chat_logic, "call_llm", fake_call_llm)
    out = chat_logic._summarize_turns_with_low_model(
        [
            {"route": "llm.main_agent", "user": "implemented tool loop", "reply": "added tests and docs"},
            {"route": "llm.main_agent", "user": "thanks", "reply": "ok"},
        ],
    )
    assert "Summary bullets." in out
    assert isinstance(captured["prompt"], str)
    assert "what was done conceptually, how it was done, and the outcome" in captured["prompt"]
    assert "Do not include idle chat" in captured["prompt"]


def test_daily_summary_fallback_prefers_signal_turns(monkeypatch):
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": False, "text": None})
    out = chat_logic._summarize_turns_with_low_model(
        [
            {"route": "llm.main_agent", "user": "thanks", "reply": "ok"},
            {"route": "llm.main_agent", "user": "implemented weather tool wiring", "reply": "added parser and tests"},
            {"route": "llm.error_fallback", "user": "hi", "reply": "provider unavailable"},
        ],
    )
    assert "Signal turns: 1 of 3" in out
    assert "implemented weather tool wiring" in out


def test_handle_chat_message_injects_forwarded_worker_events(monkeypatch):
    class _FakeManager:
        def list_workers(self):
            return {
                "ok": True,
                "workers": [],
                "runtime": {"running_count": 0, "queued_count": 0, "max_concurrent_workers": 3},
            }

        def list_forward_events(self, consume=True):
            _ = consume
            return {
                "ok": True,
                "events": [
                    {
                        "event_id": "wevt_1",
                        "worker_id": "worker_1",
                        "worker_title": "Research",
                        "type": "worker_completed",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "payload": {"summary": "done"},
                    }
                ],
            }

    monkeypatch.setattr(chat_logic, "get_worker_manager", lambda: _FakeManager())
    monkeypatch.setattr(chat_logic, "call_llm", lambda **kwargs: {"ok": True, "text": "ack"})
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    result = handle_chat_message("status?", allow_llm_fallback=True, session_id="worker-forward")
    assert result["ok"] is True
    assert result["data"]["context_debug"]["forwarded_worker_events_injected"] == 1


def test_handle_chat_message_keeps_worker_context_isolated(monkeypatch):
    captured = {"messages": None}
    secret = "WORKER_INTERNAL_SECRET_SHOULD_NOT_LEAK"

    class _FakeManager:
        def list_workers(self):
            return {
                "ok": True,
                "workers": [
                    {
                        "worker_id": "worker_1",
                        "title": "Research Task",
                        "status": "running",
                        "cancel_requested": False,
                        # Simulate accidental internal fields in manager output.
                        "context_session_dump": secret,
                        "facts_raw": {"internal": secret},
                    }
                ],
                "runtime": {"running_count": 1, "queued_count": 0, "max_concurrent_workers": 3},
            }

        def list_forward_events(self, consume=True):
            _ = consume
            return {"ok": True, "events": []}

    def fake_call_llm(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return {"ok": True, "text": "ok"}

    monkeypatch.setattr(chat_logic, "get_worker_manager", lambda: _FakeManager())
    monkeypatch.setattr(chat_logic, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        chat_logic,
        "load_context_bundle",
        lambda **kwargs: {"base": {"context/AGENT.md": "x"}, "supplemental": {}},
    )
    result = handle_chat_message("check status", allow_llm_fallback=True, session_id="worker-isolation")
    assert result["ok"] is True

    all_content = " ".join(str(msg.get("content", "")) for msg in (captured["messages"] or []))
    assert secret not in all_content
