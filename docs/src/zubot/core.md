# Zubot Core

Core runtime assumptions and invariants live in:
- `context/KERNEL.md`

This area will eventually define:
- agent loop orchestration
- context assembly pipeline
- model/tool routing and safety checks

## Agent Contracts

Primary module:
- `src/zubot/core/agent_types.py`

Schemas:
- `TaskEnvelope`:
  - `task_id`
  - `requested_by`
  - `instructions`
  - `model_tier`
  - `tool_access` / `skill_access`
  - `deadline_iso`
- `WorkerResult`:
  - `task_id`
  - `status`
  - `summary`
  - `artifacts`
  - `error`
  - `trace`
- `SessionEvent`:
  - event timeline entries for user/assistant/tool/worker/system events

## Agent Loop

Primary module:
- `src/zubot/core/agent_loop.py`

Current scaffold responsibilities:
- ingest user input events
- assemble working context from base + recent events
- plan next action via pluggable planner
- execute action via pluggable executor
- optional token-budget stop checks per turn
- optional session event persistence to JSONL
- emit deterministic stop reasons:
  - `final_response`
  - `needs_user_input`
  - `step_budget_exhausted`
  - `tool_call_budget_exhausted`
  - `timeout_budget_exhausted`
  - `context_budget_exhausted`

## Config Loader

Primary module:
- `src/zubot/core/config_loader.py`

Responsibilities:
- resolve config path from:
  - explicit argument
  - `ZUBOT_CONFIG_PATH` env var
  - default `config/config.json`
- load JSON config safely
- expose helper accessors for common runtime needs

Current helper surface:
- `load_config()`
- `resolve_config_path()`
- `get_timezone()`
- `get_home_location()`
- `get_model_by_alias()`
- `get_model_by_id()`
- `get_model_config()`
- `get_provider_config()`
- `get_default_model()`
- `get_max_concurrent_workers()`
- `get_central_service_config()`
- `get_task_agent_config()`
- `clear_config_cache()`

Design note:
- Runtime code should call this loader instead of reading `config/config.json` directly.

## Path Policy

Primary module:
- `src/zubot/core/path_policy.py`

Responsibilities:
- repository-root path normalization (`normalize_repo_path`)
- safe path resolution (`resolve_repo_path`)
- filesystem policy parsing (`get_filesystem_policy`)
- access checks (`check_access`, `can_read`, `can_write`)

Filesystem policy fields (from config):
- `default_access`: `allow` or `deny`
- `allow_read`: list of glob patterns
- `allow_write`: list of glob patterns
- `deny`: denylist patterns (always takes precedence)

## LLM Client

Primary modules:
- `src/zubot/core/llm_client.py`
- `src/zubot/core/providers/openrouter.py`

Responsibilities:
- resolve model + provider from config
- map model endpoint for provider calls
- execute provider request
- normalize response payload shape (`text`, `tool_calls`, `usage`, `error`)
- retry transient provider network failures (DNS/timeouts/5xx/429) with bounded backoff
- default retry schedule is `1s, 3s, 5s` (4 total attempts including initial call)
- return retry metadata (`attempts_used`, `attempts_configured`, `retryable_error`) on both success and failure

## Tool Registry

Primary module:
- `src/zubot/core/tool_registry.py`
- user-specific registration layer: `src/zubot/core/tool_registry_user.py`

Responsibilities:
- maintain one canonical registry of callable tool contracts
- compose base/core tools with a second-level user-specific registry module
- expose metadata (`name`, `category`, `description`, `parameters`) for planner/UI use
- provide deterministic runtime dispatch (`invoke_tool(name, **kwargs)`)
- normalize unknown-tool and bad-argument errors into stable payloads

Current surface:
- `get_tool_registry()`
- `list_tools(category=None)`
- `invoke_tool(name, **kwargs)`

## Token Budgeting

Primary module:
- `src/zubot/core/token_estimator.py`

Responsibilities:
- estimate text/message token usage
- load per-model limits from config
- compute fill ratio and remaining input budget
- drive context-budget stop checks in `AgentLoop`

## Context Pipeline

Primary modules:
- `src/zubot/core/context_loader.py`
- `src/zubot/core/context_assembler.py`
- `src/zubot/core/context_state.py`
- `src/zubot/core/context_policy.py`
- `src/zubot/core/summary_memory.py`
- `src/zubot/core/fact_memory.py`

Responsibilities:
- always-load base context (`KERNEL`, `AGENT`, `SOUL`, `USER`)
- optionally select and load situational supplemental context
- assemble ordered model messages from context + session events
- apply budget-aware trimming with deterministic context priority rules
- compact older recent events into rolling summary content
- extract and carry forward durable user/task facts

## Session Persistence

Primary module:
- `src/zubot/core/session_store.py`

Responsibilities:
- append session events to `memory/sessions/<session_id>.jsonl`
- load persisted event timelines for replay/debugging
- cleanup old session logs via retention helper (`cleanup_session_logs_older_than`)

## Memory Index

Primary module:
- `src/zubot/core/memory_index.py`

Responsibilities:
- maintain per-day summary counters and status in SQLite
- track:
  - `total_messages`
  - `last_summarized_total`
  - `messages_since_last_summary`
  - finalization state
- own queued summary-job table (`memory_summary_jobs`) with active-day dedupe
- support pending-day queries for startup finalization workflows

## Daily Summary Pipeline

Primary module:
- `src/zubot/core/daily_summary_pipeline.py`

Responsibilities:
- summarize full raw-day transcript (not just unsummarized tail buffers)
- recursively compact oversized daily transcripts
- write day summary snapshots
- process queued summary jobs and mark completion/failure

## Memory Summary Worker

Primary module:
- `src/zubot/core/memory_summary_worker.py`

Responsibilities:
- run background non-blocking summary-job processing
- expose lifecycle methods (`start`, `stop`, `kick`, `status`)
- drain `memory_summary_jobs` queue with configurable poll/throughput

## Memory Manager

Primary module:
- `src/zubot/core/memory_manager.py`

Responsibilities:
- perform periodic sweeps for prior non-finalized day summaries
- perform completion-triggered debounced sweeps from central runtime hooks
- finalize pending days by replaying full raw day transcript and marking day status finalized

## Daily Memory

Primary module:
- `src/zubot/core/daily_memory.py`

Responsibilities:
- write day-scoped raw/summary memory rows in SQLite:
  - raw events table: `daily_memory_events`
  - summary table: `daily_memory_summaries`
- append turn-level raw log entries for completed interactions
- load recent summary snapshots (today + yesterday by default) with trimmed raw fallback when summary is unavailable
- write summary snapshots from queued full-day summarization jobs (replace per-day summary row per update)
- support writing to explicit day IDs for finalization/backfill cases

Behavior note:
- daily memory persists across session resets
- session reset clears in-memory chat context only

## Local App Runtime Behavior

Primary module:
- `app/chat_logic.py`

Responsibilities:
- maintain per-session runtime state (`recent_events`, rolling summary, facts)
- enforce bounded in-memory session retention (TTL + max active sessions)
- provide explicit session initialization API behavior (preload before first message)
- refresh recent daily memory before each chat turn
- append completed-turn entries to daily memory raw rows
- keep daily-memory persistence scoped to:
  - user/main-agent completed chat turns
  - central task-agent lifecycle milestones (`run_queued`, `run_finished`, `run_failed`, `run_blocked`)
  - (worker internals, tool telemetry, and system chatter are excluded from daily-memory rows)
- enqueue day-summary jobs on turn completion and kick background summary worker
- expose session reset that clears in-memory state while preserving persisted daily memory
- execute an iterative model/tool loop for LLM-routed requests:
  - include tool schemas from `tool_registry`
  - execute model tool calls through `invoke_tool(...)`
  - append tool outputs back into the model message stream
  - stop on final assistant text or max tool-loop step guard

## Runtime Facade + Daemon

Primary modules:
- `src/zubot/runtime/service.py`
- `src/zubot/daemon/main.py`

Responsibilities:
- provide one runtime authority for:
  - chat/session operations
  - worker operations
  - central scheduler/task-agent operations
- support daemon-first local startup (`python -m src.zubot.daemon.main`)
- keep app endpoints as a thin local client layer over runtime service

## Sub-Agent Runtime

Primary module:
- `src/zubot/core/sub_agent_runner.py`

Responsibilities:
- execute scoped worker tasks from `TaskEnvelope`
- reuse context assembly/token budgeting/memory primitives
- return normalized `WorkerResult` payloads
- support stateless worker behavior between tasks
- default worker path supports LLM tool-loop execution using tool schemas from registry
- worker tool access can be restricted per-task via `TaskEnvelope.tool_access`
- orchestration-category tools are excluded from worker loop by default (no recursive worker spawn)

## Worker Manager

Primary modules:
- `src/zubot/core/worker_manager.py`
- `src/zubot/core/worker_policy.py`

Responsibilities:
- enforce hard concurrency cap (`max_concurrent_workers`, default 3)
- queue worker tasks when active slots are full
- maintain worker registry state:
  - `worker_id`, `title`, `status`
  - `task_envelope`, `started_at`, `finished_at`
  - `error`, `result`
- own worker control surface:
  - spawn
  - message/enqueue task
  - cancel
  - reset worker context
  - get/list worker status
- keep per-worker scoped context session (summary + facts + preload bundle)
- emit worker lifecycle events with forwarding policy hook:
  - `should_forward_worker_event_to_user(...)` (v1: always `True`)
- expose forwardable worker event feed (`list_forward_events`) for main-agent context injection

## Task Scheduler Store

Primary module:
- `src/zubot/core/task_scheduler_store.py`

Responsibilities:
- persist defined-task definitions and run queue state in SQLite
- enqueue due runs based on interval cadence or calendar wall-clock cadence
- enqueue manual runs for on-demand profile triggers
- claim queued runs deterministically for consumer processing
- record run completion status (`done`, `failed`, `blocked`) and update schedule last-run metadata
- persist completion snapshots into `defined_task_run_history` for bounded historical reporting

Default DB path:
- `memory/central/zubot_core.db`
- override via `central_service.scheduler_db_path`
- schema evolution reference: `docs/src/zubot/central_db_schema.md`

## Task-Agent Runner

Primary module:
- `src/zubot/core/task_agent_runner.py`

Responsibilities:
- resolve predefined task scripts from `pre_defined_tasks.tasks`
- generate textual run descriptions for user-facing check-in
- execute predefined scripts through a controlled subprocess runner
- pass run payload context to scripts via `ZUBOT_TASK_PAYLOAD_JSON`
- return normalized run result payloads

Behavior note:
- execution is deterministic script-based and bounded by configured entrypoint + timeout
- deeper task routing/parameter schemas can be layered on top of this contract

## Worker Capacity Policy

Primary module:
- `src/zubot/core/worker_capacity_policy.py`

Responsibilities:
- define reusable capacity checks for task-agent initiated worker escalation
- preserve configurable worker-slot reserve boundaries in policy code
- enforce reserve-aware dispatch in `WorkerManager.spawn_worker(...)` when `requested_by` is a task-agent identity

## Central Service

Primary module:
- `src/zubot/core/central_service.py`

Responsibilities:
- run a single-process scheduler/consumer loop for task-agent jobs
- sync schedule config into the scheduler store
- enqueue due runs on each poll
- consume queued runs with bounded concurrency (`central_service.task_runner_concurrency`)
- run housekeeping for 24/7 safety:
  - prune completed run history via retention policy
  - run memory-manager sweeps (periodic + completion-debounced)
- expose central runtime status and task-agent check-in view

Status/check-in surface:
- reports service runtime state:
  - `running`
  - `enabled_in_config`
  - poll/concurrency settings
- reports queue/runtime counts:
  - queued/running runs
- reports per-profile check-in:
  - `free`/`queued`/`running`
  - current run description
  - queue position (when queued)
  - last result summary/error
- main-agent check-in helper:
  - registry tool `get_task_agent_checkin` returns structured status plus concise text summary

Activation:
- runtime is implemented but disabled by default via `central_service.enabled`
- daemon startup (`python -m src.zubot.daemon.main`) auto-starts central loop only when config-enabled
- app startup initializes runtime in client mode and does not own central lifecycle
