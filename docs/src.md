# Source

`src/` will hold application code once implementation begins.

Current scope:
- architecture-first phase
- no required runtime modules yet

Planned top-level folders:
- `src/core/`: static, high-importance runtime context and orchestration primitives
- `src/core/kernel/`: foundational rules and baseline behavior the agent should always know
- `src/mcps/`: MCP server integrations and adapters
- `src/skills/`: skill definitions and skill-loading logic
- `src/tools/`: tool wrappers, invocation logic, and safety boundaries

Future documentation will further define:
- core agent loop modules
- context pipeline integration
- tool execution and permission boundaries
