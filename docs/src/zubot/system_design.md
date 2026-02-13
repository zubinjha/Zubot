# System Design

This document captures the conceptual architecture of Zubot at the system level.

## Agent Hierarchy

### 1) User-Facing Agent (Main Agent)
- Primary chat-facing agent.
- Handles direct user interaction.
- Can inspect worker/task-agent status and orchestrate follow-up actions.
- Operates through the runtime/app chat path.

### 2) Task Agents
- Profile-driven agents executed by the central scheduler runtime.
- Runs are queue-based (`queued` -> `running` -> terminal status).
- Intended for recurring or structured jobs (for example job search workflows).
- Can request worker help via reserve-aware task-agent worker tooling.

### 3) Worker Agents
- Execution helpers for scoped sub-tasks.
- Managed by worker manager with concurrency/queue constraints.
- Separate control path from task-agent run queue.

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
- Primary tables: `defined_tasks`, `defined_tasks_run_times`, `defined_task_runs`, `defined_task_run_history`.
- Supports interval and wall-clock schedule modes.

### Memory Summary Queue
- Backed by SQLite (`memory_summary_jobs`).
- Dedupes active summary jobs per day.
- Drained by background summary worker.

## Tool System

### Registry Layers
- Core registry: `src/zubot/core/tool_registry.py`
- User-specific extensions: `src/zubot/core/tool_registry_user.py`

### Invocation Pattern
- User-facing agent uses model tool-calls routed through registry dispatch.
- Task/worker runs can be scoped by allowed tool lists.

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
- Exclude routine low-value system chatter.

## Why SQLite For Summaries Is Acceptable
- SQLite `TEXT` is appropriate for summary payloads at this scale.
- Conventional for local-first apps to persist event/snapshot memory in SQLite.
- Supports transactional writes, indexing, and simple migration path.
- Fits current architecture and future DB convergence goals.

## Planned Direction
- Continue improving retrieval/retention on DB-backed memory rows.
- Keep markdown files optional for export/human inspection only.
