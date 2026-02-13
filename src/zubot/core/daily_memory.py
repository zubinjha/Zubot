"""Daily memory helpers backed by SQLite (raw events + summaries)."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config_loader import get_timezone
from .memory_index import memory_index_path
from .path_policy import repo_root

_RAW_LINE_RE = re.compile(r"^- \[(?P<time>[^\]]+)\] \[(?P<kind>[^\]]+)\](?: \((?P<sid>[^)]+)\))? (?P<text>.*)$")


def _now_local() -> datetime:
    try:
        tz = get_timezone()
        if isinstance(tz, str) and tz.strip():
            return datetime.now(ZoneInfo(tz))
    except Exception:
        pass
    return datetime.now(UTC)


def local_day_str(*, now: datetime | None = None) -> str:
    return (now or _now_local()).strftime("%Y-%m-%d")


def _connect(*, root: Path | None = None, db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or memory_index_path(root=root))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_daily_memory_schema(*, root: Path | None = None, db_path: Path | None = None) -> None:
    resolved = db_path or memory_index_path(root=root)
    with _connect(root=root, db_path=resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_memory_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                event_time TEXT NOT NULL,
                session_id TEXT,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                layer TEXT NOT NULL DEFAULT 'raw',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_memory_summaries (
                day TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                session_id TEXT,
                text TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_daily_memory_events_day_time
                ON daily_memory_events(day, event_time, event_id);
            CREATE INDEX IF NOT EXISTS idx_daily_memory_events_kind_day
                ON daily_memory_events(kind, day);
            """
        )
        conn.commit()
    _migrate_legacy_daily_files(root=root, db_path=resolved)


def _legacy_daily_root(*, root: Path | None = None) -> Path:
    return (root or repo_root()) / "memory" / "daily"


def _migrate_legacy_daily_files(*, root: Path | None = None, db_path: Path | None = None) -> None:
    base = _legacy_daily_root(root=root)
    raw_dir = base / "raw"
    summary_dir = base / "summary"
    if not raw_dir.exists() and not summary_dir.exists():
        return

    with _connect(root=root, db_path=db_path) as conn:
        # Raw import
        for file_path in sorted(raw_dir.glob("*.md")):
            day_key = file_path.stem
            count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM daily_memory_events WHERE day = ?;",
                (day_key,),
            ).fetchone()
            if int(count_row["c"] if count_row else 0) > 0:
                continue
            lines = file_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                raw = line.strip()
                if not raw.startswith("- ["):
                    continue
                m = _RAW_LINE_RE.match(raw)
                if not m:
                    continue
                hhmmss = m.group("time") or "00:00:00"
                kind = (m.group("kind") or "note").strip() or "note"
                sid = m.group("sid")
                text = (m.group("text") or "").strip()
                if not text:
                    continue
                event_time = f"{day_key}T{hhmmss}+00:00"
                conn.execute(
                    """
                    INSERT INTO daily_memory_events(day, event_time, session_id, kind, text, layer, created_at)
                    VALUES(?, ?, ?, ?, ?, 'raw', ?);
                    """,
                    (day_key, event_time, sid, kind, text, datetime.now(UTC).isoformat()),
                )

        # Summary import
        for file_path in sorted(summary_dir.glob("*.md")):
            day_key = file_path.stem
            existing = conn.execute(
                "SELECT day FROM daily_memory_summaries WHERE day = ?;",
                (day_key,),
            ).fetchone()
            if existing is not None:
                continue
            text = file_path.read_text(encoding="utf-8")
            if not text.strip():
                continue
            conn.execute(
                """
                INSERT INTO daily_memory_summaries(day, updated_at, session_id, text)
                VALUES(?, ?, NULL, ?)
                ON CONFLICT(day) DO NOTHING;
                """,
                (day_key, datetime.now(UTC).isoformat(), text),
            )
        conn.commit()


def daily_memory_path(*, day: datetime | None = None, root: Path | None = None, layer: str = "summary") -> Path:
    """Legacy compatibility helper (DB is authoritative; path is informational only)."""
    if layer not in {"raw", "summary"}:
        raise ValueError("layer must be 'raw' or 'summary'")
    dt = day or _now_local()
    root_path = root or repo_root()
    return root_path / "memory" / "daily" / layer / f"{dt.strftime('%Y-%m-%d')}.md"


def ensure_daily_memory_file(*, day: datetime | None = None, root: Path | None = None, layer: str = "summary") -> Path:
    """Legacy compatibility shim; ensures DB schema and returns legacy-style path."""
    ensure_daily_memory_schema(root=root)
    return daily_memory_path(day=day, root=root, layer=layer)


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
    if layer not in {"raw", "summary"}:
        return {"ok": False, "error": "invalid_layer", "path": None}

    resolved = memory_index_path(root=root)
    ensure_daily_memory_schema(root=root, db_path=resolved)
    day_key = day_str or (day or _now_local()).strftime("%Y-%m-%d")
    source_dt = event_time or day or _now_local()
    if source_dt.tzinfo is None:
        source_dt = source_dt.replace(tzinfo=UTC)
    stamp_dt = source_dt.astimezone(UTC).isoformat()
    created_at = datetime.now(UTC).isoformat()
    clean_text = text.strip()
    with _connect(root=root, db_path=resolved) as conn:
        conn.execute(
            """
            INSERT INTO daily_memory_events(day, event_time, session_id, kind, text, layer, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?);
            """,
            (day_key, stamp_dt, session_id, kind, clean_text, layer, created_at),
        )
        conn.commit()

    ts = (event_time or day or _now_local()).strftime("%H:%M:%S")
    sid = f" ({session_id})" if session_id else ""
    line = f"- [{ts}] [{kind}]{sid} {clean_text}"
    return {"ok": True, "error": None, "path": None, "entry": line}


def write_daily_summary_snapshot(
    *,
    text: str,
    session_id: str | None = None,
    day: datetime | None = None,
    day_str: str | None = None,
    event_time: datetime | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if not text.strip():
        return {"ok": False, "error": "empty_text", "path": None}

    resolved = memory_index_path(root=root)
    ensure_daily_memory_schema(root=root, db_path=resolved)
    day_key = day_str or (day or _now_local()).strftime("%Y-%m-%d")
    updated_at = (event_time or day or _now_local()).astimezone(UTC).isoformat()
    body = text.strip()
    if not body.startswith("-"):
        body = f"- {body}"

    rendered = (
        f"# Daily Summary {day_key}\n\n"
        f"- Last updated: [{datetime.fromisoformat(updated_at).strftime('%H:%M:%S')}]"
        f"{f' ({session_id})' if session_id else ''}\n\n"
        f"{body}\n"
    )
    with _connect(root=root, db_path=resolved) as conn:
        conn.execute(
            """
            INSERT INTO daily_memory_summaries(day, updated_at, session_id, text)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                updated_at = excluded.updated_at,
                session_id = excluded.session_id,
                text = excluded.text;
            """,
            (day_key, updated_at, session_id, rendered),
        )
        conn.commit()
    return {"ok": True, "error": None, "path": None}


def list_day_raw_entries(*, day: str, root: Path | None = None) -> list[dict[str, Any]]:
    resolved = memory_index_path(root=root)
    ensure_daily_memory_schema(root=root, db_path=resolved)
    with _connect(root=root, db_path=resolved) as conn:
        rows = conn.execute(
            """
            SELECT event_id, day, event_time, session_id, kind, text, layer
            FROM daily_memory_events
            WHERE day = ? AND layer = 'raw'
            ORDER BY event_time ASC, event_id ASC;
            """,
            (day,),
        ).fetchall()
    return [
        {
            "event_id": int(row["event_id"]),
            "day": str(row["day"]),
            "event_time": str(row["event_time"]),
            "session_id": row["session_id"],
            "kind": str(row["kind"]),
            "text": str(row["text"]),
            "layer": str(row["layer"]),
        }
        for row in rows
    ]


def _render_raw_fallback(*, day_key: str, rows: list[dict[str, Any]], max_lines: int = 80) -> str:
    tail = rows[-max_lines:] if rows else []
    lines: list[str] = []
    for row in tail:
        ts = row["event_time"]
        try:
            t = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except Exception:
            t = "??:??:??"
        sid = f" ({row['session_id']})" if row.get("session_id") else ""
        lines.append(f"- [{t}] [{row['kind']}]{sid} {row['text']}")
    return (
        f"# Daily Raw Snapshot {day_key}\n\n"
        "Summary snapshot not available yet; this is a trimmed raw fallback.\n\n"
        + ("\n".join(lines) + "\n" if lines else "- (no raw entries)\n")
    )


def load_recent_daily_memory(
    *,
    days: int = 2,
    root: Path | None = None,
) -> dict[str, str]:
    if days <= 0:
        return {}
    resolved = memory_index_path(root=root)
    ensure_daily_memory_schema(root=root, db_path=resolved)
    now = _now_local()
    loaded: dict[str, str] = {}

    with _connect(root=root, db_path=resolved) as conn:
        for offset in range(days):
            day_key = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
            summary = conn.execute(
                "SELECT text FROM daily_memory_summaries WHERE day = ?;",
                (day_key,),
            ).fetchone()
            if summary is not None and isinstance(summary["text"], str) and summary["text"].strip():
                loaded[f"memory/db/summary/{day_key}.md"] = str(summary["text"])
                continue

            rows = conn.execute(
                """
                SELECT event_time, session_id, kind, text
                FROM daily_memory_events
                WHERE day = ? AND layer = 'raw'
                ORDER BY event_time ASC, event_id ASC;
                """,
                (day_key,),
            ).fetchall()
            if rows:
                dict_rows = [
                    {
                        "event_time": str(row["event_time"]),
                        "session_id": row["session_id"],
                        "kind": str(row["kind"]),
                        "text": str(row["text"]),
                    }
                    for row in rows
                ]
                loaded[f"memory/db/raw/{day_key}.md#raw_fallback"] = _render_raw_fallback(day_key=day_key, rows=dict_rows)
    return loaded
