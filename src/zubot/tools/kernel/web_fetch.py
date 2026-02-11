"""Fetch and extract readable content from a URL."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.zubot.core.config_loader import load_config


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        raw = " ".join(self._chunks)
        return re.sub(r"\s+", " ", raw).strip()


def _web_fetch_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    block = payload.get("web_fetch")
    config = block if isinstance(block, dict) else {}
    return {
        "timeout_sec": int(config.get("timeout_sec", 10)),
        "max_chars": int(config.get("max_chars", 20000)),
        "user_agent": config.get("user_agent", "Zubot/0.1 (+local-first-agent)"),
    }


def _extract_text(content_type: str, body: bytes, max_chars: int) -> str:
    text = body.decode("utf-8", errors="replace")
    if "text/html" in content_type:
        parser = _TextExtractor()
        parser.feed(text)
        extracted = parser.text()
    else:
        extracted = text
    return extracted[:max_chars]


def fetch_url(url: str) -> dict[str, Any]:
    """Fetch URL content and return normalized extracted text."""
    source = "web_fetch"
    settings = _web_fetch_settings()

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": None,
            "title": None,
            "text": "",
            "error": "Only http/https URLs are supported.",
            "source": source,
        }

    headers = {
        "User-Agent": settings["user_agent"],
        "Accept": "text/html, text/plain, application/xhtml+xml;q=0.9, */*;q=0.8",
    }
    req = Request(url, headers=headers, method="GET")

    try:
        with urlopen(req, timeout=settings["timeout_sec"]) as response:
            status = getattr(response, "status", None)
            raw_content_type = response.headers.get("Content-Type", "text/plain")
            content_type = raw_content_type.split(";")[0].strip().lower()
            body = response.read()
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status": None,
            "content_type": None,
            "title": None,
            "text": "",
            "error": str(exc),
            "source": "web_fetch_error",
        }

    text = _extract_text(content_type, body, settings["max_chars"])

    title: str | None = None
    if "text/html" in content_type:
        match = re.search(r"<title[^>]*>(.*?)</title>", body.decode("utf-8", errors="replace"), re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()

    return {
        "ok": True,
        "url": url,
        "status": status,
        "content_type": content_type,
        "title": title,
        "text": text,
        "error": None,
        "source": source,
    }
