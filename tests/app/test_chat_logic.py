from app.chat_logic import handle_chat_message


def test_handle_chat_message_empty():
    result = handle_chat_message("   ", allow_llm_fallback=False)
    assert not result["ok"]
    assert result["route"] == "validation"


def test_handle_chat_message_time_route():
    result = handle_chat_message("what time is it?", allow_llm_fallback=False)
    assert result["ok"]
    assert result["route"] == "direct_tool.time"
    assert "Current local time" in result["reply"]


def test_handle_chat_message_direct_fallback():
    result = handle_chat_message("tell me a joke", allow_llm_fallback=False)
    assert result["ok"]
    assert result["route"] == "direct_fallback"
