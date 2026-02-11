import json
import uuid
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.data.json_tools import read_json, write_json
from src.zubot.tools.data.text_search import search_text


@pytest.fixture()
def configured_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "filesystem": {
                    "default_access": "deny",
                    "allow_read": ["**"],
                    "allow_write": ["outputs/**"],
                    "deny": ["config/config.json", ".git/**", ".venv/**"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return True


@pytest.fixture(autouse=True)
def cleanup_generated_files():
    yield
    for path in Path("outputs/.tmp").glob("pytest-*"):
        if path.is_file():
            path.unlink()


def test_write_and_read_json(configured_policy):
    rel = f"outputs/.tmp/pytest-json-{uuid.uuid4().hex}.json"
    write_payload = write_json(rel, {"a": 1, "b": "two"}, create_parents=True)
    assert write_payload["ok"]

    read_payload = read_json(rel)
    assert read_payload["ok"]
    assert read_payload["data"]["a"] == 1
    assert read_payload["data"]["b"] == "two"


def test_search_text_finds_matches(configured_policy):
    rel = f"outputs/.tmp/pytest-search-{uuid.uuid4().hex}.txt"
    write_json(rel.replace(".txt", ".json"), {"note": "not used"}, create_parents=True)

    from src.zubot.tools.kernel.filesystem import write_file

    write_file(rel, "alpha\nbeta\ngamma beta", create_parents=True)
    result = search_text("beta", path_or_glob="outputs/.tmp/*.txt")
    assert result["ok"]
    assert len(result["matches"]) >= 2
