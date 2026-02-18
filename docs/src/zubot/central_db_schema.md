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
- `src/zubot/core/daily_summary_pipeline.py`

## Current Tables

### `defined_tasks`
- `schedule_id` TEXT PK
- `profile_id` TEXT NOT NULL
- `enabled` INTEGER NOT NULL
- `mode` TEXT NOT NULL (`frequency`/`calendar`)
- `execution_order` INTEGER NOT NULL
- `misfire_policy` TEXT NOT NULL (`queue_all`/`queue_latest`/`skip`)
- `run_frequency_minutes` INTEGER
- `next_run_at` TEXT
- `last_planned_run_at` TEXT
- `last_scheduled_fire_time` TEXT
- `last_run_at` TEXT
- `last_successful_run_at` TEXT
- `last_status` TEXT
- `last_summary` TEXT
- `last_error` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Conceptual behavior:
- `next_run_at` is the scheduler cursor (UTC) for this schedule.
- Heartbeat should claim schedules where `enabled=1 AND next_run_at <= now`.
- When heartbeat enqueues a run, it should transactionally:
  - copy `next_run_at` to `last_planned_run_at`
  - compute/store the next future `next_run_at`
  - enqueue run row(s) using `planned_fire_at = last_planned_run_at`
- no-overlap rule: no new queued/running/waiting run should be created for the same task profile while one is active.
- `misfire_policy` controls missed-fire handling:
  - `queue_all`: enqueue each missed fire instant
  - `queue_latest`: enqueue only latest missed fire
  - `skip`: advance cursor without enqueuing missed fires

### `defined_tasks_run_times`
- `run_time_id` INTEGER PK AUTOINCREMENT
- `schedule_id` TEXT NOT NULL FK -> `defined_tasks(schedule_id)` (`ON DELETE CASCADE`)
- `time_of_day` TEXT NOT NULL (`HH:MM`)
- `timezone` TEXT NOT NULL
- `enabled` INTEGER NOT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### `defined_tasks_days_of_week`
- `schedule_id` TEXT NOT NULL FK -> `defined_tasks(schedule_id)` (`ON DELETE CASCADE`)
- `day_of_week` TEXT NOT NULL (`mon`..`sun`)
- `created_at` TEXT NOT NULL

### `defined_task_runs`
- `run_id` TEXT PK
- `schedule_id` TEXT NULL FK -> `defined_tasks(schedule_id)` (`ON DELETE SET NULL`)
- `profile_id` TEXT NOT NULL
- `status` TEXT NOT NULL (`queued`/`running`/`waiting_for_user`/`done`/`failed`/`blocked`)
- `planned_fire_at` TEXT
- `queued_at` TEXT NOT NULL
- `started_at` TEXT
- `finished_at` TEXT
- `summary` TEXT
- `error` TEXT
- `payload_json` TEXT NOT NULL

### `defined_task_run_history`
- `run_id` TEXT PK
- `schedule_id` TEXT NULL FK -> `defined_tasks(schedule_id)` (`ON DELETE SET NULL`)
- `profile_id` TEXT NOT NULL
- `status` TEXT NOT NULL (`done`/`failed`/`blocked`)
- `planned_fire_at` TEXT
- `queued_at` TEXT NOT NULL
- `started_at` TEXT
- `finished_at` TEXT
- `summary` TEXT
- `error` TEXT
- `payload_json` TEXT NOT NULL
- `archived_at` TEXT NOT NULL

### `scheduler_runtime_state`
- `id` TEXT PK (`main`)
- `last_heartbeat_started_at` TEXT
- `last_heartbeat_finished_at` TEXT
- `last_heartbeat_status` TEXT (`ok`/`error`)
- `last_heartbeat_error` TEXT
- `last_heartbeat_enqueued_count` INTEGER NOT NULL DEFAULT 0
- `updated_at` TEXT NOT NULL

### `task_seen_items`
- `task_id` TEXT NOT NULL
- `provider` TEXT NOT NULL
- `item_key` TEXT NOT NULL
- `metadata_json` TEXT NOT NULL
- `first_seen_at` TEXT NOT NULL
- `last_seen_at` TEXT NOT NULL
- `seen_count` INTEGER NOT NULL DEFAULT 1
- PK: (`task_id`, `provider`, `item_key`)

Used by predefined tasks (including `indeed_daily_search`) as idempotency/recency ledger.
The current recency contract for seen-key preloading is `first_seen_at DESC`.

### `day_memory_status`
- `day` TEXT PK
- `total_messages` INTEGER NOT NULL DEFAULT 0
- `last_summarized_total` INTEGER NOT NULL DEFAULT 0
- `messages_since_last_summary` INTEGER NOT NULL DEFAULT 0
- `summaries_count` INTEGER NOT NULL DEFAULT 0
- `is_finalized` INTEGER NOT NULL DEFAULT 0
- `last_summary_at` TEXT
- `last_event_at` TEXT

### `memory_summary_jobs`
- `job_id` INTEGER PK AUTOINCREMENT
- `day` TEXT NOT NULL
- `status` TEXT NOT NULL (`queued`/`running`/`done`/`failed`)
- `reason` TEXT
- `created_at` TEXT NOT NULL
- `started_at` TEXT
- `finished_at` TEXT
- `error` TEXT
- `attempt_count` INTEGER NOT NULL DEFAULT 0

### `daily_memory_events`
- `event_id` INTEGER PK AUTOINCREMENT
- `day` TEXT NOT NULL
- `event_time` TEXT NOT NULL
- `session_id` TEXT
- `kind` TEXT NOT NULL
- `text` TEXT NOT NULL
- `layer` TEXT NOT NULL (`raw`/`summary`)
- `created_at` TEXT NOT NULL

### `daily_memory_summaries`
- `day` TEXT PK
- `updated_at` TEXT NOT NULL
- `session_id` TEXT
- `text` TEXT NOT NULL

### `chat_messages`
- `message_id` INTEGER PK AUTOINCREMENT
- `session_id` TEXT NOT NULL
- `role` TEXT NOT NULL (`user`/`assistant`)
- `content` TEXT NOT NULL
- `route` TEXT
- `created_at` TEXT NOT NULL

### `job_applications`
- `job_key` TEXT PK
- `company` TEXT NOT NULL
- `job_title` TEXT NOT NULL
- `location` TEXT NOT NULL
- `date_found` TEXT NOT NULL
- `date_applied` TEXT
- `status` TEXT NOT NULL (`Recommend Apply`/`Recommend Maybe`/`Applied`/`Interviewing`/`Offer`/`Rejected`/`Closed`)
- `pay_range` TEXT
- `job_link` TEXT NOT NULL
- `source` TEXT NOT NULL
- `cover_letter` TEXT
- `notes` TEXT
- `ai_notes` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### `job_discovery`
- `task_id` TEXT NOT NULL FK -> `task_profiles(task_id)` (`ON DELETE CASCADE`)
- `job_key` TEXT NOT NULL
- `found_at` TEXT NOT NULL
- `decision` TEXT NOT NULL (`Recommend Apply`/`Recommend Maybe`/`Skip`)
- `created_at` TEXT NOT NULL
- PK: (`task_id`, `job_key`)

### `todo_items`
- `todo_item_id` INTEGER PK AUTOINCREMENT
- `parent_id` INTEGER NULL FK -> `todo_items(todo_item_id)` (`ON DELETE SET NULL`)
- `todo_item_name` TEXT NOT NULL
- `todo_item_description` TEXT NOT NULL
- `create_date` TEXT NOT NULL
- `due_date` TEXT NULL
- `priority_level` INTEGER NOT NULL (`0..10`)
- `instructions` TEXT NULL
- `notes` TEXT NULL
- `status` TEXT NOT NULL (`todo`/`started`/`finished`/`backlogged`)

## Current Indexes

- `idx_defined_tasks_enabled_order(enabled, execution_order, schedule_id)` on `defined_tasks`
- `idx_defined_tasks_next_run_at(enabled, next_run_at)` on `defined_tasks`
- `idx_defined_task_run_times_schedule_enabled(schedule_id, enabled, time_of_day)` on `defined_tasks_run_times`
- `idx_defined_tasks_days_schedule(schedule_id, day_of_week)` on `defined_tasks_days_of_week`
- `idx_defined_task_runs_status_queued_at(status, queued_at)` on `defined_task_runs`
- `idx_defined_task_runs_profile_queued_at(profile_id, queued_at)` on `defined_task_runs`
- `idx_defined_task_runs_schedule_planned_fire(schedule_id, planned_fire_at)` unique partial index on `defined_task_runs`
- `idx_defined_task_run_history_status_finished_at(status, finished_at)` on `defined_task_run_history`
- `idx_defined_task_run_history_profile_finished_at(profile_id, finished_at)` on `defined_task_run_history`
- `idx_task_profiles_kind_enabled(kind, enabled)` on `task_profiles`
- `idx_task_profiles_queue_group(queue_group)` on `task_profiles`
- `idx_task_seen_items_task_provider_first_seen(task_id, provider, first_seen_at DESC)` on `task_seen_items`
- `idx_day_memory_finalized(is_finalized)`
- `idx_memory_summary_jobs_status_created(status, created_at, job_id)`
- `idx_memory_summary_jobs_day_active(day)` with partial predicate `status IN ('queued','running')`
- `idx_daily_memory_events_day_time(day, event_time, event_id)`
- `idx_daily_memory_events_kind_day(kind, day)`
- `idx_daily_memory_events_session_event(session_id, event_id)`
- `idx_chat_messages_session_message(session_id, message_id)`
- `idx_job_discovery_found_at(found_at)`
- `idx_job_discovery_decision(decision)`
- `idx_todo_items_create_date(create_date)`
- `idx_todo_items_due_date(due_date)`
- `idx_todo_items_status(status)`

## Event Taxonomy (Raw Daily Memory)

Raw ingestion event kinds used by summarization pipeline:
- `user`
- `main_agent`
- `task_agent_event`
  - scheduler/task-agent milestones only (`run_queued`, `run_finished`, `run_failed`, `run_blocked`)

Legacy/optional kinds that may exist from older snapshots or custom writers:
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
4. If legacy `memory/daily/raw/*.md` or `memory/daily/summary/*.md` exists, runtime can import into `daily_memory_events`/`daily_memory_summaries` only when `memory.legacy_daily_file_migration_enabled=true`.

## Sample Queries

Task-agent check-in snapshot:

```sql
SELECT profile_id, status, queued_at, started_at, finished_at, summary, error
FROM defined_task_runs
WHERE profile_id = ?
ORDER BY queued_at DESC
LIMIT 20;
```

Recent run outcomes:

```sql
SELECT profile_id, status, finished_at, summary, error
FROM defined_task_run_history
ORDER BY COALESCE(finished_at, queued_at) DESC
LIMIT 50;
```

Pending days for summarization:

```sql
SELECT day, total_messages, last_summarized_total, messages_since_last_summary, summaries_count
FROM day_memory_status
WHERE total_messages > last_summarized_total
ORDER BY day ASC;
```

Queued summary jobs:

```sql
SELECT job_id, day, status, reason, created_at, started_at, finished_at, error
FROM memory_summary_jobs
ORDER BY created_at DESC
LIMIT 50;
```

Raw events for one day:

```sql
SELECT event_time, session_id, kind, text
FROM daily_memory_events
WHERE day = ? AND layer = 'raw'
ORDER BY event_time ASC, event_id ASC;
```
