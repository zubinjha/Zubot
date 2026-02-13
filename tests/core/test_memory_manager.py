from src.zubot.core.memory_index import get_day_status, increment_day_message_count
from src.zubot.core.memory_manager import MemoryManager, MemoryManagerSettings


def test_memory_manager_sweeps_pending_previous_days(tmp_path):
    increment_day_message_count(day="2026-02-10", amount=3, root=tmp_path)
    manager = MemoryManager(root=tmp_path)

    out = manager.sweep_pending_previous_days(session_id="test")
    assert out["ok"] is True
    assert "2026-02-10" in out["finalized_days"]

    status = get_day_status(day="2026-02-10", root=tmp_path)
    assert status is not None
    assert status["messages_since_last_summary"] == 0
    assert status["is_finalized"] is True


def test_memory_manager_completion_sweep_is_debounced(tmp_path):
    increment_day_message_count(day="2026-02-10", amount=1, root=tmp_path)
    manager = MemoryManager(root=tmp_path)
    settings = MemoryManagerSettings(sweep_interval_sec=3600, completion_debounce_sec=3600)

    first = manager.maybe_completion_sweep(settings=settings)
    second = manager.maybe_completion_sweep(settings=settings)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second.get("skipped") is True
    assert second.get("reason") == "completion_debounce"
