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


def local_day_str(*, now: datetime | None = None) -> str:
    return (now or _now_local()).strftime("%Y-%m-%d")


def _daily_dir(*, root: Path | None = None, base_dir: str = "memory/daily") -> Path:
    root_path = root or repo_root()
    path = root_path / base_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _daily_layer_dir(*, layer: str, root: Path | None = None, base_dir: str = "memory/daily") -> Path:
    if layer not in {"raw", "summary"}:
        raise ValueError("layer must be 'raw' or 'summary'")
    path = _daily_dir(root=root, base_dir=base_dir) / layer
    path.mkdir(parents=True, exist_ok=True)
    return path


def daily_memory_path(*, day: datetime | None = None, root: Path | None = None, layer: str = "summary") -> Path:
    dt = day or _now_local()
    return _daily_layer_dir(layer=layer, root=root) / f"{dt.strftime('%Y-%m-%d')}.md"


def _legacy_daily_memory_path(*, day: datetime | None = None, root: Path | None = None) -> Path:
    dt = day or _now_local()
    return _daily_dir(root=root) / f"{dt.strftime('%Y-%m-%d')}.md"


def ensure_daily_memory_file(*, day: datetime | None = None, root: Path | None = None, layer: str = "summary") -> Path:
    path = daily_memory_path(day=day, root=root, layer=layer)
    if not path.exists():
        title = path.stem
        header = "Daily Summary" if layer == "summary" else "Daily Raw"
        path.write_text(f"# {header} {title}\n\n", encoding="utf-8")
    return path


def append_daily_memory_entry(
    *,
    text: str,
    session_id: str | None = None,
    kind: str = "note",
    day: datetime | None = None,
    day_str: str | None = None,
    event_time: datetime | None = None,
    root: Path | None = None,
    layer: str = "raw",
) -> dict[str, Any]:
    if not text.strip():
        return {"ok": False, "error": "empty_text", "path": None}

    now = day or _now_local()
    if day_str:
        now = datetime.strptime(day_str, "%Y-%m-%d")
    stamp_dt = event_time or day or _now_local()
    path = ensure_daily_memory_file(day=now, root=root, layer=layer)
    timestamp = stamp_dt.strftime("%H:%M:%S")
    sid = f" ({session_id})" if session_id else ""
    line = f"- [{timestamp}] [{kind}]{sid} {text.strip()}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return {"ok": True, "error": None, "path": str(path), "entry": line.rstrip("\n")}


def write_daily_summary_snapshot(
    *,
    text: str,
    session_id: str | None = None,
    day: datetime | None = None,
    day_str: str | None = None,
    event_time: datetime | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Replace the daily summary file with the latest concise summary snapshot."""
    if not text.strip():
        return {"ok": False, "error": "empty_text", "path": None}

    now = day or _now_local()
    if day_str:
        now = datetime.strptime(day_str, "%Y-%m-%d")
    stamp_dt = event_time or day or _now_local()

    path = ensure_daily_memory_file(day=now, root=root, layer="summary")
    timestamp = stamp_dt.strftime("%H:%M:%S")
    sid = f" ({session_id})" if session_id else ""
    body = text.strip()
    if not body.startswith("-"):
        body = f"- {body}"

    rendered = (
        f"# Daily Summary {now.strftime('%Y-%m-%d')}\n\n"
        f"- Last updated: [{timestamp}]{sid}\n\n"
        f"{body}\n"
    )
    path.write_text(rendered, encoding="utf-8")
    return {"ok": True, "error": None, "path": str(path)}


def load_recent_daily_memory(
    *,
    days: int = 2,
    root: Path | None = None,
) -> dict[str, str]:
    """Load recent summary files (with legacy fallback for pre-migration files)."""
    if days <= 0:
        return {}
    now = _now_local()
    loaded: dict[str, str] = {}
    for offset in range(days):
        day = now - timedelta(days=offset)
        path = daily_memory_path(day=day, root=root, layer="summary")
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8")
            if text.strip():
                loaded[path.as_posix()] = text
            continue

        legacy = _legacy_daily_memory_path(day=day, root=root)
        if legacy.exists() and legacy.is_file():
            text = legacy.read_text(encoding="utf-8")
            if text.strip():
                loaded[legacy.as_posix()] = text
    return loaded
