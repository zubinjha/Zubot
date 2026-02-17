# Cover Letter Template Placeholders

This workflow is template-first.

- Keep styling/layout in `cover_letter_template.docx`.
- Let the LLM decide only variable values.
- Renderer will fill placeholders in the DOCX.

## Rules
- Keep each placeholder as one contiguous token: `{{placeholder_name}}`.
- Do not split a placeholder across runs with mixed formatting.
- Do not add extra braces or spaces (use `{{company_name}}`, not `{ { company_name } }`).

## Replace In The DOCX
Use Find/Replace in Word and replace these current variable texts with placeholders:

- `Month, DD, Year` -> `{{date}}`
- `Name of person if there is one, their title/role [Human Resources or Hiring Manager]` -> `{{recipient_name_title}}`
- `Company name` -> `{{company_name}}`
- `name if there is a name, otherwise just put “Hiring Manager at <company name>` -> `{{salutation_target}}`
- `___[would you say you have a speciality, or area of interest to work in?]` -> `{{specialty_area}}`
- `_____[mention some of the key ones but not all]` -> `{{key_tools_list}}`
- `_____ describe briefly your win probability project here.` -> `{{project_example_summary}}`
- `the Software Engineer` -> `{{role_title}}`
- `company` -> `{{company_name}}`
- `<put something about their mission/industry/products>` -> `{{mission_hook}}`
- `_____ put something about what the company [use its name] does, for example, for Ford we could say, “...impactful code to improve automobiles and related systems at Ford.”` -> `{{impact_phrase}}`
- `(company’s)` -> `{{company_name_possessive}}`

## Field Contract
- Schema: `cover_letter_fields.schema.json`
- Example: `cover_letter_fields.example.json`
- Blank template: `cover_letter_fields.blank.json`
