"""Application tracking routes: /applications/*."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from latex_resume.api import (
    ApplicationScoreResponse,
    ApplicationsHealthResponse,
    CreateApplicationEventRequest,
    CreateApplicationRequest,
    CreateApplicationTaskRequest,
    PatchApplicationRequest,
    ScoreApplicationRequest,
    TransitionApplicationRequest,
    UpdateArtifactStatusRequest,
    _score_application_for_profile,
)
from latex_resume.application_store import InvalidApplicationTransition
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationDetail,
    ApplicationEvent,
    ApplicationRecord,
    ApplicationStatus,
    ApplicationTask,
)
from latex_resume.routers._deps import (
    require_application_for_profile,
    resolve_request_profile_id,
)
from latex_resume.session import store

router = APIRouter()


@router.post("/applications", response_model=ApplicationRecord)
async def create_application(
    request: Request,
    body: CreateApplicationRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplicationRecord:
    """Create a controlled application record for a saved job."""
    if body.resume_session_id and await store.get(body.resume_session_id) is None:
        raise HTTPException(404, "resume_session_id not found or expired.")
    profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id,
    )
    try:
        record = request.app.state.application_store.get_or_create_application(
            job_id=body.job_id,
            profile_id=profile_id,
            resume_session_id=body.resume_session_id,
            notes=body.notes,
            force_new=body.force_new,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        scored = await _score_application_for_profile(
            request.app, record.application_id, profile_id
        )
        return scored.application
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return record


@router.get("/applications", response_model=list[ApplicationRecord])
async def list_applications(
    request: Request,
    limit: int = 100,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> list[ApplicationRecord]:
    """List locally persisted application records."""
    bounded_limit = min(max(limit, 1), 200)
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    return request.app.state.application_store.list_applications(
        bounded_limit,
        profile_id=scoped_profile_id,
    )


@router.get("/applications/health", response_model=ApplicationsHealthResponse)
async def applications_health(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationsHealthResponse:
    """Return tracker health metrics for the local dashboard."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    merged_now = request.app.state.application_store.dedupe_applications()
    applications = request.app.state.application_store.list_applications(
        1000,
        profile_id=scoped_profile_id,
    )
    scores = [
        application.current_resume_score
        for application in applications
        if application.current_resume_score is not None
    ]
    inactive_statuses = {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.FAILED,
        ApplicationStatus.SKIPPED,
    }
    average_score = round(sum(scores) / len(scores), 1) if scores else None
    return ApplicationsHealthResponse(
        total=len(applications),
        active=sum(
            1 for application in applications if application.status not in inactive_statuses
        ),
        duplicates_merged=(
            merged_now or request.app.state.application_store.get_last_dedupe_count()
        ),
        average_current_resume_score=average_score,
        missing_answers=sum(
            application.missing_answers_count for application in applications
        ),
        captured_jobs=request.app.state.application_store.count_jobs(
            profile_id=scoped_profile_id
        ),
        profile_id=scoped_profile_id,
    )


@router.get("/applications/{application_id}", response_model=ApplicationDetail)
async def get_application(
    request: Request,
    application_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationDetail:
    """Return one application with job, artifacts, tasks, events, and scan."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    detail = request.app.state.application_store.get_application_detail(application_id)
    if detail is None:
        raise HTTPException(404, f"Application '{application_id}' not found.")
    return detail


@router.patch("/applications/{application_id}", response_model=ApplicationRecord)
async def patch_application(
    request: Request,
    application_id: str,
    body: PatchApplicationRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationRecord:
    """Update tracker metadata such as stage, priority, deadlines, and notes."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    updates = body.model_dump(exclude_unset=True)
    try:
        record = request.app.state.application_store.update_application(
            application_id, updates
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return record


@router.post("/applications/{application_id}/score", response_model=ApplicationScoreResponse)
async def score_application(
    request: Request,
    application_id: str,
    body: ScoreApplicationRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplicationScoreResponse:
    """Persist a fast deterministic resume/JD score snapshot for one application."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id if body else None,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    return await _score_application_for_profile(
        request.app,
        application_id,
        scoped_profile_id,
    )


@router.post("/applications/{application_id}/events", response_model=ApplicationEvent)
async def create_application_event(
    request: Request,
    application_id: str,
    body: CreateApplicationEventRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationEvent:
    """Append a tracker timeline entry."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    try:
        return request.app.state.application_store.create_application_event(
            application_id=application_id,
            kind=body.kind,
            label=body.label,
            detail=body.detail,
            payload=body.payload,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/applications/{application_id}/tasks", response_model=ApplicationTask)
async def create_application_task(
    request: Request,
    application_id: str,
    body: CreateApplicationTaskRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationTask:
    """Create a manual follow-up, missing-answer, or interview task."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    try:
        return request.app.state.application_store.create_application_task(
            application_id=application_id,
            title=body.title,
            category=body.category,
            due_at=body.due_at,
            notes=body.notes,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/applications/{application_id}/artifacts/latest",
    response_model=ApplicationArtifact,
)
async def get_latest_application_artifact(
    request: Request,
    application_id: str,
    type: ApplicationArtifactType = ApplicationArtifactType.TAILORED_RESUME,
    status: ApplicationArtifactStatus = ApplicationArtifactStatus.APPROVED,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationArtifact:
    """Return the latest matching artifact for extension upload handoff."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    artifact = request.app.state.application_store.get_latest_application_artifact(
        application_id,
        artifact_type=type,
        status=status,
    )
    if artifact is None:
        raise HTTPException(404, "No matching application artifact found.")
    return artifact


@router.post(
    "/applications/{application_id}/artifacts/{artifact_id}/status",
    response_model=ApplicationArtifact,
)
async def update_application_artifact_status(
    request: Request,
    application_id: str,
    artifact_id: str,
    body: UpdateArtifactStatusRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationArtifact:
    """Approve or mark an application artifact as uploaded."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    artifact = request.app.state.application_store.get_application_artifact(artifact_id)
    if artifact is None or artifact.application_id != application_id:
        raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
    try:
        return request.app.state.application_store.update_application_artifact_status(
            artifact_id,
            body.status,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(
    "/applications/{application_id}/transition",
    response_model=ApplicationRecord,
)
async def transition_application(
    request: Request,
    application_id: str,
    body: TransitionApplicationRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationRecord:
    """Advance an application through the human-approved workflow."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_application_for_profile(request, application_id, scoped_profile_id)
    try:
        return request.app.state.application_store.transition_application(
            application_id,
            body.status,
            body.notes,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvalidApplicationTransition as exc:
        raise HTTPException(409, str(exc)) from exc
