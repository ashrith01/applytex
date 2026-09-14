# ApplyTeX vs Tsenta: where the repo stands and how to close the loop

Audit date: September 13, 2026. Companion to
[JOBRIGHT_PARITY_PLAN.md](JOBRIGHT_PARITY_PLAN.md) (September 9). This document
compares ApplyTeX against **Tsenta**, records a senior-developer review of the
codebase, folds in two persona audits (business stakeholder, end user), and
sets a phased plan. The goal is a personal tool that cuts the owner's own
application effort first, with a clean path to multi-user later.

## 1. What Tsenta is

Sources: [tsenta.com](https://tsenta.com/), [/auto-apply](https://tsenta.com/auto-apply),
[docs: introduction](https://docs.tsenta.com/product/introduction),
[how auto-apply works](https://docs.tsenta.com/product/how-auto-apply-works),
[supported job boards](https://docs.tsenta.com/product/supported-job-boards),
[profile and resume](https://docs.tsenta.com/product/profile-and-resume),
[LoopCV review](https://www.loopcv.pro/directory/tsenta/).

Tsenta is a YC-backed cloud agent that runs a four-stage loop **without the
user opening the ATS**:

| Stage | What Tsenta does |
|---|---|
| **Find** | Monitors 50,000+ career pages across 19–28 ATS (Workday, Greenhouse, Lever, Ashby, iCIMS, SmartRecruiters, Workable, Rippling, BambooHR, UKG, SuccessFactors, Teamtailor, …). Scores every new role against the profile; match feed ranked by fit; filters for role family, location, salary, seniority, "what to avoid". Jobs can also be pasted in by link. |
| **Prep** | Per-job resume tailoring in three modes (Off / Honest = reword existing content only / Aggressive). Diff shown; "resume auto-approval" toggle. Cover-letter generation. |
| **Apply** | Cloud browser logs in to the real ATS, fills every field, drafts open-ended answers from the profile and reusable "application defaults", uploads documents, handles "supported account, verification and one-time-passcode steps", and submits. "Review before submit" pauses after fill where the ATS allows it. "Auto Apply" = match threshold + filters + daily cap → selects and submits with no per-job click. |
| **Track** | A receipt per application (every field, every answer, resume version, timestamp). Optional email integration routes recruiter replies to the application and auto-updates status: applied / viewed / replied / interview / ghosted / offer / rejected. |

Also: multiple resume profiles with a default, DOCX import, web + mobile +
iMessage + Chrome extension (detection only on LinkedIn/Indeed; "detection ≠
submission") + MCP server. Pricing: 25 free, then $19 / $39 / $99 per month for
600 / 1,500 / 4,500 applications. Disclosed limits: no promise automation is
undetectable; user responsible for accuracy; no public detail on credential
handling or CAPTCHA.

**The insight that matters for this plan:** Tsenta's moat is not the form
filler. It is (1) always-on ingestion and matching, (2) an executor that
removes the "open the ATS" step, and (3) a receipt plus email-routed tracker
that closes the loop. ApplyTeX already has a *better* resume engine than
Tsenta's "Honest" mode (byte-preserving LaTeX, evidence gates, one-page
geometry). What it lacks is the loop.

## 2. Where ApplyTeX stands (verified against code, not docs)

### 2.1 Gap matrix

| Capability | Tsenta | ApplyTeX | Rating | Evidence |
|---|---|---|---|---|
| Resume tailoring per job | Honest/Aggressive rewrite | LaTeX-preserving, evidence-gated, one-page geometry gate | **Ahead** | `routers/tailor.py`, `optimizer.py`, `renderer.py`, 325 tests |
| Reviewed form fill (user's tab) | Cloud fill | 13-provider extension; deterministic resolution; exact/alias option matching; never guesses tri-state facts | **Done** | `form_resolution.py`, `option_matching.py`, `panel.js` |
| Application tracker | Pipeline + receipts | Kanban, state machine, artifacts, events, tasks | **Done** (no receipt) | `routers/applications.py`, `application_store.py` |
| Narrative screening answers | "In your voice" | ≤100-word grounded draft, validator rejects uncited metrics/tools | **Done** | `application_answers.py:362-416` |
| Short screening answers (18+, relocate, salary, degree) | Profile defaults | Catalog matcher misses; same 7 Workday questions unresolved every run | **Partial** | `.applytex/qa/autofill-workday-regression.md:21-27`, `form_resolution.py:1304-1310` |
| Job discovery feed | 50k pages, 19+ ATS, ranked feed | Manual provider + board-token lookup; 3 public APIs; roles/locations hard-coded to AI/ML + "Remote - US" | **Partial** | `job_sources.py:118`, `jobs/page.tsx:34-38`, `job_models.py:73-92` |
| Apply without opening the ATS | Cloud executor | None by design; `can_submit=False`, final Submit blocked | **Missing** | `api.py:313`, `panel.js:2449-2503` |
| Cover letter | Generated | Enum + FK only; "Attach document" opens native chooser | **Missing** | `job_models.py:208,284`, `panel.js:1741-1758` |
| Submission detection / receipt | Receipt per app | Status is self-reported by dragging the kanban card | **Missing** | `applications/page.tsx:341-349` |
| Recruiter email → status | Email routing | None (zero hits for smtp/imap/webhook) | **Missing** | — |
| Follow-ups / notifications | Pipeline reminders | `follow_up` task category only; no reminders | **Missing** | `job_models.py:356` |
| Multiple resume masters | Resume profiles | One profile resume + per-application artifacts | **Partial** | JOBRIGHT plan P2 |

### 2.2 Senior-developer review

**Shape.** ~31k LOC: FastAPI backend (51 routes / 7 routers), Next.js 15
frontend (8 pages, ~3.2k LOC), MV3 extension (~8k LOC), Streamlit legacy UI
(2.7k LOC), benchmark package. CI runs pytest on 3.12/3.13 with TeX Live, `uv
build`, and frontend lint/typecheck/build. 40 files uncommitted on
`codex/profile-resume-workspace`; `routers/`, `logging_config.py`,
`option_matching.py`, `panel-profile.js` are **untracked**, so `main` does not
match the docs.

**Test status (verified):** plain `uv run pytest` is *interrupted at
collection* — `tests/test_autofill_lab.py:7` imports `scripts.autofill_lab`
but `pyproject.toml:62` sets `pythonpath = ["src"]` and there is no
`scripts/__init__.py`. With that file excluded, 343 pass. CI will go red the
moment the branch is committed. CI also never runs the browser QA scripts, so
autofill behaviour is not gated.

**What is well designed (keep):**

1. Domain contracts in one module: `job_models.py` holds `JobPosting`,
   `CandidateProfile`, `FormQuestion`, `FillAction`, `ApplicationRecord` and an
   explicit `ALLOWED_APPLICATION_TRANSITIONS` state machine. This is the right
   spine for multi-user.
2. Ownership enforced at the dependency layer: `routers/_deps.py`
   `require_*_for_profile` returns 404, not 403 — no cross-profile leak.
3. Deterministic-first answer resolution. `ApplicationFactsProfile` fields are
   `bool | None`; unknown → `skip` with `answer_source="user_input"`
   (`form_resolution.py:1628`); LLM drafting only for narrative textareas
   (`is_question_draft_eligible`, line 512). This is the safety posture Tsenta
   *claims* but does not document.
4. The extension hard-blocks the final Submit button
   (`panel.js:2449-2503`). Correct default while trust is built.
5. Evidence culture: `.applytex/qa/*`, `docs/evaluation/*.json`, a
   39-scenario synthetic lab against the real API.

**Structural problems, ranked by how much they block the Tsenta direction:**

1. **`api.py` (1,655 LOC) is still a god module and every router imports back
   into it** (`from latex_resume.api import ...` in all seven). Request/response
   schemas and helpers like `_build_fill_plan_for_scan`, `_apply_plan_overrides`,
   `_profile_view` live in the app factory. Nothing there can be reused by a
   non-HTTP worker — which the Find and Apply loops need. Fix: `schemas/` for
   Pydantic models, `services/` for fill-plan / profile / tailor helpers;
   routers depend downward only.
2. **Two session stores, one durable.** `session.py` (in-memory, TTL loop at
   `api.py:623`) vs `tailor_store.py` (SQLite). Delete the classic `/latex/*`
   flow or port it; carrying both is the worst option.
3. **`panel.js` is a 6,698-line IIFE with 231 inner functions and a 45-key
   mutable `state`.** Auth, profile editing, scanning, Workday record matching,
   rendering, fill execution and API transport all share one closure. The
   split started (`panel-profile.js` 768 LOC) but `panel-scan.js`,
   `panel-fill.js`, `panel-workday.js` are 15–60-line shims. Any executor that
   runs outside the extension (Playwright) needs the DOM scan/fill primitives
   as a plain module with no `chrome.*` or panel-state dependency.
4. **No background execution primitive.** The only async task is session
   cleanup. No scheduler, no queue, no worker table. Find and Track both mean
   "runs while you sleep"; the repo has nowhere to put that.
5. **Discovery is a form, not a feed** (see matrix). `TargetRole` and
   `DEFAULT_PREFERRED_LOCATIONS` are hard-coded to the owner's search.
6. **`optimizer.py` (3,132) and `form_resolution.py` (1,805; ~30 `_workday_*`
   helpers mixed into generic resolution)** are second-tier god modules.
7. **Docs drift.** `CLAUDE.md` has no `routers/`, extension, or application
   store; `ARCHITECTURE.md` says browser scanning "is not part of the current
   phase". Agents planning from these docs will mis-plan.
8. **Streamlit app (2.7k LOC)** has no product role now that Next.js exists.

**Data-model gaps for the loop:** no submission bundle (which resume bytes,
answers, options — so no receipt); `CandidateProfile.custom_answers` is a flat
`dict[str, str]` with no provenance, alias, or last-used; no
`watchlist` / `ingestion_run` tables; no email or notification integration.

### 2.3 Business-stakeholder audit (agent)

- End-to-end today: upload `.tex` → confirm skills → one-page tailored PDF
  with diff → approved artifact tied to an application; capture from 13 hosts;
  scan + one-click reviewed fill; grounded narrative drafts; profile editing in
  web and panel; kanban tracker; single-board search.
- Half-built: discovery is a lookup; cover letter is an enum; follow-ups are a
  task category; "submitted" is self-reported; autofill accuracy is measured
  only on fixture HTML (39/39 synthetic; Workday failed 1/1 live before a
  targeted fix; Workable/Indeed/ZipRecruiter/Glassdoor/Wellfound/Dice never
  live-verified).
- Top personal-effort wins: saved board watchlist + scheduled ingestion (M);
  fix the Workday unresolved-question catalog (S); cover-letter artifact (M);
  follow-up reminders (S); auto-advance stage on complete fill (S).
- Risks: ToS/legal for server-side submission on LinkedIn/Indeed/Workday
  accounts (the "user clicks Submit" boundary is the defensible position);
  selector-heuristic fragility with no CI gate; EEO/salary/resume text in
  unencrypted SQLite plus JD/resume text sent to third-party LLMs and web
  research fetched into prompts (`application_answers.py:209`).
- Multi-user blockers: auth off by default and `X-Profile-Id`/`?profile_id=`
  trusted when off (`_deps.py:48-49`, `profiles.py:53-60` no ownership
  check); `GET /jobs/{job_id}` unscoped (`jobs.py:59-68`); tailor sessions
  with empty `profile_id` readable by anyone (`tailor.py:311`); tokens in an
  in-process dict (`local_auth.py:63,101`); one global "active profile"
  setting; one server-wide `.env` of LLM keys; extension hard-codes
  `127.0.0.1:8000`; no migrations, backup, export/delete.

### 2.4 End-user walkthrough (agent)

Journey: ~10 install clicks plus two terminals; username-only sign-in whose
token dies on API restart; ~30 minutes of hand profile entry; must open the
panel on the JD **before** clicking Apply (`panel-shared.js:58`,
`panel.js:397-402`); scan is automatic; fill is one click; "Continue" is one
click per page; Submit and status are manual; tailoring is a separate five-step
wizard whose Approve is disabled unless opened from the extension
(`tailor-client.tsx:595-603`).

Frustrations ranked: (1) two servers must be running; (2) open-the-JD-first
rule; (3) the same seven Workday questions skipped every run even though 18+,
relocate and desired income are in the profile; (4) custom answers match by
label alias only; (5) cover letter is manual; (6) submission tracking manual;
(7) tokens vanish on restart; (8) tailoring is a separate tab.

Trust risks worth fixing before any auto-submit: backend skips option
validation when a select's options are lazy-loaded (`form_resolution.py:1664`)
and the browser then treats any "Yes, …" as Yes and picks the first match
(`panel.js:5511-5529, 6000`) — Workday has `exactOnly`, other providers do
not; AI drafts are typed into the page without a click (`panel.js:3894-3975`);
resume file-input selection is heuristic scoring (`panel.js:6620-6631`);
profile extraction is regex.

Verdict: usable daily only for Workday and Greenhouse once the profile is
filled; the single flip is a **review-gated resolver for short unknown
screening questions that maps them onto existing profile facts and saves the
mapping**, so the unresolved count shrinks with use.

## 3. The plan

Ordering principle: each phase must reduce the owner's own daily effort on its
own, and the executor (the "don't open the ATS" feature) comes *after* the
resolver and the receipt, because an executor that skips seven questions and
leaves no receipt is worse than the current extension.

### Phase 0 — Land the branch (1 day)

1. Fix collection: add `scripts/__init__.py` **or** set
   `pythonpath = ["src", "."]` in `pyproject.toml`. Run `uv run pytest` clean.
2. Commit the 40 files as the four groups JOBRIGHT plan recommends (API
   refactor + deps; extension/profile; Tailor Studio/PDF; docs). Push, open a
   PR, get CI green.
3. Update `CLAUDE.md` module map (routers, extension, application store,
   form resolution) and `ARCHITECTURE.md` runtime section. **Streamlit stays**
   (owner decision, 2026-09-13: frontend UI work is deferred).
4. Add a `browser-qa` CI job running `scripts/autofill_lab_qa.mjs` headless.

### Phase 1 — Shrink the unresolved count (3–4 days) — **implemented 2026-09-13**

Persona-agreed highest-value fix. Turns the fill from "70% then I type" into
"review and continue".

Implementation notes: `profile_answers` table + `SavedAnswer` model;
`resolve_form_questions(saved_answers=…)` consults the bank only where profile
facts could not answer; `remember_answer()` routes typed booleans to profile
facts; `POST /extension/forms/{scan}/plan` gained `remember`; new
`/answers/propose` and `/answers/used` routes; `/profile/answers` CRUD; panel
gained "Suggest answers from saved facts", per-suggestion "Use and remember /
Use once", and a remember prompt on one-off answers. Item 4 (lazy selects) was
found already safe: the browser's `uniqueOption` refuses ambiguous matches.
The frontend answers-bank page (item 5) is deferred with the rest of the UI.

1. **Answers bank table.** `profile_answers(profile_id, intent, prompt_text,
   normalized_prompt, value, aliases[], source ∈ {user, resolved, llm_reviewed},
   ats_provider, last_used_at, use_count)`. Replace `custom_answers: dict`.
2. **Intent catalog expansion** in `form_resolution.py` for the seven
   recurring Workday intents (18+, relocate, travel, desired income, highest
   degree, start date, hear-about-us) mapped to existing
   `ApplicationFactsProfile` / `EducationProfile` fields.
3. **Review-gated LLM resolver for short questions.** For `required` fields
   still `skip` after deterministic resolution and *not* sensitive: ask the
   LLM to (a) classify intent, (b) propose a value **only from profile facts**,
   (c) cite the fact. Panel shows it as "Proposed — confirm". On confirm, save
   to the answers bank with the prompt alias so the next form resolves
   deterministically. Never auto-fill; never for EEO/authorization/salary
   unless the value is an explicit stored fact.
4. **Close the lazy-select hole.** When `options` is empty at plan time, mark
   the action `defer_until_options` and have the browser re-request validation
   after opening the control; drop the "starts with yes" heuristic for
   non-Workday providers (`panel.js:5511`).
5. Frontend: answers-bank page under Profile with per-row "last used on".

### Phase 2 — Passive discovery (4–5 days) — **implemented 2026-09-13**

Replaces "type a board token" with "new matches since yesterday".

Implementation notes: `WatchlistEntry` / `IngestionRun` models and tables;
`watchlist.py` ingestor (concurrent board fetch, preference filter, resume
fit score, `first_seen_at` preserved across refreshes, per-board error and
count bookkeeping); lifespan scheduler; `/watchlist*` routes and
`GET /jobs/feed`; `applytex-watchlist` CLI with a markdown digest
(`--markdown auto` → `.applytex/feed/<date>.md`). The bundled seed
(`data/watchlist_seed.json`) holds 103 boards verified live on 2026-09-13,
tagged by domain (`robotics`, `autonomous_driving`, `llm`, `ml_infra`,
`agents`, `product`, …). Aurora, Applied Intuition, Boston Dynamics, Groq,
Cruise, Rippling and a few others expose no public board API and stay
capture-only. SmartRecruiters / Workday-JSON sources and the daily digest
notification are deferred; the feed UI is CLI + API until the frontend work
resumes.

1. **Watchlist + ingestion tables:** `watchlist(profile_id, provider,
   board_token, company, domain_tags[], enabled)`, `ingestion_runs(run_id,
   started_at, source_count, new_jobs, errors)`. Seed from the companies
   already in `benchmark_data/manifests/live_job_boards.json` and captured
   jobs.

   **Owner-specified scope (2026-09-13):** a curated list of 50–100 top
   *product-based* companies that hire the `TargetRole` families (AI / ML
   engineer, data scientist, NLP / LLM, agentic AI, and their intern variants)
   plus companies in AI-application domains — **robotics** and **autonomous
   driving** first. Each entry carries `domain_tags` (e.g. `llm`, `robotics`,
   `autonomous_driving`, `platform`) so the feed can be filtered by domain.
   Only companies whose careers page is on a public-API ATS (Greenhouse, Lever,
   Ashby, SmartRecruiters, Workday-JSON) are ingestible; others are
   capture-only via the extension.
2. **Scheduler.** An `asyncio` loop in lifespan (APScheduler if cron-style
   control is wanted) polling every N hours; dedupe by `apply_url` and stable
   job id; store `first_seen_at`.
3. **Sources.** Keep Greenhouse/Lever/Ashby; add SmartRecruiters public
   postings API and Workday's per-tenant JSON jobs endpoint (unofficial —
   verify per tenant, keep it behind a flag). Skip LinkedIn/Indeed ingestion
   (ToS).
4. **Match feed.** Score each new job with the existing `ats.check_ats` +
   `screening.analyze_screening_fit` against the profile resume; store
   `fit_score` on the job; `/jobs/feed?since=` route; dashboard "new since
   last visit" with a match threshold slider. Generalize `TargetRole` into
   profile-owned role families.
5. Optional: a daily digest (local notification or email via SMTP env vars)
   — first notification primitive in the repo.

### Phase 3 — Close the loop: receipt, cover letter, status (4–5 days) — **implemented 2026-09-14**

Implementation notes: `submission.py` (receipt built from the latest scan per
step + the reviewed plan, resume/cover-letter provenance by artifact id and
SHA-256, BFS walk through the approval gate, follow-up task), `cover_letters.py`
(grounded draft → edit → approve → PDF, with a validator that rejects numbers
absent from the resume/JD, links, and placeholders), routes
`POST/GET /applications/{id}/submission`, `/cover-letter*`,
`/artifacts/{id}/file`, `/tasks/due`, `/tasks/{id}/complete`,
`POST /extension/forms/{scan}/fill-result`. Extension: detection prompt
("Looks like this application was submitted — Confirm / Not yet"), manual
"Mark as submitted", fill-result reporting, Cover letter section in the
Tailor tab, "Attach ApplyTeX cover letter" on cover-letter upload fields.
Decision: detection *prompts*; it never auto-advances (false positives on
saved-draft banners were the risk). Email → status routing and notifications
remain deferred.

1. **Submission bundle** (JOBRIGHT P1): `submission_bundles(application_id,
   resume_artifact_id, resume_sha256, cover_letter_artifact_id, job_snapshot,
   fields[] {label, value, option, source}, step_history, created_at)`. Frozen
   at approve; later profile edits never mutate it. This *is* the Tsenta
   receipt.
2. **Submission detection in the extension.** Observe navigation/DOM for
   thank-you / confirmation pages per provider (Greenhouse "Thank you for
   applying", Workday "Application submitted", Lever/Ashby equivalents);
   transition `approved → submitted`, stamp `submitted_at`, attach the bundle.
   Manual "Set submitted" remains as fallback.
3. **Auto-advance:** fill with zero unresolved required → `ready_for_review`;
   user Approve in panel → `approved`.
4. **Cover-letter workspace.** Reuse `generate_application_answer` with a
   letter-length policy (250–350 words), grounded in the tailored artifact +
   JD; store as `COVER_LETTER` artifact (PDF via the same renderer, `.txt`
   for textarea variants); "Attach document" offers it by exact field.
5. **Follow-up tasks:** auto-create `follow_up` 7 days after `submitted`;
   due-today list on dashboard.

### Phase 4 — The executor: apply without opening the ATS (2–3 weeks) — **first slice implemented 2026-09-14**

Implementation notes: instead of extracting `panel.js` primitives first, the
executor (`scripts/executor.mjs`) injects the *real* panel scripts into a
Playwright page with a `chrome.*` shim (the same seam the synthetic lab uses)
and drives the panel's reviewed fill; a one-line panel hook
(`window.__applytexExecutor`) binds the run's application. `ApplyRun` +
`apply_runs` table, `/applications/{id}/apply-runs`, `/apply-runs/*`
(claim / progress / approve / resume / cancel / submitted / screenshot).
Submit is clicked only in `submitting`, reached from `approved` with a minted
token that `POST /apply-runs/{id}/submitted` verifies. Sign-in, CAPTCHA, MFA
and unresolved questions pause as `awaiting_input`. Dedicated Chrome profile
under `.applytex/browser-profile`. `scripts/executor_lab_qa.mjs` runs the
whole loop against the lab. Deferred: auto-tailor before fill, Workday
(behind `--allow-provider`), the `panel.js` decomposition, a feed UI action.

This is the Tsenta feature. Build it for **personal use on the owner's own
machine and own logged-in browser**, not as a cloud service. Server-side login
to Workday/LinkedIn with stored credentials is the ToS and security risk the
stakeholder audit flags; a local Playwright **persistent context** on the
user's Chrome profile stays inside "the user's own browser, the user's own
session".

1. **Extract DOM primitives from `panel.js`** into `extension/core/{scan,fill,
   options,workday}.js` with zero `chrome.*` or panel `state` references;
   `panel.js` and Playwright both import them. This is also the panel.js
   decomposition the codebase needs anyway.
2. **`apply_runs` table + worker:** `apply_runs(run_id, application_id,
   status ∈ {queued, running, paused_for_review, awaiting_input, submitted,
   failed}, step_log, screenshot_paths)`. Worker = Python `asyncio` loop
   driving Playwright with the core modules injected via `page.add_init_script`.
3. **Flow:** feed match → user clicks "Prepare" (or auto-prepare above a
   threshold) → tailor session runs → resume auto-approve toggle → executor
   opens the apply URL, scans, plans, fills, uploads bundle documents,
   continues through steps → `paused_for_review` with a screenshot + field
   list in the web app → user clicks **Approve & submit** → executor clicks
   Submit (the *only* place the final-submit block is lifted, and only with a
   per-run approval token) → receipt.
4. **Anything unresolved → `awaiting_input`** with the exact question; answer
   in the web app writes to the answers bank and resumes the run.
5. **Never** bypass CAPTCHA/MFA; on detection, pause and hand the tab to the
   user (persistent context makes that a normal Chrome window).
6. Start with Greenhouse, Lever, Ashby (verified adapters, no login). Workday
   second (uses the owner's existing session). LinkedIn/Indeed stay
   capture-only.

### Phase 5 — Multi-user hardening (when the personal loop is stable) — **5a implemented 2026-09-14**

5a notes: every route now resolves its acting profile through
`resolve_request_profile_id` (audit found `/profile*`, `GET /jobs/{id}`,
tailor sessions, classic `/latex/*` sessions unscoped); `PUT /profile` cannot
write another profile; `GET /profiles` and `/auth/status` no longer leak with
auth on; bearer tokens persist hashed in `auth_sessions` with TTL and
`POST /auth/logout`; password rotation needs proof of ownership; rate limits
key per profile; `GET /profile/export` and `DELETE /profile` (typed
confirmation); `schema_migrations` table + runner; the 57 wire models moved
from `api.py` to `schemas.py`. Remaining for 5b: extension options page
(configurable API base + token), classic-session port to SQLite, per-profile
LLM keys/budgets, encryption at rest for EEO/compensation, Web Store packaging.

1. Auth required by default; drop trust in `X-Profile-Id` when a token is
   present; scope `GET /jobs/{id}` and empty-`profile_id` tailor sessions.
2. Persist tokens (`auth_sessions` table) with expiry and revocation.
3. Per-profile LLM keys / budgets; per-profile rate limits.
4. `schemas/` + `services/` split of `api.py`; port or delete `session.py`.
5. Alembic-style migrations for SQLite (or move to Postgres); export/delete
   endpoints; encrypt EEO/salary columns at rest.
6. Extension: configurable API base + token in options page; package for
   the Web Store.
7. Executor as an opt-in local companion, never a hosted service, unless a
   legal review of each ATS's terms is done first.

## 4. Decisions needed from the owner

1. **Executor boundary:** local Playwright on your own Chrome profile with
   per-run "Approve & submit" (recommended) vs. keep the extension-only
   posture and stop at Phase 3.
2. **Discovery universe:** a curated watchlist of ~50–100 target companies
   (recommended; cheap, reliable) vs. broad crawling.
3. ~~**Streamlit:** delete or archive.~~ Decided: keep for now.
4. **Notification channel** for the daily digest: none / macOS notification /
   email.

Decisions 1 (local executor, recommended) and 2 (curated watchlist) were
accepted on 2026-09-13; Phase 0 started the same day.

## 5. Effort summary

| Phase | Outcome | Effort |
|---|---|---|
| 0 | Green CI, docs match code, Streamlit gone | 1 day |
| 1 | Unresolved count shrinks with use; lazy-select hole closed | 3–4 days |
| 2 | Daily "new matches" feed from a watchlist | 4–5 days |
| 3 | Receipt, auto status, cover letter, follow-ups | 4–5 days |
| 4 | Apply without opening the ATS (local, review-gated) | 2–3 weeks |
| 5 | Multi-user ready | 1–2 weeks |
