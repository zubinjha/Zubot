# Central Service

This document describes the v1 central runtime scaffold for scheduled task-agent execution.

## Modules
- `src/zubot/core/central_service.py`
- `src/zubot/core/control_panel.py`
- `src/zubot/core/task_scheduler_store.py`
- `src/zubot/core/task_heartbeat.py`
- `src/zubot/core/task_agent_runner.py`
- `src/zubot/core/central_db_queue.py`
- `src/zubot/core/memory_manager.py`
- `src/zubot/core/memory_summary_worker.py`

## Runtime Model (v1)
- single-process Control Panel + queue consumer
- heartbeat is isolated from execution:
  - heartbeat queues due scheduled runs
  - dispatcher claims and executes queued runs in task slots
- task execution supports:
  - `predefined` runs (`pre_defined_tasks` scripts)
  - `agentic` runs (background sub-agent tasks)
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

`pre_defined_tasks`:
- `tasks` map (`task_id` -> script entrypoint + args + timeout)

## SQLite Store

Default DB path:
- `memory/central/zubot_core.db`

Tables:
- `defined_tasks`
  - schedule metadata + cadence + last run info
  - supports `frequency` and `calendar` schedule modes
- `defined_tasks_run_times`
  - optional calendar-mode run-time rows (multiple `HH:MM` entries per defined task)
- `defined_task_runs`
  - queued/running/terminal run lifecycle records
- `defined_task_run_history`
  - completion snapshots for historical reporting/pruning

Indexes:
- status/queued-time lookup for efficient queue claiming
- profile/queued-time lookup for per-profile state views

## Queue Flow
1. Read due schedules from SQLite (`defined_tasks` + `defined_tasks_run_times`) and enqueue run records (`status = queued`).
2. Claim queued runs (`status = running`) under concurrency cap.
3. Execute via `TaskAgentRunner`.
4. Write completion status (`done`/`failed`/`blocked`) and schedule last-run metadata.
5. Support explicit run kill:
  - queued run -> immediate `blocked`
  - running run -> cancellation requested, executor terminates subprocess and finalizes `blocked`
6. Agentic queueing:
  - enqueue non-blocking background tasks with `instructions` + model/tool scope
  - task runner executes through sub-agent path and writes terminal state to queue DB
7. Run housekeeping:
  - prune old completed run history rows
  - run debounced/periodic memory finalization sweeps for prior non-finalized days (full raw-day replay summary)
  - emit structured memory-manager sweep events for observability (not persisted to daily-memory raw events)
8. Memory ingestion behavior for task-agent events:
  - append raw memory events (`task_agent_event`) for queue + terminal lifecycle milestones:
    - `run_queued`
    - `run_finished`
    - `run_failed`
    - `run_blocked`
  - increment day-memory counters
  - enqueue day-summary jobs with dedupe
  - kick background summary worker for non-blocking summary updates
  - do not persist routine central internal/system events to daily memory

## Check-In Contract (Profile View)

Per profile status includes:
- state: `free` | `queued` | `running`
- current run id (if any)
- current textual description
- started timestamp (if running)
- queue position (if queued)
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
- `GET /api/central/schedules`
- `POST /api/central/schedules`
- `DELETE /api/central/schedules/{schedule_id}`
- `GET /api/central/runs`
- `GET /api/central/metrics`
- `POST /api/central/trigger/{task_id}`
- `POST /api/central/agentic/enqueue`
- `POST /api/central/runs/{run_id}/kill`
- `POST /api/central/sql`

## Future Direction
- merge scheduler store + memory index into a unified sqlite authority
- schedule memory summarization as a first-class queued task type
- add richer per-profile execution handlers and structured artifacts

## Operations
- Runbook: `docs/src/zubot/operations.md`
