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

## Path Policy

Primary module:
- `src/zubot/core/path_policy.py`

Responsibilities:
- repository-root path normalization (`normalize_repo_path`)
- safe path resolution (`resolve_repo_path`)
- filesystem policy parsing (`get_filesystem_policy`)
- access checks (`check_access`, `can_read`, `can_write`)

Filesystem policy fields (from config):
- `default_access`: `allow` or `deny`
- `allow_read`: list of glob patterns
- `allow_write`: list of glob patterns
- `deny`: denylist patterns (always takes precedence)
