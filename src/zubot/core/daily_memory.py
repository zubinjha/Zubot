"""Daily memory file helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config_loader import get_timezone
from .path_policy import repo_root


def _now_local() -> datetime:
    try:
        return datetime.now(ZoneInfo(get_timezone()))
    except Exception:
        return datetime.utcnow()


def _daily_dir(*, root: Path | None = None, base_dir: str = "memory/daily") -> Path:
    root_path = root or repo_root()
    path = root_path / base_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def daily_memory_path(*, day: datetime | None = None, root: Path | None = None) -> Path:
    dt = day or _now_local()
    return _daily_dir(root=root) / f"{dt.strftime('%Y-%m-%d')}.md"


def ensure_daily_memory_file(*, day: datetime | None = None, root: Path | None = None) -> Path:
    path = daily_memory_path(day=day, root=root)
    if not path.exists():
        title = path.stem
        path.write_text(f"# Daily Memory {title}\n\n", encoding="utf-8")
    return path


def append_daily_memory_entry(
    *,
    text: str,
    session_id: str | None = None,
    kind: str = "note",
    root: Path | None = None,
) -> dict[str, Any]:
    if not text.strip():
        return {"ok": False, "error": "empty_text", "path": None}

    now = _now_local()
    path = ensure_daily_memory_file(day=now, root=root)
    timestamp = now.strftime("%H:%M:%S")
    sid = f" ({session_id})" if session_id else ""
    line = f"- [{timestamp}] [{kind}]{sid} {text.strip()}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return {"ok": True, "error": None, "path": str(path), "entry": line.rstrip("\n")}


def load_recent_daily_memory(
    *,
    days: int = 2,
    root: Path | None = None,
) -> dict[str, str]:
    if days <= 0:
        return {}
    now = _now_local()
    loaded: dict[str, str] = {}
    for offset in range(days):
        day = now - timedelta(days=offset)
        path = daily_memory_path(day=day, root=root)
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text.strip():
            loaded[path.as_posix()] = text
    return loaded
