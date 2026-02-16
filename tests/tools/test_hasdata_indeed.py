import importlib
import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.hasdata_indeed import get_indeed_job_detail, get_indeed_jobs

module = importlib.import_module("src.zubot.tools.kernel.hasdata_indeed")


@pytest.fixture()
def configured_hasdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tool_profiles": {
                    "user_specific": {
                        "has_data": {
                            "api_key": "HASDATA_TEST_KEY",
                            "base_url": "https://api.hasdata.com",
                            "timeout_sec": 12,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return True


def test_get_indeed_jobs_validates_inputs(configured_hasdata):
    a = get_indeed_jobs(keyword=" ", location="Columbus, OH")
    b = get_indeed_jobs(keyword="software engineer", location=" ")
    assert not a["ok"] and "keyword" in a["error"]
    assert not b["ok"] and "location" in b["error"]


def test_get_indeed_jobs_missing_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"tool_profiles": {"user_specific": {"has_data": {}}}}), encoding="utf-8")
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(path))
    clear_config_cache()

    out = get_indeed_jobs(keyword="software engineer", location="Columbus, OH")
    assert out["ok"] is False
    assert out["source"] == "hasdata_indeed_listing"
    assert "api_key" in out["error"]


def test_get_indeed_jobs_supports_legacy_top_level_has_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "has_data": {
                    "api_key": "HASDATA_TEST_KEY",
                    "base_url": "https://api.hasdata.com",
                    "timeout_sec": 12,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(path))
    clear_config_cache()

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "keyword=software+engineer" in url
        assert headers["x-api-key"] == "HASDATA_TEST_KEY"
        assert timeout_sec == 12
        return {
            "requestMetadata": {"status": "ok"},
            "searchInformation": {},
            "jobs": [],
            "pagination": {},
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = get_indeed_jobs(keyword="software engineer", location="Columbus, OH")
    assert out["ok"] is True


def test_get_indeed_jobs_success(configured_hasdata, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "/scrape/indeed/listing?" in url
        assert "keyword=software+engineer" in url
        assert "location=Columbus%2C+OH" in url
        assert "sort=date" in url
        assert "domain=www.indeed.com" in url
        assert headers["x-api-key"] == "HASDATA_TEST_KEY"
        assert timeout_sec == 12
        return {
            "requestMetadata": {"status": "ok", "id": "id_1"},
            "searchInformation": {"title": "Software engineer jobs in Columbus, OH"},
            "jobs": [{"title": "Software Engineer I"}],
            "pagination": {"nextPage": "https://www.indeed.com/m/jobs?..."},
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = get_indeed_jobs(keyword="software engineer", location="Columbus, OH")
    assert out["ok"] is True
    assert out["jobs_count"] == 1
    assert out["jobs"][0]["title"] == "Software Engineer I"
    assert out["queue"]["group"] == "hasdata"
    assert "wait_sec_last" in out["queue_stats"]
    assert out["error"] is None


def test_get_indeed_jobs_ignores_override_attempts(configured_hasdata, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "sort=date" in url
        assert "domain=www.indeed.com" in url
        assert "sort=relevance" not in url
        assert "domain=uk.indeed.com" not in url
        return {
            "requestMetadata": {"status": "ok"},
            "searchInformation": {},
            "jobs": [],
            "pagination": {},
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = get_indeed_jobs(
        keyword="software engineer",
        location="Columbus, OH",
        sort="relevance",
        domain="uk.indeed.com",
    )
    assert out["ok"] is True
    assert out["request"]["sort"] == "date"
    assert out["request"]["domain"] == "www.indeed.com"


def test_get_indeed_job_detail_validates_input(configured_hasdata):
    out = get_indeed_job_detail(url=" ")
    assert out["ok"] is False
    assert "url must be non-empty" in out["error"]


def test_get_indeed_job_detail_success(configured_hasdata, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "/scrape/indeed/job?" in url
        assert "url=https%3A%2F%2Fwww.indeed.com%2Fviewjob%3Fjk%3Dabc" in url
        assert headers["x-api-key"] == "HASDATA_TEST_KEY"
        assert timeout_sec == 12
        return {
            "requestMetadata": {"status": "ok", "id": "id_2"},
            "job": {"title": "Software Engineer", "company": "Google"},
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = get_indeed_job_detail(url="https://www.indeed.com/viewjob?jk=abc")
    assert out["ok"] is True
    assert out["job"]["company"] == "Google"
    assert out["queue"]["group"] == "hasdata"
    assert "wait_sec_last" in out["queue_stats"]
    assert out["error"] is None


def test_get_indeed_job_detail_error_path(configured_hasdata, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        raise RuntimeError("network fail")

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = get_indeed_job_detail(url="https://www.indeed.com/viewjob?jk=abc")
    assert out["ok"] is False
    assert out["source"] == "hasdata_indeed_job_error"
    assert "network fail" in out["error"]
