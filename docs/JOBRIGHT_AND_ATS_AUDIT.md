# Jobright Comparison and ATS Autofill Audit

Audit date: 2026-07-18

## Outcome

ApplyTeX now has code-level support for 13 providers and a reviewed autofill
workflow that preserves existing answers, uploads the saved resume, fills known
profile facts, and never submits the employer form. The automated browser matrix
passes 130 varied applications: 10 fixtures for each provider, zero failures,
zero browser errors, and zero unresolved required questions outside the profile
catalog.

Live end-to-end verification is intentionally incomplete in two places:

1. Jobright Autofill on the selected NBCUniversal SmartRecruiters application
   would transmit the candidate's contact/profile data and resume and may consume
   one of four displayed credits. It has not been clicked without action-time
   authorization.
2. The live Athena Workday application remains on the employer's sign-in page.
   The signed-in My Information, My Experience, Voluntary Disclosures, and Review
   pages cannot be inspected until the user signs in.

No employer form was submitted during this audit.

## Evidence Levels

| Level | Meaning | Providers/features |
|---|---|---|
| Live verified | Inspected in the authorized Chrome session | Jobright product/profile/resume/extension; LinkedIn, Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS; Workday entry and sign-in workflow |
| Automated browser verified | Real Chromium, rendered fixture UI, extension panel, API mock, resume upload, reviewed fill, post-fill rescan | All 13 providers, 10 varied forms each |
| Code/unit verified | Deterministic backend behavior covered by Python and static extension tests | Profile answer resolution, record indexing, dates, skills, EEO consent, identity, CORS, existing-value preservation |
| Pending live verification | Registered and synthetically verified, but no current public application completed in the live session | Workable, Indeed, ZipRecruiter, Glassdoor, Wellfound, Dice; signed-in Workday; Jobright credit-backed fill |

Synthetic coverage is regression evidence, not proof that a provider will never
change its production DOM. Provider-specific live smoke checks remain part of the
release checklist.

## Jobright Feature Inventory

### Job discovery and job intelligence

Jobright presents:

- recommended, liked, applied, and external-job views;
- total match plus Experience Level, Skills, and Industry Experience signals;
- a candidate-facing "Why this job is a match" explanation;
- applicant counts, compensation, workplace type, seniority, and role metadata;
- H-1B sponsorship likelihood and historical sponsorship trends;
- company growth, funding, leadership, news, and employee-review context;
- Ask Orion and an Agent workflow for search planning and career guidance.

ApplyTeX currently has deterministic job scoring and a required/preferred/JD
keyword explanation, but it does not provide Jobright's market, sponsorship,
company, applicant, or recruiter-intelligence datasets.

### Resume management and optimization

Jobright provides:

- multiple saved resume slots and a primary resume;
- a structured editor for summary, education, experience, skills, projects,
  publications, and certifications;
- resume ordering, export, deletion, and re-analysis;
- a letter-grade report with urgent, critical, and optional issues;
- issue explanations such as brevity, effectiveness, and filler-word feedback;
- an extension action to generate a custom resume for the current job;
- separate resume-analysis and application-autofill credit displays.

The inspected Jobright resume report was a general resume-quality report. The
custom-resume generation action was not run, so its exact job-description rewrite
strategy is not yet verified.

ApplyTeX's differentiators are:

- direct statement-level edits to the original LaTeX source;
- byte-preserving reconstruction outside changed statement spans;
- locked education/certification/publication/personal sections;
- explicit confirmation for skills not already supported by the resume;
- required/preferred/keyword evidence reporting;
- a hard one-page PDF gate;
- a reviewable diff before resume upload.

### Jobright extension autofill

On a live SmartRecruiters application, Jobright displayed:

- "Add This job in one click";
- Autofill and the remaining autofill-credit count;
- the current resume and Upload Resume;
- Generate Custom Resume;
- upload or generate cover letter;
- Your Autofill Information;
- a candidate match/tailoring entry point.

The Autofill Information dialog contains:

- Personal;
- Education;
- Work Experience;
- Skill;
- Equal Employment;
- Preference;
- Sign-up Information.

Preference includes expected salary, next-job start date, and a general
"anything else" answer. Sign-up Information offers to retain a Workday
registration email and password in browser local storage.

ApplyTeX intentionally does not store or fill employer passwords. Authentication,
CAPTCHA, MFA, Next, Save, and Submit remain user actions. This is a deliberate
privacy and safety difference, not a missing autofill field.

### Coaching and interview preparation

Jobright also includes:

- recruiter coaching packages for first-job, no-response, and interview needs;
- an interview-question database grouped by coding, system design, and behavioral
  topics;
- company-level question counts and freshness;
- Orion/Agent setup for profile, target role, market fit, autofill, and agent
  settings.

ApplyTeX does not currently implement coaching, interview-question discovery, or
an autonomous job-search agent.

## Feature Comparison

| Capability | Jobright | ApplyTeX | Recommended direction |
|---|---|---|---|
| Job match explanation | Experience, skills, industry, overall match | Required, preferred, JD keywords | Keep ApplyTeX's evidence detail; add role/industry/seniority categories |
| Company and market intelligence | Strong | Minimal | Add sponsorship, company, applicant, and market data only from defensible sources |
| Resume format | Structured profile document | Original LaTeX source | Preserve LaTeX as the primary differentiator |
| Resume quality report | Letter grade and issue severities | ATS score, one-page gate, diff | Add severity-ranked writing diagnostics without weakening truthfulness rules |
| Job-specific resume | Extension generation action | Guided LaTeX tailoring | Live-compare output only after credit-backed Jobright generation is authorized |
| Autofill explanation | Profile/resume actions and credits | Already filled / ready / needs answer, current step, safety note | ApplyTeX is clearer about field-level review; add provider guidance where needed |
| Password handling | Optional Workday credential storage | Never stores passwords | Keep the ApplyTeX boundary |
| Submission | Jobright markets one-click/agent flows | Manual employer submission only | Keep manual submission until a separate approved production workflow exists |
| Coaching/interviews | Present | Absent | Lower priority than reliable ATS coverage |

## Provider Workflow and Question Matrix

| Provider | Observed or expected workflow | Distinct questions/controls | ApplyTeX status |
|---|---|---|---|
| LinkedIn | Easy Apply modal, sometimes one-step, sometimes multi-step | Contact, phone country, resume choice/upload, top-choice checkbox, follow-company checkbox | Live inspected; generic modal scan and stable job identity covered |
| Greenhouse | Usually one long form with conditional questions | Contact, resume, cover letter, links, restrictive agreements, authorization, sponsorship, EEO | Live inspected and browser-tested |
| Lever | Usually one application page | Resume, full name, email, phone, current location/company, social/portfolio links, EEO | Live inspected and browser-tested |
| Ashby | Overview/Application tabs and React/custom controls | Preferred name, custom location combobox, start date, authorization, sponsorship, onsite availability, acknowledgements, additional info, EEO | Live inspected; required custom combobox and custom Yes/No controls fixed and tested |
| Workday | Create Account/Sign In, My Information, My Experience, Voluntary Disclosures, Review | Address and phone types, work history, education, split dates, current flags, skills, resume, authorization, sponsorship, EEO | Entry workflow live inspected; signed-in pages pending; record selector, dates, skills, and second-record behavior browser-tested |
| iCIMS | Job iframe, apply link, email gate, returning-candidate/account flow, CAPTCHA, application | Email-first gate, resume/profile, later application questions | Live inspected; same-origin iframe traversal and one-field apply-mode scan fixed |
| SmartRecruiters | Easy Apply, personal info, Add Experience, Add Education, profiles, resume, message, Next | Confirm email, postal autocomplete, phone country, repeatable experience/education, hiring-team message | Live inspected; nested open Shadow DOM traversal and repeated-record support fixed |
| Workable | Job page to application flow | Contact, resume, screening questions vary by employer | Automated browser verified; live indexed job was expired |
| Indeed | Native apply or external ATS redirect | Contact/resume, employer screening questions, assessment/redirect variance | Automated browser verified; live end-to-end pending |
| ZipRecruiter | Native one-click or external ATS redirect | Contact/resume, commute, schedule, employer questions | Automated browser verified; live end-to-end pending |
| Glassdoor | Often redirects to employer ATS | Job capture plus destination ATS questions | Automated browser verified; live end-to-end pending |
| Wellfound | Account-oriented startup application flow | Startup interest, links, authorization, profile/resume | Automated browser verified; live end-to-end pending |
| Dice | Native/external technology-job flow | Clearance, authorization, rate/pay, skills, resume | Automated browser verified; live end-to-end pending |

## Repeated Questions Added to the Profile

The reusable profile now covers:

- legal, first, last, and preferred name;
- email, phone, phone device type, address, city, county, state, country, postal
  code;
- LinkedIn, GitHub, portfolio, and relevant-project URL;
- multiple education records with institution, degree, major, GPA, dates, and
  currently-studying state;
- multiple work records with title, company, type, location, dates, current state,
  summary, and bullets;
- skill list;
- work authorization and sponsorship;
- earliest start date;
- salary/compensation expectations;
- relocation and reliable commute;
- previous employment by the company;
- security clearance;
- weekend and onsite/hybrid availability;
- application SMS consent;
- how the candidate heard about the role;
- why-this-role response;
- production-system/project summary;
- reusable short cover letter;
- additional application context;
- voluntary EEO fields behind explicit autofill consent.

Unknown required questions remain unresolved and visible for review. Skill-specific
years or narrative questions require an explicitly saved matching custom answer;
ApplyTeX does not infer experience duration from a keyword list.

## Implemented Reliability and Safety Changes

- Local API requests now go through the extension service worker.
- CORS accepts extension origins rather than arbitrary ATS web origins.
- Existing employer-form values are never overwritten by a fill plan.
- Stable provider/job keys reduce duplicate jobs and application records across
  SPA navigation.
- iCIMS same-origin iframes and SmartRecruiters open Shadow DOM are traversed.
- Third-party extension Shadow DOM, including Jobright and Grammarly UI, is
  excluded from scanning.
- Ashby custom select/Yes-No controls are recognized.
- Phone country/type/number controls are separated.
- Workday split month/year dates, current flags, skills, and multiple education
  and work records are supported.
- The panel explains current step, required readiness, already-filled fields,
  planned fills, unresolved answers, and the no-submit boundary.
- Voluntary EEO autofill is no longer silently enabled when the profile is saved.

## Verification

Commands:

```bash
uv run pytest -q
cd frontend && npm run typecheck && npm run lint
node --check extension/background.js
node --check extension/panel.js
node --check scripts/extension_platform_qa.mjs
node scripts/extension_platform_qa.mjs --jobs-per-provider 10
git diff --check
```

Latest verified results:

- 290 Python/static tests passed;
- the Next.js production build, TypeScript validation, and ESLint passed;
- JavaScript syntax and diff checks passed;
- the prior 130-application browser matrix passed across 13 providers;
- a current 13-provider rendered browser smoke matrix passed with zero failures
  and zero console/page errors;
- a current targeted Workday browser run passed exact school and field-of-study
  matching, idempotent reruns, and record-level failure isolation;
- every current smoke fixture uploaded a resume and stopped before final submit.

## Remaining Completion Gates

1. Reload the unpacked extension so Chrome runs the current worktree code.
2. Authorize the selected Jobright credit-backed Autofill action, then inspect the
   filled fields without submitting.
3. Complete a signed-in Workday My Experience smoke check (manual):
   - Sign in on a live `myworkdayjobs.com` application.
   - Open ApplyTeX panel on My Information and My Experience.
   - Confirm record matching, skills, and date fills.
   - Confirm the panel does **not** click Save and Continue.
   - Leave final Review/Submit to the candidate.
4. Spot-check one aggregator host (Indeed or Dice) and label failures as experimental.
5. Re-run `node scripts/extension_platform_qa.mjs --jobs-per-provider 10` before release.
