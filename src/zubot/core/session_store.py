"""Session event persistence utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_types import SessionEvent
from .path_policy import repo_root


def _sessions_dir(base_dir: str = "memory/sessions", *, root: Path | None = None) -> Path:
    root_path = root or repo_root()
    path = root_path / base_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_log_path(session_id: str, *, base_dir: str = "memory/sessions", root: Path | None = None) -> Path:
    safe = session_id.replace("/", "_")
    return _sessions_dir(base_dir, root=root) / f"{safe}.jsonl"


def append_session_events(
    session_id: str,
    events: list[SessionEvent | dict[str, Any]],
    *,
    base_dir: str = "memory/sessions",
    root: Path | None = None,
) -> None:
    path = session_log_path(session_id, base_dir=base_dir, root=root)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            payload = event.to_dict() if isinstance(event, SessionEvent) else event
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_session_events(
    session_id: str,
    *,
    base_dir: str = "memory/sessions",
    root: Path | None = None,
) -> list[dict[str, Any]]:
    path = session_log_path(session_id, base_dir=base_dir, root=root)
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            events.append(parsed)
    return events
