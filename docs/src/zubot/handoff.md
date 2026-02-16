# Handoff (Current Checkpoint)

This file is the fastest way for a new agent to resume productive work.

## 1) Runtime Startup
- Preferred:
  - `source .venv/bin/activate`
  - `python -m src.zubot.daemon.main`
- App-only iteration:
  - `python -m uvicorn app.main:app --reload --port 8000`

## 2) Source of Truth
- Runtime config: `config/config.json` (local, ignored).
- Config schema reference: `config/example_config.json` (tracked).
- Central DB:
  - `memory/central/zubot_core.db`
  - schema file: `memory/central/zubot_core.sql`

## 3) Current Memory Policy (Important)
- Persist to `daily_memory_events`:
  - user-facing transcript only:
    - `user`
    - `main_agent`
  - central task lifecycle milestones:
    - `task_agent_event` with `run_queued`, `run_finished`, `run_failed`, `run_blocked`, `run_waiting`, `run_resumed`
- Do not persist internal/system chatter:
  - task-agent internal chatter
  - tool telemetry
  - routine runtime/system events

## 4) Central Scheduler Model
- Task profiles are DB rows in:
  - `task_profiles`
  - `task_profile_run_stats`
- Backward compatibility bootstrap:
  - if `task_profiles` is empty at startup, legacy config maps are imported once from:
    - `task_profiles.tasks`
    - `pre_defined_tasks.tasks`
- Scheduled rows live in DB tables:
  - `defined_tasks`
  - `defined_tasks_run_times`
  - `defined_tasks_days_of_week`
- Run queue/history:
  - `defined_task_runs`
  - `defined_task_run_history`
  - `task_state_kv`
  - `task_seen_items`
  - `job_applications`

## 5) Scheduled Tasks UI
- Top tabs: Chat / Scheduled Tasks.
- Task Registry panel:
  - register/edit/delete task profiles (`task_id`, `name`, `kind`, entrypoint/module, resources, timeout)
- Scheduled Tasks form:
  - constants: name, config task, mode, enabled
  - frequency mode: numeric hours/minutes inputs
  - calendar mode: per-row numeric hour/minute + AM/PM + day checkboxes
- Save errors are shown in-form (`sched-form-status`), not only in chat.

## 6) DB Integrity Notes
- Foreign keys must be enabled per SQLite connection.
- `TaskSchedulerStore` enforces `PRAGMA foreign_keys = ON;`.
- Schema init also cleans orphan child rows in:
  - `defined_tasks_run_times`
  - `defined_tasks_days_of_week`

## 7) Known Good Validation
- Latest checkpoint passed:
  - `python -m pytest -q`
  - all tests green.

## 8) Useful Reset Commands
- Rebuild DB from schema:
  - `rm -f memory/central/zubot_core.db`
  - `sqlite3 memory/central/zubot_core.db < memory/central/zubot_core.sql`
