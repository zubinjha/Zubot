"""Canonical schema contract for job applications across Sheets and SQLite."""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_STATUS = "Recommend Apply"
ALLOWED_STATUS_VALUES = ("Recommend Apply", "Recommend Maybe", "Applied", "Interviewing", "Offer", "Rejected", "Closed")

SHEET_COLUMNS = (
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
)

REQUIRED_SHEET_COLUMNS = (
    "JobKey",
    "Company",
    "Job Title",
    "Location",
    "Date Found",
    "Status",
    "Job Link",
    "Source",
)

DB_TABLE_NAME = "job_applications"
DB_COLUMNS = (
    "job_key",
    "company",
    "job_title",
    "location",
    "date_found",
    "date_applied",
    "status",
    "pay_range",
    "job_link",
    "source",
    "cover_letter",
    "notes",
)
DB_METADATA_COLUMNS = (
    "created_at",
    "updated_at",
)

SHEET_TO_DB_COLUMN = {
    "JobKey": "job_key",
    "Company": "company",
    "Job Title": "job_title",
    "Location": "location",
    "Date Found": "date_found",
    "Date Applied": "date_applied",
    "Status": "status",
    "Pay Range": "pay_range",
    "Job Link": "job_link",
    "Source": "source",
    "Cover Letter": "cover_letter",
    "Notes": "notes",
}
DB_TO_SHEET_COLUMN = {db_col: sheet_col for sheet_col, db_col in SHEET_TO_DB_COLUMN.items()}


def normalize_sheet_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Return sheet row with canonical keys and string values."""
    out: dict[str, str] = {}
    for column in SHEET_COLUMNS:
        value = row.get(column, "") if isinstance(row, Mapping) else ""
        out[column] = str(value) if value is not None else ""
    return out


def sheet_row_to_db_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Map a sheet-shaped row object to DB column names."""
    normalized = normalize_sheet_row(row)
    out: dict[str, str] = {}
    for sheet_col in SHEET_COLUMNS:
        db_col = SHEET_TO_DB_COLUMN[sheet_col]
        out[db_col] = normalized[sheet_col]
    return out


def db_row_to_sheet_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Map a DB-shaped row object to sheet column names."""
    out: dict[str, str] = {}
    for db_col in DB_COLUMNS:
        sheet_col = DB_TO_SHEET_COLUMN[db_col]
        value = row.get(db_col, "") if isinstance(row, Mapping) else ""
        out[sheet_col] = str(value) if value is not None else ""
    return out
