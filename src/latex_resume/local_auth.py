"""Optional local authentication for ApplyTeX ATS.

Disabled by default. Enable with ``APPLYTEX_REQUIRE_AUTH=1``.

When enabled:
- ``POST /auth/login`` exchanges a profile id + local password for a bearer token
- API requests must send ``Authorization: Bearer <token>``
- ``X-Profile-Id`` alone is no longer trusted for privileged reads

When disabled, the existing username-only local profile flow continues to work.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response


def auth_required() -> bool:
    """Return True when the API should reject unauthenticated requests."""
    return os.environ.get("APPLYTEX_REQUIRE_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _hash_secret(value: str, *, salt: str = "") -> str:
    material = f"{salt}:{value}".encode()
    return hashlib.sha256(material).hexdigest()


@dataclass
class AuthSession:
    token: str
    profile_id: str
    created_at: float
    expires_at: float


class LocalAuthStore:
    """In-process local password + bearer token store (SQLite-backed secrets)."""

    def __init__(self, application_store: object) -> None:
        self._store = application_store
        self._sessions: dict[str, AuthSession] = {}
        self._ttl_seconds = 60 * 60 * 12

    def set_password(self, profile_id: str, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        salt = secrets.token_hex(8)
        digest = _hash_secret(password, salt=salt)
        self._store.set_setting(f"auth.password.{profile_id}", f"{salt}:{digest}")

    def verify_password(self, profile_id: str, password: str) -> bool:
        raw = self._store.get_setting(f"auth.password.{profile_id}")
        if not raw or ":" not in raw:
            return False
        salt, expected = raw.split(":", 1)
        actual = _hash_secret(password, salt=salt)
        return hmac.compare_digest(actual, expected)

    def has_password(self, profile_id: str) -> bool:
        raw = self._store.get_setting(f"auth.password.{profile_id}")
        return bool(raw and ":" in raw)

    def issue_token(self, profile_id: str) -> AuthSession:
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = AuthSession(
            token=token,
            profile_id=profile_id,
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        self._sessions[token] = session
        return session

    def resolve_token(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at < time.time():
            self._sessions.pop(token, None)
            return None
        return session

    def revoke_token(self, token: str) -> None:
        self._sessions.pop(token, None)


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
