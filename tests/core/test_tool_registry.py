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
        "search_text",
        "stat_path",
        "get_worker",
        "cancel_worker",
        "web_search",
        "write_file",
        "write_json",
    }
    assert expected.issubset(names)


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
