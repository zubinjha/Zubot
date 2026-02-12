# Memory

`memory/` is for session history and longer-term memory artifacts.

Current implementation:
- Daily memory is split into:
  - raw per-day logs: `memory/daily/raw/YYYY-MM-DD.md`
  - summary snapshot files: `memory/daily/summary/YYYY-MM-DD.md`
- Chat turns are appended to raw logs as lightweight structured lines.
- Summary files are rewritten as snapshots on flush (not endlessly appended).
- Default flush policy:
  - periodic flush every 30 turns
  - forced flush on session reset
- Chat context autoloads summary files only (default: today + yesterday) each turn before LLM assembly.
- Resetting a chat session clears in-memory conversation context only; daily files remain.
- Full-fidelity event logs in `memory/sessions/*.jsonl` are optional and disabled by default via config.
- Day status/index metadata is tracked in `memory/memory_index.sqlite3`:
  - `messages_since_last_summary`
  - summary/finalization status
  - pending count resets to 0 on successful summary flush to avoid drift
- Daily summary generation attempts the `low` model alias first, with deterministic fallback summarization if unavailable.

Planned direction:
- improve summarization quality and retrieval of older daily notes
- tighten privacy/redaction policies for persisted entries

Repository rule:
- `memory/` contents are local-only and ignored by default.
- Documentation for memory behavior lives here in `docs/memory.md` (not inside `memory/`).

Retention:
- Use `src/zubot/core/session_store.py::cleanup_session_logs_older_than(days=...)` to remove stale JSONL session logs.
