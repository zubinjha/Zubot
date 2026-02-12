from src.zubot.core.agent_types import TaskEnvelope
from src.zubot.core.sub_agent_runner import SubAgentRunner


def test_sub_agent_runner_default_llm_success():
    def fake_llm(*, messages, model=None, **_kwargs):
        assert isinstance(messages, list)
        return {"ok": True, "text": "Worker complete."}

    runner = SubAgentRunner(llm_caller=fake_llm)
    task = TaskEnvelope.create(instructions="Summarize this", model_tier="low")
    out = runner.run_task(
        task,
        base_context={"context/AGENT.md": "worker instructions"},
    )
    assert out["ok"] is True
    assert out["result"]["status"] == "success"
    assert "Worker complete." in out["result"]["summary"]


def test_sub_agent_runner_planner_needs_user_input():
    def planner(_ctx):
        return {"kind": "respond", "text": "Need more details.", "needs_user_input": True}

    runner = SubAgentRunner(planner=planner, llm_caller=lambda **_: {"ok": True, "text": "unused"})
    task = TaskEnvelope.create(instructions="Ambiguous task")
    out = runner.run_task(task)
    assert out["ok"] is True
    assert out["result"]["status"] == "needs_user_input"
    assert out["result"]["summary"] == "Need more details."


def test_sub_agent_runner_stateless_between_runs():
    seen_counts = []

    def planner(ctx):
        seen_counts.append(ctx["context_debug"]["kept_recent_message_count"])
        return {"kind": "respond", "text": "done"}

    runner = SubAgentRunner(planner=planner, llm_caller=lambda **_: {"ok": True, "text": "unused"})
    task1 = TaskEnvelope.create(instructions="first")
    task2 = TaskEnvelope.create(instructions="second")
    out1 = runner.run_task(task1)
    out2 = runner.run_task(task2)

    assert out1["ok"] and out2["ok"]
    assert seen_counts == [1, 1]


def test_sub_agent_runner_tool_action_uses_executor():
    def planner(_ctx):
        return {"kind": "tool", "name": "mock_tool", "args": {"x": 1}}

    def executor(action):
        return {"ok": True, "tool": action["name"], "value": 7}

    runner = SubAgentRunner(
        planner=planner,
        action_executor=executor,
        llm_caller=lambda **_: {"ok": True, "text": "unused"},
    )
    task = TaskEnvelope.create(instructions="use tool")
    out = runner.run_task(task, max_steps=1)
    assert out["ok"] is False
    assert out["result"]["error"] == "step_budget_exhausted"


def test_sub_agent_runner_default_tool_loop_executes_registry_tool(monkeypatch):
    calls = {"n": 0}

    def fake_llm(*, messages, model=None, tools=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            assert isinstance(tools, list)
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
        return {"ok": True, "text": "done with tools", "tool_calls": None}

    monkeypatch.setattr(
        "src.zubot.core.tool_registry.list_tools",
        lambda **_kwargs: [
            {
                "name": "get_current_time",
                "category": "kernel",
                "description": "time",
                "parameters": {},
            }
        ],
    )
    monkeypatch.setattr("src.zubot.core.tool_registry.invoke_tool", lambda name, **kwargs: {"ok": True, "name": name, "value": "10:00"})

    runner = SubAgentRunner(llm_caller=fake_llm)
    task = TaskEnvelope.create(instructions="use tools")
    out = runner.run_task(task, max_steps=3)
    assert out["ok"] is True
    assert out["result"]["status"] == "success"
    assert out["result"]["summary"] == "done with tools"
    artifacts = out["result"]["artifacts"]
    assert any(item.get("type") == "tool_execution" for item in artifacts)


def test_sub_agent_runner_tool_access_blocks_unallowed_tool(monkeypatch):
    calls = {"n": 0}

    def fake_llm(*, messages, model=None, tools=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": True,
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_week_outlook", "arguments": "{}"},
                    }
                ],
            }
        return {"ok": True, "text": "completed", "tool_calls": None}

    monkeypatch.setattr(
        "src.zubot.core.tool_registry.list_tools",
        lambda **_kwargs: [
            {
                "name": "get_current_time",
                "category": "kernel",
                "description": "time",
                "parameters": {},
            }
        ],
    )
    runner = SubAgentRunner(llm_caller=fake_llm)
    task = TaskEnvelope.create(instructions="restricted", tool_access=["get_current_time"])
    out = runner.run_task(task, max_steps=3)
    assert out["ok"] is True
    tool_artifact = next(item for item in out["result"]["artifacts"] if item.get("type") == "tool_execution")
    assert tool_artifact["data"][0]["result_ok"] is False
