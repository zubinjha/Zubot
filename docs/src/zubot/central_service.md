# Central Service

This document describes the v1 central runtime scaffold for scheduled task-agent execution.

## Modules
- `src/zubot/core/central_service.py`
- `src/zubot/core/task_scheduler_store.py`
- `src/zubot/core/task_agent_runner.py`
- `src/zubot/core/memory_manager.py`
- `src/zubot/core/memory_summary_worker.py`

## Runtime Model (v1)
- single-process scheduler + queue consumer
- task execution is `pre_defined_tasks` script-driven
- implemented but disabled by default (`central_service.enabled = false`)
- daemon-first startup supported via `python -m src.zubot.daemon.main`
- central loop auto-runs at daemon startup only when config-enabled
- app startup runs in client mode and does not own central lifecycle

## Config

`central_service`:
- `enabled`
- `poll_interval_sec`
- `task_runner_concurrency`
- `scheduler_db_path`
- `worker_slot_reserve_for_workers`
- `run_history_retention_days`
- `run_history_max_rows`
- `memory_manager_sweep_interval_sec`
- `memory_manager_completion_debounce_sec`
- `queue_warning_threshold`
- `running_age_warning_sec`

`pre_defined_tasks`:
- `tasks` map (`task_id` -> script entrypoint + args + timeout)

## SQLite Store

Default DB path:
- `memory/central/zubot_core.db`

Tables:
- `defined_tasks`
  - schedule metadata + cadence + last run info
  - supports `interval` and `calendar` schedule modes
- `defined_tasks_run_times`
  - optional calendar-mode run-time rows (multiple `HH:MM` entries per defined task)
- `defined_task_runs`
  - queued/running/completed run lifecycle records
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
5. Run housekeeping:
  - prune old completed run history rows
  - run debounced/periodic memory finalization sweeps for prior non-finalized days (full raw-day replay summary)
  - emit structured memory-manager sweep events for observability
6. Memory ingestion behavior for task-agent events:
  - append raw memory events (`task_agent_event`)
  - increment day-memory counters
  - enqueue day-summary jobs with dedupe
  - kick background summary worker for non-blocking summary updates

## Check-In Contract

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

## Future Direction
- merge scheduler store + memory index into a unified sqlite authority
- schedule memory summarization as a first-class queued task type
- add richer per-profile execution handlers and structured artifacts

## Operations
- Runbook: `docs/src/zubot/operations.md`
