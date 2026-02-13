"""SQLite-backed scheduler/queue store for task-agent runs."""

from __future__ import annotations

import json
import sqlite3
import shutil
from datetime import time as dt_time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config_loader import load_config

DEFAULT_DB_PATH = "memory/central/zubot_core.db"
_WEEKDAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WEEKDAY_TO_INDEX = {name: idx for idx, name in enumerate(_WEEKDAY_ORDER)}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _resolve_db_path(path: str | None) -> Path:
    candidate = Path(path or DEFAULT_DB_PATH)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _parse_time_of_day(value: str | None) -> dt_time | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        hour_str, min_str = raw.split(":", 1)
        hour = int(hour_str)
        minute = int(min_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dt_time(hour=hour, minute=minute)
    except Exception:
        return None
    return None


def _normalize_days_of_week(value: Any) -> str | None:
    if isinstance(value, list):
        tokens = [str(v).strip().lower()[:3] for v in value if isinstance(v, str) and v.strip()]
    elif isinstance(value, str):
        tokens = [part.strip().lower()[:3] for part in value.split(",") if part.strip()]
    else:
        tokens = []
    unique = [token for token in _WEEKDAY_ORDER if token in set(tokens)]
    return ",".join(unique) if unique else None


def _weekday_allowed(days_csv: str | None, local_dt: datetime) -> bool:
    if not isinstance(days_csv, str) or not days_csv.strip():
        return True
    allowed = {part.strip().lower()[:3] for part in days_csv.split(",") if part.strip()}
    return _WEEKDAY_ORDER[local_dt.weekday()] in allowed


def _most_recent_calendar_fire(
    *,
    now_dt: datetime,
    timezone_name: str,
    time_of_day: str,
    days_of_week: str | None,
) -> datetime | None:
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        return None
    tod = _parse_time_of_day(time_of_day)
    if tod is None:
        return None

    local_now = now_dt.astimezone(zone)
    for delta in range(0, 8):
        candidate_date = local_now.date() - timedelta(days=delta)
        candidate_local = datetime(
            year=candidate_date.year,
            month=candidate_date.month,
            day=candidate_date.day,
            hour=tod.hour,
            minute=tod.minute,
            tzinfo=zone,
        )
        if candidate_local > local_now:
            continue
        if not _weekday_allowed(days_of_week, candidate_local):
            continue
        return candidate_local.astimezone(UTC)
    return None


def _next_calendar_fire_after(
    *,
    fire_dt: datetime,
    timezone_name: str,
    time_of_day: str,
    days_of_week: str | None,
) -> datetime | None:
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        return None
    tod = _parse_time_of_day(time_of_day)
    if tod is None:
        return None

    local_fire = fire_dt.astimezone(zone)
    for delta in range(1, 15):
        candidate_date = local_fire.date() + timedelta(days=delta)
        candidate_local = datetime(
            year=candidate_date.year,
            month=candidate_date.month,
            day=candidate_date.day,
            hour=tod.hour,
            minute=tod.minute,
            tzinfo=zone,
        )
        if not _weekday_allowed(days_of_week, candidate_local):
            continue
        candidate_utc = candidate_local.astimezone(UTC)
        if candidate_utc > fire_dt:
            return candidate_utc
    return None


def _scheduler_db_path_from_config() -> Path:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    central = cfg.get("central_service") if isinstance(cfg, dict) else None
    raw_db_path = None
    if isinstance(central, dict):
        raw = central.get("scheduler_db_path")
        if isinstance(raw, str) and raw.strip():
            raw_db_path = raw
    return _resolve_db_path(raw_db_path)


def resolve_scheduler_db_path(path: str | None) -> Path:
    """Resolve scheduler DB path against repo root."""
    return _resolve_db_path(path)


class TaskSchedulerStore:
    """Persist and query schedule/run queue state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            self._db_path = _scheduler_db_path_from_config()
        else:
            self._db_path = _resolve_db_path(str(db_path))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._maybe_migrate_from_legacy_sqlite3()
        self.ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _maybe_migrate_from_legacy_sqlite3(self) -> None:
        """One-time copy forward from legacy `.sqlite3` filename to `.db`."""
        if self._db_path.exists():
            return
        if self._db_path.suffix != ".db":
            return
        legacy_path = self._db_path.with_suffix(".sqlite3")
        if legacy_path.exists():
            shutil.copy2(legacy_path, self._db_path)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, *, table: str, column: str, definition: str) -> None:
        existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition};")

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    run_frequency_minutes INTEGER NOT NULL,
                    schedule_mode TEXT NOT NULL DEFAULT 'interval',
                    schedule_timezone TEXT,
                    schedule_time_of_day TEXT,
                    schedule_days_of_week TEXT,
                    schedule_catch_up_window_minutes INTEGER NOT NULL DEFAULT 180,
                    schedule_max_runtime_sec INTEGER,
                    next_run_at TEXT,
                    last_scheduled_fire_time TEXT,
                    last_successful_run_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT,
                    last_summary TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    summary TEXT,
                    error TEXT,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES schedules(schedule_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS run_history (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    summary TEXT,
                    error TEXT,
                    payload_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES schedules(schedule_id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_status_queued_at ON runs(status, queued_at);
                CREATE INDEX IF NOT EXISTS idx_runs_profile_queued_at ON runs(profile_id, queued_at);
                CREATE INDEX IF NOT EXISTS idx_run_history_status_finished_at ON run_history(status, finished_at);
                CREATE INDEX IF NOT EXISTS idx_run_history_profile_finished_at ON run_history(profile_id, finished_at);
                """
            )
            # Backward-compatible column migrations for pre-existing local DBs.
            self._ensure_column(conn, table="schedules", column="schedule_mode", definition="schedule_mode TEXT NOT NULL DEFAULT 'interval'")
            self._ensure_column(conn, table="schedules", column="schedule_timezone", definition="schedule_timezone TEXT")
            self._ensure_column(conn, table="schedules", column="schedule_time_of_day", definition="schedule_time_of_day TEXT")
            self._ensure_column(conn, table="schedules", column="schedule_days_of_week", definition="schedule_days_of_week TEXT")
            self._ensure_column(
                conn,
                table="schedules",
                column="schedule_catch_up_window_minutes",
                definition="schedule_catch_up_window_minutes INTEGER NOT NULL DEFAULT 180",
            )
            self._ensure_column(
                conn,
                table="schedules",
                column="schedule_max_runtime_sec",
                definition="schedule_max_runtime_sec INTEGER",
            )
            self._ensure_column(conn, table="schedules", column="last_scheduled_fire_time", definition="last_scheduled_fire_time TEXT")
            self._ensure_column(conn, table="schedules", column="last_successful_run_at", definition="last_successful_run_at TEXT")

    def sync_schedules(self, schedules: list[dict[str, Any]]) -> dict[str, Any]:
        now = _iso(_utc_now())
        upserted = 0
        with self._connect() as conn:
            for item in schedules:
                schedule_id = str(item.get("schedule_id") or "").strip()
                profile_id = str(item.get("profile_id") or "").strip()
                enabled = bool(item.get("enabled", True))
                mode_raw = str(item.get("mode") or "interval").strip().lower()
                mode = mode_raw if mode_raw in {"interval", "calendar"} else "interval"
                freq = item.get("run_frequency_minutes")
                if not schedule_id or not profile_id:
                    continue
                if mode == "interval":
                    if not isinstance(freq, int) or freq <= 0:
                        continue
                else:
                    # calendar mode can omit frequency; keep a sane fallback for compatibility fields.
                    freq = int(freq) if isinstance(freq, int) and freq > 0 else 1440
                    schedule_tz = str(item.get("timezone") or "UTC").strip() or "UTC"
                    try:
                        ZoneInfo(schedule_tz)
                    except Exception:
                        continue
                    schedule_tod = str(item.get("time_of_day") or "").strip()
                    if _parse_time_of_day(schedule_tod) is None:
                        continue
                    normalized_days = _normalize_days_of_week(item.get("days_of_week"))
                    catch_up_window = item.get("catch_up_window_minutes")
                    catch_up = int(catch_up_window) if isinstance(catch_up_window, int) and catch_up_window >= 0 else 180
                    max_runtime = item.get("max_runtime_sec")
                    max_runtime_sec = int(max_runtime) if isinstance(max_runtime, int) and max_runtime > 0 else None
                if mode == "interval":
                    schedule_tz = None
                    schedule_tod = None
                    normalized_days = None
                    catch_up = int(item.get("catch_up_window_minutes")) if isinstance(item.get("catch_up_window_minutes"), int) else 180
                    max_runtime = item.get("max_runtime_sec")
                    max_runtime_sec = int(max_runtime) if isinstance(max_runtime, int) and max_runtime > 0 else None

                next_run_at = item.get("next_run_at")
                conn.execute(
                    """
                    INSERT INTO schedules(
                        schedule_id, profile_id, enabled, run_frequency_minutes,
                        schedule_mode, schedule_timezone, schedule_time_of_day, schedule_days_of_week,
                        schedule_catch_up_window_minutes, schedule_max_runtime_sec,
                        next_run_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_id) DO UPDATE SET
                        profile_id = excluded.profile_id,
                        enabled = excluded.enabled,
                        run_frequency_minutes = excluded.run_frequency_minutes,
                        schedule_mode = excluded.schedule_mode,
                        schedule_timezone = excluded.schedule_timezone,
                        schedule_time_of_day = excluded.schedule_time_of_day,
                        schedule_days_of_week = excluded.schedule_days_of_week,
                        schedule_catch_up_window_minutes = excluded.schedule_catch_up_window_minutes,
                        schedule_max_runtime_sec = excluded.schedule_max_runtime_sec,
                        next_run_at = COALESCE(excluded.next_run_at, schedules.next_run_at),
                        updated_at = excluded.updated_at;
                    """,
                    (
                        schedule_id,
                        profile_id,
                        1 if enabled else 0,
                        int(freq),
                        mode,
                        schedule_tz,
                        schedule_tod,
                        normalized_days,
                        int(catch_up),
                        max_runtime_sec,
                        str(next_run_at) if isinstance(next_run_at, str) and next_run_at.strip() else None,
                        now,
                        now,
                    ),
                )
                upserted += 1
        return {"ok": True, "upserted": upserted}

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, profile_id, enabled, run_frequency_minutes,
                       schedule_mode, schedule_timezone, schedule_time_of_day, schedule_days_of_week,
                       schedule_catch_up_window_minutes, schedule_max_runtime_sec,
                       next_run_at, last_scheduled_fire_time, last_successful_run_at,
                       last_run_at, last_status, last_summary, last_error
                FROM schedules
                ORDER BY schedule_id;
                """
            ).fetchall()
        return [
            {
                "schedule_id": row["schedule_id"],
                "profile_id": row["profile_id"],
                "enabled": bool(row["enabled"]),
                "run_frequency_minutes": int(row["run_frequency_minutes"]),
                "mode": row["schedule_mode"] or "interval",
                "timezone": row["schedule_timezone"],
                "time_of_day": row["schedule_time_of_day"],
                "days_of_week": (
                    [part for part in str(row["schedule_days_of_week"]).split(",") if part]
                    if row["schedule_days_of_week"]
                    else None
                ),
                "catch_up_window_minutes": int(row["schedule_catch_up_window_minutes"])
                if row["schedule_catch_up_window_minutes"] is not None
                else None,
                "max_runtime_sec": int(row["schedule_max_runtime_sec"]) if row["schedule_max_runtime_sec"] is not None else None,
                "next_run_at": row["next_run_at"],
                "last_scheduled_fire_time": row["last_scheduled_fire_time"],
                "last_successful_run_at": row["last_successful_run_at"],
                "last_run_at": row["last_run_at"],
                "last_status": row["last_status"],
                "last_summary": row["last_summary"],
                "last_error": row["last_error"],
            }
            for row in rows
        ]

    def enqueue_due_runs(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        now_iso = _iso(now_dt)
        due: list[dict[str, Any]] = []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, profile_id, run_frequency_minutes, next_run_at, last_run_at,
                       schedule_mode, schedule_timezone, schedule_time_of_day, schedule_days_of_week,
                       schedule_catch_up_window_minutes, schedule_max_runtime_sec,
                       last_scheduled_fire_time
                FROM schedules
                WHERE enabled = 1;
                """
            ).fetchall()

            for row in rows:
                mode = str(row["schedule_mode"] or "interval").strip().lower()
                freq = int(row["run_frequency_minutes"])
                next_run_update: str | None = None
                scheduled_fire_time: str | None = None

                if mode == "calendar":
                    timezone_name = str(row["schedule_timezone"] or "UTC").strip() or "UTC"
                    time_of_day = str(row["schedule_time_of_day"] or "").strip()
                    days_csv = row["schedule_days_of_week"] if isinstance(row["schedule_days_of_week"], str) else None
                    catch_up = (
                        int(row["schedule_catch_up_window_minutes"])
                        if row["schedule_catch_up_window_minutes"] is not None
                        else 180
                    )
                    fire_dt = _most_recent_calendar_fire(
                        now_dt=now_dt,
                        timezone_name=timezone_name,
                        time_of_day=time_of_day,
                        days_of_week=days_csv,
                    )
                    if fire_dt is None:
                        continue
                    if fire_dt > now_dt:
                        continue
                    if now_dt > fire_dt + timedelta(minutes=max(0, catch_up)):
                        continue
                    last_fire = _parse_iso(row["last_scheduled_fire_time"])
                    if last_fire is not None and last_fire >= fire_dt:
                        continue

                    next_fire = _next_calendar_fire_after(
                        fire_dt=fire_dt,
                        timezone_name=timezone_name,
                        time_of_day=time_of_day,
                        days_of_week=days_csv,
                    )
                    next_run_update = _iso(next_fire) if next_fire is not None else None
                    scheduled_fire_time = _iso(fire_dt)
                else:
                    next_run = _parse_iso(row["next_run_at"])
                    last_run = _parse_iso(row["last_run_at"])
                    if next_run is not None:
                        is_due = next_run <= now_dt
                    elif last_run is None:
                        is_due = True
                    else:
                        is_due = (last_run + timedelta(minutes=freq)) <= now_dt
                    if not is_due:
                        continue
                    next_run_update = _iso(now_dt + timedelta(minutes=freq))

                # Prevent duplicate enqueues for same schedule if a queued/running run already exists.
                existing = conn.execute(
                    """
                    SELECT run_id FROM runs
                    WHERE schedule_id = ? AND status IN ('queued', 'running')
                    LIMIT 1;
                    """,
                    (row["schedule_id"],),
                ).fetchone()
                if existing is not None:
                    continue

                run_id = f"trun_{uuid4().hex}"
                payload = {
                    "schedule_id": row["schedule_id"],
                    "profile_id": row["profile_id"],
                    "trigger": "scheduled",
                    "enqueued_at": now_iso,
                    "schedule_mode": mode,
                    "scheduled_fire_time": scheduled_fire_time,
                }
                max_runtime_sec = row["schedule_max_runtime_sec"]
                if max_runtime_sec is not None:
                    payload["max_runtime_sec"] = int(max_runtime_sec)
                conn.execute(
                    """
                    INSERT INTO runs(run_id, schedule_id, profile_id, status, queued_at, payload_json)
                    VALUES (?, ?, ?, 'queued', ?, ?);
                    """,
                    (run_id, row["schedule_id"], row["profile_id"], now_iso, json.dumps(payload)),
                )
                if mode == "calendar":
                    conn.execute(
                        """
                        UPDATE schedules
                        SET next_run_at = ?, last_scheduled_fire_time = ?, updated_at = ?
                        WHERE schedule_id = ?;
                        """,
                        (next_run_update, scheduled_fire_time, now_iso, row["schedule_id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE schedules
                        SET next_run_at = ?, last_scheduled_fire_time = ?, updated_at = ?
                        WHERE schedule_id = ?;
                        """,
                        (next_run_update, now_iso, now_iso, row["schedule_id"]),
                    )
                due.append({"run_id": run_id, "schedule_id": row["schedule_id"], "profile_id": row["profile_id"]})

        return {"ok": True, "enqueued": len(due), "runs": due}

    def enqueue_manual_run(self, *, profile_id: str, description: str | None = None) -> dict[str, Any]:
        clean_profile = profile_id.strip()
        if not clean_profile:
            return {"ok": False, "error": "profile_id is required."}

        run_id = f"trun_{uuid4().hex}"
        queued_at = _iso(_utc_now())
        payload = {
            "schedule_id": None,
            "profile_id": clean_profile,
            "trigger": "manual",
            "description": description,
            "enqueued_at": queued_at,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs(run_id, schedule_id, profile_id, status, queued_at, payload_json)
                VALUES (?, NULL, ?, 'queued', ?, ?);
                """,
                (run_id, clean_profile, queued_at, json.dumps(payload)),
            )
        return {"ok": True, "run_id": run_id}

    def claim_next_run(self) -> dict[str, Any] | None:
        now_iso = _iso(_utc_now())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id FROM runs
                WHERE status = 'queued'
                ORDER BY queued_at ASC
                LIMIT 1;
                """
            ).fetchone()
            if row is None:
                return None

            run_id = row["run_id"]
            conn.execute(
                """
                UPDATE runs
                SET status = 'running', started_at = ?
                WHERE run_id = ?;
                """,
                (now_iso, run_id),
            )

            result = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at, summary, error, payload_json
                FROM runs
                WHERE run_id = ?;
                """,
                (run_id,),
            ).fetchone()

        if result is None:
            return None

        payload = json.loads(result["payload_json"]) if result["payload_json"] else {}
        return {
            "run_id": result["run_id"],
            "schedule_id": result["schedule_id"],
            "profile_id": result["profile_id"],
            "status": result["status"],
            "queued_at": result["queued_at"],
            "started_at": result["started_at"],
            "finished_at": result["finished_at"],
            "summary": result["summary"],
            "error": result["error"],
            "payload": payload if isinstance(payload, dict) else {},
        }

    def complete_run(
        self,
        *,
        run_id: str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"done", "failed", "blocked"}:
            return {"ok": False, "error": "invalid completion status"}

        now_iso = _iso(_utc_now())
        with self._connect() as conn:
            row = conn.execute("SELECT schedule_id FROM runs WHERE run_id = ?;", (run_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "run not found"}

            conn.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, summary = ?, error = ?
                WHERE run_id = ?;
                """,
                (status, now_iso, summary, error, run_id),
            )
            conn.execute(
                """
                INSERT INTO run_history(
                    run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at,
                    summary, error, payload_json, archived_at
                )
                SELECT
                    run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at,
                    summary, error, payload_json, ?
                FROM runs
                WHERE run_id = ?
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    summary = excluded.summary,
                    error = excluded.error,
                    payload_json = excluded.payload_json,
                    archived_at = excluded.archived_at;
                """,
                (now_iso, run_id),
            )

            schedule_id = row["schedule_id"]
            if schedule_id:
                successful_at = now_iso if status == "done" else None
                conn.execute(
                    """
                    UPDATE schedules
                    SET last_run_at = ?, last_successful_run_at = COALESCE(?, last_successful_run_at),
                        last_status = ?, last_summary = ?, last_error = ?, updated_at = ?
                    WHERE schedule_id = ?;
                    """,
                    (now_iso, successful_at, status, summary, error, now_iso, schedule_id),
                )

        return {"ok": True, "run_id": run_id, "status": status}

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at, summary, error, payload_json
                FROM runs
                ORDER BY queued_at DESC
                LIMIT ?;
                """,
                (safe_limit,),
            ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            out.append(
                {
                    "run_id": row["run_id"],
                    "schedule_id": row["schedule_id"],
                    "profile_id": row["profile_id"],
                    "status": row["status"],
                    "queued_at": row["queued_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "summary": row["summary"],
                    "error": row["error"],
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        return out

    def runtime_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            queued = conn.execute("SELECT COUNT(*) AS c FROM runs WHERE status = 'queued';").fetchone()["c"]
            running = conn.execute("SELECT COUNT(*) AS c FROM runs WHERE status = 'running';").fetchone()["c"]
        return {"queued_count": int(queued), "running_count": int(running)}

    def runtime_metrics(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        with self._connect() as conn:
            oldest_queued = conn.execute(
                "SELECT MIN(queued_at) AS t FROM runs WHERE status = 'queued';"
            ).fetchone()["t"]
            oldest_running = conn.execute(
                "SELECT MIN(started_at) AS t FROM runs WHERE status = 'running';"
            ).fetchone()["t"]

        queued_age = None
        running_age = None
        queued_dt = _parse_iso(oldest_queued) if isinstance(oldest_queued, str) else None
        running_dt = _parse_iso(oldest_running) if isinstance(oldest_running, str) else None
        if queued_dt is not None:
            queued_age = max(0.0, (now_dt - queued_dt).total_seconds())
        if running_dt is not None:
            running_age = max(0.0, (now_dt - running_dt).total_seconds())
        return {
            "oldest_queued_age_sec": queued_age,
            "longest_running_age_sec": running_age,
        }

    def list_run_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at, summary, error, payload_json
                FROM run_history
                ORDER BY COALESCE(finished_at, queued_at) DESC
                LIMIT ?;
                """,
                (safe_limit,),
            ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            out.append(
                {
                    "run_id": row["run_id"],
                    "schedule_id": row["schedule_id"],
                    "profile_id": row["profile_id"],
                    "status": row["status"],
                    "queued_at": row["queued_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "summary": row["summary"],
                    "error": row["error"],
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        return out

    def prune_runs(
        self,
        *,
        max_age_days: int | None = None,
        max_history_rows: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Prune completed run history while preserving queued/running runs."""
        deleted = 0
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        completion_statuses = ("done", "failed", "blocked")

        with self._connect() as conn:
            if isinstance(max_age_days, int) and max_age_days >= 0:
                cutoff = _iso(now_dt - timedelta(days=max_age_days))
                rows = conn.execute(
                    """
                    SELECT run_id
                    FROM run_history
                    WHERE status IN (?, ?, ?)
                      AND COALESCE(finished_at, queued_at) < ?;
                    """,
                    (*completion_statuses, cutoff),
                ).fetchall()
                for row in rows:
                    run_id = row["run_id"]
                    res_hist = conn.execute("DELETE FROM run_history WHERE run_id = ?;", (run_id,))
                    res_runs = conn.execute("DELETE FROM runs WHERE run_id = ?;", (run_id,))
                    deleted += int((res_hist.rowcount or 0) + (res_runs.rowcount or 0))

            if isinstance(max_history_rows, int) and max_history_rows >= 0:
                rows = conn.execute(
                    """
                    SELECT run_id
                    FROM run_history
                    WHERE status IN (?, ?, ?)
                    ORDER BY COALESCE(finished_at, queued_at) DESC;
                    """,
                    completion_statuses,
                )
                rows = rows.fetchall()
                if len(rows) > max_history_rows:
                    to_delete = [row["run_id"] for row in rows[max_history_rows:]]
                    for run_id in to_delete:
                        res_hist = conn.execute("DELETE FROM run_history WHERE run_id = ?;", (run_id,))
                        res_runs = conn.execute("DELETE FROM runs WHERE run_id = ?;", (run_id,))
                        deleted += int((res_hist.rowcount or 0) + (res_runs.rowcount or 0))

        return {"ok": True, "deleted_runs": deleted}
