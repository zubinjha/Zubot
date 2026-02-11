import pytest

from src.zubot.core.agent_types import SessionEvent, TaskEnvelope, WorkerResult


def test_task_envelope_create_defaults():
    task = TaskEnvelope.create(instructions="Research current API limits")
    assert task.task_id.startswith("task_")
    assert task.requested_by == "main_agent"
    assert task.model_tier == "medium"
    assert task.instructions == "Research current API limits"


def test_task_envelope_invalid_model_tier_raises():
    with pytest.raises(ValueError, match="model_tier"):
        TaskEnvelope(
            task_id="task_1",
            requested_by="main_agent",
            instructions="x",
            model_tier="ultra",  # type: ignore[arg-type]
        )


def test_worker_result_failed_requires_error():
    with pytest.raises(ValueError, match="required"):
        WorkerResult(task_id="task_1", status="failed", summary="failed but no error")


def test_session_event_valid_types():
    event = SessionEvent(
        session_id="sess_1",
        event_type="tool_call",
        payload={"tool": "read_file"},
    )
    assert event.event_id.startswith("evt_")
    assert event.to_dict()["event_type"] == "tool_call"
