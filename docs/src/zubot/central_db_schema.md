# Central DB Schema

This document defines the authoritative local SQLite schema used by central scheduling and memory status.

## Authoritative DB

Path (default):
- `memory/central/zubot_core.db`

Config:
- `central_service.scheduler_db_path`

Source modules:
- `src/zubot/core/task_scheduler_store.py`
- `src/zubot/core/memory_index.py`

## Current Tables

### `schedules`
- `schedule_id` TEXT PK
- `profile_id` TEXT NOT NULL
- `enabled` INTEGER NOT NULL
- `run_frequency_minutes` INTEGER NOT NULL
- `schedule_mode` TEXT NOT NULL (`interval`/`calendar`)
- `schedule_timezone` TEXT
- `schedule_time_of_day` TEXT (`HH:MM`)
- `schedule_days_of_week` TEXT CSV (`mon,tue,...`)
- `schedule_catch_up_window_minutes` INTEGER NOT NULL
- `schedule_max_runtime_sec` INTEGER
- `next_run_at` TEXT
- `last_scheduled_fire_time` TEXT
- `last_successful_run_at` TEXT
- `last_run_at` TEXT
- `last_status` TEXT
- `last_summary` TEXT
- `last_error` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### `runs`
- `run_id` TEXT PK
- `schedule_id` TEXT NULL FK -> `schedules(schedule_id)` (`ON DELETE SET NULL`)
- `profile_id` TEXT NOT NULL
- `status` TEXT NOT NULL (`queued`/`running`/`done`/`failed`/`blocked`)
- `queued_at` TEXT NOT NULL
- `started_at` TEXT
- `finished_at` TEXT
- `summary` TEXT
- `error` TEXT
- `payload_json` TEXT NOT NULL

### `run_history`
- `run_id` TEXT PK
- `schedule_id` TEXT NULL FK -> `schedules(schedule_id)` (`ON DELETE SET NULL`)
- `profile_id` TEXT NOT NULL
- `status` TEXT NOT NULL (`done`/`failed`/`blocked`)
- `queued_at` TEXT NOT NULL
- `started_at` TEXT
- `finished_at` TEXT
- `summary` TEXT
- `error` TEXT
- `payload_json` TEXT NOT NULL
- `archived_at` TEXT NOT NULL

### `day_memory_status`
- `day` TEXT PK
- `messages_since_last_summary` INTEGER NOT NULL DEFAULT 0
- `summaries_count` INTEGER NOT NULL DEFAULT 0
- `is_finalized` INTEGER NOT NULL DEFAULT 0
- `last_summary_at` TEXT
- `last_event_at` TEXT

## Current Indexes

- `idx_runs_status_queued_at(status, queued_at)`
- `idx_runs_profile_queued_at(profile_id, queued_at)`
- `idx_run_history_status_finished_at(status, finished_at)`
- `idx_run_history_profile_finished_at(profile_id, finished_at)`
- `idx_day_memory_finalized(is_finalized)`

## Event Taxonomy (Raw Daily Memory)

Raw ingestion event kinds used by summarization pipeline:
- `user`
- `main_agent`
- `worker_event`
- `task_agent_event`
- `tool_event`
- `system`

## Legacy Migration Behavior

### Legacy files
- previous scheduler filename: `memory/central/zubot_core.sqlite3`
- previous memory index file: `memory/memory_index.sqlite3`

### Runtime migration rules
1. If configured DB path ends with `.db` and sibling `.sqlite3` exists, scheduler store copies the legacy DB to the `.db` path once.
2. Memory-index schema init creates/uses `day_memory_status` in the central DB.
3. If legacy `memory/memory_index.sqlite3` exists, `day_memory_status` rows are upsert-imported into the central DB.

## Sample Queries

Task-agent check-in snapshot:

```sql
SELECT profile_id, status, queued_at, started_at, finished_at, summary, error
FROM runs
WHERE profile_id = ?
ORDER BY queued_at DESC
LIMIT 20;
```

Recent run outcomes:

```sql
SELECT profile_id, status, finished_at, summary, error
FROM run_history
ORDER BY COALESCE(finished_at, queued_at) DESC
LIMIT 50;
```

Pending days for summarization:

```sql
SELECT day, messages_since_last_summary, summaries_count
FROM day_memory_status
WHERE messages_since_last_summary > 0
ORDER BY day ASC;
```
