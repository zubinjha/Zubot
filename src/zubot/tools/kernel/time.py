"""Time tool scaffold."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .location import get_location


def get_current_time(location: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return current UTC and local time from system clock + timezone context."""
    resolved_location = location or get_location()
    requested_tz = resolved_location.get("timezone")

    utc_now = datetime.now(timezone.utc)
    local_tz_name = "UTC"
    local_source = "utc_fallback"
    local_now = utc_now

    if isinstance(requested_tz, str) and requested_tz:
        try:
            local_tz = ZoneInfo(requested_tz)
            local_now = utc_now.astimezone(local_tz)
            local_tz_name = requested_tz
            local_source = "location_timezone"
        except ZoneInfoNotFoundError:
            local_tz_name = "UTC"
            local_source = "invalid_timezone_fallback"

    return {
        "iso_utc": utc_now.isoformat(),
        "iso_local": local_now.isoformat(),
        "human_utc": utc_now.strftime("%Y-%m-%d %I:%M:%S %p UTC"),
        "human_local": local_now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "timezone": local_tz_name,
        "timezone_source": local_source,
        "location": resolved_location,
        "source": "system_clock_utc",
    }
