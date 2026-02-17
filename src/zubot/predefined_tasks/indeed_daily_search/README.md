# Indeed Daily Search Task

This folder is the task-local resource/config package for `indeed_daily_search`.

- `task.py`: predefined task entrypoint used by central task runner.
- `pipeline.py`: end-to-end pipeline implementation.
- `task_config.json`: runtime config (queries, model aliases, limits, file mode).
- `prompts/`: decision + cover-letter prompt templates.
- `assets/`: decision rubric + cover-letter style spec.
- `state/`: task-local runtime outputs (cover letters, debug artifacts).

## Runtime Behavior
1. Load latest seen job keys from `task_seen_items` (capped by `seen_ids_limit`, default `200`).
2. Execute all `search_profiles` with HasData Indeed listing API.
3. Dedupe across:
   - previously seen keys
   - current run keys from other query profiles
4. Mark newly discovered keys in `task_seen_items` immediately.
5. Fetch detail payload for each new job.
6. Run field-extraction LLM to normalize spreadsheet fields:
   - `company`, `job_title`, `location`, `pay_range`, `job_link`
   - on model failure or invalid JSON, deterministic fallback is `Not Found` for each field
7. Run decision LLM (`Recommend Apply` / `Recommend Maybe` / `Skip`) with strict JSON validation.
8. For non-skip decisions:
   - generate cover-letter body via LLM with strict JSON validation
   - render styled DOCX (Times New Roman spec from `assets/cover_letter_style_spec.md`)
   - upload DOCX to Google Drive
   - append row to job applications spreadsheet (`append_job_app_row`)
9. Persist triage outcome in `job_discovery` table.
10. Persist concise run debug payload in `task_state_kv` under `state_key=last_run_snapshot`.

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
  - `versioned` (default): append timestamp suffix
  - `overwrite`: deterministic filename by job key

## Task Config Keys
- `search_profiles[]`: list of `{profile_id, keyword, location}` search definitions used for HasData listing calls.
- `seen_ids_limit`: max recent seen keys loaded before each run.
- `extraction_model_alias`: model alias for LLM field extraction (`company/job_title/location/pay_range/job_link`).
- `decision_model_alias`: model alias for application triage.
- `cover_letter_model_alias`: model alias for cover-letter body generation.
- `invalid_schema_retry_limit`: number of retries when model output fails JSON/schema validation.
- `project_context_top_n`: number of matched project docs to include per job prompt.
- `project_context_max_chars`: max chars per selected project doc included in prompt context.
- `candidate_context_files[]`: optional override list of repo-relative base context files.
- `project_context_files[]`: optional override list of repo-relative project context files.
- `cover_letter_destination_path`: Drive folder path for uploads.
- `cover_letter_file_mode`: `versioned` or `overwrite`.
- `sheet_retry_attempts`: retry attempts for spreadsheet row append failures.
- `sheet_retry_backoff_sec`: linear backoff seconds between sheet retry attempts.
