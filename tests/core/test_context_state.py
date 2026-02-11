from src.zubot.core.context_state import ContextState


def test_context_state_upsert_reuses_item_when_content_unchanged():
    state = ContextState()

    first = state.upsert_item("file:context/USER.md", "Name: Zubin", priority="base", turn=1)
    second = state.upsert_item("file:context/USER.md", "Name: Zubin", priority="base", turn=2)

    assert first["created"] is True
    assert first["changed"] is True
    assert second["created"] is False
    assert second["changed"] is False
    assert len(state.all_items()) == 1
    assert state.get("file:context/USER.md").last_used_turn == 2


def test_context_state_upsert_replaces_item_when_content_changes():
    state = ContextState()
    state.upsert_item("file:context/USER.md", "Name: Zubin", priority="base")

    update = state.upsert_item("file:context/USER.md", "Name: Zubin Jha", priority="base")
    item = state.get("file:context/USER.md")

    assert update["created"] is False
    assert update["changed"] is True
    assert item is not None
    assert item.content == "Name: Zubin Jha"


def test_context_state_remove_and_touch():
    state = ContextState()
    state.upsert_item("memory:summary", "summary text", priority="summary")

    assert state.touch("memory:summary", turn=5) is True
    assert state.get("memory:summary").last_used_turn == 5
    assert state.remove("memory:summary") is True
    assert state.remove("memory:summary") is False
