"""Minimal local web chat interface for loop testing."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .chat_logic import handle_chat_message

app = FastAPI(title="Zubot Local Chat")


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    return handle_chat_message(req.message, allow_llm_fallback=True)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Zubot Local Chat</title>
  <style>
    body { font-family: Menlo, Monaco, Consolas, monospace; margin: 24px; max-width: 900px; }
    .row { display: flex; gap: 8px; }
    input { flex: 1; padding: 10px; }
    button { padding: 10px 14px; cursor: pointer; }
    pre { white-space: pre-wrap; background: #f7f7f7; padding: 12px; border: 1px solid #ddd; }
  </style>
</head>
<body>
  <h2>Zubot Local Chat</h2>
  <p>Try: "time", "today weather", "24 hour weather", "week weather".</p>
  <div class="row">
    <input id="msg" placeholder="Type a message..." />
    <button onclick="sendMsg()">Send</button>
  </div>
  <h3>Reply</h3>
  <pre id="reply"></pre>
  <h3>Raw</h3>
  <pre id="raw"></pre>
  <script>
    async function sendMsg() {
      const message = document.getElementById('msg').value;
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message})
      });
      const data = await res.json();
      document.getElementById('reply').textContent = data.reply || '';
      document.getElementById('raw').textContent = JSON.stringify(data, null, 2);
    }
  </script>
</body>
</html>
"""
