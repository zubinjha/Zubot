# Source

`src/` contains the active Zubot runtime implementation.

Current key folders:
- `src/zubot/core/`
  - chat/runtime orchestration
  - central scheduler service + SQLite store
  - memory pipeline (raw events, summary queue, summary worker, memory manager)
  - config loading + context assembly
- `src/zubot/runtime/`
  - shared runtime owner facade (`RuntimeService`)
- `src/zubot/daemon/`
  - daemon-first startup entrypoint
- `src/zubot/predefined_tasks/`
  - executable predefined task scripts referenced by config
- `src/zubot/tools/`
  - tool modules + registry wiring

Primary docs:
- [docs/src/zubot/core.md](src/zubot/core.md)
- [docs/src/zubot/central_service.md](src/zubot/central_service.md)
- [docs/src/zubot/central_db_schema.md](src/zubot/central_db_schema.md)
- [docs/src/zubot/tools.md](src/zubot/tools.md)
