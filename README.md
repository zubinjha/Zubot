# Zubot

Zubot is a local-first personal AI agent framework.

Primary use cases:
- coding orchestration
- todo management
- future job automation

Deeper architectural documentation lives in [docs/README.md](docs/README.md).

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
- Core agent runtime scaffolding in `src/zubot/core/`:
  - agent loop + event schemas
  - sub-agent runner scaffold + delegation path
  - config-driven LLM client (OpenRouter adapter)
  - centralized tool registry and dispatch helpers
  - context loading/assembly pipeline
  - context state/policy + rolling summary + fact extraction
  - token estimation + budget checks
  - session event persistence + daily memory helpers
  - SQLite-backed day-status index for summary/finalization tracking
- Automated tests in `tests/` with `pytest`.

## Agent Resume Checklist
For new agents or fresh sessions, use this order:
1. Read `AGENTS.md` (repo-level execution rules and startup contract).
2. Read `context/AGENT.md`, `context/SOUL.md`, and `context/USER.md`.
3. Read `docs/README.md` and relevant docs under `docs/src/zubot/`.
4. Load runtime config from `config/config.json` via `src/zubot/core/config_loader.py`.
5. Run tests before and after edits:
   - `source .venv/bin/activate`
   - `python -m pytest -q`

## Security Notes
- Never commit secrets from `config/config.json`.
- Use `config/example_config.json` as the committed schema reference.

## Local Web Chat (Test UI)
- Minimal local app lives in `app/`.
- Run:
  - `source .venv/bin/activate`
  - `python -m uvicorn app.main:app --reload --port 8000`
- Open: `http://127.0.0.1:8000`
- Supports `session_id` scoping and session reset via `/api/session/reset`.
- Supports explicit session initialization via `/api/session/init`.
- Session reset clears chat working context but preserves local daily memory files.
- Daily memory is split into raw logs and summary snapshots under `memory/daily/`.
- Session JSONL logging is optional (`memory.session_event_logging_enabled`) and disabled by default.
- LLM-routed queries run through a registry-backed tool-call loop (tool schema -> tool execution -> final response).
- UI now includes:
  - chat-style message timeline
  - live in-flight progress states (thinking/context/tool-check phases)
  - runtime panel with route, tool-call record, and last reply snapshot
  - auto session initialization on page load/session change
- App chat uses unified LLM + registry tool loop (no keyword-based direct routing).
