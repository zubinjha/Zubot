from src.zubot.core.context_assembler import assemble_messages


def test_assemble_messages_basic():
    context_bundle = {
        "base": {
            "context/AGENT.md": "agent rules",
            "context/SOUL.md": "soul rules",
        },
        "supplemental": {"context/more-about-human/resume.md": "resume info"},
    }
    recent_events = [
        {"event_type": "user_message", "payload": {"text": "hello"}},
        {"event_type": "assistant_message", "payload": {"text": "hi"}},
    ]

    result = assemble_messages(context_bundle=context_bundle, recent_events=recent_events)
    assert len(result["messages"]) >= 4
    assert result["token_estimate"] > 0


def test_assemble_messages_trims_optional_when_over_budget():
    context_bundle = {
        "base": {"context/AGENT.md": "A" * 3000},
        "supplemental": {"context/more-about-human/resume.md": "B" * 3000},
    }
    result = assemble_messages(
        context_bundle=context_bundle,
        recent_events=[],
        max_context_tokens=200,
        reserved_output_tokens=50,
    )
    assert result["budget"] is not None
    assert result["removed_optional_context_messages"] >= 1


def test_assemble_messages_keeps_newest_recent_under_budget():
    recent_events = [
        {"event_type": "user_message", "payload": {"text": "old message " + ("x" * 800)}},
        {"event_type": "assistant_message", "payload": {"text": "old reply " + ("y" * 800)}},
        {"event_type": "user_message", "payload": {"text": "newest question"}},
    ]
    result = assemble_messages(
        context_bundle={"base": {"context/AGENT.md": "agent"}},
        recent_events=recent_events,
        max_context_tokens=240,
        reserved_output_tokens=80,
    )

    all_content = " ".join(msg["content"] for msg in result["messages"])
    assert "newest question" in all_content
    assert result["dropped_recent_event_count"] >= 1


def test_assemble_messages_updates_rolling_summary_from_dropped_recent():
    recent_events = [
        {"event_type": "user_message", "payload": {"text": "first older question " + ("a" * 1000)}},
        {"event_type": "assistant_message", "payload": {"text": "first older answer " + ("b" * 1000)}},
        {"event_type": "user_message", "payload": {"text": "latest"}},
    ]
    result = assemble_messages(
        context_bundle={"base": {"context/AGENT.md": "agent"}},
        recent_events=recent_events,
        session_summary="Existing summary.",
        max_context_tokens=260,
        reserved_output_tokens=100,
    )

    updated = result.get("updated_session_summary")
    assert isinstance(updated, str)
    assert "Existing summary." in updated
    assert "CompactedHistory" in updated


def test_assemble_messages_recent_window_changes_with_budget():
    recent_events = [
        {"event_type": "user_message", "payload": {"text": "msg1 " + ("a" * 350)}},
        {"event_type": "assistant_message", "payload": {"text": "msg2 " + ("b" * 350)}},
        {"event_type": "user_message", "payload": {"text": "msg3 " + ("c" * 350)}},
    ]
    tight = assemble_messages(
        context_bundle={"base": {"context/AGENT.md": "agent"}},
        recent_events=recent_events,
        max_context_tokens=220,
        reserved_output_tokens=90,
    )
    loose = assemble_messages(
        context_bundle={"base": {"context/AGENT.md": "agent"}},
        recent_events=recent_events,
        max_context_tokens=900,
        reserved_output_tokens=90,
    )

    assert tight["kept_recent_message_count"] <= loose["kept_recent_message_count"]


def test_assemble_messages_extracts_updated_facts_from_recent_events():
    recent_events = [
        {"event_type": "user_message", "payload": {"text": "My name is Zubin Jha."}},
    ]
    result = assemble_messages(
        context_bundle={"base": {"context/AGENT.md": "agent"}},
        recent_events=recent_events,
    )

    assert result["updated_facts"]["user_name"] == "Zubin Jha"
