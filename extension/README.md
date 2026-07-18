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

## Local Installation

1. Start the API with `uv run applytex-api` and the web UI with `cd frontend && npm run dev`.
2. Open `chrome://extensions`.
3. Enable Developer mode.
4. Choose **Load unpacked** and select this `extension` directory.
5. Open a supported job page and click the ApplyTeX ATS extension icon to open the in-page panel.
6. Sign in with the same username as the web app (no password when auth is off). When the API is started with `APPLYTEX_REQUIRE_AUTH=1`, the panel asks for a password and stores a bearer token. Use **Switch** / **Log out** in the panel header to change accounts.

The live product surface is the in-page panel (`panel.js` plus `panel-*.js` modules). The older popup UI lives under `legacy/` and is not wired in `manifest.json`.

The extension communicates with `http://127.0.0.1:8000` and opens the web UI at `http://localhost:3000` for guided resume tailoring. The signed-in username is stored in `chrome.storage.local` under `applytexExtensionProfileId` and sent as `X-Profile-Id` on API calls. When auth is required, the bearer token is stored under `applytexExtensionAccessToken` and forwarded as `Authorization` by the service worker. See [`docs/AUTH.md`](../docs/AUTH.md).

## Provider depth

Not every listed host has equal autofill depth:

- **Deep:** Workday, Ashby, Greenhouse, Lever
- **Capture + generic fill:** LinkedIn, SmartRecruiters, iCIMS, Workable
- **Experimental / fixture-backed:** Indeed, ZipRecruiter, Glassdoor, Wellfound, Dice
