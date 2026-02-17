# Zubot

Zubot is a local-first personal AI agent framework.

Primary use cases:
- coding orchestration
- todo management
- future job automation

Deeper architectural documentation lives in [docs/README.md](docs/README.md).
Conceptual system architecture is documented in [docs/src/zubot/system_design.md](docs/src/zubot/system_design.md).
Operations guidance for long-running mode lives in [docs/src/zubot/operations.md](docs/src/zubot/operations.md).

## Current State
- Architecture-first project with core context contracts in `context/`.
- Config-driven runtime setup via `config/config.json` (local, ignored) and `config/example_config.json` (tracked schema).
  - model resolution uses:
    - `models.<model_id>` for model definitions
    - `model_aliases.<alias> -> <model_id>` for runtime tiers (for example `low`/`med`/`high`)
    - `default_model_alias` for default alias selection
- Kernel tool scaffolding implemented in `src/zubot/tools/kernel/`:
  - filesystem (policy-enforced read/list/write primitives)
  - location
  - time
  - weather (Open-Meteo integration)
  - web search (Brave API integration)
  - web fetch (page content extraction for URLs)
- Data-aware helper tools in `src/zubot/tools/data/`:
  - JSON read/write
  - text search
- Tool registration is layered:
  - base/core tools in `src/zubot/core/tool_registry.py`
  - user-specific tools in `src/zubot/core/tool_registry_user.py`
- Unregistered Google helper modules are available in `src/zubot/tools/kernel/`:
  - OAuth token lifecycle helper (`google_auth.py`)
  - Job application sheet helpers (`google_sheets_job_apps.py`)
  - DOCX creation + Drive upload helpers (`google_drive_docs.py`)
- Core agent runtime scaffolding in `src/zubot/core/`:
  - agent loop + event schemas
  - sub-agent runner scaffold + delegation path
  - control-panel orchestration facade + central service runtime for scheduled/queued task runs
    - heartbeat-driven due-run queueing
    - cursor-based scheduler state (`next_run_at`, `last_planned_run_at`, misfire policy)
    - strict no-overlap for the same task profile
    - task executions are queued/claimed runs by fixed concurrency slots
    - serialized SQL queue for central DB access
  - config-driven LLM client (OpenRouter adapter)
  - centralized tool registry and dispatch helpers
  - context loading/assembly pipeline
  - context state/policy + rolling summary + fact extraction
  - token estimation + budget checks
  - session event persistence + daily memory helpers
  - SQLite-backed daily memory events/summaries + day-status + summary-job queue
  - background memory-summary worker (non-blocking queue drain)
  - task profile execution support (`script`/`agentic`/`interactive_wrapper`)
    - waiting-for-user pause/resume lifecycle for interactive runs
    - automatic waiting-run timeout handling
  - provider-level serialized queue support for rate-limited integrations (for example HasData)
- Daemon-first runtime facade:
  - shared runtime service in `src/zubot/runtime/service.py`
  - daemon entrypoint in `src/zubot/daemon/main.py`
- Task-agent identity context files:
  - `context/TASK_AGENT.md`
  - `context/TASK_SOUL.md`
- Automated tests in `tests/` with `pytest`.

## Task Profiles
- Runtime task registry is DB-backed in `memory/central/zubot_core.db` table `task_profiles`.
- Register/edit/delete tasks from daemon UI (`Scheduled Tasks` tab -> `Task Registry`) or API:
  - `POST /api/central/tasks`
  - `DELETE /api/central/tasks/{task_id}`
- Backward compatibility seed:
  - if DB has zero task profiles at startup, legacy config maps are imported once from:
    - `task_profiles.tasks`
    - `pre_defined_tasks.tasks`
- Script entrypoints should be repository-relative paths (for example `src/zubot/predefined_tasks/indeed_daily_search/task.py`).
- Standardized task package layout is supported under `src/zubot/predefined_tasks/<task_id>/`:
  - `task.py`
  - `task_config.json`
  - optional `prompts/`, `assets/`, `state/`
- Reference template lives at `src/zubot/predefined_tasks/example_task_layout/`.
- Scheduler rows live in SQLite (`defined_tasks` / `defined_tasks_run_times`) and reference `profile_id == task_id`.
- Central service resolves `task_id -> task_profiles.task_id` at execution time.

## Agent Resume Checklist
For new agents or fresh sessions, use this order:
1. Read `AGENTS.md` (repo-level execution rules and startup contract).
2. Read `context/KERNEL.md`, `context/AGENT.md`, `context/SOUL.md`, and `context/USER.md`.
3. Read `docs/README.md` and relevant docs under `docs/src/zubot/`.
4. Load runtime config from `config/config.json` via `src/zubot/core/config_loader.py`.
5. Run tests before and after edits:
   - `source .venv/bin/activate`
   - `python -m pytest -q`

## Security Notes
- Never commit secrets from `config/config.json`.
- Use `config/example_config.json` as the committed schema reference.
- User-specific tool secrets/config now live under:
  - `tool_profiles.user_specific.*`

## Local Runtime (Primary)
- Preferred startup path is daemon-first:
  - `source .venv/bin/activate`
  - `python -m src.zubot.daemon.main`
- This starts runtime ownership (user-facing agent + task-agent queue manager) and local app server in one process.
- Optional headless runtime mode (no app server):
  - `python -m src.zubot.daemon.main --no-app`

Quick run command:
```bash
source .venv/bin/activate
python -m src.zubot.daemon.main
```

## Usage
Choose one of these startup modes based on what you want to run.

1. Full local stack (recommended):
   - Starts Zubot runtime + local user-facing app together.
   - `source .venv/bin/activate`
   - `python -m src.zubot.daemon.main`

2. Zubot standalone runtime (no UI):
   - Starts runtime only (user-facing agent runtime + task-agent scheduler).
   - `source .venv/bin/activate`
   - `python -m src.zubot.daemon.main --no-app`

3. App-only local view (UI iteration mode):
   - Starts just the local app process.
   - `source .venv/bin/activate`
   - `python -m uvicorn app.main:app --reload --port 8000`
   - If central scheduler is needed in this mode, start it via:
     - `POST /api/central/start`

## Context Autoload Matrix
- User-facing agent (main chat path):
  - Always loads:
    - `context/KERNEL.md`
    - `context/AGENT.md`
    - `context/SOUL.md`
    - `context/USER.md`
    - `context/more-about-human/README.md`
  - Supplemental project/profile files are not query-autoloaded by default.
  - Project-specific details should be grounded via explicit file reads/tool calls.
  - Daily memory auto-load:
    - recent daily memory via `memory.autoload_summary_days` (default `2`)
- Task agents (scheduled/manual profile runs):
  - Always loads:
    - `context/KERNEL.md`
    - `context/TASK_AGENT.md`
    - `context/TASK_SOUL.md`
    - `context/USER.md`
  - Daily memory auto-load:
    - recent daily memory via `memory.autoload_summary_days` (default `2`)

## Local Web Chat (Test UI)
- Minimal local app lives in `app/`.
- Direct app run (client surface only) for UI iteration:
  - `source .venv/bin/activate`
  - `python -m uvicorn app.main:app --reload --port 8000`
- Open: `http://127.0.0.1:8000`
- Supports `session_id` scoping and session reset via `/api/session/reset`.
- Supports explicit session initialization via `/api/session/init`.
- Supports fetching the latest assembled per-session context snapshot via `/api/session/context`.
- Supports loading persisted per-session transcript history via `/api/session/history` (UI reload restores recent chat timeline from `chat_messages` only).
- Supports clearing persisted per-session transcript history via `/api/session/history/clear`.
- Supports restarting in-memory session context from recent persisted chat via `/api/session/restart_context`.
- UI behavior: `Reset Session` clears persisted transcript history for that `session_id` and resets in-memory session context.
- UI controls:
  - `Reset Context`: reset in-memory session context and clear UI chat view (preserves DB transcript)
  - `Normal Context`: apply normal startup context policy (rehydrate from recent persisted transcript and show last N messages)
- Runtime behavior model:
  - task-agent work is scheduled/triggered into central queue (`defined_task_runs`) and then claimed by central service
  - queue controls are task-run centric (`trigger` and `kill run`)
- Central service endpoints:
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
  - `GET /api/central/runs/waiting`
  - `GET /api/central/metrics`
  - `POST /api/central/trigger/{task_id}`
  - `POST /api/central/agentic/enqueue`
  - `POST /api/central/runs/{run_id}/kill`
  - `POST /api/central/runs/{run_id}/resume`
  - `POST /api/central/sql`
  - `POST /api/central/task-state/upsert`
  - `POST /api/central/task-state/get`
  - `POST /api/central/task-seen/mark`
  - `POST /api/central/task-seen/has`
  - `POST /api/control/ingest`
  - `GET /api/control/pending`
  - `POST /api/control/approve`
  - `POST /api/control/deny`
- Session reset clears chat working context but preserves persisted daily memory in SQLite.
- Daily memory is DB-backed in `memory/central/zubot_core.db`:
  - raw events (`daily_memory_events`)
  - summary snapshots (`daily_memory_summaries`)
  - explicit transcript rows (`chat_messages`)
  - status + queue metadata (`day_memory_status`, `memory_summary_jobs`)
  - task runtime helper tables (`task_state_kv`, `task_seen_items`, `job_applications`)
- Daily summaries are queue-driven from full raw-day replay (deduped per day) and processed by background worker.
- Daily raw memory uses signal-first ingestion (user/main-agent interactions plus task queue/finalization milestones; routine system chatter and tool-call telemetry are excluded).
- Session JSONL logging is optional (`memory.session_event_logging_enabled`) and disabled by default.
- Legacy markdown import is opt-in (`memory.legacy_daily_file_migration_enabled`) and disabled by default.
- LLM-routed queries run through a registry-backed tool-call loop (tool schema -> tool execution -> final response).
- Tool registry includes task queue orchestration tools:
  - `enqueue_task`, `enqueue_agentic_task`, `kill_task_run`, `list_task_runs`, `list_waiting_runs`, `resume_task_run`, `get_task_agent_checkin`, `query_central_db`
  - `upsert_task_state`, `get_task_state`, `mark_task_item_seen`, `has_task_item_seen`
- UI now includes:
  - chat-style message timeline
  - Chat/Scheduled Tasks tab split in the left panel
  - scheduled-task editor/list (create + delete with confirm modal)
  - live in-flight progress states (thinking/context/tool-check phases)
  - post-response tool-chain summary in Progress (exact tool names + status)
  - task-runtime status panel with queue/run visibility
  - task-slot status visibility (busy/free/disabled counts)
  - task-agent panel with central runtime status + recent outcomes
  - runtime panel with route, tool-call record, and last reply snapshot
  - on-demand "Context JSON" dialog with collapsible full-context snapshot + JSON download
  - auto session initialization on page load/session change
- App chat uses unified LLM + registry tool loop (no keyword-based direct routing).
- High-impact control actions can run through approval-gated text protocol:
  - assistant emits `[ZUBOT_CONTROL_REQUEST] ... [/ZUBOT_CONTROL_REQUEST]`
  - UI renders allow/disallow controls from parsed request blocks
  - backend executes only after explicit `approve` API call

## Handoff Notes
- Current checkpoint and resume guidance:
  - [docs/src/zubot/handoff.md](docs/src/zubot/handoff.md)
