# Tests

Tracked automated tests live in this folder.

Current layout:
- `tests/app/`: app API + chat logic behavior
- `tests/core/`: runtime/central service/memory/scheduler/config internals
- `tests/tools/`: unit/contract tests for tool modules in `src/zubot/tools/`

Run:
- `source .venv/bin/activate`
- `python -m pytest -q`
