"""Extension routes: /extension/resume/*, /extension/forms/*."""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from latex_resume.api import (
    AnswerProposalRequest,
    AnswerProposalResponse,
    AnswerUsageRequest,
    AnswerUsageResponse,
    ApplicationAnswerDraftRequest,
    FillPlanOverrideRequest,
    FillPlanResponse,
    FormScanRequest,
    PrepareResumeRequest,
    PreparedResumeResponse,
    ResumeCustomizationPreviewRequest,
    ResumeCustomizationPreviewResponse,
    _build_fill_plan_for_scan,
    _prepared_response_from_artifact,
    _profile_pdf_response,
    _render_profile_latex_to_pdf,
)
from latex_resume.answer_proposals import propose_short_answers
from latex_resume.application_answers import ApplicationAnswerDraft, generate_application_answer
from latex_resume.ats import check_ats
from latex_resume.extractor import extract_full_resume
from latex_resume.form_resolution import (
    is_question_draft_eligible,
    is_question_proposal_eligible,
    remember_answer,
)
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    FormScan,
    PlanOverride,
    utc_now,
)
from latex_resume.optimizer import (
    _build_plain_text,
    extract_job_keywords_fast,
    run_optimization_pipeline,
    split_skill_confirmation_candidates,
)
from latex_resume.routers._deps import (
    require_application_for_profile,
    require_artifact_for_profile,
    require_form_scan_for_profile,
    resolve_request_profile_id,
)
from latex_resume.run_analysis import ats_to_dict

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/extension/resume/prepare", response_model=PreparedResumeResponse)
async def prepare_extension_resume(
    request: Request,
    body: PrepareResumeRequest,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> PreparedResumeResponse:
    """Return a PDF resume for the current application.

    If an approved web-reviewed artifact exists for the application, return it.
    Otherwise, customization can still be generated locally from profile LaTeX.
    """
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    if body.artifact_id:
        artifact = require_artifact_for_profile(request, body.artifact_id, scoped_profile_id)
        return _prepared_response_from_artifact(
            artifact,
            db_path=request.app.state.application_store.path,
        )

    if body.application_id and body.prefer_approved_artifact:
        require_application_for_profile(request, body.application_id, scoped_profile_id)
        artifact = request.app.state.application_store.get_latest_application_artifact(
            body.application_id,
            artifact_type=ApplicationArtifactType.TAILORED_RESUME,
            status=ApplicationArtifactStatus.APPROVED,
        )
        if artifact is not None:
            return _prepared_response_from_artifact(
                artifact,
                db_path=request.app.state.application_store.path,
                warnings=["Using the approved tailored resume from the web review."],
            )

    resolved_profile_id = scoped_profile_id
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    profile = _render_profile_latex_to_pdf(
        profile,
        db_path=request.app.state.application_store.path,
    )
    request.app.state.application_store.save_candidate_profile(profile)

    warnings: list[str] = []
    db_path = request.app.state.application_store.path
    if not body.customize:
        return _profile_pdf_response(profile, db_path=db_path)
    if not body.job_description.strip():
        warnings.append(
            "No captured job description was available; using the saved profile resume."
        )
        return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)
    if not profile.resume_latex_source.strip():
        warnings.append("Profile resume is a PDF only, so customization is unavailable.")
        return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)

    try:
        from latex_resume.parser import parse as _parse

        parse_result = _parse(
            profile.resume_latex_source,
            resume_id=Path(profile.resume_filename or "profile_resume").stem,
        )
        opt = await run_optimization_pipeline(
            parse_result=parse_result,
            job_description=body.job_description,
            confirmed_skills=body.confirmed_skills,
            allowed_stmt_ids=body.allowed_stmt_ids,
            optimization_strategy=body.optimization_strategy,
            reviewer_backend=body.reviewer_backend,
        )
    except Exception as exc:
        warnings.append(
            f"Customization failed locally; using saved profile resume. Reason: {exc}"
        )
        return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)

    if opt.overflow or not opt.pdf_bytes:
        warnings.extend(opt.warnings)
        warnings.append(
            "Customized resume did not pass the one-page render gate; using saved profile resume."
        )
        return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)

    filename = f"{Path(profile.resume_filename or 'resume').stem}_customized.pdf"
    artifact_id: str | None = None
    artifact_status: ApplicationArtifactStatus | None = None
    if body.application_id:
        application = require_application_for_profile(
            request, body.application_id, resolved_profile_id
        )
        artifact = ApplicationArtifact(
            artifact_id=str(uuid.uuid4()),
            application_id=body.application_id,
            job_id=application.job_id,
            profile_id=resolved_profile_id,
            type=ApplicationArtifactType.TAILORED_RESUME,
            status=ApplicationArtifactStatus.GENERATED,
            filename=filename,
            mime_type="application/pdf",
            latex_source=opt.modified_latex,
            pdf_b64=base64.b64encode(opt.pdf_bytes).decode(),
            diff=opt.diff,
            confirmed_skills=opt.confirmed_skills,
            ats_before=ats_to_dict(opt.ats_before),
            ats_after=ats_to_dict(opt.ats_after),
            warnings=opt.warnings,
            page_count=opt.page_count,
            overflow=opt.overflow,
            visual_overflow=opt.visual_overflow,
            min_text_baseline_pt=opt.min_text_baseline_pt,
        )
        try:
            saved_artifact = request.app.state.application_store.save_application_artifact(
                artifact
            )
            request.app.state.application_store.create_application_event(
                application_id=body.application_id,
                kind="resume_generated",
                label="Tailored resume generated",
                detail="Generated from the browser extension review flow.",
                payload={"artifact_id": saved_artifact.artifact_id},
            )
            artifact_id = saved_artifact.artifact_id
            artifact_status = saved_artifact.status
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    return PreparedResumeResponse(
        filename=filename,
        mime_type="application/pdf",
        data_b64=base64.b64encode(opt.pdf_bytes).decode(),
        customized=True,
        artifact_id=artifact_id,
        artifact_status=artifact_status,
        warnings=opt.warnings,
        ats_score=opt.ats_after.score if opt.ats_after else None,
        overflow=opt.overflow,
    )


@router.post(
    "/extension/resume/customization-preview",
    response_model=ResumeCustomizationPreviewResponse,
)
async def preview_resume_customization(
    request: Request,
    body: ResumeCustomizationPreviewRequest,
    profile_id: str | None = None,
) -> ResumeCustomizationPreviewResponse:
    """Return fast local skill confirmation candidates before customization."""
    resolved_profile_id = (
        profile_id or request.app.state.application_store.get_active_profile_id()
    )
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    if not body.job_description.strip():
        return ResumeCustomizationPreviewResponse(
            available=False,
            warnings=["No captured job description is available."],
        )
    if not profile.resume_latex_source.strip():
        return ResumeCustomizationPreviewResponse(
            available=False,
            warnings=["Upload a .tex profile resume to enable customization."],
        )
    try:
        from latex_resume.parser import parse as _parse

        parse_result = _parse(
            profile.resume_latex_source,
            resume_id=Path(profile.resume_filename or "profile_resume").stem,
        )
        resume_data = extract_full_resume(parse_result)
        plain_resume = _build_plain_text(resume_data)
        job_keywords = extract_job_keywords_fast(body.job_description)
        baseline = check_ats(plain_resume, job_keywords)
        raw_missing = list(
            dict.fromkeys(
                list(baseline.required_missing) + list(baseline.preferred_missing)
            )
        )
        candidates, theme_gaps = split_skill_confirmation_candidates(raw_missing)
    except Exception as exc:
        return ResumeCustomizationPreviewResponse(
            available=False,
            warnings=[f"Could not analyze the profile resume: {exc}"],
        )
    return ResumeCustomizationPreviewResponse(
        available=True,
        baseline_score=baseline.score,
        required_missing=list(baseline.required_missing),
        preferred_missing=list(baseline.preferred_missing),
        skill_candidates=candidates,
        theme_gaps=theme_gaps,
    )


@router.post("/extension/forms/scan", response_model=FormScan)
async def save_form_scan(
    request: Request,
    body: FormScanRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> FormScan:
    """Store a read-only form inventory from the Chrome extension."""
    if not body.page_url.startswith("https://"):
        raise HTTPException(400, "Form page URL must use HTTPS.")
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    if body.application_id:
        require_application_for_profile(request, body.application_id, scoped_profile_id)
    scan = FormScan(
        scan_id=str(uuid.uuid4()),
        application_id=body.application_id,
        provider=body.provider,
        page_url=body.page_url,
        page_title=body.page_title,
        step_key=body.step_key,
        form_signature=body.form_signature,
        replace_existing=body.replace_existing,
        questions=body.questions,
    )
    try:
        return request.app.state.application_store.save_form_scan(scan)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/extension/forms/{scan_id}/plan", response_model=FillPlanResponse)
async def build_fill_plan(
    request: Request,
    scan_id: str,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> FillPlanResponse:
    """Resolve known fields while keeping final submission unavailable."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    require_form_scan_for_profile(request, scan_id, scoped_profile_id)
    return _build_fill_plan_for_scan(
        request.app,
        scan_id=scan_id,
        profile_id=scoped_profile_id,
    )


@router.post("/extension/forms/{scan_id}/plan", response_model=FillPlanResponse)
async def override_fill_plan(
    request: Request,
    scan_id: str,
    body: FillPlanOverrideRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> FillPlanResponse:
    """Merge reviewed one-off answers into the scan plan without inventing profile facts."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id,
    )
    scan = require_form_scan_for_profile(request, scan_id, scoped_profile_id)
    cleaned: dict[str, PlanOverride] = {}
    for field_id, value in body.overrides.items():
        clean_field_id = field_id.strip()
        if not clean_field_id:
            continue
        if isinstance(value, str):
            clean_value: str | bool | list[str] = value.strip()
            if not clean_value:
                continue
        elif isinstance(value, list):
            clean_value = [item.strip() for item in value if item.strip()]
            if not clean_value:
                continue
        else:
            clean_value = value
        cleaned[clean_field_id] = PlanOverride(
            value=clean_value,
            answer_source=body.answer_source,
            research_sources=body.research_sources,
        )
    updated = scan.model_copy(
        update={
            "plan_overrides": {
                **scan.plan_overrides,
                **cleaned,
            }
        }
    )
    store = request.app.state.application_store
    store.save_form_scan(updated)
    if body.remember and cleaned:
        _remember_overrides(store, scan, cleaned, scoped_profile_id, body.answer_source)
    return _build_fill_plan_for_scan(
        request.app,
        scan_id=scan_id,
        profile_id=scoped_profile_id,
    )


def _remember_overrides(
    store,
    scan: FormScan,
    overrides: dict[str, PlanOverride],
    profile_id: str,
    answer_source: str,
) -> None:
    """Write reviewed overrides back as profile facts or answers-bank rows."""
    profile = store.get_candidate_profile(profile_id)
    original = profile
    by_id = {question.field_id: question for question in scan.questions}
    source = "llm_reviewed" if answer_source == "generated" else "user"
    for field_id, override in overrides.items():
        question = by_id.get(field_id)
        if question is None:
            continue
        profile, saved = remember_answer(
            profile,
            question,
            override.value,
            provider=scan.provider.value,
            source=source,
        )
        if saved is not None:
            store.upsert_profile_answer(saved)
    if profile is not original:
        store.save_candidate_profile(profile)


@router.post("/extension/forms/{scan_id}/answers/propose", response_model=AnswerProposalResponse)
async def propose_answers(
    request: Request,
    scan_id: str,
    body: AnswerProposalRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> AnswerProposalResponse:
    """Suggest short answers from saved facts only; nothing is filled until confirmed."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id,
    )
    scan = require_form_scan_for_profile(request, scan_id, scoped_profile_id)
    plan = _build_fill_plan_for_scan(request.app, scan_id=scan_id, profile_id=scoped_profile_id)
    wanted = set(body.field_ids)
    questions = [
        question
        for question, action in zip(scan.questions, plan.actions, strict=True)
        if (not wanted or question.field_id in wanted)
        and question.required
        and is_question_proposal_eligible(question, action)
    ]
    if not questions:
        return AnswerProposalResponse(scan_id=scan_id, proposals=[])
    store = request.app.state.application_store
    company = ""
    job_title = ""
    if scan.application_id:
        application = store.get_application(scan.application_id)
        if application:
            company = application.company
            job_title = application.job_title
    try:
        proposals = await propose_short_answers(
            questions,
            store.get_candidate_profile(scoped_profile_id),
            store.list_profile_answers(scoped_profile_id),
            provider=scan.provider.value,
            company=company,
            job_title=job_title,
        )
    except Exception as exc:
        logger.exception("Answer proposal generation failed for scan %s", scan_id)
        raise HTTPException(502, str(exc)) from exc
    return AnswerProposalResponse(scan_id=scan_id, proposals=proposals)


@router.post("/extension/forms/{scan_id}/answers/used", response_model=AnswerUsageResponse)
async def record_answers_used(
    request: Request,
    scan_id: str,
    body: AnswerUsageRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> AnswerUsageResponse:
    """Record that remembered answers were filled, so the bank shows real usage."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id,
    )
    require_form_scan_for_profile(request, scan_id, scoped_profile_id)
    plan = _build_fill_plan_for_scan(request.app, scan_id=scan_id, profile_id=scoped_profile_id)
    wanted = set(body.field_ids)
    answer_ids = [
        action.saved_answer_id
        for action in plan.actions
        if action.saved_answer_id and (not wanted or action.field_id in wanted)
    ]
    recorded = request.app.state.application_store.record_profile_answer_usage(
        scoped_profile_id, answer_ids
    )
    return AnswerUsageResponse(recorded=recorded)


@router.post(
    "/extension/forms/{scan_id}/answers/draft",
    response_model=ApplicationAnswerDraft,
)
async def draft_application_answer(
    request: Request,
    scan_id: str,
    body: ApplicationAnswerDraftRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ApplicationAnswerDraft:
    """Generate a short evidence-grounded draft without changing the browser form."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=body.profile_id,
    )
    scan = require_form_scan_for_profile(request, scan_id, scoped_profile_id)
    question = next(
        (item for item in scan.questions if item.field_id == body.field_id),
        None,
    )
    if question is None:
        raise HTTPException(404, f"Unknown field_id: {body.field_id}")
    if not is_question_draft_eligible(question):
        raise HTTPException(409, "AI drafts are available only for narrative application questions.")
    profile = request.app.state.application_store.get_candidate_profile(scoped_profile_id)
    try:
        return await generate_application_answer(
            request.app.state.application_store,
            scan=scan,
            question=question,
            profile=profile,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("Application answer generation failed for scan %s", scan_id)
        raise HTTPException(502, str(exc)) from exc
