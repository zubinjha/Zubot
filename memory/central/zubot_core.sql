-- Zubot Core SQLite schema
-- Canonical local schema for scheduler + memory tables.

PRAGMA foreign_keys = ON;

-- Scheduler definitions: each row describes a recurring schedule mapped to one task profile.
-- Heartbeat evaluates these rows to decide when runs should be enqueued.
CREATE TABLE IF NOT EXISTS defined_tasks (
    schedule_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    mode TEXT NOT NULL DEFAULT 'frequency' CHECK (mode IN ('frequency', 'calendar')), -- frequency | calendar
    execution_order INTEGER NOT NULL DEFAULT 100 CHECK (execution_order >= 0),
    misfire_policy TEXT NOT NULL DEFAULT 'queue_latest' CHECK (misfire_policy IN ('queue_all', 'queue_latest', 'skip')),
    run_frequency_minutes INTEGER CHECK (run_frequency_minutes IS NULL OR run_frequency_minutes > 0),
    next_run_at TEXT, -- scheduler cursor in UTC; heartbeat compares this to now
    last_planned_run_at TEXT, -- most recent fire-time cursor that heartbeat advanced/enqueued
    last_scheduled_fire_time TEXT,
    last_run_at TEXT,
    last_successful_run_at TEXT,
    last_status TEXT,
    last_summary TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Calendar trigger times for schedule rows.
-- Allows one schedule to define multiple wall-clock fire times.
CREATE TABLE IF NOT EXISTS defined_tasks_run_times (
    run_time_id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id TEXT NOT NULL,
    time_of_day TEXT NOT NULL, -- HH:MM
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE CASCADE,
    UNIQUE (schedule_id, time_of_day, timezone)
);

-- Weekday constraints for schedule rows.
-- Limits calendar schedules to specific days (mon..sun).
CREATE TABLE IF NOT EXISTS defined_tasks_days_of_week (
    schedule_id TEXT NOT NULL,
    day_of_week TEXT NOT NULL CHECK (day_of_week IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')), -- mon..sun
    created_at TEXT NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week),
    FOREIGN KEY (schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE CASCADE
);

-- Active/nearline run queue for task execution lifecycle.
-- Holds queued/running/waiting and recently completed rows before archival/pruning.
CREATE TABLE IF NOT EXISTS defined_task_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_for_user', 'done', 'failed', 'blocked')), -- queued | running | waiting_for_user | done | failed | blocked
    planned_fire_at TEXT, -- canonical scheduled fire instant for dedupe/audit
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,
    error TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE SET NULL
);

-- Historical archive of terminal run outcomes.
-- Used for observability and retention-managed execution history.
CREATE TABLE IF NOT EXISTS defined_task_run_history (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('done', 'failed', 'blocked')), -- done | failed | blocked
    planned_fire_at TEXT, -- copied from run row for historical analytics
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,
    error TEXT,
    payload_json TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    FOREIGN KEY (schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE SET NULL
);

-- Heartbeat/runtime checkpoint metadata for scheduler loop health.
-- Keeps latest heartbeat run times, status, and enqueue count.
CREATE TABLE IF NOT EXISTS scheduler_runtime_state (
    id TEXT PRIMARY KEY,
    last_heartbeat_started_at TEXT,
    last_heartbeat_finished_at TEXT,
    last_heartbeat_status TEXT,
    last_heartbeat_error TEXT,
    last_heartbeat_enqueued_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Task registry/source of truth for executable task definitions.
-- Stores script/agentic/interactive task metadata used by scheduler + runner.
CREATE TABLE IF NOT EXISTS task_profiles (
    task_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('script', 'agentic', 'interactive_wrapper')),
    entrypoint_path TEXT,
    module TEXT,
    resources_path TEXT,
    queue_group TEXT,
    timeout_sec INTEGER,
    retry_policy_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'config',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-task aggregate run stats and latest run markers.
-- Supports quick status panels without scanning full run history.
CREATE TABLE IF NOT EXISTS task_profile_run_stats (
    task_id TEXT PRIMARY KEY,
    last_queued_at TEXT,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_status TEXT,
    last_run_id TEXT,
    run_count_total INTEGER NOT NULL DEFAULT 0,
    run_count_done INTEGER NOT NULL DEFAULT 0,
    run_count_failed INTEGER NOT NULL DEFAULT 0,
    run_count_blocked INTEGER NOT NULL DEFAULT 0,
    run_count_waiting INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES task_profiles(task_id) ON DELETE CASCADE
);

-- Generic per-task key/value state store.
-- Used for checkpoints/cursors that task code can atomically update.
CREATE TABLE IF NOT EXISTS task_state_kv (
    task_id TEXT NOT NULL,
    state_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (task_id, state_key)
);

-- Idempotency ledger for externally discovered items.
-- Tracks "seen" entities per task/provider/item key to avoid duplicate processing.
CREATE TABLE IF NOT EXISTS task_seen_items (
    task_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    item_key TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (task_id, provider, item_key)
);

-- Local canonical job-application records.
-- Mirrors spreadsheet-oriented columns plus local auditing timestamps.
CREATE TABLE IF NOT EXISTS job_applications (
    job_key TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    location TEXT NOT NULL,
    date_found TEXT NOT NULL,
    date_applied TEXT,
    status TEXT NOT NULL CHECK (status IN ('Recommend Apply', 'Recommend Maybe', 'Applied', 'Interviewing', 'Offer', 'Rejected', 'Closed')),
    pay_range TEXT,
    job_link TEXT NOT NULL,
    source TEXT NOT NULL,
    cover_letter TEXT,
    notes TEXT,
    ai_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Lean task-scoped job discovery triage records.
-- Stores minimal first-pass decision outcomes before full application tracking.
CREATE TABLE IF NOT EXISTS job_discovery (
    task_id TEXT NOT NULL,
    job_key TEXT NOT NULL,
    found_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('Recommend Apply', 'Recommend Maybe', 'Skip')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, job_key),
    FOREIGN KEY (task_id) REFERENCES task_profiles(task_id) ON DELETE CASCADE
);

-- Nested todo checklist items for personal task tracking.
-- Supports hierarchical parent/child todo trees via self-referential FK.
CREATE TABLE IF NOT EXISTS todo_items (
    todo_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    todo_item_name TEXT NOT NULL,
    todo_item_description TEXT NOT NULL,
    create_date TEXT NOT NULL,
    due_date TEXT,
    priority_level INTEGER NOT NULL DEFAULT 5 CHECK (priority_level >= 0 AND priority_level <= 10),
    instructions TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'started', 'finished', 'backlogged')),
    FOREIGN KEY (parent_id) REFERENCES todo_items(todo_item_id) ON DELETE SET NULL
);

-- Per-day memory processing counters/status.
-- Tracks summarization progress and finalization for each day.
CREATE TABLE IF NOT EXISTS day_memory_status (
    day TEXT PRIMARY KEY,
    total_messages INTEGER NOT NULL DEFAULT 0,
    last_summarized_total INTEGER NOT NULL DEFAULT 0,
    messages_since_last_summary INTEGER NOT NULL DEFAULT 0,
    summaries_count INTEGER NOT NULL DEFAULT 0,
    is_finalized INTEGER NOT NULL DEFAULT 0,
    last_summary_at TEXT,
    last_event_at TEXT
);

-- Queue of day-summary jobs.
-- Background worker consumes these to build/update daily summaries.
CREATE TABLE IF NOT EXISTS memory_summary_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    status TEXT NOT NULL, -- queued | running | done | failed
    reason TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0
);

-- Raw chronological memory event log.
-- Captures user/main-agent/task milestones used for later summarization/retrieval.
CREATE TABLE IF NOT EXISTS daily_memory_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    event_time TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    layer TEXT NOT NULL DEFAULT 'raw', -- raw | summary
    created_at TEXT NOT NULL
);

-- Latest materialized summary per day.
-- Provides quick read path for concise day-level recall.
CREATE TABLE IF NOT EXISTS daily_memory_summaries (
    day TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    session_id TEXT,
    text TEXT NOT NULL
);

-- Explicit user-facing chat transcript storage.
-- Stores only user and assistant-visible messages by session for UI/session history replay.
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    route TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_defined_tasks_enabled_order
    ON defined_tasks(enabled, execution_order, schedule_id);

CREATE INDEX IF NOT EXISTS idx_defined_tasks_next_run_at
    ON defined_tasks(enabled, next_run_at);

CREATE INDEX IF NOT EXISTS idx_defined_task_run_times_schedule_enabled
    ON defined_tasks_run_times(schedule_id, enabled, time_of_day);

CREATE INDEX IF NOT EXISTS idx_defined_tasks_days_schedule
    ON defined_tasks_days_of_week(schedule_id, day_of_week);

CREATE INDEX IF NOT EXISTS idx_defined_task_runs_status_queued_at
    ON defined_task_runs(status, queued_at);

CREATE INDEX IF NOT EXISTS idx_defined_task_runs_profile_queued_at
    ON defined_task_runs(profile_id, queued_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_defined_task_runs_schedule_planned_fire
    ON defined_task_runs(schedule_id, planned_fire_at)
    WHERE schedule_id IS NOT NULL AND planned_fire_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_status_finished_at
    ON defined_task_run_history(status, finished_at);

CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_profile_finished_at
    ON defined_task_run_history(profile_id, finished_at);

CREATE INDEX IF NOT EXISTS idx_task_profiles_kind_enabled
    ON task_profiles(kind, enabled);

CREATE INDEX IF NOT EXISTS idx_task_profiles_queue_group
    ON task_profiles(queue_group);

CREATE INDEX IF NOT EXISTS idx_task_seen_items_task_provider_first_seen
    ON task_seen_items(task_id, provider, first_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_applications_date_found
    ON job_applications(date_found);

CREATE INDEX IF NOT EXISTS idx_job_applications_status
    ON job_applications(status);

CREATE INDEX IF NOT EXISTS idx_job_discovery_found_at
    ON job_discovery(found_at);

CREATE INDEX IF NOT EXISTS idx_job_discovery_decision
    ON job_discovery(decision);

CREATE INDEX IF NOT EXISTS idx_todo_items_create_date
    ON todo_items(create_date);

CREATE INDEX IF NOT EXISTS idx_todo_items_due_date
    ON todo_items(due_date);

CREATE INDEX IF NOT EXISTS idx_todo_items_status
    ON todo_items(status);

CREATE INDEX IF NOT EXISTS idx_day_memory_finalized
    ON day_memory_status(is_finalized);

CREATE INDEX IF NOT EXISTS idx_memory_summary_jobs_status_created
    ON memory_summary_jobs(status, created_at, job_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_summary_jobs_day_active
    ON memory_summary_jobs(day)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_daily_memory_events_day_time
    ON daily_memory_events(day, event_time, event_id);

CREATE INDEX IF NOT EXISTS idx_daily_memory_events_kind_day
    ON daily_memory_events(kind, day);

CREATE INDEX IF NOT EXISTS idx_daily_memory_events_session_event
    ON daily_memory_events(session_id, event_id);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_message
    ON chat_messages(session_id, message_id);

CREATE INDEX IF NOT EXISTS idx_daily_memory_events_session_event
    ON daily_memory_events(session_id, event_id);
