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
