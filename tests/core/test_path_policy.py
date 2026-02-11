import pytest

from src.zubot.core.path_policy import (
    can_read,
    can_write,
    check_access,
    normalize_repo_path,
    resolve_repo_path,
)


def _policy_config() -> dict:
    return {
        "filesystem": {
            "default_access": "deny",
            "allow_read": ["**"],
            "allow_write": ["outputs/**"],
            "deny": ["config/config.json", ".git/**", ".venv/**"],
        }
    }


def test_resolve_repo_path_rejects_traversal():
    with pytest.raises(ValueError, match="traversal"):
        resolve_repo_path("../outside.txt")


def test_normalize_repo_path_relative():
    assert normalize_repo_path("docs/README.md") == "docs/README.md"
    assert normalize_repo_path(".") == "."


def test_access_policy_read_and_write():
    config = _policy_config()
    assert can_read("README.md", config=config)
    assert not can_read("config/config.json", config=config)
    assert can_write("outputs/test.txt", config=config)
    assert not can_write("README.md", config=config)

    allowed, reason = check_access("config/config.json", "read", config=config)
    assert not allowed
    assert "denied" in reason
