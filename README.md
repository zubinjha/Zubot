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
  - central service scaffold for scheduled/queued task-agent runs (SQLite-backed queue/store)
    - task-agent executions are queued/claimed runs by fixed concurrency slots
  - config-driven LLM client (OpenRouter adapter)
  - centralized tool registry and dispatch helpers
  - context loading/assembly pipeline
  - context state/policy + rolling summary + fact extraction
  - token estimation + budget checks
  - session event persistence + daily memory helpers
  - SQLite-backed daily memory events/summaries + day-status + summary-job queue
  - background memory-summary worker (non-blocking queue drain)
  - predefined task execution support (`pre_defined_tasks` config + script entrypoints)
- Daemon-first runtime facade:
  - shared runtime service in `src/zubot/runtime/service.py`
  - daemon entrypoint in `src/zubot/daemon/main.py`
- Task-agent identity context files:
  - `context/TASK_AGENT.md`
  - `context/TASK_SOUL.md`
- Automated tests in `tests/` with `pytest`.

## Predefined Tasks
- Configure executable scheduled tasks in `config/config.json` under:
  - `pre_defined_tasks.tasks`
- Script entrypoints should be repository-relative paths (for example `src/zubot/predefined_tasks/indeed_daily_search.py`).
- Scheduler rows live in SQLite (`defined_tasks` / `defined_tasks_run_times`) and reference `profile_id == task_id`.
- Central service resolves `task_id -> pre_defined_tasks.tasks.<task_id>` at execution time.

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
  - Query-scored supplemental auto-load:
    - `context/more-about-human/*.md`
    - `context/more-about-human/projects/*.md`
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
- Runtime behavior model:
  - task-agent work is scheduled/triggered into central queue (`defined_task_runs`) and then claimed by central service
  - queue controls are task-run centric (`trigger` and `kill run`)
- Central service endpoints:
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
  - `POST /api/central/runs/{run_id}/kill`
- Session reset clears chat working context but preserves persisted daily memory in SQLite.
- Daily memory is DB-backed in `memory/central/zubot_core.db`:
  - raw events (`daily_memory_events`)
  - summary snapshots (`daily_memory_summaries`)
  - status + queue metadata (`day_memory_status`, `memory_summary_jobs`)
- Daily summaries are queue-driven from full raw-day replay (deduped per day) and processed by background worker.
- Daily raw memory uses signal-first ingestion (user/main-agent interactions plus task queue/finalization milestones; routine system chatter and tool-call telemetry are excluded).
- Session JSONL logging is optional (`memory.session_event_logging_enabled`) and disabled by default.
- LLM-routed queries run through a registry-backed tool-call loop (tool schema -> tool execution -> final response).
- Tool registry includes task queue orchestration tools:
  - `enqueue_task`, `kill_task_run`, `list_task_runs`, `get_task_agent_checkin`
- UI now includes:
  - chat-style message timeline
  - Chat/Scheduled Tasks tab split in the left panel
  - scheduled-task editor/list (create + delete with confirm modal)
  - live in-flight progress states (thinking/context/tool-check phases)
  - post-response tool-chain summary in Progress (exact tool names + status)
  - task-runtime status panel with queue/run visibility
  - task-agent panel with central runtime status + recent outcomes
  - runtime panel with route, tool-call record, and last reply snapshot
  - on-demand "Context JSON" dialog with collapsible full-context snapshot + JSON download
  - auto session initialization on page load/session change
- App chat uses unified LLM + registry tool loop (no keyword-based direct routing).

## Handoff Notes
- Current checkpoint and resume guidance:
  - [docs/src/zubot/handoff.md](docs/src/zubot/handoff.md)
