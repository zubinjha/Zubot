from src.zubot.core.job_applications_schema import (
    DB_COLUMNS,
    REQUIRED_SHEET_COLUMNS,
    SHEET_COLUMNS,
    db_row_to_sheet_row,
    normalize_sheet_row,
    sheet_row_to_db_row,
)


def test_sheet_and_db_columns_align_by_count():
    assert len(SHEET_COLUMNS) == len(DB_COLUMNS)
    assert len(SHEET_COLUMNS) == 13
    assert len(REQUIRED_SHEET_COLUMNS) > 0


def test_sheet_row_to_db_row_exact_mapping():
    sheet_row = {
        "JobKey": "k1",
        "Company": "Acme",
        "Job Title": "Engineer",
        "Location": "Remote",
        "Date Found": "2026-02-16",
        "Date Applied": "2026-02-17",
        "Status": "Applied",
        "Pay Range": "120k-140k",
        "Job Link": "https://example.com/job/1",
        "Source": "Indeed",
        "Cover Letter": "https://drive.google.com/file/d/1",
        "Notes": "follow up next week",
        "AI Notes": "fit_score=9",
    }
    db_row = sheet_row_to_db_row(sheet_row)
    assert db_row["job_key"] == "k1"
    assert db_row["job_title"] == "Engineer"
    assert db_row["date_found"] == "2026-02-16"
    assert db_row["job_link"] == "https://example.com/job/1"
    assert list(db_row.keys()) == list(DB_COLUMNS)


def test_db_row_to_sheet_row_exact_mapping():
    db_row = {
        "job_key": "k9",
        "company": "Beta",
        "job_title": "Data Scientist",
        "location": "Columbus, OH",
        "date_found": "2026-01-01",
        "date_applied": "",
        "status": "Recommend Apply",
        "pay_range": "",
        "job_link": "https://example.com/job/9",
        "source": "LinkedIn",
        "cover_letter": "",
        "notes": "",
        "ai_notes": "",
        "created_at": "2026-01-01T00:00:00Z",
    }
    sheet_row = db_row_to_sheet_row(db_row)
    assert sheet_row["JobKey"] == "k9"
    assert sheet_row["Job Title"] == "Data Scientist"
    assert sheet_row["Date Found"] == "2026-01-01"
    assert sheet_row["Job Link"] == "https://example.com/job/9"
    assert list(sheet_row.keys()) == list(SHEET_COLUMNS)


def test_normalize_sheet_row_fills_missing_with_empty_string():
    out = normalize_sheet_row({"JobKey": "k1", "Company": "Acme"})
    assert out["JobKey"] == "k1"
    assert out["Company"] == "Acme"
    assert out["Job Title"] == ""
    assert out["Notes"] == ""
    assert out["AI Notes"] == ""
