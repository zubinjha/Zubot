import importlib
import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.web_search import web_search

web_search_module = importlib.import_module("src.zubot.tools.kernel.web_search")


@pytest.fixture()
def configured_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "web_search": {
                    "provider": "brave",
                    "base_url": "https://api.search.brave.com/res/v1/web/search",
                    "brave_api_key": "TEST_KEY",
                    "timeout_sec": 5,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return True


def test_web_search_requires_non_empty_query(configured_search):
    result = web_search("  ")
    assert not result["ok"]
    assert "non-empty" in result["error"]


def test_web_search_missing_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"web_search": {}}), encoding="utf-8")
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()

    result = web_search("hello")
    assert not result["ok"]
    assert result["source"] == "config_missing"


def test_web_search_success_normalizes_results(configured_search, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "q=python" in url
        assert headers["X-Subscription-Token"] == "TEST_KEY"
        return {
            "web": {
                "results": [
                    {
                        "title": "Python",
                        "url": "https://www.python.org/",
                        "description": "Official Python website.",
                        "age": "2026-02-01",
                        "language": "en",
                    }
                ]
            }
        }

    monkeypatch.setattr(web_search_module, "_fetch_json", fake_fetch_json)
    result = web_search("python", count=3)
    assert result["ok"]
    assert result["error"] is None
    assert result["source"] == "brave_api"
    assert result["results"][0]["url"] == "https://www.python.org/"


def test_web_search_api_error(configured_search, monkeypatch):
    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        raise RuntimeError("network fail")

    monkeypatch.setattr(web_search_module, "_fetch_json", fake_fetch_json)
    result = web_search("python")
    assert not result["ok"]
    assert result["source"] == "brave_api_error"
    assert "network fail" in result["error"]
