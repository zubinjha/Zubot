"""Weather tools backed by Open-Meteo."""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen
from typing import Any, Literal

from src.zubot.core.config_loader import load_config

from .location import get_location

WeatherHorizon = Literal["hourly", "daily"]
DEFAULT_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _fetch_json(url: str, timeout_sec: int = 10) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Weather API response must be a JSON object.")
    return payload


def _weather_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    weather = payload.get("weather")
    weather_config = weather if isinstance(weather, dict) else {}

    return {
        "base_url": weather_config.get("base_url", DEFAULT_OPEN_METEO_URL),
        "temperature_unit": weather_config.get("temperature_unit", "fahrenheit"),
        "wind_speed_unit": weather_config.get("wind_speed_unit", "mph"),
        "precipitation_unit": weather_config.get("precipitation_unit", "inch"),
        "timeout_sec": int(weather_config.get("timeout_sec", 10)),
    }


def _to_forecast_rows(block: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    if not isinstance(block, dict):
        return []

    times = block.get("time")
    if not isinstance(times, list):
        return []

    rows: list[dict[str, Any]] = []
    for idx, timestamp in enumerate(times[:limit]):
        row: dict[str, Any] = {"time": timestamp}
        for field, values in block.items():
            if field == "time" or not isinstance(values, list):
                continue
            if idx < len(values):
                row[field] = values[idx]
        rows.append(row)
    return rows


def get_weather(location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return current weather conditions for a resolved location using Open-Meteo."""
    resolved_location = location or get_location()
    lat = resolved_location.get("lat")
    lon = resolved_location.get("lon")
    timezone = resolved_location.get("timezone")
    settings = _weather_settings()

    if lat is None or lon is None:
        return {
            "location": resolved_location,
            "current": None,
            "units": {
                "temperature": settings["temperature_unit"],
                "wind_speed": settings["wind_speed_unit"],
                "precipitation": settings["precipitation_unit"],
            },
            "provider": "open_meteo",
            "source": "location_unresolved",
            "error": None,
        }

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "is_day",
            ]
        ),
        "temperature_unit": settings["temperature_unit"],
        "wind_speed_unit": settings["wind_speed_unit"],
        "precipitation_unit": settings["precipitation_unit"],
        "timezone": timezone or "auto",
    }
    url = f"{settings['base_url']}?{urlencode(params)}"

    try:
        payload = _fetch_json(url, timeout_sec=settings["timeout_sec"])
    except Exception as exc:  # pragma: no cover - covered by contract behavior tests
        return {
            "location": resolved_location,
            "current": None,
            "units": {
                "temperature": settings["temperature_unit"],
                "wind_speed": settings["wind_speed_unit"],
                "precipitation": settings["precipitation_unit"],
            },
            "provider": "open_meteo",
            "source": "open_meteo_error",
            "error": str(exc),
        }

    return {
        "location": resolved_location,
        "current": payload.get("current"),
        "units": {
            "temperature": settings["temperature_unit"],
            "wind_speed": settings["wind_speed_unit"],
            "precipitation": settings["precipitation_unit"],
        },
        "provider": "open_meteo",
        "source": "open_meteo",
        "error": None,
    }


def get_future_weather(
    location: dict[str, Any] | None = None,
    *,
    horizon: WeatherHorizon = "daily",
    hours: int = 24,
    days: int = 7,
) -> dict[str, Any]:
    """Return future forecast data for hourly or daily horizons via Open-Meteo."""
    resolved_location = location or get_location()
    lat = resolved_location.get("lat")
    lon = resolved_location.get("lon")
    timezone = resolved_location.get("timezone")
    settings = _weather_settings()

    if lat is None or lon is None:
        return {
            "location": resolved_location,
            "horizon": horizon,
            "hours": hours,
            "days": days,
            "forecast": [],
            "units": {
                "temperature": settings["temperature_unit"],
                "wind_speed": settings["wind_speed_unit"],
                "precipitation": settings["precipitation_unit"],
            },
            "provider": "open_meteo",
            "source": "location_unresolved",
            "error": None,
        }

    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "temperature_unit": settings["temperature_unit"],
        "wind_speed_unit": settings["wind_speed_unit"],
        "precipitation_unit": settings["precipitation_unit"],
        "timezone": timezone or "auto",
    }
    if horizon == "hourly":
        params["hourly"] = ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "precipitation_probability",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ]
        )
        params["forecast_days"] = max(1, min(16, ((hours - 1) // 24) + 1))
    else:
        params["daily"] = ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "wind_speed_10m_max",
                "sunrise",
                "sunset",
            ]
        )
        params["forecast_days"] = max(1, min(16, days))

    url = f"{settings['base_url']}?{urlencode(params)}"

    try:
        payload = _fetch_json(url, timeout_sec=settings["timeout_sec"])
    except Exception as exc:  # pragma: no cover - covered by contract behavior tests
        return {
            "location": resolved_location,
            "horizon": horizon,
            "hours": hours,
            "days": days,
            "forecast": [],
            "units": {
                "temperature": settings["temperature_unit"],
                "wind_speed": settings["wind_speed_unit"],
                "precipitation": settings["precipitation_unit"],
            },
            "provider": "open_meteo",
            "source": "open_meteo_error",
            "error": str(exc),
        }

    key = "hourly" if horizon == "hourly" else "daily"
    limit = hours if horizon == "hourly" else days
    forecast_rows = _to_forecast_rows(payload.get(key, {}), limit=limit)

    return {
        "location": resolved_location,
        "horizon": horizon,
        "hours": hours,
        "days": days,
        "forecast": forecast_rows,
        "units": {
            "temperature": settings["temperature_unit"],
            "wind_speed": settings["wind_speed_unit"],
            "precipitation": settings["precipitation_unit"],
        },
        "provider": "open_meteo",
        "source": "open_meteo",
        "error": None,
    }


def get_week_outlook(location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return 7-day weather outlook with normalized daily fields."""
    payload = get_future_weather(location=location, horizon="daily", days=7)
    outlook: list[dict[str, Any]] = []
    for row in payload.get("forecast", []):
        if not isinstance(row, dict):
            continue
        outlook.append(
            {
                "date": row.get("time"),
                "high": row.get("temperature_2m_max"),
                "low": row.get("temperature_2m_min"),
                "precip_probability": row.get("precipitation_probability_max"),
                "precip_total": row.get("precipitation_sum"),
                "wind_max": row.get("wind_speed_10m_max"),
                "weather_code": row.get("weather_code"),
                "sunrise": row.get("sunrise"),
                "sunset": row.get("sunset"),
            }
        )

    return {
        "location": payload.get("location"),
        "days": 7,
        "outlook": outlook,
        "units": payload.get("units"),
        "provider": payload.get("provider"),
        "source": payload.get("source"),
        "error": payload.get("error"),
    }


def get_weather_24hr(location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return 24-hour weather outlook with normalized hourly fields."""
    payload = get_future_weather(location=location, horizon="hourly", hours=24)
    hourly: list[dict[str, Any]] = []
    for row in payload.get("forecast", []):
        if not isinstance(row, dict):
            continue
        hourly.append(
            {
                "time": row.get("time"),
                "temp": row.get("temperature_2m"),
                "feels_like": row.get("apparent_temperature"),
                "precip_probability": row.get("precipitation_probability"),
                "precip": row.get("precipitation"),
                "wind": row.get("wind_speed_10m"),
                "weather_code": row.get("weather_code"),
            }
        )

    return {
        "location": payload.get("location"),
        "hours": 24,
        "hourly": hourly,
        "units": payload.get("units"),
        "provider": payload.get("provider"),
        "source": payload.get("source"),
        "error": payload.get("error"),
    }


def get_today_weather(location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compact summary for today's weather."""
    payload = get_future_weather(location=location, horizon="daily", days=1)
    today = payload.get("forecast", [])
    first = today[0] if isinstance(today, list) and today else {}
    if not isinstance(first, dict):
        first = {}

    return {
        "location": payload.get("location"),
        "date": first.get("time"),
        "high": first.get("temperature_2m_max"),
        "low": first.get("temperature_2m_min"),
        "precip_probability": first.get("precipitation_probability_max"),
        "precip_total": first.get("precipitation_sum"),
        "wind_max": first.get("wind_speed_10m_max"),
        "sunrise": first.get("sunrise"),
        "sunset": first.get("sunset"),
        "weather_code": first.get("weather_code"),
        "units": payload.get("units"),
        "provider": payload.get("provider"),
        "source": payload.get("source"),
        "error": payload.get("error"),
    }
