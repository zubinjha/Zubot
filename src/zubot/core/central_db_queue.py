"""Serialized SQL execution queue for central SQLite access."""

from __future__ import annotations

import queue
import sqlite3
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any


def _as_row_dict(row: sqlite3.Row | Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, dict):
        return dict(row)
    return {"value": row}


@dataclass(slots=True)
class _SqlRequest:
    request_id: str
    sql: str
    params: Any
    read_only: bool
    timeout_sec: float
    max_rows: int
    done: Event
    result: dict[str, Any] | None = None


class CentralDbQueue:
    """Single-threaded SQL queue with correlation IDs and safe defaults."""

    def __init__(self, *, db_path: str | Path, busy_timeout_ms: int = 5000) -> None:
        self._db_path = Path(db_path)
        self._busy_timeout_ms = int(busy_timeout_ms) if int(busy_timeout_ms) > 0 else 5000
        self._queue: queue.Queue[_SqlRequest] = queue.Queue()
        self._stop = Event()
        self._thread: Thread | None = None
        self._counter = count(1)
        self._lock = Lock()
        self._last_error: str | None = None

    @staticmethod
    def _is_read_only_sql(sql: str) -> bool:
        token = (sql or "").strip().split(None, 1)
        if not token:
            return False
        head = token[0].strip().lower()
        return head in {"select", "pragma", "explain", "with"}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms:d};")
        return conn

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "running": True, "already_running": True}
            self._stop.clear()
            self._thread = Thread(target=self._run_loop, daemon=True, name="zubot-central-db-queue")
            self._thread.start()
        return {"ok": True, "running": True, "already_running": False}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
        return {"ok": True, "running": False}

    def health(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
        return {
            "ok": True,
            "running": running,
            "queue_depth": int(self._queue.qsize()),
            "db_path": str(self._db_path),
            "busy_timeout_ms": self._busy_timeout_ms,
            "last_error": self._last_error,
        }

    def execute(
        self,
        *,
        sql: str,
        params: Any = None,
        read_only: bool = True,
        timeout_sec: float = 5.0,
        max_rows: int = 500,
    ) -> dict[str, Any]:
        clean_sql = str(sql or "").strip()
        if not clean_sql:
            return {"ok": False, "error": "sql is required."}
        if read_only and not self._is_read_only_sql(clean_sql):
            return {"ok": False, "error": "read_only query must be SELECT/PRAGMA/EXPLAIN/WITH."}

        self.start()
        request_id = f"sqlq_{next(self._counter)}"
        req = _SqlRequest(
            request_id=request_id,
            sql=clean_sql,
            params=params,
            read_only=bool(read_only),
            timeout_sec=float(timeout_sec) if float(timeout_sec) > 0 else 5.0,
            max_rows=max(1, int(max_rows)),
            done=Event(),
        )
        self._queue.put(req)
        if not req.done.wait(timeout=req.timeout_sec):
            return {"ok": False, "request_id": request_id, "error": "sql_queue_timeout"}
        return req.result if isinstance(req.result, dict) else {"ok": False, "request_id": request_id, "error": "no_result"}

    def _run_loop(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            while not self._stop.is_set():
                try:
                    req = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                req.result = self._execute_request(conn, req)
                req.done.set()
        except Exception as exc:  # pragma: no cover - defensive guard
            self._last_error = str(exc)
        finally:
            if conn is not None:
                conn.close()

    def _execute_request(self, conn: sqlite3.Connection, req: _SqlRequest) -> dict[str, Any]:
        try:
            cursor = conn.execute(req.sql, req.params or ())
            if req.read_only:
                rows = cursor.fetchmany(req.max_rows)
                return {
                    "ok": True,
                    "request_id": req.request_id,
                    "mode": "read",
                    "rows": [_as_row_dict(row) for row in rows],
                    "row_count": len(rows),
                }
            conn.commit()
            return {
                "ok": True,
                "request_id": req.request_id,
                "mode": "write",
                "rows": [],
                "row_count": 0,
                "rows_affected": int(cursor.rowcount or 0),
            }
        except Exception as exc:
            conn.rollback()
            self._last_error = str(exc)
            return {
                "ok": False,
                "request_id": req.request_id,
                "error": str(exc),
            }

