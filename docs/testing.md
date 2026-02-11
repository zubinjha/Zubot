# Testing

Automated tests are tracked in `tests/`.

Current layout:
- `tests/tools/`: validates output contracts for `src/zubot/tools/*`

Execution:
- `source .venv/bin/activate`
- `python -m pytest -q`

Guidelines:
- Keep unit tests fast and deterministic.
- Mock external providers when tool integrations are added.
- Add integration tests behind explicit env flags or markers.
