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
