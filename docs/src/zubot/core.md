# Zubot Core

Core runtime assumptions and invariants live in:
- `context/KERNEL.md`

This document reflects the active task-agent-centric runtime.

## Runtime Shape

Primary runtime modules:
- `src/zubot/runtime/service.py`
- `src/zubot/core/control_panel.py`
- `src/zubot/core/central_service.py`
- `app/chat_logic.py`

Current execution model:
- one user-facing main agent (chat path)
- control-panel orchestration boundary for central runtime
- central queue manager for predefined + agentic task runs
- heartbeat scheduler tick for due-run queueing
- fixed task-agent concurrency via `central_service.task_runner_concurrency`
- no worker-agent API/runtime path in active orchestration flow

## Agent Contracts

Primary module:
- `src/zubot/core/agent_types.py`

Key schemas:
- `TaskEnvelope`
- `WorkerResult` (legacy type retained for compatibility with older scaffolding)
- `SessionEvent`

Main-agent chat path currently uses registry tool-calls and session runtime state in `app/chat_logic.py`.

## Config Loader

Primary module:
- `src/zubot/core/config_loader.py`

Responsibilities:
- resolve config path (`ZUBOT_CONFIG_PATH` or `config/config.json`)
- safely parse JSON config with cache
- expose normalized runtime helpers
- keep schema-driven contracts centralized (for example `job_applications_schema`)

Common helpers used by runtime:
- `load_config()`
- `get_model_config()`
- `get_provider_config()`
- `get_central_service_config()`
- `get_task_profiles_config()` (`get_predefined_task_config()` compatibility alias)

Design rule:
- do not parse config ad hoc in feature modules; use config loader helpers.

## LLM Gateway

Primary modules:
- `src/zubot/core/llm_client.py`
- `src/zubot/core/providers/openrouter.py`

Responsibilities:
- central provider routing and model resolution
- retry/backoff for transient provider failures
- normalized response payloads (`text`, `tool_calls`, `usage`, `error`)
- structured retry metadata (`attempts_used`, `attempts_configured`, `retryable_error`)

All main-agent model calls route through this gateway.

## Tool Registry

Primary modules:
- `src/zubot/core/tool_registry.py`
- `src/zubot/core/tool_registry_user.py`

Responsibilities:
- maintain canonical tool contracts
- expose listable schema metadata
- dispatch tool calls with normalized error payloads

Active orchestration tools are task-run-centric:
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

## Context Pipeline

Primary modules:
- `src/zubot/core/context_loader.py`
- `src/zubot/core/context_assembler.py`
- `src/zubot/core/context_policy.py`
- `src/zubot/core/context_state.py`
- `src/zubot/core/summary_memory.py`
- `src/zubot/core/fact_memory.py`

Responsibilities:
- load baseline identity/system context (`KERNEL/AGENT/SOUL/USER`)
- score and include supplemental context files
- build model-ready message list with summary/fact carryover
- apply deterministic budget-aware context selection

Main-agent turn behavior (chat path):
- per user turn, auto-inject compact location/time runtime context
- include forwarded task-agent lifecycle events
- persist latest assembled context snapshot per session for UI/API inspection (`/api/session/context`)
- exclude internal low-signal chatter from persisted daily memory rows

## Scheduler + Queue Store

Primary modules:
- `src/zubot/core/central_service.py`
- `src/zubot/core/task_heartbeat.py`
- `src/zubot/core/task_scheduler_store.py`
- `src/zubot/core/task_agent_runner.py`
- `src/zubot/core/central_db_queue.py`

Responsibilities:
- heartbeat enqueues due schedule runs from SQLite
- claim queued runs under fixed concurrency cap
- execute task-profile script entrypoints and agentic background runs
- support interactive pause/resume (`waiting_for_user` + resume API/tool)
- persist run lifecycle transitions and history
- support run kill/cancel via central service API/tool surface
- provide serialized SQL queue access for concurrent DB callers
- provide provider-level serialized external API calls for rate-limited tools (for example HasData)

Structured task progress payloads include:
- `task_id`
- `task_name`
- `run_id`
- `slot_id`
- `status` (`queued`, `running`, `progress`, `waiting_for_user`, `completed`, `failed`, `killed`)
- optional `message` / `percent` / `origin`
- `started_at`, `updated_at`, `finished_at`

## Memory Pipeline

Primary modules:
- `src/zubot/core/daily_memory.py`
- `src/zubot/core/memory_index.py`
- `src/zubot/core/daily_summary_pipeline.py`
- `src/zubot/core/memory_summary_worker.py`
- `src/zubot/core/memory_manager.py`

Responsibilities:
- append raw daily memory events
- queue and process summary jobs in SQLite
- finalize prior days via periodic/completion sweeps
- keep summarization asynchronous from chat/task execution

Signal policy:
- persist high-value user/main-agent transcript entries
- persist high-signal task lifecycle milestones
- do not persist internal telemetry noise

## Legacy Notes

Legacy worker modules remain in source for now (`worker_manager`, `worker_policy`, `worker_capacity_policy`) but are not part of active runtime/API/tool orchestration path.
