import json
import uuid
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.filesystem import (
    append_file,
    list_dir,
    path_exists,
    read_file,
    stat_path,
    write_file,
)


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


@pytest.fixture()
def sandbox_path() -> str:
    rel = f"outputs/.tmp/pytest-fs-{uuid.uuid4().hex}.txt"
    return rel


@pytest.fixture(autouse=True)
def cleanup_generated_files():
    yield
    for path in Path("outputs/.tmp").glob("pytest-*"):
        if path.is_file():
            path.unlink()


def test_read_file_denied_for_sensitive_config(configured_policy):
    payload = read_file("config/config.json")
    assert not payload["ok"]
    assert "denied" in payload["error"]


def test_write_read_append_cycle(configured_policy, sandbox_path: str):
    written = write_file(sandbox_path, "hello", create_parents=True)
    assert written["ok"]
    assert written["path"] == sandbox_path

    appended = append_file(sandbox_path, "\nworld")
    assert appended["ok"]

    read_back = read_file(sandbox_path)
    assert read_back["ok"]
    assert read_back["content"] == "hello\nworld"


def test_list_dir_and_stat(configured_policy, sandbox_path: str):
    write_file(sandbox_path, "abc", create_parents=True)

    listing = list_dir("outputs/.tmp")
    assert listing["ok"]
    assert isinstance(listing["entries"], list)

    st = stat_path(sandbox_path)
    assert st["ok"]
    assert st["stat"]["is_file"] is True
    assert st["stat"]["size_bytes"] >= 3


def test_path_exists(configured_policy, sandbox_path: str):
    assert path_exists(sandbox_path)["exists"] is False
    write_file(sandbox_path, "x", create_parents=True)
    exists_payload = path_exists(sandbox_path)
    assert exists_payload["ok"]
    assert exists_payload["exists"] is True
