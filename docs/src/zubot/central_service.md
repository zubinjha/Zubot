# Central Service

This document describes the v1 central runtime scaffold for scheduled task-agent execution.

## Modules
- `src/zubot/core/central_service.py`
- `src/zubot/core/control_panel.py`
- `src/zubot/core/task_scheduler_store.py`
- `src/zubot/core/task_heartbeat.py`
- `src/zubot/core/task_agent_runner.py`
- `src/zubot/core/central_db_queue.py`
- `src/zubot/core/provider_queue.py`
- `src/zubot/core/memory_manager.py`
- `src/zubot/core/memory_summary_worker.py`

## Runtime Model (v1)
- single-process Control Panel + queue consumer
- heartbeat is isolated from execution:
  - heartbeat queues due scheduled runs
  - dispatcher claims and executes queued runs in task slots
- task execution supports:
  - `script` profile runs (`task_profiles` table entrypoints)
  - `agentic` runs (background sub-agent tasks)
  - `interactive_wrapper` profile runs (pause/resume user handshake)
- implemented but disabled by default (`central_service.enabled = false`)
- daemon-first startup supported via `python -m src.zubot.daemon.main`
- central loop auto-runs at daemon startup only when config-enabled
- app startup runs in client mode and does not own central lifecycle

## Config

`central_service`:
- `enabled`
- `heartbeat_poll_interval_sec` (preferred)
- `poll_interval_sec`
- `task_runner_concurrency`
- `scheduler_db_path`
- `run_history_retention_days`
- `run_history_max_rows`
- `memory_manager_sweep_interval_sec`
- `memory_manager_completion_debounce_sec`
- `queue_warning_threshold`
- `running_age_warning_sec`
- `db_queue_busy_timeout_ms`
- `db_queue_default_max_rows`
- `waiting_for_user_timeout_sec`

Task profiles are DB-backed in `task_profiles`:
- fields:
  - `task_id`
  - `name`
  - `kind` (`script` | `agentic` | `interactive_wrapper`)
  - `entrypoint_path` or `module`
  - `resources_path`
  - `queue_group`
  - `timeout_sec`
  - `retry_policy_json`
  - `enabled`
  - `source`
- compatibility bootstrap:
  - if DB has zero rows at startup, legacy config maps are imported once from:
    - `task_profiles.tasks`
    - `pre_defined_tasks.tasks`

## SQLite Store

Default DB path:
- `memory/central/zubot_core.db`

Tables:
- `defined_tasks`
  - schedule metadata + cadence + cursor state + last run info
  - supports `frequency` and `calendar` schedule modes
  - cursor fields: `next_run_at`, `last_planned_run_at`
  - misfire policy: `queue_all` | `queue_latest` | `skip`
- `defined_tasks_run_times`
  - optional calendar-mode run-time rows (multiple `HH:MM` entries per defined task)
- `defined_task_runs`
  - queued/running/waiting/terminal run lifecycle records
  - includes `planned_fire_at` for dedupe/audit
- `defined_task_run_history`
  - completion snapshots for historical reporting/pruning
  - includes `planned_fire_at`
- `scheduler_runtime_state`
  - last heartbeat start/finish/status/error/enqueued-count metadata
- `task_profiles`
  - registered executable tasks (daemon/API managed)
- `task_profile_run_stats`
  - per-task latest run timestamps/counters
- `task_state_kv`
  - atomic task state checkpoints
- `task_seen_items`
  - atomic idempotent seen-item tracking
- `job_applications`
  - local DB mirror for spreadsheet-backed job application rows
  - core columns match sheet contract 1:1 (plus `created_at`, `updated_at`)

Indexes:
- status/queued-time lookup for efficient queue claiming
- profile/queued-time lookup for per-profile state views

## Queue Flow
1. Heartbeat reads enabled schedules and cursor state (`next_run_at`) from SQLite.
2. For each due schedule (`next_run_at <= now`), heartbeat transactionally:
  - determines due fire(s) from cursor
  - enqueues at most one run per task profile (strict no-overlap for same task)
  - writes run `planned_fire_at`
  - advances cursor (`next_run_at`) and `last_planned_run_at`
  - applies `misfire_policy` (`queue_all`, `queue_latest`, `skip`)
3. Claim queued runs (`status = running`) under concurrency cap.
4. Execute via `TaskAgentRunner`.
5. Write completion status (`done`/`failed`/`blocked`) and schedule last-run metadata.
6. Interactive pause/resume:
  - runner may return `waiting_for_user`
  - run payload stores waiting contract:
    - `request_id`
    - `question`
    - `context`
    - `expires_at`
  - run is resumed by API/tool and re-queued.
7. Waiting timeout handling:
  - housekeeping expires overdue waiting runs to terminal `blocked` (`waiting_for_user_timeout`).
8. Support explicit run kill:
  - queued run -> immediate `blocked`
  - running run -> cancellation requested, executor terminates subprocess and finalizes `blocked`
9. Agentic queueing:
  - enqueue non-blocking background tasks with `instructions` + model/tool scope
  - task runner executes through sub-agent path and writes terminal state to queue DB
10. Run housekeeping:
  - prune old completed run history rows
  - expire overdue waiting runs
  - run debounced/periodic memory finalization sweeps for prior non-finalized days (full raw-day replay summary)
  - emit structured memory-manager sweep events for observability (not persisted to daily-memory raw events)
11. Memory ingestion behavior for task-agent events:
  - append raw memory events (`task_agent_event`) for queue + terminal lifecycle milestones:
    - `run_queued`
    - `run_finished`
    - `run_failed`
    - `run_blocked`
    - `run_waiting`
    - `run_resumed`
  - increment day-memory counters
  - enqueue day-summary jobs with dedupe
  - kick background summary worker for non-blocking summary updates
  - do not persist routine central internal/system events to daily memory

## Check-In Contract (Profile View)

Per profile status includes:
- state: `free` | `queued` | `running` | `waiting_for_user`
- current run id (if any)
- current textual description
- started timestamp (if running)
- queue position (if queued)
- waiting question (if waiting)
- last result object:
  - status
  - summary
  - error
  - finished_at

## Task Slot Contract

`status().task_slots` exposes slot-level runtime metadata:
- `slot_id`
- `enabled`
- `state` (`free` | `busy`)
- `run_id`
- `task_id`
- `task_name`
- `started_at`
- `updated_at`
- `last_result`

## Task Progress Event Contract

Forwarded task events (`type = task_agent_event`) include normalized payload fields:
- `task_id`
- `task_name`
- `run_id`
- `status`:
  - `queued`
  - `running`
  - `progress`
  - `waiting_for_user`
  - `completed`
  - `failed`
  - `killed`
- optional:
  - `slot_id`
  - `message`
  - `percent` (0-100)
  - `origin` (`scheduled`, `manual`, `agentic`, etc.)
- timestamps:
  - `started_at`
  - `updated_at`
  - `finished_at`

## Central SQL Queue Contract

- Serialized SQL path: `CentralService.execute_sql(...)`
- Backed by `CentralDbQueue` worker thread:
  - correlation ids (`request_id`)
  - read-only guard by default
  - WAL + busy-timeout aware connection settings
- Intended for concurrent callers where direct SQLite access could contend.

## API Surface
- `GET /api/central/status`
- `POST /api/central/start`
- `POST /api/central/stop`
- `GET /api/central/tasks`
- `POST /api/central/tasks`
- `DELETE /api/central/tasks/{task_id}`
- `GET /api/central/schedules`
- `POST /api/central/schedules`
- `DELETE /api/central/schedules/{schedule_id}`
- `GET /api/central/runs`
- `GET /api/central/metrics`
- `POST /api/central/trigger/{task_id}`
- `POST /api/central/agentic/enqueue`
- `POST /api/central/runs/{run_id}/kill`
- `GET /api/central/runs/waiting`
- `POST /api/central/runs/{run_id}/resume`
- `POST /api/central/sql`
- `POST /api/central/task-state/upsert`
- `POST /api/central/task-state/get`
- `POST /api/central/task-seen/mark`
- `POST /api/central/task-seen/has`

## Future Direction
- merge scheduler store + memory index into a unified sqlite authority
- schedule memory summarization as a first-class queued task type
- add richer per-profile execution handlers and structured artifacts

## Operations
- Runbook: `docs/src/zubot/operations.md`
