"""Shared FastAPI dependencies for the router sub-modules.

All helpers that previously lived as closures over ``app.state.*`` inside
``create_app()`` now accept ``request: Request`` and read
``request.app.state.*`` instead.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from latex_resume.local_auth import auth_required


def resolve_request_profile_id(
    *,
    request: Request | None = None,
    x_profile_id: str | None = None,
    profile_id: str | None = None,
) -> str:
    """Resolve the acting profile for this request.

    When ``APPLYTEX_REQUIRE_AUTH`` is on, the bearer-bound profile wins and
    conflicting ``X-Profile-Id`` / query / body profile ids are rejected.
    When auth is off, prefer explicit header/body/query, else the active profile.
    """
    requested = next(
        (
            candidate.strip()
            for candidate in (profile_id, x_profile_id)
            if candidate and candidate.strip()
        ),
        None,
    )
    if auth_required():
        if request is None:
            raise HTTPException(401, "Authentication required.")
        bound = getattr(request.state, "auth_profile_id", None)
        if not bound:
            raise HTTPException(401, "Authentication required. POST /auth/login first.")
        bound_id = str(bound).strip()
        if requested and requested != bound_id:
            raise HTTPException(
                403,
                "X-Profile-Id does not match the authenticated profile.",
            )
        return bound_id
    if requested:
        return requested
    if request is None:
        raise HTTPException(500, "Internal error: no request context available.")
    return request.app.state.application_store.get_active_profile_id()


def require_application_for_profile(
    request: Request,
    application_id: str,
    profile_id: str,
):
    """Return application owned by profile_id, or 404 (no cross-profile leak)."""
    application = request.app.state.application_store.get_application(application_id)
    if application is None or application.profile_id != profile_id:
        raise HTTPException(404, f"Application '{application_id}' not found.")
    return application


def require_application_detail_for_profile(
    request: Request,
    application_id: str,
    profile_id: str,
):
    require_application_for_profile(request, application_id, profile_id)
    detail = request.app.state.application_store.get_application_detail(application_id)
    if detail is None:
        raise HTTPException(404, f"Application '{application_id}' not found.")
    return detail


def require_form_scan_for_profile(
    request: Request,
    scan_id: str,
    profile_id: str,
):
    scan = request.app.state.application_store.get_form_scan(scan_id)
    if scan is None:
        raise HTTPException(404, f"Unknown scan_id: {scan_id}")
    if scan.application_id:
        require_application_for_profile(request, scan.application_id, profile_id)
    return scan


def require_artifact_for_profile(
    request: Request,
    artifact_id: str,
    profile_id: str,
):
    artifact = request.app.state.application_store.get_application_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
    require_application_for_profile(request, artifact.application_id, profile_id)
    return artifact
