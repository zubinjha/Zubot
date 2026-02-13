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
DEFAULT_CALENDAR_CATCHUP_MINUTES = 180
_WEEKDAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


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


def _normalize_days_of_week(value: Any) -> list[str]:
    if isinstance(value, list):
        tokens = [str(v).strip().lower()[:3] for v in value if isinstance(v, str) and v.strip()]
    elif isinstance(value, str):
        tokens = [part.strip().lower()[:3] for part in value.split(",") if part.strip()]
    else:
        tokens = []
    seen = set(tokens)
    return [token for token in _WEEKDAY_ORDER if token in seen]


def _weekday_allowed(days: list[str], local_dt: datetime) -> bool:
    if not days:
        return True
    return _WEEKDAY_ORDER[local_dt.weekday()] in set(days)


def _most_recent_calendar_fire(
    *,
    now_dt: datetime,
    timezone_name: str,
    time_of_day: str,
    days_of_week: list[str],
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
    days_of_week: list[str],
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
    return _resolve_db_path(path)


def _normalize_time_str(raw: str | None) -> str | None:
    parsed = _parse_time_of_day(raw)
    if parsed is None:
        return None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _parse_run_time_specs(item: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    run_times = item.get("run_times")

    def add_spec(time_of_day: str | None, timezone_name: str | None, days_value: Any) -> None:
        normalized_time = _normalize_time_str(time_of_day)
        if normalized_time is None:
            return
        tz = str(timezone_name or "UTC").strip() or "UTC"
        try:
            ZoneInfo(tz)
        except Exception:
            return
        days = _normalize_days_of_week(days_value)
        out.append({"time_of_day": normalized_time, "timezone": tz, "days_of_week": days})

    if isinstance(run_times, list):
        for entry in run_times:
            if isinstance(entry, str):
                add_spec(entry, item.get("timezone"), item.get("days_of_week"))
                continue
            if isinstance(entry, dict):
                add_spec(
                    entry.get("time_of_day"),
                    entry.get("timezone") or item.get("timezone"),
                    entry.get("days_of_week", item.get("days_of_week")),
                )

    if not out:
        add_spec(item.get("time_of_day"), item.get("timezone"), item.get("days_of_week"))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for spec in out:
        key = (
            str(spec["time_of_day"]),
            str(spec["timezone"]),
            tuple(spec.get("days_of_week") or []),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


class TaskSchedulerStore:
    """Persist and query defined-task schedule/run queue state."""

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
        if self._db_path.exists():
            return
        if self._db_path.suffix != ".db":
            return
        legacy_path = self._db_path.with_suffix(".sqlite3")
        if legacy_path.exists():
            shutil.copy2(legacy_path, self._db_path)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS defined_tasks (
                    schedule_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    mode TEXT NOT NULL DEFAULT 'frequency' CHECK (mode IN ('frequency', 'calendar')),
                    execution_order INTEGER NOT NULL DEFAULT 100 CHECK (execution_order >= 0),
                    run_frequency_minutes INTEGER CHECK (run_frequency_minutes IS NULL OR run_frequency_minutes > 0),
                    last_scheduled_fire_time TEXT,
                    last_run_at TEXT,
                    last_successful_run_at TEXT,
                    last_status TEXT,
                    last_summary TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS defined_tasks_run_times (
                    run_time_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE CASCADE,
                    UNIQUE(schedule_id, time_of_day, timezone)
                );

                CREATE TABLE IF NOT EXISTS defined_tasks_run_times_days_of_week (
                    run_time_id INTEGER NOT NULL,
                    day_of_week TEXT NOT NULL CHECK (day_of_week IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_time_id, day_of_week),
                    FOREIGN KEY(run_time_id) REFERENCES defined_tasks_run_times(run_time_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS defined_task_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'blocked')),
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    summary TEXT,
                    error TEXT,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS defined_task_run_history (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('done', 'failed', 'blocked')),
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    summary TEXT,
                    error TEXT,
                    payload_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_defined_tasks_enabled_order
                    ON defined_tasks(enabled, execution_order, schedule_id);
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_times_schedule_enabled
                    ON defined_tasks_run_times(schedule_id, enabled, time_of_day);
                CREATE INDEX IF NOT EXISTS idx_defined_task_runs_status_queued_at
                    ON defined_task_runs(status, queued_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_runs_profile_queued_at
                    ON defined_task_runs(profile_id, queued_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_status_finished_at
                    ON defined_task_run_history(status, finished_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_profile_finished_at
                    ON defined_task_run_history(profile_id, finished_at);
                """
            )

    def _replace_run_times(self, conn: sqlite3.Connection, *, schedule_id: str, specs: list[dict[str, Any]], now: str) -> None:
        existing = conn.execute(
            "SELECT run_time_id FROM defined_tasks_run_times WHERE schedule_id = ?;",
            (schedule_id,),
        ).fetchall()
        for row in existing:
            conn.execute(
                "DELETE FROM defined_tasks_run_times_days_of_week WHERE run_time_id = ?;",
                (int(row["run_time_id"]),),
            )
        conn.execute("DELETE FROM defined_tasks_run_times WHERE schedule_id = ?;", (schedule_id,))

        for spec in specs:
            conn.execute(
                """
                INSERT INTO defined_tasks_run_times(schedule_id, time_of_day, timezone, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?);
                """,
                (schedule_id, str(spec["time_of_day"]), str(spec["timezone"]), now, now),
            )
            run_time_id = int(conn.execute("SELECT last_insert_rowid() AS rid;").fetchone()["rid"])
            for day in spec.get("days_of_week") or []:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO defined_tasks_run_times_days_of_week(run_time_id, day_of_week, created_at)
                    VALUES (?, ?, ?);
                    """,
                    (run_time_id, str(day), now),
                )

    def sync_schedules(self, defined_tasks: list[dict[str, Any]]) -> dict[str, Any]:
        now = _iso(_utc_now())
        upserted = 0
        with self._connect() as conn:
            for item in defined_tasks:
                schedule_id = str(item.get("schedule_id") or "").strip()
                profile_id = str(item.get("profile_id") or "").strip()
                if not schedule_id or not profile_id:
                    continue

                enabled = 1 if bool(item.get("enabled", True)) else 0
                mode_raw = str(item.get("mode") or "frequency").strip().lower()
                if mode_raw == "interval":
                    mode_raw = "frequency"
                mode = mode_raw if mode_raw in {"frequency", "calendar"} else "frequency"
                execution_order_raw = item.get("execution_order")
                execution_order = int(execution_order_raw) if isinstance(execution_order_raw, int) and execution_order_raw >= 0 else 100

                run_frequency_minutes: int | None
                run_time_specs: list[dict[str, Any]] = []
                if mode == "frequency":
                    freq = item.get("run_frequency_minutes")
                    if not isinstance(freq, int) or freq <= 0:
                        continue
                    run_frequency_minutes = int(freq)
                else:
                    run_frequency_minutes = None
                    run_time_specs = _parse_run_time_specs(item)
                    if not run_time_specs:
                        continue

                conn.execute(
                    """
                    INSERT INTO defined_tasks(
                        schedule_id, profile_id, enabled, mode, execution_order,
                        run_frequency_minutes, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_id) DO UPDATE SET
                        profile_id = excluded.profile_id,
                        enabled = excluded.enabled,
                        mode = excluded.mode,
                        execution_order = excluded.execution_order,
                        run_frequency_minutes = excluded.run_frequency_minutes,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        schedule_id,
                        profile_id,
                        enabled,
                        mode,
                        execution_order,
                        run_frequency_minutes,
                        now,
                        now,
                    ),
                )

                self._replace_run_times(conn, schedule_id=schedule_id, specs=run_time_specs, now=now)
                upserted += 1
        return {"ok": True, "upserted": upserted}

    def _load_run_times_for_schedule(self, conn: sqlite3.Connection, *, schedule_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT rt.run_time_id, rt.time_of_day, rt.timezone, rt.enabled,
                   GROUP_CONCAT(rtd.day_of_week) AS days_csv
            FROM defined_tasks_run_times rt
            LEFT JOIN defined_tasks_run_times_days_of_week rtd ON rtd.run_time_id = rt.run_time_id
            WHERE rt.schedule_id = ?
            GROUP BY rt.run_time_id, rt.time_of_day, rt.timezone, rt.enabled
            ORDER BY rt.time_of_day ASC;
            """,
            (schedule_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            days = _normalize_days_of_week(str(row["days_csv"])) if row["days_csv"] else []
            out.append(
                {
                    "run_time_id": int(row["run_time_id"]),
                    "time_of_day": str(row["time_of_day"]),
                    "timezone": str(row["timezone"]),
                    "days_of_week": days,
                    "enabled": bool(row["enabled"]),
                }
            )
        return out

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, profile_id, enabled, mode, execution_order, run_frequency_minutes,
                       last_scheduled_fire_time, last_run_at, last_successful_run_at,
                       last_status, last_summary, last_error
                FROM defined_tasks
                ORDER BY execution_order ASC, schedule_id ASC;
                """
            ).fetchall()

            out: list[dict[str, Any]] = []
            for row in rows:
                schedule_id = str(row["schedule_id"])
                run_times = self._load_run_times_for_schedule(conn, schedule_id=schedule_id)
                first = run_times[0] if run_times else None
                out.append(
                    {
                        "schedule_id": schedule_id,
                        "profile_id": str(row["profile_id"]),
                        "enabled": bool(row["enabled"]),
                        "mode": str(row["mode"] or "frequency"),
                        "execution_order": int(row["execution_order"]),
                        "run_frequency_minutes": int(row["run_frequency_minutes"])
                        if row["run_frequency_minutes"] is not None
                        else None,
                        "last_scheduled_fire_time": row["last_scheduled_fire_time"],
                        "last_run_at": row["last_run_at"],
                        "last_successful_run_at": row["last_successful_run_at"],
                        "last_status": row["last_status"],
                        "last_summary": row["last_summary"],
                        "last_error": row["last_error"],
                        "run_times": run_times,
                        # Compatibility facade for older callers.
                        "timezone": first["timezone"] if first else None,
                        "time_of_day": first["time_of_day"] if first else None,
                        "days_of_week": list(first["days_of_week"]) if first else None,
                        "catch_up_window_minutes": DEFAULT_CALENDAR_CATCHUP_MINUTES,
                        "max_runtime_sec": None,
                    }
                )
        return out

    def enqueue_due_runs(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        now_iso = _iso(now_dt)
        due: list[dict[str, Any]] = []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, profile_id, enabled, mode, execution_order,
                       run_frequency_minutes, last_scheduled_fire_time
                FROM defined_tasks
                WHERE enabled = 1
                ORDER BY execution_order ASC, schedule_id ASC;
                """
            ).fetchall()

            for row in rows:
                schedule_id = str(row["schedule_id"])
                profile_id = str(row["profile_id"])
                mode = str(row["mode"] or "frequency").strip().lower()
                if mode == "interval":
                    mode = "frequency"
                last_fire = _parse_iso(row["last_scheduled_fire_time"] if isinstance(row["last_scheduled_fire_time"], str) else None)
                execution_order = int(row["execution_order"]) if row["execution_order"] is not None else 100

                scheduled_fire_time: datetime | None = None

                if mode == "frequency":
                    freq = int(row["run_frequency_minutes"]) if row["run_frequency_minutes"] is not None else 0
                    if freq <= 0:
                        continue
                    if last_fire is None:
                        scheduled_fire_time = now_dt
                    elif now_dt >= (last_fire + timedelta(minutes=freq)):
                        scheduled_fire_time = now_dt
                else:
                    run_times = [item for item in self._load_run_times_for_schedule(conn, schedule_id=schedule_id) if item.get("enabled")]
                    candidates: list[tuple[datetime, str, list[str]]] = []
                    for spec in run_times:
                        fire = _most_recent_calendar_fire(
                            now_dt=now_dt,
                            timezone_name=str(spec["timezone"]),
                            time_of_day=str(spec["time_of_day"]),
                            days_of_week=list(spec.get("days_of_week") or []),
                        )
                        if fire is None:
                            continue
                        if now_dt > fire + timedelta(minutes=DEFAULT_CALENDAR_CATCHUP_MINUTES):
                            continue
                        candidates.append((fire, str(spec["time_of_day"]), list(spec.get("days_of_week") or [])))

                    if candidates:
                        chosen_fire, _chosen_time, _chosen_days = max(candidates, key=lambda item: item[0])
                        if last_fire is None or chosen_fire > last_fire:
                            scheduled_fire_time = chosen_fire

                if scheduled_fire_time is None:
                    continue

                existing = conn.execute(
                    """
                    SELECT run_id FROM defined_task_runs
                    WHERE schedule_id = ? AND status IN ('queued', 'running')
                    LIMIT 1;
                    """,
                    (schedule_id,),
                ).fetchone()
                if existing is not None:
                    continue

                run_id = f"trun_{uuid4().hex}"
                payload = {
                    "schedule_id": schedule_id,
                    "profile_id": profile_id,
                    "trigger": "scheduled",
                    "enqueued_at": now_iso,
                    "mode": mode,
                    "scheduled_fire_time": _iso(scheduled_fire_time),
                }
                conn.execute(
                    """
                    INSERT INTO defined_task_runs(run_id, schedule_id, profile_id, status, queued_at, payload_json)
                    VALUES (?, ?, ?, 'queued', ?, ?);
                    """,
                    (run_id, schedule_id, profile_id, now_iso, json.dumps(payload)),
                )
                conn.execute(
                    """
                    UPDATE defined_tasks
                    SET last_scheduled_fire_time = ?, updated_at = ?
                    WHERE schedule_id = ?;
                    """,
                    (_iso(scheduled_fire_time), now_iso, schedule_id),
                )
                due.append(
                    {
                        "run_id": run_id,
                        "schedule_id": schedule_id,
                        "profile_id": profile_id,
                        "execution_order": execution_order,
                    }
                )

        due.sort(key=lambda item: (int(item.get("execution_order", 100)), str(item.get("schedule_id") or "")))
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
                INSERT INTO defined_task_runs(run_id, schedule_id, profile_id, status, queued_at, payload_json)
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
                SELECT run_id FROM defined_task_runs
                WHERE status = 'queued'
                ORDER BY queued_at ASC
                LIMIT 1;
                """
            ).fetchone()
            if row is None:
                return None

            run_id = str(row["run_id"])
            conn.execute(
                """
                UPDATE defined_task_runs
                SET status = 'running', started_at = ?
                WHERE run_id = ?;
                """,
                (now_iso, run_id),
            )

            result = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at, summary, error, payload_json
                FROM defined_task_runs
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
            row = conn.execute("SELECT schedule_id FROM defined_task_runs WHERE run_id = ?;", (run_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "run not found"}

            conn.execute(
                """
                UPDATE defined_task_runs
                SET status = ?, finished_at = ?, summary = ?, error = ?
                WHERE run_id = ?;
                """,
                (status, now_iso, summary, error, run_id),
            )
            conn.execute(
                """
                INSERT INTO defined_task_run_history(
                    run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at,
                    summary, error, payload_json, archived_at
                )
                SELECT
                    run_id, schedule_id, profile_id, status, queued_at, started_at, finished_at,
                    summary, error, payload_json, ?
                FROM defined_task_runs
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
                    UPDATE defined_tasks
                    SET last_run_at = ?,
                        last_successful_run_at = COALESCE(?, last_successful_run_at),
                        last_status = ?,
                        last_summary = ?,
                        last_error = ?,
                        updated_at = ?
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
                FROM defined_task_runs
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
            queued = conn.execute("SELECT COUNT(*) AS c FROM defined_task_runs WHERE status = 'queued';").fetchone()["c"]
            running = conn.execute("SELECT COUNT(*) AS c FROM defined_task_runs WHERE status = 'running';").fetchone()["c"]
        return {"queued_count": int(queued), "running_count": int(running)}

    def runtime_metrics(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        with self._connect() as conn:
            oldest_queued = conn.execute(
                "SELECT MIN(queued_at) AS t FROM defined_task_runs WHERE status = 'queued';"
            ).fetchone()["t"]
            oldest_running = conn.execute(
                "SELECT MIN(started_at) AS t FROM defined_task_runs WHERE status = 'running';"
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
                FROM defined_task_run_history
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
        deleted = 0
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        completion_statuses = ("done", "failed", "blocked")

        with self._connect() as conn:
            if isinstance(max_age_days, int) and max_age_days >= 0:
                cutoff = _iso(now_dt - timedelta(days=max_age_days))
                rows = conn.execute(
                    """
                    SELECT run_id
                    FROM defined_task_run_history
                    WHERE status IN (?, ?, ?)
                      AND COALESCE(finished_at, queued_at) < ?;
                    """,
                    (*completion_statuses, cutoff),
                ).fetchall()
                for row in rows:
                    run_id = row["run_id"]
                    res_hist = conn.execute("DELETE FROM defined_task_run_history WHERE run_id = ?;", (run_id,))
                    res_runs = conn.execute("DELETE FROM defined_task_runs WHERE run_id = ?;", (run_id,))
                    deleted += int((res_hist.rowcount or 0) + (res_runs.rowcount or 0))

            if isinstance(max_history_rows, int) and max_history_rows >= 0:
                rows = conn.execute(
                    """
                    SELECT run_id
                    FROM defined_task_run_history
                    WHERE status IN (?, ?, ?)
                    ORDER BY COALESCE(finished_at, queued_at) DESC;
                    """,
                    completion_statuses,
                ).fetchall()
                if len(rows) > max_history_rows:
                    to_delete = [row["run_id"] for row in rows[max_history_rows:]]
                    for run_id in to_delete:
                        res_hist = conn.execute("DELETE FROM defined_task_run_history WHERE run_id = ?;", (run_id,))
                        res_runs = conn.execute("DELETE FROM defined_task_runs WHERE run_id = ?;", (run_id,))
                        deleted += int((res_hist.rowcount or 0) + (res_runs.rowcount or 0))

        return {"ok": True, "deleted_runs": deleted}
