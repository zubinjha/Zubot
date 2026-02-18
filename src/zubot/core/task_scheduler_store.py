"""SQLite-backed scheduler/queue store for task-agent runs."""

from __future__ import annotations

import json
import sqlite3
import shutil
import time as pytime
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


def _next_calendar_fire_on_or_after(
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
    for delta in range(0, 15):
        candidate_date = local_now.date() + timedelta(days=delta)
        candidate_local = datetime(
            year=candidate_date.year,
            month=candidate_date.month,
            day=candidate_date.day,
            hour=tod.hour,
            minute=tod.minute,
            tzinfo=zone,
        )
        if candidate_local < local_now:
            continue
        if not _weekday_allowed(days_of_week, candidate_local):
            continue
        return candidate_local.astimezone(UTC)
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

    def add_spec(time_of_day: str | None, timezone_name: str | None) -> None:
        normalized_time = _normalize_time_str(time_of_day)
        if normalized_time is None:
            return
        tz = str(timezone_name or "UTC").strip() or "UTC"
        try:
            ZoneInfo(tz)
        except Exception:
            return
        out.append({"time_of_day": normalized_time, "timezone": tz})

    if isinstance(run_times, list):
        for entry in run_times:
            if isinstance(entry, str):
                add_spec(entry, item.get("timezone"))
                continue
            if isinstance(entry, dict):
                add_spec(
                    entry.get("time_of_day"),
                    entry.get("timezone") or item.get("timezone"),
                )

    if not out:
        add_spec(item.get("time_of_day"), item.get("timezone"))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for spec in out:
        key = (str(spec["time_of_day"]), str(spec["timezone"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped


def _parse_schedule_days(item: dict[str, Any]) -> list[str]:
    explicit_days = _normalize_days_of_week(item.get("days_of_week"))
    if explicit_days:
        return explicit_days

    # Backward-compatible parse: if days were nested under run_times entries,
    # aggregate them into schedule-level day constraints.
    run_times = item.get("run_times")
    if not isinstance(run_times, list):
        return []
    aggregated: set[str] = set()
    for entry in run_times:
        if isinstance(entry, dict):
            for day in _normalize_days_of_week(entry.get("days_of_week")):
                aggregated.add(day)
    return [day for day in _WEEKDAY_ORDER if day in aggregated]


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
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _enable_wal_mode(self, conn: sqlite3.Connection) -> None:
        """Best-effort WAL enablement without failing under transient lock pressure."""
        attempts = 0
        while attempts < 3:
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                attempts += 1
                if attempts >= 3:
                    return
                pytime.sleep(0.05)

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
            self._enable_wal_mode(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS defined_tasks (
                    schedule_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    mode TEXT NOT NULL DEFAULT 'frequency' CHECK (mode IN ('frequency', 'calendar')),
                    execution_order INTEGER NOT NULL DEFAULT 100 CHECK (execution_order >= 0),
                    misfire_policy TEXT NOT NULL DEFAULT 'queue_latest' CHECK (misfire_policy IN ('queue_all', 'queue_latest', 'skip')),
                    run_frequency_minutes INTEGER CHECK (run_frequency_minutes IS NULL OR run_frequency_minutes > 0),
                    next_run_at TEXT,
                    last_planned_run_at TEXT,
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

                CREATE TABLE IF NOT EXISTS defined_tasks_days_of_week (
                    schedule_id TEXT NOT NULL,
                    day_of_week TEXT NOT NULL CHECK (day_of_week IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(schedule_id, day_of_week),
                    FOREIGN KEY(schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS defined_task_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_for_user', 'done', 'failed', 'blocked')),
                    planned_fire_at TEXT,
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
                    planned_fire_at TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_defined_tasks_next_run_at
                    ON defined_tasks(enabled, next_run_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_times_schedule_enabled
                    ON defined_tasks_run_times(schedule_id, enabled, time_of_day);
                CREATE INDEX IF NOT EXISTS idx_defined_tasks_days_schedule
                    ON defined_tasks_days_of_week(schedule_id, day_of_week);
                CREATE INDEX IF NOT EXISTS idx_defined_task_runs_status_queued_at
                    ON defined_task_runs(status, queued_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_runs_profile_queued_at
                    ON defined_task_runs(profile_id, queued_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_defined_task_runs_schedule_planned_fire
                    ON defined_task_runs(schedule_id, planned_fire_at)
                    WHERE schedule_id IS NOT NULL AND planned_fire_at IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_status_finished_at
                    ON defined_task_run_history(status, finished_at);
                CREATE INDEX IF NOT EXISTS idx_defined_task_run_history_profile_finished_at
                    ON defined_task_run_history(profile_id, finished_at);

                CREATE TABLE IF NOT EXISTS task_profiles (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('script', 'agentic', 'interactive_wrapper')),
                    entrypoint_path TEXT,
                    module TEXT,
                    resources_path TEXT,
                    queue_group TEXT,
                    timeout_sec INTEGER,
                    retry_policy_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    source TEXT NOT NULL DEFAULT 'config',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS task_profile_run_stats (
                    task_id TEXT PRIMARY KEY,
                    last_queued_at TEXT,
                    last_started_at TEXT,
                    last_finished_at TEXT,
                    last_status TEXT,
                    last_run_id TEXT,
                    run_count_total INTEGER NOT NULL DEFAULT 0,
                    run_count_done INTEGER NOT NULL DEFAULT 0,
                    run_count_failed INTEGER NOT NULL DEFAULT 0,
                    run_count_blocked INTEGER NOT NULL DEFAULT 0,
                    run_count_waiting INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(task_id) REFERENCES task_profiles(task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_task_profiles_kind_enabled
                    ON task_profiles(kind, enabled);
                CREATE INDEX IF NOT EXISTS idx_task_profiles_queue_group
                    ON task_profiles(queue_group);

                CREATE TABLE IF NOT EXISTS scheduler_runtime_state (
                    id TEXT PRIMARY KEY,
                    last_heartbeat_started_at TEXT,
                    last_heartbeat_finished_at TEXT,
                    last_heartbeat_status TEXT,
                    last_heartbeat_error TEXT,
                    last_heartbeat_enqueued_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_state_kv (
                    task_id TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT,
                    PRIMARY KEY(task_id, state_key)
                );

                CREATE TABLE IF NOT EXISTS task_seen_items (
                    task_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(task_id, provider, item_key)
                );
                CREATE INDEX IF NOT EXISTS idx_task_seen_items_task_provider_first_seen
                    ON task_seen_items(task_id, provider, first_seen_at DESC);

                CREATE TABLE IF NOT EXISTS job_applications (
                    job_key TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    job_title TEXT NOT NULL,
                    location TEXT NOT NULL,
                    date_found TEXT NOT NULL,
                    date_applied TEXT,
                    status TEXT NOT NULL CHECK (status IN ('Recommend Apply', 'Recommend Maybe', 'Applied', 'Interviewing', 'Offer', 'Rejected', 'Closed')),
                    pay_range TEXT,
                    job_link TEXT NOT NULL,
                    source TEXT NOT NULL,
                    cover_letter TEXT,
                    notes TEXT,
                    ai_notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_job_applications_date_found
                    ON job_applications(date_found);
                CREATE INDEX IF NOT EXISTS idx_job_applications_status
                    ON job_applications(status);

                CREATE TABLE IF NOT EXISTS job_discovery (
                    task_id TEXT NOT NULL,
                    job_key TEXT NOT NULL,
                    found_at TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN ('Recommend Apply', 'Recommend Maybe', 'Skip')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(task_id, job_key),
                    FOREIGN KEY(task_id) REFERENCES task_profiles(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_discovery_found_at
                    ON job_discovery(found_at);
                CREATE INDEX IF NOT EXISTS idx_job_discovery_decision
                    ON job_discovery(decision);
                """
            )
            run_table_sql = conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'defined_task_runs';
                """
            ).fetchone()
            run_table_sql_text = str(run_table_sql["sql"] or "").lower() if run_table_sql else ""
            if "waiting_for_user" not in run_table_sql_text:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS defined_task_runs_new (
                        run_id TEXT PRIMARY KEY,
                        schedule_id TEXT,
                        profile_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_for_user', 'done', 'failed', 'blocked')),
                        planned_fire_at TEXT,
                        queued_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        summary TEXT,
                        error TEXT,
                        payload_json TEXT NOT NULL,
                        FOREIGN KEY(schedule_id) REFERENCES defined_tasks(schedule_id) ON DELETE SET NULL
                    );
                    INSERT INTO defined_task_runs_new(run_id, schedule_id, profile_id, status, planned_fire_at, queued_at, started_at, finished_at, summary, error, payload_json)
                    SELECT run_id, schedule_id, profile_id, status, NULL, queued_at, started_at, finished_at, summary, error, payload_json
                    FROM defined_task_runs;
                    DROP TABLE defined_task_runs;
                    ALTER TABLE defined_task_runs_new RENAME TO defined_task_runs;
                    CREATE INDEX IF NOT EXISTS idx_defined_task_runs_status_queued_at
                        ON defined_task_runs(status, queued_at);
                    CREATE INDEX IF NOT EXISTS idx_defined_task_runs_profile_queued_at
                        ON defined_task_runs(profile_id, queued_at);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_defined_task_runs_schedule_planned_fire
                        ON defined_task_runs(schedule_id, planned_fire_at)
                        WHERE schedule_id IS NOT NULL AND planned_fire_at IS NOT NULL;
                    """
                )
            task_columns = {
                str(col["name"])
                for col in conn.execute("PRAGMA table_info(defined_tasks);").fetchall()
            }
            if "misfire_policy" not in task_columns:
                conn.execute(
                    "ALTER TABLE defined_tasks ADD COLUMN misfire_policy TEXT NOT NULL DEFAULT 'queue_latest';"
                )
            if "next_run_at" not in task_columns:
                conn.execute("ALTER TABLE defined_tasks ADD COLUMN next_run_at TEXT;")
            if "last_planned_run_at" not in task_columns:
                conn.execute("ALTER TABLE defined_tasks ADD COLUMN last_planned_run_at TEXT;")

            job_app_table_sql = conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'job_applications';
                """
            ).fetchone()
            job_app_table_sql_text = str(job_app_table_sql["sql"] or "") if job_app_table_sql else ""
            if "Recommend Apply" not in job_app_table_sql_text:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS job_applications_new (
                        job_key TEXT PRIMARY KEY,
                        company TEXT NOT NULL,
                        job_title TEXT NOT NULL,
                        location TEXT NOT NULL,
                        date_found TEXT NOT NULL,
                        date_applied TEXT,
                        status TEXT NOT NULL CHECK (status IN ('Recommend Apply', 'Recommend Maybe', 'Applied', 'Interviewing', 'Offer', 'Rejected', 'Closed')),
                        pay_range TEXT,
                        job_link TEXT NOT NULL,
                        source TEXT NOT NULL,
                        cover_letter TEXT,
                        notes TEXT,
                        ai_notes TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO job_applications_new(job_key, company, job_title, location, date_found, date_applied, status, pay_range, job_link, source, cover_letter, notes, ai_notes, created_at, updated_at)
                    SELECT
                        job_key,
                        company,
                        job_title,
                        location,
                        date_found,
                        date_applied,
                        CASE
                            WHEN status = 'Found' THEN 'Recommend Apply'
                            ELSE status
                        END,
                        pay_range,
                        job_link,
                        source,
                        cover_letter,
                        notes,
                        '',
                        created_at,
                        updated_at
                    FROM job_applications;
                    DROP TABLE job_applications;
                    ALTER TABLE job_applications_new RENAME TO job_applications;
                    CREATE INDEX IF NOT EXISTS idx_job_applications_date_found
                        ON job_applications(date_found);
                    CREATE INDEX IF NOT EXISTS idx_job_applications_status
                        ON job_applications(status);
                    """
                )

            job_app_columns = {
                str(col["name"])
                for col in conn.execute("PRAGMA table_info(job_applications);").fetchall()
            }
            if "ai_notes" not in job_app_columns:
                conn.execute("ALTER TABLE job_applications ADD COLUMN ai_notes TEXT;")

            run_columns = {
                str(col["name"])
                for col in conn.execute("PRAGMA table_info(defined_task_runs);").fetchall()
            }
            if "planned_fire_at" not in run_columns:
                conn.execute("ALTER TABLE defined_task_runs ADD COLUMN planned_fire_at TEXT;")

            history_columns = {
                str(col["name"])
                for col in conn.execute("PRAGMA table_info(defined_task_run_history);").fetchall()
            }
            if "planned_fire_at" not in history_columns:
                conn.execute("ALTER TABLE defined_task_run_history ADD COLUMN planned_fire_at TEXT;")
            # One-time compatibility migration from older schema where weekdays
            # were attached to run_time rows instead of schedule rows.
            old_days_table = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'defined_tasks_run_times_days_of_week';
                """
            ).fetchone()
            if old_days_table is not None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO defined_tasks_days_of_week(schedule_id, day_of_week, created_at)
                    SELECT rt.schedule_id, rtd.day_of_week, COALESCE(rtd.created_at, ?)
                    FROM defined_tasks_run_times_days_of_week rtd
                    JOIN defined_tasks_run_times rt ON rt.run_time_id = rtd.run_time_id;
                    """,
                    (_iso(_utc_now()),),
                )
            # Safety cleanup: remove orphan child rows that may have been created
            # earlier when foreign key enforcement was disabled.
            conn.execute(
                """
                DELETE FROM defined_tasks_run_times
                WHERE schedule_id NOT IN (SELECT schedule_id FROM defined_tasks);
                """
            )
            conn.execute(
                """
                DELETE FROM defined_tasks_days_of_week
                WHERE schedule_id NOT IN (SELECT schedule_id FROM defined_tasks);
                """
            )

    def _replace_run_times(self, conn: sqlite3.Connection, *, schedule_id: str, specs: list[dict[str, Any]], now: str) -> None:
        conn.execute("DELETE FROM defined_tasks_run_times WHERE schedule_id = ?;", (schedule_id,))

        for spec in specs:
            conn.execute(
                """
                INSERT INTO defined_tasks_run_times(schedule_id, time_of_day, timezone, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?);
                """,
                (schedule_id, str(spec["time_of_day"]), str(spec["timezone"]), now, now),
            )

    def _replace_schedule_days(self, conn: sqlite3.Connection, *, schedule_id: str, days: list[str], now: str) -> None:
        conn.execute("DELETE FROM defined_tasks_days_of_week WHERE schedule_id = ?;", (schedule_id,))
        for day in days:
            conn.execute(
                """
                INSERT OR IGNORE INTO defined_tasks_days_of_week(schedule_id, day_of_week, created_at)
                VALUES (?, ?, ?);
                """,
                (schedule_id, day, now),
            )

    @staticmethod
    def _normalize_misfire_policy(raw: Any) -> str:
        policy = str(raw or "queue_latest").strip().lower()
        if policy in {"queue_all", "queue_latest", "skip"}:
            return policy
        return "queue_latest"

    @staticmethod
    def _next_calendar_fire_for_specs(
        *,
        run_times: list[dict[str, Any]],
        schedule_days: list[str],
        now_dt: datetime,
    ) -> datetime | None:
        candidates: list[datetime] = []
        for spec in run_times:
            fire = _next_calendar_fire_on_or_after(
                now_dt=now_dt,
                timezone_name=str(spec.get("timezone") or "UTC"),
                time_of_day=str(spec.get("time_of_day") or ""),
                days_of_week=list(schedule_days),
            )
            if fire is not None:
                candidates.append(fire)
        if not candidates:
            return None
        return min(candidates)

    @staticmethod
    def _next_fire_after_cursor(
        *,
        mode: str,
        cursor_dt: datetime,
        frequency_minutes: int | None,
        run_times: list[dict[str, Any]],
        schedule_days: list[str],
    ) -> datetime | None:
        if mode == "frequency":
            freq = int(frequency_minutes or 0)
            if freq <= 0:
                return None
            return cursor_dt + timedelta(minutes=freq)

        candidates: list[datetime] = []
        for spec in run_times:
            fire = _next_calendar_fire_after(
                fire_dt=cursor_dt,
                timezone_name=str(spec.get("timezone") or "UTC"),
                time_of_day=str(spec.get("time_of_day") or ""),
                days_of_week=list(schedule_days),
            )
            if fire is not None:
                candidates.append(fire)
        if not candidates:
            return None
        return min(candidates)

    @staticmethod
    def _normalize_task_kind(raw: Any) -> str:
        kind = str(raw or "script").strip().lower()
        if kind in {"script", "agentic", "interactive_wrapper"}:
            return kind
        return "script"

    def upsert_task_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        task_id = str(profile.get("task_id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id is required."}
        name = str(profile.get("name") or task_id).strip() or task_id
        kind = self._normalize_task_kind(profile.get("kind"))
        entrypoint_path = str(profile.get("entrypoint_path") or "").strip() or None
        module = str(profile.get("module") or "").strip() or None
        resources_path = str(profile.get("resources_path") or "").strip() or None
        queue_group = str(profile.get("queue_group") or "").strip() or None
        timeout_sec = profile.get("timeout_sec")
        timeout_value = int(timeout_sec) if isinstance(timeout_sec, int) and timeout_sec > 0 else None
        retry_policy = profile.get("retry_policy") if isinstance(profile.get("retry_policy"), dict) else None
        enabled = 1 if bool(profile.get("enabled", True)) else 0
        source = str(profile.get("source") or "ui").strip() or "ui"
        now = _iso(_utc_now())

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_profiles(
                    task_id, name, kind, entrypoint_path, module, resources_path, queue_group,
                    timeout_sec, retry_policy_json, enabled, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    entrypoint_path = excluded.entrypoint_path,
                    module = excluded.module,
                    resources_path = excluded.resources_path,
                    queue_group = excluded.queue_group,
                    timeout_sec = excluded.timeout_sec,
                    retry_policy_json = excluded.retry_policy_json,
                    enabled = excluded.enabled,
                    source = excluded.source,
                    updated_at = excluded.updated_at;
                """,
                (
                    task_id,
                    name,
                    kind,
                    entrypoint_path,
                    module,
                    resources_path,
                    queue_group,
                    timeout_value,
                    json.dumps(retry_policy) if isinstance(retry_policy, dict) else None,
                    enabled,
                    source,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_profile_run_stats(task_id)
                VALUES (?)
                ON CONFLICT(task_id) DO NOTHING;
                """,
                (task_id,),
            )
        return {"ok": True, "task_id": task_id}

    def list_task_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, name, kind, entrypoint_path, module, resources_path, queue_group,
                       timeout_sec, retry_policy_json, enabled, source, created_at, updated_at
                FROM task_profiles
                ORDER BY task_id ASC;
                """
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            retry_policy = None
            raw_retry = row["retry_policy_json"]
            if isinstance(raw_retry, str) and raw_retry.strip():
                try:
                    parsed = json.loads(raw_retry)
                    retry_policy = parsed if isinstance(parsed, dict) else None
                except Exception:
                    retry_policy = None
            out.append(
                {
                    "task_id": str(row["task_id"]),
                    "name": str(row["name"] or row["task_id"]),
                    "kind": self._normalize_task_kind(row["kind"]),
                    "entrypoint_path": row["entrypoint_path"],
                    "module": row["module"],
                    "resources_path": row["resources_path"],
                    "queue_group": row["queue_group"],
                    "timeout_sec": int(row["timeout_sec"]) if row["timeout_sec"] is not None else None,
                    "retry_policy": retry_policy,
                    "enabled": bool(row["enabled"]),
                    "source": row["source"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def get_task_profile(self, *, task_id: str) -> dict[str, Any] | None:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return None
        rows = [item for item in self.list_task_profiles() if item.get("task_id") == clean_task_id]
        if not rows:
            return None
        return rows[0]

    def delete_task_profile(self, *, task_id: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return {"ok": False, "error": "task_id is required."}
        with self._connect() as conn:
            linked_schedules = conn.execute(
                "SELECT COUNT(*) AS c FROM defined_tasks WHERE profile_id = ?;",
                (clean_task_id,),
            ).fetchone()
            if int(linked_schedules["c"] or 0) > 0:
                return {"ok": False, "error": "task profile has linked schedules. Delete schedules first."}
            res = conn.execute("DELETE FROM task_profiles WHERE task_id = ?;", (clean_task_id,))
            deleted = int(res.rowcount or 0)
            conn.execute("DELETE FROM task_profile_run_stats WHERE task_id = ?;", (clean_task_id,))
        return {"ok": True, "task_id": clean_task_id, "deleted": deleted}

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
                misfire_policy = self._normalize_misfire_policy(item.get("misfire_policy"))
                execution_order_raw = item.get("execution_order")
                execution_order = int(execution_order_raw) if isinstance(execution_order_raw, int) and execution_order_raw >= 0 else 100

                run_frequency_minutes: int | None
                run_time_specs: list[dict[str, Any]] = []
                schedule_days: list[str] = []
                next_run_at: str | None = None
                next_run_override = _parse_iso(
                    str(item.get("next_run_at"))
                    if isinstance(item.get("next_run_at"), str)
                    else None
                )
                if next_run_override is not None:
                    next_run_at = _iso(next_run_override)
                if mode == "frequency":
                    freq = item.get("run_frequency_minutes")
                    if not isinstance(freq, int) or freq <= 0:
                        continue
                    run_frequency_minutes = int(freq)
                else:
                    run_frequency_minutes = None
                    run_time_specs = _parse_run_time_specs(item)
                    schedule_days = _parse_schedule_days(item)
                    if not run_time_specs:
                        continue

                conn.execute(
                    """
                    INSERT INTO defined_tasks(
                        schedule_id, profile_id, enabled, mode, execution_order,
                        misfire_policy, run_frequency_minutes, next_run_at, created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(schedule_id) DO UPDATE SET
                        profile_id = excluded.profile_id,
                        enabled = excluded.enabled,
                        mode = excluded.mode,
                        execution_order = excluded.execution_order,
                        misfire_policy = excluded.misfire_policy,
                        run_frequency_minutes = excluded.run_frequency_minutes,
                        next_run_at = excluded.next_run_at,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        schedule_id,
                        profile_id,
                        enabled,
                        mode,
                        execution_order,
                        misfire_policy,
                        run_frequency_minutes,
                        next_run_at,
                        now,
                        now,
                    ),
                )

                self._replace_run_times(conn, schedule_id=schedule_id, specs=run_time_specs, now=now)
                self._replace_schedule_days(conn, schedule_id=schedule_id, days=schedule_days, now=now)
                upserted += 1
        return {"ok": True, "upserted": upserted}

    def upsert_schedule(self, schedule: dict[str, Any]) -> dict[str, Any]:
        schedule_id = str(schedule.get("schedule_id") or "").strip()
        profile_id = str(schedule.get("profile_id") or "").strip()
        if not schedule_id:
            return {"ok": False, "error": "schedule_id is required."}
        if not profile_id:
            return {"ok": False, "error": "profile_id is required."}
        out = self.sync_schedules([schedule])
        if not out.get("ok"):
            return out
        return {"ok": True, "schedule_id": schedule_id, "upserted": int(out.get("upserted") or 0)}

    def delete_schedule(self, *, schedule_id: str) -> dict[str, Any]:
        clean = schedule_id.strip()
        if not clean:
            return {"ok": False, "error": "schedule_id is required."}
        with self._connect() as conn:
            res = conn.execute("DELETE FROM defined_tasks WHERE schedule_id = ?;", (clean,))
            deleted = int(res.rowcount or 0)
        return {"ok": True, "schedule_id": clean, "deleted": deleted}

    def _load_run_times_for_schedule(self, conn: sqlite3.Connection, *, schedule_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT rt.run_time_id, rt.time_of_day, rt.timezone, rt.enabled
            FROM defined_tasks_run_times rt
            WHERE rt.schedule_id = ?
            ORDER BY rt.time_of_day ASC;
            """,
            (schedule_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "run_time_id": int(row["run_time_id"]),
                    "time_of_day": str(row["time_of_day"]),
                    "timezone": str(row["timezone"]),
                    "enabled": bool(row["enabled"]),
                }
            )
        return out

    def _load_schedule_days(self, conn: sqlite3.Connection, *, schedule_id: str) -> list[str]:
        rows = conn.execute(
            """
            SELECT day_of_week
            FROM defined_tasks_days_of_week
            WHERE schedule_id = ?
            ORDER BY CASE day_of_week
                WHEN 'mon' THEN 1
                WHEN 'tue' THEN 2
                WHEN 'wed' THEN 3
                WHEN 'thu' THEN 4
                WHEN 'fri' THEN 5
                WHEN 'sat' THEN 6
                WHEN 'sun' THEN 7
                ELSE 99
            END ASC;
            """,
            (schedule_id,),
        ).fetchall()
        return [str(row["day_of_week"]) for row in rows]

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, profile_id, enabled, mode, execution_order, misfire_policy,
                       run_frequency_minutes, next_run_at, last_planned_run_at,
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
                schedule_days = self._load_schedule_days(conn, schedule_id=schedule_id)
                first = run_times[0] if run_times else None
                run_times_with_days = [
                    {
                        **item,
                        "days_of_week": list(schedule_days),
                    }
                    for item in run_times
                ]
                out.append(
                    {
                        "schedule_id": schedule_id,
                        "task_id": str(row["profile_id"]),
                        "profile_id": str(row["profile_id"]),
                        "enabled": bool(row["enabled"]),
                        "mode": str(row["mode"] or "frequency"),
                        "execution_order": int(row["execution_order"]),
                        "misfire_policy": self._normalize_misfire_policy(row["misfire_policy"]),
                        "run_frequency_minutes": int(row["run_frequency_minutes"])
                        if row["run_frequency_minutes"] is not None
                        else None,
                        "next_run_at": row["next_run_at"],
                        "last_planned_run_at": row["last_planned_run_at"],
                        "last_scheduled_fire_time": row["last_scheduled_fire_time"],
                        "last_run_at": row["last_run_at"],
                        "last_successful_run_at": row["last_successful_run_at"],
                        "last_status": row["last_status"],
                        "last_summary": row["last_summary"],
                        "last_error": row["last_error"],
                        "run_times": run_times_with_days,
                        # Compatibility facade for older callers.
                        "timezone": first["timezone"] if first else None,
                        "time_of_day": first["time_of_day"] if first else None,
                        "days_of_week": list(schedule_days),
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
                       misfire_policy, run_frequency_minutes, next_run_at,
                       last_planned_run_at, last_scheduled_fire_time
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
                policy = self._normalize_misfire_policy(row["misfire_policy"])
                execution_order = int(row["execution_order"]) if row["execution_order"] is not None else 100
                freq_minutes = int(row["run_frequency_minutes"]) if row["run_frequency_minutes"] is not None else None
                run_times = (
                    [item for item in self._load_run_times_for_schedule(conn, schedule_id=schedule_id) if item.get("enabled")]
                    if mode == "calendar"
                    else []
                )
                schedule_days = self._load_schedule_days(conn, schedule_id=schedule_id) if mode == "calendar" else []
                current_cursor = _parse_iso(row["next_run_at"] if isinstance(row["next_run_at"], str) else None)

                if current_cursor is None:
                    last_planned = _parse_iso(row["last_planned_run_at"] if isinstance(row["last_planned_run_at"], str) else None)
                    last_scheduled = _parse_iso(
                        row["last_scheduled_fire_time"] if isinstance(row["last_scheduled_fire_time"], str) else None
                    )
                    anchor = last_planned or last_scheduled
                    if anchor is not None:
                        current_cursor = self._next_fire_after_cursor(
                            mode=mode,
                            cursor_dt=anchor,
                            frequency_minutes=freq_minutes,
                            run_times=run_times,
                            schedule_days=schedule_days,
                        )
                    elif mode == "frequency":
                        current_cursor = now_dt
                    else:
                        recent_candidates: list[datetime] = []
                        for spec in run_times:
                            recent = _most_recent_calendar_fire(
                                now_dt=now_dt,
                                timezone_name=str(spec.get("timezone") or "UTC"),
                                time_of_day=str(spec.get("time_of_day") or ""),
                                days_of_week=list(schedule_days),
                            )
                            if recent is not None:
                                recent_candidates.append(recent)
                        if recent_candidates:
                            recent_fire = max(recent_candidates)
                            if now_dt <= recent_fire + timedelta(minutes=DEFAULT_CALENDAR_CATCHUP_MINUTES):
                                current_cursor = recent_fire
                            else:
                                current_cursor = self._next_calendar_fire_for_specs(
                                    run_times=run_times,
                                    schedule_days=schedule_days,
                                    now_dt=now_dt,
                                )
                        else:
                            current_cursor = self._next_calendar_fire_for_specs(
                                run_times=run_times,
                                schedule_days=schedule_days,
                                now_dt=now_dt,
                            )
                    if current_cursor is not None:
                        conn.execute(
                            """
                            UPDATE defined_tasks
                            SET next_run_at = ?, updated_at = ?
                            WHERE schedule_id = ?;
                            """,
                            (_iso(current_cursor), now_iso, schedule_id),
                        )

                if current_cursor is None:
                    continue
                if mode == "frequency" and (freq_minutes is None or freq_minutes <= 0):
                    continue
                if mode == "calendar" and not run_times:
                    continue
                if current_cursor > now_dt:
                    continue

                has_active_profile_run = conn.execute(
                    """
                    SELECT 1
                    FROM defined_task_runs
                    WHERE profile_id = ?
                      AND status IN ('queued', 'running', 'waiting_for_user')
                    LIMIT 1;
                    """,
                    (profile_id,),
                ).fetchone()
                if has_active_profile_run is not None:
                    continue

                due_fires: list[datetime] = []
                cursor = current_cursor
                for _ in range(0, 512):
                    if cursor is None or cursor > now_dt:
                        break
                    due_fires.append(cursor)
                    nxt = self._next_fire_after_cursor(
                        mode=mode,
                        cursor_dt=cursor,
                        frequency_minutes=freq_minutes,
                        run_times=run_times,
                        schedule_days=schedule_days,
                    )
                    if nxt is None or nxt <= cursor:
                        cursor = None
                        break
                    cursor = nxt

                if not due_fires:
                    continue

                selected_fire: datetime | None
                if policy == "queue_all":
                    selected_fire = due_fires[0]
                elif policy == "skip":
                    selected_fire = None
                else:
                    selected_fire = due_fires[-1]

                if selected_fire is not None:
                    fire_iso = _iso(selected_fire)
                    run_id = f"trun_{uuid4().hex}"
                    payload = {
                        "schedule_id": schedule_id,
                        "profile_id": profile_id,
                        "trigger": "scheduled",
                        "origin": "scheduled",
                        "enqueued_at": now_iso,
                        "mode": mode,
                        "scheduled_fire_time": fire_iso,
                    }
                    try:
                        conn.execute(
                            """
                            INSERT INTO defined_task_runs(
                                run_id, schedule_id, profile_id, status, planned_fire_at, queued_at, payload_json
                            )
                            VALUES (?, ?, ?, 'queued', ?, ?, ?);
                            """,
                            (run_id, schedule_id, profile_id, fire_iso, now_iso, json.dumps(payload)),
                        )
                    except sqlite3.IntegrityError:
                        pass
                    else:
                        due.append(
                            {
                                "run_id": run_id,
                                "schedule_id": schedule_id,
                                "profile_id": profile_id,
                                "execution_order": execution_order,
                                "planned_fire_at": fire_iso,
                            }
                        )

                if policy == "queue_all":
                    if selected_fire is not None:
                        next_cursor_dt = self._next_fire_after_cursor(
                            mode=mode,
                            cursor_dt=selected_fire,
                            frequency_minutes=freq_minutes,
                            run_times=run_times,
                            schedule_days=schedule_days,
                        )
                        last_processed_dt = selected_fire
                    else:
                        next_cursor_dt = cursor
                        last_processed_dt = due_fires[0]
                elif policy == "skip":
                    next_cursor_dt = cursor
                    last_processed_dt = due_fires[-1]
                else:
                    next_cursor_dt = cursor
                    last_processed_dt = selected_fire if selected_fire is not None else due_fires[-1]

                if next_cursor_dt is None:
                    next_cursor_dt = last_processed_dt

                conn.execute(
                    """
                    UPDATE defined_tasks
                    SET next_run_at = ?,
                        last_planned_run_at = ?,
                        last_scheduled_fire_time = ?,
                        updated_at = ?
                    WHERE schedule_id = ?;
                    """,
                    (
                        _iso(next_cursor_dt) if isinstance(next_cursor_dt, datetime) else None,
                        _iso(last_processed_dt),
                        _iso(last_processed_dt),
                        now_iso,
                        schedule_id,
                    ),
                )

        due.sort(key=lambda item: (int(item.get("execution_order", 100)), str(item.get("schedule_id") or "")))
        return {"ok": True, "enqueued": len(due), "runs": due}

    def enqueue_manual_run(
        self,
        *,
        profile_id: str,
        description: str | None = None,
        run_kind: str = "predefined",
        payload_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_profile = profile_id.strip()
        if not clean_profile:
            return {"ok": False, "error": "profile_id is required."}

        run_id = f"trun_{uuid4().hex}"
        queued_at = _iso(_utc_now())
        kind = str(run_kind or "predefined").strip().lower()
        if kind not in {"predefined", "agentic"}:
            kind = "predefined"
        payload = {
            "schedule_id": None,
            "profile_id": clean_profile,
            "trigger": "manual",
            "origin": "manual",
            "run_kind": kind,
            "description": description,
            "enqueued_at": queued_at,
        }
        if isinstance(payload_overrides, dict):
            payload.update(payload_overrides)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO defined_task_runs(run_id, schedule_id, profile_id, status, queued_at, payload_json)
                VALUES (?, NULL, ?, 'queued', ?, ?);
                """,
                (run_id, clean_profile, queued_at, json.dumps(payload)),
            )
        return {"ok": True, "run_id": run_id, "run_kind": kind}

    def enqueue_agentic_run(
        self,
        *,
        task_name: str,
        instructions: str,
        requested_by: str = "main_agent",
        model_tier: str = "medium",
        tool_access: list[str] | None = None,
        skill_access: list[str] | None = None,
        timeout_sec: int = 180,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_instructions = str(instructions or "").strip()
        if not clean_instructions:
            return {"ok": False, "error": "instructions are required."}

        clean_name = str(task_name or "Agentic Task").strip() or "Agentic Task"
        clean_requested_by = str(requested_by or "main_agent").strip() or "main_agent"
        clean_model_tier = str(model_tier or "medium").strip().lower() or "medium"
        clean_timeout = int(timeout_sec) if isinstance(timeout_sec, int) and timeout_sec > 0 else 180
        payload = {
            "run_kind": "agentic",
            "origin": "agentic",
            "task_name": clean_name,
            "task_id": f"agentic_{uuid4().hex[:8]}",
            "instructions": clean_instructions,
            "requested_by": clean_requested_by,
            "model_tier": clean_model_tier,
            "tool_access": [str(item).strip() for item in (tool_access or []) if isinstance(item, str) and str(item).strip()],
            "skill_access": [str(item).strip() for item in (skill_access or []) if isinstance(item, str) and str(item).strip()],
            "timeout_sec": clean_timeout,
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        return self.enqueue_manual_run(
            profile_id="agentic_task",
            description=clean_name,
            run_kind="agentic",
            payload_overrides=payload,
        )

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
                SELECT run_id, schedule_id, profile_id, status, planned_fire_at,
                       queued_at, started_at, finished_at, summary, error, payload_json
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
            "planned_fire_at": result["planned_fire_at"],
            "queued_at": result["queued_at"],
            "started_at": result["started_at"],
            "finished_at": result["finished_at"],
            "summary": result["summary"],
            "error": result["error"],
            "payload": payload if isinstance(payload, dict) else {},
        }

    def get_run(self, *, run_id: str) -> dict[str, Any] | None:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, planned_fire_at,
                       queued_at, started_at, finished_at, summary, error, payload_json
                FROM defined_task_runs
                WHERE run_id = ?;
                """,
                (clean_run_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        return {
            "run_id": row["run_id"],
            "schedule_id": row["schedule_id"],
            "profile_id": row["profile_id"],
            "status": row["status"],
            "planned_fire_at": row["planned_fire_at"],
            "queued_at": row["queued_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "summary": row["summary"],
            "error": row["error"],
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
            row = conn.execute(
                "SELECT schedule_id, planned_fire_at, profile_id FROM defined_task_runs WHERE run_id = ?;",
                (run_id,),
            ).fetchone()
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
                    run_id, schedule_id, profile_id, status, planned_fire_at, queued_at, started_at, finished_at,
                    summary, error, payload_json, archived_at
                )
                SELECT
                    run_id, schedule_id, profile_id, status, planned_fire_at, queued_at, started_at, finished_at,
                    summary, error, payload_json, ?
                FROM defined_task_runs
                WHERE run_id = ?
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    planned_fire_at = excluded.planned_fire_at,
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

            profile_id = str(row["profile_id"] or "").strip()
            if profile_id:
                profile_exists = conn.execute(
                    "SELECT 1 FROM task_profiles WHERE task_id = ? LIMIT 1;",
                    (profile_id,),
                ).fetchone()
                if profile_exists is not None:
                    conn.execute(
                        """
                        INSERT INTO task_profile_run_stats(
                            task_id, last_finished_at, last_status, last_run_id,
                            run_count_total, run_count_done, run_count_failed, run_count_blocked, run_count_waiting
                        )
                        VALUES (?, ?, ?, ?, 1, ?, ?, ?, 0)
                        ON CONFLICT(task_id) DO UPDATE SET
                            last_finished_at = excluded.last_finished_at,
                            last_status = excluded.last_status,
                            last_run_id = excluded.last_run_id,
                            run_count_total = task_profile_run_stats.run_count_total + 1,
                            run_count_done = task_profile_run_stats.run_count_done + excluded.run_count_done,
                            run_count_failed = task_profile_run_stats.run_count_failed + excluded.run_count_failed,
                            run_count_blocked = task_profile_run_stats.run_count_blocked + excluded.run_count_blocked;
                        """,
                        (
                            profile_id,
                            now_iso,
                            status,
                            run_id,
                            1 if status == "done" else 0,
                            1 if status == "failed" else 0,
                            1 if status == "blocked" else 0,
                        ),
                    )

        return {"ok": True, "run_id": run_id, "status": status}

    def mark_waiting_for_user(
        self,
        *,
        run_id: str,
        question: str | None = None,
        wait_context: dict[str, Any] | None = None,
        requested_by: str = "main_agent",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            return {"ok": False, "error": "run_id is required."}
        now_iso = _iso(_utc_now())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, payload_json
                FROM defined_task_runs
                WHERE run_id = ?;
                """,
                (clean_run_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "run not found"}
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            payload_dict = payload if isinstance(payload, dict) else {}
            waiting = {
                "request_id": f"wait_{uuid4().hex[:10]}",
                "question": str(question or "").strip() or None,
                "context": wait_context if isinstance(wait_context, dict) else {},
                "requested_by": str(requested_by or "main_agent").strip() or "main_agent",
                "waiting_since": now_iso,
                "expires_at": str(expires_at or "").strip() or None,
                "state": "waiting_for_user",
            }
            payload_dict["waiting"] = waiting
            conn.execute(
                """
                UPDATE defined_task_runs
                SET status = 'waiting_for_user',
                    summary = ?,
                    error = NULL,
                    payload_json = ?
                WHERE run_id = ?;
                """,
                (waiting.get("question"), json.dumps(payload_dict), clean_run_id),
            )
        return {
            "ok": True,
            "run_id": clean_run_id,
            "status": "waiting_for_user",
            "waiting": waiting,
        }

    def resume_waiting_run(
        self,
        *,
        run_id: str,
        user_response: str,
        requested_by: str = "main_agent",
    ) -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        clean_response = str(user_response or "").strip()
        if not clean_run_id:
            return {"ok": False, "error": "run_id is required."}
        if not clean_response:
            return {"ok": False, "error": "user_response is required."}
        now_iso = _iso(_utc_now())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, payload_json
                FROM defined_task_runs
                WHERE run_id = ?;
                """,
                (clean_run_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "run not found"}
            if str(row["status"] or "") != "waiting_for_user":
                return {"ok": False, "error": "run is not waiting for user input"}
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            payload_dict = payload if isinstance(payload, dict) else {}
            waiting = payload_dict.get("waiting") if isinstance(payload_dict.get("waiting"), dict) else {}
            history = payload_dict.get("resume_history")
            resume_history = history if isinstance(history, list) else []
            resume_history.append(
                {
                    "response": clean_response,
                    "requested_by": str(requested_by or "main_agent").strip() or "main_agent",
                    "at": now_iso,
                }
            )
            payload_dict["resume_history"] = resume_history[-20:]
            payload_dict["resume_response"] = clean_response
            if isinstance(payload_dict.get("instructions"), str):
                payload_dict["instructions"] = f"{payload_dict['instructions']}\n\n[User Response]\n{clean_response}"
            waiting["resumed_at"] = now_iso
            waiting["resumed_by"] = str(requested_by or "main_agent").strip() or "main_agent"
            waiting["resume_requested_at"] = now_iso
            waiting["state"] = "resumed"
            payload_dict["waiting"] = waiting
            conn.execute(
                """
                UPDATE defined_task_runs
                SET status = 'queued',
                    queued_at = ?,
                    payload_json = ?,
                    summary = NULL,
                    error = NULL
                WHERE run_id = ?;
                """,
                (now_iso, json.dumps(payload_dict), clean_run_id),
            )
        return {
            "ok": True,
            "run_id": clean_run_id,
            "status": "queued",
            "resumed": True,
            "waiting": waiting,
        }

    def cancel_run(self, *, run_id: str, reason: str = "killed_by_user") -> dict[str, Any]:
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            return {"ok": False, "error": "run_id is required."}

        row = self.get_run(run_id=clean_run_id)
        if row is None:
            return {"ok": False, "error": "run not found"}

        status = str(row.get("status") or "")
        if status in {"done", "failed", "blocked"}:
            return {
                "ok": True,
                "run_id": clean_run_id,
                "status": status,
                "already_terminal": True,
            }
        if status in {"queued", "waiting_for_user"}:
            out = self.complete_run(run_id=clean_run_id, status="blocked", summary=None, error=reason)
            if out.get("ok"):
                out["already_terminal"] = False
            return out
        if status == "running":
            return {
                "ok": True,
                "run_id": clean_run_id,
                "status": "running",
                "cancel_requested": True,
                "already_terminal": False,
            }
        return {"ok": False, "error": f"unsupported run status `{status}`"}

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, planned_fire_at,
                       queued_at, started_at, finished_at, summary, error, payload_json
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
                    "planned_fire_at": row["planned_fire_at"],
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
            waiting = conn.execute(
                "SELECT COUNT(*) AS c FROM defined_task_runs WHERE status = 'waiting_for_user';"
            ).fetchone()["c"]
        return {
            "queued_count": int(queued),
            "running_count": int(running),
            "waiting_count": int(waiting),
        }

    def runtime_metrics(self, *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now.astimezone(UTC) if isinstance(now, datetime) else _utc_now()
        with self._connect() as conn:
            oldest_queued = conn.execute(
                "SELECT MIN(queued_at) AS t FROM defined_task_runs WHERE status = 'queued';"
            ).fetchone()["t"]
            oldest_running = conn.execute(
                "SELECT MIN(started_at) AS t FROM defined_task_runs WHERE status = 'running';"
            ).fetchone()["t"]
            oldest_waiting = conn.execute(
                "SELECT MIN(started_at) AS t FROM defined_task_runs WHERE status = 'waiting_for_user';"
            ).fetchone()["t"]

        queued_age = None
        running_age = None
        waiting_age = None
        queued_dt = _parse_iso(oldest_queued) if isinstance(oldest_queued, str) else None
        running_dt = _parse_iso(oldest_running) if isinstance(oldest_running, str) else None
        waiting_dt = _parse_iso(oldest_waiting) if isinstance(oldest_waiting, str) else None
        if queued_dt is not None:
            queued_age = max(0.0, (now_dt - queued_dt).total_seconds())
        if running_dt is not None:
            running_age = max(0.0, (now_dt - running_dt).total_seconds())
        if waiting_dt is not None:
            waiting_age = max(0.0, (now_dt - waiting_dt).total_seconds())
        return {
            "oldest_queued_age_sec": queued_age,
            "longest_running_age_sec": running_age,
            "longest_waiting_age_sec": waiting_age,
        }

    def record_heartbeat_state(
        self,
        *,
        started_at: str,
        finished_at: str,
        status: str,
        enqueued_count: int = 0,
        error: str | None = None,
    ) -> dict[str, Any]:
        clean_status = str(status or "").strip().lower()
        if clean_status not in {"ok", "error"}:
            clean_status = "error"
        clean_started = str(started_at or "").strip()
        clean_finished = str(finished_at or "").strip()
        if not clean_started or not clean_finished:
            return {"ok": False, "error": "started_at and finished_at are required."}
        clean_error = str(error or "").strip() or None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_runtime_state(
                    id, last_heartbeat_started_at, last_heartbeat_finished_at,
                    last_heartbeat_status, last_heartbeat_error, last_heartbeat_enqueued_count, updated_at
                )
                VALUES ('main', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_heartbeat_started_at = excluded.last_heartbeat_started_at,
                    last_heartbeat_finished_at = excluded.last_heartbeat_finished_at,
                    last_heartbeat_status = excluded.last_heartbeat_status,
                    last_heartbeat_error = excluded.last_heartbeat_error,
                    last_heartbeat_enqueued_count = excluded.last_heartbeat_enqueued_count,
                    updated_at = excluded.updated_at;
                """,
                (
                    clean_started,
                    clean_finished,
                    clean_status,
                    clean_error,
                    int(enqueued_count) if int(enqueued_count) >= 0 else 0,
                    clean_finished,
                ),
            )
        return {"ok": True}

    def heartbeat_state(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, last_heartbeat_started_at, last_heartbeat_finished_at,
                       last_heartbeat_status, last_heartbeat_error, last_heartbeat_enqueued_count, updated_at
                FROM scheduler_runtime_state
                WHERE id = 'main';
                """
            ).fetchone()
        if row is None:
            return {
                "ok": True,
                "state": {
                    "id": "main",
                    "last_heartbeat_started_at": None,
                    "last_heartbeat_finished_at": None,
                    "last_heartbeat_status": None,
                    "last_heartbeat_error": None,
                    "last_heartbeat_enqueued_count": 0,
                    "updated_at": None,
                },
            }
        return {
            "ok": True,
            "state": {
                "id": row["id"],
                "last_heartbeat_started_at": row["last_heartbeat_started_at"],
                "last_heartbeat_finished_at": row["last_heartbeat_finished_at"],
                "last_heartbeat_status": row["last_heartbeat_status"],
                "last_heartbeat_error": row["last_heartbeat_error"],
                "last_heartbeat_enqueued_count": int(row["last_heartbeat_enqueued_count"] or 0),
                "updated_at": row["updated_at"],
            },
        }

    def list_run_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, schedule_id, profile_id, status, planned_fire_at,
                       queued_at, started_at, finished_at, summary, error, payload_json
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
                    "planned_fire_at": row["planned_fire_at"],
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
