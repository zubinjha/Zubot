# Operations Runbook

This runbook covers practical operation of Zubot for long-running local usage.

## Long-Run Startup Mode

1. Activate environment:
   - `source .venv/bin/activate`
2. Confirm central runtime config:
   - `central_service.enabled = true`
   - `central_service.heartbeat_poll_interval_sec` (default `3600`) and `task_runner_concurrency` (current default `3`) set to desired values
   - `central_service.db_queue_busy_timeout_ms` and `central_service.db_queue_default_max_rows` set for desired SQL queue behavior
   - `central_service.waiting_for_user_timeout_sec` set for interactive run expiry policy
3. Start daemon (runtime-first):
   - `python -m src.zubot.daemon.main`
   - optional host/port override: `python -m src.zubot.daemon.main --host 127.0.0.1 --port 8000`
   - optional headless mode: `python -m src.zubot.daemon.main --no-app`
4. Verify health and scheduler state:
   - `GET /health`
   - `GET /api/central/status`
   - `GET /api/central/metrics`

Notes:
- Central service auto-starts on daemon startup only when `central_service.enabled` is true.
- Runtime remains single-process by design in v1.

## Restart Strategy

Use a process supervisor (for example launchd/systemd/pm2/supervisord) with:
- automatic restart on non-zero exit
- short restart delay (2-5 seconds)
- bounded restart burst policy to avoid tight crash loops

At restart:
- Scheduler state resumes from SQLite (`memory/central/zubot_core.db` by default).
- In-flight run threads are not resumed, they will be re-enqueued by schedule cadence/manual trigger.

## Rolling Restart Guidance (Single Host)

Because v1 is single-process, "rolling restart" means controlled brief downtime:
1. `POST /api/central/stop`
2. Wait for `GET /api/central/status` to report `running=false`.
3. Restart process.
4. Confirm startup + status endpoints.
5. If central runtime should be active, either:
   - keep `central_service.enabled=true` for auto-start, or
   - call `POST /api/central/start` manually.

## Runtime Pressure Checks

Use `GET /api/central/metrics` and monitor:
- `runtime.queued_count`
- `runtime.running_count`
- `runtime.waiting_count`
- `runtime.task_slot_busy_count`
- `runtime.task_slot_free_count`
- `runtime.task_slot_disabled_count`
- `runtime.oldest_queued_age_sec`
- `runtime.longest_running_age_sec`
- `runtime.longest_waiting_age_sec`
- `runtime.warnings`
- `runtime.active_runs`
- `runtime.queued_runs_preview`
- `runtime.waiting_runs_preview`

Warning semantics:
- `queue_depth_high`: queued runs crossed configured threshold.
- `running_task_stale`: longest running task crossed configured age threshold.

Operational control:
- enqueue manual task run: `POST /api/central/trigger/{task_id}`
- enqueue agentic background task: `POST /api/central/agentic/enqueue`
- kill queued/running run: `POST /api/central/runs/{run_id}/kill`
- inspect waiting runs: `GET /api/central/runs/waiting`
- resume waiting run: `POST /api/central/runs/{run_id}/resume`
- run serialized SQL query: `POST /api/central/sql` (read-only default)
- atomic task state:
  - `POST /api/central/task-state/upsert`
  - `POST /api/central/task-state/get`
- seen-item idempotency:
  - `POST /api/central/task-seen/mark`
  - `POST /api/central/task-seen/has`
- approval-gated control actions:
  - `POST /api/control/ingest` (parse assistant text blocks into pending actions)
  - `GET /api/control/pending` (list pending actions)
  - `POST /api/control/approve` (execute approved action)
  - `POST /api/control/deny` (reject action)

Control request text contract:
- assistant message includes one or more blocks:
  - `[ZUBOT_CONTROL_REQUEST]`
  - JSON payload with: `action_id`, `action`, `title`, `risk_level`, `payload`, optional `expires_at`
  - `[/ZUBOT_CONTROL_REQUEST]`
- supported actions: `enqueue_task`, `enqueue_agentic_task`, `kill_task_run`, `query_central_db`

Predefined task note:
- `indeed_daily_search` writes task-local artifacts under:
  - `src/zubot/predefined_tasks/indeed_daily_search/state/cover_letters/`
- It normalizes spreadsheet fields through an LLM extraction step.
  - On extraction failure, field defaults are deterministic: `Not Found`.
- It also uses central DB helper tables:
  - `task_seen_items` for seen-id dedupe
  - `job_discovery` for run-level triage results

## Task Logs Pattern

Preferred pattern for predefined-task runtime logs:
- one folder per task under task-local state:
  - `src/zubot/predefined_tasks/<task_id>/state/logs/`
- one append-only log file per run:
  - `run-YYYYMMDD-HHMMSS.log` (UTC timestamp recommended)
- optional convenience pointer:
  - `latest.log` (overwrite/symlink to current run)

Design rationale:
- keeps logs colocated with task runtime artifacts
- avoids cross-task log mixing during debugging
- preserves predictable cleanup and gitignore behavior

Repository policy:
- task-local state outputs (including `state/logs/`) are runtime-generated and should remain untracked.

## Provider Queue Monitoring (HasData)

HasData-backed tools are serialized through provider queue group `hasdata`.

Observe from tool responses:
- `queue.group`
- `queue.wait_sec`
- `queue.attempt`
- `queue_stats.pending`
- `queue_stats.calls_total`
- `queue_stats.calls_success`
- `queue_stats.calls_failed`
- `queue_stats.wait_sec_last`
- `queue_stats.wait_sec_max`
- `queue_stats.wait_sec_avg`

Tuning knobs (`tool_profiles.user_specific.has_data`):
- `queue_min_interval_sec`
- `queue_jitter_sec`
- `queue_max_retries`
- `queue_retry_backoff_sec`

## Retention and Pruning

Central housekeeping enforces:
- run-history retention window (`run_history_retention_days`) applied to `defined_task_run_history`
- run-history cap (`run_history_max_rows`) applied to `defined_task_run_history`
- periodic memory sweep cadence (`memory_manager_sweep_interval_sec`)
- completion-debounced memory sweep (`memory_manager_completion_debounce_sec`)
