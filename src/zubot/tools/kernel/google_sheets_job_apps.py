"""Google Sheets helpers for the Job Applications tracker."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.zubot.core.config_loader import load_config
from src.zubot.core.job_applications_schema import (
    ALLOWED_STATUS_VALUES as SCHEMA_ALLOWED_STATUS_VALUES,
    DEFAULT_STATUS as SCHEMA_DEFAULT_STATUS,
    REQUIRED_SHEET_COLUMNS,
    SHEET_COLUMNS,
    db_row_to_sheet_row,
    normalize_sheet_row,
    sheet_row_to_db_row,
)
from src.zubot.tools.kernel.google_auth import get_google_access_token

DEFAULT_TIMEOUT_SEC = 15
DEFAULT_SHEET_NAME = "Job Applications"
DEFAULT_STATUS = SCHEMA_DEFAULT_STATUS
ALLOWED_STATUS_VALUES = set(SCHEMA_ALLOWED_STATUS_VALUES)
COLUMNS = list(SHEET_COLUMNS)
REQUIRED_COLUMNS = list(REQUIRED_SHEET_COLUMNS)


def _column_letters(one_based_index: int) -> str:
    if one_based_index <= 0:
        raise ValueError("one_based_index must be >= 1")
    out: list[str] = []
    value = one_based_index
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        out.append(chr(ord("A") + remainder))
    return "".join(reversed(out))


def _sheet_row_range(*, start_row: int) -> str:
    end_col = _column_letters(len(COLUMNS))
    return f"{DEFAULT_SHEET_NAME}!A{start_row}:{end_col}"


def _sheet_single_row_range(*, row_number: int) -> str:
    end_col = _column_letters(len(COLUMNS))
    return f"{DEFAULT_SHEET_NAME}!A{row_number}:{end_col}{row_number}"


def _google_drive_settings() -> dict[str, Any]:
    try:
        payload = load_config()
    except (FileNotFoundError, ValueError):
        payload = {}

    config: dict[str, Any] = {}
    profiles = payload.get("tool_profiles")
    if isinstance(profiles, dict):
        user_specific = profiles.get("user_specific")
        if isinstance(user_specific, dict):
            block = user_specific.get("google_drive")
            if isinstance(block, dict):
                config = block

    spreadsheet_id = config.get("job_application_spreadsheet_id")
    timeout_sec = config.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
    return {
        "spreadsheet_id": spreadsheet_id if isinstance(spreadsheet_id, str) else None,
        "timeout_sec": int(timeout_sec),
    }


def _authorized_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _build_values_get_url(spreadsheet_id: str, range_name: str) -> str:
    encoded_sheet = quote(spreadsheet_id, safe="")
    encoded_range = quote(range_name, safe="!:$")
    return f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}/values/{encoded_range}"


def _build_values_append_url(spreadsheet_id: str, range_name: str) -> str:
    encoded_sheet = quote(spreadsheet_id, safe="")
    encoded_range = quote(range_name, safe="!:$")
    return (
        f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}/values/{encoded_range}:append"
        "?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )


def _build_values_update_url(spreadsheet_id: str, range_name: str) -> str:
    encoded_sheet = quote(spreadsheet_id, safe="")
    encoded_range = quote(range_name, safe="!:$")
    return f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}/values/{encoded_range}?valueInputOption=RAW"


def _build_batch_update_url(spreadsheet_id: str) -> str:
    encoded_sheet = quote(spreadsheet_id, safe="")
    return f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}:batchUpdate"


def _build_sheet_metadata_url(spreadsheet_id: str) -> str:
    encoded_sheet = quote(spreadsheet_id, safe="")
    return f"https://sheets.googleapis.com/v4/spreadsheets/{encoded_sheet}?fields=sheets(properties(sheetId,title))"


def _fetch_json(url: str, headers: dict[str, str], timeout_sec: int) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Google Sheets response must be a JSON object.")
    return payload


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    request_headers = dict(headers)
    request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Google Sheets response must be a JSON object.")
    return data


def _put_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    request_headers = dict(headers)
    request_headers["Content-Type"] = "application/json"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="PUT",
    )
    with urlopen(request, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Google Sheets response must be a JSON object.")
    return data


def _parse_human_date(raw_value: str) -> date:
    value = raw_value.strip()
    if not value:
        raise ValueError("date cannot be empty")
    if "/" in value:
        return datetime.strptime(value, "%m/%d/%Y").date()
    if "-" in value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError("date must be in YYYY-MM-DD or MM/DD/YYYY format")


def _normalize_date_string(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value:
        return None
    return _parse_human_date(value).isoformat()


def _row_values_to_dict(row_values: list[Any]) -> dict[str, str]:
    raw_row: dict[str, Any] = {}
    for index, column in enumerate(COLUMNS):
        raw_row[column] = row_values[index] if index < len(row_values) else ""
    mapped = normalize_sheet_row(raw_row)

    for field in ("Date Found", "Date Applied"):
        try:
            normalized = _normalize_date_string(mapped[field])
        except ValueError:
            normalized = None
        if normalized:
            mapped[field] = normalized
    return mapped


def _extract_job_keys(values_rows: list[list[Any]]) -> set[str]:
    keys: set[str] = set()
    for row in values_rows:
        if not row:
            continue
        value = row[0]
        key = str(value).strip() if value is not None else ""
        if key:
            keys.add(key)
    return keys


def _find_job_key_rows(values_rows: list[list[Any]], job_key: str) -> list[int]:
    matches: list[int] = []
    for idx, row in enumerate(values_rows):
        if not row:
            continue
        value = str(row[0]).strip() if row[0] is not None else ""
        if value == job_key:
            # Data starts at row 2 because A1 is header.
            matches.append(idx + 2)
    return matches


def _find_first_available_row(values_rows: list[list[Any]]) -> int:
    for idx, row in enumerate(values_rows, start=2):
        values = row if isinstance(row, list) else []
        job_key = str(values[0]).strip() if len(values) > 0 and values[0] is not None else ""
        job_title = str(values[2]).strip() if len(values) > 2 and values[2] is not None else ""
        if not job_key and not job_title:
            return idx
    return len(values_rows) + 2


def _get_sheet_id(payload: dict[str, Any], title: str) -> int | None:
    sheets = payload.get("sheets")
    if not isinstance(sheets, list):
        return None
    for item in sheets:
        if not isinstance(item, dict):
            continue
        props = item.get("properties")
        if not isinstance(props, dict):
            continue
        if props.get("title") != title:
            continue
        sheet_id = props.get("sheetId")
        if isinstance(sheet_id, int):
            return sheet_id
    return None


def _row_dict_to_sheet_values(row: dict[str, Any]) -> list[str]:
    normalized = normalize_sheet_row(row)
    output: list[str] = []
    for column in COLUMNS:
        value = normalized.get(column, "")
        output.append(str(value) if value is not None else "")
    return output


def _error_payload(source: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source": source,
        "error": message,
    }


def list_job_app_rows(*, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    source = "google_sheets_job_apps_list"
    settings = _google_drive_settings()
    spreadsheet_id = settings["spreadsheet_id"]
    if not spreadsheet_id:
        return _error_payload(source, "Missing tool_profiles.user_specific.google_drive.job_application_spreadsheet_id.")

    try:
        start_iso = _normalize_date_string(start_date)
        end_iso = _normalize_date_string(end_date)
    except ValueError as exc:
        return _error_payload(source, f"Invalid date filter: {exc}")

    if start_iso and end_iso and start_iso > end_iso:
        return _error_payload(source, "start_date must be less than or equal to end_date.")

    token = get_google_access_token()
    if not token.get("ok"):
        return {
            "ok": False,
            "source": source,
            "error": f"Google auth failed: {token.get('error')}",
        }

    url = _build_values_get_url(spreadsheet_id, _sheet_row_range(start_row=1))
    try:
        payload = _fetch_json(url, _authorized_headers(str(token["access_token"])), settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to read sheet rows: {exc}")

    values = payload.get("values")
    values_rows = values if isinstance(values, list) else []
    data_rows = values_rows[1:] if values_rows else []

    mapped_rows: list[dict[str, str]] = []
    for raw_row in data_rows:
        row_values = raw_row if isinstance(raw_row, list) else []
        mapped = _row_values_to_dict(row_values)

        try:
            date_found_iso = _normalize_date_string(mapped.get("Date Found"))
        except ValueError:
            date_found_iso = None
        if start_iso or end_iso:
            if date_found_iso is None:
                continue
            if start_iso and date_found_iso < start_iso:
                continue
            if end_iso and date_found_iso > end_iso:
                continue
        mapped_rows.append(db_row_to_sheet_row(sheet_row_to_db_row(mapped)))

    return {
        "ok": True,
        "source": source,
        "rows": mapped_rows,
        "rows_count": len(mapped_rows),
        "filter": {
            "date_field": "Date Found",
            "start_date": start_iso,
            "end_date": end_iso,
        },
        "error": None,
    }


def append_job_app_row(*, row: dict[str, Any]) -> dict[str, Any]:
    source = "google_sheets_job_apps_append"
    if not isinstance(row, dict):
        return _error_payload(source, "row must be an object.")

    normalized_row: dict[str, Any] = normalize_sheet_row(row)
    for required in REQUIRED_COLUMNS:
        value = normalized_row.get(required)
        if not isinstance(value, str) or not value.strip():
            return _error_payload(source, f"row.{required} must be non-empty.")

    try:
        normalized_row["Date Found"] = _normalize_date_string(str(normalized_row.get("Date Found"))) or ""
        date_applied = _normalize_date_string(str(normalized_row.get("Date Applied", "")))
        normalized_row["Date Applied"] = date_applied or ""
    except ValueError as exc:
        return _error_payload(source, f"Invalid row date: {exc}")

    status = str(normalized_row.get("Status") or "").strip() or DEFAULT_STATUS
    if status not in ALLOWED_STATUS_VALUES:
        return _error_payload(
            source,
            "row.Status must be one of: Recommend Apply, Recommend Maybe, Applied, Interviewing, Offer, Rejected, Closed.",
        )
    normalized_row["Status"] = status

    settings = _google_drive_settings()
    spreadsheet_id = settings["spreadsheet_id"]
    if not spreadsheet_id:
        return _error_payload(source, "Missing tool_profiles.user_specific.google_drive.job_application_spreadsheet_id.")

    token = get_google_access_token()
    if not token.get("ok"):
        return {
            "ok": False,
            "source": source,
            "error": f"Google auth failed: {token.get('error')}",
        }

    headers = _authorized_headers(str(token["access_token"]))

    rows_url = _build_values_get_url(spreadsheet_id, _sheet_row_range(start_row=2))
    try:
        rows_payload = _fetch_json(rows_url, headers, settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to read existing rows: {exc}")

    values = rows_payload.get("values")
    values_rows = values if isinstance(values, list) else []
    existing_keys = _extract_job_keys(values_rows)
    new_key = str(normalized_row["JobKey"]).strip()
    if new_key in existing_keys:
        return _error_payload(source, f"Duplicate JobKey: {new_key}")

    target_row = _find_first_available_row(values_rows)
    update_url = _build_values_update_url(spreadsheet_id, _sheet_single_row_range(row_number=target_row))
    body = {"values": [_row_dict_to_sheet_values(normalized_row)]}

    try:
        write_payload = _put_json(update_url, headers, body, settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to write row: {exc}")

    updated_rows = write_payload.get("updatedRows")
    updated_range = write_payload.get("updatedRange")

    return {
        "ok": True,
        "source": source,
        "updated_range": updated_range,
        "updated_rows": updated_rows,
        "target_row": target_row,
        "row": db_row_to_sheet_row(sheet_row_to_db_row(normalized_row)),
        "error": None,
    }


def delete_job_app_row_by_key(*, job_key: str) -> dict[str, Any]:
    source = "google_sheets_job_apps_delete"
    key = job_key.strip()
    if not key:
        return _error_payload(source, "job_key must be non-empty.")

    settings = _google_drive_settings()
    spreadsheet_id = settings["spreadsheet_id"]
    if not spreadsheet_id:
        return _error_payload(source, "Missing tool_profiles.user_specific.google_drive.job_application_spreadsheet_id.")

    token = get_google_access_token()
    if not token.get("ok"):
        return {
            "ok": False,
            "source": source,
            "error": f"Google auth failed: {token.get('error')}",
        }

    headers = _authorized_headers(str(token["access_token"]))
    keys_url = _build_values_get_url(spreadsheet_id, f"{DEFAULT_SHEET_NAME}!A2:A")
    try:
        key_payload = _fetch_json(keys_url, headers, settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to read existing JobKey values: {exc}")

    values = key_payload.get("values")
    rows = values if isinstance(values, list) else []
    matches = _find_job_key_rows(rows, key)
    if not matches:
        return _error_payload(source, f"JobKey not found: {key}")
    if len(matches) > 1:
        return _error_payload(source, f"Duplicate JobKey values found for delete: {key}")

    metadata_url = _build_sheet_metadata_url(spreadsheet_id)
    try:
        metadata_payload = _fetch_json(metadata_url, headers, settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to read sheet metadata: {exc}")

    sheet_id = _get_sheet_id(metadata_payload, DEFAULT_SHEET_NAME)
    if sheet_id is None:
        return _error_payload(source, f"Sheet tab not found: {DEFAULT_SHEET_NAME}")

    row_number = matches[0]
    delete_body = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number,
                    }
                }
            }
        ]
    }
    delete_url = _build_batch_update_url(spreadsheet_id)
    try:
        _post_json(delete_url, headers, delete_body, settings["timeout_sec"])
    except Exception as exc:
        return _error_payload(source, f"Failed to delete row: {exc}")

    return {
        "ok": True,
        "source": source,
        "job_key": key,
        "deleted_row_number": row_number,
        "error": None,
    }
