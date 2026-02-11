# Source

`src/` will hold application code once implementation begins.

Current scope:
- architecture-first phase
- no required runtime modules yet

Planned top-level folders:
- `src/zubot/core/`: static, high-importance runtime context and orchestration primitives
- `src/zubot/core/kernel/`: foundational rules and baseline behavior the agent should always know
- `src/zubot/mcps/`: MCP server integrations and adapters
- `src/zubot/skills/`: skill definitions and skill-loading logic
- `src/zubot/tools/`: tool wrappers, invocation logic, and safety boundaries
- `src/zubot/tools/kernel/`: built-in personal/kernel tools (location, time, weather)

Future documentation will further define:
- core agent loop modules
- context pipeline integration
- tool execution and permission boundaries

Component docs:
- [docs/src/zubot/core.md](src/zubot/core.md)
- [docs/src/zubot/tools.md](src/zubot/tools.md)
