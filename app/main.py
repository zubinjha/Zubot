"""Minimal local web chat interface for loop testing."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .chat_logic import handle_chat_message, initialize_session_context, reset_session_context
from src.zubot.core.worker_manager import get_worker_manager

app = FastAPI(title="Zubot Local Chat")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ResetRequest(BaseModel):
    session_id: str = "default"

class InitRequest(BaseModel):
    session_id: str = "default"

class SpawnWorkerRequest(BaseModel):
    title: str
    instructions: str
    model_tier: str = "medium"
    tool_access: list[str] = Field(default_factory=list)
    skill_access: list[str] = Field(default_factory=list)
    preload_files: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class WorkerMessageRequest(BaseModel):
    message: str
    model_tier: str = "medium"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    return handle_chat_message(req.message, allow_llm_fallback=True, session_id=req.session_id)


@app.post("/api/session/reset")
def reset_session(req: ResetRequest) -> dict:
    return reset_session_context(req.session_id)


@app.post("/api/session/init")
def init_session(req: InitRequest) -> dict:
    return initialize_session_context(req.session_id)


@app.post("/api/workers/spawn")
def spawn_worker(req: SpawnWorkerRequest) -> dict:
    return get_worker_manager().spawn_worker(
        title=req.title,
        instructions=req.instructions,
        model_tier=req.model_tier,
        tool_access=req.tool_access,
        skill_access=req.skill_access,
        preload_files=req.preload_files,
        metadata=req.metadata,
    )


@app.post("/api/workers/{worker_id}/cancel")
def cancel_worker(worker_id: str) -> dict:
    return get_worker_manager().cancel_worker(worker_id)


@app.post("/api/workers/{worker_id}/reset-context")
def reset_worker_context(worker_id: str) -> dict:
    return get_worker_manager().reset_worker_context(worker_id)


@app.post("/api/workers/{worker_id}/message")
def message_worker(worker_id: str, req: WorkerMessageRequest) -> dict:
    return get_worker_manager().message_worker(
        worker_id=worker_id,
        message=req.message,
        model_tier=req.model_tier,
    )


@app.get("/api/workers/{worker_id}")
def get_worker(worker_id: str) -> dict:
    return get_worker_manager().get_worker(worker_id)


@app.get("/api/workers")
def list_workers() -> dict:
    return get_worker_manager().list_workers()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html>
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
      display: grid;
      place-items: center;
      padding: 20px;
    }

    .app {
      width: min(1100px, 100%);
      height: min(860px, calc(100vh - 40px));
      display: grid;
      grid-template-columns: 1.6fr 1fr;
      gap: 14px;
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
      grid-template-rows: auto 1fr auto;
      min-height: 0;
    }

    .chat-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(120deg, #f3fff9 0%, #fff7eb 100%);
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

    .workers {
      display: grid;
      gap: 8px;
    }

    .worker-row {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdfa;
      padding: 8px;
      display: grid;
      gap: 6px;
    }

    .worker-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .worker-title {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.78rem;
      color: var(--ink);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 220px;
    }

    .worker-meta {
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.73rem;
      color: var(--muted);
      word-break: break-word;
    }

    .btn-kill {
      border-color: #f3b6b6;
      background: #ffecec;
      color: #862c2c;
      padding: 5px 8px;
      font-size: 0.74rem;
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
  <div class="app">
    <section class="panel chat">
      <div class="chat-header">
        <h1>Zubot Local Chat</h1>
        <div class="sub">Session-based chat with context + daily memory refresh</div>
      </div>
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
        <h3>Workers</h3>
        <div id="workers" class="body workers">Loading worker status...</div>
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
    const statusEl = document.getElementById('status');
    const progressEl = document.getElementById('progress');
    const messagesEl = document.getElementById('messages');
    const lastResponseEl = document.getElementById('last-response');
    const routePill = document.getElementById('route-pill');
    const sessionPill = document.getElementById('session-pill');
    const msgCountPill = document.getElementById('msgcount-pill');
    const workersEl = document.getElementById('workers');
    let workerPollTimer = null;

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
      routePill.textContent = `route: ${data.route || '-'}`;
      sessionPill.textContent = `session: ${sessionId}`;
      const assembled = data?.data?.context_debug?.assembled_message_count;
      msgCountPill.textContent = `assembled: ${assembled ?? '-'}`;
    }

    function extractToolCalls(data) {
      if (Array.isArray(data?.data?.tool_execution) && data.data.tool_execution.length) {
        return data.data.tool_execution.map((item) => ({
          name: item?.name || 'unknown_tool',
          source: 'tool_registry',
          ok: !!item?.result_ok,
          error: item?.error || null,
        }));
      }
      if (Array.isArray(data?.data?.tool_calls)) {
        return data.data.tool_calls;
      }
      return [];
    }

    function setLastResponsePanel(data) {
      const payload = {
        route: data?.route || null,
        tool_calls: extractToolCalls(data),
        reply: data?.reply || '',
      };
      lastResponseEl.textContent = JSON.stringify(payload, null, 2);
    }

    function setProgressFromResponse(data) {
      const route = data?.route || 'unknown route';
      const tools = extractToolCalls(data);
      if (!tools.length) {
        progressEl.textContent = `Completed (${route})\nTools: none`;
        return;
      }
      const chain = tools.map((tool) => {
        const name = tool?.name || 'unknown_tool';
        const status = typeof tool?.ok === 'boolean' ? (tool.ok ? 'ok' : 'error') : 'attempted';
        return `${name} (${status})`;
      }).join(' -> ');
      progressEl.textContent = `Completed (${route})\nTools: ${chain}`;
    }

    function renderWorkers(data) {
      const workers = Array.isArray(data?.workers) ? data.workers : [];
      const runtime = data?.runtime || {};
      const running = runtime.running_count ?? 0;
      const queued = runtime.queued_count ?? 0;
      const max = runtime.max_concurrent_workers ?? 3;

      if (!workers.length) {
        workersEl.innerHTML = `<div class="worker-meta">No workers yet. Capacity: ${running}/${max} running, ${queued} queued.</div>`;
        return;
      }

      const rows = workers.slice(0, 3).map((worker) => {
        const workerId = worker.worker_id || 'worker?';
        const title = worker.title || 'Untitled worker';
        const status = worker.status || 'unknown';
        const disableKill = status === 'done' || status === 'failed' || status === 'cancelled';
        return `
          <div class="worker-row">
            <div class="worker-top">
              <div class="worker-title" title="${title}">${title}</div>
              <button class="btn-kill" data-worker-id="${workerId}" ${disableKill ? 'disabled' : ''}>Kill</button>
            </div>
            <div class="worker-meta">id=${workerId}</div>
            <div class="worker-meta">status=${status}</div>
          </div>
        `;
      }).join('');

      workersEl.innerHTML = `
        <div class="worker-meta">Capacity: ${running}/${max} running, ${queued} queued.</div>
        ${rows}
      `;

      workersEl.querySelectorAll('.btn-kill').forEach((btn) => {
        btn.addEventListener('click', async (evt) => {
          const id = evt.currentTarget?.getAttribute('data-worker-id');
          if (!id) return;
          await killWorker(id);
        });
      });
    }

    async function refreshWorkers() {
      try {
        const res = await fetch('/api/workers');
        const data = await res.json();
        if (data?.ok) renderWorkers(data);
      } catch (_err) {
        workersEl.textContent = 'Worker status unavailable.';
      }
    }

    async function killWorker(workerId) {
      setBusyStatus(true, `Killing ${workerId}...`);
      try {
        const res = await fetch(`/api/workers/${workerId}/cancel`, { method: 'POST' });
        const data = await res.json();
        if (data?.ok) {
          appendMessage('bot', `Worker ${workerId} cancelled.`);
          progressEl.textContent = `Cancelled ${workerId}.`;
        } else {
          appendMessage('bot', data?.error || `Failed to cancel ${workerId}.`);
        }
      } catch (_err) {
        appendMessage('bot', `Failed to cancel ${workerId}.`);
      } finally {
        setBusyStatus(false, '');
        await refreshWorkers();
      }
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
        await refreshWorkers();
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
        await refreshWorkers();
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
        if (showWelcome && data?.welcome) {
          appendMessage('bot', data.welcome);
        }
        setLastResponsePanel({
          route: 'session.init',
          reply: data?.welcome || 'Session initialized.',
          data: { tool_calls: [] }
        });
        setRuntimeFromResponse({ route: 'session.init', data: { context_debug: {} } }, session_id);
        progressEl.textContent = `Session initialized (${session_id}).`;
        await refreshWorkers();
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

    workerPollTimer = setInterval(refreshWorkers, 1200);
    initSession(true);
  </script>
</body>
</html>
"""
