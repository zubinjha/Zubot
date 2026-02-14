# Operations Runbook

This runbook covers practical operation of Zubot for long-running local usage.

## Long-Run Startup Mode

1. Activate environment:
   - `source .venv/bin/activate`
2. Confirm central runtime config:
   - `central_service.enabled = true`
   - `central_service.poll_interval_sec` (default `3600`) and `task_runner_concurrency` (current default `3`) set to desired values
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
- `runtime.oldest_queued_age_sec`
- `runtime.longest_running_age_sec`
- `runtime.warnings`
- `runtime.active_runs`
- `runtime.queued_runs_preview`

Warning semantics:
- `queue_depth_high`: queued runs crossed configured threshold.
- `running_task_stale`: longest running task crossed configured age threshold.

Operational control:
- enqueue manual task run: `POST /api/central/trigger/{task_id}`
- kill queued/running run: `POST /api/central/runs/{run_id}/kill`

## Retention and Pruning

Central housekeeping enforces:
- run-history retention window (`run_history_retention_days`) applied to `defined_task_run_history`
- run-history cap (`run_history_max_rows`) applied to `defined_task_run_history`
- periodic memory sweep cadence (`memory_manager_sweep_interval_sec`)
- completion-debounced memory sweep (`memory_manager_completion_debounce_sec`)
