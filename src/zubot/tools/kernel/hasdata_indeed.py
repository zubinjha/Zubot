"""HasData-backed Indeed tools."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.zubot.core.config_loader import load_config
from src.zubot.core.provider_queue import execute_provider_call, provider_queue_stats

DEFAULT_HASDATA_BASE_URL = "https://api.hasdata.com"
DEFAULT_INDEED_DOMAIN = "www.indeed.com"
DEFAULT_INDEED_SORT = "date"


def _hasdata_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    config: dict[str, Any] = {}
    profiles = payload.get("tool_profiles")
    if isinstance(profiles, dict):
        user_specific = profiles.get("user_specific")
        if isinstance(user_specific, dict):
            nested = user_specific.get("has_data")
            if isinstance(nested, dict):
                config = nested
    if not config:
        # Backward compatibility for pre-profile configs.
        block = payload.get("has_data")
        if isinstance(block, dict):
            config = block
    api_key = config.get("api_key")

    return {
        "base_url": str(config.get("base_url", DEFAULT_HASDATA_BASE_URL)).rstrip("/"),
        "api_key": api_key if isinstance(api_key, str) else None,
        "timeout_sec": int(config.get("timeout_sec", 15)),
        "queue_min_interval_sec": float(config.get("queue_min_interval_sec", 0.0))
        if isinstance(config.get("queue_min_interval_sec", 0.0), (int, float))
        else 0.0,
        "queue_max_retries": int(config.get("queue_max_retries", 1))
        if isinstance(config.get("queue_max_retries", 1), int)
        else 1,
        "queue_retry_backoff_sec": float(config.get("queue_retry_backoff_sec", 1.0))
        if isinstance(config.get("queue_retry_backoff_sec", 1.0), (int, float))
        else 1.0,
        "queue_jitter_sec": float(config.get("queue_jitter_sec", 0.0))
        if isinstance(config.get("queue_jitter_sec", 0.0), (int, float))
        else 0.0,
    }


def _fetch_json(url: str, headers: dict[str, str], timeout_sec: int) -> dict[str, Any]:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("HasData response must be a JSON object.")
    return payload


def _missing_key_payload(source: str) -> dict[str, Any]:
    return {
        "ok": False,
        "provider": "hasdata",
        "source": source,
        "error": "Missing `tool_profiles.user_specific.has_data.api_key` in config.",
    }


def _is_retryable_hasdata_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        return code == 429 or 500 <= code < 600
    if isinstance(exc, URLError):
        return True
    return False


def get_indeed_jobs(
    *,
    keyword: str,
    location: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get Indeed job listings from HasData listing endpoint.

    Contract intentionally keeps only `keyword` and `location` as user-facing inputs.
    Query behavior is fixed to:
    - domain: www.indeed.com
    - sort: date
    """
    source = "hasdata_indeed_listing"
    settings = _hasdata_settings()

    if not keyword.strip():
        return {
            "ok": False,
            "provider": "hasdata",
            "source": source,
            "error": "keyword must be non-empty.",
        }
    if not location.strip():
        return {
            "ok": False,
            "provider": "hasdata",
            "source": source,
            "error": "location must be non-empty.",
        }
    if not settings["api_key"]:
        return _missing_key_payload(source)

    params = {
        "keyword": keyword,
        "location": location,
        "sort": DEFAULT_INDEED_SORT,
        "domain": DEFAULT_INDEED_DOMAIN,
    }
    url = f"{settings['base_url']}/scrape/indeed/listing?{urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": settings["api_key"],
    }

    queued = execute_provider_call(
        group="hasdata",
        fn=lambda: _fetch_json(url, headers=headers, timeout_sec=settings["timeout_sec"]),
        min_interval_sec=settings["queue_min_interval_sec"],
        jitter_sec=settings["queue_jitter_sec"],
        max_retries=settings["queue_max_retries"],
        retry_backoff_sec=settings["queue_retry_backoff_sec"],
        is_retryable=_is_retryable_hasdata_error,
    )
    if not queued.get("ok"):
        exc = queued.get("error")
        return {
            "ok": False,
            "provider": "hasdata",
            "source": "hasdata_indeed_listing_error",
            "error": str(exc),
            "queue": queued.get("queue"),
            "queue_stats": provider_queue_stats("hasdata"),
            "request": {
                "keyword": keyword,
                "location": location,
                "sort": DEFAULT_INDEED_SORT,
                "domain": DEFAULT_INDEED_DOMAIN,
            },
        }
    payload = queued.get("value") if isinstance(queued.get("value"), dict) else {}

    request_meta = payload.get("requestMetadata") if isinstance(payload.get("requestMetadata"), dict) else {}
    search_info = payload.get("searchInformation") if isinstance(payload.get("searchInformation"), dict) else {}
    jobs_raw = payload.get("jobs")
    jobs = jobs_raw if isinstance(jobs_raw, list) else []
    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}

    return {
        "ok": True,
        "provider": "hasdata",
        "source": source,
        "request": {
            "keyword": keyword,
            "location": location,
            "sort": DEFAULT_INDEED_SORT,
            "domain": DEFAULT_INDEED_DOMAIN,
        },
        "request_metadata": request_meta,
        "search_information": search_info,
        "jobs": jobs,
        "jobs_count": len(jobs),
        "pagination": pagination,
        "queue": queued.get("queue"),
        "queue_stats": provider_queue_stats("hasdata"),
        "error": None,
    }


def get_indeed_job_detail(*, url: str) -> dict[str, Any]:
    """Get detailed Indeed job information from HasData job endpoint."""
    source = "hasdata_indeed_job"
    settings = _hasdata_settings()
    job_url = url.strip()
    if not job_url:
        return {
            "ok": False,
            "provider": "hasdata",
            "source": source,
            "error": "url must be non-empty.",
        }
    if not settings["api_key"]:
        return _missing_key_payload(source)

    params = {"url": job_url}
    request_url = f"{settings['base_url']}/scrape/indeed/job?{urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": settings["api_key"],
    }

    queued = execute_provider_call(
        group="hasdata",
        fn=lambda: _fetch_json(request_url, headers=headers, timeout_sec=settings["timeout_sec"]),
        min_interval_sec=settings["queue_min_interval_sec"],
        jitter_sec=settings["queue_jitter_sec"],
        max_retries=settings["queue_max_retries"],
        retry_backoff_sec=settings["queue_retry_backoff_sec"],
        is_retryable=_is_retryable_hasdata_error,
    )
    if not queued.get("ok"):
        exc = queued.get("error")
        return {
            "ok": False,
            "provider": "hasdata",
            "source": "hasdata_indeed_job_error",
            "error": str(exc),
            "queue": queued.get("queue"),
            "queue_stats": provider_queue_stats("hasdata"),
            "request": {"url": job_url},
        }
    payload = queued.get("value") if isinstance(queued.get("value"), dict) else {}

    request_meta = payload.get("requestMetadata") if isinstance(payload.get("requestMetadata"), dict) else {}
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}

    return {
        "ok": True,
        "provider": "hasdata",
        "source": source,
        "request": {"url": job_url},
        "request_metadata": request_meta,
        "job": job,
        "queue": queued.get("queue"),
        "queue_stats": provider_queue_stats("hasdata"),
        "error": None,
    }
