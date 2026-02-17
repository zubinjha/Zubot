# Cover Letter Values Prompt

Use this prompt when generating values for `cover_letter_template.docx` placeholders.

## Instructions To LLM
You are filling placeholder values for a fixed-style cover letter template.

Inputs you will receive:
- job description and company details
- user profile/resume context

Output requirements:
- return valid JSON only
- keys must exactly match the schema fields
- keep style concise, professional, and naturally aligned with the user's voice
- avoid hallucinated personal facts
- do not include markdown fences
- do not use em dashes
- avoid dash-heavy clause chaining

Required keys:
- `date`
- `recipient_name_title`
- `company_name`
- `salutation_target`
- `opening_focus_area`
- `stack_list`
- `project_summary`
- `project_ownership_list`
- `role_title`
- `company_why`
- `role_scope_alignment`
- `growth_focus_area`
- `immediate_value_areas`
- `closing_value_line`
- `team_name`

Guidance:
- `date`: format like `February 17, 2026`
- `recipient_name_title`: if no named person, use `Hiring Manager`
- `salutation_target`: use a natural salutation target, e.g. `Hiring Manager at <Company>`
- `project_summary`: include role-relevant evidence (API systems, CI/CD, data pipelines, reliability) when true
- `company_why`: tie directly to role/company specifics from the job description
- `immediate_value_areas`: emphasize practical, near-term contribution areas

Validation:
- every key present
- every value non-empty string
- no extra keys
