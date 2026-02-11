"""Location tool scaffold."""

from __future__ import annotations

from typing import Any

from src.zubot.core.config_loader import get_home_location, get_timezone, load_config


def get_location() -> dict[str, Any]:
    """Return normalized location data for the current user/session.

    Intended future source order:
    1. explicit config override
    2. OS-level location/timezone signals
    3. IP geolocation fallback
    """
    payload: dict[str, Any] = {}
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    home_location = get_home_location(payload) or {}
    tz = home_location.get("timezone") if isinstance(home_location.get("timezone"), str) else None
    if tz is None:
        tz = get_timezone(payload)

    lat = home_location.get("lat")
    lon = home_location.get("lon")
    city = home_location.get("city")
    region = home_location.get("region")
    country = home_location.get("country")

    has_any_location_data = any(
        value is not None for value in (lat, lon, city, region, country, tz)
    )

    return {
        "lat": lat,
        "lon": lon,
        "city": city,
        "region": region,
        "country": country,
        "timezone": tz,
        "source": "config_home_location" if has_any_location_data else "unresolved",
    }
