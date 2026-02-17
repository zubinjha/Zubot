from src.zubot.core.control_protocol import CONTROL_REQUEST_BEGIN, CONTROL_REQUEST_END, extract_control_requests, is_expired


def test_extract_control_requests_parses_valid_block():
    text = (
        "Please approve this action.\n"
        f"{CONTROL_REQUEST_BEGIN}\n"
        '{"action_id":"act_1","action":"enqueue_task","title":"Run Indeed Task","risk_level":"high","payload":{"task_id":"indeed_daily_search"}}\n'
        f"{CONTROL_REQUEST_END}"
    )
    out = extract_control_requests(text, default_route="llm.main_agent")
    assert len(out) == 1
    assert out[0]["action_id"] == "act_1"
    assert out[0]["action"] == "enqueue_task"
    assert out[0]["requested_by_route"] == "llm.main_agent"


def test_extract_control_requests_skips_invalid_blocks():
    text = (
        f"{CONTROL_REQUEST_BEGIN}\n"
        '{"action_id":"act_bad","action":"unknown","payload":{}}\n'
        f"{CONTROL_REQUEST_END}"
    )
    out = extract_control_requests(text)
    assert out == []


def test_is_expired_handles_iso_timestamp():
    assert is_expired("2000-01-01T00:00:00+00:00") is True
    assert is_expired("2999-01-01T00:00:00+00:00") is False

