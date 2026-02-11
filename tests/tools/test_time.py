from src.zubot.tools.kernel.time import get_current_time


def test_get_current_time_contract_shape():
    result = get_current_time()
    expected_keys = {
        "iso_utc",
        "iso_local",
        "human_utc",
        "human_local",
        "timezone",
        "timezone_source",
        "location",
        "source",
    }
    assert set(result.keys()) == expected_keys
    assert result["source"] == "system_clock_utc"
    assert result["iso_utc"].endswith("+00:00")
    assert isinstance(result["iso_local"], str)
    assert isinstance(result["human_utc"], str)
    assert isinstance(result["human_local"], str)
    assert isinstance(result["timezone"], str)
    assert result["timezone_source"] in {
        "location_timezone",
        "invalid_timezone_fallback",
        "utc_fallback",
    }


def test_get_current_time_invalid_timezone_falls_back_to_utc():
    result = get_current_time(location={"timezone": "Not/A_Real_TZ"})
    assert result["timezone"] == "UTC"
    assert result["timezone_source"] == "invalid_timezone_fallback"
    assert result["iso_local"].endswith("+00:00")
    assert result["human_local"].endswith("UTC")
