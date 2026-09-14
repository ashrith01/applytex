# ApplyTeX: Jobright comparison and implementation plan

Audit date: September 9, 2026. This is the current comparison; the July audit
in [JOBRIGHT_AND_ATS_AUDIT.md](JOBRIGHT_AND_ATS_AUDIT.md) is historical evidence.
Implementation and validation updated September 10, 2026.

## Repository identity and pending work

The checkout's origin is `https://github.com/ashrith01/applytex.git`. After fetching,
both local HEAD (`codex/profile-resume-workspace`) and `origin/main` were
`cd0737eb3aa3cb90b3e8d2fc3515b68a1dfed79b` (`Improve application autofill reliability
(#19)`). There were zero ahead/behind commits. The local branch has no upstream.

The real folder is now `/Users/ashrithvadde/Documents/EDSAI/Projects/applytex`.
The old `latex-resume-matcher` path has been removed. A temporary compatibility
symlink was removed because the workspace sandbox could not use it as a root.
The virtualenv packages/entrypoints were reinstalled at the new path using
`uv sync --locked --reinstall`; stored SQLite artifact payloads were checked and
contained no old absolute project paths. Unpacked extension installations and
new checkouts should use `applytex`. Virtualenv activation scripts were also
updated and activation was checked at the new path.

Substantial work was already uncommitted before this audit:

- Extension profile/resume workspaces (`panel-profile.js`, panel integration,
  manifest/service worker), provider handling, fill events, and browser QA.
- FastAPI router extraction into `src/latex_resume/routers/`, logging setup,
  optional auth changes, and related API tests.
- Tailor Studio, PDF preview, and statement-diff frontend updates.
- Parser/rendering changes, LLM response handling, dependencies and lockfile.
- README and extension documentation updates.

These are local changes, not commits that `git push` alone would upload. All new
router files and `logging_config.py` must accompany the API changes; omitting
untracked files would break a fresh checkout. The new profile module likewise
must accompany the extension manifest. No changes were committed or pushed in
this audit. Existing work was preserved.

Recommended release grouping: API/refactor and dependencies; extension/profile
and browser fixes; Tailor Studio/PDF work; this audit and document-handling
increment. Several files overlap, so review hunks rather than staging by broad
directory. Validate and commit on a branch, then push it and review a PR.

## Live observations and limits

The intended product is **Jobright**, at [jobright.ai](https://jobright.ai/).
Inspected the signed-in [recommendations page](https://jobright.ai/jobs/recommend),
[Pindrop job detail](https://jobright.ai/jobs/info/6a98609711f73b6462c8e4e9), and
the installed extension on the employer's Greenhouse application.

The employer application was already populated and had a customized resume
attached before this audit. Only the optional preferred-name field was filled
during this audit. The resume chooser and Autofill Information were inspected;
no employer application was submitted, no resume/cover letter was generated,
and no credit-backed bulk Autofill action was invoked by this audit. This is
live UI inspection and a limited form interaction, not a measured end-to-end
Jobright autofill accuracy test.

The extension is a narrow white right-hand panel with:

- Company/job card, match badge, posting age/applicant count, insider connections.
- Prominent green Autofill action with a credit balance beneath it.
- Autofill Information, resume selection/upload, Generate Custom Resume,
  cover-letter upload/generation, and Autofill for Another Job.
- A bottom progress indicator and expandable required/optional field checklist.
- A resume chooser with primary/customized saved versions, template/original
  choices, download, and continue actions.
- Profile categories: Personal, Education, Work Experience, Skill, Equal
  Employment, Preference, Sign-up Information. An automatic profile-update
  switch was visible; its behavior was not exercised.

The panel claimed **14/14 required fields filled (100%)**, while the employer
DOM still showed an empty required cover-letter control and empty contact
fields. The cause was not investigated; the discrepancy is sufficient reason
to validate readiness against the employer's current form rather than trust
a cached completion badge. Do not copy that behavior into ApplyTeX.

Job detail displayed experience/skills/education fit, editable qualification
tags, required/preferred qualifications, referral and email-discovery entry
points, company metadata, sponsorship history, funding, leadership, and news.
These were observed UI features; underlying data accuracy was not validated.
The navigation exposed Resume, Profile, Agent, Coaching, and Interview. Paid
coaching, autonomous-agent execution, and interview content were not exercised.

## Feature matrix

“Present” means code exists in the current working tree, not production parity.
“Partial” means an underlying building block exists but not the complete flow.

| Capability | Jobright evidence | ApplyTeX status and code evidence |
|---|---|---|
| Job capture / external-job tracking | Extension and External view | Present: `routers/extension.py`, `application_store.py`, extension provider registry |
| Broad personalized job feed and filters | Signed-in recommendation feed | Partial: public Greenhouse/Lever/Ashby APIs, deterministic role/location matching; limited configurable board universe (`job_sources.py`, `job_matching.py`) |
| Match explanation and skills | Overall and category scores, editable tags | Present: required/preferred keywords and five-category screening (`ats.py`, `screening.py`); scores are not equivalent/calibrated to Jobright |
| Applicant counts and early-applicant signals | Job cards and detail | Missing verified data source and consistent UI |
| Sponsorship/company/funding/news intelligence | Job detail | Missing dedicated datasets, refresh jobs, and sourced company views |
| Insider referrals / contact discovery | Detail and extension | Missing; needs a licensed/public-data design and explicit outreach review |
| LaTeX tailoring and one-page review | Jobright has custom resumes; generation not run here | Present ApplyTeX specialization: source spans, locked facts, one-page gate, diff, PDF review (`parser.py`, `reconstructor.py`, `renderer.py`, tailor routes/UI) |
| Multiple reusable resumes / primary selection | Live resume chooser | Partial: one current profile resume plus per-application generated artifacts; no general reusable resume library |
| Resume quality diagnostics | July audit; not re-run here | Partial: deterministic evaluation, deductions, naturalness checks and Resume Lab; no complete severity-ranked issue/fix workspace |
| Autofill profile editor in extension | Live profile dialog | Present locally, not pushed: `panel-profile.js`, profile patch routes and tests |
| Reviewed field filling and existing-answer handling | Live panel/checklist; bulk fill not exercised | Present: field review, typed resolution, custom controls, repeatable records, per-question overrides (`form_resolution.py`, `panel.js`) |
| Provider breadth | Jobright advertises major ATS coverage | ApplyTeX registers 13 providers; deeper Workday/Ashby/Greenhouse/Lever logic, others generic or experimental; no claim of universal live parity |
| Application question drafts | Jobright assistance surface | Present: evidence-backed narrative drafting and edited overrides (`application_answers.py`, extension routes/panel); full acceptance/review UX needs continued audit |
| Cover letter file attachment | Upload entry point; empty required employer field observed | Implemented first slice in this audit: exact-field chooser and attachment-safe resolution |
| Full job-specific cover-letter generation/export/history | Live Generate Cover Letter action; output not tested | Partial building blocks: reusable short text, narrative generation, artifact type enum; complete workflow missing |
| Application tracker / follow-ups | Liked, Applied, External views | Present: pipeline stages, tasks, deadlines, notes, timeline, artifacts (`applications` routes and frontend) |
| Exact submitted record / interview-prep view | Not verified for Jobright | Partial foundations: artifacts, scans, overrides and events; no complete immutable submission bundle or dedicated interview-prep view |
| Automatic profile learning from form edits | Switch visible, behavior untested | Missing explicit proposed-change/diff review; current category saves are intentional |
| Orion career copilot / autonomous search agent | Navigation, Ask Orion and marketing | Missing integrated conversation, durable agent runs, evaluation and approval boundaries |
| Coaching / interview-question library | Navigation now; detailed July audit | Missing; separate content/service product |
| Credentials, hosted accounts, cloud sync | Signed-in hosted product | Local optional auth and SQLite exist; production tenancy, backup/recovery, hosted sync and release hardening remain |

## Prioritized implementation backlog

### P0 — Finish and release the current local application workflow

1. Review the existing uncommitted changes as cohesive commits, including all
   new modules. Verify clean install, Python tests, frontend build/lint/typecheck,
   and rendered provider checks. Update stale docs rather than rebuild existing
   persistence/auth/frontend features.
2. **Implemented here:** identify grouped/hidden document inputs; show an exact
   employer-field attachment action; rescan after selection; never treat a text
   answer or override as an uploaded file. Cover-letter controls must never
   receive the saved resume just because their surrounding copy mentions CV.
3. Complete release smoke checks on live Greenhouse, Ashby, Lever, and signed-in
   Workday. Record original/final field state, unmatched controls and employer
   upload errors. Keep experimental providers labeled until verified.

The September rendered smoke check also exposed a Workday step-detection issue:
after My Experience, Application Questions can keep the same job URL and use
button-based dropdowns. Detection now recognizes that active step with visible
question controls, without requiring a visible resume input or several native
text fields. This fixes a transition that previously left 11 scanned questions
without a fill plan.

Acceptance: empty required documents remain visible as missing; chosen files go
to the intended control; existing answers are not inadvertently overwritten;
no final employer submit occurs. Fixture coverage does not replace live checks.

### P1 — Application documents and submission evidence

**Cover-letter workspace:** build on application artifacts and grounded answer
generation. Provide draft, edit, preview, download, approve, and upload states;
support employer text and file variants. Use a separate letter format/length
policy instead of reusing the 100-word application-answer cap. Persist the exact
approved and edited letter per application, with grounding/source references.

**Submission bundle:** add versioned application-scoped records containing the
exact resume/letter bytes and hashes, job description/URL/company/role at that
time, field labels and submitted values/options, user edits and overrides,
resolution sources, page/step history, and status events. Distinguish prepared,
uploaded, user-reported submitted, and employer-confirmed submission. Bind a
bundle to the selected artifact IDs, not a mutable “latest resume” pointer.

**Interview preparation:** add an application detail view of exactly what was
supplied, linked claims/projects, answers and supporting evidence. Later profile
or job edits must not change this record. Employer credentials are excluded;
EEO/sensitive answers need explicit restricted access and retention controls.

Acceptance: change the profile, resume, job description, and a reused answer
after capture; the earlier bundle must remain identical. Cross-profile reads
must be rejected. Sensitive values must not leak in general timeline payloads.
Do not infer a successful submission merely from clicking Apply/Next.

### P2 — Resume library and diagnostics

Introduce reusable `ResumeVersion` records with an explicit primary version,
source/PDF/hash/provenance, archive state, and profile ownership. Reuse the
existing extension chooser and profile-resume page. Keep per-job tailored
artifacts distinct from reusable masters. Archiving a master must not delete
historical application evidence.

Expose existing evaluation/naturalness results as severity-ranked issues with
statement links and individually reviewable edits. Preserve source-span and
locked-section invariants; validate the one-page PDF after accepted changes.

Acceptance: select among several saved resumes, tailor from the selected one,
and verify the application references that precise version after primary changes.

### P3 — Discovery and company intelligence

Expand board configuration, deduplication, saved searches and freshness tracking;
add a scheduled ingestion service with failure reporting before promising a
large personalized feed. Generalize the existing role/geography assumptions.

Add sourced company records with timestamps and uncertainty. Treat sponsorship
history separately from role-specific sponsorship eligibility. Funding, news,
applicant counts and contacts each need a justified data source and refresh
policy; missing data should display as unknown. Do not fabricate datasets to
imitate Jobright cards.

Acceptance: every displayed external fact has provenance and an observation
date; expired jobs and source outages are visible; duplicate captures merge
without losing application history.

### P4 — Career assistance and interview content

Build application-specific interview questions from immutable evidence first.
Later add Orion-like chat with cited job/profile context and explicit permissions
for any external actions. An autonomous search agent requires durable queues,
retry/idempotency behavior, budgets, cancellation and review states. Referrals,
email discovery, curated interview banks and human coaching require separate
data/content/service work; they are not extension-only UI additions.

### Release track — Hosted operation

Require authentication and enforce ownership across every profile, artifact,
scan, draft and application route; address legacy in-memory sessions, backup/
restore, retention/export/deletion, tenant isolation, renderer sandboxing,
observability and extension distribution. Local opt-in auth is not sufficient
evidence of hosted production readiness.

## Validation for this increment

The expanded September 10 autofill work and evaluation strategy are documented
in [AUTOFILL_STRATEGY_AND_EVALUATION.md](AUTOFILL_STRATEGY_AND_EVALUATION.md).
It adds exact/alias option validation, correct placeholder counts and labels,
preservation of existing answers with explicit replacement, later-step detection,
and a real-API synthetic application lab. See
[ML_PORTFOLIO_ROADMAP.md](ML_PORTFOLIO_ROADMAP.md) for the proposed ML evaluation
and project extensions.

Final September 10 validation:

- **344 Python tests passed.**
- **39/39 synthetic real-API application cases passed, 1,583 assertions** across
  13 provider identities. Checks include repeated fills after user corrections,
  explicit replacement and reset, required-field placeholders, option matching,
  current/future sponsorship, EEO opt-in, documents and multi-step discovery.
- The separate existing provider-specific fixture run passed 12/13. Its Workday
  failure exposed a reporting collision between repeated field labels. The fix
  uses field identity when available; the targeted Workday rerun passed with
  zero failures and zero browser errors. Combined regression coverage is 13/13;
  this was not a single fresh all-provider run after that last fix.
- The visible Chrome lab demo filled Avery's name, Java, current sponsorship No,
  future sponsorship Yes and the lazy age dropdown Yes; EEO and unknown Rust
  duration stayed blank. Its local submission counter remained zero.
- JavaScript syntax and Git whitespace checks passed. Frontend typecheck, lint,
  production build and the one-page engine smoke had passed earlier in this
  task; the subsequent changes did not modify frontend or engine code.
- GitHub main was rechecked September 10 and remains at the same commit as HEAD.
  All pending source changes remain uncommitted and unpushed.

Machine-readable synthetic results are summarized in
[`evaluation/autofill-2026-09-10.json`](evaluation/autofill-2026-09-10.json).

Baseline before this increment: 316 tests passed; **318 passed** after the
document-handling change. Frontend typecheck, ESLint and production build passed
against the existing local changes. JavaScript syntax and diff checks passed. New regression
tests cover typed answers/overrides versus file requirements and preserving an
already-selected document. The rendered Greenhouse fixture exercises a hidden
cover-letter input with its required label on an enclosing ARIA group and
asserts that attachment does not populate the resume input. This targeted
Greenhouse check passed with zero failures or browser errors. Its report is local
at `.applytex/qa/jobright-documents.json` (synthetic data, not a live application).

The browser harness also needed corrections: fixture employers were named after
ATS providers and rejected by the production branding filter; the mock fill-plan
API disabled all filling for a missing required answer, unlike the real API's
partial-fill behavior. The harness now uses distinct employer names, mirrors
partial fill, and exits nonzero when a fixture fails.

The earlier July provider counts should not be interpreted as current production
coverage. None of these synthetic results establish Jobright equivalence or
employer acceptance on untested live forms.
