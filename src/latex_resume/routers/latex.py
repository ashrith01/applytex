"""LaTeX session routes: /latex/*."""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile

from latex_resume.api import (
    AnalyzeRequest,
    AnalyzeResponse,
    OptimizeRequest,
    OptimizeResponse,
    RefineRequest,
    RerenderRequest,
    RerenderResponse,
    ReportResponse,
    StatusResponse,
    UploadResponse,
    _analyze_resume,
    _resolve_latex_source,
)
from latex_resume.engine import extract_editable
from latex_resume.extractor import extract_full_resume
from latex_resume.models import ParseResult
from latex_resume.optimizer import (
    extract_job_keywords_fast,
    refine_resume_with_instruction,
    run_optimization_pipeline,
)
from latex_resume.renderer import check_one_page
from latex_resume.run_analysis import build_run_record
from latex_resume.session import ResumeSession, store
from latex_resume.api import limiter

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_session_or_404(session_id: str, profile_id: str | None = None) -> ResumeSession:
    """Local alias that delegates to the shared helper in api.py."""
    # Import here to avoid a module-level circular import between routers
    # and api (api.py is not fully loaded until after all router modules
    # are first imported inside create_app()).
    from latex_resume.api import _get_session_or_404 as _orig  # noqa: PLC0415
    return await _orig(session_id, profile_id)


def _scoped(request: Request, x_profile_id: str | None, profile_id: str | None = None) -> str:
    from latex_resume.routers._deps import resolve_request_profile_id  # noqa: PLC0415

    return resolve_request_profile_id(request=request, x_profile_id=x_profile_id, profile_id=profile_id)


@router.post("/latex/upload", response_model=UploadResponse)
async def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> UploadResponse:
    """Parse a ``.tex`` file and open a new optimization session owned by this profile."""
    scoped_profile_id = _scoped(request, x_profile_id)
    if not file.filename or not file.filename.endswith(".tex"):
        raise HTTPException(400, "Only .tex files are accepted.")

    raw = await file.read()
    try:
        latex_source = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 encoded.")

    if len(latex_source) > 500_000:
        raise HTTPException(413, "File too large (max 500 KB).")

    try:
        from latex_resume.parser import parse as _parse

        pr: ParseResult = _parse(latex_source, resume_id=file.filename.removesuffix(".tex"))
    except Exception as exc:
        logger.error("Parse failed for %s: %s", file.filename, exc)
        raise HTTPException(422, f"Failed to parse .tex file: {exc}")

    session = await store.create(
        parse_result=pr,
        latex_source=latex_source,
        filename=file.filename,
        profile_id=scoped_profile_id,
    )

    editable_data = extract_editable(pr)
    resume_data = extract_full_resume(pr)

    return UploadResponse(
        session_id=session.session_id,
        filename=file.filename,
        editable=editable_data["editable"],
        resume_data=resume_data,
        page_budget=editable_data["page_budget"],
    )


@router.post("/latex/optimize", response_model=OptimizeResponse)
@limiter.limit("10/minute")
async def optimize_resume(
    request: Request,
    body: OptimizeRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> OptimizeResponse:
    """Run the full LLM optimization pipeline for a session."""

    scoped_profile_id = _scoped(request, x_profile_id)
    session = await _get_session_or_404(body.session_id, scoped_profile_id)

    if not body.job_description.strip():
        raise HTTPException(400, "job_description must not be empty.")

    async with session.lock:
        opt = await run_optimization_pipeline(
            parse_result=session.parse_result,
            job_description=body.job_description,
            confirmed_skills=body.confirmed_skills,
            allowed_stmt_ids=body.allowed_stmt_ids,
            optimization_strategy=body.optimization_strategy,
            reviewer_backend=body.reviewer_backend,
        )
        session.optimization_result = opt
        store.save(session)

    pdf_b64: str | None = None
    if opt.ats_target_met and not opt.overflow and opt.pdf_bytes:
        pdf_b64 = base64.b64encode(opt.pdf_bytes).decode()

    return OptimizeResponse(
        session_id=body.session_id,
        optimization_strategy=opt.optimization_strategy,
        reviewer_backend=opt.reviewer_backend,
        strategy_notes=opt.strategy_notes,
        diff=opt.diff,
        warnings=opt.warnings,
        ats_target_score=opt.ats_target_score,
        ats_target_met=opt.ats_target_met,
        confirmed_skills=opt.confirmed_skills,
        confirmation_required_skills=opt.confirmation_required_skills,
        ats_before=opt.ats_before.__dict__ if opt.ats_before else None,
        ats_after=opt.ats_after.__dict__ if opt.ats_after else None,
        overflow=opt.overflow,
        visual_overflow=opt.visual_overflow,
        min_text_baseline_pt=opt.min_text_baseline_pt,
        page_count=opt.page_count,
        modified_latex=opt.modified_latex,
        modified_pdf_b64=pdf_b64,
    )


@router.get("/latex/{session_id}/status", response_model=StatusResponse)
async def session_status(
    request: Request,
    session_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> StatusResponse:
    session = await _get_session_or_404(session_id, _scoped(request, x_profile_id))
    return StatusResponse(**session.to_status_dict())


@router.post("/latex/{session_id}/rerender", response_model=RerenderResponse)
async def rerender(
    request: Request,
    session_id: str,
    body: RerenderRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> RerenderResponse:
    """Apply a custom changes map to the original parsed resume and re-render."""

    scoped_profile_id = _scoped(request, x_profile_id)
    from latex_resume.engine import reconstruct

    session = await _get_session_or_404(session_id, scoped_profile_id)

    async with session.lock:
        recon = reconstruct(session.parse_result, body.changes)
        session.touch()

    check = check_one_page(recon.latex)

    pdf_b64: str | None = None
    if not check.overflow and check.pdf_bytes:
        pdf_b64 = base64.b64encode(check.pdf_bytes).decode()

    return RerenderResponse(
        applied=recon.applied,
        rejected=recon.rejected,
        overflow=check.overflow,
        visual_overflow=check.visual_overflow,
        min_text_baseline_pt=check.min_text_baseline_pt,
        page_count=check.page_count,
        modified_latex=recon.latex,
        modified_pdf_b64=pdf_b64,
    )


@router.delete("/latex/{session_id}", status_code=204)
async def delete_session(
    request: Request,
    session_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> None:
    await _get_session_or_404(session_id, _scoped(request, x_profile_id))
    existed = await store.delete(session_id)
    if not existed:
        raise HTTPException(404, f"Session '{session_id}' not found.")


@router.post("/latex/analyze", response_model=AnalyzeResponse)
async def analyze_resume(body: AnalyzeRequest) -> AnalyzeResponse:
    """Score a resume against a JD without running full optimization."""
    latex_source, parse_result = await _resolve_latex_source(
        session_id=body.session_id,
        latex_source=body.latex_source,
    )
    return await _analyze_resume(
        latex_source=latex_source,
        parse_result=parse_result,
        job_description=body.job_description,
        confirmed_skills=body.confirmed_skills,
        analysis_mode=body.analysis_mode,
    )


@router.post("/latex/{session_id}/refine", response_model=OptimizeResponse)
async def refine_session(
    request: Request,
    session_id: str,
    body: RefineRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> OptimizeResponse:
    """Apply a chat-style instruction to an uploaded resume session."""

    scoped_profile_id = _scoped(request, x_profile_id)
    session = await _get_session_or_404(session_id, scoped_profile_id)
    latex_source = body.latex_source or session.latex_source
    job_keywords = body.job_keywords or extract_job_keywords_fast(body.job_description)

    async with session.lock:
        opt = await refine_resume_with_instruction(
            latex_source=latex_source,
            job_description=body.job_description,
            instruction=body.instruction,
            job_keywords=job_keywords,
            confirmed_skills=body.confirmed_skills,
            allowed_stmt_ids=body.allowed_stmt_ids,
            scope_label=body.scope_label,
        )
        session.optimization_result = opt
        session.latex_source = opt.modified_latex
        store.save(session)

    pdf_b64 = None
    if opt.pdf_bytes and not opt.overflow:
        pdf_b64 = base64.b64encode(opt.pdf_bytes).decode()

    return OptimizeResponse(
        session_id=session_id,
        optimization_strategy=opt.optimization_strategy,
        reviewer_backend=opt.reviewer_backend,
        strategy_notes=opt.strategy_notes,
        diff=opt.diff,
        warnings=opt.warnings,
        ats_target_score=opt.ats_target_score,
        ats_target_met=opt.ats_target_met,
        confirmed_skills=opt.confirmed_skills,
        confirmation_required_skills=opt.confirmation_required_skills,
        ats_before=opt.ats_before.__dict__ if opt.ats_before else None,
        ats_after=opt.ats_after.__dict__ if opt.ats_after else None,
        overflow=opt.overflow,
        visual_overflow=opt.visual_overflow,
        min_text_baseline_pt=opt.min_text_baseline_pt,
        page_count=opt.page_count,
        modified_latex=opt.modified_latex,
        modified_pdf_b64=pdf_b64,
    )


@router.get("/latex/{session_id}/report", response_model=ReportResponse)
async def session_report(
    request: Request,
    session_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ReportResponse:
    """Return optimization analytics for a completed session."""

    scoped_profile_id = _scoped(request, x_profile_id)
    session = await _get_session_or_404(session_id, scoped_profile_id)
    opt = session.optimization_result
    if opt is None:
        return ReportResponse(run_record=None, optimized=False)
    record = build_run_record(
        opt,
        job_description="",
        confirmed_skills=opt.confirmed_skills,
        resume_id=session.parse_result.doc.resume_id,
        source="api",
    )
    return ReportResponse(run_record=record, optimized=True)
