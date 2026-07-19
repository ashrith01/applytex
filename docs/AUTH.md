# Optional local authentication

ApplyTeX auth is **off by default**. The username-only web login continues to work
for local single-user use.

## Enable auth

```bash
export APPLYTEX_REQUIRE_AUTH=1
uv run applytex-api
```

When enabled:

1. `GET /auth/status` reports `auth_required: true`.
2. `POST /auth/login` with `{ "profile_id": "you", "password": "at-least-8-chars" }`
   - First call (or `set_password: true`) stores a salted password hash in SQLite settings.
   - Returns `{ "access_token": "...", "profile_id": "you" }`.
3. Send `Authorization: Bearer <access_token>` on subsequent API requests.
4. Also send matching `X-Profile-Id` (web UI and extension do this automatically).
5. A conflicting `X-Profile-Id` / body / query profile id returns **403**.
6. Application, artifact, and form-scan routes are profile-scoped: another profile's
   ids return **404** (no cross-profile leak).

## Clients

### Web (Next.js)

- On load, calls `/auth/status`.
- When auth is required, the login page shows a password field and stores the bearer
  token in `localStorage` (`applytex_access_token`).
- All `apiFetch` / `apiUpload` calls attach `Authorization` when a token is present
  and send `X-Profile-Id` whenever a `profileId` is passed.

### Extension

- On sign-in, calls `/auth/status`; when required, prompts for password and
  `POST /auth/login` before `PUT /profile/active`.
- Token is stored in `chrome.storage.local` under `applytexExtensionAccessToken`.
- The service worker forwards `Authorization` alongside `X-Profile-Id`.

When auth is **off**, both clients keep the username-only / usable-profile picker flow.

## Migration from username-only login

1. Keep using the same username (profile id).
2. Set `APPLYTEX_REQUIRE_AUTH=1` and restart the API.
3. Sign in once with a password (first login stores the hash).
4. Re-login after API restarts (tokens are in-memory for the process lifetime).

## Security notes

- Passwords never leave the local machine; hashes live in `.applytex/applytex.db`.
- Tokens are in-memory for the API process lifetime (re-login after restart).
- This is still a local-first control, not multi-tenant cloud identity.
- Ownership checks use the resolved profile id even when auth is off
  (`X-Profile-Id` scopes application reads).
