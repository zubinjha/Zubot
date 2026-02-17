# Indeed Daily Search Task

This folder is the task-local resource/config package for `indeed_daily_search`.

- `task.py`: profile entrypoint wrapper for this task package.
- `task_config.json`: task-local settings consumed by runtime/task script.
- `prompts/`: optional prompt assets used by this task.
- `assets/`: optional static assets.
- `state/`: optional local task runtime scratch area.

Business logic script implementation is intentionally unchanged for now.

## Cover Letter Template Assets
- `assets/cover_letter_template.docx`: style/layout source of truth for cover letters.
- `assets/cover_letter_fields.schema.json`: structured input contract for variable fields.
- `assets/cover_letter_template_placeholders.md`: placeholder and editing rules.
- `prompts/cover_letter_values_prompt.md`: LLM prompt contract for generating placeholder values.

## Task Config Keys
- `search_profiles[]`: list of `{profile_id, keyword, location}` search definitions used for HasData listing calls.
- `max_listing_jobs_per_profile`: cap on listing items consumed from each profile in a run.
- `max_job_details_to_fetch_per_run`: cap on detail fetch calls per run.
- `seen_items.provider`: provider key used for dedupe checks (`task_seen_items` path).
- `cover_letter.*`: template/prompt/schema paths and output directory used for letter drafting.
