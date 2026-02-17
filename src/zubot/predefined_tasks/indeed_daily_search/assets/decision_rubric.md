# Indeed Daily Search Decision Rubric

## Label Contract
- `Recommend Apply`
- `Recommend Maybe`
- `Skip`

## Hard Skip Signals
- Role level is clearly senior/staff/principal with 5+ years required.
- Role is clearly outside target domains (for example pure frontend-only or devops-only with no backend/data scope).
- Role contains hard constraints that are not currently viable.

## Recommend Apply Signals
- Strong overlap with candidate strengths (Python, SQL, backend/data systems, applied AI tooling).
- Scope is realistic for early-career profile.
- Clear interview story can be grounded in existing projects.

## Recommend Maybe Signals
- Partial overlap with good upside.
- Some gaps exist but role still plausible as stretch.
- Candidate can credibly frame transferable experience.

## Scoring
- `fit_score` should be 1-10.
- `Recommend Apply` should usually map to 7-10.
- `Recommend Maybe` should usually map to 4-7.
- `Skip` should usually map to 1-4.

## Output Discipline
- Decisions must be specific and evidence-based.
- Avoid generic language and motivational filler.
