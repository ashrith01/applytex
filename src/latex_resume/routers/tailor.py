"""Guided tailor session routes: /tailor/*."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from latex_resume.api import (
    ApproveTailorSessionRequest,
    CreateTailorSessionRequest,
    ProjectRankResponse,
    TailorOptimizeRequest,
    TailorRefineRequest,
    TailorSessionResponse,
    UpdateTailorProjectsRequest,
    UpdateTailorSessionRequest,
    _application_artifact_from_tailor_session,
    _apply_project_selection_to_session,
    _build_tailor_session_response,
    _optimization_to_dict,
    _rank_projects_for_session,
    _score_application_for_profile,
    limiter,
)
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationStage,
    ApplicationStatus,
)
from latex_resume.optimizer import (
    extract_job_keywords_fast,
    refine_resume_with_instruction,
    run_optimization_pipeline,
)
from latex_resume.project_library import allowed_statement_ids_after_project_filter
from latex_resume.renderer import check_one_page
from latex_resume.local_auth import auth_required
from latex_resume.routers._deps import (
    require_application_for_profile,
    resolve_request_profile_id,
)
from latex_resume.session import store
from latex_resume.tailor_store import TailorSession, tailor_store

logger = logging.getLogger(__name__)

router = APIRouter()


def _owned_tailor_session(request: Request, session_id: str) -> TailorSession:
    """Tailor sessions belong to the profile that opened them; others get 404.

    Sessions created before ownership existed have an empty profile_id; they
    stay reachable in local mode and are hidden once auth is required.
    """
    scoped = resolve_request_profile_id(request=request, x_profile_id=request.headers.get("x-profile-id"))
    session = tailor_store.get(session_id)
    if (
        session is None
        or (session.profile_id and session.profile_id != scoped)
        or (not session.profile_id and auth_required())
    ):
        raise HTTPException(404, f"Tailor session '{session_id}' not found.")
    return session


@router.post("/tailor/sessions", response_model=TailorSessionResponse)
async def create_tailor_session(
    request: Request,
    body: CreateTailorSessionRequest,
) -> TailorSessionResponse:
    """Bootstrap a guided tailor flow for a saved job and active profile."""
    profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=request.headers.get("x-profile-id"),
        profile_id=body.profile_id,
    )
    if not auth_required():
        request.app.state.application_store.set_active_profile_id(profile_id)
    profile = request.app.state.application_store.get_candidate_profile(profile_id)
    job = request.app.state.application_store.get_job(body.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{body.job_id}' not found.")
    if not profile.resume_latex_source.strip():
        raise HTTPException(
            409,
            "Upload a .tex profile resume before tailoring.",
        )

    from latex_resume.parser import parse as _parse

    parse_result = _parse(
        profile.resume_latex_source,
        resume_id=Path(profile.resume_filename or "profile_resume").stem,
    )
    latex_session = await store.create(
        parse_result=parse_result,
        latex_source=profile.resume_latex_source,
        filename=profile.resume_filename or "profile_resume.tex",
        profile_id=profile_id,
    )
    session = tailor_store.create(
        job_id=body.job_id,
        profile_id=profile_id,
        application_id=body.application_id,
        current_latex=profile.resume_latex_source,
    )
    session.latex_session_id = latex_session.session_id
    await _rank_projects_for_session(request.app, session, reset_default=True)
    tailor_store.save(session)
    if body.application_id:
        try:
            await _score_application_for_profile(
                request.app, body.application_id, profile_id
            )
        except HTTPException as exc:
            if exc.status_code not in {404, 409}:
                raise
    return await _build_tailor_session_response(request.app, session)


@router.get("/tailor/sessions/{session_id}", response_model=TailorSessionResponse)
async def get_tailor_session(
    request: Request,
    session_id: str,
) -> TailorSessionResponse:
    session = _owned_tailor_session(request, session_id)
    return await _build_tailor_session_response(request.app, session)


@router.patch("/tailor/sessions/{session_id}", response_model=TailorSessionResponse)
async def update_tailor_session(
    request: Request,
    session_id: str,
    body: UpdateTailorSessionRequest,
) -> TailorSessionResponse:
    session = _owned_tailor_session(request, session_id)
    if body.confirmed_skills is not None:
        session.confirmed_skills = list(body.confirmed_skills)
    if body.current_latex is not None:
        session.source_latex = body.current_latex
        session.current_latex = body.current_latex
        session.diff = []
        session.last_result = None
        await _rank_projects_for_session(request.app, session, reset_default=True)
    tailor_store.save(session)
    return await _build_tailor_session_response(request.app, session)


@router.post(
    "/tailor/sessions/{session_id}/projects/rank",
    response_model=ProjectRankResponse,
)
async def rank_tailor_projects(
    request: Request,
    session_id: str,
) -> ProjectRankResponse:
    """Rank resume and GitHub projects against this session's job."""
    session = _owned_tailor_session(request, session_id)
    ranked = await _rank_projects_for_session(
        request.app,
        session,
        reset_default=not session.selected_project_ids,
    )
    tailor_store.save(session)
    return ranked


@router.patch("/tailor/sessions/{session_id}/projects", response_model=ProjectRankResponse)
async def update_tailor_projects(
    request: Request,
    session_id: str,
    body: UpdateTailorProjectsRequest,
) -> ProjectRankResponse:
    """Persist user-approved resume projects for the tailored PDF."""
    session = _owned_tailor_session(request, session_id)
    if not session.project_recommendations:
        await _rank_projects_for_session(request.app, session, reset_default=True)
    selectable_ids = {
        item.project.project_id
        for item in session.project_recommendations
        if item.selectable
    }
    session.selected_project_ids = [
        project_id
        for project_id in dict.fromkeys(body.selected_project_ids)
        if project_id in selectable_ids
    ]
    ranked = await _rank_projects_for_session(request.app, session, reset_default=False)
    tailor_store.save(session)
    return ranked


@router.post("/tailor/sessions/{session_id}/optimize", response_model=TailorSessionResponse)
@limiter.limit("10/minute")
async def optimize_tailor_session(
    request: Request,
    session_id: str,
    body: TailorOptimizeRequest,
) -> TailorSessionResponse:
    session = _owned_tailor_session(request, session_id)
    job = request.app.state.application_store.get_job(session.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{session.job_id}' not found.")
    if not session.latex_session_id:
        raise HTTPException(409, "Tailor session has no linked LaTeX session.")

    latex_session_obj = await store.get(session.latex_session_id)
    if latex_session_obj is None:
        raise HTTPException(
            404, f"Session '{session.latex_session_id}' not found or expired."
        )
    if not session.project_recommendations:
        await _rank_projects_for_session(request.app, session, reset_default=True)
    filtered_latex = _apply_project_selection_to_session(session)

    from latex_resume.parser import parse as _parse

    filtered_parse_result = _parse(
        filtered_latex,
        resume_id=Path(session.profile_id).stem,
    )
    allowed_stmt_ids = allowed_statement_ids_after_project_filter(filtered_parse_result)
    if body.allowed_stmt_ids is not None:
        allowed_stmt_ids = [
            stmt_id
            for stmt_id in body.allowed_stmt_ids
            if stmt_id in set(allowed_stmt_ids)
        ]
    async with latex_session_obj.lock:
        latex_session_obj.parse_result = filtered_parse_result
        latex_session_obj.latex_source = filtered_latex
        opt = await run_optimization_pipeline(
            parse_result=filtered_parse_result,
            job_description=job.description,
            confirmed_skills=session.confirmed_skills,
            allowed_stmt_ids=allowed_stmt_ids,
            optimization_strategy=body.optimization_strategy,
            reviewer_backend=body.reviewer_backend,
        )
        latex_session_obj.optimization_result = opt
        latex_session_obj.latex_source = opt.modified_latex
        latex_session_obj.touch()

    session.current_latex = opt.modified_latex
    session.diff = opt.diff
    session.change_history.extend(opt.diff)
    session.last_result = _optimization_to_dict(opt)
    if session.project_filter_warnings:
        session.last_result["warnings"] = [
            *session.project_filter_warnings,
            *[
                str(item)
                for item in session.last_result.get("warnings", [])
                if isinstance(item, str)
            ],
        ]
    tailor_store.save(session)
    return await _build_tailor_session_response(request.app, session)


@router.post("/tailor/sessions/{session_id}/refine", response_model=TailorSessionResponse)
async def refine_tailor_session(
    request: Request,
    session_id: str,
    body: TailorRefineRequest,
) -> TailorSessionResponse:
    session = _owned_tailor_session(request, session_id)
    job = request.app.state.application_store.get_job(session.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{session.job_id}' not found.")

    job_keywords = extract_job_keywords_fast(job.description)
    opt = await refine_resume_with_instruction(
        latex_source=session.current_latex,
        job_description=job.description,
        instruction=body.instruction,
        job_keywords=job_keywords,
        confirmed_skills=session.confirmed_skills,
        allowed_stmt_ids=body.allowed_stmt_ids,
        scope_label=body.scope_label,
    )
    session.current_latex = opt.modified_latex
    session.diff = opt.diff
    session.change_history.extend(opt.diff)
    session.last_result = _optimization_to_dict(opt)
    if session.latex_session_id:
        latex_session_obj = await store.get(session.latex_session_id)
        if latex_session_obj is not None:
            latex_session_obj.latex_source = opt.modified_latex
            latex_session_obj.optimization_result = opt
    tailor_store.save(session)
    return await _build_tailor_session_response(request.app, session)


@router.post(
    "/tailor/sessions/{session_id}/approve",
    response_model=ApplicationArtifact,
)
async def approve_tailor_session(
    request: Request,
    session_id: str,
    body: ApproveTailorSessionRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> ApplicationArtifact:
    """Persist the reviewed tailored resume as an approved application artifact."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    session = _owned_tailor_session(request, session_id)
    if session.profile_id and session.profile_id != scoped_profile_id:
        raise HTTPException(404, f"Tailor session '{session_id}' not found.")
    application_id = body.application_id or session.application_id
    if not application_id:
        raise HTTPException(
            409, "Attach this tailor session to an application before approval."
        )
    application = require_application_for_profile(request, application_id, scoped_profile_id)
    if application.job_id != session.job_id:
        raise HTTPException(409, "Tailor session job does not match the application job.")
    if not session.current_latex.strip():
        raise HTTPException(409, "Tailor session has no LaTeX source to approve.")

    render = check_one_page(session.current_latex)
    if render.overflow:
        raise HTTPException(
            409,
            "Tailored resume exceeds the one-page gate and cannot be approved.",
        )
    fallback_pdf_b64 = None
    if isinstance(session.last_result, dict):
        value = session.last_result.get("modified_pdf_b64")
        fallback_pdf_b64 = value if isinstance(value, str) and value else None
    pdf_b64 = (
        base64.b64encode(render.pdf_bytes).decode()
        if render.pdf_bytes
        else fallback_pdf_b64
    )
    if not pdf_b64:
        raise HTTPException(
            409,
            "A PDF could not be rendered locally, so the tailored resume cannot be approved for upload.",
        )

    job = request.app.state.application_store.get_job(session.job_id)
    job_slug = "_".join(
        part.lower()
        for part in [
            *(job.company.split() if job else []),
            *(job.title.split() if job else []),
        ]
        if part.isalnum()
    )
    filename = body.filename or f"{job_slug or 'tailored_resume'}_applytex.pdf"
    artifact = _application_artifact_from_tailor_session(
        session=session,
        application_id=application_id,
        filename=filename,
        pdf_b64=pdf_b64,
        render_page_count=render.page_count,
        render_visual_overflow=render.visual_overflow,
        render_min_text_baseline_pt=render.min_text_baseline_pt,
        status=ApplicationArtifactStatus.APPROVED,
        db_path=request.app.state.application_store.path,
    )
    try:
        saved = request.app.state.application_store.save_application_artifact(artifact)
        request.app.state.application_store.create_application_event(
            application_id=application_id,
            kind="resume_approved",
            label="Tailored resume approved",
            detail="Approved in the local web tailoring studio.",
            payload={"artifact_id": saved.artifact_id, "session_id": session_id},
        )
        record = request.app.state.application_store.get_application(application_id)
        if record and record.status in {
            ApplicationStatus.DISCOVERED,
            ApplicationStatus.SCORED,
            ApplicationStatus.SELECTED,
        }:
            if record.status is ApplicationStatus.DISCOVERED:
                record = request.app.state.application_store.transition_application(
                    application_id,
                    ApplicationStatus.SELECTED,
                )
            if record.status is ApplicationStatus.SCORED:
                record = request.app.state.application_store.transition_application(
                    application_id,
                    ApplicationStatus.SELECTED,
                )
            if record.status is ApplicationStatus.SELECTED:
                request.app.state.application_store.transition_application(
                    application_id,
                    ApplicationStatus.RESUME_READY,
                )
        else:
            request.app.state.application_store.update_application(
                application_id,
                {"stage": ApplicationStage.TAILORING},
            )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return saved
