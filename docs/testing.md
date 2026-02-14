# Testing

Automated tests are tracked in `tests/`.

Current layout:
- `tests/app/`: API/chat/UI-surface behavior
- `tests/core/`: runtime, scheduler, memory, and config contracts
- `tests/tools/`: tool module contract/unit tests

Execution:
- `source .venv/bin/activate`
- `python -m pytest -q`

Guidelines:
- Keep unit tests fast and deterministic.
- Mock external providers when tool integrations are added.
- Add integration tests behind explicit env flags or markers.
