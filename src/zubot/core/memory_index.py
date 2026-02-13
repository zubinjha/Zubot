"""SQLite index and queue for daily memory summary/finalization state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config_loader import get_timezone, load_config
from .path_policy import repo_root

DEFAULT_UNIFIED_DB_PATH = "memory/central/zubot_core.db"
LEGACY_MEMORY_INDEX_PATH = "memory/memory_index.sqlite3"


def _local_day_str() -> str:
    try:
        tz = get_timezone()
        if isinstance(tz, str) and tz.strip():
            return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    target = (
        path
        if isinstance(path, str) and path.strip()
        else (_configured_scheduler_db_path() if root is None else DEFAULT_UNIFIED_DB_PATH)
    )
    db_path = root_path / target
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _connect(*, root: Path | None = None, db_path: Path | None = None) -> sqlite3.Connection:
    target = db_path or memory_index_path(root=root)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def _legacy_memory_index_path(*, root: Path | None = None) -> Path:
    root_path = root or repo_root()
    return root_path / LEGACY_MEMORY_INDEX_PATH


def _ensure_column(conn: sqlite3.Connection, *, table: str, column: str, definition: str) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition};")


def _migrate_legacy_day_memory_status(*, root: Path | None = None, db_path: Path | None = None) -> None:
    unified = db_path or memory_index_path(root=root)
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

    with _connect(root=root, db_path=unified) as conn:
        for row in rows:
            pending = int(row["messages_since_last_summary"])
            conn.execute(
                """
                INSERT INTO day_memory_status(
                    day, total_messages, last_summarized_total, messages_since_last_summary,
                    summaries_count, is_finalized, last_summary_at, last_event_at
                )
                VALUES(?, ?, 0, ?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    total_messages = MAX(day_memory_status.total_messages, excluded.total_messages),
                    messages_since_last_summary = MAX(day_memory_status.messages_since_last_summary, excluded.messages_since_last_summary),
                    summaries_count = MAX(day_memory_status.summaries_count, excluded.summaries_count),
                    is_finalized = MAX(day_memory_status.is_finalized, excluded.is_finalized),
                    last_summary_at = COALESCE(excluded.last_summary_at, day_memory_status.last_summary_at),
                    last_event_at = COALESCE(excluded.last_event_at, day_memory_status.last_event_at);
                """,
                (
                    row["day"],
                    pending,
                    pending,
                    int(row["summaries_count"]),
                    int(row["is_finalized"]),
                    row["last_summary_at"],
                    row["last_event_at"],
                ),
            )
        conn.commit()


def ensure_memory_index_schema(*, root: Path | None = None, db_path: Path | None = None) -> None:
    resolved = db_path or memory_index_path(root=root)
    with _connect(root=root, db_path=resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS day_memory_status (
                day TEXT PRIMARY KEY,
                total_messages INTEGER NOT NULL DEFAULT 0,
                last_summarized_total INTEGER NOT NULL DEFAULT 0,
                messages_since_last_summary INTEGER NOT NULL DEFAULT 0,
                summaries_count INTEGER NOT NULL DEFAULT 0,
                is_finalized INTEGER NOT NULL DEFAULT 0,
                last_summary_at TEXT,
                last_event_at TEXT
            );

            CREATE TABLE IF NOT EXISTS memory_summary_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_day_memory_finalized ON day_memory_status(is_finalized);
            CREATE INDEX IF NOT EXISTS idx_memory_summary_jobs_status_created
                ON memory_summary_jobs(status, created_at, job_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_summary_jobs_day_active
                ON memory_summary_jobs(day)
                WHERE status IN ('queued', 'running');
            """
        )
        _ensure_column(conn, table="day_memory_status", column="total_messages", definition="total_messages INTEGER NOT NULL DEFAULT 0")
        _ensure_column(
            conn,
            table="day_memory_status",
            column="last_summarized_total",
            definition="last_summarized_total INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            table="day_memory_status",
            column="messages_since_last_summary",
            definition="messages_since_last_summary INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(conn, table="memory_summary_jobs", column="attempt_count", definition="attempt_count INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    _migrate_legacy_day_memory_status(root=root, db_path=resolved)


def increment_day_message_count(
    *,
    day: str | None = None,
    amount: int = 1,
    root: Path | None = None,
) -> dict[str, Any]:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    day_key = day or _local_day_str()
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root, db_path=db_path) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(
                day, total_messages, last_summarized_total, messages_since_last_summary,
                summaries_count, is_finalized, last_summary_at, last_event_at
            )
            VALUES(?, ?, 0, ?, 0, 0, NULL, ?)
            ON CONFLICT(day) DO UPDATE SET
                total_messages = day_memory_status.total_messages + excluded.total_messages,
                messages_since_last_summary = day_memory_status.messages_since_last_summary + excluded.messages_since_last_summary,
                is_finalized = 0,
                last_event_at = excluded.last_event_at;
            """,
            (day_key, amount, amount, now),
        )
        row = conn.execute(
            """
            SELECT total_messages, last_summarized_total, messages_since_last_summary, summaries_count, is_finalized
            FROM day_memory_status
            WHERE day = ?;
            """,
            (day_key,),
        ).fetchone()
        conn.commit()
    return {
        "day": day_key,
        "total_messages": int(row["total_messages"]),
        "last_summarized_total": int(row["last_summarized_total"]),
        "messages_since_last_summary": int(row["messages_since_last_summary"]),
        "summaries_count": int(row["summaries_count"]),
        "is_finalized": bool(row["is_finalized"]),
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
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root, db_path=db_path) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(
                day, total_messages, last_summarized_total, messages_since_last_summary,
                summaries_count, is_finalized, last_summary_at, last_event_at
            )
            VALUES(?, 0, 0, 0, 1, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                last_summarized_total = day_memory_status.total_messages,
                messages_since_last_summary = 0,
                summaries_count = day_memory_status.summaries_count + 1,
                is_finalized = CASE WHEN ? = 1 THEN 1 ELSE day_memory_status.is_finalized END,
                last_summary_at = ?;
            """,
            (day, 1 if finalize else 0, now, now, 1 if finalize else 0, now),
        )
        row = conn.execute(
            """
            SELECT total_messages, last_summarized_total, messages_since_last_summary, summaries_count, is_finalized
            FROM day_memory_status
            WHERE day = ?;
            """,
            (day,),
        ).fetchone()
        conn.commit()
    return {
        "day": day,
        "total_messages": int(row["total_messages"]),
        "last_summarized_total": int(row["last_summarized_total"]),
        "messages_since_last_summary": int(row["messages_since_last_summary"]),
        "summaries_count": int(row["summaries_count"]),
        "is_finalized": bool(row["is_finalized"]),
    }


def mark_day_finalized(*, day: str, root: Path | None = None) -> dict[str, Any]:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    with _connect(root=root, db_path=db_path) as conn:
        conn.execute(
            """
            INSERT INTO day_memory_status(
                day, total_messages, last_summarized_total, messages_since_last_summary,
                summaries_count, is_finalized
            )
            VALUES(?, 0, 0, 0, 0, 1)
            ON CONFLICT(day) DO UPDATE SET
                is_finalized = 1;
            """,
            (day,),
        )
        conn.commit()
    return {"ok": True, "day": day, "is_finalized": True}


def enqueue_day_summary_job(
    *,
    day: str,
    reason: str,
    root: Path | None = None,
) -> dict[str, Any]:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root, db_path=db_path) as conn:
        existing = conn.execute(
            """
            SELECT job_id, status
            FROM memory_summary_jobs
            WHERE day = ? AND status IN ('queued', 'running')
            ORDER BY job_id ASC
            LIMIT 1;
            """,
            (day,),
        ).fetchone()
        if existing is not None:
            return {
                "ok": True,
                "enqueued": False,
                "deduped": True,
                "job_id": int(existing["job_id"]),
                "status": str(existing["status"]),
            }
        cursor = conn.execute(
            """
            INSERT INTO memory_summary_jobs(day, status, reason, created_at)
            VALUES(?, 'queued', ?, ?);
            """,
            (day, reason, now),
        )
        conn.commit()
        job_id = int(cursor.lastrowid)
    return {"ok": True, "enqueued": True, "deduped": False, "job_id": job_id, "status": "queued"}


def claim_next_day_summary_job(*, root: Path | None = None) -> dict[str, Any] | None:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connect(root=root, db_path=db_path) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            """
            SELECT job_id
            FROM memory_summary_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC, job_id ASC
            LIMIT 1;
            """
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        job_id = int(row["job_id"])
        updated = conn.execute(
            """
            UPDATE memory_summary_jobs
            SET status = 'running', started_at = ?, attempt_count = attempt_count + 1
            WHERE job_id = ? AND status = 'queued';
            """,
            (now, job_id),
        )
        if int(updated.rowcount or 0) <= 0:
            conn.commit()
            return None

        claimed = conn.execute(
            """
            SELECT job_id, day, status, reason, created_at, started_at, finished_at, error, attempt_count
            FROM memory_summary_jobs
            WHERE job_id = ?;
            """,
            (job_id,),
        ).fetchone()
        conn.commit()
    if claimed is None:
        return None
    return {
        "job_id": int(claimed["job_id"]),
        "day": str(claimed["day"]),
        "status": str(claimed["status"]),
        "reason": str(claimed["reason"] or ""),
        "created_at": claimed["created_at"],
        "started_at": claimed["started_at"],
        "finished_at": claimed["finished_at"],
        "error": claimed["error"],
        "attempt_count": int(claimed["attempt_count"] or 0),
    }


def complete_day_summary_job(
    *,
    job_id: int,
    ok: bool,
    error: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    finish = datetime.now(timezone.utc).isoformat()
    status = "done" if ok else "failed"
    with _connect(root=root, db_path=db_path) as conn:
        conn.execute(
            """
            UPDATE memory_summary_jobs
            SET status = ?, finished_at = ?, error = ?
            WHERE job_id = ?;
            """,
            (status, finish, (error or "")[:500], int(job_id)),
        )
        conn.commit()
    return {"ok": True, "job_id": int(job_id), "status": status}


def get_days_pending_summary(
    *,
    before_day: str | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    query = """
        SELECT day, total_messages, last_summarized_total, messages_since_last_summary,
               summaries_count, is_finalized, last_summary_at, last_event_at
        FROM day_memory_status
        WHERE (
            messages_since_last_summary > 0
            OR total_messages > last_summarized_total
        )
    """
    params: list[Any] = []
    if before_day:
        query += " AND day < ?"
        params.append(before_day)
    query += " ORDER BY day ASC"
    with _connect(root=root, db_path=db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {
            "day": str(row["day"]),
            "total_messages": int(row["total_messages"]),
            "last_summarized_total": int(row["last_summarized_total"]),
            "messages_since_last_summary": int(row["messages_since_last_summary"]),
            "summaries_count": int(row["summaries_count"]),
            "is_finalized": bool(row["is_finalized"]),
            "last_summary_at": row["last_summary_at"],
            "last_event_at": row["last_event_at"],
        }
        for row in rows
    ]


def get_day_status(*, day: str, root: Path | None = None) -> dict[str, Any] | None:
    db_path = memory_index_path(root=root)
    ensure_memory_index_schema(root=root, db_path=db_path)
    with _connect(root=root, db_path=db_path) as conn:
        row = conn.execute(
            """
            SELECT day, total_messages, last_summarized_total, messages_since_last_summary,
                   summaries_count, is_finalized, last_summary_at, last_event_at
            FROM day_memory_status
            WHERE day = ?;
            """,
            (day,),
        ).fetchone()
    if row is None:
        return None
    return {
        "day": str(row["day"]),
        "total_messages": int(row["total_messages"]),
        "last_summarized_total": int(row["last_summarized_total"]),
        "messages_since_last_summary": int(row["messages_since_last_summary"]),
        "summaries_count": int(row["summaries_count"]),
        "is_finalized": bool(row["is_finalized"]),
        "last_summary_at": row["last_summary_at"],
        "last_event_at": row["last_event_at"],
    }
