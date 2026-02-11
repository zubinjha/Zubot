"""Filesystem path normalization and access policy helpers."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from .config_loader import load_config

AccessMode = Literal["read", "write"]

DEFAULT_FILESYSTEM_POLICY: dict[str, Any] = {
    "default_access": "deny",
    "allow_read": ["**"],
    "allow_write": ["memory/**", "outputs/**"],
    "deny": ["config/config.json", ".git/**", ".venv/**"],
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_repo_path(path: str | Path) -> Path:
    """Resolve repository-relative path and reject traversal patterns."""
    raw = Path(path)
    if raw.is_absolute():
        raise ValueError("Absolute paths are not supported for repo-scoped file tools.")
    if ".." in raw.parts:
        raise ValueError("Path traversal (`..`) is not allowed.")

    root = repo_root().resolve()
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Resolved path escapes repository root.")
    return candidate


def normalize_repo_path(path: str | Path) -> str:
    """Return repository-root-relative POSIX path string."""
    root = repo_root().resolve()
    resolved = resolve_repo_path(path)
    rel = resolved.relative_to(root).as_posix()
    return "." if rel == "." else rel


def get_filesystem_policy(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = config or load_config()
    fs = payload.get("filesystem")
    raw = fs if isinstance(fs, dict) else {}

    def _list_of_strings(value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            return fallback
        result = [item for item in value if isinstance(item, str) and item.strip()]
        return result or fallback

    default_access = raw.get("default_access", DEFAULT_FILESYSTEM_POLICY["default_access"])
    if default_access not in {"allow", "deny"}:
        default_access = DEFAULT_FILESYSTEM_POLICY["default_access"]

    return {
        "default_access": default_access,
        "allow_read": _list_of_strings(raw.get("allow_read"), DEFAULT_FILESYSTEM_POLICY["allow_read"]),
        "allow_write": _list_of_strings(raw.get("allow_write"), DEFAULT_FILESYSTEM_POLICY["allow_write"]),
        "deny": _list_of_strings(raw.get("deny"), DEFAULT_FILESYSTEM_POLICY["deny"]),
    }


def _pattern_matches(path: str, pattern: str) -> bool:
    target = path or "."
    if pattern == "**":
        return True
    if fnmatch(target, pattern):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return target == prefix or target.startswith(prefix + "/")
    return False


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_pattern_matches(path, pattern) for pattern in patterns)


def check_access(path: str | Path, mode: AccessMode, config: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Check whether `path` is allowed for `mode` under filesystem policy."""
    rel_path = normalize_repo_path(path)
    policy = get_filesystem_policy(config=config)

    if _matches_any(rel_path, policy["deny"]):
        return False, f"{mode} denied by policy for '{rel_path}'."

    allow_patterns = policy["allow_read"] if mode == "read" else policy["allow_write"]
    if _matches_any(rel_path, allow_patterns):
        return True, ""

    if policy["default_access"] == "allow":
        return True, ""
    return False, f"{mode} not allowed by policy for '{rel_path}'."


def can_read(path: str | Path, config: dict[str, Any] | None = None) -> bool:
    allowed, _ = check_access(path, "read", config=config)
    return allowed


def can_write(path: str | Path, config: dict[str, Any] | None = None) -> bool:
    allowed, _ = check_access(path, "write", config=config)
    return allowed
