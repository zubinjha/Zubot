from src.zubot.core.tool_registry import ToolRegistry, ToolSpec, get_tool_registry, invoke_tool, list_tools


def test_list_tools_contains_expected_names():
    names = {entry["name"] for entry in list_tools()}
    expected = {
        "append_file",
        "fetch_url",
        "get_current_time",
        "get_future_weather",
        "get_indeed_job_detail",
        "get_indeed_jobs",
        "get_location",
        "get_today_weather",
        "get_task_agent_checkin",
        "get_weather",
        "get_weather_24hr",
        "get_week_outlook",
        "list_dir",
        "list_worker_events",
        "list_workers",
        "message_worker",
        "path_exists",
        "read_file",
        "read_json",
        "reset_worker_context",
        "spawn_worker",
        "spawn_task_agent_worker",
        "search_text",
        "stat_path",
        "get_worker",
        "cancel_worker",
        "web_search",
        "write_file",
        "write_json",
    }
    assert expected.issubset(names)
    assert "get_google_access_token" not in names
    assert "list_job_app_rows" not in names
    assert "append_job_app_row" not in names
    assert "create_local_docx" not in names
    assert "upload_file_to_google_drive" not in names
    assert "create_and_upload_docx" not in names


def test_list_tools_can_filter_by_category():
    kernel = list_tools(category="kernel")
    data = list_tools(category="data")
    assert kernel
    assert data
    assert all(item["category"] == "kernel" for item in kernel)
    assert all(item["category"] == "data" for item in data)


def test_invoke_tool_known_and_unknown():
    now = invoke_tool("get_current_time")
    assert "human_local" in now

    missing = invoke_tool("not_a_real_tool")
    assert missing["ok"] is False
    assert "Unknown tool" in missing["error"]


def test_invoke_tool_argument_validation_error_shape():
    bad = invoke_tool("read_file")
    assert bad["ok"] is False
    assert "Invalid arguments" in bad["error"]


def test_registry_get_returns_spec():
    spec = get_tool_registry().get("get_location")
    assert spec.name == "get_location"
    assert spec.category == "kernel"


def test_indeed_jobs_contract_is_keyword_and_location_only():
    spec = get_tool_registry().get("get_indeed_jobs")
    assert set(spec.parameters.keys()) == {"keyword", "location"}


def test_invoke_time_tool_uses_default_location(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "src.zubot.core.tool_registry.get_location",
        lambda: {"city": "Worthington", "timezone": "America/New_York"},
    )

    def fake_time(*, location=None):
        captured["location"] = location
        return {"ok": True, "human_local": "x"}

    temp = ToolRegistry()
    temp.register(
        ToolSpec(
            name="get_current_time",
            handler=fake_time,
            category="kernel",
            description="test",
        )
    )
    monkeypatch.setattr("src.zubot.core.tool_registry.get_tool_registry", lambda: temp)
    result = invoke_tool("get_current_time")
    assert result["ok"] is True
    assert captured["location"] == {"city": "Worthington", "timezone": "America/New_York"}


def test_invoke_weather_tool_respects_explicit_location(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "src.zubot.core.tool_registry.get_location",
        lambda: {"city": "Worthington", "timezone": "America/New_York"},
    )

    def fake_week(*, location=None):
        captured["location"] = location
        return {"ok": True, "outlook": []}

    temp = ToolRegistry()
    temp.register(
        ToolSpec(
            name="get_week_outlook",
            handler=fake_week,
            category="kernel",
            description="test",
        )
    )
    monkeypatch.setattr("src.zubot.core.tool_registry.get_tool_registry", lambda: temp)
    explicit = {"city": "Columbus", "timezone": "America/New_York"}
    result = invoke_tool("get_week_outlook", location=explicit)
    assert result["ok"] is True
    assert captured["location"] == explicit


def test_get_task_agent_checkin_tool_returns_summary(monkeypatch):
    class _FakeCentralService:
        def status(self):
            return {
                "ok": True,
                "service": {"running": True},
                "runtime": {"queued_count": 1, "running_count": 0},
                "task_agents": [
                    {
                        "profile_id": "profile_a",
                        "name": "Profile A",
                        "state": "queued",
                        "current_run_id": "run_1",
                        "current_description": "Profile A: doing queued work.",
                        "started_at": None,
                        "queue_position": 1,
                        "last_result": None,
                    }
                ],
            }

        def list_runs(self, *, limit: int = 50):
            return {"ok": True, "runs": [{"run_id": "run_1"}], "limit": limit}

    monkeypatch.setattr("src.zubot.core.tool_registry.get_central_service", lambda: _FakeCentralService())

    out = invoke_tool("get_task_agent_checkin", include_runs=True, runs_limit=5)
    assert out["ok"] is True
    assert out["source"] == "central_service_checkin"
    assert "Profile A: queued (position 1)" in out["summary"]
    assert out["runs"] == [{"run_id": "run_1"}]


def test_spawn_task_agent_worker_tool_uses_reserve(monkeypatch):
    class _FakeManager:
        def __init__(self) -> None:
            self.last = None

        def spawn_worker(self, **kwargs):
            self.last = kwargs
            return {"ok": True, "worker": {"worker_id": "worker_x"}}

    fake_manager = _FakeManager()

    monkeypatch.setattr("src.zubot.core.tool_registry.get_worker_manager", lambda: fake_manager)
    monkeypatch.setattr(
        "src.zubot.core.tool_registry.load_config",
        lambda: {"central_service": {"worker_slot_reserve_for_workers": 2}},
    )
    monkeypatch.setattr(
        "src.zubot.core.tool_registry.get_central_service_config",
        lambda cfg: cfg["central_service"],
    )

    out = invoke_tool(
        "spawn_task_agent_worker",
        title="task worker",
        instructions="run task",
        requested_by="task_agent:profile_a",
    )
    assert out["ok"] is True
    assert fake_manager.last is not None
    assert fake_manager.last["requested_by"] == "task_agent:profile_a"
    assert fake_manager.last["reserve_for_workers"] == 2
