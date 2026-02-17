"""Indeed daily search task pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from time import sleep
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.zubot.core.config_loader import get_central_service_config, load_config
from src.zubot.core.llm_client import call_llm
from src.zubot.core.task_scheduler_store import resolve_scheduler_db_path
from src.zubot.tools.kernel.google_drive_docs import upload_file_to_google_drive
from src.zubot.tools.kernel.google_sheets_job_apps import append_job_app_row
from src.zubot.tools.kernel.hasdata_indeed import get_indeed_job_detail, get_indeed_jobs

DECISION_RECOMMEND_APPLY = "Recommend Apply"
DECISION_RECOMMEND_MAYBE = "Recommend Maybe"
DECISION_SKIP = "Skip"
ALLOWED_DECISIONS = {DECISION_RECOMMEND_APPLY, DECISION_RECOMMEND_MAYBE, DECISION_SKIP}

DEFAULT_SEEN_LIMIT = 200
DEFAULT_PROVIDER = "indeed"
NOT_FOUND_VALUE = "Not Found"
DEFAULT_EXTRACTION_MODEL = "medium"
DEFAULT_DECISION_MODEL = "medium"
DEFAULT_COVER_LETTER_MODEL = "high"
DEFAULT_COVER_LETTER_DESTINATION_PATH = "Job Applications/Cover Letters"
DEFAULT_FILE_MODE = "versioned"
DEFAULT_SHEET_RETRY_ATTEMPTS = 2
DEFAULT_SHEET_RETRY_BACKOFF_SEC = 1.0
DEFAULT_PROJECT_CONTEXT_TOP_N = 3
DEFAULT_PROJECT_CONTEXT_MAX_CHARS = 2600

DEFAULT_CANDIDATE_CONTEXT_FILES = [
    "context/USER.md",
    "context/more-about-human/README.md",
    "context/more-about-human/professional_profile.md",
    "context/more-about-human/passion_profile.md",
    "context/more-about-human/writing_voice.md",
    "context/more-about-human/resume.md",
    "context/more-about-human/more-about-me.md",
    "context/more-about-human/cover_letter_brain.json",
    "context/more-about-human/projects/project_index.md",
]

_KEY_CHARS_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _extract_from_dict(payload: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = payload.get(key)
        text = _coerce_text(value)
        if text:
            return text
    return ""


def _extract_job_key(job: dict[str, Any]) -> str:
    explicit = _extract_from_dict(job, ["jobKey", "jobkey", "job_key", "key", "id"])
    if explicit:
        return explicit
    url = _extract_job_url(job)
    if url:
        try:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            jk = query.get("jk")
            if isinstance(jk, list) and jk and _coerce_text(jk[0]):
                return _coerce_text(jk[0])
        except Exception:
            pass
    material = "|".join(
        [
            _extract_job_title(job),
            _extract_job_company(job),
            _extract_job_location(job),
            url,
        ]
    )
    if not material.strip():
        material = json.dumps(job, ensure_ascii=True, sort_keys=True)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:20]


def _extract_job_url(job: dict[str, Any]) -> str:
    return _extract_from_dict(job, ["url", "jobUrl", "job_url", "jobLink", "link"])


def _extract_job_title(job: dict[str, Any]) -> str:
    return _extract_from_dict(job, ["title", "jobTitle", "job_title", "position"])


def _extract_job_company(job: dict[str, Any]) -> str:
    company = job.get("company")
    if isinstance(company, dict):
        nested = _extract_from_dict(company, ["name", "companyName"])
        if nested:
            return nested
    return _extract_from_dict(job, ["companyName", "company", "company_name", "employer"])


def _extract_job_location(job: dict[str, Any]) -> str:
    location = job.get("location")
    if isinstance(location, dict):
        nested = _extract_from_dict(location, ["formattedAddress", "displayName", "name"])
        if nested:
            return nested
    return _extract_from_dict(job, ["location", "jobLocation", "formattedLocation", "cityState"])


def _sanitize_file_stem(value: str) -> str:
    clean = _KEY_CHARS_PATTERN.sub("_", value.strip())
    return clean.strip("._")[:96] or "cover_letter"


def _repo_relative_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(_repo_root())
    except Exception:
        return Path("outputs") / "cover_letters" / "indeed_daily_search"


def _read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _context_key_from_path(path: Path, prefix: str = "context") -> str:
    stem = _KEY_CHARS_PATTERN.sub("_", path.stem).strip("_").lower() or "item"
    return f"{prefix}_{stem}"


def _normalize_not_found(value: Any) -> str:
    text = _coerce_text(value)
    return text if text else NOT_FOUND_VALUE


@dataclass(frozen=True)
class CandidateContextBundle:
    base_context: dict[str, str]
    project_context: dict[str, str]


def _read_relative_context_file(path_text: str) -> tuple[str, str] | None:
    rel = _coerce_text(path_text)
    if not rel:
        return None
    path = _repo_root() / rel
    content = _read_text_if_exists(path)
    if not content:
        return None
    return _context_key_from_path(path), content


def _load_candidate_context_bundle(cfg: dict[str, Any]) -> CandidateContextBundle:
    root = _repo_root()
    configured_files = cfg.get("candidate_context_files")
    context_files = [item for item in configured_files if isinstance(item, str)] if isinstance(configured_files, list) else list(DEFAULT_CANDIDATE_CONTEXT_FILES)

    base_context: dict[str, str] = {}
    for item in context_files:
        loaded = _read_relative_context_file(item)
        if loaded is None:
            continue
        key, content = loaded
        base_context[key] = content

    configured_project_files = cfg.get("project_context_files")
    project_context_files = [item for item in configured_project_files if isinstance(item, str)] if isinstance(configured_project_files, list) else []
    if not project_context_files:
        default_project_dir = root / "context" / "more-about-human" / "projects"
        if default_project_dir.exists():
            for path in sorted(default_project_dir.glob("*.md")):
                if path.name.lower() == "project_index.md":
                    continue
                project_context_files.append(str(path.relative_to(root).as_posix()))

    project_context: dict[str, str] = {}
    for item in project_context_files:
        loaded = _read_relative_context_file(item)
        if loaded is None:
            continue
        _, content = loaded
        key = f"project_{Path(item).stem}"
        project_context[key] = content
    return CandidateContextBundle(base_context=base_context, project_context=project_context)


def _tokenize_for_scoring(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


def _select_project_context_for_job(
    *,
    bundle: CandidateContextBundle,
    job_listing: dict[str, Any],
    job_detail: dict[str, Any],
    top_n: int,
    max_chars_per_project: int,
) -> dict[str, str]:
    if not bundle.project_context:
        return {}
    safe_top_n = max(0, int(top_n))
    if safe_top_n == 0:
        return {}

    job_text = json.dumps(job_listing, ensure_ascii=True) + "\n" + json.dumps(job_detail, ensure_ascii=True)
    job_tokens = _tokenize_for_scoring(job_text)

    ranked: list[tuple[int, str, str]] = []
    for key, content in bundle.project_context.items():
        score = len(job_tokens.intersection(_tokenize_for_scoring(content)))
        ranked.append((score, key, content))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected = ranked[:safe_top_n]
    if not any(score > 0 for score, _, _ in selected):
        selected = ranked[:safe_top_n]

    out: dict[str, str] = {}
    safe_max = max(400, int(max_chars_per_project))
    for _, key, content in selected:
        text = content[:safe_max].strip()
        if text:
            out[key] = text
    return out


def _assemble_candidate_context_for_job(
    *,
    bundle: CandidateContextBundle,
    job_listing: dict[str, Any],
    job_detail: dict[str, Any],
    project_top_n: int,
    project_max_chars: int,
) -> dict[str, str]:
    context = dict(bundle.base_context)
    context.update(
        _select_project_context_for_job(
            bundle=bundle,
            job_listing=job_listing,
            job_detail=job_detail,
            top_n=project_top_n,
            max_chars_per_project=project_max_chars,
        )
    )
    return context


def _config_int(cfg: dict[str, Any], key: str, default: int, *, min_value: int = 1) -> int:
    value = cfg.get(key)
    if isinstance(value, int) and value >= min_value:
        return value
    return default


def _db_path_from_config() -> Path:
    cfg = load_config()
    central = get_central_service_config(cfg)
    raw = central.get("scheduler_db_path")
    return resolve_scheduler_db_path(str(raw) if isinstance(raw, str) else None)


def _connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


def load_recent_seen_job_keys(
    *,
    task_id: str,
    provider: str = DEFAULT_PROVIDER,
    limit: int = DEFAULT_SEEN_LIMIT,
    db_path: Path | None = None,
) -> list[str]:
    safe_limit = max(1, int(limit))
    path = db_path or _db_path_from_config()
    with _connect_db(path) as conn:
        rows = conn.execute(
            """
            SELECT item_key
            FROM task_seen_items
            WHERE task_id = ? AND provider = ?
            ORDER BY COALESCE(last_seen_at, first_seen_at) DESC
            LIMIT ?;
            """,
            (task_id, provider, safe_limit),
        ).fetchall()
    out: list[str] = []
    for row in rows:
        key = _coerce_text(row["item_key"])
        if key:
            out.append(key)
    return out


def _mark_job_seen(
    *,
    task_id: str,
    provider: str,
    job_key: str,
    metadata: dict[str, Any],
    db_path: Path,
) -> None:
    now = _iso_now()
    with _connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_seen_items(task_id, provider, item_key, metadata_json, first_seen_at, last_seen_at, seen_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(task_id, provider, item_key) DO UPDATE SET
                metadata_json = excluded.metadata_json,
                last_seen_at = excluded.last_seen_at,
                seen_count = task_seen_items.seen_count + 1;
            """,
            (
                task_id,
                provider,
                job_key,
                json.dumps(metadata, ensure_ascii=True),
                now,
                now,
            ),
        )


def _upsert_job_discovery(
    *,
    task_id: str,
    job_key: str,
    found_at: str,
    decision: str,
    db_path: Path,
) -> None:
    with _connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO job_discovery(task_id, job_key, found_at, decision, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(task_id, job_key) DO UPDATE SET
                found_at = excluded.found_at,
                decision = excluded.decision;
            """,
            (task_id, job_key, found_at, decision),
        )


def _upsert_task_state_snapshot(
    *,
    task_id: str,
    state_key: str,
    value: dict[str, Any],
    updated_by: str,
    db_path: Path,
) -> None:
    with _connect_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_state_kv(task_id, state_key, value_json, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id, state_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by;
            """,
            (task_id, state_key, json.dumps(value, ensure_ascii=True), _iso_now(), updated_by),
        )


def _render_cover_letter_docx(
    *,
    output_path: Path,
    company_name: str,
    body_paragraphs: list[str],
    date_line: str | None = None,
) -> None:
    from docx import Document  # type: ignore
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT, WD_TAB_ALIGNMENT
    from docx.shared import Inches, Pt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()

    # Name line
    name_paragraph = doc.add_paragraph()
    name_paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    name_run = name_paragraph.add_run("Zubin Jha")
    name_run.bold = True
    name_run.font.name = "Times New Roman"
    name_run.font.size = Pt(21)

    # Address + phone line
    address_line = doc.add_paragraph()
    address_line.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.RIGHT)
    address_line.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    run = address_line.add_run("45 West Stafford Ave, Worthington, OH 43085\t(614)-653-3941")
    run.font.name = "Times New Roman"
    run.font.size = Pt(12)

    # Email + LinkedIn + Portfolio line
    contact_line = doc.add_paragraph()
    contact_line.paragraph_format.tab_stops.add_tab_stop(Inches(4.4), WD_TAB_ALIGNMENT.RIGHT)
    contact_line.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.RIGHT)
    contact_line.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    email_run = contact_line.add_run("zubinkjha2025@gmail.com")
    email_run.font.name = "Times New Roman"
    email_run.font.size = Pt(12)
    email_run.underline = True
    divider = contact_line.add_run("\tlinkedin.com/in/zubin-jha-30752a355\tzubinjha.com")
    divider.font.name = "Times New Roman"
    divider.font.size = Pt(12)

    doc.add_paragraph("")

    date_text = date_line or _utc_now().strftime("%B %d, %Y")
    date_paragraph = doc.add_paragraph(date_text)
    date_paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    for run in date_paragraph.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)

    salutation = doc.add_paragraph(f"Dear Hiring Manager at {company_name},")
    for run in salutation.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)

    for paragraph in body_paragraphs:
        p = doc.add_paragraph(paragraph.strip())
        p.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
        for run in p.runs:
            run.font.name = "Times New Roman"
            run.font.size = Pt(12)

    closing = doc.add_paragraph("Sincerely,")
    for run in closing.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)

    signature = doc.add_paragraph("Zubin Jha")
    for run in signature.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)

    doc.save(str(output_path))


def _validate_decision_payload(raw: dict[str, Any]) -> tuple[bool, str]:
    decision = _coerce_text(raw.get("decision"))
    if decision not in ALLOWED_DECISIONS:
        return False, "decision must be one of Recommend Apply, Recommend Maybe, Skip"
    fit_score = raw.get("fit_score")
    if not isinstance(fit_score, int) or fit_score < 1 or fit_score > 10:
        return False, "fit_score must be integer 1-10"
    rationale = raw.get("rationale_short")
    if not isinstance(rationale, str) or not rationale.strip():
        return False, "rationale_short must be non-empty string"
    for list_key in ("reasons", "risks", "missing_requirements"):
        value = raw.get(list_key)
        if not isinstance(value, list):
            return False, f"{list_key} must be list"
        if any(not isinstance(item, str) or not item.strip() for item in value):
            return False, f"{list_key} must contain non-empty strings"
    return True, ""


def _validate_letter_payload(raw: dict[str, Any]) -> tuple[bool, str]:
    paragraphs = raw.get("paragraphs")
    if not isinstance(paragraphs, list):
        return False, "paragraphs must be a list"
    clean = [item.strip() for item in paragraphs if isinstance(item, str) and item.strip()]
    if len(clean) < 3:
        return False, "paragraphs must contain at least 3 non-empty entries"
    return True, ""


def _validate_sheet_field_payload(raw: dict[str, Any]) -> tuple[bool, str]:
    expected = ["company", "job_title", "location", "pay_range", "job_link"]
    for key in expected:
        value = raw.get(key)
        if not isinstance(value, str):
            return False, f"{key} must be string"
    return True, ""


def _default_sheet_fields() -> dict[str, str]:
    return {
        "company": NOT_FOUND_VALUE,
        "job_title": NOT_FOUND_VALUE,
        "location": NOT_FOUND_VALUE,
        "pay_range": NOT_FOUND_VALUE,
        "job_link": NOT_FOUND_VALUE,
    }


def _candidate_context_text(candidate_context: dict[str, str]) -> str:
    return "\n\n".join([f"[{k}]\n{v}" for k, v in candidate_context.items() if _coerce_text(v)])


def _extract_sheet_fields_via_llm(
    *,
    model_alias: str,
    job_listing: dict[str, Any],
    job_detail: dict[str, Any],
    invalid_retry_limit: int,
) -> dict[str, Any]:
    listing_json = json.dumps(job_listing, ensure_ascii=True)
    detail_json = json.dumps(job_detail.get("job") if isinstance(job_detail.get("job"), dict) else {}, ensure_ascii=True)
    prompt = (
        "Extract canonical spreadsheet fields from this job listing/detail payload.\n"
        f"Return JSON only with keys company, job_title, location, pay_range, job_link.\n"
        f"When a value is missing or ambiguous return exact string: {NOT_FOUND_VALUE}\n"
        "Do not include markdown.\n\n"
        f"[JobListing]\n{listing_json}\n\n"
        f"[JobDetail]\n{detail_json}\n"
    )
    system = "You are a strict JSON extraction engine. Return only valid JSON."
    attempts = max(0, int(invalid_retry_limit)) + 1
    last_error = "sheet_field_extraction_failed"
    for attempt in range(1, attempts + 1):
        result = _llm_json_response(model_alias=model_alias, system_prompt=system, user_prompt=prompt)
        if not result.get("ok"):
            last_error = str(result.get("error") or "llm_error")
            if attempt < attempts:
                prompt += f"\n\nValidation error from prior attempt: {last_error}. Fix JSON strictly."
            continue
        payload = result["payload"]
        valid, reason = _validate_sheet_field_payload(payload)
        if valid:
            return {
                "ok": True,
                "fields": {
                    "company": _normalize_not_found(payload.get("company")),
                    "job_title": _normalize_not_found(payload.get("job_title")),
                    "location": _normalize_not_found(payload.get("location")),
                    "pay_range": _normalize_not_found(payload.get("pay_range")),
                    "job_link": _normalize_not_found(payload.get("job_link")),
                },
            }
        last_error = reason
        if attempt < attempts:
            prompt += f"\n\nValidation error from prior attempt: {reason}. Fix JSON strictly."
    return {"ok": False, "error": last_error, "fields": _default_sheet_fields()}


def _llm_json_response(
    *,
    model_alias: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    out = call_llm(
        model=model_alias,
        max_output_tokens=1200,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "llm_error")}
    text = _coerce_text(out.get("text"))
    if not text:
        return {"ok": False, "error": "llm_empty_text"}
    try:
        payload = json.loads(text)
    except Exception:
        return {"ok": False, "error": "invalid_json", "raw_text": text}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "json_root_not_object", "raw_text": text}
    return {"ok": True, "payload": payload, "raw_text": text}


def _append_sheet_row_with_retry(
    *,
    row: dict[str, Any],
    attempts: int,
    backoff_sec: float,
) -> dict[str, Any]:
    safe_attempts = max(1, int(attempts))
    safe_backoff = max(0.0, float(backoff_sec))
    last: dict[str, Any] = {"ok": False, "error": "unknown"}
    for idx in range(1, safe_attempts + 1):
        out = append_job_app_row(row=row)
        if bool(out.get("ok")):
            return {**out, "attempts_used": idx, "attempts_configured": safe_attempts}
        last = out if isinstance(out, dict) else {"ok": False, "error": "invalid_response"}
        if idx >= safe_attempts:
            break
        if safe_backoff > 0:
            sleep(safe_backoff * idx)
    return {**last, "attempts_used": safe_attempts, "attempts_configured": safe_attempts}


def _evaluate_job(
    *,
    model_alias: str,
    job_listing: dict[str, Any],
    job_detail: dict[str, Any],
    candidate_context: dict[str, str],
    decision_rubric_text: str,
    invalid_retry_limit: int,
) -> dict[str, Any]:
    listing_json = json.dumps(job_listing, ensure_ascii=True)
    detail_json = json.dumps(job_detail.get("job") if isinstance(job_detail.get("job"), dict) else {}, ensure_ascii=True)
    context_text = _candidate_context_text(candidate_context)
    prompt = (
        "Decide whether this job should be Recommend Apply, Recommend Maybe, or Skip.\n"
        "Output JSON only with keys: decision, fit_score, rationale_short, reasons, risks, missing_requirements.\n"
        "Do not include markdown.\n\n"
        f"[DecisionRubric]\n{decision_rubric_text}\n\n"
        f"[CandidateContext]\n{context_text}\n\n"
        f"[JobListing]\n{listing_json}\n\n"
        f"[JobDetail]\n{detail_json}\n"
    )
    system = "You are a strict job application triage engine. Return valid JSON only."
    attempts = max(0, int(invalid_retry_limit)) + 1
    last_error = "decision_validation_failed"
    for attempt in range(1, attempts + 1):
        result = _llm_json_response(model_alias=model_alias, system_prompt=system, user_prompt=prompt)
        if not result.get("ok"):
            last_error = str(result.get("error") or "llm_error")
            if attempt < attempts:
                prompt += f"\n\nValidation error from prior attempt: {last_error}. Fix JSON strictly."
            continue
        payload = result["payload"]
        valid, reason = _validate_decision_payload(payload)
        if valid:
            return {"ok": True, "decision_payload": payload}
        last_error = reason
        if attempt < attempts:
            prompt += f"\n\nValidation error from prior attempt: {reason}. Fix JSON strictly."
    return {"ok": False, "error": last_error}


def _generate_cover_letter(
    *,
    model_alias: str,
    job_listing: dict[str, Any],
    job_detail: dict[str, Any],
    candidate_context: dict[str, str],
    style_spec_text: str,
    invalid_retry_limit: int,
) -> dict[str, Any]:
    listing_json = json.dumps(job_listing, ensure_ascii=True)
    detail_json = json.dumps(job_detail.get("job") if isinstance(job_detail.get("job"), dict) else {}, ensure_ascii=True)
    context_text = _candidate_context_text(candidate_context)
    prompt = (
        "Write a tailored cover letter body that sounds like the candidate profile.\n"
        "Output JSON only with key paragraphs: array of 3-5 paragraphs.\n"
        "No em dash characters.\n\n"
        f"[StyleSpec]\n{style_spec_text}\n\n"
        f"[CandidateContext]\n{context_text}\n\n"
        f"[JobListing]\n{listing_json}\n\n"
        f"[JobDetail]\n{detail_json}\n"
    )
    system = "You write concise, specific cover letter paragraphs. Return valid JSON only."
    attempts = max(0, int(invalid_retry_limit)) + 1
    last_error = "letter_validation_failed"
    for attempt in range(1, attempts + 1):
        result = _llm_json_response(model_alias=model_alias, system_prompt=system, user_prompt=prompt)
        if not result.get("ok"):
            last_error = str(result.get("error") or "llm_error")
            if attempt < attempts:
                prompt += f"\n\nValidation error from prior attempt: {last_error}. Fix JSON strictly."
            continue
        payload = result["payload"]
        valid, reason = _validate_letter_payload(payload)
        if valid:
            paragraphs = [str(item).strip() for item in payload.get("paragraphs", []) if isinstance(item, str) and str(item).strip()]
            return {"ok": True, "paragraphs": paragraphs}
        last_error = reason
        if attempt < attempts:
            prompt += f"\n\nValidation error from prior attempt: {reason}. Fix JSON strictly."
    return {"ok": False, "error": last_error}


def _map_sheet_row(
    *,
    job_key: str,
    extracted_fields: dict[str, str],
    decision_payload: dict[str, Any],
    date_found_iso: str,
    cover_letter_link: str | None,
    note_suffix: str | None = None,
) -> dict[str, str]:
    company = _normalize_not_found(extracted_fields.get("company"))
    title = _normalize_not_found(extracted_fields.get("job_title"))
    location = _normalize_not_found(extracted_fields.get("location"))
    pay_range = _normalize_not_found(extracted_fields.get("pay_range"))
    job_url = _normalize_not_found(extracted_fields.get("job_link"))
    decision = _coerce_text(decision_payload.get("decision")) or DECISION_SKIP
    fit_score = decision_payload.get("fit_score")
    rationale = _coerce_text(decision_payload.get("rationale_short"))
    notes = f"fit_score={fit_score}; rationale={rationale}"
    if note_suffix:
        notes = f"{notes}; {note_suffix}"
    return {
        "JobKey": job_key,
        "Company": company or "Unknown",
        "Job Title": title or "Unknown",
        "Location": location or "Unknown",
        "Date Found": date_found_iso,
        "Date Applied": "",
        "Status": decision,
        "Pay Range": pay_range,
        "Job Link": job_url,
        "Source": "Indeed",
        "Cover Letter": cover_letter_link or "",
        "Notes": notes[:500],
    }


@dataclass
class SearchCandidate:
    job_key: str
    job_listing: dict[str, Any]
    search_profile_id: str


def _collect_new_candidates(
    *,
    task_id: str,
    db_path: Path,
    search_profiles: list[dict[str, Any]],
    seen_limit: int,
    provider: str,
) -> tuple[list[SearchCandidate], dict[str, Any], list[str]]:
    errors: list[str] = []
    stats: dict[str, Any] = {
        "queries_total": len(search_profiles),
        "queries_ok": 0,
        "jobs_returned_total": 0,
        "jobs_filtered_seen": 0,
        "jobs_new_total": 0,
        "per_query": [],
    }
    initial_seen = set(load_recent_seen_job_keys(task_id=task_id, provider=provider, limit=seen_limit, db_path=db_path))
    in_run_seen = set(initial_seen)
    stats["initial_seen_loaded"] = len(initial_seen)
    discovered: list[SearchCandidate] = []

    for profile in search_profiles:
        profile_id = _coerce_text(profile.get("profile_id")) or "search_profile"
        keyword = _coerce_text(profile.get("keyword"))
        location = _coerce_text(profile.get("location"))
        if not keyword or not location:
            errors.append(f"search profile `{profile_id}` missing keyword/location")
            continue
        result = get_indeed_jobs(keyword=keyword, location=location)
        query_stats = {
            "profile_id": profile_id,
            "keyword": keyword,
            "location": location,
            "ok": bool(result.get("ok")),
            "returned": 0,
            "filtered_seen": 0,
            "kept_new": 0,
        }
        if not result.get("ok"):
            errors.append(f"search `{profile_id}` failed: {result.get('error')}")
            stats["per_query"].append(query_stats)
            continue
        stats["queries_ok"] += 1
        jobs = result.get("jobs") if isinstance(result.get("jobs"), list) else []
        query_stats["returned"] = len(jobs)
        stats["jobs_returned_total"] += len(jobs)
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_key = _extract_job_key(job)
            if not job_key:
                continue
            if job_key in in_run_seen:
                query_stats["filtered_seen"] += 1
                stats["jobs_filtered_seen"] += 1
                continue
            in_run_seen.add(job_key)
            discovered.append(SearchCandidate(job_key=job_key, job_listing=job, search_profile_id=profile_id))
            query_stats["kept_new"] += 1
            stats["jobs_new_total"] += 1
            try:
                _mark_job_seen(
                    task_id=task_id,
                    provider=provider,
                    job_key=job_key,
                    metadata={
                        "search_profile_id": profile_id,
                        "keyword": keyword,
                        "location": location,
                        "job_url": _extract_job_url(job),
                        "job_title": _extract_job_title(job),
                    },
                    db_path=db_path,
                )
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                errors.append(f"mark_seen failed for `{job_key}`: {exc}")
        stats["per_query"].append(query_stats)
    return discovered, stats, errors


def run_pipeline(*, task_id: str, payload: dict[str, Any], local_config: dict[str, Any], resources_dir: Path) -> dict[str, Any]:
    cfg = local_config if isinstance(local_config, dict) else {}
    search_profiles_raw = cfg.get("search_profiles")
    search_profiles = [item for item in search_profiles_raw if isinstance(item, dict)] if isinstance(search_profiles_raw, list) else []
    if not search_profiles:
        return {"ok": False, "error": "task_config.search_profiles is required"}

    seen_limit = _config_int(cfg, "seen_ids_limit", DEFAULT_SEEN_LIMIT, min_value=1)
    provider = _coerce_text(cfg.get("seen_provider")) or DEFAULT_PROVIDER
    extraction_model_alias = _coerce_text(cfg.get("extraction_model_alias")) or DEFAULT_EXTRACTION_MODEL
    decision_model_alias = _coerce_text(cfg.get("decision_model_alias")) or DEFAULT_DECISION_MODEL
    cover_letter_model_alias = _coerce_text(cfg.get("cover_letter_model_alias")) or DEFAULT_COVER_LETTER_MODEL
    invalid_retry_limit = _config_int(cfg, "invalid_schema_retry_limit", 1, min_value=0)
    destination_path = _coerce_text(cfg.get("cover_letter_destination_path")) or DEFAULT_COVER_LETTER_DESTINATION_PATH
    file_mode = _coerce_text(cfg.get("cover_letter_file_mode")).lower() or DEFAULT_FILE_MODE
    sheet_retry_attempts = _config_int(cfg, "sheet_retry_attempts", DEFAULT_SHEET_RETRY_ATTEMPTS, min_value=1)
    sheet_retry_backoff_sec = float(cfg.get("sheet_retry_backoff_sec")) if isinstance(cfg.get("sheet_retry_backoff_sec"), (int, float)) else DEFAULT_SHEET_RETRY_BACKOFF_SEC
    project_context_top_n = _config_int(cfg, "project_context_top_n", DEFAULT_PROJECT_CONTEXT_TOP_N, min_value=0)
    project_context_max_chars = _config_int(cfg, "project_context_max_chars", DEFAULT_PROJECT_CONTEXT_MAX_CHARS, min_value=300)
    if file_mode not in {"overwrite", "versioned"}:
        file_mode = DEFAULT_FILE_MODE

    db_path = _db_path_from_config()
    discovered, search_stats, errors = _collect_new_candidates(
        task_id=task_id,
        db_path=db_path,
        search_profiles=search_profiles,
        seen_limit=seen_limit,
        provider=provider,
    )

    candidate_context_bundle = _load_candidate_context_bundle(cfg)
    decision_rubric_text = _read_text_if_exists(resources_dir / "assets" / "decision_rubric.md")
    style_spec_text = _read_text_if_exists(resources_dir / "assets" / "cover_letter_style_spec.md")
    if not decision_rubric_text:
        errors.append("missing decision_rubric.md; using built-in fallback rubric")
        decision_rubric_text = (
            "Recommend Apply for clear alignment and interview viability.\n"
            "Recommend Maybe for partial alignment with meaningful upside.\n"
            "Skip for clear mismatch or seniority gap."
        )
    if not style_spec_text:
        errors.append("missing cover_letter_style_spec.md; using built-in style fallback")
        style_spec_text = "Times New Roman, clear concrete language, no em dash punctuation."

    counts = {
        "searched": int(search_stats.get("queries_total") or 0),
        "new_jobs": len(discovered),
        "seen_filtered": int(search_stats.get("jobs_filtered_seen") or 0),
        "recommended_apply": 0,
        "recommended_maybe": 0,
        "skipped": 0,
        "extraction_errors": 0,
        "decision_errors": 0,
        "cover_letter_errors": 0,
        "upload_errors": 0,
        "sheet_rows_written": 0,
        "sheet_rows_deduped": 0,
    }
    job_results: list[dict[str, Any]] = []
    found_at_iso = _utc_now().date().isoformat()

    for candidate in discovered:
        listing = candidate.job_listing
        job_key = candidate.job_key
        job_url = _extract_job_url(listing)
        if not job_url:
            counts["skipped"] += 1
            counts["decision_errors"] += 1
            errors.append(f"missing job url for `{job_key}`")
            continue

        detail = get_indeed_job_detail(url=job_url)
        if not detail.get("ok"):
            counts["skipped"] += 1
            counts["decision_errors"] += 1
            errors.append(f"detail fetch failed for `{job_key}`: {detail.get('error')}")
            continue

        candidate_context = _assemble_candidate_context_for_job(
            bundle=candidate_context_bundle,
            job_listing=listing,
            job_detail=detail,
            project_top_n=project_context_top_n,
            project_max_chars=project_context_max_chars,
        )
        sheet_extract = _extract_sheet_fields_via_llm(
            model_alias=extraction_model_alias,
            job_listing=listing,
            job_detail=detail,
            invalid_retry_limit=invalid_retry_limit,
        )
        extracted_fields = sheet_extract.get("fields") if isinstance(sheet_extract.get("fields"), dict) else _default_sheet_fields()
        if not sheet_extract.get("ok"):
            counts["extraction_errors"] += 1
            errors.append(f"field extraction failed for `{job_key}`: {sheet_extract.get('error')}")

        decision_out = _evaluate_job(
            model_alias=decision_model_alias,
            job_listing=listing,
            job_detail=detail,
            candidate_context=candidate_context,
            decision_rubric_text=decision_rubric_text,
            invalid_retry_limit=invalid_retry_limit,
        )
        if not decision_out.get("ok"):
            counts["skipped"] += 1
            counts["decision_errors"] += 1
            decision_error = f"decision failed for `{job_key}`: {decision_out.get('error')}"
            errors.append(decision_error)
            try:
                _upsert_job_discovery(
                    task_id=task_id,
                    job_key=job_key,
                    found_at=found_at_iso,
                    decision=DECISION_SKIP,
                    db_path=db_path,
                )
            except Exception as exc:  # pragma: no cover
                errors.append(f"job_discovery write failed for `{job_key}`: {exc}")
            job_results.append({"job_key": job_key, "decision": DECISION_SKIP, "status": "decision_error", "error": decision_error})
            continue

        decision_payload = decision_out["decision_payload"]
        decision = str(decision_payload["decision"])
        if decision == DECISION_RECOMMEND_APPLY:
            counts["recommended_apply"] += 1
        elif decision == DECISION_RECOMMEND_MAYBE:
            counts["recommended_maybe"] += 1
        else:
            counts["skipped"] += 1

        try:
            _upsert_job_discovery(
                task_id=task_id,
                job_key=job_key,
                found_at=found_at_iso,
                decision=decision,
                db_path=db_path,
            )
        except Exception as exc:  # pragma: no cover
            errors.append(f"job_discovery write failed for `{job_key}`: {exc}")

        if decision == DECISION_SKIP:
            job_results.append({"job_key": job_key, "decision": decision, "status": "skipped"})
            continue

        letter_out = _generate_cover_letter(
            model_alias=cover_letter_model_alias,
            job_listing=listing,
            job_detail=detail,
            candidate_context=candidate_context,
            style_spec_text=style_spec_text,
            invalid_retry_limit=invalid_retry_limit,
        )
        if not letter_out.get("ok"):
            counts["cover_letter_errors"] += 1
            errors.append(f"cover letter generation failed for `{job_key}`: {letter_out.get('error')}")
            job_results.append({"job_key": job_key, "decision": decision, "status": "cover_letter_error"})
            continue

        company_name = _coerce_text(extracted_fields.get("company"))
        if not company_name or company_name.lower() == NOT_FOUND_VALUE.lower():
            company_name = "the company"
        file_stem = _sanitize_file_stem(f"{job_key}_cover_letter")
        if file_mode == "versioned":
            file_stem = f"{file_stem}_{_utc_now().strftime('%Y%m%d_%H%M%S')}"
        relative_output_path = _repo_relative_path(resources_dir) / "state" / "cover_letters" / f"{file_stem}.docx"
        absolute_output_path = _repo_root() / relative_output_path
        try:
            _render_cover_letter_docx(
                output_path=absolute_output_path,
                company_name=company_name,
                body_paragraphs=letter_out["paragraphs"],
            )
        except Exception as exc:
            counts["cover_letter_errors"] += 1
            errors.append(f"cover letter render failed for `{job_key}`: {exc}")
            job_results.append({"job_key": job_key, "decision": decision, "status": "cover_letter_render_error"})
            continue

        upload_out = upload_file_to_google_drive(
            local_path=str(relative_output_path),
            destination_path=destination_path,
            filename=absolute_output_path.name,
        )
        if not upload_out.get("ok"):
            counts["upload_errors"] += 1
            errors.append(f"cover letter upload failed for `{job_key}`: {upload_out.get('error')}")
            job_results.append({"job_key": job_key, "decision": decision, "status": "cover_letter_upload_error"})
            continue
        cover_link = _coerce_text(upload_out.get("web_view_link"))

        row = _map_sheet_row(
            job_key=job_key,
            extracted_fields=extracted_fields,
            decision_payload=decision_payload,
            date_found_iso=found_at_iso,
            cover_letter_link=cover_link,
        )
        append_out = _append_sheet_row_with_retry(
            row=row,
            attempts=sheet_retry_attempts,
            backoff_sec=sheet_retry_backoff_sec,
        )
        if append_out.get("ok"):
            counts["sheet_rows_written"] += 1
            job_results.append({"job_key": job_key, "decision": decision, "status": "uploaded", "cover_letter": cover_link})
            continue

        err_text = _coerce_text(append_out.get("error"))
        if "Duplicate JobKey" in err_text:
            counts["sheet_rows_deduped"] += 1
            job_results.append({"job_key": job_key, "decision": decision, "status": "sheet_duplicate", "cover_letter": cover_link})
            continue
        counts["upload_errors"] += 1
        errors.append(f"sheet upload failed for `{job_key}`: {err_text or append_out.get('source')}")
        job_results.append({"job_key": job_key, "decision": decision, "status": "sheet_upload_error"})

    trigger = _coerce_text(payload.get("trigger")) or "scheduled"
    summary = (
        f"indeed_daily_search done trigger={trigger} "
        f"queries={counts['searched']} new_jobs={counts['new_jobs']} "
        f"apply={counts['recommended_apply']} maybe={counts['recommended_maybe']} "
        f"skip={counts['skipped']} rows_written={counts['sheet_rows_written']} "
        f"errors={len(errors)}"
    )
    snapshot = {
        "updated_at": _iso_now(),
        "summary": summary,
        "counts": counts,
        "search_stats": {
            "queries_total": search_stats.get("queries_total"),
            "queries_ok": search_stats.get("queries_ok"),
            "jobs_returned_total": search_stats.get("jobs_returned_total"),
            "jobs_filtered_seen": search_stats.get("jobs_filtered_seen"),
            "jobs_new_total": search_stats.get("jobs_new_total"),
            "initial_seen_loaded": search_stats.get("initial_seen_loaded"),
        },
        "errors": errors[:20],
    }
    try:
        _upsert_task_state_snapshot(
            task_id=task_id,
            state_key="last_run_snapshot",
            value=snapshot,
            updated_by="indeed_daily_search_task",
            db_path=db_path,
        )
    except Exception as exc:  # pragma: no cover
        errors.append(f"failed to persist last_run_snapshot: {exc}")

    return {
        "ok": True,
        "summary": summary,
        "counts": counts,
        "search_stats": search_stats,
        "job_results": job_results,
        "errors": errors,
    }
