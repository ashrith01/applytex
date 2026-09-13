"""Auth routes: /auth/status and /auth/login."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from latex_resume.api import AuthLoginRequest, AuthLoginResponse, AuthStatusResponse
from latex_resume.local_auth import LocalAuthStore, auth_required
from latex_resume.routers._deps import resolve_request_profile_id

router = APIRouter()


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    profile_id: str | None = None,
) -> AuthStatusResponse:
    """Report whether auth is required and whether this request is authenticated."""
    bound = getattr(request.state, "auth_profile_id", None)
    resolved = (
        profile_id
        or bound
        or request.app.state.application_store.get_active_profile_id()
    )
    return AuthStatusResponse(
        auth_required=auth_required(),
        authenticated=bool(bound),
        profile_id=str(bound) if bound else None,
        has_password=request.app.state.auth_store.has_password(resolved),
    )


@router.post("/auth/login", response_model=AuthLoginResponse)
async def auth_login(
    request: Request,
    body: AuthLoginRequest,
) -> AuthLoginResponse:
    """Create or verify a local password and issue a bearer token."""
    profile = request.app.state.application_store.get_candidate_profile(body.profile_id)
    auth_store: LocalAuthStore = request.app.state.auth_store
    if body.set_password or not auth_store.has_password(profile.profile_id):
        try:
            auth_store.set_password(profile.profile_id, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif not auth_store.verify_password(profile.profile_id, body.password):
        raise HTTPException(401, "Invalid profile password.")
    session = auth_store.issue_token(profile.profile_id)
    request.app.state.application_store.set_active_profile_id(profile.profile_id)
    return AuthLoginResponse(
        access_token=session.token,
        profile_id=profile.profile_id,
        auth_required=auth_required(),
    )
