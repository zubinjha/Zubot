"""Minimal local web chat interface for loop testing."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Literal

from src.zubot.core.control_protocol import extract_control_requests, is_expired
from src.zubot.runtime.service import get_runtime_service

app = FastAPI(title="Zubot Local Chat")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ResetRequest(BaseModel):
    session_id: str = "default"


class RestartContextRequest(BaseModel):
    session_id: str = "default"
    history_limit: int | None = None


class InitRequest(BaseModel):
    session_id: str = "default"


class SessionContextRequest(BaseModel):
    session_id: str = "default"


class TriggerTaskProfileRequest(BaseModel):
    description: str | None = None


class EnqueueAgenticTaskRequest(BaseModel):
    task_name: str = "Background Research Task"
    instructions: str
    requested_by: str = "main_agent"
    model_tier: Literal["low", "medium", "high"] = "medium"
    tool_access: list[str] = Field(default_factory=list)
    skill_access: list[str] = Field(default_factory=list)
    timeout_sec: int = 180
    metadata: dict[str, object] = Field(default_factory=dict)


class KillTaskRunRequest(BaseModel):
    requested_by: str = "main_agent"


class ResumeTaskRunRequest(BaseModel):
    user_response: str
    requested_by: str = "main_agent"


class CentralSqlRequest(BaseModel):
    sql: str
    params: list[object] | dict[str, object] | None = None
    read_only: bool = True
    timeout_sec: float = 5.0
    max_rows: int | None = None


class TaskStateUpsertRequest(BaseModel):
    task_id: str
    state_key: str
    value: dict[str, object] = Field(default_factory=dict)
    updated_by: str = "task_runtime"


class TaskStateGetRequest(BaseModel):
    task_id: str
    state_key: str


class TaskSeenMarkRequest(BaseModel):
    task_id: str
    provider: str
    item_key: str
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskSeenHasRequest(BaseModel):
    task_id: str
    provider: str
    item_key: str


class ScheduleUpsertRequest(BaseModel):
    schedule_id: str | None = None
    task_id: str
    enabled: bool = True
    mode: Literal["frequency", "calendar"] = "frequency"
    execution_order: int = 100
    misfire_policy: Literal["queue_all", "queue_latest", "skip"] = "queue_latest"
    run_frequency_minutes: int | None = None
    timezone: str | None = "America/New_York"
    run_times: list[str] = Field(default_factory=list)
    days_of_week: list[str] = Field(default_factory=list)


class TaskProfileUpsertRequest(BaseModel):
    task_id: str
    name: str | None = None
    kind: Literal["script", "agentic", "interactive_wrapper"] = "script"
    entrypoint_path: str | None = None
    module: str | None = None
    resources_path: str | None = None
    queue_group: str | None = None
    timeout_sec: int | None = None
    retry_policy: dict[str, object] = Field(default_factory=dict)
    enabled: bool = True


class ControlIngestRequest(BaseModel):
    session_id: str = "default"
    assistant_text: str
    route: str = "llm.main_agent"


class ControlApproveRequest(BaseModel):
    action_id: str
    approved_by: str = "user"


class ControlDenyRequest(BaseModel):
    action_id: str
    denied_by: str = "user"
    reason: str | None = None


_CONTROL_ACTIONS: dict[str, dict[str, Any]] = {}
_CONTROL_ACTIONS_LOCK = RLock()


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _control_execute(action: dict[str, Any], *, actor: str = "user") -> dict[str, Any]:
    runtime = get_runtime_service()
    action_name = str(action.get("action") or "").strip()
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}

    if action_name == "enqueue_task":
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "payload.task_id is required for enqueue_task"}
        description_raw = payload.get("description")
        description = str(description_raw).strip() if isinstance(description_raw, str) and description_raw.strip() else None
        out = runtime.central_trigger_profile(profile_id=task_id, description=description)
        return {"ok": bool(out.get("ok")), "result": out}

    if action_name == "enqueue_agentic_task":
        instructions = str(payload.get("instructions") or "").strip()
        if not instructions:
            return {"ok": False, "error": "payload.instructions is required for enqueue_agentic_task"}
        out = runtime.central_enqueue_agentic_task(
            task_name=str(payload.get("task_name") or "Background Research Task").strip() or "Background Research Task",
            instructions=instructions,
            requested_by=str(payload.get("requested_by") or f"approval:{actor}").strip() or f"approval:{actor}",
            model_tier=str(payload.get("model_tier") or "medium").strip() or "medium",
            tool_access=payload.get("tool_access") if isinstance(payload.get("tool_access"), list) else [],
            skill_access=payload.get("skill_access") if isinstance(payload.get("skill_access"), list) else [],
            timeout_sec=int(payload.get("timeout_sec")) if isinstance(payload.get("timeout_sec"), int) else 180,
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
        return {"ok": bool(out.get("ok")), "result": out}

    if action_name == "kill_task_run":
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return {"ok": False, "error": "payload.run_id is required for kill_task_run"}
        out = runtime.central_kill_run(run_id=run_id, requested_by=f"approval:{actor}")
        return {"ok": bool(out.get("ok")), "result": out}

    if action_name == "query_central_db":
        sql = str(payload.get("sql") or "").strip()
        if not sql:
            return {"ok": False, "error": "payload.sql is required for query_central_db"}
        out = runtime.central_execute_sql(
            sql=sql,
            params=payload.get("params") if isinstance(payload.get("params"), (list, dict)) else None,
            read_only=bool(payload.get("read_only", True)),
            timeout_sec=float(payload.get("timeout_sec")) if isinstance(payload.get("timeout_sec"), (int, float)) else 5.0,
            max_rows=int(payload.get("max_rows")) if isinstance(payload.get("max_rows"), int) else None,
        )
        return {"ok": bool(out.get("ok")), "result": out}

    return {"ok": False, "error": f"Unsupported action: {action_name}"}

@app.on_event("startup")
def _init_runtime_client() -> None:
    # App is a client surface; central runtime ownership belongs to daemon/runtime service.
    get_runtime_service().start(start_central_if_enabled=False, source="app")


@app.get("/health")
def health() -> dict:
    return get_runtime_service().health()


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    return get_runtime_service().chat(message=req.message, allow_llm_fallback=True, session_id=req.session_id)


@app.post("/api/session/reset")
def reset_session(req: ResetRequest) -> dict:
    return get_runtime_service().reset_session(session_id=req.session_id)


@app.post("/api/session/restart_context")
def restart_context(req: RestartContextRequest) -> dict:
    return get_runtime_service().restart_session_context(
        session_id=req.session_id,
        history_limit=req.history_limit,
    )


@app.post("/api/session/init")
def init_session(req: InitRequest) -> dict:
    return get_runtime_service().init_session(session_id=req.session_id)


@app.post("/api/session/context")
def session_context(req: SessionContextRequest) -> dict:
    return get_runtime_service().session_context_snapshot(session_id=req.session_id)


@app.get("/api/session/history")
def session_history(session_id: str = "default", limit: int = 100) -> dict:
    safe_limit = max(1, min(500, int(limit)))
    return get_runtime_service().session_history(session_id=session_id, limit=safe_limit)


@app.post("/api/session/history/clear")
def clear_session_history(req: ResetRequest) -> dict:
    return get_runtime_service().clear_session_history(session_id=req.session_id)


@app.get("/api/central/status")
def central_status() -> dict:
    return get_runtime_service().central_status()


@app.post("/api/central/start")
def central_start() -> dict:
    return get_runtime_service().central_start()


@app.post("/api/central/stop")
def central_stop() -> dict:
    return get_runtime_service().central_stop()


@app.get("/api/central/schedules")
def central_schedules() -> dict:
    return get_runtime_service().central_schedules()


@app.get("/api/central/runs")
def central_runs(limit: int = 50) -> dict:
    return get_runtime_service().central_runs(limit=limit)


@app.get("/api/central/runs/waiting")
def central_waiting_runs(limit: int = 50) -> dict:
    return get_runtime_service().central_waiting_runs(limit=limit)


@app.get("/api/central/metrics")
def central_metrics() -> dict:
    return get_runtime_service().central_metrics()


@app.get("/api/central/tasks")
def central_tasks() -> dict:
    return get_runtime_service().central_list_defined_tasks()


@app.post("/api/central/tasks")
def central_upsert_task_profile(req: TaskProfileUpsertRequest) -> dict:
    return get_runtime_service().central_upsert_task_profile(
        task_id=req.task_id,
        name=req.name,
        kind=req.kind,
        entrypoint_path=req.entrypoint_path,
        module=req.module,
        resources_path=req.resources_path,
        queue_group=req.queue_group,
        timeout_sec=req.timeout_sec,
        retry_policy=req.retry_policy,
        enabled=req.enabled,
        source="ui",
    )


@app.delete("/api/central/tasks/{task_id}")
def central_delete_task_profile(task_id: str) -> dict:
    return get_runtime_service().central_delete_task_profile(task_id=task_id)


@app.post("/api/central/schedules")
def central_upsert_schedule(req: ScheduleUpsertRequest) -> dict:
    return get_runtime_service().central_upsert_schedule(
        schedule_id=req.schedule_id,
        task_id=req.task_id,
        enabled=req.enabled,
        mode=req.mode,
        execution_order=req.execution_order,
        misfire_policy=req.misfire_policy,
        run_frequency_minutes=req.run_frequency_minutes,
        timezone=req.timezone,
        run_times=req.run_times,
        days_of_week=req.days_of_week,
    )


@app.delete("/api/central/schedules/{schedule_id}")
def central_delete_schedule(schedule_id: str) -> dict:
    return get_runtime_service().central_delete_schedule(schedule_id=schedule_id)


@app.post("/api/central/trigger/{task_id}")
def central_trigger_profile(task_id: str, req: TriggerTaskProfileRequest | None = None) -> dict:
    description = req.description if isinstance(req, TriggerTaskProfileRequest) else None
    return get_runtime_service().central_trigger_profile(profile_id=task_id, description=description)


@app.post("/api/central/agentic/enqueue")
def central_enqueue_agentic_task(req: EnqueueAgenticTaskRequest) -> dict:
    return get_runtime_service().central_enqueue_agentic_task(
        task_name=req.task_name,
        instructions=req.instructions,
        requested_by=req.requested_by,
        model_tier=req.model_tier,
        tool_access=req.tool_access,
        skill_access=req.skill_access,
        timeout_sec=req.timeout_sec,
        metadata=req.metadata,
    )


@app.post("/api/central/runs/{run_id}/kill")
def central_kill_run(run_id: str, req: KillTaskRunRequest | None = None) -> dict:
    requested_by = req.requested_by if isinstance(req, KillTaskRunRequest) else "main_agent"
    return get_runtime_service().central_kill_run(run_id=run_id, requested_by=requested_by)


@app.post("/api/central/runs/{run_id}/resume")
def central_resume_run(run_id: str, req: ResumeTaskRunRequest) -> dict:
    return get_runtime_service().central_resume_run(
        run_id=run_id,
        user_response=req.user_response,
        requested_by=req.requested_by,
    )


@app.post("/api/central/sql")
def central_execute_sql(req: CentralSqlRequest) -> dict:
    return get_runtime_service().central_execute_sql(
        sql=req.sql,
        params=req.params,
        read_only=req.read_only,
        timeout_sec=req.timeout_sec,
        max_rows=req.max_rows,
    )


@app.post("/api/central/task-state/upsert")
def central_upsert_task_state(req: TaskStateUpsertRequest) -> dict:
    return get_runtime_service().central_upsert_task_state(
        task_id=req.task_id,
        state_key=req.state_key,
        value=req.value,
        updated_by=req.updated_by,
    )


@app.post("/api/central/task-state/get")
def central_get_task_state(req: TaskStateGetRequest) -> dict:
    return get_runtime_service().central_get_task_state(task_id=req.task_id, state_key=req.state_key)


@app.post("/api/central/task-seen/mark")
def central_mark_task_item_seen(req: TaskSeenMarkRequest) -> dict:
    return get_runtime_service().central_mark_task_item_seen(
        task_id=req.task_id,
        provider=req.provider,
        item_key=req.item_key,
        metadata=req.metadata,
    )


@app.post("/api/central/task-seen/has")
def central_has_task_item_seen(req: TaskSeenHasRequest) -> dict:
    return get_runtime_service().central_has_task_item_seen(
        task_id=req.task_id,
        provider=req.provider,
        item_key=req.item_key,
    )


@app.post("/api/control/ingest")
def control_ingest(req: ControlIngestRequest) -> dict:
    clean_session_id = str(req.session_id or "default").strip() or "default"
    clean_route = str(req.route or "llm.main_agent").strip() or "llm.main_agent"
    parsed = extract_control_requests(req.assistant_text, default_route=clean_route)
    registered: list[dict[str, Any]] = []
    with _CONTROL_ACTIONS_LOCK:
        for request in parsed:
            action_id = str(request.get("action_id") or "").strip()
            if not action_id:
                continue
            current = _CONTROL_ACTIONS.get(action_id)
            now_iso = _utc_now_iso()
            if isinstance(current, dict):
                registered.append(current)
                continue
            row = {
                **request,
                "action_id": action_id,
                "session_id": clean_session_id,
                "status": "pending",
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            _CONTROL_ACTIONS[action_id] = row
            registered.append(row)
    return {"ok": True, "registered": registered, "count": len(registered)}


@app.get("/api/control/pending")
def control_pending(session_id: str | None = None) -> dict:
    clean_session = str(session_id or "").strip()
    rows: list[dict[str, Any]] = []
    with _CONTROL_ACTIONS_LOCK:
        for row in _CONTROL_ACTIONS.values():
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") != "pending":
                continue
            if clean_session and str(row.get("session_id") or "") != clean_session:
                continue
            if is_expired(row.get("expires_at")):
                row["status"] = "expired"
                row["updated_at"] = _utc_now_iso()
                continue
            rows.append(dict(row))
    rows.sort(key=lambda item: str(item.get("created_at") or ""))
    return {"ok": True, "pending": rows, "count": len(rows)}


@app.post("/api/control/approve")
def control_approve(req: ControlApproveRequest) -> dict:
    action_id = str(req.action_id or "").strip()
    if not action_id:
        return {"ok": False, "error": "action_id is required"}
    with _CONTROL_ACTIONS_LOCK:
        row = _CONTROL_ACTIONS.get(action_id)
        if not isinstance(row, dict):
            return {"ok": False, "error": "action_not_found", "action_id": action_id}
        if str(row.get("status") or "") != "pending":
            return {"ok": False, "error": "action_not_pending", "action_id": action_id, "status": row.get("status")}
        if is_expired(row.get("expires_at")):
            row["status"] = "expired"
            row["updated_at"] = _utc_now_iso()
            return {"ok": False, "error": "action_expired", "action_id": action_id}
        row["status"] = "approving"
        row["updated_at"] = _utc_now_iso()
        action = dict(row)

    executed = _control_execute(action, actor=str(req.approved_by or "user").strip() or "user")
    with _CONTROL_ACTIONS_LOCK:
        row = _CONTROL_ACTIONS.get(action_id) or {}
        row["status"] = "approved" if bool(executed.get("ok")) else "failed"
        row["updated_at"] = _utc_now_iso()
        row["decision_by"] = str(req.approved_by or "user").strip() or "user"
        row["execution"] = executed
        _CONTROL_ACTIONS[action_id] = row
        final_row = dict(row)
    return {"ok": bool(executed.get("ok")), "action": final_row, "execution": executed}


@app.post("/api/control/deny")
def control_deny(req: ControlDenyRequest) -> dict:
    action_id = str(req.action_id or "").strip()
    if not action_id:
        return {"ok": False, "error": "action_id is required"}
    with _CONTROL_ACTIONS_LOCK:
        row = _CONTROL_ACTIONS.get(action_id)
        if not isinstance(row, dict):
            return {"ok": False, "error": "action_not_found", "action_id": action_id}
        if str(row.get("status") or "") != "pending":
            return {"ok": False, "error": "action_not_pending", "action_id": action_id, "status": row.get("status")}
        row["status"] = "denied"
        row["updated_at"] = _utc_now_iso()
        row["decision_by"] = str(req.denied_by or "user").strip() or "user"
        row["deny_reason"] = str(req.reason or "").strip() or None
        _CONTROL_ACTIONS[action_id] = row
        final_row = dict(row)
    return {"ok": True, "action": final_row}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zubot Local Chat</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {
      --bg: #f6f1e8;
      --panel: #fffaf2;
      --ink: #1e2a24;
      --muted: #5c6d63;
      --accent: #0e8f73;
      --accent-2: #f59e0b;
      --line: #d8d2c7;
      --user: #d8f3eb;
      --bot: #f0ece5;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Space Grotesk", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(900px 600px at -10% 0%, #d7f8ef 0%, transparent 60%),
        radial-gradient(700px 500px at 100% 100%, #ffe3b2 0%, transparent 50%),
        var(--bg);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-start;
      padding: 20px;
    }

    .app {
      width: min(1100px, 100%);
      height: min(860px, calc(100vh - 40px));
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 14px;
    }

    .app.schedules-mode {
      grid-template-columns: 1fr;
    }

    .app.schedules-mode .side {
      display: none;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 10px 35px rgba(37, 48, 42, 0.08);
    }

    .chat {
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 0;
    }

    .chat-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(120deg, #f3fff9 0%, #fff7eb 100%);
    }

    .global-tabs {
      display: flex;
      gap: 8px;
      width: min(1100px, 100%);
      margin-bottom: 10px;
    }

    .tab-btn {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 6px 10px;
      border-radius: 8px;
      cursor: pointer;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.8rem;
    }

    .tab-btn.active {
      border-color: var(--accent);
      background: #e8faf4;
      color: #0a614e;
    }

    .chat-header h1 {
      margin: 0;
      font-size: 1.05rem;
      letter-spacing: 0.02em;
    }

    .sub {
      margin-top: 4px;
      color: var(--muted);
      font-size: 0.86rem;
    }

    .messages {
      min-height: 0;
      overflow: auto;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .tab-panel {
      min-height: 0;
      display: none;
      height: 100%;
    }

    .tab-panel.active {
      display: grid;
      grid-template-rows: 1fr auto;
    }

    .tab-panel.schedules.active {
      grid-template-rows: auto 1fr;
    }

    .sched-wrap {
      padding: 12px;
      min-height: 0;
      overflow: auto;
      display: grid;
      gap: 10px;
      align-content: start;
    }

    .sched-form {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fffdfa;
      padding: 10px;
      display: grid;
      gap: 8px;
    }

    .sched-status {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.76rem;
      color: var(--muted);
      min-height: 18px;
    }

    .sched-status.error {
      color: #9b3c3c;
    }

    .sched-status.ok {
      color: #0e8f73;
    }

    .sched-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .sched-grid.single {
      grid-template-columns: 1fr;
    }

    .sched-time-entry {
      display: grid;
      grid-template-columns: 1fr auto 1fr 1fr auto;
      gap: 8px;
      align-items: center;
    }

    .sched-time-entry.frequency {
      grid-template-columns: 1fr auto 1fr;
    }

    .time-join {
      text-align: center;
      font-family: "IBM Plex Mono", monospace;
      color: var(--muted);
    }

    .sched-time-rows {
      display: grid;
      gap: 8px;
    }

    .days {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.78rem;
    }

    .day-item {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }

    .sched-list {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      overflow: hidden;
    }

    .sched-head, .sched-row {
      display: grid;
      grid-template-columns: 1.4fr 1fr 0.8fr 0.7fr 0.9fr;
      gap: 8px;
      padding: 8px 10px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.76rem;
      align-items: center;
    }

    .sched-head {
      background: #fcfaf5;
      border-bottom: 1px solid var(--line);
      font-weight: 600;
    }

    .sched-row {
      border-bottom: 1px solid var(--line);
    }

    .sched-row:last-child {
      border-bottom: 0;
    }

    .sched-details {
      border-bottom: 1px solid var(--line);
      background: #fcfaf5;
      padding: 8px 12px 10px 28px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.74rem;
      color: var(--muted);
      display: grid;
      gap: 4px;
    }

    .sched-row-title {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .sched-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .caret-btn {
      width: 18px;
      height: 18px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 0;
      font-size: 0.72rem;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
    }

    .sched-actions {
      display: flex;
      gap: 6px;
      justify-content: flex-end;
    }

    .btn-mini {
      padding: 5px 8px;
      font-size: 0.72rem;
      border-radius: 8px;
      cursor: pointer;
    }

    .msg {
      max-width: 86%;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      line-height: 1.35;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
      hyphens: auto;
      animation: rise .16s ease-out;
    }

    .msg.user {
      margin-left: auto;
      background: var(--user);
    }

    .msg.bot {
      background: var(--bot);
    }

    .msg-time-divider {
      align-self: center;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #f2ede3;
      color: var(--muted);
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.7rem;
      line-height: 1.2;
    }

    @keyframes rise {
      from { transform: translateY(4px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }

    .composer {
      border-top: 1px solid var(--line);
      padding: 12px;
      display: grid;
      gap: 10px;
      background: #fffdfa;
    }

    .row {
      display: flex;
      gap: 8px;
    }

    input, button {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.88rem;
      border-radius: 10px;
      border: 1px solid var(--line);
      padding: 9px 10px;
    }

    #session { width: 170px; }
    #msg { flex: 1; }

    button {
      cursor: pointer;
      background: white;
      color: var(--ink);
      transition: transform .08s ease, background .2s ease;
    }

    button:hover { background: #f7fff9; }
    button:active { transform: translateY(1px); }
    button.primary { border-color: #7ec8b5; background: #e9fff8; }
    button.warn { border-color: #f1c98b; background: #fff3df; }
    button.ghost { background: #fff; }

    .status {
      min-height: 20px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.8rem;
      color: var(--muted);
    }

    .status.busy { color: var(--accent); }

    .side {
      display: grid;
      grid-template-rows: auto auto auto 1fr;
      gap: 12px;
      padding: 12px;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      overflow: hidden;
    }

    .card h3 {
      margin: 0;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 0.9rem;
      letter-spacing: .02em;
      background: #fcfaf5;
    }

    .card .body {
      padding: 10px 12px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.8rem;
      line-height: 1.45;
      color: var(--muted);
      white-space: pre-wrap;
    }

    pre {
      margin: 0;
      height: 100%;
      overflow: auto;
      padding: 10px 12px;
      background: #fff;
      font-size: 0.76rem;
      line-height: 1.35;
      font-family: "IBM Plex Mono", monospace;
    }

    .pill {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      margin-right: 6px;
      margin-bottom: 6px;
      font-size: 0.75rem;
    }

    .worker-meta {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.73rem;
      color: var(--muted);
      word-break: break-word;
    }

    .progress-live {
      display: grid;
      gap: 8px;
      white-space: normal;
    }

    .progress-summary {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.75rem;
      color: var(--muted);
      display: grid;
      gap: 2px;
    }

    .worker-lines {
      display: grid;
      gap: 4px;
      margin-top: 4px;
    }

    .worker-line {
      padding-left: 14px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.73rem;
      color: var(--muted);
      overflow-wrap: anywhere;
    }

    .worker-frac {
      color: #b3261e;
      font-weight: 700;
    }

    .control-item {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px;
      margin-bottom: 8px;
      background: #fffdfa;
      display: grid;
      gap: 6px;
    }

    .control-title {
      font-weight: 600;
      color: var(--ink);
      font-family: "Space Grotesk", sans-serif;
    }

    .control-meta {
      font-size: 0.72rem;
      color: var(--muted);
      word-break: break-word;
    }

    .control-actions {
      display: flex;
      gap: 6px;
    }

    .context-dialog {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0;
      width: min(900px, 94vw);
      max-height: 86vh;
      box-shadow: 0 14px 40px rgba(37, 48, 42, 0.15);
    }

    .context-dialog::backdrop {
      background: rgba(23, 28, 24, 0.3);
    }

    .context-head {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      background: #fcfaf5;
    }

    .context-head-title {
      font-family: "Space Grotesk", sans-serif;
      font-size: 0.92rem;
      font-weight: 600;
    }

    .context-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .context-body {
      padding: 10px 12px 14px;
      overflow: auto;
      max-height: calc(86vh - 60px);
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.76rem;
      line-height: 1.4;
    }

    .json-node {
      margin-left: 10px;
      border-left: 1px dashed #d9d4ca;
      padding-left: 8px;
    }

    .json-summary {
      cursor: pointer;
      list-style: none;
      display: inline-block;
    }

    .json-summary::-webkit-details-marker {
      display: none;
    }

    .json-summary::before {
      content: '>';
      margin-right: 6px;
      color: var(--muted);
    }

    details[open] > .json-summary::before {
      content: 'v';
    }

    .json-key {
      color: #195f4e;
    }

    .json-type {
      color: #7a7368;
      margin-left: 6px;
    }

    .json-atom {
      color: #2f3f36;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .context-muted {
      color: var(--muted);
    }

    @media (max-width: 900px) {
      .app {
        grid-template-columns: 1fr;
        height: auto;
      }
      .panel { min-height: 440px; }
    }
  </style>
</head>
<body>
  <div class="global-tabs">
    <button id="tab-chat" class="tab-btn active" onclick="switchTab('chat')">Chat</button>
    <button id="tab-schedules" class="tab-btn" onclick="switchTab('schedules')">Scheduled Tasks</button>
  </div>
  <div id="app-root" class="app">
    <section class="panel chat">
      <div class="chat-header">
        <h1>Zubot Local Chat</h1>
        <div class="sub">Session-based chat with context + daily memory refresh</div>
      </div>
      <div id="panel-chat" class="tab-panel active">
        <div id="messages" class="messages">
          <div class="msg bot">Try: "what time is it?", "weather tomorrow", or "sunrise today".</div>
        </div>
        <div class="composer">
          <div class="row">
            <input id="session" placeholder="Session ID" value="default" />
            <input id="msg" placeholder="Ask Zubot..." />
          </div>
          <div class="row">
            <button class="primary" onclick="sendMsg()">Send</button>
            <button class="warn" onclick="resetContext()">Reset Context</button>
            <button onclick="normalContext()">Normal Context</button>
          </div>
          <div id="status" class="status"></div>
        </div>
      </div>
      <div id="panel-schedules" class="tab-panel schedules">
        <div class="sched-wrap">
          <div class="sched-form">
            <h4 style="margin-top:0;">Task Registry</h4>
            <div class="sched-grid">
              <input id="task-task-id" placeholder="Task ID (example: indeed_daily_search)" />
              <input id="task-name" placeholder="Task Name" />
            </div>
            <div class="sched-grid">
              <select id="task-kind">
                <option value="script">script</option>
                <option value="agentic">agentic</option>
                <option value="interactive_wrapper">interactive_wrapper</option>
              </select>
              <input id="task-timeout" type="number" min="1" step="1" inputmode="numeric" placeholder="Timeout Seconds" />
            </div>
            <div class="sched-grid">
              <input id="task-entrypoint" placeholder="Entrypoint Path (for script kind)" />
              <input id="task-resources" placeholder="Resources Path (optional)" />
            </div>
            <div class="row">
              <button class="primary" onclick="saveTaskProfile()">Save Task</button>
              <button class="warn" onclick="deleteTaskProfileFromForm()">Delete Task</button>
            </div>
            <div id="task-form-status" class="sched-status"></div>
            <div id="tasks-list" class="worker-meta"></div>
          </div>

          <div class="sched-form">
            <div class="sched-grid">
              <input id="sched-name" placeholder="Schedule Name" />
              <select id="sched-task-id"></select>
            </div>
            <div class="sched-grid">
              <select id="sched-mode" onchange="onScheduleModeChange()">
                <option value="frequency">frequency</option>
                <option value="calendar">calendar</option>
              </select>
              <select id="sched-misfire">
                <option value="queue_latest">misfire: queue_latest</option>
                <option value="queue_all">misfire: queue_all</option>
                <option value="skip">misfire: skip</option>
              </select>
            </div>
            <div class="row">
              <label class="day-item"><input id="sched-enabled" type="checkbox" checked /> enabled</label>
            </div>
            <div class="sched-time-entry frequency" id="sched-frequency-row">
              <input id="sched-frequency-hours" type="number" min="0" step="1" inputmode="numeric" placeholder="Hours" />
              <span class="time-join">:</span>
              <input id="sched-frequency-minutes" type="number" min="0" max="59" step="1" inputmode="numeric" placeholder="Minutes" />
            </div>
            <div id="sched-calendar-fields" style="display:none;">
              <div class="sched-grid">
                <select id="sched-timezone">
                  <option value="America/New_York">America/New_York</option>
                </select>
                <div></div>
              </div>
              <div id="sched-calendar-time-rows" class="sched-time-rows"></div>
              <div class="row">
                <button onclick="addCalendarRunTimeRow()">Add Another Time</button>
              </div>
              <div class="days">
                <label class="day-item"><input type="checkbox" value="mon" class="sched-day" />mon</label>
                <label class="day-item"><input type="checkbox" value="tue" class="sched-day" />tue</label>
                <label class="day-item"><input type="checkbox" value="wed" class="sched-day" />wed</label>
                <label class="day-item"><input type="checkbox" value="thu" class="sched-day" />thu</label>
                <label class="day-item"><input type="checkbox" value="fri" class="sched-day" />fri</label>
                <label class="day-item"><input type="checkbox" value="sat" class="sched-day" />sat</label>
                <label class="day-item"><input type="checkbox" value="sun" class="sched-day" />sun</label>
              </div>
            </div>
            <div class="row">
              <button class="primary" onclick="saveSchedule()">Save Schedule</button>
              <button onclick="clearScheduleForm()">Clear</button>
            </div>
            <div id="sched-form-status" class="sched-status"></div>
            <div class="worker-meta">Switching mode from calendar to frequency will clear calendar rows on save.</div>
          </div>
          <div class="sched-list">
            <div class="sched-head">
              <div>name</div>
              <div>config item</div>
              <div>mode</div>
              <div>enabled</div>
              <div>actions</div>
            </div>
            <div id="schedules-list"></div>
          </div>
        </div>
      </div>
    </section>

    <aside class="panel side">
      <div class="card">
        <h3>Runtime</h3>
        <div class="body">
          <span id="route-pill" class="pill">route: -</span>
          <span id="session-pill" class="pill">session: default</span>
          <span id="msgcount-pill" class="pill">assembled: -</span>
        </div>
      </div>
      <div class="card">
        <h3>Progress</h3>
        <div id="progress" class="body">Idle</div>
      </div>
      <div class="card">
        <h3>Approvals</h3>
        <div id="control-approvals" class="body">No pending approvals.</div>
      </div>
      <div class="card">
        <h3>Task Slots</h3>
        <div id="central-status" class="body">Loading central status...</div>
      </div>
      <div class="card" style="min-height: 0;">
        <h3>Last Response</h3>
        <pre id="last-response">{
  "route": "-",
  "tool_calls": [],
  "reply": ""
}</pre>
      </div>
    </aside>
  </div>

  <dialog id="context-dialog" class="context-dialog">
    <div class="context-head">
      <div class="context-head-title">Session Context Snapshot</div>
      <div class="context-actions">
        <button class="ghost" id="context-download-btn">Download</button>
        <button class="warn" id="context-close-btn">Close</button>
      </div>
    </div>
    <div id="context-body" class="context-body">
      <div class="context-muted">No snapshot loaded yet.</div>
    </div>
  </dialog>

  <script>
    // Compatibility fallback: activates only if the richer UI script failed to initialize.
    (function () {
      function el(id) { return document.getElementById(id); }
      var DEFAULT_SESSION_HISTORY_LIMIT = 100;
      function appendMessage(role, text) {
        var messages = el('messages');
        if (!messages) return;
        var div = document.createElement('div');
        div.className = 'msg ' + role;
        div.textContent = text || '';
        messages.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
      }
      function postJson(url, payload, onDone) {
        var xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.onreadystatechange = function () {
          if (xhr.readyState !== 4) return;
          var body = {};
          try { body = JSON.parse(xhr.responseText || '{}'); } catch (_e) {}
          onDone(xhr.status, body);
        };
        xhr.send(JSON.stringify(payload || {}));
      }
      function getJson(url, onDone) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.onreadystatechange = function () {
          if (xhr.readyState !== 4) return;
          var body = {};
          try { body = JSON.parse(xhr.responseText || '{}'); } catch (_e) {}
          onDone(xhr.status, body);
        };
        xhr.send();
      }
      function policyHistoryLimit(rawValue) {
        var parsed = parseInt(String(rawValue == null ? '' : rawValue), 10);
        if (!isFinite(parsed) || parsed <= 0) return DEFAULT_SESSION_HISTORY_LIMIT;
        if (parsed > 500) return 500;
        return parsed;
      }
      function loadSessionHistoryFallback(sessionId, limit, onDone) {
        var safeLimit = policyHistoryLimit(limit);
        getJson('/api/session/history?session_id=' + encodeURIComponent(sessionId) + '&limit=' + encodeURIComponent(String(safeLimit)), function (_status, body) {
          var messages = el('messages');
          if (messages) messages.innerHTML = '';
          var entries = body && body.ok && body.entries && body.entries.length ? body.entries : [];
          for (var i = 0; i < entries.length; i += 1) {
            var entry = entries[i] || {};
            var role = entry.role === 'user' ? 'user' : 'bot';
            var text = entry.content ? String(entry.content) : '';
            if (text) appendMessage(role, text);
          }
          onDone(entries.length, body || {});
        });
      }
      function getSessionId() {
        var sessionInput = el('session');
        var sid = sessionInput && sessionInput.value ? String(sessionInput.value).trim() : 'default';
        return sid || 'default';
      }
      function extractToolCalls(data) {
        if (data && data.data && data.data.tool_execution && data.data.tool_execution.length) {
          var out = [];
          for (var i = 0; i < data.data.tool_execution.length; i += 1) {
            var item = data.data.tool_execution[i] || {};
            out.push({
              name: item.name || 'unknown_tool',
              ok: !!item.result_ok,
              error: item.error || null
            });
          }
          return out;
        }
        return [];
      }
      function setBusyStatus(on, text) {
        var statusEl = el('status');
        if (!statusEl) return;
        statusEl.textContent = text || '';
        if (statusEl.classList) {
          if (on) statusEl.classList.add('busy');
          else statusEl.classList.remove('busy');
        }
      }
      function setRuntimeFromResponse(data, sessionId) {
        var routePill = el('route-pill');
        var sessionPill = el('session-pill');
        var msgCountPill = el('msgcount-pill');
        if (routePill) routePill.textContent = 'route: ' + (data && data.route ? data.route : '-');
        if (sessionPill) sessionPill.textContent = 'session: ' + sessionId;
        var assembled = '-';
        if (data && data.data && data.data.context_debug && data.data.context_debug.assembled_message_count != null) {
          assembled = data.data.context_debug.assembled_message_count;
        }
        if (msgCountPill) msgCountPill.textContent = 'assembled: ' + assembled;
      }
      function setLastResponsePanel(data) {
        var panel = el('last-response');
        if (!panel) return;
        var payload = {
          route: data && data.route ? data.route : null,
          tool_calls: extractToolCalls(data),
          reply: data && data.reply ? data.reply : ''
        };
        panel.textContent = JSON.stringify(payload, null, 2);
      }
      function setProgressFromResponse(data) {
        var progressEl = el('progress');
        if (!progressEl) return;
        var route = data && data.route ? data.route : 'unknown route';
        var tools = extractToolCalls(data);
        if (!tools.length) {
          progressEl.textContent = 'Completed (' + route + ')\\nTools: none';
          return;
        }
        var parts = [];
        for (var i = 0; i < tools.length; i += 1) {
          var tool = tools[i] || {};
          var status = typeof tool.ok === 'boolean' ? (tool.ok ? 'ok' : 'error') : 'attempted';
          parts.push((tool.name || 'unknown_tool') + ' (' + status + ')');
        }
        progressEl.textContent = 'Completed (' + route + ')\\nTools: ' + parts.join(' -> ');
      }
      function startProgressTicker() {
        var progressEl = el('progress');
        if (!progressEl) return null;
        var phases = [
          'Thinking...',
          'Checking available tool routes...',
          'Assembling context...',
          'Waiting for model response...'
        ];
        var i = 0;
        progressEl.textContent = phases[0];
        return setInterval(function () {
          i = (i + 1) % phases.length;
          progressEl.textContent = phases[i];
        }, 460);
      }
      function refreshCentralStatusOnly() {
        var centralEl = el('central-status');
        if (centralEl) {
          var xhrC = new XMLHttpRequest();
          xhrC.open('GET', '/api/central/status', true);
          xhrC.onreadystatechange = function () {
            if (xhrC.readyState !== 4) return;
            var body = {};
            try { body = JSON.parse(xhrC.responseText || '{}'); } catch (_e) {}
            if (!body || !body.service || !body.runtime) return;
            centralEl.textContent =
              'service_running=' + (!!body.service.running) + ' enabled_in_config=' + (!!body.service.enabled_in_config) + '\\n' +
              'queued=' + (body.runtime.queued_count != null ? body.runtime.queued_count : 0) +
              ' running=' + (body.runtime.running_count != null ? body.runtime.running_count : 0) +
              ' waiting=' + (body.runtime.waiting_count != null ? body.runtime.waiting_count : 0) +
              ' slots_busy=' + (body.runtime.task_slot_busy_count != null ? body.runtime.task_slot_busy_count : 0) +
              ' slots_free=' + (body.runtime.task_slot_free_count != null ? body.runtime.task_slot_free_count : 0);
          };
          xhrC.send();
        }
      }
      function installFallback() {
        if (window.__zubotFallbackActive) return;
        window.__zubotFallbackActive = true;

        window.switchTab = function (tabName) {
          var appRoot = el('app-root');
          var chat = el('panel-chat');
          var schedules = el('panel-schedules');
          var tabChat = el('tab-chat');
          var tabSchedules = el('tab-schedules');
          var useSchedules = tabName === 'schedules';
          if (chat && chat.classList) chat.classList.toggle('active', !useSchedules);
          if (schedules && schedules.classList) schedules.classList.toggle('active', useSchedules);
          if (tabChat && tabChat.classList) tabChat.classList.toggle('active', !useSchedules);
          if (tabSchedules && tabSchedules.classList) tabSchedules.classList.toggle('active', useSchedules);
          if (appRoot && appRoot.classList) appRoot.classList.toggle('schedules-mode', useSchedules);
          if (useSchedules && typeof window.refreshScheduleManager === 'function') {
            window.refreshScheduleManager();
          }
        };

        window.sendMsg = function () {
          var msgInput = el('msg');
          var sessionInput = el('session');
          if (!msgInput) return;
          var message = (msgInput.value || '').trim();
          var sessionId = sessionInput && sessionInput.value ? String(sessionInput.value).trim() : 'default';
          if (!message) return;
          appendMessage('user', message);
          msgInput.value = '';

          setBusyStatus(true, 'Working...');
          var ticker = startProgressTicker();
          postJson('/api/chat', { message: message, session_id: sessionId }, function (_status, body) {
            appendMessage('bot', body && body.reply ? body.reply : '(No reply)');
            setLastResponsePanel(body || {});
            setRuntimeFromResponse(body || {}, sessionId);
            setProgressFromResponse(body || {});
            refreshCentralStatusOnly();
            if (ticker) clearInterval(ticker);
            setBusyStatus(false, '');
          });
        };
        window.resetContext = function () {
          var sessionId = getSessionId();
          setBusyStatus(true, 'Resetting context...');
          postJson('/api/session/reset', { session_id: sessionId }, function (_status, body) {
            var messages = el('messages');
            if (messages) messages.innerHTML = '';
            setLastResponsePanel({
              route: 'session.reset',
              reply: body && body.note ? body.note : 'Session reset.',
              data: {}
            });
            setRuntimeFromResponse({ route: 'session.reset', data: {} }, sessionId);
            var progressEl = el('progress');
            if (progressEl) progressEl.textContent = 'Session context reset (history preserved).';
            setBusyStatus(false, '');
            refreshCentralStatusOnly();
          });
        };
        window.normalContext = function () {
          var sessionId = getSessionId();
          setBusyStatus(true, 'Loading normal context...');
          postJson('/api/session/init', { session_id: sessionId }, function (_status, body) {
            var preload = body && body.preload ? body.preload : {};
            var historyLimit = policyHistoryLimit(preload.rehydrate_limit);
            loadSessionHistoryFallback(sessionId, historyLimit, function (loadedCount) {
              if ((loadedCount || 0) === 0 && body && body.welcome) {
                appendMessage('bot', body.welcome);
              }
              setLastResponsePanel({
                route: 'session.init',
                reply: body && body.welcome ? body.welcome : 'Session initialized.',
                data: {}
              });
              setRuntimeFromResponse({ route: 'session.init', data: {} }, sessionId);
              var progressEl = el('progress');
              if (progressEl) progressEl.textContent = 'Session initialized (' + sessionId + ').';
              setBusyStatus(false, '');
              refreshCentralStatusOnly();
            });
          });
        };
        window.openContextDialog = function () {
          var sessionId = getSessionId();
          postJson('/api/session/context', { session_id: sessionId }, function (_status, body) {
            var dialogEl = el('context-dialog');
            var bodyEl = el('context-body');
            if (bodyEl) {
              var payload = body && body.ok ? body.snapshot : body;
              bodyEl.innerHTML = '';
              var pre = document.createElement('pre');
              pre.textContent = JSON.stringify(payload || { ok: false, error: 'empty_response' }, null, 2);
              bodyEl.appendChild(pre);
            }
            if (dialogEl && typeof dialogEl.showModal === 'function') {
              dialogEl.showModal();
            }
          });
        };
        var msgInput = el('msg');
        if (msgInput && !msgInput.__zubotFallbackBound) {
          msgInput.__zubotFallbackBound = true;
          msgInput.addEventListener('keydown', function (e) {
            if (e && e.key === 'Enter') window.sendMsg();
          });
        }
        var closeBtn = el('context-close-btn');
        var dialogEl = el('context-dialog');
        if (closeBtn && dialogEl && !closeBtn.__zubotFallbackBound) {
          closeBtn.__zubotFallbackBound = true;
          closeBtn.addEventListener('click', function () {
            if (typeof dialogEl.close === 'function') dialogEl.close();
          });
        }
        window.normalContext();
        refreshCentralStatusOnly();
      }
      function maybeInstallFallback() {
        if (window.__zubotRichUiInitDone) return;
        installFallback();
      }
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
          setTimeout(maybeInstallFallback, 250);
        });
      } else {
        setTimeout(maybeInstallFallback, 250);
      }
    })();
  </script>

  <script>
    const statusEl = document.getElementById('status');
    const progressEl = document.getElementById('progress');
    const messagesEl = document.getElementById('messages');
    const lastResponseEl = document.getElementById('last-response');
    const routePill = document.getElementById('route-pill');
    const sessionPill = document.getElementById('session-pill');
    const msgCountPill = document.getElementById('msgcount-pill');
    const centralStatusEl = document.getElementById('central-status');
    const controlApprovalsEl = document.getElementById('control-approvals');
    const appRoot = document.getElementById('app-root');
    const panelChat = document.getElementById('panel-chat');
    const panelSchedules = document.getElementById('panel-schedules');
    const tabChat = document.getElementById('tab-chat');
    const tabSchedules = document.getElementById('tab-schedules');
    const schedulesListEl = document.getElementById('schedules-list');
    const tasksListEl = document.getElementById('tasks-list');
    const scheduleTaskSelect = document.getElementById('sched-task-id');
    const scheduleModeSelect = document.getElementById('sched-mode');
    const scheduleMisfireSelect = document.getElementById('sched-misfire');
    const scheduleEnabledCheckbox = document.getElementById('sched-enabled');
    const scheduleCalendarFields = document.getElementById('sched-calendar-fields');
    const scheduleFrequencyRow = document.getElementById('sched-frequency-row');
    const scheduleNameInput = document.getElementById('sched-name');
    const scheduleFrequencyHours = document.getElementById('sched-frequency-hours');
    const scheduleFrequencyMinutes = document.getElementById('sched-frequency-minutes');
    const scheduleCalendarRows = document.getElementById('sched-calendar-time-rows');
    const scheduleFormStatus = document.getElementById('sched-form-status');
    const taskFormStatus = document.getElementById('task-form-status');
    const taskIdInput = document.getElementById('task-task-id');
    const taskNameInput = document.getElementById('task-name');
    const taskKindSelect = document.getElementById('task-kind');
    const taskEntrypointInput = document.getElementById('task-entrypoint');
    const taskResourcesInput = document.getElementById('task-resources');
    const taskTimeoutInput = document.getElementById('task-timeout');
    const contextDialogEl = document.getElementById('context-dialog');
    const contextBodyEl = document.getElementById('context-body');
    const contextCloseBtnEl = document.getElementById('context-close-btn');
    const contextDownloadBtnEl = document.getElementById('context-download-btn');
    let latestContextSnapshot = null;
    let currentUiTab = 'chat';
    let cachedTaskProfiles = [];
    let cachedSchedules = [];
    let sessionTimezone = 'UTC';
    let lastRenderedMessageMs = null;
    const MESSAGE_GAP_MS = 2 * 60 * 60 * 1000;
    let scheduleEditingId = null;
    let expandedScheduleIds = new Set();
    const DEFAULT_SESSION_HISTORY_LIMIT = 100;

    function switchTab(tabName) {
      currentUiTab = tabName === 'schedules' ? 'schedules' : 'chat';
      const chatActive = currentUiTab === 'chat';
      panelChat.classList.toggle('active', chatActive);
      panelSchedules.classList.toggle('active', !chatActive);
      tabChat.classList.toggle('active', chatActive);
      tabSchedules.classList.toggle('active', !chatActive);
      if (appRoot && appRoot.classList) {
        appRoot.classList.toggle('schedules-mode', !chatActive);
      }
      if (!chatActive) {
        refreshScheduleManager();
      }
    }

    function onScheduleModeChange() {
      const mode = scheduleModeSelect ? scheduleModeSelect.value : 'frequency';
      const isCalendar = mode === 'calendar';
      if (scheduleCalendarFields) scheduleCalendarFields.style.display = isCalendar ? 'block' : 'none';
      if (scheduleFrequencyRow) scheduleFrequencyRow.style.display = isCalendar ? 'none' : 'grid';
    }

    function selectedScheduleDays() {
      return Array.from(document.querySelectorAll('.sched-day'))
        .filter((el) => el.checked)
        .map((el) => el.value);
    }

    function setScheduleDays(days) {
      const set = new Set(Array.isArray(days) ? days : []);
      Array.from(document.querySelectorAll('.sched-day')).forEach((el) => {
        el.checked = set.has(el.value);
      });
    }

    function setScheduleFormStatus(text, level = 'info') {
      if (!scheduleFormStatus) return;
      scheduleFormStatus.textContent = text || '';
      scheduleFormStatus.classList.remove('error', 'ok');
      if (level === 'error') scheduleFormStatus.classList.add('error');
      if (level === 'ok') scheduleFormStatus.classList.add('ok');
    }

    function setTaskFormStatus(text, level = 'info') {
      if (!taskFormStatus) return;
      taskFormStatus.textContent = text || '';
      taskFormStatus.classList.remove('error', 'ok');
      if (level === 'error') taskFormStatus.classList.add('error');
      if (level === 'ok') taskFormStatus.classList.add('ok');
    }

    function bindNumericOnly(inputEl) {
      if (!inputEl) return;
      inputEl.addEventListener('input', () => {
        const cleaned = String(inputEl.value || '').replace(/[^\\d]/g, '');
        if (inputEl.value !== cleaned) inputEl.value = cleaned;
      });
    }

    function parseIntegerField(rawValue) {
      const text = String(rawValue == null ? '' : rawValue).trim();
      if (!text) return null;
      if (!/^\\d+$/.test(text)) return Number.NaN;
      return Number.parseInt(text, 10);
    }

    function initSchedulePickers() {
      bindNumericOnly(scheduleFrequencyHours);
      bindNumericOnly(scheduleFrequencyMinutes);
    }

    function frequencyMinutesToHHMM(totalMinutes) {
      const value = Number.parseInt(String(totalMinutes || 0), 10);
      if (!Number.isFinite(value) || value <= 0) return '24:00';
      const hours = Math.floor(value / 60);
      const minutes = value % 60;
      return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
    }

    function frequencySelectorToMinutes() {
      const hours = parseIntegerField(scheduleFrequencyHours ? scheduleFrequencyHours.value : '');
      const minutes = parseIntegerField(scheduleFrequencyMinutes ? scheduleFrequencyMinutes.value : '');
      if (hours === null || minutes === null) {
        return { minutes: null, error: 'Frequency requires both hour and minute values.' };
      }
      if (!Number.isFinite(hours) || hours < 0) {
        return { minutes: null, error: 'Frequency hours must be 0 or greater.' };
      }
      if (!Number.isFinite(minutes) || minutes < 0 || minutes > 59) {
        return { minutes: null, error: 'Frequency minutes must be between 0 and 59.' };
      }
      const total = (hours * 60) + minutes;
      if (total <= 0) {
        return { minutes: null, error: 'Frequency must be greater than 00:00.' };
      }
      return { minutes: total, error: null };
    }

    function setFrequencySelectorsFromMinutes(totalMinutes) {
      const safeTotal = Number.isFinite(Number(totalMinutes)) ? Number(totalMinutes) : 1440;
      const hours = Math.floor(safeTotal / 60);
      const minutes = safeTotal % 60;
      if (scheduleFrequencyHours) scheduleFrequencyHours.value = String(Math.max(0, hours));
      if (scheduleFrequencyMinutes) scheduleFrequencyMinutes.value = String(Math.max(0, Math.min(59, minutes)));
    }

    function formatCalendarTime(hhmm) {
      const match = String(hhmm || '').match(/^([01]\\d|2[0-3]):([0-5]\\d)$/);
      if (!match) return hhmm || '';
      let hour = Number.parseInt(match[1], 10);
      const minute = match[2];
      const suffix = hour >= 12 ? 'PM' : 'AM';
      if (hour === 0) hour = 12;
      if (hour > 12) hour -= 12;
      return `${hour}:${minute} ${suffix}`;
    }

    function parseHHMM(hhmm) {
      const match = String(hhmm || '').match(/^([01]\\d|2[0-3]):([0-5]\\d)$/);
      if (!match) return null;
      return {
        hour24: Number.parseInt(match[1], 10),
        minute: Number.parseInt(match[2], 10),
      };
    }

    function hhmmToRowParts(hhmm) {
      const parsed = parseHHMM(hhmm);
      if (!parsed) return { hour12: 9, minute: 0, ampm: 'AM' };
      let hour12 = parsed.hour24;
      const ampm = hour12 >= 12 ? 'PM' : 'AM';
      if (hour12 === 0) hour12 = 12;
      else if (hour12 > 12) hour12 -= 12;
      return { hour12, minute: parsed.minute, ampm };
    }

    function rowPartsToHHMM(hour12, minute, ampm) {
      let h = Number.parseInt(String(hour12), 10);
      const m = Number.parseInt(String(minute), 10);
      const suffix = String(ampm || 'AM').toUpperCase();
      if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
      if (h < 1 || h > 12 || m < 0 || m > 59) return null;
      if (suffix === 'AM') {
        if (h === 12) h = 0;
      } else {
        if (h !== 12) h += 12;
      }
      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    }

    function addCalendarRunTimeRow(initialHHMM = null) {
      if (!scheduleCalendarRows) return;
      const row = document.createElement('div');
      row.className = 'sched-time-entry';
      const initial = hhmmToRowParts(initialHHMM);

      const hourInput = document.createElement('input');
      hourInput.type = 'number';
      hourInput.className = 'sched-time-hour';
      hourInput.inputMode = 'numeric';
      hourInput.step = '1';
      hourInput.min = '1';
      hourInput.max = '12';
      hourInput.placeholder = 'HH';
      hourInput.value = String(initial.hour12);
      bindNumericOnly(hourInput);

      const minuteInput = document.createElement('input');
      minuteInput.type = 'number';
      minuteInput.className = 'sched-time-minute';
      minuteInput.inputMode = 'numeric';
      minuteInput.step = '1';
      minuteInput.min = '0';
      minuteInput.max = '59';
      minuteInput.placeholder = 'MM';
      minuteInput.value = String(initial.minute);
      bindNumericOnly(minuteInput);

      const ampmSel = document.createElement('select');
      ampmSel.className = 'sched-time-ampm';
      ampmSel.innerHTML = '<option value="AM">AM</option><option value="PM">PM</option>';
      ampmSel.value = initial.ampm;

      const join = document.createElement('span');
      join.className = 'time-join';
      join.textContent = ':';

      const removeBtn = document.createElement('button');
      removeBtn.className = 'warn';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', () => {
        row.remove();
      });

      row.appendChild(hourInput);
      row.appendChild(join);
      row.appendChild(minuteInput);
      row.appendChild(ampmSel);
      row.appendChild(removeBtn);
      scheduleCalendarRows.appendChild(row);
    }

    function clearCalendarRunTimeRows() {
      if (!scheduleCalendarRows) return;
      scheduleCalendarRows.innerHTML = '';
    }

    function collectCalendarRunTimes() {
      if (!scheduleCalendarRows) return { times: [], error: 'Calendar time rows are unavailable.' };
      const rows = Array.from(scheduleCalendarRows.querySelectorAll('.sched-time-entry'));
      const out = [];
      for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index];
        const hour = row.querySelector('.sched-time-hour');
        const minute = row.querySelector('.sched-time-minute');
        const ampm = row.querySelector('.sched-time-ampm');
        const hourValue = parseIntegerField(hour && hour.value);
        const minuteValue = parseIntegerField(minute && minute.value);
        if (hourValue === null || minuteValue === null) {
          return { times: [], error: `Calendar row ${index + 1}: hour and minute are required.` };
        }
        if (!Number.isFinite(hourValue) || hourValue < 1 || hourValue > 12) {
          return { times: [], error: `Calendar row ${index + 1}: hour must be between 1 and 12.` };
        }
        if (!Number.isFinite(minuteValue) || minuteValue < 0 || minuteValue > 59) {
          return { times: [], error: `Calendar row ${index + 1}: minute must be between 0 and 59.` };
        }
        const hhmm = rowPartsToHHMM(hourValue, minuteValue, ampm && ampm.value);
        if (!hhmm) {
          return { times: [], error: `Calendar row ${index + 1}: invalid time.` };
        }
        out.push(hhmm);
      }
      return { times: Array.from(new Set(out)).sort(), error: null };
    }

    function normalizeRunTimes(runTimes) {
      if (!Array.isArray(runTimes)) return [];
      const normalized = [];
      runTimes.forEach((row) => {
        const value = typeof row === 'string' ? row : row && row.time_of_day;
        const parsed = parseHHMM(value) ? value : null;
        if (parsed && !normalized.includes(parsed)) normalized.push(parsed);
      });
      normalized.sort();
      return normalized;
    }

    function clearScheduleForm() {
      scheduleEditingId = null;
      if (scheduleTaskSelect && scheduleTaskSelect.options.length > 0) {
        scheduleTaskSelect.selectedIndex = 0;
      }
      if (scheduleNameInput) {
        const selected = scheduleTaskSelect && scheduleTaskSelect.value ? scheduleTaskSelect.value : '';
        scheduleNameInput.value = selected ? `${selected}_schedule` : '';
      }
      if (scheduleModeSelect) scheduleModeSelect.value = 'frequency';
      if (scheduleMisfireSelect) scheduleMisfireSelect.value = 'queue_latest';
      setFrequencySelectorsFromMinutes(1440);
      clearCalendarRunTimeRows();
      addCalendarRunTimeRow('09:00');
      document.getElementById('sched-timezone').value = 'America/New_York';
      if (scheduleEnabledCheckbox) scheduleEnabledCheckbox.checked = true;
      setScheduleDays([]);
      setScheduleFormStatus('');
      onScheduleModeChange();
    }

    function renderTaskProfilesList(tasks) {
      if (!tasksListEl) return;
      if (!Array.isArray(tasks) || !tasks.length) {
        tasksListEl.textContent = 'No task profiles registered yet.';
        return;
      }
      tasksListEl.textContent = `Registered tasks: ${tasks.map((row) => `${row.task_id} [${row.kind || 'script'}]`).join(', ')}`;
    }

    function fillTaskFormFromTask(taskId) {
      const task = cachedTaskProfiles.find((row) => row.task_id === taskId);
      if (!task) return;
      if (taskIdInput) taskIdInput.value = task.task_id || '';
      if (taskNameInput) taskNameInput.value = task.name || '';
      if (taskKindSelect) taskKindSelect.value = task.kind || 'script';
      if (taskEntrypointInput) taskEntrypointInput.value = task.entrypoint_path || '';
      if (taskResourcesInput) taskResourcesInput.value = task.resources_path || '';
      if (taskTimeoutInput) taskTimeoutInput.value = task.timeout_sec ? String(task.timeout_sec) : '';
    }

    async function loadDefinedTasks() {
      const previousValue = scheduleTaskSelect ? scheduleTaskSelect.value : '';
      const res = await fetch('/api/central/tasks');
      const payload = await res.json();
      const ok = !!(payload && payload.ok);
      const tasks = payload && Array.isArray(payload.tasks) ? payload.tasks : [];
      cachedTaskProfiles = tasks;
      renderTaskProfilesList(tasks);
      scheduleTaskSelect.innerHTML = '';
      tasks.forEach((task) => {
        const opt = document.createElement('option');
        opt.value = task.task_id;
        opt.textContent = `${task.task_id}${task.name ? ` (${task.name})` : ''}`;
        scheduleTaskSelect.appendChild(opt);
      });
      if (!tasks.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = ok ? 'No predefined tasks configured' : 'Failed to load predefined tasks';
        scheduleTaskSelect.appendChild(opt);
        setScheduleFormStatus(ok ? 'No configured tasks found in config.' : 'Failed to load configured tasks.', ok ? 'info' : 'error');
      } else if (previousValue && tasks.some((task) => task.task_id === previousValue)) {
        scheduleTaskSelect.value = previousValue;
        setScheduleFormStatus('');
      } else {
        if (scheduleTaskSelect) scheduleTaskSelect.selectedIndex = 0;
        setScheduleFormStatus('');
      }
      if (!scheduleEditingId && scheduleNameInput) {
        const selected = scheduleTaskSelect && scheduleTaskSelect.value ? scheduleTaskSelect.value : '';
        scheduleNameInput.value = selected ? `${selected}_schedule` : '';
      }
      if (taskIdInput && !taskIdInput.value && tasks.length) {
        fillTaskFormFromTask(tasks[0].task_id);
      }
    }

    async function saveTaskProfile() {
      const taskId = taskIdInput ? String(taskIdInput.value || '').trim() : '';
      const name = taskNameInput ? String(taskNameInput.value || '').trim() : '';
      const kind = taskKindSelect ? String(taskKindSelect.value || 'script').trim() : 'script';
      const entrypointPath = taskEntrypointInput ? String(taskEntrypointInput.value || '').trim() : '';
      const resourcesPath = taskResourcesInput ? String(taskResourcesInput.value || '').trim() : '';
      const timeoutRaw = taskTimeoutInput ? String(taskTimeoutInput.value || '').trim() : '';
      const timeoutSec = timeoutRaw ? Number.parseInt(timeoutRaw, 10) : null;

      if (!taskId) {
        setTaskFormStatus('Task ID is required.', 'error');
        return;
      }
      if (kind === 'script' && !entrypointPath) {
        setTaskFormStatus('Entrypoint path is required for script tasks.', 'error');
        return;
      }
      if (timeoutSec !== null && (!Number.isFinite(timeoutSec) || timeoutSec <= 0)) {
        setTaskFormStatus('Timeout must be a positive integer.', 'error');
        return;
      }

      const body = {
        task_id: taskId,
        name: name || taskId,
        kind,
        entrypoint_path: entrypointPath || null,
        resources_path: resourcesPath || null,
        timeout_sec: timeoutSec,
        retry_policy: {},
        enabled: true,
      };
      setBusyStatus(true, 'Saving task profile...');
      try {
        const res = await fetch('/api/central/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const payload = await res.json();
        if (payload && payload.ok) {
          await refreshScheduleManager();
          if (scheduleTaskSelect) scheduleTaskSelect.value = taskId;
          fillTaskFormFromTask(taskId);
          setTaskFormStatus(`Saved ${taskId}.`, 'ok');
        } else {
          setTaskFormStatus(payload && payload.error ? payload.error : 'Failed to save task profile.', 'error');
        }
      } catch (_err) {
        setTaskFormStatus('Failed to save task profile.', 'error');
      } finally {
        setBusyStatus(false, '');
      }
    }

    async function deleteTaskProfileFromForm() {
      const taskId = taskIdInput ? String(taskIdInput.value || '').trim() : '';
      if (!taskId) {
        setTaskFormStatus('Task ID is required for delete.', 'error');
        return;
      }
      if (!window.confirm(`Delete task profile ${taskId}?`)) return;
      setBusyStatus(true, `Deleting ${taskId}...`);
      try {
        const res = await fetch(`/api/central/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
        const payload = await res.json();
        if (payload && payload.ok) {
          if (taskIdInput) taskIdInput.value = '';
          if (taskNameInput) taskNameInput.value = '';
          if (taskEntrypointInput) taskEntrypointInput.value = '';
          if (taskResourcesInput) taskResourcesInput.value = '';
          if (taskTimeoutInput) taskTimeoutInput.value = '';
          await refreshScheduleManager();
          setTaskFormStatus(`Deleted ${taskId}.`, 'ok');
        } else {
          setTaskFormStatus(payload && payload.error ? payload.error : `Failed to delete ${taskId}.`, 'error');
        }
      } catch (_err) {
        setTaskFormStatus(`Failed to delete ${taskId}.`, 'error');
      } finally {
        setBusyStatus(false, '');
      }
    }

    function editSchedule(scheduleId) {
      const item = cachedSchedules.find((row) => row.schedule_id === scheduleId);
      if (!item) return;
      scheduleEditingId = item.schedule_id || null;
      if (scheduleNameInput) scheduleNameInput.value = item.schedule_id || '';
      if (scheduleTaskSelect) scheduleTaskSelect.value = item.task_id || item.profile_id || '';
      if (scheduleModeSelect) scheduleModeSelect.value = item.mode || 'frequency';
      if (scheduleMisfireSelect) scheduleMisfireSelect.value = item.misfire_policy || 'queue_latest';
      setFrequencySelectorsFromMinutes(item.run_frequency_minutes);
      if (scheduleEnabledCheckbox) scheduleEnabledCheckbox.checked = !!item.enabled;
      document.getElementById('sched-timezone').value = item.timezone || 'America/New_York';
      clearCalendarRunTimeRows();
      const runTimes = normalizeRunTimes(item.run_times);
      if (runTimes.length) {
        runTimes.forEach((time) => addCalendarRunTimeRow(time));
      } else {
        addCalendarRunTimeRow('09:00');
      }
      setScheduleDays(item.days_of_week || []);
      setScheduleFormStatus(`Editing ${scheduleEditingId}`, 'ok');
      onScheduleModeChange();
    }

    async function deleteSchedule(scheduleId) {
      if (!window.confirm(`Delete schedule ${scheduleId}?`)) return;
      setBusyStatus(true, `Deleting ${scheduleId}...`);
      try {
        const res = await fetch(`/api/central/schedules/${encodeURIComponent(scheduleId)}`, { method: 'DELETE' });
        const body = await res.json();
        if (!body || !body.ok) {
          setScheduleFormStatus(body && body.error ? body.error : `Failed to delete ${scheduleId}.`, 'error');
        } else {
          setScheduleFormStatus(`Deleted ${scheduleId}.`, 'ok');
        }
      } catch (_err) {
        setScheduleFormStatus(`Failed to delete ${scheduleId}.`, 'error');
      } finally {
        setBusyStatus(false, '');
        await refreshScheduleManager();
      }
    }

    function renderSchedulesList(payload) {
      const schedules = payload && Array.isArray(payload.schedules) ? payload.schedules : [];
      cachedSchedules = schedules;
      if (!schedules.length) {
        schedulesListEl.innerHTML = '<div class="sched-row"><div>(none)</div><div>-</div><div>-</div><div>-</div><div>-</div></div>';
        return;
      }
      schedulesListEl.innerHTML = schedules.map((row) => {
        const id = row.schedule_id || '';
        const taskId = row.task_id || row.profile_id || '';
        const expanded = expandedScheduleIds.has(id);
        const runTimes = normalizeRunTimes(row.run_times);
        const dayList = Array.isArray(row.days_of_week) ? row.days_of_week.join(', ') : '-';
        const frequencyLabel = frequencyMinutesToHHMM(row.run_frequency_minutes);
        const policyLine = `<div>misfire policy: ${row.misfire_policy || 'queue_latest'}</div>`;
        const cursorLines = `<div>next run: ${row.next_run_at || '-'}</div><div>last planned: ${row.last_planned_run_at || '-'}</div>`;
        const detailLines = row.mode === 'frequency'
          ? `<div>frequency: ${frequencyLabel}</div>${policyLine}${cursorLines}`
          : `${runTimes.map((time) => `<div>time: ${formatCalendarTime(time)}</div>`).join('')}<div>days: ${dayList || '-'}</div><div>timezone: ${row.timezone || 'America/New_York'}</div>${policyLine}${cursorLines}`;
        return `
          <div class="sched-row">
            <div class="sched-row-title">
              <button class="caret-btn" data-toggle-schedule="${id}" title="Show details">${expanded ? 'v' : '>'}</button>
              <span class="sched-name" title="${id}">${id}</span>
            </div>
            <div title="${taskId}">${taskId}</div>
            <div>${row.mode || '-'}</div>
            <div>${row.enabled ? 'yes' : 'no'}</div>
            <div class="sched-actions">
              <button class="btn-mini warn" data-delete-schedule="${id}">Delete</button>
            </div>
          </div>
          ${expanded ? `<div class="sched-details">${detailLines || '<div>(no details)</div>'}</div>` : ''}
        `;
      }).join('');

      schedulesListEl.querySelectorAll('[data-toggle-schedule]').forEach((btn) => {
        btn.addEventListener('click', (evt) => {
          const scheduleId = evt.currentTarget.getAttribute('data-toggle-schedule');
          if (!scheduleId) return;
          if (expandedScheduleIds.has(scheduleId)) expandedScheduleIds.delete(scheduleId);
          else expandedScheduleIds.add(scheduleId);
          renderSchedulesList({ schedules: cachedSchedules });
        });
      });
      schedulesListEl.querySelectorAll('[data-delete-schedule]').forEach((btn) => {
        btn.addEventListener('click', (evt) => {
          const scheduleId = evt.currentTarget.getAttribute('data-delete-schedule');
          if (scheduleId) deleteSchedule(scheduleId);
        });
      });
    }

    async function refreshScheduleManager() {
      try {
        await loadDefinedTasks();
        const res = await fetch('/api/central/schedules');
        const payload = await res.json();
        renderSchedulesList(payload);
      } catch (_err) {
        schedulesListEl.innerHTML = '<div class="sched-row"><div>Failed to load schedules.</div><div>-</div><div>-</div><div>-</div><div>-</div></div>';
      }
    }

    function toScheduleId(name, taskId) {
      const text = String(name || '').trim().toLowerCase();
      if (!text) return null;
      const slug = text.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40);
      const taskSlug = String(taskId || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 20);
      if (!slug) return null;
      return `${taskSlug || 'task'}_${slug}`;
    }

    async function saveSchedule() {
      const mode = scheduleModeSelect.value;
      const scheduleName = scheduleNameInput ? scheduleNameInput.value : '';
      const taskId = scheduleTaskSelect ? scheduleTaskSelect.value : '';
      const frequencyValidation = frequencySelectorToMinutes();
      const calendarValidation = collectCalendarRunTimes();
      const selectedDays = selectedScheduleDays();
      const scheduleId = scheduleEditingId || toScheduleId(scheduleName, taskId);

      if (!scheduleId) {
        setScheduleFormStatus('Schedule name is required.', 'error');
        return;
      }
      if (!taskId) {
        setScheduleFormStatus('Choose a config item before saving.', 'error');
        return;
      }
      if (mode === 'frequency' && frequencyValidation.error) {
        setScheduleFormStatus(frequencyValidation.error, 'error');
        return;
      }
      if (mode === 'calendar' && calendarValidation.error) {
        setScheduleFormStatus(calendarValidation.error, 'error');
        return;
      }
      if (mode === 'calendar' && calendarValidation.times.length === 0) {
        setScheduleFormStatus('Add at least one calendar run time.', 'error');
        return;
      }
      if (mode === 'calendar' && selectedDays.length === 0) {
        setScheduleFormStatus('Select at least one day for calendar mode.', 'error');
        return;
      }

      const body = {
        schedule_id: scheduleId,
        task_id: taskId,
        enabled: !!scheduleEnabledCheckbox.checked,
        mode,
        misfire_policy: scheduleMisfireSelect ? String(scheduleMisfireSelect.value || 'queue_latest') : 'queue_latest',
        execution_order: 100,
        run_frequency_minutes: mode === 'frequency' ? frequencyValidation.minutes : null,
        timezone: 'America/New_York',
        run_times: mode === 'calendar' ? calendarValidation.times : [],
        days_of_week: mode === 'calendar' ? selectedDays : [],
      };

      setBusyStatus(true, 'Saving schedule...');
      try {
        const res = await fetch('/api/central/schedules', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const payload = await res.json();
        if (payload && payload.ok) {
          clearScheduleForm();
          await refreshScheduleManager();
          setScheduleFormStatus(`Saved ${payload.schedule_id || scheduleId}.`, 'ok');
        } else {
          setScheduleFormStatus(payload && payload.error ? payload.error : 'Failed to save schedule.', 'error');
        }
      } catch (_err) {
        setScheduleFormStatus('Failed to save schedule.', 'error');
      } finally {
        setBusyStatus(false, '');
      }
    }

    function parseMessageTimeMs(createdAt) {
      const value = String(createdAt || '').trim();
      if (!value) return null;
      const ms = Date.parse(value);
      return Number.isFinite(ms) ? ms : null;
    }

    function dateKeyForMs(ms) {
      try {
        return new Intl.DateTimeFormat('en-CA', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          timeZone: sessionTimezone || 'UTC',
        }).format(new Date(ms));
      } catch (_err) {
        return new Date(ms).toISOString().slice(0, 10);
      }
    }

    function formatMessageTimeLabel(createdAt, previousMs = null) {
      const ms = parseMessageTimeMs(createdAt);
      if (ms === null) return null;
      const date = new Date(ms);
      let timeLabel = null;
      try {
        timeLabel = new Intl.DateTimeFormat('en-US', {
          hour: '2-digit',
          minute: '2-digit',
          hour12: true,
          timeZone: sessionTimezone || 'UTC',
        }).format(date);
      } catch (_err) {
        timeLabel = new Intl.DateTimeFormat('en-US', {
          hour: '2-digit',
          minute: '2-digit',
          hour12: true,
        }).format(date);
      }
      const buildDateLabel = () => {
        try {
          return new Intl.DateTimeFormat('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            timeZone: sessionTimezone || 'UTC',
          }).format(date);
        } catch (_err) {
          return dateKeyForMs(ms);
        }
      };

      if (previousMs === null) {
        return `${buildDateLabel()} ${timeLabel}`;
      }

      const prevKey = dateKeyForMs(previousMs);
      const currKey = dateKeyForMs(ms);
      if (prevKey === currKey) return timeLabel;

      return `${buildDateLabel()} ${timeLabel}`;
    }

    function appendMessage(role, text, meta = {}) {
      const createdAt = (meta && meta.created_at) ? String(meta.created_at) : new Date().toISOString();
      const safeText = text == null ? '' : String(text);
      const createdMs = parseMessageTimeMs(createdAt);
      if (createdMs !== null) {
        const isFirstVisibleMessage = lastRenderedMessageMs === null;
        const hasLargeGap = !isFirstVisibleMessage && (createdMs - lastRenderedMessageMs) >= MESSAGE_GAP_MS;
        if (isFirstVisibleMessage || hasLargeGap) {
          const stamp = formatMessageTimeLabel(createdAt, lastRenderedMessageMs);
          if (stamp) {
            const marker = document.createElement('div');
            marker.className = 'msg-time-divider';
            marker.textContent = stamp;
            messagesEl.appendChild(marker);
          }
        }
      }
      const div = document.createElement('div');
      div.className = `msg ${role}`;
      div.textContent = safeText;
      div.dataset.createdAt = createdAt;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      if (createdMs !== null) lastRenderedMessageMs = createdMs;
    }

    function renderSessionHistory(entries) {
      messagesEl.innerHTML = '';
      lastRenderedMessageMs = null;
      if (!Array.isArray(entries) || !entries.length) {
        return 0;
      }
      entries.forEach((entry) => {
        const role = entry && entry.role === 'user' ? 'user' : 'bot';
        const text = entry && entry.content ? String(entry.content) : '';
        const createdAt = entry && entry.created_at ? String(entry.created_at) : null;
        if (text) appendMessage(role, text, { created_at: createdAt });
      });
      return entries.length;
    }

    async function loadSessionHistory(sessionId, limit = 100) {
      try {
        const res = await fetch(`/api/session/history?session_id=${encodeURIComponent(sessionId)}&limit=${encodeURIComponent(limit)}`);
        const payload = await res.json();
        if (!payload || !payload.ok) return 0;
        sessionTimezone = payload && payload.timezone ? String(payload.timezone) : 'UTC';
        return renderSessionHistory(payload.entries);
      } catch (_err) {
        return 0;
      }
    }

    function policyHistoryLimit(rawValue) {
      const parsed = Number.parseInt(String(rawValue == null ? '' : rawValue), 10);
      if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_SESSION_HISTORY_LIMIT;
      return Math.max(1, Math.min(500, parsed));
    }

    function setBusyStatus(on, text) {
      statusEl.textContent = text || '';
      statusEl.classList.toggle('busy', !!on);
    }

    function setRuntimeFromResponse(data, sessionId) {
      routePill.textContent = `route: ${data && data.route ? data.route : '-'}`;
      sessionPill.textContent = `session: ${sessionId}`;
      const assembled = (
        data &&
        data.data &&
        data.data.context_debug &&
        data.data.context_debug.assembled_message_count !== undefined &&
        data.data.context_debug.assembled_message_count !== null
      ) ? data.data.context_debug.assembled_message_count : '-';
      msgCountPill.textContent = `assembled: ${assembled}`;
    }

    function extractToolCalls(data) {
      if (data && data.data && Array.isArray(data.data.tool_execution) && data.data.tool_execution.length) {
        return data.data.tool_execution.map((item) => ({
          name: item && item.name ? item.name : 'unknown_tool',
          source: 'tool_registry',
          ok: !!(item && item.result_ok),
          error: item && item.error ? item.error : null,
        }));
      }
      if (data && data.data && Array.isArray(data.data.tool_calls)) {
        return data.data.tool_calls;
      }
      return [];
    }

    function setLastResponsePanel(data) {
      const payload = {
        route: data && data.route ? data.route : null,
        tool_calls: extractToolCalls(data),
        reply: data && data.reply ? data.reply : '',
      };
      lastResponseEl.textContent = JSON.stringify(payload, null, 2);
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function renderJsonNode(value, key = null, depth = 0) {
      const keyHtml = key !== null ? `<span class="json-key">${escapeHtml(key)}</span>: ` : '';
      if (value === null || typeof value !== 'object') {
        return `<div>${keyHtml}<span class="json-atom">${escapeHtml(JSON.stringify(value))}</span></div>`;
      }
      const isArray = Array.isArray(value);
      const entries = isArray ? value.map((v, i) => [String(i), v]) : Object.entries(value);
      const openByDefault = depth <= 1;
      const summary = `${keyHtml}<span class="json-atom">${isArray ? '[' : '{'}</span><span class="json-type">${entries.length} item(s)</span><span class="json-atom">${isArray ? ']' : '}'}</span>`;
      if (!entries.length) {
        return `<div>${keyHtml}<span class="json-atom">${isArray ? '[]' : '{}'}</span></div>`;
      }
      const children = entries.map(([childKey, childValue]) => renderJsonNode(childValue, childKey, depth + 1)).join('');
      return `<details ${openByDefault ? 'open' : ''}><summary class="json-summary">${summary}</summary><div class="json-node">${children}</div></details>`;
    }

    async function fetchSessionContextSnapshot(sessionId) {
      const res = await fetch('/api/session/context', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sessionId }),
      });
      return res.json();
    }

    function getSessionId() {
      const value = (document.getElementById('session').value || 'default').trim();
      return value || 'default';
    }

    function renderContextDialogPayload(payload) {
      if (!contextBodyEl) return;
      if (!payload || !payload.ok) {
        contextBodyEl.innerHTML = `<div class="context-muted">${escapeHtml(payload && payload.message ? payload.message : 'No context snapshot available.')}</div>`;
        return;
      }
      latestContextSnapshot = payload.snapshot || null;
      contextBodyEl.innerHTML = renderJsonNode(payload.snapshot || {}, null, 0);
    }

    async function openContextDialog() {
      const sessionId = getSessionId();
      if (contextBodyEl) {
        contextBodyEl.innerHTML = '<div class="context-muted">Loading context snapshot...</div>';
      }
      if (contextDialogEl && typeof contextDialogEl.showModal === 'function') {
        contextDialogEl.showModal();
      }
      try {
        const payload = await fetchSessionContextSnapshot(sessionId);
        renderContextDialogPayload(payload);
      } catch (_err) {
        if (contextBodyEl) {
          contextBodyEl.innerHTML = '<div class="context-muted">Failed to load context snapshot.</div>';
        }
      }
    }

    function setProgressFromResponse(data) {
      const route = data && data.route ? data.route : 'unknown route';
      const tools = extractToolCalls(data);
      if (!tools.length) {
        progressEl.textContent = `Completed (${route})\nTools: none`;
        return;
      }
      const chain = tools.map((tool) => {
        const name = tool && tool.name ? tool.name : 'unknown_tool';
        const status = tool && typeof tool.ok === 'boolean' ? (tool.ok ? 'ok' : 'error') : 'attempted';
        return `${name} (${status})`;
      }).join(' -> ');
      progressEl.textContent = `Completed (${route})\nTools: ${chain}`;
    }

    function safeInt(value, fallback = 0) {
      const parsed = Number.parseInt(String(value == null ? '' : value), 10);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function clampPercent(value) {
      const num = Number(value);
      if (!Number.isFinite(num)) return 0;
      return Math.max(0, Math.min(100, num));
    }

    function renderLiveTaskProgress(taskId, payload, updatedAt) {
      if (!payload || typeof payload !== 'object') {
        progressEl.textContent = 'Idle';
        return;
      }

      const stage = String(payload.stage || 'idle');
      const percent = clampPercent(payload.overall_percent != null ? payload.overall_percent : payload.total_percent);
      const queryIdx = safeInt(payload.query_index, 0);
      const queryTotal = safeInt(payload.query_total, 0);
      const jobIdx = safeInt(payload.job_index, 0);
      const jobTotal = safeInt(payload.job_total, 0);
      const statusLine = payload.status_line ? String(payload.status_line) : '';
      const slots = Array.isArray(payload.worker_slots) ? payload.worker_slots : [];

      const summaryLines = [
        `<div>task: ${escapeHtml(taskId || 'indeed_daily_search')}</div>`,
        `<div>stage: ${escapeHtml(stage)} | overall: ${escapeHtml(percent.toFixed(1))}%</div>`,
        `<div>query: ${escapeHtml(String(queryIdx))}/${escapeHtml(String(queryTotal))} | result: ${escapeHtml(String(jobIdx))}/${escapeHtml(String(jobTotal))}</div>`,
      ];
      if (updatedAt) {
        summaryLines.push(`<div>updated: ${escapeHtml(String(updatedAt))}</div>`);
      }
      if (statusLine) {
        summaryLines.push(`<div>status: ${escapeHtml(statusLine)}</div>`);
      }

      const workerLines = slots.map((slot) => {
        const slotId = safeInt(slot && slot.slot, 0);
        const state = slot && slot.state ? String(slot.state) : 'idle';
        const stepLabel = slot && slot.step_label ? String(slot.step_label) : 'Idle';
        const stepIndex = safeInt(slot && slot.step_index, 0);
        const stepTotalRaw = safeInt(slot && slot.step_total, 4);
        const stepTotal = stepTotalRaw > 0 ? stepTotalRaw : 4;
        const jobKey = slot && slot.job_key ? String(slot.job_key) : '';
        const queryIndex = safeInt(slot && slot.query_index, 0);
        const queryTotalLocal = safeInt(slot && slot.query_total, 0);
        const queryKeyword = slot && slot.query_keyword ? String(slot.query_keyword) : '';
        const queryLocation = slot && slot.query_location ? String(slot.query_location) : '';
        const queryLabel = queryIndex > 0 && queryTotalLocal > 0
          ? `${queryIndex}/${queryTotalLocal}`
          : '-';
        const queryContext = queryKeyword || queryLocation
          ? ` (${queryKeyword}${queryKeyword && queryLocation ? ', ' : ''}${queryLocation})`
          : '';
        const shortJob = jobKey ? jobKey.slice(0, 8) : '-';
        return `
          <div class="worker-line">
            worker ${escapeHtml(String(slotId || '?'))}: <span class="worker-frac">${escapeHtml(String(stepIndex))}/${escapeHtml(String(stepTotal))}</span>
            ${escapeHtml(stepLabel)} | ${escapeHtml(state)} | query ${escapeHtml(queryLabel)}${escapeHtml(queryContext)} | job ${escapeHtml(shortJob)}
          </div>
        `;
      }).join('');

      progressEl.innerHTML = `
        <div class="progress-live">
          <div class="progress-summary">
            ${summaryLines.join('')}
          </div>
          <div class="worker-lines">
            ${workerLines || '<div class="worker-meta">No worker slot state reported.</div>'}
          </div>
        </div>
      `;
    }

    function renderCentralStatus(statusPayload, runsPayload) {
      const service = statusPayload && statusPayload.service ? statusPayload.service : {};
      const runtime = statusPayload && statusPayload.runtime ? statusPayload.runtime : {};
      const taskSlots = statusPayload && Array.isArray(statusPayload.task_slots) ? statusPayload.task_slots : [];
      const recentRuns = runsPayload && Array.isArray(runsPayload.runs) ? runsPayload.runs : [];
      let activeTaskId = 'indeed_daily_search';

      const lines = [
        `service_running=${!!service.running} enabled_in_config=${!!service.enabled_in_config}`,
        `queued=${runtime.queued_count != null ? runtime.queued_count : 0} running=${runtime.running_count != null ? runtime.running_count : 0} waiting=${runtime.waiting_count != null ? runtime.waiting_count : 0} active_threads=${runtime.active_task_threads != null ? runtime.active_task_threads : 0}`,
        `slots_busy=${runtime.task_slot_busy_count != null ? runtime.task_slot_busy_count : 0} slots_free=${runtime.task_slot_free_count != null ? runtime.task_slot_free_count : 0} slots_disabled=${runtime.task_slot_disabled_count != null ? runtime.task_slot_disabled_count : 0}`,
      ];
      if (Array.isArray(runtime.warnings) && runtime.warnings.length) {
        lines.push(`warnings=${runtime.warnings.join(',')}`);
      }

      if (!taskSlots.length) {
        lines.push('task_slots: none reported');
      } else {
        lines.push('task_slots:');
        taskSlots.forEach((slot) => {
          const slotId = slot && slot.slot_id ? slot.slot_id : 'slot?';
          const enabled = slot && typeof slot.enabled === 'boolean' ? slot.enabled : true;
          const state = slot && slot.state ? slot.state : (enabled ? 'free' : 'disabled');
          const runId = slot && slot.run_id ? slot.run_id : '-';
          const taskId = slot && slot.task_id ? slot.task_id : '-';
          const taskName = slot && slot.task_name ? slot.task_name : '-';
          if (
            slot &&
            slot.task_id &&
            state !== 'free' &&
            state !== 'disabled' &&
            activeTaskId === 'indeed_daily_search'
          ) {
            activeTaskId = String(slot.task_id);
          }
          lines.push(`- ${slotId} enabled=${enabled} state=${state} run_id=${runId} task_id=${taskId} task_name=${taskName}`);
          if (slot && slot.last_result && slot.last_result.status) {
            lines.push(`  last=${slot.last_result.status}`);
          }
        });
      }

      if (recentRuns.length) {
        lines.push('recent_runs:');
        recentRuns.slice(0, 5).forEach((run) => {
          lines.push(`- ${run.profile_id || 'profile?'} status=${run.status || 'unknown'} run_id=${run.run_id || 'run?'}`);
        });
      }

      centralStatusEl.textContent = lines.join('\\n');
      return activeTaskId;
    }

    async function fetchTaskLiveProgress(taskId) {
      const res = await fetch('/api/central/task_state/get', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          task_id: taskId || 'indeed_daily_search',
          state_key: 'live_progress',
        }),
      });
      return res.json();
    }

    async function refreshCentralStatus() {
      try {
        const [statusRes, runsRes] = await Promise.all([
          fetch('/api/central/status'),
          fetch('/api/central/runs?limit=5'),
        ]);
        const statusPayload = await statusRes.json();
        const runsPayload = await runsRes.json();
        if (statusPayload && statusPayload.ok) {
          const activeTaskId = renderCentralStatus(statusPayload, runsPayload);
          try {
            const liveState = await fetchTaskLiveProgress(activeTaskId || 'indeed_daily_search');
            renderLiveTaskProgress(
              activeTaskId || 'indeed_daily_search',
              liveState && liveState.ok ? liveState.value : null,
              liveState && liveState.updated_at ? liveState.updated_at : null,
            );
          } catch (_liveErr) {
            // Keep current progress panel state on task-state fetch errors.
          }
          return;
        }
      } catch (_err) {
        // fall through
      }
      centralStatusEl.textContent = 'Central status unavailable.';
    }

    function summarizeControlPayload(action) {
      const payload = action && action.payload && typeof action.payload === 'object' ? action.payload : {};
      const keys = Object.keys(payload);
      if (!keys.length) return '{}';
      const preview = {};
      keys.slice(0, 4).forEach((key) => {
        preview[key] = payload[key];
      });
      return JSON.stringify(preview);
    }

    function renderControlApprovals(rows) {
      if (!controlApprovalsEl) return;
      if (!Array.isArray(rows) || !rows.length) {
        controlApprovalsEl.textContent = 'No pending approvals.';
        return;
      }
      controlApprovalsEl.innerHTML = rows.map((row) => {
        const actionId = row && row.action_id ? String(row.action_id) : '';
        const title = row && row.title ? String(row.title) : (row && row.action ? String(row.action) : 'Action');
        const action = row && row.action ? String(row.action) : '-';
        const risk = row && row.risk_level ? String(row.risk_level) : 'medium';
        const route = row && row.requested_by_route ? String(row.requested_by_route) : '-';
        const payload = summarizeControlPayload(row);
        return `
          <div class="control-item">
            <div class="control-title">${escapeHtml(title)}</div>
            <div class="control-meta">action=${escapeHtml(action)} risk=${escapeHtml(risk)} route=${escapeHtml(route)}</div>
            <div class="control-meta">payload=${escapeHtml(payload)}</div>
            <div class="control-actions">
              <button class="primary btn-mini" data-control-approve="${escapeHtml(actionId)}">Allow</button>
              <button class="warn btn-mini" data-control-deny="${escapeHtml(actionId)}">Disallow</button>
            </div>
          </div>
        `;
      }).join('');
      controlApprovalsEl.querySelectorAll('[data-control-approve]').forEach((btn) => {
        btn.addEventListener('click', async (evt) => {
          const actionId = evt.currentTarget.getAttribute('data-control-approve');
          if (!actionId) return;
          await approveControlAction(actionId);
        });
      });
      controlApprovalsEl.querySelectorAll('[data-control-deny]').forEach((btn) => {
        btn.addEventListener('click', async (evt) => {
          const actionId = evt.currentTarget.getAttribute('data-control-deny');
          if (!actionId) return;
          await denyControlAction(actionId);
        });
      });
    }

    async function refreshControlPending() {
      const sessionId = getSessionId();
      try {
        const res = await fetch(`/api/control/pending?session_id=${encodeURIComponent(sessionId)}`);
        const payload = await res.json();
        const pending = payload && Array.isArray(payload.pending) ? payload.pending : [];
        renderControlApprovals(pending);
      } catch (_err) {
        if (controlApprovalsEl) controlApprovalsEl.textContent = 'Approvals unavailable.';
      }
    }

    async function ingestControlRequestsFromReply(replyText, route, sessionId) {
      if (!replyText || !String(replyText).trim()) return;
      try {
        await fetch('/api/control/ingest', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            session_id: sessionId,
            assistant_text: String(replyText),
            route: route || 'llm.main_agent',
          }),
        });
      } catch (_err) {
        return;
      }
      await refreshControlPending();
    }

    async function approveControlAction(actionId) {
      try {
        const res = await fetch('/api/control/approve', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action_id: actionId, approved_by: 'ui_user' }),
        });
        const payload = await res.json();
        if (payload && payload.ok) {
          appendMessage('bot', `Approved action ${actionId} and executed it.`, { created_at: new Date().toISOString() });
        } else {
          appendMessage('bot', `Approval failed for ${actionId}: ${(payload && payload.error) ? payload.error : 'unknown error'}`, { created_at: new Date().toISOString() });
        }
      } catch (_err) {
        appendMessage('bot', `Approval failed for ${actionId}.`, { created_at: new Date().toISOString() });
      }
      await refreshControlPending();
      await refreshCentralStatus();
    }

    async function denyControlAction(actionId) {
      try {
        const res = await fetch('/api/control/deny', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ action_id: actionId, denied_by: 'ui_user', reason: 'user_disallowed' }),
        });
        const payload = await res.json();
        if (payload && payload.ok) {
          appendMessage('bot', `Denied action ${actionId}.`, { created_at: new Date().toISOString() });
        } else {
          appendMessage('bot', `Failed to deny ${actionId}: ${(payload && payload.error) ? payload.error : 'unknown error'}`, { created_at: new Date().toISOString() });
        }
      } catch (_err) {
        appendMessage('bot', `Failed to deny ${actionId}.`, { created_at: new Date().toISOString() });
      }
      await refreshControlPending();
    }

    function startProgressTicker() {
      const phases = [
        'Thinking...',
        'Checking available tool routes...',
        'Assembling context...',
        'Waiting for model response...'
      ];
      let i = 0;
      progressEl.textContent = phases[0];
      return setInterval(() => {
        i = (i + 1) % phases.length;
        progressEl.textContent = phases[i];
      }, 460);
    }

    async function sendMsg() {
      const message = document.getElementById('msg').value.trim();
      const session_id = (document.getElementById('session').value || 'default').trim();
      if (!message) return;

      appendMessage('user', message, { created_at: new Date().toISOString() });
      document.getElementById('msg').value = '';

      setBusyStatus(true, 'Working...');
      const ticker = startProgressTicker();

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message, session_id})
        });
        const data = await res.json();
        const transcriptMeta = data && data.data && data.data.transcript_meta ? data.data.transcript_meta : {};
        appendMessage('bot', data.reply || '(No reply)', { created_at: transcriptMeta.assistant_created_at || new Date().toISOString() });
        await ingestControlRequestsFromReply(data && data.reply ? data.reply : '', data && data.route ? data.route : 'llm.main_agent', session_id);
        setLastResponsePanel(data);
        setRuntimeFromResponse(data, session_id);
        setProgressFromResponse(data);
        await refreshCentralStatus();
      } catch (err) {
        appendMessage('bot', 'Request failed.', { created_at: new Date().toISOString() });
        progressEl.textContent = 'Request failed.';
        lastResponseEl.textContent = JSON.stringify({
          route: "error",
          tool_calls: [],
          reply: "Request failed."
        }, null, 2);
      } finally {
        clearInterval(ticker);
        setBusyStatus(false, '');
      }
    }

    async function normalContext(showWelcome = true) {
      const session_id = (document.getElementById('session').value || 'default').trim();
      setBusyStatus(true, 'Loading normal context...');
      try {
        const res = await fetch('/api/session/init', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id})
        });
        const data = await res.json();
        const historyLimit = policyHistoryLimit(
          data && data.preload ? data.preload.rehydrate_limit : DEFAULT_SESSION_HISTORY_LIMIT
        );
        const loadedHistoryCount = await loadSessionHistory(session_id, historyLimit);
        if (showWelcome && data && data.welcome && loadedHistoryCount === 0) {
          appendMessage('bot', data.welcome, { created_at: new Date().toISOString() });
        }
        await refreshControlPending();
        setLastResponsePanel({
          route: 'session.init',
          reply: data.welcome || 'Session initialized.',
          data: {},
        });
        setRuntimeFromResponse({ route: 'session.init', data: { context_debug: {} } }, session_id);
        progressEl.textContent = `Session initialized (${session_id}).`;
        await refreshCentralStatus();
      } finally {
        setBusyStatus(false, '');
      }
    }

    async function resetContext() {
      const session_id = (document.getElementById('session').value || 'default').trim();
      setBusyStatus(true, 'Resetting context...');
      try {
        const res = await fetch('/api/session/reset', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id})
        });
        const data = await res.json();
        messagesEl.innerHTML = '';
        lastRenderedMessageMs = null;
        setLastResponsePanel({
          route: 'session.reset',
          reply: data.note || 'Session reset.',
          data: {},
        });
        await refreshControlPending();
        setRuntimeFromResponse({ route: 'session.reset', data: {} }, session_id);
        progressEl.textContent = 'Session context reset (history preserved).';
        await refreshCentralStatus();
      } finally {
        setBusyStatus(false, '');
      }
    }

    document.getElementById('msg').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendMsg();
    });

    document.getElementById('session').addEventListener('change', () => {
      normalContext(true);
    });

    window.switchTab = switchTab;
    window.onScheduleModeChange = onScheduleModeChange;
    window.clearScheduleForm = clearScheduleForm;
    window.refreshScheduleManager = refreshScheduleManager;
    window.saveSchedule = saveSchedule;
    window.addCalendarRunTimeRow = addCalendarRunTimeRow;
    window.resetContext = resetContext;
    window.normalContext = normalContext;
    window.openContextDialog = openContextDialog;

    if (contextCloseBtnEl && contextDialogEl) {
      contextCloseBtnEl.addEventListener('click', () => {
        contextDialogEl.close();
      });
    }
    if (contextDownloadBtnEl) {
      contextDownloadBtnEl.addEventListener('click', () => {
        if (!latestContextSnapshot) return;
        const blob = new Blob([JSON.stringify(latestContextSnapshot, null, 2)], { type: 'application/json' });
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = `zubot_context_snapshot_${getSessionId()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
      });
    }

    setInterval(() => {
      refreshCentralStatus();
      refreshControlPending();
    }, 1200);
    initSchedulePickers();
    if (scheduleTaskSelect) {
      scheduleTaskSelect.addEventListener('change', () => {
        if (!scheduleEditingId && scheduleNameInput) {
          const selected = scheduleTaskSelect.value || '';
          scheduleNameInput.value = selected ? `${selected}_schedule` : '';
        }
      });
    }
    onScheduleModeChange();
    clearScheduleForm();
    normalContext(true);
    refreshControlPending();
    window.__zubotRichUiInitDone = true;
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, max-age=0"})
