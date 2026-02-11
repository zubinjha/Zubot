from src.zubot.core.context_policy import score_context_item, select_items_for_budget
from src.zubot.core.context_state import ContextItem


def _item(
    source_id: str,
    *,
    content: str,
    priority: str,
    token_estimate: int,
    pinned: bool = False,
    last_used_turn: int | None = None,
) -> ContextItem:
    return ContextItem(
        source_id=source_id,
        content=content,
        priority=priority,
        token_estimate=token_estimate,
        pinned=pinned,
        last_used_turn=last_used_turn,
    )


def test_score_context_item_prefers_relevant_and_recent_items():
    recent_relevant = _item(
        "supplemental:project.md",
        content="Python weather tooling",
        priority="supplemental",
        token_estimate=20,
        last_used_turn=8,
    )
    stale_irrelevant = _item(
        "supplemental:other.md",
        content="Gardening notes",
        priority="supplemental",
        token_estimate=20,
        last_used_turn=1,
    )

    score_recent = score_context_item(recent_relevant, query="python weather", current_turn=10)
    score_stale = score_context_item(stale_irrelevant, query="python weather", current_turn=10)

    assert score_recent > score_stale


def test_select_items_for_budget_keeps_required_and_best_optional():
    items = [
        _item("base:AGENT", content="agent rules", priority="base", token_estimate=20),
        _item(
            "supplemental:proj_a",
            content="python weather project",
            priority="supplemental",
            token_estimate=10,
            last_used_turn=9,
        ),
        _item(
            "supplemental:proj_b",
            content="unrelated archive",
            priority="supplemental",
            token_estimate=10,
            last_used_turn=2,
        ),
    ]

    selected = select_items_for_budget(
        items,
        max_input_tokens=30,
        query="python weather",
        current_turn=10,
    )

    assert "base:AGENT" in selected["kept_source_ids"]
    assert "supplemental:proj_a" in selected["kept_source_ids"]
    assert "supplemental:proj_b" in selected["dropped_source_ids"]


def test_select_items_for_budget_can_be_over_budget_due_to_required_items():
    items = [
        _item("base:AGENT", content="agent rules", priority="base", token_estimate=80),
        _item("base:SOUL", content="soul rules", priority="base", token_estimate=80),
        _item("supplemental:small", content="small", priority="supplemental", token_estimate=5),
    ]

    selected = select_items_for_budget(items, max_input_tokens=100)

    assert selected["within_budget"] is False
    assert "supplemental:small" in selected["dropped_source_ids"]
