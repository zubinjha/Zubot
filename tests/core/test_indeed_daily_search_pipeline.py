from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path
import zipfile

from src.zubot.predefined_tasks.indeed_daily_search import pipeline


def test_assemble_search_profiles_from_locations_and_keywords():
    cfg = {
        "search_locations": ["Columbus, OH", "Denver, CO"],
        "search_keywords": ["Software Engineer", "Data Engineer"],
    }
    out = pipeline._assemble_search_profiles(cfg)
    assert [item["location"] for item in out] == [
        "Columbus, OH",
        "Columbus, OH",
        "Denver, CO",
        "Denver, CO",
    ]
    assert [item["keyword"] for item in out] == [
        "Software Engineer",
        "Data Engineer",
        "Software Engineer",
        "Data Engineer",
    ]
    assert [item["profile_id"] for item in out] == [
        "software_engineer_columbus_oh",
        "data_engineer_columbus_oh",
        "software_engineer_denver_co",
        "data_engineer_denver_co",
    ]


def test_assemble_search_profiles_prefers_legacy_search_profiles():
    cfg = {
        "search_profiles": [
            {"profile_id": "manual_1", "keyword": "K1", "location": "L1"},
            {"profile_id": "manual_2", "keyword": "K2", "location": "L2"},
        ],
        "search_locations": ["Columbus, OH"],
        "search_keywords": ["Software Engineer"],
    }
    out = pipeline._assemble_search_profiles(cfg)
    assert out == cfg["search_profiles"]


def _context_bundle() -> pipeline.CandidateContextBundle:
    return pipeline.CandidateContextBundle(base_context={"user": "context"}, project_context={})


def _init_task_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE task_seen_items (
            task_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            item_key TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (task_id, provider, item_key)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE task_state_kv (
            task_id TEXT NOT NULL,
            state_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            PRIMARY KEY (task_id, state_key)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE job_discovery (
            task_id TEXT NOT NULL,
            job_key TEXT NOT NULL,
            found_at TEXT NOT NULL,
            decision TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (task_id, job_key)
        );
        """
    )
    conn.commit()
    conn.close()


def test_load_recent_seen_job_keys_empty(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == []


def test_search_fraction_formula_matches_expected():
    # Example requested behavior:
    # 0/5 + (1/5)*(x/15)
    f = pipeline._search_fraction(query_index=1, query_total=5, job_index=3, job_total=15)
    expected = 0.0 + (1.0 / 5.0) * (3.0 / 15.0)
    assert abs(f - expected) < 1e-9


def test_overall_fraction_starts_at_zero_when_total_jobs_unknown():
    out = pipeline._overall_fraction(search_fraction=0.0, processed_jobs=0, total_jobs=0)
    assert out == 0.0


def test_load_recent_seen_job_keys_applies_limit(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    conn = sqlite3.connect(db_path)
    base = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    for idx in range(260):
        stamp = (base + timedelta(minutes=idx)).isoformat()
        conn.execute(
            """
            INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
            VALUES (?, ?, ?, '{}', ?, ?, 1);
            """,
            ("indeed_daily_search", "indeed", f"job_{idx}", stamp, stamp),
        )
    conn.commit()
    conn.close()

    out = pipeline.load_recent_seen_job_keys(
        task_id="indeed_daily_search",
        provider="indeed",
        limit=200,
        db_path=db_path,
    )
    assert len(out) == 200
    assert out[0] == "job_259"
    assert out[-1] == "job_60"


def test_load_recent_seen_job_keys_under_limit_returns_all(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    conn = sqlite3.connect(db_path)
    for idx in range(3):
        conn.execute(
            """
            INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
            VALUES (?, ?, ?, '{}', ?, ?, 1);
            """,
            (
                "indeed_daily_search",
                "indeed",
                f"k{idx}",
                f"2026-02-01T00:0{idx}:00+00:00",
                "2026-02-01T00:00:00+00:00",
            ),
        )
    conn.commit()
    conn.close()
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == ["k2", "k1", "k0"]


def test_load_recent_seen_job_keys_uses_first_seen_not_last_seen(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
        VALUES (?, ?, ?, '{}', ?, ?, 1);
        """,
        (
            "indeed_daily_search",
            "indeed",
            "older_first_seen",
            "2026-02-01T00:00:00+00:00",
            "2026-02-02T00:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
        VALUES (?, ?, ?, '{}', ?, ?, 1);
        """,
        (
            "indeed_daily_search",
            "indeed",
            "newer_first_seen",
            "2026-02-01T01:00:00+00:00",
            "2026-02-01T01:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == ["newer_first_seen", "older_first_seen"]


def test_load_recent_seen_job_keys_handles_duplicates_by_primary_key_upsert(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    pipeline._mark_job_seen(
        task_id="indeed_daily_search",
        provider="indeed",
        job_key="dup_1",
        metadata={"a": 1},
        db_path=db_path,
    )
    pipeline._mark_job_seen(
        task_id="indeed_daily_search",
        provider="indeed",
        job_key="dup_1",
        metadata={"a": 2},
        db_path=db_path,
    )
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == ["dup_1"]


def test_load_recent_seen_job_keys_ignores_blank_item_keys(tmp_path: Path):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
        VALUES (?, ?, ?, '{}', ?, ?, 1);
        """,
        ("indeed_daily_search", "indeed", "valid_1", "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
        VALUES (?, ?, ?, '{}', ?, ?, 1);
        """,
        ("indeed_daily_search", "indeed", "   ", "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == ["valid_1"]


def test_collect_new_candidates_dedupes_seen_and_cross_query(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
        VALUES (?, ?, ?, '{}', ?, ?, 1);
        """,
        ("indeed_daily_search", "indeed", "seen_1", "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    responses = [
        {
            "ok": True,
            "jobs": [
                {"jobKey": "seen_1", "url": "https://www.indeed.com/viewjob?jk=seen_1"},
                {"jobKey": "new_1", "url": "https://www.indeed.com/viewjob?jk=new_1"},
                {"jobKey": "new_2", "url": "https://www.indeed.com/viewjob?jk=new_2"},
            ],
        },
        {
            "ok": True,
            "jobs": [
                {"jobKey": "new_2", "url": "https://www.indeed.com/viewjob?jk=new_2"},
                {"jobKey": "new_3", "url": "https://www.indeed.com/viewjob?jk=new_3"},
            ],
        },
    ]

    def fake_get_indeed_jobs(*, keyword: str, location: str):
        _ = keyword
        _ = location
        return responses.pop(0)

    monkeypatch.setattr(pipeline, "get_indeed_jobs", fake_get_indeed_jobs)

    candidates, stats, errors = pipeline._collect_new_candidates(
        task_id="indeed_daily_search",
        db_path=db_path,
        search_profiles=[
            {"profile_id": "a", "keyword": "Software Engineer", "location": "Columbus, OH"},
            {"profile_id": "b", "keyword": "Software Engineer", "location": "Denver, CO"},
        ],
        seen_limit=200,
        provider="indeed",
    )
    assert errors == []
    assert [item.job_key for item in candidates] == ["seen_1", "new_1", "new_2", "new_2", "new_3"]
    assert [item.is_new for item in candidates] == [False, True, True, False, True]
    assert stats["jobs_filtered_seen"] == 2
    assert stats["jobs_new_total"] == 3


def test_assemble_search_profiles_from_locations_and_keywords():
    profiles = pipeline._assemble_search_profiles(
        {
            "search_locations": ["Columbus, OH", "Columbus, OH", "Denver, CO"],
            "search_keywords": ["Software Engineer", "Data Engineer", ""],
        }
    )
    assert len(profiles) == 4
    assert [(item["location"], item["keyword"]) for item in profiles] == [
        ("Columbus, OH", "Software Engineer"),
        ("Columbus, OH", "Data Engineer"),
        ("Denver, CO", "Software Engineer"),
        ("Denver, CO", "Data Engineer"),
    ]
    assert len({item["profile_id"] for item in profiles}) == 4


def test_assemble_search_profiles_falls_back_to_legacy_profiles():
    profiles = pipeline._assemble_search_profiles(
        {
            "search_profiles": [
                {"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"},
                {"keyword": "Data Engineer", "location": "Denver, CO"},
                {"profile_id": "bad", "keyword": "", "location": "Remote"},
            ]
        }
    )
    assert len(profiles) == 2
    assert profiles[0]["profile_id"] == "p1"
    assert profiles[1]["keyword"] == "Data Engineer"
    assert profiles[1]["location"] == "Denver, CO"
    assert profiles[1]["profile_id"]


def test_evaluate_job_retries_after_invalid_payload(monkeypatch):
    responses = [
        {"ok": True, "payload": {"decision": "bad"}},
        {
            "ok": True,
            "payload": {
                "decision": "Recommend Maybe",
                "fit_score": 6,
                "rationale_short": "reason",
                "reasons": ["a"],
                "risks": ["b"],
                "missing_requirements": ["c"],
            },
        },
    ]

    monkeypatch.setattr(pipeline, "_llm_json_response", lambda **kwargs: responses.pop(0))
    out = pipeline._evaluate_job(
        model_alias="medium",
        job_listing={},
        job_detail={},
        candidate_context={},
        decision_rubric_text="rubric",
        invalid_retry_limit=1,
    )
    assert out["ok"] is True
    assert out["decision_payload"]["decision"] == "Recommend Maybe"


def test_evaluate_job_returns_error_after_retry_exhausted(monkeypatch):
    monkeypatch.setattr(pipeline, "_llm_json_response", lambda **kwargs: {"ok": False, "error": "invalid_json"})
    out = pipeline._evaluate_job(
        model_alias="medium",
        job_listing={},
        job_detail={},
        candidate_context={},
        decision_rubric_text="rubric",
        invalid_retry_limit=1,
    )
    assert out["ok"] is False
    assert "invalid_json" in out["error"]


def test_map_sheet_row_contract():
    row = pipeline._map_sheet_row(
        job_key="jk1",
        extracted_fields={
            "company": "Example Co",
            "job_title": "Software Engineer",
            "location": "Columbus, OH",
            "pay_range": "$100k-$120k",
            "job_link": "https://www.indeed.com/viewjob?jk=jk1",
        },
        decision_payload={
            "decision": pipeline.DECISION_RECOMMEND_APPLY,
            "fit_score": 8,
            "rationale_short": "Strong fit",
            "reasons": ["r1"],
            "risks": ["x"],
            "missing_requirements": ["y"],
        },
        date_found_iso="2026-02-17",
        cover_letter_link="https://drive.google.com/file/d/1",
    )
    assert row["JobKey"] == "jk1"
    assert row["Status"] == "Recommend Apply"
    assert row["Source"] == "Indeed"
    assert row["Cover Letter"] == "https://drive.google.com/file/d/1"
    assert row["Notes"] == ""
    assert "fit_score=8" in row["AI Notes"]


def test_run_pipeline_happy_path(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {
            "ok": True,
            "fields": {
                "company": "Acme",
                "job_title": "SE",
                "location": "Remote",
                "pay_range": "Not Found",
                "job_link": "https://www.indeed.com/viewjob?jk=jk1",
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Apply",
                "fit_score": 8,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1", "p2", "p3"]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": True, "web_view_link": "https://drive.google.com/file/d/1"})
    monkeypatch.setattr(pipeline, "append_job_app_row", lambda **kwargs: {"ok": True, "updated_rows": 1})
    progress_events: list[dict[str, object]] = []

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={
            "search_locations": ["Columbus, OH"],
            "search_keywords": ["Software Engineer"],
        },
        resources_dir=resources_dir,
        progress_callback=lambda item: progress_events.append(item),
    )
    assert out["ok"] is True
    assert out["counts"]["new_jobs"] == 1
    assert out["counts"]["sheet_rows_written"] == 1
    assert out["counts"]["recommended_apply"] == 1
    result_events = [event for event in progress_events if event.get("stage") == "process_result"]
    assert result_events
    status_line = str(result_events[-1].get("status_line") or "")
    assert "decision=Recommend Apply" in status_line
    assert "job_url=https://www.indeed.com/viewjob?jk=jk1" in status_line
    assert "cover_letter_local_path=" in status_line


def test_run_pipeline_decision_error_progress_includes_reason_and_url(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    progress_events: list[dict[str, object]] = []

    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {"ok": True, "fields": pipeline._default_sheet_fields()},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(pipeline, "_evaluate_job", lambda **kwargs: {"ok": False, "error": "invalid_json"})

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={"search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}]},
        resources_dir=resources_dir,
        progress_callback=lambda item: progress_events.append(item),
    )
    assert out["ok"] is True
    assert out["counts"]["decision_errors"] == 1
    result_events = [event for event in progress_events if event.get("stage") == "process_result"]
    assert result_events
    status_line = str(result_events[-1].get("status_line") or "")
    assert "decision=Skip" in status_line
    assert "outcome=decision_error" in status_line
    assert "job_url=https://www.indeed.com/viewjob?jk=jk1" in status_line
    assert "error=invalid_json" in status_line


def test_run_pipeline_partial_failure_upload_error(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {
            "ok": True,
            "fields": {
                "company": "Acme",
                "job_title": "SE",
                "location": "Remote",
                "pay_range": "Not Found",
                "job_link": "https://www.indeed.com/viewjob?jk=jk1",
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Apply",
                "fit_score": 8,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1", "p2", "p3"]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": False, "error": "upload fail"})
    monkeypatch.setattr(pipeline, "append_job_app_row", lambda **kwargs: {"ok": True, "updated_rows": 1})

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={
            "search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}],
        },
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert out["counts"]["upload_errors"] == 1
    assert out["counts"]["sheet_rows_written"] == 1


def test_run_pipeline_upload_missing_web_link_uses_drive_id_fallback(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    captured_rows: list[dict[str, str]] = []
    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {
            "ok": True,
            "fields": {
                "company": "Acme",
                "job_title": "SE",
                "location": "Remote",
                "pay_range": "Not Found",
                "job_link": "https://www.indeed.com/viewjob?jk=jk1",
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Apply",
                "fit_score": 8,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1 " * 60, "p2 " * 60, "p3 " * 60, "p4 " * 60]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": True, "drive_file_id": "drv_123", "web_view_link": ""})
    monkeypatch.setattr(
        pipeline,
        "append_job_app_row",
        lambda **kwargs: captured_rows.append(kwargs["row"]) or {"ok": True, "updated_rows": 1},
    )

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={"search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}]},
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert captured_rows
    assert captured_rows[0]["Cover Letter"] == "https://drive.google.com/file/d/drv_123/view"


def test_run_pipeline_upload_failure_marks_cover_letter_error_note(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    captured_rows: list[dict[str, str]] = []
    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {
            "ok": True,
            "fields": {
                "company": "Acme",
                "job_title": "SE",
                "location": "Remote",
                "pay_range": "Not Found",
                "job_link": "https://www.indeed.com/viewjob?jk=jk1",
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Apply",
                "fit_score": 8,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1 " * 60, "p2 " * 60, "p3 " * 60, "p4 " * 60]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": False, "error": "upload fail"})
    monkeypatch.setattr(
        pipeline,
        "append_job_app_row",
        lambda **kwargs: captured_rows.append(kwargs["row"]) or {"ok": True, "updated_rows": 1},
    )

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={"search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}]},
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert captured_rows
    assert captured_rows[0]["Notes"] == ""
    assert "cover_letter_error=upload_failed:" in captured_rows[0]["AI Notes"]


def test_run_pipeline_sheet_duplicate_counts_as_deduped(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(
        pipeline,
        "_extract_sheet_fields_via_llm",
        lambda **kwargs: {
            "ok": True,
            "fields": {
                "company": "Acme",
                "job_title": "SE",
                "location": "Remote",
                "pay_range": "Not Found",
                "job_link": "https://www.indeed.com/viewjob?jk=jk1",
            },
        },
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Maybe",
                "fit_score": 6,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1", "p2", "p3"]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": True, "web_view_link": "https://drive.google.com/file/d/1"})
    monkeypatch.setattr(pipeline, "append_job_app_row", lambda **kwargs: {"ok": False, "error": "Duplicate JobKey: jk1"})

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={
            "search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}],
        },
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert out["counts"]["sheet_rows_written"] == 0
    assert out["counts"]["sheet_rows_deduped"] == 1


def test_render_cover_letter_docx_writes_file(tmp_path: Path):
    out = tmp_path / "letter.docx"
    pipeline._render_cover_letter_docx(
        output_path=out,
        company_name="Stripe",
        body_paragraphs=["Paragraph one.", "Paragraph two.", "Paragraph three.", "Paragraph four."],
        contact_email="zubinkjha2025@gmail.com",
        linkedin_url="https://www.linkedin.com/in/zubin-jha-30752a355",
        linkedin_label="LinkedIn",
    )
    assert out.exists()
    assert out.stat().st_size > 0
    with zipfile.ZipFile(out, "r") as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        assert "LinkedIn" in xml
        assert "zubinkjha2025@gmail.com" in xml
        assert "zubinjha.com" not in xml


def test_run_pipeline_uses_not_found_defaults_when_extraction_fails(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    captured_rows: list[dict[str, str]] = []

    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(pipeline, "_load_candidate_context_bundle", lambda cfg: _context_bundle())
    monkeypatch.setattr(pipeline, "_assemble_candidate_context_for_job", lambda **kwargs: {"user": "context"})
    monkeypatch.setattr(pipeline, "_extract_sheet_fields_via_llm", lambda **kwargs: {"ok": False, "error": "invalid_json", "fields": pipeline._default_sheet_fields()})
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "url": "https://www.indeed.com/viewjob?jk=jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "get_indeed_job_detail",
        lambda **kwargs: {"ok": True, "job": {"description": "desc", "title": "SE", "companyName": "Acme"}},
    )
    monkeypatch.setattr(
        pipeline,
        "_evaluate_job",
        lambda **kwargs: {
            "ok": True,
            "decision_payload": {
                "decision": "Recommend Maybe",
                "fit_score": 6,
                "rationale_short": "good",
                "reasons": ["r1"],
                "risks": ["r2"],
                "missing_requirements": ["r3"],
            },
        },
    )
    monkeypatch.setattr(pipeline, "_generate_cover_letter", lambda **kwargs: {"ok": True, "paragraphs": ["p1", "p2", "p3"]})
    monkeypatch.setattr(pipeline, "_render_cover_letter_docx", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "upload_file_to_google_drive", lambda **kwargs: {"ok": True, "web_view_link": "https://drive.google.com/file/d/1"})
    monkeypatch.setattr(
        pipeline,
        "append_job_app_row",
        lambda **kwargs: captured_rows.append(kwargs["row"]) or {"ok": True, "updated_rows": 1},
    )

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={"search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}]},
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert out["counts"]["extraction_errors"] == 1
    assert captured_rows
    assert captured_rows[0]["Company"] == "Acme"
    assert captured_rows[0]["Job Title"] == "SE"
    assert captured_rows[0]["Location"] == "Remote"
    assert captured_rows[0]["Pay Range"] == "Not Found"
    assert captured_rows[0]["Job Link"] == "https://www.indeed.com/viewjob?jk=jk1"


def test_llm_json_response_extracts_embedded_json(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "call_llm",
        lambda **kwargs: {"ok": True, "text": "Sure, here you go:\n```json\n{\"decision\":\"Skip\",\"fit_score\":6}\n```"},
    )
    out = pipeline._llm_json_response(
        model_alias="medium",
        system_prompt="system",
        user_prompt="user",
    )
    assert out["ok"] is True
    assert out["payload"]["decision"] == "Skip"


def test_generate_cover_letter_fallback_when_llm_invalid(monkeypatch):
    monkeypatch.setattr(pipeline, "_llm_json_response", lambda **kwargs: {"ok": False, "error": "invalid_json"})
    out = pipeline._generate_cover_letter(
        model_alias="high",
        job_listing={"title": "Software Engineer", "companyName": "Acme"},
        job_detail={"job": {"description": "desc"}},
        candidate_context={"profile": "context"},
        style_spec_text="style",
        invalid_retry_limit=0,
    )
    assert out["ok"] is True
    assert out.get("fallback_used") is True
    paragraphs = out.get("paragraphs") or []
    assert len(paragraphs) >= 4
    assert sum(pipeline._word_count(item) for item in paragraphs) >= 220
    assert all("-" not in item for item in paragraphs)
    assert "Software Engineer" in paragraphs[0]


def test_generate_cover_letter_short_payload_triggers_fallback(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "_llm_json_response",
        lambda **kwargs: {"ok": True, "payload": {"paragraphs": ["too short", "short", "still short", "short"]}},
    )
    out = pipeline._generate_cover_letter(
        model_alias="high",
        job_listing={"title": "Software Engineer", "companyName": "Acme"},
        job_detail={"job": {"description": "desc"}},
        candidate_context={"profile": "context"},
        style_spec_text="style",
        invalid_retry_limit=0,
    )
    assert out["ok"] is True
    assert out.get("fallback_used") is True
    assert sum(pipeline._word_count(item) for item in (out.get("paragraphs") or [])) >= 220


def test_normalize_role_title_for_cover_letter():
    assert pipeline._normalize_role_title_for_cover_letter("APPLICATION DEVELOPER - INFORMATION TECHNOLOGY") == "Application Developer"
    assert pipeline._normalize_role_title_for_cover_letter("IS Systems Programmer II - Web & Mobile Dev") == "IS Systems Programmer II"


def test_generate_cover_letter_rewrites_raw_role_title_mentions(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "_llm_json_response",
        lambda **kwargs: {
            "ok": True,
            "payload": {
                "paragraphs": [
                    "I am excited to apply for APPLICATION DEVELOPER - INFORMATION TECHNOLOGY at Acme Company and contribute immediately with backend ownership, data focused execution, and practical product thinking. I have built full systems with APIs, data stores, and user facing workflows, and I am motivated by solving concrete problems that matter to users and teams.",
                    "My strongest work combines backend engineering, analytics, and clear implementation plans that move quickly from design to production. I regularly build and ship complete features, write maintainable code, and iterate based on feedback. This approach helps me contribute fast while still protecting quality, reliability, and long term maintainability across the systems I own.",
                    "I am drawn to this opportunity because it aligns with my builder mindset and my interest in software roles where measurable outcomes matter. I enjoy collaborating with technical and non technical partners, clarifying requirements, and translating goals into dependable software. I can bring a disciplined work style, strong curiosity, and consistent follow through from day one.",
                    "Thank you for considering my application. I would value the chance to discuss how my project experience and engineering approach can support your team goals. I am ready to contribute with focused execution, thoughtful communication, and a steady commitment to building systems that are both useful and reliable in real world use.",
                ]
            },
        },
    )
    out = pipeline._generate_cover_letter(
        model_alias="high",
        job_listing={"title": "APPLICATION DEVELOPER - INFORMATION TECHNOLOGY", "companyName": "Acme"},
        job_detail={"job": {"description": "desc"}},
        candidate_context={"profile": "context"},
        style_spec_text="style",
        invalid_retry_limit=0,
    )
    assert out["ok"] is True
    paragraphs = out.get("paragraphs") or []
    assert paragraphs
    assert "Application Developer" in paragraphs[0]
    assert "APPLICATION DEVELOPER - INFORMATION TECHNOLOGY" not in paragraphs[0]


def test_compact_file_segment_shortens_wordy_text():
    out = pipeline._compact_file_segment(
        "NATIONWIDE CHILDREN'S HOSPITAL INFORMATION TECHNOLOGY",
        fallback="Company",
        max_words=3,
        max_chars=24,
    )
    assert len(out) <= 24
    assert out


def test_next_available_local_docx_path_uses_numeric_suffix(tmp_path: Path):
    output_dir = tmp_path / "letters"
    output_dir.mkdir(parents=True)
    (output_dir / "2026-02-18 - Acme - Software Engineer.docx").write_text("x", encoding="utf-8")
    (output_dir / "2026-02-18 - Acme - Software Engineer - 1.docx").write_text("x", encoding="utf-8")

    out = pipeline._next_available_local_docx_path(
        output_dir=output_dir,
        base_name="2026-02-18 - Acme - Software Engineer",
        file_mode="versioned",
    )
    assert out.name == "2026-02-18 - Acme - Software Engineer - 2.docx"


def test_run_pipeline_skips_row_when_job_url_missing(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "core.db"
    _init_task_db(db_path)
    resources_dir = tmp_path / "indeed_daily_search"
    (resources_dir / "assets").mkdir(parents=True)
    (resources_dir / "assets" / "decision_rubric.md").write_text("rubric", encoding="utf-8")
    (resources_dir / "assets" / "cover_letter_style_spec.md").write_text("style", encoding="utf-8")

    captured_rows: list[dict[str, str]] = []
    monkeypatch.setattr(pipeline, "_db_path_from_config", lambda: db_path)
    monkeypatch.setattr(
        pipeline,
        "get_indeed_jobs",
        lambda **kwargs: {"ok": True, "jobs": [{"jobKey": "jk1", "title": "SE", "companyName": "Acme", "location": "Remote"}]},
    )
    monkeypatch.setattr(
        pipeline,
        "append_job_app_row",
        lambda **kwargs: captured_rows.append(kwargs["row"]) or {"ok": True, "updated_rows": 1},
    )

    out = pipeline.run_pipeline(
        task_id="indeed_daily_search",
        payload={"trigger": "manual"},
        local_config={"search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}]},
        resources_dir=resources_dir,
    )
    assert out["ok"] is True
    assert out["counts"]["sheet_rows_written"] == 0
    assert out["counts"]["decision_errors"] == 1
    assert captured_rows == []
