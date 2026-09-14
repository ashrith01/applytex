"""Auth routes: /auth/status, /auth/login, /auth/logout."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latex_resume.api import AuthLoginRequest, AuthLoginResponse, AuthStatusResponse
from latex_resume.local_auth import LocalAuthStore, auth_required

router = APIRouter()


class AuthLogoutResponse(BaseModel):
    revoked: int


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    profile_id: str | None = None,
) -> AuthStatusResponse:
    """Report whether auth is required and whether this request is authenticated."""
    bound = getattr(request.state, "auth_profile_id", None)
    # With auth on, never disclose whether some *other* profile has a password.
    resolved = (
        bound
        if auth_required()
        else profile_id or bound or request.app.state.application_store.get_active_profile_id()
    )
    return AuthStatusResponse(
        auth_required=auth_required(),
        authenticated=bool(bound),
        profile_id=str(bound) if bound else None,
        has_password=bool(resolved) and request.app.state.auth_store.has_password(str(resolved)),
    )


@router.post("/auth/login", response_model=AuthLoginResponse)
async def auth_login(
    request: Request,
    body: AuthLoginRequest,
) -> AuthLoginResponse:
    """Create or verify a local password and issue a bearer token."""
    profile = request.app.state.application_store.get_candidate_profile(body.profile_id)
    auth_store: LocalAuthStore = request.app.state.auth_store
    if not auth_store.has_password(profile.profile_id):
        try:
            auth_store.set_password(profile.profile_id, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif body.set_password:
        # Changing an existing password needs the current session to prove ownership.
        bound = getattr(request.state, "auth_profile_id", None)
        if bound != profile.profile_id and not auth_store.verify_password(profile.profile_id, body.password):
            raise HTTPException(401, "Log in with the current password before setting a new one.")
        try:
            auth_store.set_password(profile.profile_id, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif not auth_store.verify_password(profile.profile_id, body.password):
        raise HTTPException(401, "Invalid profile password.")
    session = auth_store.issue_token(profile.profile_id)
    if not auth_required():
        request.app.state.application_store.set_active_profile_id(profile.profile_id)
    return AuthLoginResponse(
        access_token=session.token,
        profile_id=profile.profile_id,
        auth_required=auth_required(),
    )


@router.post("/auth/logout", response_model=AuthLogoutResponse)
async def auth_logout(request: Request, everywhere: bool = False) -> AuthLogoutResponse:
    """Revoke the current bearer token, or every token for this profile."""
    auth_store: LocalAuthStore = request.app.state.auth_store
    token = getattr(request.state, "auth_token", None)
    bound = getattr(request.state, "auth_profile_id", None)
    if not token or not bound:
        raise HTTPException(401, "No bearer session to log out.")
    if everywhere:
        return AuthLogoutResponse(revoked=auth_store.revoke_profile_tokens(str(bound)))
    return AuthLogoutResponse(revoked=1 if auth_store.revoke_token(token) else 0)
