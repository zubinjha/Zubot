# Zubot Kernel
This document defines invariant runtime assumptions for Zubot.

## File System Model
- All file paths are interpreted as relative to the repository root unless explicitly stated otherwise.
- No `../` traversal is assumed.
- Paths are never interpreted relative to the current markdown file.

