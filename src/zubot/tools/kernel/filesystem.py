"""Policy-enforced filesystem primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from src.zubot.core.path_policy import check_access, normalize_repo_path, resolve_repo_path

WriteMode = Literal["overwrite", "error_if_exists"]


def _error_payload(source: str, path: str, message: str) -> dict[str, Any]:
    return {"ok": False, "path": path, "error": message, "source": source}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_file(path: str, *, encoding: str = "utf-8") -> dict[str, Any]:
    """Read a text file with read-policy enforcement."""
    source = "filesystem_read"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "read")
        if not allowed:
            return _error_payload(source, rel, reason)
        if not resolved.exists():
            return _error_payload(source, rel, "Path does not exist.")
        if not resolved.is_file():
            return _error_payload(source, rel, "Path is not a file.")

        content = resolved.read_text(encoding=encoding)
        return {
            "ok": True,
            "path": rel,
            "content": content,
            "bytes": resolved.stat().st_size,
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return _error_payload(source, str(path), str(exc))


def list_dir(path: str = ".") -> dict[str, Any]:
    """List directory entries with read-policy enforcement."""
    source = "filesystem_list"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "read")
        if not allowed:
            return _error_payload(source, rel, reason)
        if not resolved.exists():
            return _error_payload(source, rel, "Path does not exist.")
        if not resolved.is_dir():
            return _error_payload(source, rel, "Path is not a directory.")

        entries: list[dict[str, Any]] = []
        for entry in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
            child_rel = entry.relative_to(resolve_repo_path(".")).as_posix()
            child_allowed, _ = check_access(child_rel, "read")
            if not child_allowed:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": child_rel,
                    "type": "dir" if entry.is_dir() else "file",
                }
            )

        return {
            "ok": True,
            "path": rel,
            "entries": entries,
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return _error_payload(source, str(path), str(exc))


def path_exists(path: str) -> dict[str, Any]:
    """Check path existence while enforcing read policy."""
    source = "filesystem_exists"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "read")
        if not allowed:
            return {
                "ok": False,
                "path": rel,
                "exists": False,
                "error": reason,
                "source": source,
            }

        return {
            "ok": True,
            "path": rel,
            "exists": resolved.exists(),
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(path),
            "exists": False,
            "error": str(exc),
            "source": source,
        }


def stat_path(path: str) -> dict[str, Any]:
    """Return basic stat metadata for a path."""
    source = "filesystem_stat"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "read")
        if not allowed:
            return _error_payload(source, rel, reason)
        if not resolved.exists():
            return _error_payload(source, rel, "Path does not exist.")

        stat = resolved.stat()
        return {
            "ok": True,
            "path": rel,
            "stat": {
                "is_file": resolved.is_file(),
                "is_dir": resolved.is_dir(),
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            },
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return _error_payload(source, str(path), str(exc))


def write_file(
    path: str,
    content: str,
    *,
    mode: WriteMode = "overwrite",
    create_parents: bool = False,
    dry_run: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Write a text file with write-policy enforcement."""
    source = "filesystem_write"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "write")
        if not allowed:
            return _error_payload(source, rel, reason)
        if mode not in {"overwrite", "error_if_exists"}:
            return _error_payload(source, rel, "Unsupported write mode.")
        if resolved.exists() and mode == "error_if_exists":
            return _error_payload(source, rel, "File already exists.")

        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        elif not resolved.parent.exists():
            return _error_payload(source, rel, "Parent directory does not exist.")

        if dry_run:
            return {
                "ok": True,
                "path": rel,
                "written_bytes": len(content.encode(encoding)),
                "dry_run": True,
                "written_at": _now_utc_iso(),
                "error": None,
                "source": source,
            }

        resolved.write_text(content, encoding=encoding)
        return {
            "ok": True,
            "path": rel,
            "written_bytes": len(content.encode(encoding)),
            "dry_run": False,
            "written_at": _now_utc_iso(),
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return _error_payload(source, str(path), str(exc))


def append_file(
    path: str,
    content: str,
    *,
    create_parents: bool = False,
    dry_run: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Append text to a file with write-policy enforcement."""
    source = "filesystem_append"
    try:
        resolved = resolve_repo_path(path)
        rel = normalize_repo_path(path)
        allowed, reason = check_access(path, "write")
        if not allowed:
            return _error_payload(source, rel, reason)

        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        elif not resolved.parent.exists():
            return _error_payload(source, rel, "Parent directory does not exist.")

        if dry_run:
            return {
                "ok": True,
                "path": rel,
                "written_bytes": len(content.encode(encoding)),
                "dry_run": True,
                "written_at": _now_utc_iso(),
                "error": None,
                "source": source,
            }

        with resolved.open("a", encoding=encoding) as fh:
            fh.write(content)

        return {
            "ok": True,
            "path": rel,
            "written_bytes": len(content.encode(encoding)),
            "dry_run": False,
            "written_at": _now_utc_iso(),
            "error": None,
            "source": source,
        }
    except Exception as exc:
        return _error_payload(source, str(path), str(exc))
