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
Use Find/Replace in Word and replace these current segments with placeholders:

- `Jan 15, 2026` -> `{{date}}`
- `Hiring Manager` (recipient line) -> `{{recipient_name_title}}`
- `American Express` (recipient company line and body mentions) -> `{{company_name}}`
- `Hiring Manager at American Express` -> `{{salutation_target}}`
- `building reliable, data-driven software systems with real-world impact` -> `{{opening_focus_area}}`
- `backend API development, database design with PostgreSQL, frontend development using React, and deploying production applications using modern CI/CD workflows` -> `{{stack_list}}`
- `a sports analytics platform that ingests external data, stores and processes it in relational databases, and exposes results through APIs and web applications` -> `{{project_summary}}`
- `schema design, backend logic, performance considerations, deployment, and ongoing iteration based on real usage` -> `{{project_ownership_list}}`
- `Software Engineer II` -> `{{role_title}}`
- `of the company’s emphasis on scalable, secure, and high-quality systems that directly impact millions of users` -> `{{company_why}}`
- `work on enterprise applications, collaborate closely with product and technical partners, and contribute to CI/CD and reliability initiatives` -> `{{role_scope_alignment}}`
- `enterprise Java and Spring based systems` -> `{{growth_focus_area}}`
- `my experience with full-stack development, databases, and production-focused engineering` -> `{{immediate_value_areas}}`
- `my technical skills, curiosity, and collaborative mindset` -> `{{closing_value_line}}`
- `American Express engineering team` and `your team` references -> `{{team_name}}`

## Voice and Style Guardrails
- Preserve this letter's concise, direct, professional tone.
- Use commas/periods instead of em dashes.
- Avoid generic hype language.
- Keep claims grounded in real project evidence.

## Field Contract
- Schema: `cover_letter_fields.schema.json`
- Prompt contract: `../prompts/cover_letter_values_prompt.md`
