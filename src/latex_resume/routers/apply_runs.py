"""Executor routes: /applications/{id}/apply-runs and /apply-runs/*.

Two callers share these routes. The *user* enqueues, approves, resumes and
cancels. The *executor* (``scripts/executor.mjs``, a local browser worker)
claims runs, reports progress, and records the submission — and it can only do
the last while presenting the approval token minted when the user approved.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from latex_resume.application_store import InvalidApplicationTransition, InvalidApplyRunTransition
from latex_resume.apply_runs import (
    ApplyRunPolicyError,
    enqueue_apply_run,
    mint_approval_token,
    resolve_screenshot,
    save_screenshot,
)
from latex_resume.job_models import ApplicationRecord, ApplicationStatus, ApplyRun, JobPosting
from latex_resume.routers._deps import require_application_for_profile, resolve_request_profile_id
from latex_resume.submission import confirm_submission

logger = logging.getLogger(__name__)

router = APIRouter()


class EnqueueApplyRunRequest(BaseModel):
    allow_experimental_provider: bool = False
    profile_id: str | None = None


class ClaimApplyRunRequest(BaseModel):
    executor_id: str = Field(default="", max_length=120)
    profile_id: str | None = None


class ClaimApplyRunResponse(BaseModel):
    run: ApplyRun
    application: ApplicationRecord
    job: JobPosting
    # Present only when the run moved to submitting; the executor echoes it back.
    approval_token: str | None = None


class ApplyRunProgressRequest(BaseModel):
    status: Literal["running", "awaiting_input", "paused_for_review", "submitting", "needs_verification", "failed"] | None = None
    step: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=1000)
    level: Literal["info", "warn", "error"] = "info"
    unresolved_required: list[str] | None = Field(default=None, max_length=200)
    review_summary: dict[str, Any] | None = None
    screenshot_b64: str | None = None
    error: str | None = Field(default=None, max_length=2000)
    profile_id: str | None = None


class ApplyRunSubmittedRequest(BaseModel):
    approval_token: str | None = None
    detection_evidence: str = Field(default="", max_length=500)
    screenshot_b64: str | None = None
    profile_id: str | None = None


class ApplyRunSubmittedResponse(BaseModel):
    run: ApplyRun
    application: ApplicationRecord
    bundle_id: str


class UserActionRequest(BaseModel):
    notes: str = Field(default="", max_length=1000)
    profile_id: str | None = None


def _scoped(request: Request, x_profile_id: str | None, profile_id: str | None) -> str:
    return resolve_request_profile_id(request=request, x_profile_id=x_profile_id, profile_id=profile_id)


def _require_run(request: Request, run_id: str, profile_id: str) -> ApplyRun:
    run = request.app.state.application_store.get_apply_run(run_id)
    if run is None or run.profile_id != profile_id:
        raise HTTPException(404, f"Run '{run_id}' not found.")
    return run


def _transition(request: Request, run_id: str, target: str, *, actor: str, **kwargs: Any) -> ApplyRun:
    try:
        return request.app.state.application_store.transition_apply_run(run_id, target, actor=actor, **kwargs)
    except InvalidApplyRunTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/applications/{application_id}/apply-runs", response_model=ApplyRun)
async def enqueue_run(
    request: Request,
    application_id: str,
    body: EnqueueApplyRunRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRun:
    """Queue a local executor run: fill, pause for review, submit only after approval."""
    body = body or EnqueueApplyRunRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    application = require_application_for_profile(request, application_id, scoped)
    store = request.app.state.application_store
    job = store.get_job(application.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{application.job_id}' not found.")
    try:
        return enqueue_apply_run(
            store,
            application=application,
            job=job,
            profile_id=scoped,
            allow_experimental_provider=body.allow_experimental_provider,
        )
    except ApplyRunPolicyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/apply-runs", response_model=list[ApplyRun])
async def list_runs(
    request: Request,
    status: Annotated[list[str] | None, Query()] = None,
    application_id: str | None = None,
    limit: int = 100,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> list[ApplyRun]:
    scoped = _scoped(request, x_profile_id, profile_id)
    return request.app.state.application_store.list_apply_runs(
        scoped, statuses=status, application_id=application_id, limit=min(max(limit, 1), 500)
    )


@router.get("/apply-runs/{run_id}", response_model=ApplyRun)
async def get_run(
    request: Request,
    run_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplyRun:
    return _require_run(request, run_id, _scoped(request, x_profile_id, profile_id))


@router.get("/apply-runs/{run_id}/screenshot")
async def get_run_screenshot(
    request: Request,
    run_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> FileResponse:
    run = _require_run(request, run_id, _scoped(request, x_profile_id, profile_id))
    path = resolve_screenshot(request.app.state.application_store.path, run.screenshot_path)
    if path is None:
        raise HTTPException(404, "This run has no screenshot yet.")
    return FileResponse(path, media_type="image/png")


# --- user actions -----------------------------------------------------------


@router.post("/apply-runs/{run_id}/approve", response_model=ApplyRun)
async def approve_run(
    request: Request,
    run_id: str,
    body: UserActionRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRun:
    """Approve the paused review. Mints the token the executor needs to click Submit."""
    body = body or UserActionRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    run = _require_run(request, run_id, scoped)
    updated = _transition(
        request,
        run_id,
        "approved",
        actor="user",
        expected_status=run.status,
        updates={"approval_token": mint_approval_token(), "approved_at": _now(), "step": "approved", "error": ""},
        log_message="User approved the reviewed form for submission." + (f" Notes: {body.notes}" if body.notes else ""),
    )
    try:
        request.app.state.application_store.create_application_event(
            application_id=run.application_id,
            kind="apply_run_approved",
            label="Executor run approved for submission",
            payload={"run_id": run_id},
        )
    except KeyError:
        pass
    return updated


@router.post("/apply-runs/{run_id}/resume", response_model=ApplyRun)
async def resume_run(
    request: Request,
    run_id: str,
    body: UserActionRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRun:
    """After answering what the executor asked for, put the run back in the queue."""
    body = body or UserActionRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    _require_run(request, run_id, scoped)
    return _transition(
        request,
        run_id,
        "queued",
        actor="user",
        updates={"step": "resumed", "error": ""},
        log_message="User resumed the run." + (f" Notes: {body.notes}" if body.notes else ""),
    )


@router.post("/apply-runs/{run_id}/cancel", response_model=ApplyRun)
async def cancel_run(
    request: Request,
    run_id: str,
    body: UserActionRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRun:
    body = body or UserActionRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    _require_run(request, run_id, scoped)
    return _transition(
        request,
        run_id,
        "cancelled",
        actor="user",
        updates={"step": "cancelled", "approval_token": None},
        log_message="User cancelled the run." + (f" Notes: {body.notes}" if body.notes else ""),
    )


# --- executor protocol ------------------------------------------------------


@router.post("/apply-runs/{run_id}/claim", response_model=ClaimApplyRunResponse)
async def claim_run(
    request: Request,
    run_id: str,
    body: ClaimApplyRunRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ClaimApplyRunResponse:
    """Executor takes a queued run (-> running) or an approved run (-> submitting)."""
    body = body or ClaimApplyRunRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    run = _require_run(request, run_id, scoped)
    store = request.app.state.application_store
    if run.status == "queued":
        run = _transition(
            request, run_id, "running", actor="executor", expected_status="queued",
            updates={"executor_id": body.executor_id, "step": "claimed", "error": ""},
            log_message=f"Executor {body.executor_id or 'local'} claimed the run.",
        )
        token = None
    elif run.status == "approved":
        run = _transition(
            request, run_id, "submitting", actor="executor", expected_status="approved",
            updates={"executor_id": body.executor_id, "step": "submitting"},
            log_message="Executor received the approval and will click the employer's Submit button.",
        )
        token = run.approval_token
    else:
        raise HTTPException(409, f"Run is {run.status}; only queued or approved runs can be claimed.")
    application = store.get_application(run.application_id)
    job = store.get_job(run.job_id)
    if application is None or job is None:
        raise HTTPException(404, "The run's application or job no longer exists.")
    return ClaimApplyRunResponse(run=run, application=application, job=job, approval_token=token)


@router.patch("/apply-runs/{run_id}", response_model=ApplyRun)
async def report_progress(
    request: Request,
    run_id: str,
    body: ApplyRunProgressRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRun:
    """Executor progress: log lines, pause for review, ask for input, or fail."""
    scoped = _scoped(request, x_profile_id, body.profile_id)
    run = _require_run(request, run_id, scoped)
    store = request.app.state.application_store
    updates: dict[str, Any] = {}
    if body.step is not None:
        updates["step"] = body.step
    if body.unresolved_required is not None:
        updates["unresolved_required"] = body.unresolved_required
    if body.review_summary is not None:
        updates["review_summary"] = body.review_summary
    if body.error is not None:
        updates["error"] = body.error
    if body.screenshot_b64:
        try:
            updates["screenshot_path"] = save_screenshot(store.path, run_id, body.step or body.status or "step", body.screenshot_b64)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    # "submitted" is deliberately absent from the status Literal: recording a
    # submission goes through POST /apply-runs/{id}/submitted with the token.
    target = body.status or run.status
    updated = _transition(
        request,
        run_id,
        target,
        actor="executor",
        updates=updates,
        log_message=body.message or "",
        log_level=body.level,
    )
    if body.status == "paused_for_review":
        try:
            store.create_application_event(
                application_id=run.application_id,
                kind="apply_run_paused",
                label="Executor filled the form and is waiting for your review",
                detail=f"{len(updated.unresolved_required)} required field(s) unresolved." if updated.unresolved_required else "All required fields resolved.",
                payload={"run_id": run_id},
            )
        except KeyError:
            pass
    return updated


@router.post("/apply-runs/{run_id}/submitted", response_model=ApplyRunSubmittedResponse)
async def record_run_submitted(
    request: Request,
    run_id: str,
    body: ApplyRunSubmittedRequest | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplyRunSubmittedResponse:
    """Record the submission the executor performed. Requires the approval token."""
    body = body or ApplyRunSubmittedRequest()
    scoped = _scoped(request, x_profile_id, body.profile_id)
    run = _require_run(request, run_id, scoped)
    store = request.app.state.application_store
    if run.status == "submitting":
        if not run.approval_token or body.approval_token != run.approval_token:
            raise HTTPException(403, "Approval token missing or wrong; the submission was not recorded.")
        actor = "executor"
    elif run.status == "needs_verification":
        application = store.get_application(run.application_id)
        if application is None or application.status is not ApplicationStatus.SUBMITTED:
            raise HTTPException(409, "Confirm the application as submitted first (Mark as submitted), then close the run.")
        actor = "user"
    else:
        raise HTTPException(409, f"Run is {run.status}; nothing to record.")
    updates: dict[str, Any] = {"step": "submitted", "approval_token": None}
    if body.screenshot_b64:
        try:
            updates["screenshot_path"] = save_screenshot(store.path, run_id, "submitted", body.screenshot_b64)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        application, bundle = confirm_submission(
            request.app,
            application_id=run.application_id,
            profile_id=scoped,
            confirmed_by="user",
            detection_evidence=body.detection_evidence or f"executor run {run_id}",
            notes=f"Submitted by the local executor after approval (run {run_id}).",
        )
    except InvalidApplicationTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    updated = _transition(
        request, run_id, "submitted", actor=actor, updates=updates,
        log_message=f"Submission recorded; receipt {bundle.bundle_id}.",
    )
    return ApplyRunSubmittedResponse(run=updated, application=application, bundle_id=bundle.bundle_id)


def _now() -> str:
    from latex_resume.job_models import utc_now

    return utc_now()
