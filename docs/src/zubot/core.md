# Zubot Core

Core runtime assumptions and invariants live in:
- `src/zubot/core/KERNEL.md`

This area will eventually define:
- agent loop orchestration
- context assembly pipeline
- model/tool routing and safety checks

## Config Loader

Primary module:
- `src/zubot/core/config_loader.py`

Responsibilities:
- resolve config path from:
  - explicit argument
  - `ZUBOT_CONFIG_PATH` env var
  - default `config/config.json`
- load JSON config safely
- expose helper accessors for common runtime needs

Current helper surface:
- `load_config()`
- `resolve_config_path()`
- `get_timezone()`
- `get_home_location()`
- `get_model_by_alias()`
- `get_default_model()`
- `clear_config_cache()`

Design note:
- Runtime code should call this loader instead of reading `config/config.json` directly.
