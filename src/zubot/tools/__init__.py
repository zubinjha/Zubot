"""Tool surface for Zubot.

These are lightweight scaffolds. Provider-specific integrations are added later.
"""

from .kernel import (
    append_file,
    get_current_time,
    get_future_weather,
    get_location,
    get_today_weather,
    get_weather,
    get_weather_24hr,
    get_week_outlook,
    list_dir,
    path_exists,
    read_file,
    stat_path,
    write_file,
)
from .data import read_json, search_text, write_json

__all__ = [
    "append_file",
    "get_current_time",
    "get_future_weather",
    "get_location",
    "get_today_weather",
    "get_weather",
    "get_weather_24hr",
    "get_week_outlook",
    "list_dir",
    "path_exists",
    "read_file",
    "read_json",
    "search_text",
    "stat_path",
    "write_file",
    "write_json",
]
