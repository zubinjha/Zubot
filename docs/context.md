# Context

`context/` holds runtime guidance and personalization inputs for the agent.

Expected files:
- `context/KERNEL.md`: invariant runtime assumptions and global file/path semantics
- `context/AGENT.md`: operational behavior and response conventions
- `context/SOUL.md`: philosophy, tone, and long-horizon principles
- `context/USER.md`: stable user preferences, goals, and constraints
- `context/TASK_AGENT.md`: task-agent operational behavior for scheduled routines
- `context/TASK_SOUL.md`: task-agent philosophy/tone layer for autonomous runs

Tracking model:
- Keep `context/README.md` tracked as the local folder contract.
- Keep other `context/` files local by default unless intentionally shared.
