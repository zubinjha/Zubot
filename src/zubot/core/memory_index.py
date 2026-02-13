"""SQLite index for daily memory summary/finalization state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import load_config
from .daily_memory import local_day_str
from .path_policy import repo_root

DEFAULT_UNIFIED_DB_PATH = "memory/central/zubot_core.db"
LEGACY_MEMORY_INDEX_PATH = "memory/memory_index.sqlite3"

def _configured_scheduler_db_path() -> str:
    try:
        cfg = load_config()
    except Exception:
        return DEFAULT_UNIFIED_DB_PATH
    central = cfg.get("central_service") if isinstance(cfg, dict) else None
    raw = central.get("scheduler_db_path") if isinstance(central, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw
    return DEFAULT_UNIFIED_DB_PATH


def memory_index_path(*, root: Path | None = None, path: str | None = None) -> Path:
    root_path = root or repo_root()
    target = path if isinstance(path, str) and path.strip() else (_configured_scheduler_db_path() if root is None else DEFAULT_UNIFIED_DB_PATH)
    db_path = root_path / target
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _connect(*, root: Path | None = None) -> sqlite3.Connection:
    return sqlite3.connect(memory_index_path(root=root))


def _legacy_memory_index_path(*, root: Path | None = None) -> Path:
    root_path = root or repo_root()
    return root_path / LEGACY_MEMORY_INDEX_PATH


def _migrate_legacy_day_memory_status(*, root: Path | None = None) -> None:
    unified = memory_index_path(root=root)
    legacy = _legacy_memory_index_path(root=root)
    if not legacy.exists() or legacy.resolve() == unified.resolve():
        return

    with sqlite3.connect(legacy) as old_conn:
        old_conn.row_factory = sqlite3.Row
        table = old_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='day_memory_status';"
        ).fetchone()
        if table is None:
            return
        rows = old_conn.execute(
            """
            SELECT day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at
            FROM day_memory_status
            ORDER BY day ASC;
            """
        ).fetchall()
    if not rows:
        return

    with _connect(root=root) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO day_memory_status(
                    day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    messages_since_last_summary = MAX(day_memory_status.messages_since_last_summary, excluded.messages_since_last_summary),
                    summaries_count = MAX(day_memory_status.summaries_count, excluded.summaries_count),
                    is_finalized = MAX(day_memory_status.is_finalized, excluded.is_finalized),
                    last_summary_at = COALESCE(excluded.last_summary_at, day_memory_status.last_summary_at),
                    last_event_at = COALESCE(excluded.last_event_at, day_memory_status.last_event_at);
                """,
                (
                    row["day"],
                    int(row["messages_since_last_summary"]),
                    int(row["summaries_count"]),
                    int(row["is_finalized"]),
                    row["last_summary_at"],
                    row["last_event_at"],
                ),
            )
        conn.commit()


def ensure_memory_index_schema(*, root: Path | None = None) -> None:
    with _connect(root=root) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS day_memory_status (
                day TEXT PRIMARY KEY,
                messages_since_last_summary INTEGER NOT NULL DEFAULT 0,
                summaries_count INTEGER NOT NULL DEFAULT 0,
                is_finalized INTEGER NOT NULL DEFAULT 0,
                last_summary_at TEXT,
                last_event_at TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_day_memory_finalized ON day_memory_status(is_finalized);")
        conn.commit()
    _migrate_legacy_day_memory_status(root=root)


def increment_day_message_count(
    *,
    day: str | None = None,
    amount: int = 1,
    root: Path | None = None,
) -> dict[str, Any]:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    ensure_memory_index_schema(root=root)
    day_key = day or local_day_str()
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at)
            VALUES(?, ?, 0, 0, NULL, ?)
            ON CONFLICT(day) DO UPDATE SET
                messages_since_last_summary = messages_since_last_summary + excluded.messages_since_last_summary,
                is_finalized = 0,
                last_event_at = excluded.last_event_at;
            """,
            (day_key, amount, now),
        )
        row = conn.execute(
            "SELECT messages_since_last_summary, summaries_count, is_finalized FROM day_memory_status WHERE day = ?;",
            (day_key,),
        ).fetchone()
        conn.commit()
    return {
        "day": day_key,
        "messages_since_last_summary": int(row[0]),
        "summaries_count": int(row[1]),
        "is_finalized": bool(row[2]),
    }


def mark_day_summarized(
    *,
    day: str,
    summarized_messages: int,
    finalize: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    if summarized_messages < 0:
        raise ValueError("summarized_messages must be >= 0")
    ensure_memory_index_schema(root=root)
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at)
            VALUES(?, 0, 1, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                messages_since_last_summary = 0,
                summaries_count = summaries_count + 1,
                is_finalized = CASE WHEN ? = 1 THEN 1 ELSE is_finalized END,
                last_summary_at = ?;
            """,
            (day, 1 if finalize else 0, now, now, 1 if finalize else 0, now),
        )
        row = conn.execute(
            "SELECT messages_since_last_summary, summaries_count, is_finalized FROM day_memory_status WHERE day = ?;",
            (day,),
        ).fetchone()
        conn.commit()
    return {
        "day": day,
        "messages_since_last_summary": int(row[0]),
        "summaries_count": int(row[1]),
        "is_finalized": bool(row[2]),
    }


def mark_day_finalized(*, day: str, root: Path | None = None) -> dict[str, Any]:
    ensure_memory_index_schema(root=root)
    with _connect(root=root) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(day, messages_since_last_summary, summaries_count, is_finalized)
            VALUES(?, 0, 0, 1)
            ON CONFLICT(day) DO UPDATE SET
                is_finalized = 1;
            """,
            (day,),
        )
        conn.commit()
    return {"ok": True, "day": day, "is_finalized": True}


def get_days_pending_summary(
    *,
    before_day: str | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    ensure_memory_index_schema(root=root)
    query = """
        SELECT day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at
        FROM day_memory_status
        WHERE messages_since_last_summary > 0
    """
    params: list[Any] = []
    if before_day:
        query += " AND day < ?"
        params.append(before_day)
    query += " ORDER BY day ASC"
    with _connect(root=root) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {
            "day": row[0],
            "messages_since_last_summary": int(row[1]),
            "summaries_count": int(row[2]),
            "is_finalized": bool(row[3]),
            "last_summary_at": row[4],
            "last_event_at": row[5],
        }
        for row in rows
    ]


def get_day_status(*, day: str, root: Path | None = None) -> dict[str, Any] | None:
    ensure_memory_index_schema(root=root)
    with _connect(root=root) as conn:
        row = conn.execute(
            """
            SELECT day, messages_since_last_summary, summaries_count, is_finalized, last_summary_at, last_event_at
            FROM day_memory_status
            WHERE day = ?;
            """,
            (day,),
        ).fetchone()
    if row is None:
        return None
    return {
        "day": row[0],
        "messages_since_last_summary": int(row[1]),
        "summaries_count": int(row[2]),
        "is_finalized": bool(row[3]),
        "last_summary_at": row[4],
        "last_event_at": row[5],
    }
