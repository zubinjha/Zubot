from src.zubot.tools.kernel.weather import (
    get_future_weather,
    get_today_weather,
    get_weather,
    get_weather_24hr,
    get_week_outlook,
)


def test_get_weather_contract_shape_when_location_unresolved():
    result = get_weather(location={"lat": None, "lon": None, "timezone": "America/New_York"})
    expected_keys = {"location", "current", "units", "provider", "source", "error"}
    assert set(result.keys()) == expected_keys
    assert result["source"] == "location_unresolved"
    assert result["provider"] == "open_meteo"
    assert result["error"] is None


def test_get_weather_reads_current_payload(monkeypatch):
    def fake_fetch_json(url: str, timeout_sec: int = 10):
        assert "api.open-meteo.com" in url
        return {
            "current": {
                "temperature_2m": 31.2,
                "wind_speed_10m": 7.1,
                "weather_code": 1,
            }
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather._fetch_json", fake_fetch_json)
    result = get_weather(location={"lat": 40.0931, "lon": -83.0170, "timezone": "America/New_York"})
    assert result["source"] == "open_meteo"
    assert result["error"] is None
    assert result["current"]["temperature_2m"] == 31.2


def test_get_future_weather_contract_shape_hourly(monkeypatch):
    def fake_fetch_json(url: str, timeout_sec: int = 10):
        assert "hourly=" in url
        return {
            "hourly": {
                "time": ["2026-02-11T15:00", "2026-02-11T16:00", "2026-02-11T17:00"],
                "temperature_2m": [30.1, 29.9, 29.4],
                "weather_code": [1, 2, 3],
            }
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather._fetch_json", fake_fetch_json)
    result = get_future_weather(
        location={"lat": 40.0931, "lon": -83.0170, "timezone": "America/New_York"},
        horizon="hourly",
        hours=2,
    )
    expected_keys = {
        "location",
        "horizon",
        "hours",
        "days",
        "forecast",
        "units",
        "provider",
        "source",
        "error",
    }
    assert set(result.keys()) == expected_keys
    assert result["horizon"] == "hourly"
    assert result["hours"] == 2
    assert result["days"] == 7
    assert isinstance(result["forecast"], list)
    assert len(result["forecast"]) == 2
    assert result["forecast"][0]["time"] == "2026-02-11T15:00"
    assert result["source"] == "open_meteo"
    assert result["error"] is None


def test_get_future_weather_contract_shape_daily(monkeypatch):
    def fake_fetch_json(url: str, timeout_sec: int = 10):
        assert "daily=" in url
        return {
            "daily": {
                "time": ["2026-02-11", "2026-02-12", "2026-02-13"],
                "temperature_2m_max": [38.0, 40.0, 35.0],
            }
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather._fetch_json", fake_fetch_json)
    result = get_future_weather(
        location={"lat": 40.0931, "lon": -83.0170, "timezone": "America/New_York"},
        horizon="daily",
        days=2,
    )
    assert result["horizon"] == "daily"
    assert len(result["forecast"]) == 2
    assert result["forecast"][1]["time"] == "2026-02-12"
    assert result["source"] == "open_meteo"
    assert result["error"] is None


def test_get_week_outlook_normalized(monkeypatch):
    def fake_get_future_weather(**kwargs):
        return {
            "location": {"city": "Worthington"},
            "units": {"temperature": "fahrenheit"},
            "provider": "open_meteo",
            "source": "open_meteo",
            "error": None,
            "forecast": [
                {
                    "time": "2026-02-11",
                    "temperature_2m_max": 41.0,
                    "temperature_2m_min": 27.0,
                    "precipitation_probability_max": 20,
                    "precipitation_sum": 0.1,
                    "wind_speed_10m_max": 12.0,
                    "weather_code": 2,
                    "sunrise": "2026-02-11T07:22",
                    "sunset": "2026-02-11T18:02",
                }
            ],
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather.get_future_weather", fake_get_future_weather)
    result = get_week_outlook()
    assert result["days"] == 7
    assert result["outlook"][0]["date"] == "2026-02-11"
    assert result["outlook"][0]["high"] == 41.0
    assert result["outlook"][0]["sunrise"] == "2026-02-11T07:22"


def test_get_weather_24hr_normalized(monkeypatch):
    def fake_get_future_weather(**kwargs):
        return {
            "location": {"city": "Worthington"},
            "units": {"temperature": "fahrenheit"},
            "provider": "open_meteo",
            "source": "open_meteo",
            "error": None,
            "forecast": [
                {
                    "time": "2026-02-11T15:00",
                    "temperature_2m": 33.0,
                    "apparent_temperature": 31.0,
                    "precipitation_probability": 10,
                    "precipitation": 0.0,
                    "wind_speed_10m": 8.0,
                    "weather_code": 1,
                }
            ],
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather.get_future_weather", fake_get_future_weather)
    result = get_weather_24hr()
    assert result["hours"] == 24
    assert result["hourly"][0]["time"] == "2026-02-11T15:00"
    assert result["hourly"][0]["temp"] == 33.0
    assert result["hourly"][0]["wind"] == 8.0


def test_get_today_weather_summary(monkeypatch):
    def fake_get_future_weather(**kwargs):
        return {
            "location": {"city": "Worthington"},
            "units": {"temperature": "fahrenheit"},
            "provider": "open_meteo",
            "source": "open_meteo",
            "error": None,
            "forecast": [
                {
                    "time": "2026-02-11",
                    "temperature_2m_max": 43.0,
                    "temperature_2m_min": 29.0,
                    "precipitation_probability_max": 25,
                    "precipitation_sum": 0.05,
                    "wind_speed_10m_max": 11.0,
                    "sunrise": "2026-02-11T07:22",
                    "sunset": "2026-02-11T18:02",
                    "weather_code": 3,
                }
            ],
        }

    monkeypatch.setattr("src.zubot.tools.kernel.weather.get_future_weather", fake_get_future_weather)
    result = get_today_weather()
    assert result["date"] == "2026-02-11"
    assert result["high"] == 43.0
    assert result["low"] == 29.0
    assert result["sunrise"] == "2026-02-11T07:22"
    assert result["sunset"] == "2026-02-11T18:02"
