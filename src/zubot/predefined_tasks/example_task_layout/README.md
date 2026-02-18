# Example Task Layout

Reference-only template for creating a new predefined task package.

## Files
- `task.py.example`: starter script entrypoint.
- `task_config.json.example`: starter task-local config.
- `prompts/`: optional prompt files for LLM-driven steps.
- `assets/`: optional static files (templates/lookups).
- `state/`: optional local scratch/cache/checkpoint files.
  - recommended runtime structure:
    - `state/logs/` (append-only run logs)
    - `state/artifacts/` (generated files, exports)

## How To Use
1. Copy this folder to `src/zubot/predefined_tasks/<your_task_id>/`.
2. Rename `task.py.example` -> `task.py`.
3. Rename `task_config.json.example` -> `task_config.json`.
4. Register the task in Task Registry with entrypoint `src/zubot/predefined_tasks/<your_task_id>/task.py`.
