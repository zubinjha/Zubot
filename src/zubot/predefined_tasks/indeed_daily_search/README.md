# Indeed Daily Search Task

This folder is the task-local resource/config package for `indeed_daily_search`.

- `task.py`: predefined task entrypoint used by central task runner.
- `pipeline.py`: end-to-end pipeline implementation.
- `USAGE.md`: terminal run/stop quick reference for this task.
- `task_config.example.json`: tracked schema/template config with placeholder values.
- `task_config.json`: local runtime config (queries, model aliases, limits, file mode; gitignored).
- `prompts/`: decision + cover-letter prompt templates.
- `assets/`: decision rubric + cover-letter style spec.
- `state/`: task-local runtime outputs (cover letters, debug artifacts).

## Runtime Behavior
1. Load latest seen job keys from `task_seen_items` (capped by `seen_ids_limit`, default `200` if not configured).
   - recency policy is first discovery time (`first_seen_at DESC`), not last touch time
2. Build query combinations from `search_locations x search_keywords` and execute each with HasData Indeed listing API.
3. Dedupe across:
   - previously seen keys
   - current run keys from other query profiles
4. Mark newly discovered keys in `task_seen_items` immediately.
5. Fetch detail payload for each new job.
6. Run field-extraction LLM to normalize spreadsheet fields:
   - `company`, `job_title`, `location`, `pay_range`, `job_link`
   - on model failure or invalid JSON, deterministic fallback starts as `Not Found` for each field
   - deterministic fallback then fills `company`, `job_title`, `location`, and `job_link` from listing/detail payloads when present
   - `job_link` is force-filled from the known listing URL when available (prevents blank/`Not Found` links for valid listings)
7. Run decision LLM (`Recommend Apply` / `Recommend Maybe` / `Skip`) with strict JSON validation.
8. For non-skip decisions:
   - generate cover-letter body via LLM with strict JSON validation
   - generated body must pass structure checks (4 paragraphs, minimum word count, paragraph minimum length)
   - generated body is normalized to remove dash punctuation in body text
   - if cover-letter JSON generation fails validation, task uses deterministic fallback paragraphs so file generation can proceed
   - render styled DOCX (Times New Roman spec from `assets/cover_letter_style_spec.md`)
   - header uses clickable email and clickable `LinkedIn` text (no portfolio header link)
   - upload DOCX to Google Drive
   - if upload response omits `web_view_link`, task falls back to a `drive_file_id`-based viewer link
   - append row to job applications spreadsheet (`append_job_app_row`)
9. Persist triage outcome in `job_discovery` table.
10. Persist concise run debug payload in `task_state_kv` under `state_key=last_run_snapshot`.
11. Persist live progress snapshots in `task_state_kv` under `state_key=live_progress` during run execution.

## Candidate Context Ingestion
- Base context is loaded from `candidate_context_files` (or defaults) and includes:
  - `context/USER.md`
  - `context/more-about-human/professional_profile.md`
  - `context/more-about-human/passion_profile.md`
  - `context/more-about-human/writing_voice.md`
  - `context/more-about-human/resume.md`
  - `context/more-about-human/projects/project_index.md`
- Project detail files are loaded from `project_context_files` (or all `context/more-about-human/projects/*.md`).
- Per job, the pipeline selects top-N project files by token overlap with job text before decision/cover-letter calls.

## Idempotency + Safety
- Seen ledger: `task_seen_items(task_id, provider, item_key)` prevents duplicate processing.
- Sheet row idempotency: `append_job_app_row` rejects duplicate `JobKey`; task treats this as deduped outcome.
- Cover letter artifact naming is controlled by `cover_letter_file_mode`:
  - filename format: `YYYY-MM-DD - Company - Role.docx` (shortened, human-readable)
  - `versioned` (default): if filename exists, append numeric suffix (` - 1`, ` - 2`, ...)
  - `overwrite`: write to the base filename without numeric suffix

## Live Progress Contract
- During active runs, progress state is continuously upserted at:
  - `task_state_kv(task_id='indeed_daily_search', state_key='live_progress')`
- Includes:
  - `stage` (`starting` | `search` | `process` | `done`)
  - `query_index/query_total`
  - `job_index/job_total`
  - `worker_slots[]` (process-phase slot states for concurrent workers)
    - per slot: `slot`, `state`, `step_key`, `step_label`, `step_index`, `step_total`, `job_key`, query/result metadata
  - `search_fraction`
  - `search_percent`
  - `overall_percent`
  - `total_percent`
  - `status_line`
- Search phase formula follows equal-weight query buckets and equal-weight jobs within each query:
  - `search_fraction = (query_index-1)/query_total + (1/query_total)*(job_index/job_total)`
- `total_percent` is monotonic and phase-weighted (search + processing), and does not drop between stages.
- Terminal dashboard (`task_cli run`) also shows:
  - current stage/query/result
  - current worker slot states
  - elapsed/expected runtime and projected local end time
  - rolling decision counters
  - rolling rate for recent non-seen completions (last up to 10)
  - active run log path
- Per-run terminal snapshots are logged to:
  - `src/zubot/predefined_tasks/indeed_daily_search/state/logs/run-YYYYmmdd-HHMMSS.log`

## Row Write Resilience
- For `Recommend Apply` / `Recommend Maybe`, spreadsheet row append is blocked if final `Job Link` resolves to `Not Found`.
- For cover letters:
  - generation failures trigger deterministic paragraph fallback
  - upload failures still allow row append with a `cover_letter_error=...` marker in `AI Notes`
  - fallback generation adds `cover_letter_fallback=...` marker in `AI Notes`

## Task Config Keys
- `search_locations[]`: list of locations, each paired with every keyword.
- `search_keywords[]`: list of keywords, each paired with every location.
- `search_profiles[]`: optional explicit list of `{profile_id, keyword, location}`; if present, it takes precedence over generated combinations.
- `seen_ids_limit`: max recent seen keys loaded before each run.
- `task_timeout_sec`: optional predefined-task runtime timeout (seconds) used when task profile timeout is unset (`28800` recommended for full 18-query runs).
- `process_workers`: number of concurrent process-phase workers (`1..12`).
- `db_queue_maxsize`: bounded queue size for serialized DB discovery writes.
- `sheet_queue_maxsize`: bounded queue size for serialized spreadsheet append writes.
- `extraction_model_alias`: model alias for LLM field extraction (`company/job_title/location/pay_range/job_link`).
- `decision_model_alias`: model alias for application triage.
- `cover_letter_model_alias`: model alias for cover-letter body generation.
- `invalid_schema_retry_limit`: number of retries when model output fails JSON/schema validation.
- `project_context_top_n`: number of matched project docs to include per job prompt.
- `project_context_max_chars`: max chars per selected project doc included in prompt context.
- `candidate_context_files[]`: optional override list of repo-relative base context files.
- `project_context_files[]`: optional override list of repo-relative project context files.
- `cover_letter_destination_path`: Drive folder path for uploads.
- `cover_letter_destination_folder_id`: optional explicit Google Drive folder id; when set, uploads target this exact folder id (bypasses path resolution).
- `cover_letter_contact_email`: email rendered in header as clickable `mailto:` hyperlink.
- `cover_letter_linkedin_url`: destination URL for header `LinkedIn` hyperlink text.
- `cover_letter_linkedin_label`: displayed label text for the LinkedIn hyperlink (default `LinkedIn`).
- `cover_letter_file_mode`: `versioned` or `overwrite`.
- `cover_letter_upload_retry_attempts`: retry attempts for Drive upload.
- `cover_letter_upload_retry_backoff_sec`: linear backoff seconds between Drive upload retry attempts.
- `sheet_retry_attempts`: retry attempts for spreadsheet row append failures.
- `sheet_retry_backoff_sec`: linear backoff seconds between sheet retry attempts.

## Quick Runbook

- Reset central DB:
  - `bash devtools/reset_central_db.sh`
- Run task manually from terminal:
  - `source .venv/bin/activate`
  - `python -m src.zubot.daemon.task_cli run indeed_daily_search --payload-json '{"trigger":"manual_real_run"}'`
- Inspect latest run snapshot:
  - `sqlite3 memory/central/zubot_core.db "SELECT value_json FROM task_state_kv WHERE task_id='indeed_daily_search' AND state_key='last_run_snapshot' ORDER BY updated_at DESC LIMIT 1;"`
- Inspect live progress:
  - `sqlite3 memory/central/zubot_core.db "SELECT value_json FROM task_state_kv WHERE task_id='indeed_daily_search' AND state_key='live_progress' ORDER BY updated_at DESC LIMIT 1;"`
