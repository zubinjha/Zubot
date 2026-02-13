# Memory

`memory/` is for session history and longer-term memory artifacts.

Current implementation:
- Daily memory is split into:
  - raw per-day logs: `memory/daily/raw/YYYY-MM-DD.md`
  - summary snapshot files: `memory/daily/summary/YYYY-MM-DD.md`
- Raw logs are transcript-style entries:
  - `[user]` for human messages
  - `[main_agent]` for assistant replies
  - `[worker_event]` for forwarded worker-to-main events (when present)
  - `[task_agent_event]` for central scheduler/task-agent lifecycle events
  - `[tool_event]` for explicit tool execution outcomes
  - `[system]` for orchestration/runtime status signals
- Summary files are rewritten as snapshots on flush (not endlessly appended).
- Default flush policy:
  - periodic flush every 30 turns
  - forced flush on session reset
- Daily summarization prompt is explicitly transcript-aware (it knows all entry types above).
- If a summary batch is too large for safe model input, summarization recursively splits the batch into segments, summarizes each segment, then merges summaries.
- Chat context autoloads summary files only (default: today + yesterday) each turn before LLM assembly.
- Resetting a chat session clears in-memory conversation context only; daily files remain.
- Full-fidelity event logs in `memory/sessions/*.jsonl` are optional and disabled by default via config.
- Day status/index metadata is tracked in the central runtime DB `memory/central/zubot_core.db` (table `day_memory_status`):
  - `messages_since_last_summary`
  - summary/finalization status
  - pending count resets to 0 on successful summary flush to avoid drift
- Daily summary generation attempts the `low` model alias first, with deterministic fallback summarization if unavailable.
- Raw/summary timestamps reflect event time (no forced midnight timestamps when writing with `day_str`).

Planned direction:
- improve summarization quality and retrieval of older daily notes
- tighten privacy/redaction policies for persisted entries
- converge memory status + scheduler/run state toward one centralized SQLite authority (see `docs/src/zubot/central_db_schema.md`)
- legacy `memory/memory_index.sqlite3` rows are imported into central DB on schema initialization when present
- central runtime memory-manager backstop sweep:
  - periodic sweep cadence (default 12h)
  - completion-triggered debounced sweep for prior non-finalized days

Repository rule:
- `memory/` contents are local-only and ignored by default.
- Documentation for memory behavior lives here in `docs/memory.md` (not inside `memory/`).

Retention:
- Use `src/zubot/core/session_store.py::cleanup_session_logs_older_than(days=...)` to remove stale JSONL session logs.
