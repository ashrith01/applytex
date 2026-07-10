"""FastAPI application for the LaTeX resume optimizer.

Routes
------
POST /latex/upload
    Upload a ``.tex`` file.  Returns a ``session_id`` and the parsed
    editable JSON.

POST /latex/optimize
    Submit a job description for the uploaded resume.  Runs the full
    LLM pipeline and returns the diff + modified LaTeX.

GET  /latex/{session_id}/status
    Lightweight status check (no LaTeX payload).

POST /latex/{session_id}/rerender
    Apply a custom ``{stmt_id: new_text}`` changes map and re-render,
    returning the modified LaTeX and PDF bytes (base64).

DELETE /latex/{session_id}
    Explicitly delete a session.

GET  /health
    Returns ``{"status": "ok"}``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from latex_resume.application_store import (
    ApplicationStore,
    InvalidApplicationTransition,
)
from latex_resume.ats import check_ats
from latex_resume.engine import extract_editable, parse_file, reconstruct
from latex_resume.extractor import extract_full_resume
from latex_resume.job_models import (
    AddressProfile,
    ApplicationRecord,
    ApplicationStatus,
    BrowserJobCapture,
    CandidateProfile,
    EducationProfile,
    EqualOpportunityProfile,
    FillAction,
    FormQuestion,
    FormScan,
    JobPosting,
    JobProvider,
    JobSearchQuery,
    JobSearchResult,
    JobSourceConfig,
    SearchPreferences,
    WorkAuthorizationProfile,
    WorkExperienceProfile,
    utc_now,
)
from latex_resume.form_resolution import profile_setup_status, resolve_form_questions
from latex_resume.job_sources import JobSearchService, captured_job_to_posting
from latex_resume.models import ParseResult
from latex_resume.optimizer import (
    DEFAULT_OPTIMIZER_STRATEGY,
    OptimizationResult,
    OptimizerStrategy,
    ReviewerBackend,
    _build_plain_text,
    extract_job_keywords_fast,
    extract_job_keywords_with_fallback,
    refine_resume_with_instruction,
    run_optimization_pipeline,
    split_skill_confirmation_candidates,
)
from latex_resume.profile_extraction import extract_profile_facts_from_tex, profile_with_resume_prefill
from latex_resume.renderer import check_one_page, render_pdf
from latex_resume.run_analysis import ats_to_dict, build_run_record
from latex_resume.screening import analyze_screening_fit
from latex_resume.session import ResumeSession, store
from latex_resume.tailor_store import TailorSession, tailor_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class OptimizeRequest(BaseModel):
    session_id: str
    job_description: str
    confirmed_skills: list[str] = Field(default_factory=list)
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY
    reviewer_backend: ReviewerBackend | None = None


class RerenderRequest(BaseModel):
    changes: dict[str, str]


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    editable: dict[str, Any]
    resume_data: dict[str, Any]
    page_budget: dict[str, Any]


class OptimizeResponse(BaseModel):
    session_id: str
    optimization_strategy: str
    reviewer_backend: str
    strategy_notes: str
    diff: list[dict[str, Any]]
    warnings: list[str]
    ats_target_score: float
    ats_target_met: bool
    confirmed_skills: list[str]
    confirmation_required_skills: list[str]
    ats_before: dict[str, Any] | None
    ats_after: dict[str, Any] | None
    overflow: bool
    visual_overflow: bool
    min_text_baseline_pt: float | None
    page_count: int
    modified_latex: str
    modified_pdf_b64: str | None  # None when pdflatex unavailable or submission gate fails


class RerenderResponse(BaseModel):
    applied: list[str]
    rejected: dict[str, str]  # stmt_id → rejection reason
    overflow: bool
    visual_overflow: bool
    min_text_baseline_pt: float | None
    page_count: int
    modified_latex: str
    modified_pdf_b64: str | None


class StatusResponse(BaseModel):
    session_id: str
    filename: str
    optimized: bool
    overflow: bool | None
    visual_overflow: bool | None
    min_text_baseline_pt: float | None
    page_count: int | None
    ats_target_score: float | None
    ats_target_met: bool | None
    ats_score: float | None
    confirmation_required_skills: list[str]
    changes_applied: int
    warnings: list[str]


class JobSearchRequest(BaseModel):
    query: JobSearchQuery
    sources: list[JobSourceConfig] = Field(min_length=1, max_length=50)
    use_saved_preferences: bool = True


class CreateApplicationRequest(BaseModel):
    job_id: str
    resume_session_id: str | None = None
    notes: str = Field(default="", max_length=4000)


class TransitionApplicationRequest(BaseModel):
    status: ApplicationStatus
    notes: str | None = Field(default=None, max_length=4000)


class FormScanRequest(BaseModel):
    application_id: str | None = None
    provider: JobProvider
    page_url: str
    page_title: str = Field(default="", max_length=500)
    questions: list[FormQuestion] = Field(default_factory=list, max_length=300)


class FillReviewItem(BaseModel):
    field_id: str
    label: str
    status: Literal["ready", "skipped"]
    required: bool = False
    answer_source: str
    value_preview: str | None = None


class FillPlanResponse(BaseModel):
    scan_id: str
    page_url: str
    actions: list[FillAction]
    review_items: list[FillReviewItem] = Field(default_factory=list)
    unresolved_required: list[str]
    can_fill: bool
    can_submit: bool = False


class ProfileSetupQuestion(BaseModel):
    key: str
    label: str
    category: str
    required: bool
    value_present: bool


class ProfileSetupResponse(BaseModel):
    questions: list[ProfileSetupQuestion]
    missing_required: list[str]
    ready_for_basic_autofill: bool


class ProfileResumeInfo(BaseModel):
    profile_id: str
    resume_filename: str = ""
    resume_pdf_filename: str = ""
    has_latex_source: bool = False
    has_pdf: bool = False
    resume_updated_at: str = ""


class ProfileResumeUploadResponse(ProfileResumeInfo):
    prefill_applied: list[str] = Field(default_factory=list)
    prefill_labels: list[str] = Field(default_factory=list)


class ProfileView(BaseModel):
    profile_id: str
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    address: AddressProfile = Field(default_factory=AddressProfile)
    linkedin_url: str = ""
    portfolio_url: str = ""
    github_url: str = ""
    skills: list[str] = Field(default_factory=list)
    education: EducationProfile = Field(default_factory=EducationProfile)
    educations: list[EducationProfile] = Field(default_factory=list)
    work_experiences: list[WorkExperienceProfile] = Field(default_factory=list)
    work_authorization: WorkAuthorizationProfile = Field(
        default_factory=WorkAuthorizationProfile
    )
    equal_opportunity: EqualOpportunityProfile = Field(
        default_factory=EqualOpportunityProfile
    )
    search_preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    custom_answers: dict[str, str] = Field(default_factory=dict)
    resume_filename: str = ""
    resume_pdf_filename: str = ""
    has_latex_source: bool = False
    has_pdf: bool = False
    resume_updated_at: str = ""
    updated_at: str = ""


class ProfilePatch(BaseModel):
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    address: AddressProfile | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    github_url: str | None = None
    skills: list[str] | None = None
    education: EducationProfile | None = None
    educations: list[EducationProfile] | None = None
    work_experiences: list[WorkExperienceProfile] | None = None
    work_authorization: WorkAuthorizationProfile | None = None
    equal_opportunity: EqualOpportunityProfile | None = None
    search_preferences: SearchPreferences | None = None
    custom_answers: dict[str, str] | None = None


_PREFILL_LABELS: dict[str, str] = {
    "full_name": "Full name",
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "linkedin_url": "LinkedIn",
    "github_url": "GitHub",
    "portfolio_url": "Portfolio",
    "education": "Education",
    "educations": "Education entries",
    "work_experiences": "Work experience",
    "skills": "Skills",
}


class ActiveProfileResponse(BaseModel):
    profile_id: str
    full_name: str = ""
    email: str = ""
    resume_filename: str = ""
    has_pdf: bool = False
    has_latex_source: bool = False


class PreparedResumeResponse(BaseModel):
    filename: str
    mime_type: str
    data_b64: str
    customized: bool
    warnings: list[str] = Field(default_factory=list)
    ats_score: float | None = None
    overflow: bool = False


class PrepareResumeRequest(BaseModel):
    job_description: str = Field(default="", max_length=100_000)
    customize: bool = True
    confirmed_skills: list[str] = Field(default_factory=list, max_length=100)
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY
    reviewer_backend: ReviewerBackend | None = None


class ResumeCustomizationPreviewRequest(BaseModel):
    job_description: str = Field(default="", max_length=100_000)


class ResumeCustomizationPreviewResponse(BaseModel):
    available: bool
    warnings: list[str] = Field(default_factory=list)
    baseline_score: float | None = None
    required_missing: list[str] = Field(default_factory=list)
    preferred_missing: list[str] = Field(default_factory=list)
    skill_candidates: list[str] = Field(default_factory=list)
    theme_gaps: list[str] = Field(default_factory=list)


class SetActiveProfileRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)


class AnalyzeRequest(BaseModel):
    job_description: str = Field(min_length=1, max_length=100_000)
    session_id: str | None = None
    latex_source: str | None = Field(default=None, max_length=500_000)
    confirmed_skills: list[str] = Field(default_factory=list)
    analysis_mode: Literal["fast", "deep"] = "fast"


class AnalyzeResponse(BaseModel):
    job_keywords: dict[str, Any]
    baseline_ats: dict[str, Any]
    screening: dict[str, Any]
    skill_candidates: list[str]
    theme_gaps: list[str]
    skill_groups: dict[str, list[str]]
    editable_statement_count: int
    latency_ms: dict[str, float] = Field(default_factory=dict)


class RefineRequest(BaseModel):
    job_description: str = Field(min_length=1, max_length=100_000)
    instruction: str = Field(min_length=1, max_length=4000)
    confirmed_skills: list[str] = Field(default_factory=list)
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    scope_label: str = Field(default="Selected resume statements", max_length=200)
    latex_source: str | None = Field(default=None, max_length=500_000)
    job_keywords: dict[str, Any] | None = None


class ReportResponse(BaseModel):
    run_record: dict[str, Any] | None = None
    optimized: bool


class CreateTailorSessionRequest(BaseModel):
    job_id: str
    profile_id: str | None = None
    application_id: str | None = None


class TailorSessionResponse(BaseModel):
    session_id: str
    job_id: str
    profile_id: str
    application_id: str | None
    latex_session_id: str | None
    job: JobPosting
    match_preview: AnalyzeResponse
    current_latex: str
    confirmed_skills: list[str]
    diff: list[dict[str, Any]]
    change_history: list[dict[str, Any]]
    last_result: dict[str, Any] | None


class UpdateTailorSessionRequest(BaseModel):
    confirmed_skills: list[str] | None = None
    current_latex: str | None = None


class TailorOptimizeRequest(BaseModel):
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY
    reviewer_backend: ReviewerBackend | None = None


class TailorRefineRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    scope_label: str = Field(default="Selected resume sections", max_length=200)


# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------

_CLEANUP_INTERVAL = 600  # seconds


async def _session_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        try:
            removed = await store.cleanup_expired()
            if removed:
                logger.info("Session cleanup: removed %d expired session(s)", removed)
        except Exception as exc:
            logger.error("Session cleanup error: %s", exc)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_session_cleanup_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _deep_merge_profile_dict(current: dict[str, object], updates: dict[str, object]) -> dict[str, object]:
    """Merge PATCH payloads without replacing whole nested profile sections."""
    merged = dict(current)
    nested_dict_keys = {
        "address",
        "education",
        "work_authorization",
        "equal_opportunity",
        "search_preferences",
    }
    for key, value in updates.items():
        if key in nested_dict_keys and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _preview_fill_value(value: str | bool | None) -> str | None:
    if value is None:
        return None
    text = "Yes" if value is True else "No" if value is False else str(value)
    return text if len(text) <= 48 else f"{text[:45]}..."


def _profile_resume_info(profile: CandidateProfile) -> ProfileResumeInfo:
    return ProfileResumeInfo(
        profile_id=profile.profile_id,
        resume_filename=profile.resume_filename,
        resume_pdf_filename=profile.resume_pdf_filename,
        has_latex_source=bool(profile.resume_latex_source.strip()),
        has_pdf=bool(profile.resume_pdf_b64.strip()),
        resume_updated_at=profile.resume_updated_at,
    )


def _profile_view(profile: CandidateProfile) -> ProfileView:
    return ProfileView(
        profile_id=profile.profile_id,
        full_name=profile.full_name,
        first_name=profile.first_name,
        last_name=profile.last_name,
        email=profile.email,
        phone=profile.phone,
        location=profile.location,
        address=profile.address,
        linkedin_url=profile.linkedin_url,
        portfolio_url=profile.portfolio_url,
        github_url=profile.github_url,
        skills=profile.skills,
        education=profile.education,
        educations=profile.educations,
        work_experiences=profile.work_experiences,
        work_authorization=profile.work_authorization,
        equal_opportunity=profile.equal_opportunity,
        search_preferences=profile.search_preferences,
        custom_answers=profile.custom_answers,
        resume_filename=profile.resume_filename,
        resume_pdf_filename=profile.resume_pdf_filename,
        has_latex_source=bool(profile.resume_latex_source.strip()),
        has_pdf=bool(profile.resume_pdf_b64.strip()),
        resume_updated_at=profile.resume_updated_at,
        updated_at=profile.updated_at,
    )


def _repair_profile_from_resume_metadata(profile: CandidateProfile) -> CandidateProfile:
    """Refresh clearly stale resume-derived facts from stored LaTeX metadata."""
    if not profile.resume_latex_source.strip():
        return profile
    try:
        facts = extract_profile_facts_from_tex(profile.resume_latex_source)
    except Exception:
        logger.exception("Unable to refresh profile metadata from stored resume source.")
        return profile

    updated = profile.model_copy(deep=True)
    changed = False

    extracted_educations = facts.get("educations")
    if (
        isinstance(extracted_educations, list)
        and extracted_educations
        and _educations_need_resume_refresh(updated.educations, extracted_educations)
    ):
        updated.educations = extracted_educations
        updated.education = extracted_educations[0]
        changed = True

    extracted_work = facts.get("work_experiences")
    if (
        isinstance(extracted_work, list)
        and extracted_work
        and _work_experiences_need_resume_refresh(updated.work_experiences, extracted_work)
    ):
        updated.work_experiences = extracted_work
        changed = True

    extracted_skills = facts.get("skills")
    if isinstance(extracted_skills, list) and extracted_skills and not updated.skills:
        updated.skills = extracted_skills
        changed = True

    if changed:
        updated.updated_at = utc_now()
    return updated if changed else profile


def _educations_need_resume_refresh(
    current: list[EducationProfile],
    extracted: list[EducationProfile],
) -> bool:
    if len(current) != len(extracted):
        return True
    for saved, fresh in zip(current, extracted):
        if not saved.school or not saved.degree:
            return True
        if saved.school == fresh.degree or saved.degree == fresh.school:
            return True
        if saved.school != fresh.school and saved.degree != fresh.degree:
            return True
        if fresh.gpa and not saved.gpa:
            return True
    return False


def _work_experiences_need_resume_refresh(
    current: list[WorkExperienceProfile],
    extracted: list[WorkExperienceProfile],
) -> bool:
    if len(current) != len(extracted):
        return True
    for saved, fresh in zip(current, extracted):
        if not saved.company or not saved.job_title:
            return True
        if fresh.bullets and not saved.bullets:
            return True
        if saved.company == fresh.job_title and saved.job_title.startswith(fresh.company):
            return True
        if saved.job_title == fresh.company and saved.company.startswith(fresh.job_title):
            return True
    return False


def _profile_resume_upload_response(
    profile: CandidateProfile,
    *,
    prefill_applied: list[str],
) -> ProfileResumeUploadResponse:
    info = _profile_resume_info(profile)
    labels = [_PREFILL_LABELS.get(key, key.replace("_", " ").title()) for key in prefill_applied]
    return ProfileResumeUploadResponse(
        **info.model_dump(),
        prefill_applied=prefill_applied,
        prefill_labels=labels,
    )


def _profile_pdf_response(
    profile: CandidateProfile,
    *,
    warnings: list[str] | None = None,
) -> PreparedResumeResponse:
    if not profile.resume_pdf_b64:
        raise HTTPException(
            409,
            "No profile PDF resume is available. Upload a PDF or a renderable .tex resume first.",
        )
    filename = profile.resume_pdf_filename or profile.resume_filename or "profile_resume.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{Path(filename).stem or 'profile_resume'}.pdf"
    return PreparedResumeResponse(
        filename=filename,
        mime_type="application/pdf",
        data_b64=profile.resume_pdf_b64,
        customized=False,
        warnings=warnings or [],
    )


def _skill_group(skill: str) -> str:
    norm = skill.lower()
    if any(token in norm for token in ("instinct", "communication", "stakeholder", "product", "judgment")):
        return "Soft Skills"
    if any(
        token in norm
        for token in (
            "api", "javascript", "langchain", "autogen", "n8n", "zapier",
            "slack", "salesforce", "notion", "openai", "anthropic", "crew", "tool",
        )
    ):
        return "Tools"
    return "Functional Skills"


def _group_skill_candidates(candidates: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"Functional Skills": [], "Tools": [], "Soft Skills": []}
    for skill in candidates:
        groups[_skill_group(skill)].append(skill)
    return groups


async def _resolve_latex_source(
    *,
    session_id: str | None,
    latex_source: str | None,
) -> tuple[str, ParseResult]:
    if session_id:
        session = await _get_session_or_404(session_id)
        return session.latex_source, session.parse_result
    if latex_source and latex_source.strip():
        from latex_resume.parser import parse as _parse

        pr = _parse(latex_source, resume_id="analyze")
        return latex_source, pr
    raise HTTPException(400, "Provide session_id or latex_source.")


async def _analyze_resume(
    *,
    latex_source: str,
    parse_result: ParseResult,
    job_description: str,
    confirmed_skills: list[str],
    analysis_mode: Literal["fast", "deep"],
) -> AnalyzeResponse:
    started = time.perf_counter()
    full_resume = extract_full_resume(parse_result)
    plain_resume = _build_plain_text(full_resume)
    if analysis_mode == "deep":
        job_keywords = await extract_job_keywords_with_fallback(job_description)
    else:
        job_keywords = extract_job_keywords_fast(job_description)
    keyword_ms = (time.perf_counter() - started) * 1000
    ats_started = time.perf_counter()
    baseline = check_ats(plain_resume, job_keywords, confirmed_skills=confirmed_skills)
    ats_ms = (time.perf_counter() - ats_started) * 1000
    screening = analyze_screening_fit(
        plain_resume,
        job_keywords,
        baseline,
        editable_statement_count=len(parse_result.stmt_index),
    )
    raw_missing = list(
        dict.fromkeys(list(baseline.required_missing) + list(baseline.preferred_missing))
    )
    candidates, theme_gaps = split_skill_confirmation_candidates(raw_missing)
    return AnalyzeResponse(
        job_keywords=job_keywords,
        baseline_ats=ats_to_dict(baseline) or {},
        screening=screening.to_dict(),
        skill_candidates=candidates,
        theme_gaps=theme_gaps,
        skill_groups=_group_skill_candidates(candidates),
        editable_statement_count=len(parse_result.stmt_index),
        latency_ms={"keywords": keyword_ms, "ats": ats_ms},
    )


def _optimization_to_dict(opt: OptimizationResult) -> dict[str, Any]:
    pdf_b64: str | None = None
    if opt.ats_target_met and not opt.overflow and opt.pdf_bytes:
        pdf_b64 = base64.b64encode(opt.pdf_bytes).decode()
    return {
        "optimization_strategy": opt.optimization_strategy,
        "reviewer_backend": opt.reviewer_backend,
        "strategy_notes": opt.strategy_notes,
        "diff": opt.diff,
        "warnings": opt.warnings,
        "ats_target_score": opt.ats_target_score,
        "ats_target_met": opt.ats_target_met,
        "confirmed_skills": opt.confirmed_skills,
        "confirmation_required_skills": opt.confirmation_required_skills,
        "ats_before": ats_to_dict(opt.ats_before),
        "ats_after": ats_to_dict(opt.ats_after),
        "overflow": opt.overflow,
        "visual_overflow": opt.visual_overflow,
        "min_text_baseline_pt": opt.min_text_baseline_pt,
        "page_count": opt.page_count,
        "modified_latex": opt.modified_latex,
        "modified_pdf_b64": pdf_b64,
    }


async def _build_tailor_session_response(
    app: FastAPI,
    session: TailorSession,
) -> TailorSessionResponse:
    job = app.state.application_store.get_job(session.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{session.job_id}' not found.")
    if not session.current_latex.strip():
        raise HTTPException(409, "Tailor session has no LaTeX source.")
    from latex_resume.parser import parse as _parse

    parse_result = _parse(
        session.current_latex,
        resume_id=Path(session.profile_id).stem,
    )
    match_preview = await _analyze_resume(
        latex_source=session.current_latex,
        parse_result=parse_result,
        job_description=job.description,
        confirmed_skills=session.confirmed_skills,
        analysis_mode="fast",
    )
    return TailorSessionResponse(
        session_id=session.session_id,
        job_id=session.job_id,
        profile_id=session.profile_id,
        application_id=session.application_id,
        latex_session_id=session.latex_session_id,
        job=job,
        match_preview=match_preview,
        current_latex=session.current_latex,
        confirmed_skills=session.confirmed_skills,
        diff=session.diff,
        change_history=session.change_history,
        last_result=session.last_result,
    )


def _render_profile_latex_to_pdf(profile: CandidateProfile) -> CandidateProfile:
    if not profile.resume_latex_source.strip():
        return profile
    render = render_pdf(profile.resume_latex_source)
    if not render.ok or not render.pdf_bytes:
        return profile
    filename = profile.resume_pdf_filename
    if not filename:
        source_name = profile.resume_filename or "profile_resume.tex"
        filename = f"{Path(source_name).stem}.pdf"
    return profile.model_copy(
        update={
            "resume_pdf_filename": filename,
            "resume_pdf_b64": base64.b64encode(render.pdf_bytes).decode(),
            "resume_updated_at": utc_now(),
        }
    )


def create_app(
    *,
    job_search_service: JobSearchService | None = None,
    application_store: ApplicationStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="ApplyTeX ATS API",
        description=(
            "Search public employer job boards, track controlled applications, "
            "and tailor a .tex resume to a job description."
        ),
        version="0.3.0",
        lifespan=_lifespan,
    )
    default_db_path = Path(
        os.environ.get("APPLYTEX_DB_PATH")
        or os.environ.get("SMARTJOBAPPLY_DB_PATH")
        or ".applytex/applytex.db"
    )
    app.state.job_search_service = job_search_service or JobSearchService()
    app.state.application_store = application_store or ApplicationStore(default_db_path)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_origin_regex=(
            r"chrome-extension://[a-p]{32}|"
            r"https://([^/]+\.)?greenhouse\.io|"
            r"https://([^/]+\.)?lever\.co|"
            r"https://([^/]+\.)?ashbyhq\.com|"
            r"https://(www\.)?linkedin\.com"
        ),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_private_network=True,
    )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "sessions": str(store.count)}

    # ------------------------------------------------------------------
    # Public job discovery and application tracking
    # ------------------------------------------------------------------

    @app.post("/jobs/search", response_model=JobSearchResult)
    async def search_jobs(body: JobSearchRequest) -> JobSearchResult:
        """Search configured public ATS boards without browser automation."""
        profile = app.state.application_store.get_candidate_profile()
        preferences = profile.search_preferences if body.use_saved_preferences else None
        result = await app.state.job_search_service.search(
            body.query,
            body.sources,
            preferences,
        )
        app.state.application_store.save_search(result)
        return result

    @app.get("/jobs", response_model=list[JobPosting])
    async def list_jobs(limit: int = 100) -> list[JobPosting]:
        """List normalized jobs saved from previous searches."""
        bounded_limit = min(max(limit, 1), 200)
        return app.state.application_store.list_jobs(bounded_limit)

    @app.get("/jobs/{job_id}", response_model=JobPosting)
    async def get_job(job_id: str) -> JobPosting:
        """Return one saved job by ID."""
        job = app.state.application_store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"Job '{job_id}' not found.")
        return job

    @app.post("/extension/jobs/capture", response_model=JobPosting)
    async def capture_browser_job(body: BrowserJobCapture) -> JobPosting:
        """Persist a job captured from a user-visible LinkedIn or ATS tab."""
        if not body.source_url.startswith("https://") or not body.apply_url.startswith("https://"):
            raise HTTPException(400, "Captured job URLs must use HTTPS.")
        job = captured_job_to_posting(body)
        app.state.application_store.save_job(job)
        return job

    @app.get("/profile", response_model=CandidateProfile)
    async def get_profile(profile_id: str = "default") -> CandidateProfile:
        """Return locally stored candidate facts and search preferences."""
        return app.state.application_store.get_candidate_profile(profile_id)

    @app.get("/profile/view", response_model=ProfileView)
    async def get_profile_view(profile_id: str = "default") -> ProfileView:
        """Return editable profile facts without raw resume source or PDF bytes."""
        profile = app.state.application_store.get_candidate_profile(profile_id)
        repaired = _repair_profile_from_resume_metadata(profile)
        if repaired is not profile:
            profile = app.state.application_store.save_candidate_profile(repaired)
        return _profile_view(profile)

    @app.get("/profile/active", response_model=ActiveProfileResponse)
    async def get_active_profile() -> ActiveProfileResponse:
        """Return the profile selected in the local web UI."""
        profile_id = app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(profile_id)
        return ActiveProfileResponse(
            profile_id=profile.profile_id,
            full_name=profile.full_name,
            email=profile.email,
            resume_filename=profile.resume_pdf_filename or profile.resume_filename,
            has_pdf=bool(profile.resume_pdf_b64),
            has_latex_source=bool(profile.resume_latex_source),
        )

    @app.put("/profile/active", response_model=ActiveProfileResponse)
    async def set_active_profile(body: SetActiveProfileRequest) -> ActiveProfileResponse:
        """Select which local profile the UI and extension should use."""
        profile_id = app.state.application_store.set_active_profile_id(body.profile_id)
        profile = app.state.application_store.get_candidate_profile(profile_id)
        return ActiveProfileResponse(
            profile_id=profile.profile_id,
            full_name=profile.full_name,
            email=profile.email,
            resume_filename=profile.resume_pdf_filename or profile.resume_filename,
            has_pdf=bool(profile.resume_pdf_b64),
            has_latex_source=bool(profile.resume_latex_source),
        )

    @app.get("/profile/setup-questions", response_model=ProfileSetupResponse)
    async def get_profile_setup_questions(profile_id: str = "default") -> ProfileSetupResponse:
        """Return common application questions covered by the user profile."""
        questions = [
            ProfileSetupQuestion.model_validate(item)
            for item in profile_setup_status(
                app.state.application_store.get_candidate_profile(profile_id)
            )
        ]
        missing_required = [
            question.label
            for question in questions
            if question.required and not question.value_present
        ]
        return ProfileSetupResponse(
            questions=questions,
            missing_required=missing_required,
            ready_for_basic_autofill=not missing_required,
        )

    @app.put("/profile", response_model=CandidateProfile)
    async def update_profile(body: CandidateProfile) -> CandidateProfile:
        """Replace the local candidate profile with explicitly supplied facts."""
        return app.state.application_store.save_candidate_profile(body)

    @app.patch("/profile", response_model=ProfileView)
    async def patch_profile(
        body: ProfilePatch,
        profile_id: str | None = None,
    ) -> ProfileView:
        """Merge editable profile facts while preserving stored resume payloads."""
        resolved_profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(resolved_profile_id)
        updates = body.model_dump(exclude_unset=True)
        merged = CandidateProfile.model_validate(
            _deep_merge_profile_dict(profile.model_dump(), updates)
        )
        saved = app.state.application_store.save_candidate_profile(
            merged
        )
        return _profile_view(saved)

    @app.get("/profile/resume", response_model=ProfileResumeInfo)
    async def get_profile_resume(profile_id: str | None = None) -> ProfileResumeInfo:
        """Return metadata for the resume saved with a local candidate profile."""
        profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(profile_id)
        return _profile_resume_info(profile)

    @app.post("/profile/resume", response_model=ProfileResumeUploadResponse)
    async def upload_profile_resume(
        file: UploadFile = File(...),
        profile_id: str | None = None,
        overwrite: bool = False,
    ) -> ProfileResumeUploadResponse:
        """Store a profile resume and extract facts into the candidate profile.

        PDF uploads are stored for direct application use. LaTeX uploads are stored
        for job-specific customization and rendered to PDF when a local LaTeX engine
        is available. Parsed resume content is mapped into profile fields when found.
        """
        if not file.filename:
            raise HTTPException(400, "Resume filename is required.")
        suffix = Path(file.filename).suffix.casefold()
        if suffix not in {".tex", ".pdf"}:
            raise HTTPException(400, "Only .tex and .pdf profile resumes are accepted.")

        raw = await file.read()
        if len(raw) > 2_500_000:
            raise HTTPException(413, "Resume file too large (max 2.5 MB).")

        profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(profile_id)
        updates: dict[str, Any] = {
            "resume_filename": file.filename,
            "resume_updated_at": utc_now(),
        }
        if suffix == ".pdf":
            if not raw.startswith(b"%PDF"):
                raise HTTPException(400, "Uploaded .pdf does not look like a PDF file.")
            updates.update(
                {
                    "resume_pdf_filename": file.filename,
                    "resume_pdf_b64": base64.b64encode(raw).decode(),
                }
            )
        else:
            try:
                latex_source = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(400, "LaTeX resume must be UTF-8 encoded.") from None
            if len(latex_source) > 500_000:
                raise HTTPException(413, "LaTeX resume too large (max 500 KB).")
            try:
                from latex_resume.parser import parse as _parse

                _parse(latex_source, resume_id=Path(file.filename).stem)
            except Exception as exc:
                raise HTTPException(422, f"Failed to parse .tex resume: {exc}") from exc
            updates["resume_latex_source"] = latex_source
            render = render_pdf(latex_source)
            if render.ok and render.pdf_bytes:
                updates.update(
                    {
                        "resume_pdf_filename": f"{Path(file.filename).stem}.pdf",
                        "resume_pdf_b64": base64.b64encode(render.pdf_bytes).decode(),
                    }
                )

        updated = profile.model_copy(update=updates)
        try:
            prefilled, applied = profile_with_resume_prefill(
                updated,
                filename=file.filename,
                data=raw,
                overwrite=overwrite,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.warning("Profile prefill failed for %s: %s", file.filename, exc)
            prefilled = updated
            applied = []

        saved = app.state.application_store.save_candidate_profile(prefilled)
        return _profile_resume_upload_response(saved, prefill_applied=applied)

    @app.post("/extension/resume/prepare", response_model=PreparedResumeResponse)
    async def prepare_extension_resume(
        body: PrepareResumeRequest,
        profile_id: str | None = None,
    ) -> PreparedResumeResponse:
        """Return a PDF resume for the current application.

        If customization is requested and the profile has LaTeX source, the engine
        tailors it to the captured job description. Otherwise the saved profile PDF
        is returned with a warning.
        """
        profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(profile_id)
        profile = _render_profile_latex_to_pdf(profile)
        app.state.application_store.save_candidate_profile(profile)

        warnings: list[str] = []
        if not body.customize:
            return _profile_pdf_response(profile)
        if not body.job_description.strip():
            warnings.append("No captured job description was available; using the saved profile resume.")
            return _profile_pdf_response(profile, warnings=warnings)
        if not profile.resume_latex_source.strip():
            warnings.append("Profile resume is a PDF only, so customization is unavailable.")
            return _profile_pdf_response(profile, warnings=warnings)

        try:
            from latex_resume.parser import parse as _parse

            parse_result = _parse(
                profile.resume_latex_source,
                resume_id=Path(profile.resume_filename or "profile_resume").stem,
            )
            opt: OptimizationResult = await run_optimization_pipeline(
                parse_result=parse_result,
                job_description=body.job_description,
                confirmed_skills=body.confirmed_skills,
                allowed_stmt_ids=body.allowed_stmt_ids,
                optimization_strategy=body.optimization_strategy,
                reviewer_backend=body.reviewer_backend,
            )
        except Exception as exc:
            warnings.append(f"Customization failed locally; using saved profile resume. Reason: {exc}")
            return _profile_pdf_response(profile, warnings=warnings)

        if opt.overflow or not opt.pdf_bytes:
            warnings.extend(opt.warnings)
            warnings.append("Customized resume did not pass the one-page render gate; using saved profile resume.")
            return _profile_pdf_response(profile, warnings=warnings)

        filename = f"{Path(profile.resume_filename or 'resume').stem}_customized.pdf"
        return PreparedResumeResponse(
            filename=filename,
            mime_type="application/pdf",
            data_b64=base64.b64encode(opt.pdf_bytes).decode(),
            customized=True,
            warnings=opt.warnings,
            ats_score=opt.ats_after.score if opt.ats_after else None,
            overflow=opt.overflow,
        )

    @app.post(
        "/extension/resume/customization-preview",
        response_model=ResumeCustomizationPreviewResponse,
    )
    async def preview_resume_customization(
        body: ResumeCustomizationPreviewRequest,
        profile_id: str | None = None,
    ) -> ResumeCustomizationPreviewResponse:
        """Return fast local skill confirmation candidates before customization."""
        profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(profile_id)
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

    @app.post("/extension/forms/scan", response_model=FormScan)
    async def save_form_scan(body: FormScanRequest) -> FormScan:
        """Store a read-only form inventory from the Chrome extension."""
        if not body.page_url.startswith("https://"):
            raise HTTPException(400, "Form page URL must use HTTPS.")
        scan = FormScan(
            scan_id=str(uuid.uuid4()),
            application_id=body.application_id,
            provider=body.provider,
            page_url=body.page_url,
            page_title=body.page_title,
            questions=body.questions,
        )
        try:
            return app.state.application_store.save_form_scan(scan)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/extension/forms/{scan_id}/plan", response_model=FillPlanResponse)
    async def build_fill_plan(scan_id: str, profile_id: str | None = None) -> FillPlanResponse:
        """Resolve known fields while keeping final submission unavailable."""
        scan = app.state.application_store.get_form_scan(scan_id)
        if scan is None:
            raise HTTPException(404, f"Unknown scan_id: {scan_id}")
        employment_track = "unknown"
        if scan.application_id:
            application = app.state.application_store.get_application(scan.application_id)
            if application:
                job = app.state.application_store.get_job(application.job_id)
                if job:
                    employment_track = job.employment_track
        actions = resolve_form_questions(
            scan.questions,
            app.state.application_store.get_candidate_profile(
                profile_id or app.state.application_store.get_active_profile_id()
            ),
            employment_track=employment_track,
        )
        unresolved = [
            question.label
            for question, action in zip(scan.questions, actions, strict=True)
            if question.required and not question.current_value_present and action.action == "skip"
        ]
        review_items = [
            FillReviewItem(
                field_id=question.field_id,
                label=question.label,
                status="ready" if question.current_value_present or action.action != "skip" else "skipped",
                required=question.required,
                answer_source="already_on_page" if question.current_value_present else action.answer_source,
                value_preview="Already filled" if question.current_value_present else _preview_fill_value(action.value),
            )
            for question, action in zip(scan.questions, actions, strict=True)
        ]
        return FillPlanResponse(
            scan_id=scan.scan_id,
            page_url=scan.page_url,
            actions=actions,
            review_items=review_items,
            unresolved_required=unresolved,
            can_fill=not unresolved,
        )

    @app.post("/applications", response_model=ApplicationRecord)
    async def create_application(body: CreateApplicationRequest) -> ApplicationRecord:
        """Create a controlled application record for a saved job."""
        if body.resume_session_id and await store.get(body.resume_session_id) is None:
            raise HTTPException(404, "resume_session_id not found or expired.")
        try:
            return app.state.application_store.create_application(
                job_id=body.job_id,
                resume_session_id=body.resume_session_id,
                notes=body.notes,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/applications", response_model=list[ApplicationRecord])
    async def list_applications(limit: int = 100) -> list[ApplicationRecord]:
        """List locally persisted application records."""
        bounded_limit = min(max(limit, 1), 200)
        return app.state.application_store.list_applications(bounded_limit)

    @app.post(
        "/applications/{application_id}/transition",
        response_model=ApplicationRecord,
    )
    async def transition_application(
        application_id: str,
        body: TransitionApplicationRequest,
    ) -> ApplicationRecord:
        """Advance an application through the human-approved workflow."""
        try:
            return app.state.application_store.transition_application(
                application_id,
                body.status,
                body.notes,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidApplicationTransition as exc:
            raise HTTPException(409, str(exc)) from exc

    # ------------------------------------------------------------------
    # POST /latex/upload
    # ------------------------------------------------------------------

    @app.post("/latex/upload", response_model=UploadResponse)
    async def upload_resume(file: UploadFile = File(...)) -> UploadResponse:
        """Parse a ``.tex`` file and open a new optimization session."""
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

    # ------------------------------------------------------------------
    # POST /latex/optimize
    # ------------------------------------------------------------------

    @app.post("/latex/optimize", response_model=OptimizeResponse)
    async def optimize_resume(body: OptimizeRequest) -> OptimizeResponse:
        """Run the full LLM optimization pipeline for a session."""
        session = await _get_session_or_404(body.session_id)

        if not body.job_description.strip():
            raise HTTPException(400, "job_description must not be empty.")

        async with session.lock:
            opt: OptimizationResult = await run_optimization_pipeline(
                parse_result=session.parse_result,
                job_description=body.job_description,
                confirmed_skills=body.confirmed_skills,
                allowed_stmt_ids=body.allowed_stmt_ids,
                optimization_strategy=body.optimization_strategy,
                reviewer_backend=body.reviewer_backend,
            )
            session.optimization_result = opt
            session.touch()

        # Reuse the PDF the pipeline already rendered. It is withheld unless the
        # result is both one-page and above the ATS submission gate.
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

    # ------------------------------------------------------------------
    # GET /latex/{session_id}/status
    # ------------------------------------------------------------------

    @app.get("/latex/{session_id}/status", response_model=StatusResponse)
    async def session_status(session_id: str) -> StatusResponse:
        session = await _get_session_or_404(session_id)
        return StatusResponse(**session.to_status_dict())

    # ------------------------------------------------------------------
    # POST /latex/{session_id}/rerender
    # ------------------------------------------------------------------

    @app.post("/latex/{session_id}/rerender", response_model=RerenderResponse)
    async def rerender(session_id: str, body: RerenderRequest) -> RerenderResponse:
        """Apply a custom changes map to the original parsed resume and re-render."""
        session = await _get_session_or_404(session_id)

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

    # ------------------------------------------------------------------
    # DELETE /latex/{session_id}
    # ------------------------------------------------------------------

    @app.delete("/latex/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        existed = await store.delete(session_id)
        if not existed:
            raise HTTPException(404, f"Session '{session_id}' not found.")
        return {"deleted": session_id}

    # ------------------------------------------------------------------
    # Analyze, refine, report
    # ------------------------------------------------------------------

    @app.post("/latex/analyze", response_model=AnalyzeResponse)
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

    @app.post("/latex/{session_id}/refine", response_model=OptimizeResponse)
    async def refine_session(session_id: str, body: RefineRequest) -> OptimizeResponse:
        """Apply a chat-style instruction to an uploaded resume session."""
        session = await _get_session_or_404(session_id)
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
            session.touch()

        pdf_b64: str | None = None
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

    @app.get("/latex/{session_id}/report", response_model=ReportResponse)
    async def session_report(session_id: str) -> ReportResponse:
        """Return optimization analytics for a completed session."""
        session = await _get_session_or_404(session_id)
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

    # ------------------------------------------------------------------
    # Guided tailor sessions
    # ------------------------------------------------------------------

    @app.post("/tailor/sessions", response_model=TailorSessionResponse)
    async def create_tailor_session(body: CreateTailorSessionRequest) -> TailorSessionResponse:
        """Bootstrap a guided tailor flow for a saved job and active profile."""
        profile_id = body.profile_id or app.state.application_store.get_active_profile_id()
        app.state.application_store.set_active_profile_id(profile_id)
        profile = app.state.application_store.get_candidate_profile(profile_id)
        job = app.state.application_store.get_job(body.job_id)
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
        )
        session = tailor_store.create(
            job_id=body.job_id,
            profile_id=profile_id,
            application_id=body.application_id,
            current_latex=profile.resume_latex_source,
        )
        session.latex_session_id = latex_session.session_id
        return await _build_tailor_session_response(app, session)

    @app.get("/tailor/sessions/{session_id}", response_model=TailorSessionResponse)
    async def get_tailor_session(session_id: str) -> TailorSessionResponse:
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        return await _build_tailor_session_response(app, session)

    @app.patch("/tailor/sessions/{session_id}", response_model=TailorSessionResponse)
    async def update_tailor_session(
        session_id: str,
        body: UpdateTailorSessionRequest,
    ) -> TailorSessionResponse:
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        if body.confirmed_skills is not None:
            session.confirmed_skills = list(body.confirmed_skills)
        if body.current_latex is not None:
            session.current_latex = body.current_latex
        session.touch()
        return await _build_tailor_session_response(app, session)

    @app.post("/tailor/sessions/{session_id}/optimize", response_model=TailorSessionResponse)
    async def optimize_tailor_session(
        session_id: str,
        body: TailorOptimizeRequest,
    ) -> TailorSessionResponse:
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        job = app.state.application_store.get_job(session.job_id)
        if job is None:
            raise HTTPException(404, f"Job '{session.job_id}' not found.")
        if not session.latex_session_id:
            raise HTTPException(409, "Tailor session has no linked LaTeX session.")

        latex_session = await _get_session_or_404(session.latex_session_id)
        async with latex_session.lock:
            opt = await run_optimization_pipeline(
                parse_result=latex_session.parse_result,
                job_description=job.description,
                confirmed_skills=session.confirmed_skills,
                allowed_stmt_ids=body.allowed_stmt_ids,
                optimization_strategy=body.optimization_strategy,
                reviewer_backend=body.reviewer_backend,
            )
            latex_session.optimization_result = opt
            latex_session.latex_source = opt.modified_latex
            latex_session.touch()

        session.current_latex = opt.modified_latex
        session.diff = opt.diff
        session.change_history.extend(opt.diff)
        session.last_result = _optimization_to_dict(opt)
        session.touch()
        return await _build_tailor_session_response(app, session)

    @app.post("/tailor/sessions/{session_id}/refine", response_model=TailorSessionResponse)
    async def refine_tailor_session(
        session_id: str,
        body: TailorRefineRequest,
    ) -> TailorSessionResponse:
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        job = app.state.application_store.get_job(session.job_id)
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
            latex_session = await store.get(session.latex_session_id)
            if latex_session is not None:
                latex_session.latex_source = opt.modified_latex
                latex_session.optimization_result = opt
        session.touch()
        return await _build_tailor_session_response(app, session)

    return app


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


async def _get_session_or_404(session_id: str) -> ResumeSession:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(404, f"Session '{session_id}' not found or expired.")
    return session


# ---------------------------------------------------------------------------
# ASGI application & entry point
# ---------------------------------------------------------------------------

app = create_app()


def run() -> None:
    """Entrypoint registered in pyproject.toml as ``applytex-api``."""
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    log_level = os.environ.get("LOG_LEVEL", "info").lower()

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    uvicorn.run(
        "latex_resume.api:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
    )


if __name__ == "__main__":
    run()
