"""Optional local authentication for ApplyTeX ATS.

Disabled by default. Enable with ``APPLYTEX_REQUIRE_AUTH=1``.

When enabled:
- ``POST /auth/login`` exchanges a profile id + local password for a bearer token
- API requests must send ``Authorization: Bearer <token>``
- ``X-Profile-Id`` alone is no longer trusted for privileged reads
- tokens are stored hashed in SQLite, so they survive API restarts, expire
  after ``APPLYTEX_TOKEN_TTL_HOURS`` (default 14 days), and can be revoked
  with ``POST /auth/logout``

When disabled, the existing username-only local profile flow continues to work.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

TOKEN_TTL_ENV = "APPLYTEX_TOKEN_TTL_HOURS"
DEFAULT_TOKEN_TTL_HOURS = 24 * 14


def auth_required() -> bool:
    """Return True when the API should reject unauthenticated requests."""
    return os.environ.get("APPLYTEX_REQUIRE_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def token_ttl_hours() -> int:
    raw = os.environ.get(TOKEN_TTL_ENV, str(DEFAULT_TOKEN_TTL_HOURS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TOKEN_TTL_HOURS


def _hash_secret(value: str, *, salt: str) -> str:
    """Derive a hex digest using scrypt (memory-hard, GPU-resistant)."""
    dk = hashlib.scrypt(
        value.encode(),
        salt=salt.encode(),
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return dk.hex()


def _token_hash(token: str) -> str:
    """Tokens are stored only as SHA-256 digests; the raw value never touches disk."""
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AuthSession:
    token: str
    profile_id: str
    created_at: str
    expires_at: str


class LocalAuthStore:
    """Local password + bearer token store, persisted in the application SQLite DB."""

    def __init__(self, application_store: object) -> None:
        self._store = application_store

    # --- passwords --------------------------------------------------------

    def set_password(self, profile_id: str, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        salt = secrets.token_hex(16)
        digest = _hash_secret(password, salt=salt)
        # Format: "scrypt:{salt}:{digest}"
        self._store.set_setting(f"auth.password.{profile_id}", f"scrypt:{salt}:{digest}")

    def verify_password(self, profile_id: str, password: str) -> bool:
        raw = self._store.get_setting(f"auth.password.{profile_id}")
        if not raw:
            return False
        # Only accept scrypt-format hashes; old SHA-256 entries are rejected.
        if not raw.startswith("scrypt:"):
            return False
        parts = raw[len("scrypt:"):].split(":", 1)
        if len(parts) != 2:
            return False
        salt, expected = parts
        actual = _hash_secret(password, salt=salt)
        return hmac.compare_digest(actual, expected)

    def has_password(self, profile_id: str) -> bool:
        raw = self._store.get_setting(f"auth.password.{profile_id}")
        return bool(raw and ":" in raw)

    # --- tokens -----------------------------------------------------------

    def issue_token(self, profile_id: str) -> AuthSession:
        token = secrets.token_urlsafe(32)
        created = _now()
        expires = created + timedelta(hours=token_ttl_hours())
        session = AuthSession(
            token=token,
            profile_id=profile_id,
            created_at=created.isoformat(),
            expires_at=expires.isoformat(),
        )
        self._store.save_auth_session(
            token_hash=_token_hash(token),
            profile_id=profile_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
        )
        return session

    def resolve_token(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        row = self._store.get_auth_session(_token_hash(token))
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            self._store.revoke_auth_session(_token_hash(token))
            return None
        return AuthSession(
            token=token,
            profile_id=row["profile_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def revoke_token(self, token: str) -> bool:
        return bool(self._store.revoke_auth_session(_token_hash(token)))

    def revoke_profile_tokens(self, profile_id: str) -> int:
        return int(self._store.revoke_profile_auth_sessions(profile_id))


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/auth/login",
}


def install_auth_middleware(app: object, auth_store: LocalAuthStore) -> None:
    """Reject API calls without a bearer token when auth is required."""

    @app.middleware("http")
    async def require_bearer_when_enabled(request: Request, call_next):  # type: ignore[misc]
        path = request.url.path
        auth_header = request.headers.get("authorization") or ""
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        session = auth_store.resolve_token(token) if token else None
        if session is not None:
            request.state.auth_profile_id = session.profile_id
            request.state.auth_token = token

        if not auth_required():
            return await call_next(request)
        if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)
        if path == "/auth/status":
            return await call_next(request)
        if session is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required. POST /auth/login first."},
            )
        response: Response = await call_next(request)
        return response


def authenticated_profile_id(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> str | None:
    """Prefer the bearer-bound profile when auth is enabled."""
    bound = getattr(request.state, "auth_profile_id", None)
    if auth_required():
        return bound
    return x_profile_id


def rate_limit_key(request: Request) -> str:
    """Rate-limit per authenticated profile, else per declared profile, else per IP."""
    bound = getattr(request.state, "auth_profile_id", None)
    if bound:
        return f"profile:{bound}"
    declared = (request.headers.get("x-profile-id") or "").strip()
    if declared and not auth_required():
        return f"profile:{declared}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def require_profile_match(scoped_profile_id: str, requested: str | None) -> None:
    """Reject a body/query profile that differs from the request's resolved profile."""
    if requested and requested.strip() and requested.strip() != scoped_profile_id:
        raise HTTPException(403, "profile_id does not match the acting profile.")
