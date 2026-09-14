# Authentication, ownership, and data lifecycle

ApplyTeX auth is **off by default**. The username-only web login continues to
work for local single-user use. Every route is profile-scoped in both modes;
auth changes *how* the acting profile is established, not *whether* ownership
is enforced.

## How the acting profile is resolved

`routers/_deps.resolve_request_profile_id()` decides who a request acts as:

| Mode | Acting profile | Conflicting `X-Profile-Id` / body / query |
|---|---|---|
| auth off (default) | `X-Profile-Id` header, else `profile_id` body/query, else the active profile | honored (local multi-profile convenience) |
| `APPLYTEX_REQUIRE_AUTH=1` | the profile bound to the bearer token | **403** |

Ownership rules that hold in both modes:

- applications, artifacts, form scans, receipts, executor runs, watchlist
  entries, answers, and tailor sessions belonging to another profile return
  **404** (no cross-profile leak, no existence oracle);
- `GET /jobs/{id}` hides jobs captured for another profile (public board jobs
  are visible to everyone);
- classic `/latex/*` sessions belong to the profile that uploaded them;
- `PUT /profile` may only write the acting profile (`profile_id` in the body
  must match or is rejected with 403);
- with auth on, `GET /profiles` lists only the caller and `GET /auth/status`
  never reveals whether some other profile has a password.

Rate limits are keyed per authenticated profile (else per declared profile,
else per IP), so one user cannot exhaust the LLM budget for everyone behind a
shared address.

## Enable auth

```bash
export APPLYTEX_REQUIRE_AUTH=1
uv run applytex-api
```

1. `GET /auth/status` reports `auth_required: true`.
2. `POST /auth/login` with `{ "profile_id": "you", "password": "at-least-8-chars" }`
   - the first login for a profile stores a salted scrypt hash in SQLite;
   - later logins verify it; `set_password: true` rotates it but only when the
     current password (or a current bearer session) proves ownership;
   - returns `{ "access_token": "...", "profile_id": "you" }`.
3. Send `Authorization: Bearer <access_token>` on subsequent requests. Clients
   also send `X-Profile-Id`; it must match the token's profile.
4. `POST /auth/logout` revokes the current token; `?everywhere=true` revokes
   every token for the profile.

Tokens are stored **hashed** (SHA-256) in the `auth_sessions` table, so they
survive API restarts, expire after `APPLYTEX_TOKEN_TTL_HOURS` (default 336 =
14 days), and are revocable. Expired and revoked rows are purged lazily.

## Export and delete

- `GET /profile/export` — portable JSON of everything the profile owns:
  profile facts (without PDF bytes), answers bank, projects, watchlist,
  ingestion runs, captured jobs, applications with artifacts metadata, events,
  tasks, form scans and receipts, executor runs.
- `DELETE /profile?confirm=<profile_id>` — removes every
  row and on-disk file (tailored PDFs, cover letters, run screenshots) for the
  profile, revokes its tokens and password, and clears it as the active
  profile. The typed confirmation is the only safeguard; there is no undo.

## Encryption at rest

Set `APPLYTEX_DATA_KEY` (generate one with
`uv run python -m latex_resume.data_protection`) and the store seals the
sensitive sub-documents before they reach SQLite: voluntary EEO answers,
compensation expectations, per-profile LLM API keys, and EEO values inside
submission receipts. Everything else stays plain JSON so the database remains
inspectable. Rows written before the key was set still read; rows written
with a key cannot be read without it (a clear error, never silent garbage).
The API logs a warning when auth is required but no key is set.

## Per-profile LLM keys and budgets

`PUT /profile/llm` stores a backend, API key, model, and daily call/token
budgets on the acting profile. For that profile's requests the key and
backend override the server-wide `.env` values (an explicit per-call
override, such as the application-answer fallback chain, still wins). The key
is never returned — `GET /profile/llm` shows a masked tail — and is excluded
from exports. `GET /profile/llm/usage` reports today's calls and tokens; once
a budget is reached, model-backed routes answer **429** and the model is not
called. Usage is per profile per UTC day in `llm_usage`.

## Schema changes

`ApplicationStore` creates tables idempotently and then applies numbered
entries from `_MIGRATIONS` once per database, recording them in
`schema_migrations`. Add `ALTER TABLE` / backfill steps there rather than
editing the baseline DDL, so existing local databases upgrade in place.

## Clients

### Web (Next.js)

- On load, calls `/auth/status`.
- When auth is required, the login page shows a password field and stores the
  bearer token in `localStorage` (`applytex_access_token`).
- All `apiFetch` / `apiUpload` calls attach `Authorization` when a token is
  present and send `X-Profile-Id` whenever a `profileId` is passed.

### Extension

- On sign-in, calls `/auth/status`; when required, prompts for password and
  `POST /auth/login` before `PUT /profile/active`.
- Token is stored in `chrome.storage.local` under `applytexExtensionAccessToken`.
- The service worker forwards `Authorization` alongside `X-Profile-Id`.

### Executor

- `node scripts/executor.mjs --profile <id> --token <bearer>` when auth is on.

## Still local-first

- Passwords and token hashes live in `.applytex/applytex.db`; there is no
  hosted identity provider, no MFA, and no per-user encryption of EEO or
  compensation fields yet (tracked in `docs/TSENTA_PARITY_PLAN.md`, Phase 5b).
- The executor is a local companion by design; do not expose the API to an
  untrusted network without a reverse proxy that terminates TLS.
