# System Design

This document captures the conceptual architecture of Zubot at the system level.

## Runtime Components

### 1) Control Panel
- Central orchestration boundary for runtime operations.
- Owns queue/runtime controls via central service and provides SQL queue access to central DB.
- Exposes deterministic operations used by API/tools:
  - start/stop/status
  - enqueue task runs (predefined + agentic)
  - list/kill/resume runs
  - list waiting runs
  - schedule CRUD
  - task state + seen-item atomic helpers
  - serialized SQL execution (`central DB queue`)

### 2) User-Facing Agent
- Primary chat-facing main agent.
- Handles direct user interaction, context assembly, memory-aware response generation.
- Uses tool contracts to query/control the Control Panel (non-blocking queue operations).

### 3) Heartbeat
- Dedicated scheduler tick component that only decides what should be queued.
- Runs on a polling interval and enqueues due scheduled tasks.
- Does not execute tasks directly.

### 4) Task Agent Slots
- Fixed-capacity execution slots (`central_service.task_runner_concurrency`).
- Consume queued runs when free and transition through lifecycle states.
- Interactive runs can pause in `waiting_for_user` and free their slot until resumed.
- Slot metadata is surfaced for observability (`slot_id`, state, run/task bindings, timestamps, last result).

## Runtime Topology

### Runtime Owner
- `src/zubot/runtime/service.py` is the shared lifecycle owner.
- `src/zubot/daemon/main.py` is the preferred startup path.

### Local App
- `app/main.py` is a thin client/API surface over runtime service.
- Provides local interaction UI and API endpoints.

## Scheduling + Queueing Model

### Task-Agent Queue
- Backed by SQLite (`memory/central/zubot_core.db`).
- Primary tables: `task_profiles`, `defined_tasks`, `defined_tasks_run_times`, `defined_task_runs`, `defined_task_run_history`.
- Scheduler cursor model:
  - `defined_tasks.next_run_at` drives due detection
  - `last_planned_run_at` tracks last processed fire cursor
  - `misfire_policy` controls backlog handling (`queue_all` / `queue_latest` / `skip`)
- Strict no-overlap for same task profile:
  - heartbeat will not enqueue a new run for a profile if queued/running/waiting run already exists
- `defined_task_runs.planned_fire_at` provides fire-time dedupe/audit key.
- `scheduler_runtime_state` tracks last heartbeat run metadata.
- Task runtime helper tables: `task_state_kv`, `task_seen_items`, `job_applications`.
- Supports frequency and wall-clock schedule modes.
- Run payloads support:
  - predefined script runs
  - agentic background runs
  - interactive wrapper runs that can pause/resume for user input

### Memory Summary Queue
- Backed by SQLite (`memory_summary_jobs`).
- Dedupes active summary jobs per day.
- Drained by background summary worker.

### Central DB SQL Queue
- Serialized SQL execution path for central DB calls.
- Designed to avoid write contention under concurrent callers.
- Uses correlation IDs and bounded response rows.
- Task atomic write helpers route through this queue (`upsert_task_state`, `mark_task_item_seen`).

### Provider Serialization Queue
- Runtime-local provider queue manager serializes calls per provider group.
- HasData calls run through queue group `hasdata` with configurable:
  - min interval
  - jitter
  - retry/backoff for transient failures
- Queue metrics include depth/wait/failure counters for observability.

## Tool System

### Registry Layers
- Core registry: `src/zubot/core/tool_registry.py`
- User-specific extensions: `src/zubot/core/tool_registry_user.py`

### Invocation Pattern
- User-facing agent uses model tool-calls routed through registry dispatch.
- Task runs are queued/claimed through central service and exposed via task-oriented orchestration tools.
- Orchestration tools include:
  - `enqueue_task`
  - `enqueue_agentic_task`
  - `kill_task_run`
  - `list_task_runs`
  - `list_waiting_runs`
  - `resume_task_run`
  - `get_task_agent_checkin`
  - `query_central_db`
  - `upsert_task_state`
  - `get_task_state`
  - `mark_task_item_seen`
  - `has_task_item_seen`

## Memory and Database Layers

### Current Source of Truth Split
- SQLite:
  - scheduler state (`defined_tasks`, `defined_tasks_run_times`, `defined_task_runs`, `defined_task_run_history`)
  - raw memory events (`daily_memory_events`)
  - daily summary snapshots (`daily_memory_summaries`)
  - memory status/counters (`day_memory_status`)
  - summary queue (`memory_summary_jobs`)
- Legacy markdown files (`memory/daily/...`) are optional import/export artifacts, not required runtime source of truth.

### Memory Signal Policy
- Prioritize user <-> main-agent interaction.
- Include task-agent queue/finalization milestones (`run_queued`, `run_finished`, `run_failed`, `run_blocked`).
- Include interactive milestones (`run_waiting`, `run_resumed`).
- Exclude routine low-value system chatter.

## Why SQLite For Summaries Is Acceptable
- SQLite `TEXT` is appropriate for summary payloads at this scale.
- Conventional for local-first apps to persist event/snapshot memory in SQLite.
- Supports transactional writes, indexing, and simple migration path.
- Fits current architecture and future DB convergence goals.

## Planned Direction
- Continue improving retrieval/retention on DB-backed memory rows.
- Keep markdown files optional for export/human inspection only.
