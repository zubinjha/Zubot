"""Tool surface for Zubot.

These are lightweight scaffolds. Provider-specific integrations are added later.
"""

from .kernel import (
    append_file,
    append_job_app_row,
    create_and_upload_docx,
    create_local_docx,
    delete_job_app_row_by_key,
    fetch_url,
    get_current_time,
    get_future_weather,
    get_google_access_token,
    get_location,
    get_today_weather,
    get_weather,
    get_weather_24hr,
    get_week_outlook,
    list_dir,
    list_job_app_rows,
    path_exists,
    read_file,
    stat_path,
    upload_file_to_google_drive,
    web_search,
    write_file,
)
from .data import read_json, search_text, write_json

__all__ = [
    "append_file",
    "append_job_app_row",
    "create_and_upload_docx",
    "create_local_docx",
    "delete_job_app_row_by_key",
    "fetch_url",
    "get_current_time",
    "get_future_weather",
    "get_google_access_token",
    "get_location",
    "get_today_weather",
    "get_weather",
    "get_weather_24hr",
    "get_week_outlook",
    "list_dir",
    "list_job_app_rows",
    "path_exists",
    "read_file",
    "read_json",
    "search_text",
    "stat_path",
    "upload_file_to_google_drive",
    "web_search",
    "write_file",
    "write_json",
]
