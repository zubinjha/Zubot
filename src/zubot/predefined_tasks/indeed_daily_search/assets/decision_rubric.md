# Indeed Daily Search Decision Rubric

## Label Contract
- `Recommend Apply`
- `Recommend Maybe`
- `Skip`

## Hard Skip Signals
- Role level is clearly senior/staff/principal or otherwise explicitly advanced (for example 7+ years required).
- Role is clearly outside target domains (for example pure frontend-only or devops-only with no backend/data scope).
- Role is primarily embedded/firmware/microcontroller-focused with little backend/data/software-system overlap.
- Role contains hard constraints that are not currently viable.

## Seniority Guidance
- Do not auto-skip only because a posting says 3+ years required.
- Treat 3+ years as a soft gap and decide based on overlap, interview story strength, and whether responsibilities look early-career-feasible.
- Continue to skip clearly senior ladders and explicit 7+ year requirements unless the role scope is obviously mislabeled.

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
