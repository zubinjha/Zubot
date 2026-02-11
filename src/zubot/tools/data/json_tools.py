"""JSON convenience helpers layered over kernel filesystem tools."""

from __future__ import annotations

import json
from typing import Any

from src.zubot.tools.kernel.filesystem import read_file, write_file


def read_json(path: str) -> dict[str, Any]:
    payload = read_file(path)
    if not payload.get("ok"):
        return {
            "ok": False,
            "path": payload.get("path", path),
            "data": None,
            "error": payload.get("error"),
            "source": "json_read",
        }

    try:
        data = json.loads(payload["content"])
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "path": payload.get("path", path),
            "data": None,
            "error": f"Invalid JSON: {exc}",
            "source": "json_read",
        }

    return {
        "ok": True,
        "path": payload.get("path", path),
        "data": data,
        "error": None,
        "source": "json_read",
    }


def write_json(
    path: str,
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    create_parents: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    serialized = json.dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n"
    payload = write_file(
        path,
        serialized,
        create_parents=create_parents,
        dry_run=dry_run,
    )
    return {
        "ok": payload.get("ok", False),
        "path": payload.get("path", path),
        "written_bytes": payload.get("written_bytes", 0),
        "dry_run": payload.get("dry_run", False),
        "error": payload.get("error"),
        "source": "json_write",
    }
