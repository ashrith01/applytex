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
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from latex_resume.application_store import (
    ApplicationStore,
    InvalidApplicationTransition,
)
from latex_resume.application_answers import ApplicationAnswerDraft, generate_application_answer
from latex_resume.ats import check_ats
from latex_resume.engine import extract_editable, parse_file, reconstruct
from latex_resume.extractor import extract_full_resume
from latex_resume.job_models import (
    AddressProfile,
    ApplicationFactsProfile,
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationDetail,
    ApplicationEvent,
    ApplicationRecord,
    ApplicationStage,
    ApplicationStatus,
    ApplicationTask,
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
    PlanOverride,
    JobSourceConfig,
    ProjectRecommendation,
    ProjectRecord,
    ProjectSource,
    QuestionIntent,
    SearchPreferences,
    WorkAuthorizationProfile,
    WorkExperienceProfile,
    utc_now,
)
from latex_resume.form_resolution import (
    classify_question_intent,
    is_question_draft_eligible,
    profile_setup_status,
    resolve_form_questions,
    validate_action_options,
    _geo_values_equivalent,
    _match_option,
)
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
from latex_resume.project_library import (
    GitHubProjectClient,
    allowed_statement_ids_after_project_filter,
    build_resume_project_records,
    default_selected_project_ids,
    filter_latex_projects,
    rank_project_records,
)
from latex_resume.renderer import check_one_page, render_pdf
from latex_resume.run_analysis import ats_to_dict, build_run_record
from latex_resume.screening import analyze_screening_fit
from latex_resume.session import ResumeSession, store
from latex_resume.local_auth import (
    LocalAuthStore,
    auth_required,
    install_auth_middleware,
)
from latex_resume.artifact_files import load_pdf_b64, persist_b64_pdf
from latex_resume.tailor_store import TailorSession, tailor_store
from latex_resume.job_models import AnswerProposal, SavedAnswer
from latex_resume.form_resolution import is_question_proposal_eligible

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class OptimizeRequest(BaseModel):
    session_id: str
    job_description: str = Field(min_length=1, max_length=100_000)
    confirmed_skills: list[str] = Field(default_factory=list)
    allowed_stmt_ids: list[str] | None = Field(default=None, max_length=500)
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY
    reviewer_backend: ReviewerBackend | None = None


class RerenderRequest(BaseModel):
    changes: dict[str, str] = Field(max_length=500)


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
    profile_id: str | None = None
    resume_session_id: str | None = None
    notes: str = Field(default="", max_length=4000)
    force_new: bool = False


class ScoreApplicationRequest(BaseModel):
    profile_id: str | None = None


class ApplicationsHealthResponse(BaseModel):
    total: int
    active: int
    duplicates_merged: int
    average_current_resume_score: float | None = None
    missing_answers: int = 0
    captured_jobs: int = 0
    profile_id: str = "default"


class ApplicationScoreResponse(BaseModel):
    application: ApplicationRecord
    analysis: AnalyzeResponse


class TransitionApplicationRequest(BaseModel):
    status: ApplicationStatus
    notes: str | None = Field(default=None, max_length=4000)


class PatchApplicationRequest(BaseModel):
    stage: ApplicationStage | None = None
    priority: Literal["low", "medium", "high"] | None = None
    excitement: int | None = Field(default=None, ge=1, le=5)
    salary_range: str | None = Field(default=None, max_length=160)
    deadline: str | None = Field(default=None, max_length=80)
    next_action_at: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    missing_answers_count: int | None = Field(default=None, ge=0)


class CreateApplicationEventRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateApplicationTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    category: Literal["follow_up", "missing_answer", "interview", "manual", "deadline"] = "manual"
    due_at: str | None = Field(default=None, max_length=80)
    notes: str = Field(default="", max_length=4000)


class UpdateArtifactStatusRequest(BaseModel):
    status: ApplicationArtifactStatus


class FormScanRequest(BaseModel):
    application_id: str | None = None
    provider: JobProvider
    page_url: str
    page_title: str = Field(default="", max_length=500)
    step_key: str = Field(default="", max_length=500)
    form_signature: str = Field(default="", max_length=2000)
    replace_existing: bool = False
    questions: list[FormQuestion] = Field(default_factory=list, max_length=300)


class FillReviewItem(BaseModel):
    field_id: str
    label: str
    status: Literal["ready", "skipped"]
    required: bool = False
    answer_source: str
    value_preview: str | None = None
    change_kind: Literal["keep", "replace", "fill", "unresolved"] = "unresolved"
    current_value_preview: str | None = None
    planned_value_preview: str | None = None
    failure_status: str | None = None
    question_intent: QuestionIntent = QuestionIntent.UNKNOWN
    draft_eligible: bool = False
    proposal_eligible: bool = False
    resolution_reason: str = ""


class FillPlanResponse(BaseModel):
    scan_id: str
    page_url: str
    actions: list[FillAction]
    review_items: list[FillReviewItem] = Field(default_factory=list)
    unresolved_required: list[str]
    ready_action_count: int = 0
    can_fill: bool
    can_submit: bool = False


class FillPlanOverrideRequest(BaseModel):
    overrides: dict[str, str | bool | list[str]] = Field(default_factory=dict)
    answer_source: Literal["user_input", "generated"] = "user_input"
    research_sources: list[str] = Field(default_factory=list, max_length=12)
    profile_id: str | None = None
    # When true, each override is also written back to the profile (typed
    # eligibility facts) or the answers bank so the next form resolves it.
    remember: bool = False


class AnswerProposalRequest(BaseModel):
    field_ids: list[str] = Field(default_factory=list, max_length=60)
    profile_id: str | None = None


class AnswerProposalResponse(BaseModel):
    scan_id: str
    proposals: list[AnswerProposal] = Field(default_factory=list)


class AnswerUsageRequest(BaseModel):
    field_ids: list[str] = Field(default_factory=list, max_length=300)
    profile_id: str | None = None


class AnswerUsageResponse(BaseModel):
    recorded: int = 0


class SavedAnswerUpsertRequest(BaseModel):
    prompt_text: str = Field(min_length=1, max_length=500)
    value: str | bool | list[str]
    intent: QuestionIntent = QuestionIntent.UNKNOWN
    aliases: list[str] = Field(default_factory=list, max_length=32)
    ats_provider: str = Field(default="", max_length=64)


class SavedAnswerListResponse(BaseModel):
    answers: list[SavedAnswer] = Field(default_factory=list)


class ApplicationAnswerDraftRequest(BaseModel):
    field_id: str = Field(min_length=1, max_length=500)
    profile_id: str | None = None


class AuthLoginRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)
    set_password: bool = False


class AuthLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    profile_id: str
    auth_required: bool


class AuthStatusResponse(BaseModel):
    auth_required: bool
    authenticated: bool = False
    profile_id: str | None = None
    has_password: bool = False



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
    application_facts: ApplicationFactsProfile = Field(
        default_factory=ApplicationFactsProfile
    )
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
    application_facts: ApplicationFactsProfile | None = None
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


class ProfileListItem(BaseModel):
    profile_id: str
    full_name: str = ""
    email: str = ""
    has_pdf: bool = False
    has_latex_source: bool = False
    usable: bool = False


class ProfileListResponse(BaseModel):
    profiles: list[ProfileListItem] = Field(default_factory=list)


class PreparedResumeResponse(BaseModel):
    filename: str
    mime_type: str
    data_b64: str
    customized: bool
    artifact_id: str | None = None
    artifact_status: ApplicationArtifactStatus | None = None
    warnings: list[str] = Field(default_factory=list)
    ats_score: float | None = None
    overflow: bool = False


class PrepareResumeRequest(BaseModel):
    job_description: str = Field(default="", max_length=100_000)
    customize: bool = True
    application_id: str | None = None
    artifact_id: str | None = None
    prefer_approved_artifact: bool = True
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
    project_recommendations: list[ProjectRecommendation] = Field(default_factory=list)
    selected_project_ids: list[str] = Field(default_factory=list)
    project_filter_warnings: list[str] = Field(default_factory=list)
    diff: list[dict[str, Any]]
    change_history: list[dict[str, Any]]
    last_result: dict[str, Any] | None


class ProjectSyncResponse(BaseModel):
    projects: list[ProjectRecord]
    warnings: list[str] = Field(default_factory=list)


class ProjectRankResponse(BaseModel):
    project_recommendations: list[ProjectRecommendation]
    selected_project_ids: list[str]
    project_filter_warnings: list[str] = Field(default_factory=list)


class UpdateTailorProjectsRequest(BaseModel):
    selected_project_ids: list[str] = Field(default_factory=list, max_length=20)


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


class ApproveTailorSessionRequest(BaseModel):
    application_id: str | None = None
    filename: str | None = Field(default=None, max_length=240)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Rate limiter — applied to LLM-invoking routes
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

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


def _validate_startup(app: FastAPI) -> None:
    """Fail fast if critical configuration is unusable."""
    db_path: Path = app.state.application_store.path
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Probe write access with a no-op open.
        with db_path.open("a"):
            pass
    except OSError as exc:
        raise RuntimeError(
            f"APPLYTEX_DB_PATH '{db_path}' is not writable: {exc}. "
            "Set APPLYTEX_DB_PATH to a writable directory before starting."
        ) from exc

    backend = os.environ.get("LLM_BACKEND", "ollama").lower()
    if backend == "groq" and not os.environ.get("GROQ_API_KEY"):
        logger.warning("LLM_BACKEND=groq but GROQ_API_KEY is not set — LLM calls will fail.")
    if backend == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("LLM_BACKEND=anthropic but ANTHROPIC_API_KEY is not set — LLM calls will fail.")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from latex_resume.watchlist import refresh_loop

    _validate_startup(app)
    tasks = [
        asyncio.create_task(_session_cleanup_loop()),
        asyncio.create_task(refresh_loop(app.state.watchlist_ingestor)),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
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
        "application_facts",
    }
    for key, value in updates.items():
        if key in nested_dict_keys and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _preview_fill_value(value: str | bool | list[str] | None) -> str | None:
    if value is None:
        return None
    text = "; ".join(value) if isinstance(value, list) else "Yes" if value is True else "No" if value is False else str(value)
    return text if len(text) <= 48 else f"{text[:45]}..."


def _fill_values_match(
    current: str | bool | list[str] | None,
    planned: str | bool | list[str] | None,
    *,
    select_many: bool = False,
) -> bool:
    if current is None or planned is None:
        return False

    def _scalar_match(left: object, right: object) -> bool:
        left_text = str(left).strip()
        right_text = str(right).strip()
        if left_text.casefold() == right_text.casefold():
            return True
        normalized_pair = (left_text.casefold(), right_text.casefold())
        for short_value, expanded_value in (normalized_pair, normalized_pair[::-1]):
            if short_value in {"no", "false"} and (
                expanded_value.startswith("no")
                or "do not" in expanded_value
                or "don't" in expanded_value
                or "am not" in expanded_value
            ):
                return True
            if short_value in {"yes", "true"} and expanded_value.startswith("yes"):
                return True
        if all(re.fullmatch(r"[\d\s()+.\-]+", value) for value in (left_text, right_text)):
            left_digits = re.sub(r"\D", "", left_text)
            right_digits = re.sub(r"\D", "", right_text)
            if 10 <= len(left_digits) <= 15 and left_digits == right_digits:
                return True
            if {len(left_digits), len(right_digits)} == {10, 11}:
                shorter, longer = sorted((left_digits, right_digits), key=len)
                if longer.startswith("1") and longer[1:] == shorter:
                    return True
        return _geo_values_equivalent(left_text, right_text)

    if isinstance(planned, list):
        if select_many and isinstance(current, list):
            current_norm = {str(item).strip().casefold() for item in current}
            planned_norm = {str(item).strip().casefold() for item in planned}
            if current_norm == planned_norm:
                return True
            return len(current) == len(planned) and all(
                any(_scalar_match(current_item, planned_item) for planned_item in planned)
                for current_item in current
            )
        current_values = current if isinstance(current, list) else [current]
        return any(
            _scalar_match(current_item, planned_item)
            for current_item in current_values
            for planned_item in planned
        )
    return _scalar_match(current, planned)


def _profile_resume_info(profile: CandidateProfile) -> ProfileResumeInfo:
    return ProfileResumeInfo(
        profile_id=profile.profile_id,
        resume_filename=profile.resume_filename,
        resume_pdf_filename=profile.resume_pdf_filename,
        has_latex_source=bool(profile.resume_latex_source.strip()),
        has_pdf=_profile_has_pdf(profile),
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
        application_facts=profile.application_facts,
        custom_answers=profile.custom_answers,
        resume_filename=profile.resume_filename,
        resume_pdf_filename=profile.resume_pdf_filename,
        has_latex_source=bool(profile.resume_latex_source.strip()),
        has_pdf=_profile_has_pdf(profile),
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
        if fresh.degree_level and not saved.degree_level:
            return True
        if fresh.field_of_study_candidates and not saved.field_of_study_candidates:
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


def _profile_has_pdf(profile: CandidateProfile) -> bool:
    return bool(profile.resume_pdf_path.strip() or profile.resume_pdf_b64.strip())


def _profile_pdf_response(
    profile: CandidateProfile,
    *,
    db_path: Path,
    warnings: list[str] | None = None,
) -> PreparedResumeResponse:
    data_b64 = load_pdf_b64(
        db_path=db_path,
        pdf_path=profile.resume_pdf_path or None,
        pdf_b64=profile.resume_pdf_b64 or None,
    )
    if not data_b64:
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
        data_b64=data_b64,
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


async def _score_application_for_profile(
    app: FastAPI,
    application_id: str,
    profile_id: str | None = None,
) -> ApplicationScoreResponse:
    application = app.state.application_store.get_application(application_id)
    if application is None:
        raise HTTPException(404, f"Application '{application_id}' not found.")
    job = app.state.application_store.get_job(application.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{application.job_id}' not found.")
    resolved_profile_id = profile_id or application.profile_id or app.state.application_store.get_active_profile_id()
    profile = app.state.application_store.get_candidate_profile(resolved_profile_id)
    if not profile.resume_latex_source.strip():
        raise HTTPException(409, "Upload a .tex profile resume before scoring this application.")

    from latex_resume.parser import parse as _parse

    parse_result = _parse(
        profile.resume_latex_source,
        resume_id=Path(profile.resume_filename or "profile_resume").stem,
    )
    analysis = await _analyze_resume(
        latex_source=profile.resume_latex_source,
        parse_result=parse_result,
        job_description=job.description,
        confirmed_skills=[],
        analysis_mode="fast",
    )
    baseline = analysis.baseline_ats or {}
    score = baseline.get("score")
    updates: dict[str, object] = {
        "profile_id": profile.profile_id,
        "required_missing": list(baseline.get("required_missing") or []),
        "preferred_missing": list(baseline.get("preferred_missing") or []),
        "keyword_misses": list(baseline.get("keyword_misses") or []),
        "score_updated_at": utc_now(),
    }
    if isinstance(score, (int, float)):
        updates["current_resume_score"] = float(score)
        updates["fit_score"] = float(score)

    if application.status is ApplicationStatus.DISCOVERED:
        try:
            app.state.application_store.transition_application(
                application.application_id,
                ApplicationStatus.SCORED,
            )
        except InvalidApplicationTransition:
            pass
    scored = app.state.application_store.update_application(application.application_id, updates)
    return ApplicationScoreResponse(application=scored, analysis=analysis)


def _refresh_resume_projects(
    app: FastAPI,
    *,
    profile_id: str,
    latex_source: str,
) -> list[ProjectRecord]:
    """Refresh resume-backed project records from a LaTeX source snapshot."""
    if not latex_source.strip():
        app.state.application_store.replace_profile_projects(
            profile_id,
            ProjectSource.RESUME,
            [],
        )
        return []
    from latex_resume.parser import parse as _parse

    parse_result = _parse(latex_source, resume_id=f"{profile_id}_projects")
    records = build_resume_project_records(profile_id, parse_result)
    return app.state.application_store.replace_profile_projects(
        profile_id,
        ProjectSource.RESUME,
        records,
    )


async def _rank_projects_for_session(
    app: FastAPI,
    session: TailorSession,
    *,
    reset_default: bool = False,
) -> ProjectRankResponse:
    """Rank profile projects against the session JD and persist selection state."""
    job = app.state.application_store.get_job(session.job_id)
    if job is None:
        raise HTTPException(404, f"Job '{session.job_id}' not found.")
    source_latex = session.source_latex or session.current_latex
    _refresh_resume_projects(app, profile_id=session.profile_id, latex_source=source_latex)
    projects = app.state.application_store.list_profile_projects(session.profile_id)
    recommendations = rank_project_records(
        projects,
        extract_job_keywords_fast(job.description),
        selected_project_ids=session.selected_project_ids,
    )
    selectable_ids = {
        item.project.project_id
        for item in recommendations
        if item.selectable
    }
    if reset_default:
        selected_ids = default_selected_project_ids(recommendations, limit=2)
    else:
        selected_ids = [
            project_id
            for project_id in dict.fromkeys(session.selected_project_ids)
            if project_id in selectable_ids
        ]
    session.selected_project_ids = selected_ids
    session.project_recommendations = rank_project_records(
        projects,
        extract_job_keywords_fast(job.description),
        selected_project_ids=selected_ids,
    )
    session.touch()
    return ProjectRankResponse(
        project_recommendations=session.project_recommendations,
        selected_project_ids=session.selected_project_ids,
        project_filter_warnings=session.project_filter_warnings,
    )


def _selected_resume_entry_ids(session: TailorSession) -> set[str]:
    selected_ids = set(session.selected_project_ids)
    entry_ids: set[str] = set()
    for item in session.project_recommendations:
        project = item.project
        if (
            project.project_id in selected_ids
            and project.source is ProjectSource.RESUME
            and project.resume_entry_id
        ):
            entry_ids.add(project.resume_entry_id)
    return entry_ids


def _apply_project_selection_to_session(session: TailorSession) -> str:
    """Apply selected resume projects to the clean session source."""
    source_latex = session.source_latex or session.current_latex
    selected_resume_entry_ids = _selected_resume_entry_ids(session)
    result = filter_latex_projects(
        source_latex,
        selected_resume_entry_ids=selected_resume_entry_ids,
    )
    session.project_filter_warnings = result.warnings
    return result.latex_source


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


def _artifact_ats_score(artifact: ApplicationArtifact) -> float | None:
    if artifact.ats_after and isinstance(artifact.ats_after.get("score"), (int, float)):
        return float(artifact.ats_after["score"])
    return None


def _prepared_response_from_artifact(
    artifact: ApplicationArtifact,
    *,
    db_path: Path,
    warnings: list[str] | None = None,
) -> PreparedResumeResponse:
    """Return a stored artifact as the extension upload payload."""
    if artifact.type is not ApplicationArtifactType.TAILORED_RESUME:
        raise HTTPException(409, "The requested artifact is not a tailored resume.")
    data_b64 = load_pdf_b64(
        db_path=db_path,
        pdf_path=artifact.pdf_path or None,
        pdf_b64=artifact.pdf_b64 or None,
    )
    if not data_b64:
        raise HTTPException(409, "The requested artifact has no stored PDF.")
    filename = artifact.filename or "tailored_resume.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{Path(filename).stem or 'tailored_resume'}.pdf"
    return PreparedResumeResponse(
        filename=filename,
        mime_type=artifact.mime_type or "application/pdf",
        data_b64=data_b64,
        customized=True,
        artifact_id=artifact.artifact_id,
        artifact_status=artifact.status,
        warnings=warnings or [],
        ats_score=_artifact_ats_score(artifact),
        overflow=artifact.overflow,
    )


def _application_artifact_from_tailor_session(
    *,
    session: TailorSession,
    application_id: str,
    filename: str,
    pdf_b64: str,
    render_page_count: int,
    render_visual_overflow: bool,
    render_min_text_baseline_pt: float | None,
    status: ApplicationArtifactStatus,
    db_path: Path,
) -> ApplicationArtifact:
    last_result = session.last_result or {}
    now = utc_now()
    pdf_path = ""
    pdf_size = 0
    pdf_sha = ""
    stored_b64 = pdf_b64
    if pdf_b64:
        pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
            db_path=db_path,
            profile_id=session.profile_id,
            name=f"{application_id}_{filename}",
            data_b64=pdf_b64,
        )
        # Keep SQLite lean once the file is on disk.
        stored_b64 = ""
    return ApplicationArtifact(
        artifact_id=str(uuid.uuid4()),
        application_id=application_id,
        job_id=session.job_id,
        profile_id=session.profile_id,
        type=ApplicationArtifactType.TAILORED_RESUME,
        status=status,
        filename=filename,
        mime_type="application/pdf",
        latex_source=session.current_latex,
        pdf_b64=stored_b64,
        pdf_path=pdf_path,
        pdf_size_bytes=pdf_size,
        pdf_sha256=pdf_sha,
        diff=session.diff,
        confirmed_skills=session.confirmed_skills,
        ats_before=last_result.get("ats_before") if isinstance(last_result.get("ats_before"), dict) else None,
        ats_after=last_result.get("ats_after") if isinstance(last_result.get("ats_after"), dict) else None,
        warnings=[
            str(item)
            for item in last_result.get("warnings", [])
            if isinstance(item, str)
        ],
        page_count=render_page_count,
        overflow=False,
        visual_overflow=render_visual_overflow,
        min_text_baseline_pt=render_min_text_baseline_pt,
        source_tailor_session_id=session.session_id,
        created_at=now,
        updated_at=now,
        approved_at=now if status is ApplicationArtifactStatus.APPROVED else None,
    )


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
        project_recommendations=session.project_recommendations,
        selected_project_ids=session.selected_project_ids,
        project_filter_warnings=session.project_filter_warnings,
        diff=session.diff,
        change_history=session.change_history,
        last_result=session.last_result,
    )


def _render_profile_latex_to_pdf(
    profile: CandidateProfile,
    *,
    db_path: Path,
) -> CandidateProfile:
    if not profile.resume_latex_source.strip():
        return profile
    render = render_pdf(profile.resume_latex_source)
    if not render.ok or not render.pdf_bytes:
        return profile
    filename = profile.resume_pdf_filename
    if not filename:
        source_name = profile.resume_filename or "profile_resume.tex"
        filename = f"{Path(source_name).stem}.pdf"
    data_b64 = base64.b64encode(render.pdf_bytes).decode()
    pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
        db_path=db_path,
        profile_id=profile.profile_id,
        name=filename,
        data_b64=data_b64,
    )
    return profile.model_copy(
        update={
            "resume_pdf_filename": filename,
            "resume_pdf_b64": "",
            "resume_pdf_path": pdf_path,
            "resume_pdf_size_bytes": pdf_size,
            "resume_pdf_sha256": pdf_sha,
            "resume_updated_at": utc_now(),
        }
    )


def _apply_plan_overrides(
    questions: list[FormQuestion],
    actions: list[FillAction],
    overrides: dict[str, PlanOverride],
) -> list[FillAction]:
    """Replace skip actions with reviewed one-off answers when provided."""
    if not overrides:
        return actions
    by_id = {question.field_id: question for question in questions}
    updated: list[FillAction] = []
    for action in actions:
        override = overrides.get(action.field_id)
        if override is None or action.action != "skip":
            updated.append(action)
            continue
        question = by_id.get(action.field_id)
        if question is None or question.input_type == "file":
            updated.append(action)
            continue
        value = override.value
        if question.control_kind == "multi_select":
            requested = value if isinstance(value, list) else [
                part.strip()
                for part in re.split(r"[,;\n]+", str(value))
                if part.strip()
            ]
            updated.append(
                FillAction(
                    field_id=action.field_id,
                    action="select_many",
                    value=[_match_option(item, question.options) for item in requested],
                    answer_source=override.answer_source,
                )
            )
        elif question.input_type in {"select", "radio"}:
            updated.append(
                FillAction(
                    field_id=action.field_id,
                    action="select",
                    value=_match_option(str(value), question.options),
                    answer_source=override.answer_source,
                )
            )
        elif question.input_type == "checkbox":
            normalized = str(value).strip().casefold()
            updated.append(
                FillAction(
                    field_id=action.field_id,
                    action="check",
                    value=value if isinstance(value, bool) else normalized in {"yes", "true", "1", "checked"},
                    answer_source=override.answer_source,
                )
            )
        else:
            updated.append(
                FillAction(
                    field_id=action.field_id,
                    action="fill",
                    value=str(value),
                    answer_source=override.answer_source,
                )
            )
    return updated


def _fill_resolution_reason(question: FormQuestion, action: FillAction) -> str:
    if action.resolution_reason:
        return action.resolution_reason
    if question.current_value_present and action.action == "skip":
        return "The existing page value already matches the reviewed answer."
    if action.action != "skip":
        sources = {
            "profile": "Resolved from an explicit profile fact.",
            "custom_answer": "Resolved from a saved reusable answer.",
            "saved_answer": "Resolved from an answer you remembered on an earlier form.",
            "user_input": "Resolved from an application-specific reviewed answer.",
            "generated": "Resolved from a reviewed generated answer.",
            "resume": "Resolved from the saved resume.",
            "eeo_opt_in": "Resolved from voluntary EEO data with autofill enabled.",
        }
        return sources.get(action.answer_source, "Resolved from a reviewed value.")
    intent = classify_question_intent(question)
    if intent == QuestionIntent.NARRATIVE:
        return "A narrative answer can be drafted from verified job and resume context."
    if intent == QuestionIntent.UNKNOWN:
        return "The question was not recognized confidently and was left for review."
    return f"No explicit {intent.value.replace('_', ' ')} fact is saved."


def _build_fill_plan_for_scan(
    app: FastAPI,
    *,
    scan_id: str,
    profile_id: str | None = None,
) -> FillPlanResponse:
    scan = app.state.application_store.get_form_scan(scan_id)
    if scan is None:
        raise HTTPException(404, f"Unknown scan_id: {scan_id}")
    employment_track = "unknown"
    company = ""
    if scan.application_id:
        application = app.state.application_store.get_application(scan.application_id)
        if application:
            job = app.state.application_store.get_job(application.job_id)
            if job:
                employment_track = job.employment_track
                company = job.company
    resolved_profile_id = profile_id or app.state.application_store.get_active_profile_id()
    resolved_actions = resolve_form_questions(
        scan.questions,
        app.state.application_store.get_candidate_profile(resolved_profile_id),
        employment_track=employment_track,
        provider=scan.provider.value,
        company=company,
        application_id=scan.application_id or "",
        saved_answers=app.state.application_store.list_profile_answers(resolved_profile_id),
    )
    actions = [
        FillAction(
            field_id=question.field_id,
            action="skip",
            value=None,
            answer_source="none",
        )
        if question.current_value_present and (
            not scan.replace_existing
            or question.current_value is None
            or action.action == "upload"
            or _fill_values_match(
                question.current_value,
                action.value,
                select_many=action.action == "select_many",
            )
        )
        else action
        for question, action in zip(scan.questions, resolved_actions, strict=True)
    ]
    actions = _apply_plan_overrides(scan.questions, actions, scan.plan_overrides)
    actions = [validate_action_options(question, action) for question, action in zip(scan.questions, actions, strict=True)]
    unresolved = [
        question.label
        for question, action in zip(scan.questions, actions, strict=True)
        if question.required and not question.current_value_present and action.action == "skip"
    ]
    ready_action_count = sum(1 for action in actions if action.action != "skip")
    review_items = [
        FillReviewItem(
            field_id=question.field_id,
            label=question.label,
            status="ready" if question.current_value_present or action.action != "skip" else "skipped",
            required=question.required,
            answer_source=(
                "already_on_page"
                if question.current_value_present and action.action == "skip"
                else action.answer_source
            ),
            value_preview=(
                "Already filled"
                if question.current_value_present and action.action == "skip"
                else _preview_fill_value(action.value)
            ),
            change_kind=(
                "keep"
                if question.current_value_present and action.action == "skip"
                else "replace"
                if question.current_value_present and action.action != "skip"
                else "fill"
                if action.action != "skip"
                else "unresolved"
            ),
            current_value_preview=_preview_fill_value(question.current_value),
            planned_value_preview=_preview_fill_value(action.value),
            question_intent=classify_question_intent(question),
            draft_eligible=is_question_draft_eligible(question),
            proposal_eligible=question.required and is_question_proposal_eligible(question, action),
            resolution_reason=_fill_resolution_reason(question, action),
        )
        for question, action in zip(scan.questions, actions, strict=True)
    ]
    if scan.application_id:
        try:
            app.state.application_store.update_application(
                scan.application_id,
                {"missing_answers_count": len(unresolved)},
            )
        except KeyError:
            pass
    return FillPlanResponse(
        scan_id=scan.scan_id,
        page_url=scan.page_url,
        actions=actions,
        review_items=review_items,
        unresolved_required=unresolved,
        ready_action_count=ready_action_count,
        can_fill=ready_action_count > 0,
    )


def create_app(
    *,
    job_search_service: JobSearchService | None = None,
    application_store: ApplicationStore | None = None,
) -> FastAPI:
    from latex_resume.routers import (
        applications,
        apply_runs,
        auth,
        extension,
        jobs,
        latex,
        profiles,
        tailor,
        watchlist,
    )
    from latex_resume.watchlist import WatchlistIngestor

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
    app.state.auth_store = LocalAuthStore(app.state.application_store)
    # Test doubles may stub the search service without exposing a board client.
    app.state.watchlist_ingestor = WatchlistIngestor(
        app.state.application_store,
        board_client=getattr(app.state.job_search_service, "board_client", None),
    )
    tailor_store.bind(app.state.application_store)
    install_auth_middleware(app, app.state.auth_store)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

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
        allow_origin_regex=r"chrome-extension://[a-p]{32}",
        allow_methods=["*"],
        allow_headers=["*"],
        allow_private_network=True,
    )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Sub-routers
    # ------------------------------------------------------------------

    app.include_router(auth.router)
    app.include_router(jobs.router)
    app.include_router(profiles.router)
    app.include_router(applications.router)
    app.include_router(extension.router)
    app.include_router(latex.router)
    app.include_router(tailor.router)
    app.include_router(watchlist.router)
    app.include_router(apply_runs.router)

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
    from latex_resume.logging_config import configure_logging

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    log_level = os.environ.get("LOG_LEVEL", "info").upper()

    configure_logging(level=log_level)

    uvicorn.run(
        "latex_resume.api:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
    )


if __name__ == "__main__":
    run()
