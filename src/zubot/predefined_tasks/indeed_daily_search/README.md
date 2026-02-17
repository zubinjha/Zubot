# Indeed Daily Search Task

This folder is the task-local resource/config package for `indeed_daily_search`.

- `task.py`: profile entrypoint wrapper for this task package.
- `task_config.json`: task-local settings consumed by runtime/task script.
- `prompts/`: optional prompt assets used by this task.
- `assets/`: optional static assets.
- `state/`: optional local task runtime scratch area.

Business logic script implementation is intentionally unchanged for now.
`assets/` and `prompts/` are currently scaffolding placeholders for future task-specific logic.

## Task Config Keys
- `search_profiles[]`: list of `{profile_id, keyword, location}` search definitions used for HasData listing calls.
