"""Web search tool backed by Brave Search API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.zubot.core.config_loader import load_config

DEFAULT_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def _web_search_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    block = payload.get("web_search")
    config = block if isinstance(block, dict) else {}

    api_key = config.get("brave_api_key")
    return {
        "provider": config.get("provider", "brave"),
        "base_url": config.get("base_url", DEFAULT_BRAVE_SEARCH_URL),
        "brave_api_key": api_key if isinstance(api_key, str) else None,
        "timeout_sec": int(config.get("timeout_sec", 10)),
    }


def _fetch_json(url: str, headers: dict[str, str], timeout_sec: int) -> dict[str, Any]:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Web search response must be a JSON object.")
    return payload


def web_search(
    query: str,
    *,
    count: int = 5,
    country: str = "US",
    search_lang: str = "en",
) -> dict[str, Any]:
    """Run Brave web search and return normalized results."""
    source = "brave_api"
    settings = _web_search_settings()

    if not query.strip():
        return {
            "ok": False,
            "query": query,
            "results": [],
            "provider": settings["provider"],
            "source": source,
            "error": "Query must be non-empty.",
        }

    api_key = settings["brave_api_key"]
    if not api_key:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "provider": settings["provider"],
            "source": "config_missing",
            "error": "Missing `web_search.brave_api_key` in config.",
        }

    params = {
        "q": query,
        "count": max(1, min(20, int(count))),
        "country": country,
        "search_lang": search_lang,
    }
    url = f"{settings['base_url']}?{urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }

    try:
        payload = _fetch_json(url, headers=headers, timeout_sec=settings["timeout_sec"])
    except Exception as exc:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "provider": settings["provider"],
            "source": "brave_api_error",
            "error": str(exc),
        }

    web_block = payload.get("web")
    raw_results = web_block.get("results", []) if isinstance(web_block, dict) else []
    results: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "description": item.get("description"),
                "age": item.get("age"),
                "language": item.get("language"),
            }
        )

    return {
        "ok": True,
        "query": query,
        "results": results,
        "provider": settings["provider"],
        "source": source,
        "error": None,
    }
