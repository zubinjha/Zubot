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
