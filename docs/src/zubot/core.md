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

## Tool Registry

Primary module:
- `src/zubot/core/tool_registry.py`

Responsibilities:
- maintain one canonical registry of callable tool contracts
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
- track `messages_since_last_summary` and finalization state
- support pending-day queries for startup finalization workflows

## Daily Memory

Primary module:
- `src/zubot/core/daily_memory.py`

Responsibilities:
- create and write day-scoped raw/summary memory files:
  - `memory/daily/raw/YYYY-MM-DD.md`
  - `memory/daily/summary/YYYY-MM-DD.md`
- append turn-level raw log entries for completed interactions
- load recent summary files (today + yesterday by default) and refresh before turn assembly
- write summary snapshots for buffered turn batches (replace summary file content per update)
- support writing to explicit day IDs for finalization/backfill cases

Behavior note:
- daily memory persists across session resets
- session reset clears in-memory chat context only

## Local App Runtime Behavior

Primary module:
- `app/chat_logic.py`

Responsibilities:
- maintain per-session runtime state (`recent_events`, rolling summary, facts)
- provide explicit session initialization API behavior (preload before first message)
- refresh recent daily memory before each chat turn
- append completed-turn entries to the current daily memory file
- expose session reset that clears in-memory state while preserving persisted daily memory
- execute an iterative model/tool loop for LLM-routed requests:
  - include tool schemas from `tool_registry`
  - execute model tool calls through `invoke_tool(...)`
  - append tool outputs back into the model message stream
  - stop on final assistant text or max tool-loop step guard

## Sub-Agent Runtime

Primary module:
- `src/zubot/core/sub_agent_runner.py`

Responsibilities:
- execute scoped worker tasks from `TaskEnvelope`
- reuse context assembly/token budgeting/memory primitives
- return normalized `WorkerResult` payloads
- support stateless worker behavior between tasks
