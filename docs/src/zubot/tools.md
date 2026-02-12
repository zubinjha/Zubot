# Zubot Tools

This document defines the initial tools scaffold in `src/zubot/tools/`.

## Kernel Modules
- `src/zubot/tools/kernel/filesystem.py`
  - `read_file(path)`
  - `list_dir(path=".")`
  - `path_exists(path)`
  - `stat_path(path)`
  - `write_file(path, content, ...)`
  - `append_file(path, content, ...)`
  - Enforces filesystem policy through `src/zubot/core/path_policy.py`.
- `src/zubot/tools/kernel/location.py`
  - `get_location()`
  - Returns normalized location fields (`lat`, `lon`, `city`, `region`, `country`, `timezone`, `source`).
- `src/zubot/tools/kernel/time.py`
  - `get_current_time(location=None)`
  - Returns:
    - `iso_utc` from system UTC clock
    - `iso_local` converted using location/config timezone when available
    - `human_utc` and `human_local` for display-friendly timestamps
    - `timezone` and `timezone_source` for fallback transparency
- `src/zubot/tools/kernel/web_search.py`
  - `web_search(query, count=5, country="US", search_lang="en")`
  - Uses Brave Search API and returns normalized web results (`title`, `url`, `description`, `age`, `language`).
  - Returns `source` values:
    - `brave_api`
    - `config_missing`
    - `brave_api_error`
- `src/zubot/tools/kernel/web_fetch.py`
  - `fetch_url(url)`
  - Fetches an `http/https` URL and extracts readable text.
  - Handles both `text/html` and `text/plain`.
  - Returns `source` values:
    - `web_fetch`
    - `web_fetch_error`
- `src/zubot/tools/kernel/hasdata_indeed.py`
  - `get_indeed_jobs(keyword, location)`
  - `get_indeed_job_detail(url)`
  - Uses HasData API endpoints for Indeed listing/detail retrieval.
  - Listing tool behavior is fixed internally to:
    - `domain = www.indeed.com`
    - `sort = date`
  - Returns normalized payloads with:
    - `provider` (`hasdata`)
    - `source` (`hasdata_indeed_listing`, `hasdata_indeed_job`, or `*_error`)
    - `error` when request/parsing fails
- `src/zubot/tools/kernel/weather.py`
  - `get_weather(location=None)`
  - `get_future_weather(location=None, horizon="daily", hours=24, days=7)`
  - `get_week_outlook(location=None)` for normalized daily outlook rows
  - `get_weather_24hr(location=None)` for normalized 24-hour rows
  - `get_today_weather(location=None)` for compact today summary
  - Uses Open-Meteo with location coordinates from `get_location()`.
  - Returns normalized payloads with:
    - `provider` (`open_meteo`)
    - `source` (`open_meteo`, `location_unresolved`, or `open_meteo_error`)
    - `error` when request/parsing fails

## Notes
- These are contracts-first stubs, not production integrations.
- External API/provider wiring will be added after config and context-loop foundations.
- Tools should return normalized dictionaries to keep prompt assembly stable.
- Tools should retrieve settings through `src/zubot/core/config_loader.py` (for example, timezone and home location).
- Keep personal baseline tools in `src/zubot/tools/kernel/` to avoid clutter as more specialized tool groups are added.
- Data-aware helpers live in `src/zubot/tools/data/`:
  - `read_json(path)`
  - `write_json(path, obj, ...)`
  - `search_text(query, ...)`
- Weather config lives in `config/config.json` under `weather`:
  - `base_url`
  - `temperature_unit`
  - `wind_speed_unit`
  - `precipitation_unit`
  - `timeout_sec`
- Filesystem config lives in `config/config.json` under `filesystem`:
  - `default_access`
  - `allow_read`
  - `allow_write`
  - `deny`
- Web search config lives in `config/config.json` under `web_search`:
  - `provider` (`brave`)
  - `base_url`
  - `brave_api_key`
  - `timeout_sec`
- Web fetch config lives in `config/config.json` under `web_fetch`:
  - `timeout_sec`
  - `max_chars`
  - `user_agent`
- HasData config lives in `config/config.json` under `has_data`:
  - `api_key`
  - `base_url` (default `https://api.hasdata.com`)
  - `timeout_sec`

## Registry

Primary module:
- `src/zubot/core/tool_registry.py`

Behavior:
- all kernel + data tools are registered in one place via `ToolSpec`
- registry exposes metadata as a machine-readable tool contract for model calls
- runtime dispatch should go through `invoke_tool(name, **kwargs)` instead of importing tool handlers ad hoc
- weather/time tools auto-inject `get_location()` when `location` is omitted or explicitly `null`

LLM integration:
- `app/chat_logic.py` builds OpenAI-style tool schemas from registry metadata each turn
- model tool calls are parsed and executed through `invoke_tool(...)`
- tool outputs are injected back as `role="tool"` messages before final response generation

Current registered tools:
- Kernel:
  - `get_location`
  - `get_current_time`
  - `get_weather`
  - `get_future_weather`
  - `get_today_weather`
  - `get_weather_24hr`
  - `get_week_outlook`
  - `read_file`
  - `list_dir`
  - `path_exists`
  - `stat_path`
  - `write_file`
  - `append_file`
  - `web_search`
  - `fetch_url`
  - `get_indeed_jobs`
  - `get_indeed_job_detail`
- Data:
  - `read_json`
  - `write_json`
  - `search_text`

## Chat Tool Routing

Primary module:
- `app/chat_logic.py`

Behavior rules:
- no keyword-based direct routing
- every chat request uses the LLM + tool loop
- model sees registry-backed tool schemas each turn and chooses tool calls
- runtime executes selected tools through `invoke_tool(...)` and feeds outputs back before final answer
