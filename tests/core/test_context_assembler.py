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
