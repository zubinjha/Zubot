from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path

from src.zubot.predefined_tasks.indeed_daily_search import pipeline


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
            ("indeed_daily_search", "indeed", f"k{idx}", "2026-02-01T00:00:00+00:00", f"2026-02-01T00:0{idx}:00+00:00"),
        )
    conn.commit()
    conn.close()
    out = pipeline.load_recent_seen_job_keys(task_id="indeed_daily_search", db_path=db_path)
    assert out == ["k2", "k1", "k0"]


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
            "search_profiles": [{"profile_id": "p1", "keyword": "Software Engineer", "location": "Columbus, OH"}],
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
        body_paragraphs=["Paragraph one.", "Paragraph two.", "Paragraph three."],
    )
    assert out.exists()
    assert out.stat().st_size > 0


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
    assert captured_rows[0]["Company"] == "Not Found"
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
    assert len(out.get("paragraphs") or []) >= 3
