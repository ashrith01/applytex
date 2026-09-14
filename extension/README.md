# ApplyTeX ATS Chrome Extension

This Manifest V3 extension is the user-visible browser bridge for ApplyTeX ATS.
The current version supports reviewed filling:

- capture the current job from LinkedIn, Greenhouse, Lever, Ashby, Workday,
  iCIMS, SmartRecruiters, Workable, Indeed, ZipRecruiter, Glassdoor,
  Wellfound, or Dice;
- scan visible application fields and their labels;
- send the result to the local FastAPI service;
- preview whether required answers remain unresolved;
- fill only the reviewed actions after a separate user click.

The scanner traverses same-origin application iframes and open application
Shadow DOM while excluding other extensions' UI. On repeatable education and
work-experience editors, the panel can select the corresponding saved profile
record. On Workday's My Experience page, the panel previews the complete page
plan, matches existing records by identity, creates missing records after one
approval, fills approved values and skills, and stops before Save and Continue.
The panel also offers a **Continue to the next page** button that clicks the
employer’s step-advance control (for example Workday “Save and Continue”) after
you review the step. It still refuses the final Submit/Apply button.

When a supported application navigates through an Apply or sign-in flow, the
extension keeps the captured job and application record under a stable job key.
An open panel is restored after the navigation and scans the application form
without trying to recapture the job description from the login page.

It cannot submit applications. The user reviews the completed page and clicks
the employer's Submit button manually.

## Receipts, status, and cover letters

- After a fill, the panel reports the outcome and the tracker advances to
  **ready for review** (no unresolved required fields) or **needs input** —
  never backwards.
- When a page looks like an employer confirmation ("Thank you for applying",
  "Application submitted", a `/confirmation` URL) *and* the form is gone, the
  panel shows **Looks like this application was submitted — Confirm / Not yet**.
  Nothing is recorded until you confirm. **Mark as submitted** in the job
  header does the same for pages the detector misses.
- Confirming writes an immutable **receipt** (`GET /applications/{id}/submission`):
  every scanned field's final value per step, which resume and cover letter
  were attached (by artifact id and SHA-256), the job snapshot hash, and how it
  was confirmed. Later profile or answer edits never change it. A follow-up
  task is scheduled 7 days out; `GET /applications/tasks/due` lists what is due.
- The Tailor tab gains a **Cover letter** section: draft (230–320 words,
  grounded only in your resume and the JD — any number not already in your
  resume is rejected), edit, approve (renders a one-page PDF when `pdflatex`
  is available, otherwise a text file). Cover-letter upload fields then offer
  **Attach ApplyTeX cover letter**, which injects the file exactly like the
  resume path does.

The **Autofill information** workspace edits the active local profile without
leaving the application page. Each category is saved explicitly through a
partial profile patch; unsaved edits never change reusable answers. Voluntary
EEO answers retain their separate autofill opt-in. Resume fields open a chooser
that can upload the saved profile PDF or launch the full Tailor Studio for an
approved job-specific PDF. Replacing the profile resume remains in the web UI.

Required cover letters and supporting file fields have an **Attach document**
action in the review checklist. It opens that employer field's file chooser;
the employer handles file types and upload validation. Selecting a file triggers
a rescan. Text answers cannot satisfy attachment fields or replace file uploads.
Full cover-letter generation, export, and durable attachment snapshots remain
planned in [`docs/JOBRIGHT_PARITY_PLAN.md`](../docs/JOBRIGHT_PARITY_PLAN.md).

## Answers that learn

Unresolved required questions no longer stay unresolved forever:

- **Answer once for this form** now asks whether to remember the answer. A
  remembered yes/no for age, relocation, travel, non-compete, authorization,
  or sponsorship becomes the matching profile fact; anything else is stored in
  the answers bank (`GET/POST/DELETE /profile/answers`) with its intent and the
  exact prompt, and resolves the same question on the next form (exact prompt,
  learned alias, intent, then token match). Profile facts always win over a
  remembered answer, so editing the profile takes effect everywhere.
- **Suggest answers from saved facts** asks the configured LLM to map the
  remaining short required questions onto facts you already saved — nothing
  else. Each suggestion shows the fact it cites; **Use and remember** or
  **Use once** fills it through the same reviewed plan override, and a
  suggestion that does not match an offered option or cite a real fact is
  discarded server-side. Authorization, sponsorship, compensation, demographic,
  narrative, file, and per-record questions are never sent to the model.
- Usage counts in the bank reflect fields that were actually filled.

Existing page answers are preserved by default. Select **Replace existing answers
with profile values on this fill** to review replacements; this choice resets
after filling or when the form structure changes. Empty dropdown placeholders
are counted as unanswered. Selections must match a unique available choice;
ambiguous and unavailable answers stay in the review checklist.

## Local executor (apply without opening the ATS yourself)

`scripts/executor.mjs` is a Playwright worker that runs **on your machine** in
a dedicated Chrome profile (`.applytex/browser-profile`, separate from your
daily browser). It injects these same panel scripts into the employer page with
a `chrome.*` shim, drives the panel's reviewed fill, and pauses with a
screenshot. It clicks the employer's Submit button **only** after you approve
the paused run in the API, which mints a one-time approval token the executor
must present to record the receipt.

```bash
node scripts/executor.mjs --profile <your_profile_id>          # keep running while you apply
curl -X POST localhost:8000/applications/<id>/apply-runs        # or from the feed / tracker
curl localhost:8000/apply-runs                                  # queued / paused_for_review / awaiting_input
curl -X POST localhost:8000/apply-runs/<run>/approve            # after reviewing the screenshot
```

Sign-in walls, CAPTCHAs and MFA are never bypassed: the run pauses as
`awaiting_input`, you finish the step in the executor's browser window, then
`POST /apply-runs/<run>/resume`. Unresolved required questions pause the same
way; answer them (answers bank or extension) and resume. Only Greenhouse, Lever
and Ashby run without `--allow-provider`. `APPLYTEX_EXECUTOR_DAILY_CAP`
(default 20) bounds runs per day. Sending this to a hosted service is out of
scope by design.

`node scripts/executor_lab_qa.mjs` proves the loop against the synthetic lab:
enqueue → fill → pause → approve → submit → receipt.

## Synthetic application lab

Run `uv run python scripts/autofill_lab.py` from the repository root and open
`http://127.0.0.1:8765/lab`. The lab loads the actual panel scripts and real API
with a fictional profile and a local Chrome-message bridge. It includes 39 cases
across 13 provider identities; it does not exercise manifest installation or
employer servers. Run `node scripts/autofill_lab_qa.mjs` in another terminal.
See the [autofill strategy and evaluation plan](../docs/AUTOFILL_STRATEGY_AND_EVALUATION.md)
for setup, data design, coverage limits and additional tests.

## Local Installation

1. Start the API with `uv run applytex-api` and the web UI with `cd frontend && npm run dev`.
2. Open `chrome://extensions`.
3. Enable Developer mode.
4. Choose **Load unpacked** and select this `extension` directory.
5. Open a supported job page and click the ApplyTeX ATS extension icon to open the in-page panel.
6. Sign in with the same username as the web app (no password when auth is off). When the API is started with `APPLYTEX_REQUIRE_AUTH=1`, the panel asks for a password and stores a bearer token. Use **Switch** / **Log out** in the panel header to change accounts.

The live product surface is the in-page panel (`panel.js` plus `panel-*.js` modules). The older popup UI lives under `legacy/` and is not wired in `manifest.json`.

By default the extension communicates with `http://127.0.0.1:8000` and opens the web UI at `http://localhost:3000` for guided resume tailoring. Both origins are configurable on the extension's **Options** page (right-click the toolbar icon → Options): a non-local API must use `https://`, and Chrome asks you to grant the extension access to that origin when you save. **Test connection** calls `/auth/status` through the service worker. `node scripts/package_extension.mjs` builds `dist/applytex-extension-<version>.zip` for a Web Store upload (it refuses to package debug hooks). The signed-in username is stored in `chrome.storage.local` under `applytexExtensionProfileId` and sent as `X-Profile-Id` on API calls. When auth is required, the bearer token is stored under `applytexExtensionAccessToken` and forwarded as `Authorization` by the service worker. See [`docs/AUTH.md`](../docs/AUTH.md).

## Provider depth

Not every listed host has equal autofill depth:

- **Deep:** Workday, Ashby, Greenhouse, Lever
- **Capture + generic fill:** LinkedIn, SmartRecruiters, iCIMS, Workable
- **Experimental / fixture-backed:** Indeed, ZipRecruiter, Glassdoor, Wellfound, Dice
