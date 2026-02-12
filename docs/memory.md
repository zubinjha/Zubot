# Memory

`memory/` is for session history and longer-term memory artifacts.

Current implementation:
- `memory/daily/YYYY-MM-DD.md` files are created on demand.
- Chat turns are buffered in memory and written as compact summary entries (not per-turn raw logs).
- Default flush policy:
  - periodic flush every few turns
  - forced flush on session reset
- Chat context refreshes daily memory (today + yesterday) each turn before LLM assembly.
- Resetting a chat session clears in-memory conversation context only; daily files remain.
- Full-fidelity event logs can be stored separately in `memory/sessions/*.jsonl`.

Planned direction:
- improve summarization quality and retrieval of older daily notes
- tighten privacy/redaction policies for persisted entries

Repository rule:
- `memory/` contents are local-only and ignored by default.
- Documentation for memory behavior lives here in `docs/memory.md` (not inside `memory/`).

Retention:
- Use `src/zubot/core/session_store.py::cleanup_session_logs_older_than(days=...)` to remove stale JSONL session logs.
