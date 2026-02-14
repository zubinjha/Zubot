# AGENTS

This file defines how agents should operate inside this repository.

## Objective
- Maintain and evolve Zubot as a local-first, config-driven personal agent framework.
- Prefer correctness, clarity, and reproducible changes.

## Startup Order
1. Read `README.md`.
2. Read `context/AGENT.md`, `context/SOUL.md`, and `context/USER.md`.
3. Read `docs/README.md` and the relevant component docs.
4. Load config via `src/zubot/core/config_loader.py` (do not parse config ad hoc).
5. Prefer daemon-first local runtime startup (`python -m src.zubot.daemon.main`) unless intentionally doing UI-only iteration.

## Source Of Truth
- Runtime config: `config/config.json` (ignored, local only).
- Config schema reference: `config/example_config.json` (tracked).
- Kernel assumptions: `src/zubot/core/KERNEL.md`.
- Config structure rule: keep `config/config.json` and `config/example_config.json` schema-aligned in the same change set (example file uses placeholders only).
- Central runtime config contracts live under:
  - `central_service.*`
  - `pre_defined_tasks.*`

## Tooling Conventions
- Kernel tools live in `src/zubot/tools/kernel/`.
- Keep public exports wired through:
  - `src/zubot/tools/kernel/__init__.py`
  - `src/zubot/tools/__init__.py`
- Add tests for all new behavior under `tests/`.

## File Path Policy
- Treat paths as repository-root-relative by default.
- Do not rely on markdown-file-relative assumptions for runtime logic.
- Avoid `..` traversal in runtime file tools.

## Safety Rules
- Never expose or commit secrets from `config/config.json`.
- Never print secret values from `tool_profiles.user_specific.*` (for example API keys, OAuth secrets, refresh tokens) in agent responses, logs, or docs.
- Prefer non-destructive operations unless the user explicitly requests destructive actions.
- Validate and test changes before claiming completion.

## Validation
- Activate venv: `source .venv/bin/activate`
- Run test suite: `python -m pytest -q`

## Documentation Maintenance Rule
- When behavior, architecture, or workflow changes are implemented, update documentation in the same change set.
- At minimum, verify and update:
  - `AGENTS.md` for operational rules and startup/resume behavior
  - `README.md` for current project status and entrypoint guidance
  - relevant files in `docs/` for component contracts and structure
  - `docs/src/zubot/tools.md` whenever tool config paths or registry layering changes
  - `docs/src/zubot/central_service.md` when scheduler/queue/check-in contracts change
