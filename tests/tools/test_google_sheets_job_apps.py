import importlib
import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.google_sheets_job_apps import (
    append_job_app_row,
    delete_job_app_row_by_key,
    list_job_app_rows,
)

module = importlib.import_module("src.zubot.tools.kernel.google_sheets_job_apps")


def _write_config(path: Path, payload: dict):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def configured_google_drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
                    },
                    "google_drive": {
                        "job_application_spreadsheet_id": "sheet-123",
                        "timeout_sec": 9,
                    },
                }
            }
        },
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()
    return True


def test_list_job_app_rows_invalid_date_filter(configured_google_drive):
    out = list_job_app_rows(start_date="13/35/2026")
    assert out["ok"] is False
    assert "Invalid date filter" in out["error"]


def test_list_job_app_rows_inclusive_date_filter_and_mapping(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "Job%20Applications!A1:L" in url
        assert headers["Authorization"] == "Bearer token"
        assert timeout_sec == 9
        return {
            "values": [
                [
                    "JobKey",
                    "Company",
                    "Job Title",
                    "Location",
                    "Date Found",
                    "Date Applied",
                    "Status",
                    "Pay Range",
                    "Job Link",
                    "Source",
                    "Cover Letter",
                    "Notes",
                ],
                ["k1", "A", "Role A", "Remote", "02/10/2026", "", "Recommend Apply", "", "u1", "Indeed"],
                ["k2", "B", "Role B", "Remote", "2026-02-11", "", "Applied", "", "u2", "LinkedIn"],
                ["k3", "C", "Role C", "Remote", "not-a-date", "", "Recommend Apply", "", "u3", "Site"],
                ["k4", "D", "Role D"],
            ]
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = list_job_app_rows(start_date="02/10/2026", end_date="2026-02-11")
    assert out["ok"] is True
    assert out["rows_count"] == 2
    assert out["rows"][0]["Date Found"] == "2026-02-10"
    assert out["rows"][1]["Date Found"] == "2026-02-11"
    assert out["filter"]["start_date"] == "2026-02-10"
    assert out["filter"]["end_date"] == "2026-02-11"


def test_list_job_app_rows_short_row_mapping(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})
    monkeypatch.setattr(
        module,
        "_fetch_json",
        lambda *args, **kwargs: {
            "values": [
                [
                    "JobKey",
                    "Company",
                    "Job Title",
                    "Location",
                    "Date Found",
                    "Date Applied",
                    "Status",
                    "Pay Range",
                    "Job Link",
                    "Source",
                    "Cover Letter",
                    "Notes",
                ],
                ["k1", "Company", "Role"],
            ]
        },
    )

    out = list_job_app_rows()
    assert out["ok"] is True
    assert out["rows_count"] == 1
    assert out["rows"][0]["Location"] == ""
    assert out["rows"][0]["Notes"] == ""


def test_append_job_app_row_status_validation(configured_google_drive):
    out = append_job_app_row(
        row={
            "JobKey": "abc",
            "Company": "Acme",
            "Job Title": "Engineer",
            "Location": "Remote",
            "Date Found": "2026-02-10",
            "Status": "In Process",
            "Job Link": "https://example.com/job",
            "Source": "Indeed",
        }
    )
    assert out["ok"] is False
    assert "row.Status" in out["error"]


def test_append_job_app_row_duplicate_job_key(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "Job%20Applications!A2:L" in url
        return {"values": [["existing-key", "", "Engineer"], ["other-key", "", "Analyst"]]}

    def fail_put(*args, **kwargs):
        raise AssertionError("write should not be called for duplicate key")

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(module, "_put_json", fail_put)

    out = append_job_app_row(
        row={
            "JobKey": "existing-key",
            "Company": "Acme",
            "Job Title": "Engineer",
            "Location": "Remote",
            "Date Found": "02/10/2026",
            "Status": "Recommend Apply",
            "Job Link": "https://example.com/job",
            "Source": "Indeed",
        }
    )
    assert out["ok"] is False
    assert "Duplicate JobKey" in out["error"]


def test_append_job_app_row_success(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "Job%20Applications!A2:L" in url
        return {
            "values": [
                ["", "", ""],
                ["other-key", "Other Co", "Other Title"],
            ]
        }

    def fake_put_json(url: str, headers: dict[str, str], payload: dict, timeout_sec: int):
        assert "Job%20Applications!A2:L2" in url
        assert payload["values"][0][0] == "new-key"
        assert payload["values"][0][4] == "2026-02-10"
        return {
            "updatedRange": "Job Applications!A2:L2",
            "updatedRows": 1,
        }

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(module, "_put_json", fake_put_json)

    out = append_job_app_row(
        row={
            "JobKey": "new-key",
            "Company": "Acme",
            "Job Title": "Engineer",
            "Location": "Remote",
            "Date Found": "02/10/2026",
            "Date Applied": "2026-02-11",
            "Status": "Recommend Apply",
            "Pay Range": "120k-150k",
            "Job Link": "https://example.com/job",
            "Source": "Indeed",
            "Cover Letter": "https://drive.google.com/file/d/123",
            "Notes": "first outreach",
        }
    )
    assert out["ok"] is True
    assert out["updated_rows"] == 1
    assert out["target_row"] == 2
    assert out["row"]["Date Found"] == "2026-02-10"


def test_list_job_app_rows_api_error(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def boom(*args, **kwargs):
        raise RuntimeError("network fail")

    monkeypatch.setattr(module, "_fetch_json", boom)
    out = list_job_app_rows()
    assert out["ok"] is False
    assert "Failed to read sheet rows" in out["error"]


def test_append_job_app_row_api_error(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})
    monkeypatch.setattr(module, "_fetch_json", lambda *args, **kwargs: {"values": [["k1", "Co", "Title"]]})

    def boom(*args, **kwargs):
        raise RuntimeError("network fail")

    monkeypatch.setattr(module, "_put_json", boom)

    out = append_job_app_row(
        row={
            "JobKey": "new-key",
            "Company": "Acme",
            "Job Title": "Engineer",
            "Location": "Remote",
            "Date Found": "2026-02-10",
            "Status": "Recommend Apply",
            "Job Link": "https://example.com/job",
            "Source": "Indeed",
        }
    )
    assert out["ok"] is False
    assert "Failed to write row" in out["error"]


def test_delete_job_app_row_by_key_requires_non_empty(configured_google_drive):
    out = delete_job_app_row_by_key(job_key=" ")
    assert out["ok"] is False
    assert "job_key must be non-empty" in out["error"]


def test_delete_job_app_row_by_key_not_found(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        assert "Job%20Applications!A2:A" in url
        return {"values": [["a"], ["b"]]}

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = delete_job_app_row_by_key(job_key="missing")
    assert out["ok"] is False
    assert "JobKey not found" in out["error"]


def test_delete_job_app_row_by_key_duplicate(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        return {"values": [["dup"], ["x"], ["dup"]]}

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = delete_job_app_row_by_key(job_key="dup")
    assert out["ok"] is False
    assert "Duplicate JobKey values found" in out["error"]


def test_delete_job_app_row_by_key_success(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        if "Job%20Applications!A2:A" in url:
            return {"values": [["k1"], ["k2"], ["k3"]]}
        if "fields=sheets(properties(sheetId,title))" in url:
            return {"sheets": [{"properties": {"sheetId": 777, "title": "Job Applications"}}]}
        raise AssertionError(f"unexpected URL: {url}")

    captured = {}

    def fake_post_json(url: str, headers: dict[str, str], payload: dict, timeout_sec: int):
        assert ":batchUpdate" in url
        captured["payload"] = payload
        return {"replies": [{}]}

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(module, "_post_json", fake_post_json)
    out = delete_job_app_row_by_key(job_key="k2")
    assert out["ok"] is True
    assert out["deleted_row_number"] == 3
    request = captured["payload"]["requests"][0]["deleteDimension"]["range"]
    assert request["sheetId"] == 777
    assert request["startIndex"] == 2
    assert request["endIndex"] == 3


def test_delete_job_app_row_by_key_metadata_error(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        if "Job%20Applications!A2:A" in url:
            return {"values": [["k1"]]}
        raise RuntimeError("metadata boom")

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    out = delete_job_app_row_by_key(job_key="k1")
    assert out["ok"] is False
    assert "Failed to read sheet metadata" in out["error"]


def test_delete_job_app_row_by_key_api_error(configured_google_drive, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "get_google_access_token", lambda: {"ok": True, "access_token": "token"})

    def fake_fetch_json(url: str, headers: dict[str, str], timeout_sec: int):
        if "Job%20Applications!A2:A" in url:
            return {"values": [["k1"]]}
        if "fields=sheets(properties(sheetId,title))" in url:
            return {"sheets": [{"properties": {"sheetId": 101, "title": "Job Applications"}}]}
        raise AssertionError(f"unexpected URL: {url}")

    def boom(*args, **kwargs):
        raise RuntimeError("delete fail")

    monkeypatch.setattr(module, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(module, "_post_json", boom)
    out = delete_job_app_row_by_key(job_key="k1")
    assert out["ok"] is False
    assert "Failed to delete row" in out["error"]
