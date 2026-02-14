"""Minimal local web chat interface for loop testing."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Literal

from src.zubot.runtime.service import get_runtime_service

app = FastAPI(title="Zubot Local Chat")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ResetRequest(BaseModel):
    session_id: str = "default"

class InitRequest(BaseModel):
    session_id: str = "default"


class TriggerTaskProfileRequest(BaseModel):
    description: str | None = None


class KillTaskRunRequest(BaseModel):
    requested_by: str = "main_agent"


class ScheduleUpsertRequest(BaseModel):
    schedule_id: str | None = None
    task_id: str
    enabled: bool = True
    mode: Literal["frequency", "calendar"] = "frequency"
    execution_order: int = 100
    run_frequency_minutes: int | None = None
    timezone: str | None = "America/New_York"
    run_times: list[str] = Field(default_factory=list)
    days_of_week: list[str] = Field(default_factory=list)


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


@app.post("/api/session/init")
def init_session(req: InitRequest) -> dict:
    return get_runtime_service().init_session(session_id=req.session_id)


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


@app.get("/api/central/metrics")
def central_metrics() -> dict:
    return get_runtime_service().central_metrics()


@app.get("/api/central/tasks")
def central_tasks() -> dict:
    return get_runtime_service().central_list_defined_tasks()


@app.post("/api/central/schedules")
def central_upsert_schedule(req: ScheduleUpsertRequest) -> dict:
    return get_runtime_service().central_upsert_schedule(
        schedule_id=req.schedule_id,
        task_id=req.task_id,
        enabled=req.enabled,
        mode=req.mode,
        execution_order=req.execution_order,
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


@app.post("/api/central/runs/{run_id}/kill")
def central_kill_run(run_id: str, req: KillTaskRunRequest | None = None) -> dict:
    requested_by = req.requested_by if isinstance(req, KillTaskRunRequest) else "main_agent"
    return get_runtime_service().central_kill_run(run_id=run_id, requested_by=requested_by)


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
            <button class="warn" onclick="resetSession()">Reset Session</button>
          </div>
          <div id="status" class="status"></div>
        </div>
      </div>
      <div id="panel-schedules" class="tab-panel schedules">
        <div class="sched-wrap">
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
        <h3>Task Agents</h3>
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

  <script>
    // Compatibility fallback: activates only if the richer UI script failed to initialize.
    (function () {
      function el(id) { return document.getElementById(id); }
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
              ' running=' + (body.runtime.running_count != null ? body.runtime.running_count : 0);
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
        window.resetSession = function () {
          var sessionInput = el('session');
          var sessionId = sessionInput && sessionInput.value ? String(sessionInput.value).trim() : 'default';
          setBusyStatus(true, 'Resetting session...');
          postJson('/api/session/reset', { session_id: sessionId }, function (_status, body) {
            appendMessage('bot', body && body.note ? body.note : 'Session reset.');
            setLastResponsePanel({
              route: 'session.reset',
              reply: body && body.note ? body.note : 'Session reset.',
              data: {}
            });
            setRuntimeFromResponse({ route: 'session.reset', data: {} }, sessionId);
            var progressEl = el('progress');
            if (progressEl) progressEl.textContent = 'Session context reset.';
            setBusyStatus(false, '');
            refreshCentralStatusOnly();
          });
        };
        var msgInput = el('msg');
        if (msgInput && !msgInput.__zubotFallbackBound) {
          msgInput.__zubotFallbackBound = true;
          msgInput.addEventListener('keydown', function (e) {
            if (e && e.key === 'Enter') window.sendMsg();
          });
        }
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
    const appRoot = document.getElementById('app-root');
    const panelChat = document.getElementById('panel-chat');
    const panelSchedules = document.getElementById('panel-schedules');
    const tabChat = document.getElementById('tab-chat');
    const tabSchedules = document.getElementById('tab-schedules');
    const schedulesListEl = document.getElementById('schedules-list');
    const scheduleTaskSelect = document.getElementById('sched-task-id');
    const scheduleModeSelect = document.getElementById('sched-mode');
    const scheduleEnabledCheckbox = document.getElementById('sched-enabled');
    const scheduleCalendarFields = document.getElementById('sched-calendar-fields');
    const scheduleFrequencyRow = document.getElementById('sched-frequency-row');
    const scheduleNameInput = document.getElementById('sched-name');
    const scheduleFrequencyHours = document.getElementById('sched-frequency-hours');
    const scheduleFrequencyMinutes = document.getElementById('sched-frequency-minutes');
    const scheduleCalendarRows = document.getElementById('sched-calendar-time-rows');
    const scheduleFormStatus = document.getElementById('sched-form-status');
    let currentUiTab = 'chat';
    let cachedSchedules = [];
    let scheduleEditingId = null;
    let expandedScheduleIds = new Set();

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
      setFrequencySelectorsFromMinutes(1440);
      clearCalendarRunTimeRows();
      addCalendarRunTimeRow('09:00');
      document.getElementById('sched-timezone').value = 'America/New_York';
      if (scheduleEnabledCheckbox) scheduleEnabledCheckbox.checked = true;
      setScheduleDays([]);
      setScheduleFormStatus('');
      onScheduleModeChange();
    }

    async function loadDefinedTasks() {
      const previousValue = scheduleTaskSelect ? scheduleTaskSelect.value : '';
      const res = await fetch('/api/central/tasks');
      const payload = await res.json();
      const ok = !!(payload && payload.ok);
      const tasks = payload && Array.isArray(payload.tasks) ? payload.tasks : [];
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
    }

    function editSchedule(scheduleId) {
      const item = cachedSchedules.find((row) => row.schedule_id === scheduleId);
      if (!item) return;
      scheduleEditingId = item.schedule_id || null;
      if (scheduleNameInput) scheduleNameInput.value = item.schedule_id || '';
      if (scheduleTaskSelect) scheduleTaskSelect.value = item.task_id || item.profile_id || '';
      if (scheduleModeSelect) scheduleModeSelect.value = item.mode || 'frequency';
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
        const detailLines = row.mode === 'frequency'
          ? `<div>frequency: ${frequencyLabel}</div>`
          : `${runTimes.map((time) => `<div>time: ${formatCalendarTime(time)}</div>`).join('')}<div>days: ${dayList || '-'}</div><div>timezone: ${row.timezone || 'America/New_York'}</div>`;
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

    function appendMessage(role, text) {
      const div = document.createElement('div');
      div.className = `msg ${role}`;
      div.textContent = text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
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

    function renderCentralStatus(statusPayload, runsPayload) {
      const service = statusPayload && statusPayload.service ? statusPayload.service : {};
      const runtime = statusPayload && statusPayload.runtime ? statusPayload.runtime : {};
      const taskAgents = statusPayload && Array.isArray(statusPayload.task_agents) ? statusPayload.task_agents : [];
      const recentRuns = runsPayload && Array.isArray(runsPayload.runs) ? runsPayload.runs : [];

      const lines = [
        `service_running=${!!service.running} enabled_in_config=${!!service.enabled_in_config}`,
        `queued=${runtime.queued_count != null ? runtime.queued_count : 0} running=${runtime.running_count != null ? runtime.running_count : 0} active_threads=${runtime.active_task_threads != null ? runtime.active_task_threads : 0}`,
      ];
      if (Array.isArray(runtime.warnings) && runtime.warnings.length) {
        lines.push(`warnings=${runtime.warnings.join(',')}`);
      }

      if (!taskAgents.length) {
        lines.push('task_agents: none configured');
      } else {
        lines.push('task_agents:');
        taskAgents.forEach((agent) => {
          const name = agent && (agent.name || agent.profile_id) ? (agent.name || agent.profile_id) : 'unknown';
          const state = agent && agent.state ? agent.state : 'free';
          const desc = agent && agent.current_description ? agent.current_description : '';
          const queuePos = Number.isInteger(agent && agent.queue_position) ? ` queue_pos=${agent.queue_position}` : '';
          lines.push(`- ${name} state=${state}${queuePos}`);
          if (desc) lines.push(`  desc=${desc}`);
          if (agent && agent.last_result && agent.last_result.status) {
            lines.push(`  last=${agent.last_result.status}`);
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
          renderCentralStatus(statusPayload, runsPayload);
          return;
        }
      } catch (_err) {
        // fall through
      }
      centralStatusEl.textContent = 'Central status unavailable.';
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

      appendMessage('user', message);
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
        appendMessage('bot', data.reply || '(No reply)');
        setLastResponsePanel(data);
        setRuntimeFromResponse(data, session_id);
        setProgressFromResponse(data);
        await refreshCentralStatus();
      } catch (err) {
        appendMessage('bot', 'Request failed.');
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

    async function resetSession() {
      const session_id = (document.getElementById('session').value || 'default').trim();
      setBusyStatus(true, 'Resetting session...');
      try {
        const res = await fetch('/api/session/reset', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id})
        });
        const data = await res.json();
        appendMessage('bot', data.note || 'Session reset.');
        setLastResponsePanel({
          route: 'session.reset',
          reply: data.note || 'Session reset.',
          data: {},
        });
        setRuntimeFromResponse({ route: 'session.reset', data: {} }, session_id);
        progressEl.textContent = 'Session context reset.';
        await refreshCentralStatus();
      } finally {
        setBusyStatus(false, '');
      }
    }

    async function initSession(showWelcome = true) {
      const session_id = (document.getElementById('session').value || 'default').trim();
      setBusyStatus(true, 'Initializing session...');
      try {
        const res = await fetch('/api/session/init', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id})
        });
        const data = await res.json();
        if (showWelcome && data && data.welcome) {
          appendMessage('bot', data.welcome);
        }
        setLastResponsePanel({
          route: 'session.init',
          reply: (data && data.welcome) ? data.welcome : 'Session initialized.',
          data: { tool_calls: [] }
        });
        setRuntimeFromResponse({ route: 'session.init', data: { context_debug: {} } }, session_id);
        progressEl.textContent = `Session initialized (${session_id}).`;
        await refreshCentralStatus();
      } finally {
        setBusyStatus(false, '');
      }
    }

    document.getElementById('msg').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendMsg();
    });

    document.getElementById('session').addEventListener('change', () => {
      initSession(true);
    });

    window.switchTab = switchTab;
    window.onScheduleModeChange = onScheduleModeChange;
    window.clearScheduleForm = clearScheduleForm;
    window.refreshScheduleManager = refreshScheduleManager;
    window.saveSchedule = saveSchedule;
    window.addCalendarRunTimeRow = addCalendarRunTimeRow;

    setInterval(() => {
      refreshCentralStatus();
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
    initSession(true);
    window.__zubotRichUiInitDone = true;
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, max-age=0"})
