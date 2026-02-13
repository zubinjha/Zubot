# Memory

`memory/` is for session history and longer-term memory artifacts.

Current implementation:
- Daily memory content is SQLite-backed in `memory/central/zubot_core.db`:
  - raw events: `daily_memory_events`
  - summary snapshots: `daily_memory_summaries`
  - day status metadata: `day_memory_status`
  - summary queue: `memory_summary_jobs`
- Raw logs are transcript-style entries:
  - `[user]` for human messages
  - `[main_agent]` for assistant replies
  - `[task_agent_event]` for central scheduler lifecycle milestones (`run_queued`, `run_finished`, `run_failed`, `run_blocked`)
- Signal filtering policy:
  - keep user/main-agent conversational intent as primary memory signal
  - keep only milestone task lifecycle outcomes (queue + terminal status)
  - drop worker internals, routine system chatter, route/debug metadata, and tool-call telemetry from daily memory rows
- Summary rows are rewritten as snapshots on each successful summary job (not endlessly appended).
- Summary generation is queue-driven (SQLite table `memory_summary_jobs` in `memory/central/zubot_core.db`):
  - chat-turn ingestion enqueues day summary jobs
  - task-agent lifecycle ingestion enqueues day summary jobs
  - active-job dedupe avoids multiple queued/running jobs for the same day
- Summary execution is handled by background worker thread (`src/zubot/core/memory_summary_worker.py`) so chat/task flows are not blocked by summarization work.
- Startup and periodic sweeps finalize prior pending days by summarizing the full raw day transcript.
- Daily summarization prompt is explicitly transcript-aware (it knows all entry types above).
- Summary output target is narrative and decision-focused:
  - What user wanted
  - Key decisions
  - What was executed
  - Final state
- If a summary batch is too large for safe model input, summarization recursively splits the batch into segments, summarizes each segment, then merges summaries.
- Chat context autoloads summary snapshots first (default: today + yesterday) and falls back to trimmed raw-day snapshots when summary rows are not yet available.
- Resetting a chat session clears in-memory conversation context only; persisted DB memory remains.
- Full-fidelity event logs in `memory/sessions/*.jsonl` are optional and disabled by default via config.
- Day status/index metadata is tracked in the central runtime DB `memory/central/zubot_core.db` (table `day_memory_status`):
  - `total_messages`
  - `last_summarized_total`
  - `messages_since_last_summary`
  - summary/finalization status
  - pending count resets to 0 after successful day summary completion
- Daily summary generation defaults to deterministic summarization; optional low-model summarization can be enabled via config.
- Raw/summary timestamps reflect event time.
- Legacy markdown files under `memory/daily/` are migratable legacy inputs:
  - runtime can import them into SQLite
  - after import they are optional and may be removed

Planned direction:
- improve summarization quality and retrieval of older daily notes
- tighten privacy/redaction policies for persisted entries
- converge memory status + scheduler/run state toward one centralized SQLite authority (see `docs/src/zubot/central_db_schema.md`)
- keep markdown memory files as optional export artifacts only (not source of truth)
- legacy `memory/memory_index.sqlite3` rows are imported into central DB on schema initialization when present
- central runtime memory-manager backstop sweep:
  - periodic sweep cadence (default 12h)
  - completion-triggered debounced sweep for prior non-finalized days

Relevant `memory` config keys:
- `autoload_summary_days`
- `session_event_logging_enabled`
- `session_ttl_minutes`
- `max_active_sessions`
- `realtime_summary_turn_threshold`
- `summary_worker_poll_sec`
- `summary_worker_max_jobs_per_tick`
- `daily_summary_use_model`

Repository rule:
- `memory/` contents are local-only and ignored by default.
- Documentation for memory behavior lives here in `docs/memory.md` (not inside `memory/`).

Retention:
- Use `src/zubot/core/session_store.py::cleanup_session_logs_older_than(days=...)` to remove stale JSONL session logs.
