import importlib
import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.google_drive_docs import create_and_upload_docx, create_local_docx, upload_file_to_google_drive

module = importlib.import_module("src.zubot.tools.kernel.google_drive_docs")


def _write_config(path: Path, payload: dict):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def configured_google(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "tool_profiles": {
                "user_specific": {
                    "google_oauth": {
                        "token_path": str(tmp_path / "google_token.json"),
                        "client_id": "id",
                        "client_secret": "secret",
                        "refresh_token": "refresh",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "scopes": [
                            "https://www.googleapis.com/auth/spreadsheets",
                            "https://www.googleapis.com/auth/drive.file",
                        ],
                    },
                    "google_drive": {
                        "job_application_spreadsheet_id": "sheet-123",
                        "default_upload_path": "Job Applications/Cover Letters",
                        "timeout_sec": 9,
                    },
                }
            }
        },
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return True


def test_create_local_docx_success(configured_google):
    output_dir = "outputs/test_google_drive_docs"
    out = create_local_docx(
        filename="cover-letter",
        title="My Title",
        paragraphs=["Hello.", "World."],
        output_dir=output_dir,
    )
    assert out["ok"] is True
    assert out["filename"] == "cover-letter.docx"
    assert out["bytes_written"] and out["bytes_written"] > 0
    assert (Path(module._repo_root()) / out["local_path"]).exists()


def test_create_local_docx_validates_inputs(configured_google):
    a = create_local_docx(filename="x", paragraphs=[])
    b = create_local_docx(filename=" ", paragraphs=["text"])
    c = create_local_docx(filename="x", paragraphs=[""])
    assert a["ok"] is False and "paragraphs" in a["error"]
    assert b["ok"] is False and "filename" in b["error"]
    assert c["ok"] is False and "paragraphs" in c["error"]


def test_create_local_docx_default_output_dir(configured_google, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(module, "_repo_root", lambda: tmp_path)
    out = create_local_docx(filename="default-path", title=None, paragraphs=["line"])
    assert out["ok"] is True
    assert out["local_path"].startswith("outputs/cover_letters/")
    assert (tmp_path / out["local_path"]).exists()


def test_resolve_or_create_folder_path_existing(configured_google, monkeypatch: pytest.MonkeyPatch):
    calls = {"create": 0}

    def fake_find(*, access_token: str, parent_id: str, folder_name: str, timeout_sec: int):
        if folder_name == "Job Applications":
            return "folder-a"
        if folder_name == "Cover Letters":
            return "folder-b"
        return None

    def fake_create(*, access_token: str, parent_id: str, folder_name: str, timeout_sec: int):
        calls["create"] += 1
        return "created"

    monkeypatch.setattr(module, "_find_child_folder_id", fake_find)
    monkeypatch.setattr(module, "_create_folder", fake_create)

    out = module._resolve_or_create_folder_path(
        access_token="tok",
        path="Job Applications/Cover Letters",
        timeout_sec=9,
    )
    assert out == "folder-b"
    assert calls["create"] == 0


def test_resolve_or_create_folder_path_creates_missing(configured_google, monkeypatch: pytest.MonkeyPatch):
    created = []

    def fake_find(*, access_token: str, parent_id: str, folder_name: str, timeout_sec: int):
        return None

    def fake_create(*, access_token: str, parent_id: str, folder_name: str, timeout_sec: int):
        created.append((parent_id, folder_name))
        return f"id-{len(created)}"

    monkeypatch.setattr(module, "_find_child_folder_id", fake_find)
    monkeypatch.setattr(module, "_create_folder", fake_create)

    out = module._resolve_or_create_folder_path(
        access_token="tok",
        path="Job Applications/Cover Letters",
        timeout_sec=9,
    )
    assert out == "id-2"
    assert created == [("root", "Job Applications"), ("id-1", "Cover Letters")]


def test_upload_file_to_google_drive_success(configured_google, monkeypatch: pytest.MonkeyPatch):
    local_file = Path(module._repo_root()) / "outputs/test_google_drive_docs/upload-success.docx"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"dummy")

    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})
    monkeypatch.setattr(module, "_resolve_or_create_folder_path", lambda **kwargs: "folder-123")
    monkeypatch.setattr(module, "_file_exists_in_folder", lambda **kwargs: False)

    def fake_upload_multipart(**kwargs):
        assert kwargs["metadata"]["name"] == "upload-success.docx"
        return {"id": "drive-file-1", "name": "upload-success.docx", "webViewLink": "https://drive.google.com/x"}

    monkeypatch.setattr(module, "_upload_multipart", fake_upload_multipart)

    out = upload_file_to_google_drive(
        local_path=str(local_file.relative_to(module._repo_root())),
        destination_path="Job Applications/Cover Letters",
    )
    assert out["ok"] is True
    assert out["drive_file_id"] == "drive-file-1"
    assert out["destination_folder_id"] == "folder-123"


def test_upload_file_to_google_drive_name_conflict_adds_suffix(
    configured_google,
    monkeypatch: pytest.MonkeyPatch,
):
    local_file = Path(module._repo_root()) / "outputs/test_google_drive_docs/upload-conflict.docx"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"dummy")

    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})
    monkeypatch.setattr(module, "_resolve_or_create_folder_path", lambda **kwargs: "folder-123")
    monkeypatch.setattr(module, "_file_exists_in_folder", lambda **kwargs: True)
    monkeypatch.setattr(module, "_with_timestamp_suffix", lambda name: "file-20260212-111111.docx")

    def fake_upload_multipart(**kwargs):
        assert kwargs["metadata"]["name"] == "file-20260212-111111.docx"
        return {"id": "drive-file-2", "name": "file-20260212-111111.docx"}

    monkeypatch.setattr(module, "_upload_multipart", fake_upload_multipart)

    out = upload_file_to_google_drive(
        local_path=str(local_file.relative_to(module._repo_root())),
        destination_path="Job Applications/Cover Letters",
    )
    assert out["ok"] is True
    assert out["drive_file_name"] == "file-20260212-111111.docx"


def test_upload_file_to_google_drive_upload_error(configured_google, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    local_file = Path(module._repo_root()) / "outputs/test_google_drive_docs/upload-error.docx"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"dummy")

    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})
    monkeypatch.setattr(module, "_resolve_or_create_folder_path", lambda **kwargs: "folder-123")
    monkeypatch.setattr(module, "_file_exists_in_folder", lambda **kwargs: False)

    def boom(**kwargs):
        raise RuntimeError("upload fail")

    monkeypatch.setattr(module, "_upload_multipart", boom)

    out = upload_file_to_google_drive(
        local_path=str(local_file.relative_to(module._repo_root())),
        destination_path="Job Applications/Cover Letters",
    )
    assert out["ok"] is False
    assert out["source"] == "google_drive_upload_error"
    assert "upload fail" in out["error"]


def test_upload_file_to_google_drive_path_resolve_error(configured_google, monkeypatch: pytest.MonkeyPatch):
    local_file = Path(module._repo_root()) / "outputs/test_google_drive_docs/upload-path-error.docx"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"dummy")

    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def boom(**kwargs):
        raise RuntimeError("resolve fail")

    monkeypatch.setattr(module, "_resolve_or_create_folder_path", boom)
    out = upload_file_to_google_drive(local_path=str(local_file.relative_to(module._repo_root())))
    assert out["ok"] is False
    assert out["source"] == "google_drive_path_resolve_error"
    assert "resolve fail" in out["error"]


def test_upload_file_to_google_drive_auth_error(configured_google, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    local_file = Path(module._repo_root()) / "outputs/test_google_drive_docs/upload-auth-error.docx"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"dummy")

    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": False, "error": "bad auth"})
    out = upload_file_to_google_drive(local_path=str(local_file.relative_to(module._repo_root())))
    assert out["ok"] is False
    assert out["source"] == "google_drive_upload_error"
    assert "bad auth" in out["error"]


def test_create_and_upload_docx_success(configured_google, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        module,
        "create_local_docx",
        lambda **kwargs: {
            "ok": True,
            "source": "google_docx_local",
            "local_path": "outputs/cover_letters/x.docx",
            "filename": "x.docx",
            "bytes_written": 12,
            "error": None,
        },
    )
    monkeypatch.setattr(
        module,
        "upload_file_to_google_drive",
        lambda **kwargs: {
            "ok": True,
            "source": "google_drive_upload",
            "drive_file_id": "id-1",
            "drive_file_name": "x.docx",
            "destination_folder_id": "folder-1",
            "web_view_link": "link",
            "error": None,
        },
    )

    out = create_and_upload_docx(filename="x", title="t", paragraphs=["p"])
    assert out["ok"] is True
    assert out["local"]["ok"] is True
    assert out["upload"]["ok"] is True


def test_create_and_upload_docx_local_failure(configured_google, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "create_local_docx", lambda **kwargs: {"ok": False, "error": "local fail"})
    out = create_and_upload_docx(filename="x", title="t", paragraphs=["p"])
    assert out["ok"] is False
    assert out["upload"] is None
    assert "local fail" in out["error"]


def test_create_and_upload_docx_upload_failure(configured_google, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        module,
        "create_local_docx",
        lambda **kwargs: {"ok": True, "local_path": "outputs/cover_letters/x.docx", "filename": "x.docx", "error": None},
    )
    monkeypatch.setattr(
        module,
        "upload_file_to_google_drive",
        lambda **kwargs: {"ok": False, "source": "google_drive_upload_error", "error": "upload fail"},
    )

    out = create_and_upload_docx(filename="x", title="t", paragraphs=["p"])
    assert out["ok"] is False
    assert out["local"]["ok"] is True
    assert out["upload"]["ok"] is False
    assert "upload fail" in out["error"]
