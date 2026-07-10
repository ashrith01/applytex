# SmartJobApply Build Plan

This plan captures product and engineering direction for SmartJobApply, using
the LaTeX resume matcher as the resume-tailoring engine.

## Research Context

Recent Stanford research on algorithmic hiring shows that AI screening can
produce two important labor-market problems:

- **Adverse impact by job.** Aggregated fairness numbers can hide discrimination
  that appears at the individual job or model level.
- **Algorithmic monoculture.** When many employers use the same vendor or similar
  screening logic, the same candidates can be rejected repeatedly across many
  companies for the same hidden reason.

References:

- Stanford HAI: <https://hai.stanford.edu/news/ai-hiring-tools-can-yield-racial-bias-and-systemic-rejection>
- Paper: <https://algorithmichiring.github.io/paper.pdf>
- Code: <https://github.com/rishibommasani/HiringAlgorithms>

Product implication: SmartJobApply should not only optimize resumes. It should
help users diversify application paths, detect repeated automated rejection
patterns, and preserve truthful human review.

## Product Principles

1. **Truthful optimization only.**
   Resume edits must be grounded in the original resume. No invented skills,
   metrics, certifications, jobs, degrees, dates, companies, or domain experience.

2. **Human review before apply.**
   Users must approve final resume versions and generated messages before an
   application is submitted.

3. **One-page resume hard limit.**
   A tailored resume that overflows one page is blocked from confirmation until
   the user shortens content or approves layout changes.

4. **Diversify the path, not just the resume.**
   Repeated automated rejection should trigger strategy changes: referrals,
   recruiter outreach, direct applications, different job families, or stronger
   role targeting.

5. **Explain every edit.**
   Each resume change should show the original text, revised text, reason, and
   risk notes when applicable.

## Core Modules

### 1. Resume Tailoring Engine

Existing repo responsibility:

- Parse `.tex` resumes into editable statement spans.
- Lock personal info, education, certifications, publications, and unknown
  sections.
- Optimize only summary, work experience, projects, and skills.
- Reconstruct by exact character-span splicing.
- Render PDF and enforce one-page constraint.

Next work:

- Add API integration tests.
- Add mocked full optimization tests.
- Add approval state around generated resume versions.
- Later: wire OpenAI/Gemini when keys are available.

### 2. ATS / Screening Risk Scoring

Current base:

- Keyword and skill match scoring in `ats.py`.
- Before/after ATS score in optimizer.

Build toward:

- Required skill coverage.
- Preferred skill coverage.
- Missing keyword phrases.
- Role-title alignment.
- Resume format/parser safety.
- Over-generic bullet detection.
- Suspicious over-tailoring detection.
- Screening risk label: low, medium, high.

### 3. Application Diversification

Track each application by:

- Company.
- Role title.
- Role family.
- Industry.
- Location / remote status.
- Application channel.
- Known or inferred ATS/vendor when available.
- Resume version used.
- Outreach/referral path used.

Use this to recommend a diversified application mix instead of sending many
similar applications through the same likely filter.

### 4. Rejection Pattern Detection

Track outcomes:

- Drafted.
- Applied.
- Auto-rejected.
- No response.
- Recruiter screen.
- Interview.
- Offer.
- Withdrawn.

Flag patterns such as:

- Many rapid rejections from similar roles.
- Rejections clustered by company group, ATS/vendor, or role family.
- Strong keyword match but no human responses.
- Repeated failures from one resume variant.

When flagged, suggest strategy changes rather than endless resume rewrites.

### 5. Resume Variant Testing

Generate controlled, truthful resume variants for different target families:

- ML engineer.
- Data scientist.
- AI/LLM engineer.
- Backend AI engineer.
- AI product engineer.

Store which variant was used for each application and compare outcomes over time.

### 6. Human-Path Assistant

When algorithmic screening risk is high, prioritize human channels:

- Referral target discovery.
- Recruiter message drafts.
- Alumni/company contact notes.
- Follow-up reminders.
- Short role-specific “why me” blurbs.

Generated outreach should remain truthful and user-approved.

### 7. Persistence And Audit Trail

SmartJobApply needs durable records for:

- User.
- Base resume.
- Job description.
- Tailored resume version.
- Diff and edit reasons.
- Rendered PDF.
- ATS before/after scores.
- Approval status.
- Application status.
- Outreach/follow-up history.

This is outside the current in-memory API session model.

## Suggested Implementation Sequence

### Phase 1: Stabilize Resume Engine

- Finish contract drift fixes.
- Add parser edge-case tests.
- Add API route tests.
- Add mocked optimize end-to-end test.
- Add explicit generated resume status:
  `draft`, `overflow_blocked`, `ready_for_review`, `approved`, `rejected`.

### Phase 2: SmartJobApply Data Model

- Design database tables for users, resumes, jobs, applications, versions,
  outcomes, and outreach.
- Replace in-memory sessions with persistent records.
- Store original `.tex`, modified `.tex`, PDF bytes/path, diff, and ATS scores.

### Phase 3: Application Strategy Layer

- Add application tracking.
- Add rejection pattern detection.
- Add diversification recommendations.
- Add resume variant outcome comparison.

### Phase 4: Human Outreach Layer

- Add referral/recruiter outreach drafts.
- Add follow-up reminders.
- Add user-approved message generation.

### Phase 5: Production Hardening

- Add authentication and user isolation.
- Restrict CORS.
- Add rate limits.
- Add audit logs.
- Add background jobs for rendering/optimization.
- Add monitoring for LLM failures and PDF compile failures.

## Near-Term Engineering Tasks

1. Add FastAPI tests for upload, optimize, rerender, status, and delete.
2. Add mocked LLM tests for the complete optimization pipeline.
3. Add persistent `ResumeVersion` and `Application` concepts.
4. Add approval status so overflowed resumes cannot be submitted.
5. Add outcome tracking fields.
6. Add first rejection-pattern heuristic:
   rapid repeated rejection across similar roles should suggest channel
   diversification and human outreach.

## ATS 80+ Optimization Plan

Public resume-matching tools commonly recommend aiming around 75-80% match
against a specific job description. Treat 80+ as SmartJobApply's minimum
optimization target for online applications, but not as a guarantee of
shortlisting. The resume must still be truthful, readable, and defensible in a
recruiter screen.

### Optimization Targets

SmartJobApply should produce a tailored resume only when all checks pass:

- ATS/match score >= 80.
- Required skill coverage >= 90 when truthful.
- Preferred skill coverage improved where truthful.
- JD keyword phrase coverage >= 70.
- One-page render passes.
- No fabricated metrics or domain experience.
- Recruiter readability score passes.
- User approves every added skill and final diff.

If score cannot reach 80 truthfully, the product should say why and recommend a
different application strategy instead of forcing keyword stuffing.

### Optimization Loop

1. Parse resume and extract editable statements.
2. Extract JD requirements into structured fields.
3. Score baseline ATS and recruiter fit.
4. Build a missing-evidence map:
   - required skills already present
   - required skills missing but likely supported by resume evidence
   - required skills that need user confirmation
   - required skills not supported and should not be added
5. Confirm missing skills with the user.
6. Apply deterministic skills-line patch for confirmed skills.
7. Rewrite only high-impact summary/work/project statements.
8. Re-render and enforce one page.
9. Re-score ATS and recruiter fit.
10. Iterate until score >= 80 or no truthful edits remain.
11. Return final diff with explanations and approval status.

### Model Routing

Use multiple model paths instead of one large prompt.

#### Local Ollama Tasks

Use Ollama for cheaper, repeatable, privacy-sensitive, and schema-constrained
tasks where latency is acceptable:

- Resume parsing sanity checks after deterministic parser output.
- JD cleanup and section detection.
- Keyword normalization and synonym expansion.
- Skill alias suggestions.
- Recruiter-readability critique.
- Bullet naturalness critique.
- Diff risk classification.
- User-facing explanations.
- Small JSON transformations with structured-output schema.

Recommended local models:

- `qwen2.5:14b` or similar Qwen instruct model for structured JSON, extraction,
  and compact reasoning.
- A stronger local model can be added later for recruiter critique if hardware
  allows.

Implementation notes:

- Use Ollama structured outputs / JSON schema instead of plain JSON prompting.
- Keep prompts small and deterministic.
- Cache outputs by resume hash + JD hash.

#### Groq Qwen Tasks

Use Groq `qwen/qwen3-32b` for tasks that benefit from fast, stronger reasoning
and larger context, while keeping each request under rate/token limits:

- JD requirement extraction for complex postings.
- Skill target planning.
- High-impact bullet rewrite generation.
- Final consistency audit across resume + JD.
- Ranking candidate changes by expected score lift.

Implementation notes:

- Do not send the whole resume and full JD into one giant diff prompt.
- Split optimization by section or top-N statements.
- Use compact structured inputs, not full prose where possible.
- Add token preflight before every request.
- Add provider-specific fallback states: `rate_limited`, `prompt_too_large`,
  `invalid_json`, `llm_unavailable`.

### Minimum Viable 80+ Algorithm

For each JD:

1. `extract_requirements(jd)` -> structured required/preferred/keywords.
2. `score_resume(resume, requirements)` -> baseline score.
3. `build_gap_plan(resume, requirements)` -> missing and supported gaps.
4. `ask_user_to_confirm(gaps)` -> confirmed skills only.
5. `patch_skills_lines(confirmed_skills)` -> deterministic changes.
6. `rewrite_summary(requirements, confirmed_skills)` -> one concise summary edit.
7. `rewrite_top_bullets(top_3_gaps)` -> only 2-4 bullets.
8. `validate_changes()` -> anti-fabrication gates.
9. `render_pdf()` -> one-page gate.
10. `score_resume()` again.
11. If score < 80:
    - try one more compact iteration
    - otherwise report honest blockers and suggest human outreach/referral path.

### Recruiter Guardrail

An 80+ ATS score should never be accepted alone. Add a senior-recruiter review
step that checks:

- Is the candidate actually qualified for the role?
- Are the must-haves visible in the top third of the resume?
- Are added skills defensible in an interview?
- Does the resume read naturally?
- Is the seniority gap too large?
- Does the resume overfit the JD?

Only resumes that pass both ATS and recruiter guardrails should become
`ready_for_review`.

### Confirmation API Contract

`POST /latex/optimize` accepts:

```json
{
  "session_id": "...",
  "job_description": "...",
  "confirmed_skills": ["LangChain", "Azure"]
}
```

The optimizer may return:

```json
{
  "ats_target_score": 80,
  "ats_target_met": false,
  "confirmed_skills": ["LangChain"],
  "confirmation_required_skills": ["Azure", "Docker"],
  "warnings": [
    "Missing skills require user confirmation before patching: Azure, Docker"
  ]
}
```

Frontend behavior:

- Show `confirmation_required_skills` as checkboxes.
- Ask: "Which of these can you confidently discuss in an interview?"
- Re-run `/latex/optimize` with the selected `confirmed_skills`.
- Never auto-confirm skills for the user.
