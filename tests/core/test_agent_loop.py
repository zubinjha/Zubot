from src.zubot.core.agent_loop import AgentLoop
from src.zubot.core.sub_agent_runner import SubAgentRunner


def test_agent_loop_stops_on_final_response():
    def planner(_context):
        return {"kind": "tool", "name": "mock"}

    def executor(_action):
        return {"final_response": "Done."}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(session_id="sess_1", user_text="hi")
    assert result["ok"]
    assert result["stop_reason"] == "final_response"
    assert result["response"] == "Done."
    assert result["tool_calls"] == 1


def test_agent_loop_stops_when_user_input_needed():
    def planner(_context):
        return {"kind": "spawn_sub_agent", "task": "ask question"}

    def executor(_action):
        return {"type": "worker_result", "needs_user_input": True}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(session_id="sess_1", user_text="hi")
    assert result["stop_reason"] == "needs_user_input"
    assert result["response"] is None


def test_agent_loop_stops_on_step_budget():
    def planner(_context):
        return {"kind": "noop"}

    def executor(_action):
        return {"progress": "continue"}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(session_id="sess_1", user_text="hi", max_steps=2)
    assert result["stop_reason"] == "step_budget_exhausted"
    assert result["steps"] == 2


def test_agent_loop_stops_on_tool_budget():
    def planner(_context):
        return {"kind": "tool", "name": "a"}

    def executor(_action):
        return {"progress": "continue"}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(session_id="sess_1", user_text="hi", max_steps=5, max_tool_calls=1)
    assert result["stop_reason"] == "tool_call_budget_exhausted"


def test_agent_loop_stops_on_context_budget():
    def planner(_context):
        return {"kind": "noop"}

    def executor(_action):
        return {"progress": "continue"}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(
        session_id="sess_1",
        user_text="x" * 5000,
        max_steps=2,
        max_context_tokens=100,
        reserved_output_tokens=20,
    )
    assert result["stop_reason"] == "context_budget_exhausted"


def test_agent_loop_persists_events(tmp_path):
    def planner(_context):
        return {"kind": "tool", "name": "mock"}

    def executor(_action):
        return {"final_response": "Done."}

    loop = AgentLoop(planner=planner, action_executor=executor)
    result = loop.run_turn(
        session_id="sess_store",
        user_text="hi",
        persist_events=True,
        events_base_dir=str(tmp_path / "sessions"),
    )
    assert result["stop_reason"] == "final_response"
    log = (tmp_path / "sessions" / "sess_store.jsonl")
    assert log.exists()


def test_agent_loop_spawn_sub_agent_with_runner_success():
    def planner(_context):
        return {"kind": "spawn_sub_agent", "task": "do work", "model_tier": "low"}

    def executor(_action):
        return {"progress": "unused"}

    def fake_llm(*, messages, model=None, **_kwargs):
        assert isinstance(messages, list)
        return {"ok": True, "text": "worker finished"}

    sub_runner = SubAgentRunner(llm_caller=fake_llm)
    loop = AgentLoop(planner=planner, action_executor=executor, sub_agent_runner=sub_runner)
    result = loop.run_turn(session_id="sess_1", user_text="hi")
    assert result["stop_reason"] == "final_response"
    assert result["response"] == "worker finished"


def test_agent_loop_spawn_sub_agent_with_runner_needs_user_input():
    def planner(_context):
        return {"kind": "spawn_sub_agent", "task": "clarify task"}

    def worker_planner(_context):
        return {"kind": "respond", "text": "Need clarification", "needs_user_input": True}

    def executor(_action):
        return {"progress": "unused"}

    sub_runner = SubAgentRunner(planner=worker_planner, llm_caller=lambda **_: {"ok": True, "text": "unused"})
    loop = AgentLoop(planner=planner, action_executor=executor, sub_agent_runner=sub_runner)
    result = loop.run_turn(session_id="sess_1", user_text="hi")
    assert result["stop_reason"] == "needs_user_input"
