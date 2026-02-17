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
- keep style concise and professional
- avoid hallucinated personal facts
- do not include markdown fences

Required keys:
- `date`
- `recipient_name_title`
- `company_name`
- `salutation_target`
- `specialty_area`
- `key_tools_list`
- `project_example_summary`
- `role_title`
- `mission_hook`
- `impact_phrase`
- `company_name_possessive`

Guidance:
- `date`: format like `February 17, 2026`
- `recipient_name_title`: if no named person, use `Hiring Manager`
- `salutation_target`: use a natural salutation target, e.g. `Hiring Manager at <Company>`
- `company_name_possessive`: possessive form, e.g. `Acme's`

Validation:
- every key present
- every value non-empty string
- no extra keys
