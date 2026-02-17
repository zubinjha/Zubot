# Decision Evaluation Prompt Template

Goal:
- Return JSON decision for one job using rubric + candidate context.

Output JSON keys:
- `decision` (`Recommend Apply` | `Recommend Maybe` | `Skip`)
- `fit_score` (1-10 integer)
- `rationale_short` (string)
- `reasons` (array of strings)
- `risks` (array of strings)
- `missing_requirements` (array of strings)

Rules:
- JSON only.
- No markdown fences.
- Evidence-based reasoning only.
