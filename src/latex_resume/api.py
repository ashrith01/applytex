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
        if question is None:
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
    if question.current_value_present and action.action == "skip":
        return "The existing page value already matches the reviewed answer."
    if action.action != "skip":
        sources = {
            "profile": "Resolved from an explicit profile fact.",
            "custom_answer": "Resolved from a saved reusable answer.",
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
    resolved_actions = resolve_form_questions(
        scan.questions,
        app.state.application_store.get_candidate_profile(
            profile_id or app.state.application_store.get_active_profile_id()
        ),
        employment_track=employment_track,
        provider=scan.provider.value,
        company=company,
        application_id=scan.application_id or "",
    )
    actions = [
        FillAction(
            field_id=question.field_id,
            action="skip",
            value=None,
            answer_source="none",
        )
        if question.current_value_present and (
            question.current_value is None
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
    tailor_store.bind(app.state.application_store)
    install_auth_middleware(app, app.state.auth_store)

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
        return app.state.application_store.get_active_profile_id()

    def _require_application_for_profile(
        application_id: str,
        profile_id: str,
    ):
        """Return application owned by profile_id, or 404 (no cross-profile leak)."""
        application = app.state.application_store.get_application(application_id)
        if application is None or application.profile_id != profile_id:
            raise HTTPException(404, f"Application '{application_id}' not found.")
        return application

    def _require_application_detail_for_profile(application_id: str, profile_id: str):
        _require_application_for_profile(application_id, profile_id)
        detail = app.state.application_store.get_application_detail(application_id)
        if detail is None:
            raise HTTPException(404, f"Application '{application_id}' not found.")
        return detail

    def _require_form_scan_for_profile(scan_id: str, profile_id: str):
        scan = app.state.application_store.get_form_scan(scan_id)
        if scan is None:
            raise HTTPException(404, f"Unknown scan_id: {scan_id}")
        if scan.application_id:
            _require_application_for_profile(scan.application_id, profile_id)
        return scan

    def _require_artifact_for_profile(artifact_id: str, profile_id: str):
        artifact = app.state.application_store.get_application_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
        _require_application_for_profile(artifact.application_id, profile_id)
        return artifact

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
        return {"status": "ok", "sessions": str(store.count)}

    @app.get("/auth/status", response_model=AuthStatusResponse)
    async def auth_status(
        request: Request,
        profile_id: str | None = None,
    ) -> AuthStatusResponse:
        """Report whether auth is required and whether this request is authenticated."""
        bound = getattr(request.state, "auth_profile_id", None)
        resolved = profile_id or bound or app.state.application_store.get_active_profile_id()
        return AuthStatusResponse(
            auth_required=auth_required(),
            authenticated=bool(bound),
            profile_id=str(bound) if bound else None,
            has_password=app.state.auth_store.has_password(resolved),
        )

    @app.post("/auth/login", response_model=AuthLoginResponse)
    async def auth_login(body: AuthLoginRequest) -> AuthLoginResponse:
        """Create or verify a local password and issue a bearer token."""
        profile = app.state.application_store.get_candidate_profile(body.profile_id)
        auth_store: LocalAuthStore = app.state.auth_store
        if body.set_password or not auth_store.has_password(profile.profile_id):
            try:
                auth_store.set_password(profile.profile_id, body.password)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        elif not auth_store.verify_password(profile.profile_id, body.password):
            raise HTTPException(401, "Invalid profile password.")
        session = auth_store.issue_token(profile.profile_id)
        app.state.application_store.set_active_profile_id(profile.profile_id)
        return AuthLoginResponse(
            access_token=session.token,
            profile_id=profile.profile_id,
            auth_required=auth_required(),
        )

    # ------------------------------------------------------------------
    # Public job discovery and application tracking
    # ------------------------------------------------------------------

    @app.post("/jobs/search", response_model=JobSearchResult)
    async def search_jobs(
        request: Request,
        body: JobSearchRequest,
        x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    ) -> JobSearchResult:
        """Search configured public ATS boards without browser automation."""
        profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
        profile = app.state.application_store.get_candidate_profile(profile_id)
        preferences = profile.search_preferences if body.use_saved_preferences else None
        result = await app.state.job_search_service.search(
            body.query,
            body.sources,
            preferences,
        )
        stamped_jobs = [
            job.model_copy(update={"captured_for_profile_id": profile_id})
            for job in result.jobs
        ]
        result = result.model_copy(update={"jobs": stamped_jobs})
        app.state.application_store.save_search(result)
        return result

    @app.get("/jobs", response_model=list[JobPosting])
    async def list_jobs(
        request: Request,
        limit: int = 100,
        x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
        profile_id: str | None = None,
    ) -> list[JobPosting]:
        """List normalized jobs saved from previous searches."""
        bounded_limit = min(max(limit, 1), 200)
        scoped_profile_id = resolve_request_profile_id(
            request=request,
            x_profile_id=x_profile_id,
            profile_id=profile_id,
        )
        return app.state.application_store.list_jobs(
            bounded_limit,
            profile_id=scoped_profile_id,
        )

    @app.get("/jobs/{job_id}", response_model=JobPosting)
    async def get_job(job_id: str) -> JobPosting:
        """Return one saved job by ID."""
        job = app.state.application_store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"Job '{job_id}' not found.")
        return job

    @app.post("/extension/jobs/capture", response_model=JobPosting)
    async def capture_browser_job(
        request: Request,
        body: BrowserJobCapture,
        x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    ) -> JobPosting:
        """Persist a job captured from a user-visible LinkedIn or ATS tab."""
        if not body.source_url.startswith("https://") or not body.apply_url.startswith("https://"):
            raise HTTPException(400, "Captured job URLs must use HTTPS.")
        profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
        job = captured_job_to_posting(body).model_copy(
            update={"captured_for_profile_id": profile_id}
        )
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
    async def get_active_profile(
        request: Request,
        x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    ) -> ActiveProfileResponse:
        """Return the profile selected in the local web UI."""
        profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
        profile = app.state.application_store.get_candidate_profile(profile_id)
        return ActiveProfileResponse(
            profile_id=profile.profile_id,
            full_name=profile.full_name,
            email=profile.email,
            resume_filename=profile.resume_pdf_filename or profile.resume_filename,
            has_pdf=_profile_has_pdf(profile),
            has_latex_source=bool(profile.resume_latex_source),
        )

    @app.get("/profiles", response_model=ProfileListResponse)
    async def list_profiles() -> ProfileListResponse:
        """List local profiles so the extension/web UI can pick an existing account."""
        items: list[ProfileListItem] = []
        for profile in app.state.application_store.list_candidate_profiles():
            has_pdf = _profile_has_pdf(profile)
            # Name-only smoke shells (e.g. "Smoke Tester") are not sign-in targets.
            usable = bool(
                has_pdf
                or profile.resume_latex_source.strip()
                or (profile.full_name.strip() and profile.email.strip())
            )
            items.append(
                ProfileListItem(
                    profile_id=profile.profile_id,
                    full_name=profile.full_name,
                    email=profile.email,
                    has_pdf=has_pdf,
                    has_latex_source=bool(profile.resume_latex_source.strip()),
                    usable=usable,
                )
            )
        return ProfileListResponse(profiles=items)

    @app.put("/profile/active", response_model=ActiveProfileResponse)
    async def set_active_profile(
        request: Request,
        body: SetActiveProfileRequest,
        x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    ) -> ActiveProfileResponse:
        """Select which local profile the UI and extension should use."""
        if auth_required():
            bound = resolve_request_profile_id(
                request=request,
                x_profile_id=x_profile_id,
                profile_id=body.profile_id,
            )
            profile_id = app.state.application_store.set_active_profile_id(bound)
        else:
            profile_id = app.state.application_store.set_active_profile_id(body.profile_id)
        profile = app.state.application_store.get_candidate_profile(profile_id)
        return ActiveProfileResponse(
            profile_id=profile.profile_id,
            full_name=profile.full_name,
            email=profile.email,
            resume_filename=profile.resume_pdf_filename or profile.resume_filename,
            has_pdf=_profile_has_pdf(profile),
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

    @app.get("/profile/projects", response_model=list[ProjectRecord])
    async def list_profile_projects(profile_id: str | None = None) -> list[ProjectRecord]:
        """Return cached project evidence for the active profile."""
        resolved_profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(resolved_profile_id)
        if profile.resume_latex_source.strip():
            _refresh_resume_projects(
                app,
                profile_id=resolved_profile_id,
                latex_source=profile.resume_latex_source,
            )
        return app.state.application_store.list_profile_projects(resolved_profile_id)

    @app.post("/profile/projects/sync/github", response_model=ProjectSyncResponse)
    async def sync_profile_github_projects(profile_id: str | None = None) -> ProjectSyncResponse:
        """Fetch public non-fork GitHub repositories into the local project library."""
        resolved_profile_id = profile_id or app.state.application_store.get_active_profile_id()
        profile = app.state.application_store.get_candidate_profile(resolved_profile_id)
        if not profile.github_url.strip():
            raise HTTPException(409, "Add a GitHub profile URL before syncing public projects.")
        try:
            projects = await GitHubProjectClient().fetch_public_projects(
                profile_id=resolved_profile_id,
                github_url=profile.github_url,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise HTTPException(
                    429,
                    "GitHub rate limited this public sync. Try again later or continue with resume projects.",
                ) from exc
            raise HTTPException(
                502,
                f"GitHub project sync failed with HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                "GitHub project sync failed. Continue with resume projects and try again later.",
            ) from exc
        saved = app.state.application_store.replace_profile_projects(
            resolved_profile_id,
            ProjectSource.GITHUB,
            projects,
        )
        warnings = [] if saved else ["No public non-fork GitHub repositories were found."]
        return ProjectSyncResponse(projects=saved, warnings=warnings)

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
            data_b64 = base64.b64encode(raw).decode()
            pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
                db_path=app.state.application_store.path,
                profile_id=profile_id,
                name=file.filename,
                data_b64=data_b64,
            )
            updates.update(
                {
                    "resume_pdf_filename": file.filename,
                    "resume_pdf_b64": "",
                    "resume_pdf_path": pdf_path,
                    "resume_pdf_size_bytes": pdf_size,
                    "resume_pdf_sha256": pdf_sha,
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
                pdf_filename = f"{Path(file.filename).stem}.pdf"
                data_b64 = base64.b64encode(render.pdf_bytes).decode()
                pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
                    db_path=app.state.application_store.path,
                    profile_id=profile_id,
                    name=pdf_filename,
                    data_b64=data_b64,
                )
                updates.update(
                    {
                        "resume_pdf_filename": pdf_filename,
                        "resume_pdf_b64": "",
                        "resume_pdf_path": pdf_path,
                        "resume_pdf_size_bytes": pdf_size,
                        "resume_pdf_sha256": pdf_sha,
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
            artifact = _require_artifact_for_profile(body.artifact_id, scoped_profile_id)
            return _prepared_response_from_artifact(
                artifact,
                db_path=app.state.application_store.path,
            )

        if body.application_id and body.prefer_approved_artifact:
            _require_application_for_profile(body.application_id, scoped_profile_id)
            artifact = app.state.application_store.get_latest_application_artifact(
                body.application_id,
                artifact_type=ApplicationArtifactType.TAILORED_RESUME,
                status=ApplicationArtifactStatus.APPROVED,
            )
            if artifact is not None:
                return _prepared_response_from_artifact(
                    artifact,
                    db_path=app.state.application_store.path,
                    warnings=["Using the approved tailored resume from the web review."],
                )

        profile_id = scoped_profile_id
        profile = app.state.application_store.get_candidate_profile(profile_id)
        profile = _render_profile_latex_to_pdf(
            profile,
            db_path=app.state.application_store.path,
        )
        app.state.application_store.save_candidate_profile(profile)

        warnings: list[str] = []
        db_path = app.state.application_store.path
        if not body.customize:
            return _profile_pdf_response(profile, db_path=db_path)
        if not body.job_description.strip():
            warnings.append("No captured job description was available; using the saved profile resume.")
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
            return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)

        if opt.overflow or not opt.pdf_bytes:
            warnings.extend(opt.warnings)
            warnings.append("Customized resume did not pass the one-page render gate; using saved profile resume.")
            return _profile_pdf_response(profile, db_path=db_path, warnings=warnings)

        filename = f"{Path(profile.resume_filename or 'resume').stem}_customized.pdf"
        artifact_id: str | None = None
        artifact_status: ApplicationArtifactStatus | None = None
        if body.application_id:
            application = _require_application_for_profile(body.application_id, profile_id)
            artifact = ApplicationArtifact(
                artifact_id=str(uuid.uuid4()),
                application_id=body.application_id,
                job_id=application.job_id,
                profile_id=profile_id,
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
                saved_artifact = app.state.application_store.save_application_artifact(artifact)
                app.state.application_store.create_application_event(
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
            _require_application_for_profile(body.application_id, scoped_profile_id)
        scan = FormScan(
            scan_id=str(uuid.uuid4()),
            application_id=body.application_id,
            provider=body.provider,
            page_url=body.page_url,
            page_title=body.page_title,
            step_key=body.step_key,
            form_signature=body.form_signature,
            questions=body.questions,
        )
        try:
            return app.state.application_store.save_form_scan(scan)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/extension/forms/{scan_id}/plan", response_model=FillPlanResponse)
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
        _require_form_scan_for_profile(scan_id, scoped_profile_id)
        return _build_fill_plan_for_scan(
            app,
            scan_id=scan_id,
            profile_id=scoped_profile_id,
        )

    @app.post("/extension/forms/{scan_id}/plan", response_model=FillPlanResponse)
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
        scan = _require_form_scan_for_profile(scan_id, scoped_profile_id)
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
        app.state.application_store.save_form_scan(updated)
        return _build_fill_plan_for_scan(
            app,
            scan_id=scan_id,
            profile_id=scoped_profile_id,
        )

    @app.post(
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
        scan = _require_form_scan_for_profile(scan_id, scoped_profile_id)
        question = next(
            (item for item in scan.questions if item.field_id == body.field_id),
            None,
        )
        if question is None:
            raise HTTPException(404, f"Unknown field_id: {body.field_id}")
        if not is_question_draft_eligible(question):
            raise HTTPException(409, "AI drafts are available only for narrative application questions.")
        profile = app.state.application_store.get_candidate_profile(scoped_profile_id)
        try:
            return await generate_application_answer(
                app.state.application_store,
                scan=scan,
                question=question,
                profile=profile,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            logger.exception("Application answer generation failed for scan %s", scan_id)
            raise HTTPException(502, str(exc)) from exc

    @app.post("/applications", response_model=ApplicationRecord)
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
            record = app.state.application_store.get_or_create_application(
                job_id=body.job_id,
                profile_id=profile_id,
                resume_session_id=body.resume_session_id,
                notes=body.notes,
                force_new=body.force_new,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        try:
            scored = await _score_application_for_profile(app, record.application_id, profile_id)
            return scored.application
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return record

    @app.get("/applications", response_model=list[ApplicationRecord])
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
        return app.state.application_store.list_applications(
            bounded_limit,
            profile_id=scoped_profile_id,
        )

    @app.get("/applications/health", response_model=ApplicationsHealthResponse)
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
        merged_now = app.state.application_store.dedupe_applications()
        applications = app.state.application_store.list_applications(
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
            active=sum(1 for application in applications if application.status not in inactive_statuses),
            duplicates_merged=merged_now or app.state.application_store.get_last_dedupe_count(),
            average_current_resume_score=average_score,
            missing_answers=sum(application.missing_answers_count for application in applications),
            captured_jobs=app.state.application_store.count_jobs(profile_id=scoped_profile_id),
            profile_id=scoped_profile_id,
        )

    @app.get("/applications/{application_id}", response_model=ApplicationDetail)
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
        return _require_application_detail_for_profile(application_id, scoped_profile_id)

    @app.patch("/applications/{application_id}", response_model=ApplicationRecord)
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
        _require_application_for_profile(application_id, scoped_profile_id)
        updates = body.model_dump(exclude_unset=True)
        try:
            record = app.state.application_store.update_application(application_id, updates)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return record

    @app.post("/applications/{application_id}/score", response_model=ApplicationScoreResponse)
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
        _require_application_for_profile(application_id, scoped_profile_id)
        return await _score_application_for_profile(
            app,
            application_id,
            scoped_profile_id,
        )

    @app.post("/applications/{application_id}/events", response_model=ApplicationEvent)
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
        _require_application_for_profile(application_id, scoped_profile_id)
        try:
            return app.state.application_store.create_application_event(
                application_id=application_id,
                kind=body.kind,
                label=body.label,
                detail=body.detail,
                payload=body.payload,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/applications/{application_id}/tasks", response_model=ApplicationTask)
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
        _require_application_for_profile(application_id, scoped_profile_id)
        try:
            return app.state.application_store.create_application_task(
                application_id=application_id,
                title=body.title,
                category=body.category,
                due_at=body.due_at,
                notes=body.notes,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get(
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
        _require_application_for_profile(application_id, scoped_profile_id)
        artifact = app.state.application_store.get_latest_application_artifact(
            application_id,
            artifact_type=type,
            status=status,
        )
        if artifact is None:
            raise HTTPException(404, "No matching application artifact found.")
        return artifact

    @app.post(
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
        _require_application_for_profile(application_id, scoped_profile_id)
        artifact = app.state.application_store.get_application_artifact(artifact_id)
        if artifact is None or artifact.application_id != application_id:
            raise HTTPException(404, f"Artifact '{artifact_id}' not found.")
        try:
            return app.state.application_store.update_application_artifact_status(
                artifact_id,
                body.status,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post(
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
        _require_application_for_profile(application_id, scoped_profile_id)
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
        await _rank_projects_for_session(app, session, reset_default=True)
        tailor_store.save(session)
        if body.application_id:
            try:
                await _score_application_for_profile(app, body.application_id, profile_id)
            except HTTPException as exc:
                if exc.status_code not in {404, 409}:
                    raise
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
            session.source_latex = body.current_latex
            session.current_latex = body.current_latex
            session.diff = []
            session.last_result = None
            await _rank_projects_for_session(app, session, reset_default=True)
        tailor_store.save(session)
        return await _build_tailor_session_response(app, session)

    @app.post("/tailor/sessions/{session_id}/projects/rank", response_model=ProjectRankResponse)
    async def rank_tailor_projects(session_id: str) -> ProjectRankResponse:
        """Rank resume and GitHub projects against this session's job."""
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        ranked = await _rank_projects_for_session(
            app,
            session,
            reset_default=not session.selected_project_ids,
        )
        tailor_store.save(session)
        return ranked

    @app.patch("/tailor/sessions/{session_id}/projects", response_model=ProjectRankResponse)
    async def update_tailor_projects(
        session_id: str,
        body: UpdateTailorProjectsRequest,
    ) -> ProjectRankResponse:
        """Persist user-approved resume projects for the tailored PDF."""
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        if not session.project_recommendations:
            await _rank_projects_for_session(app, session, reset_default=True)
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
        ranked = await _rank_projects_for_session(app, session, reset_default=False)
        tailor_store.save(session)
        return ranked

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
        if not session.project_recommendations:
            await _rank_projects_for_session(app, session, reset_default=True)
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
        async with latex_session.lock:
            latex_session.parse_result = filtered_parse_result
            latex_session.latex_source = filtered_latex
            opt = await run_optimization_pipeline(
                parse_result=filtered_parse_result,
                job_description=job.description,
                confirmed_skills=session.confirmed_skills,
                allowed_stmt_ids=allowed_stmt_ids,
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
        tailor_store.save(session)
        return await _build_tailor_session_response(app, session)

    @app.post(
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
        session = tailor_store.get(session_id)
        if session is None:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        if session.profile_id and session.profile_id != scoped_profile_id:
            raise HTTPException(404, f"Tailor session '{session_id}' not found.")
        application_id = body.application_id or session.application_id
        if not application_id:
            raise HTTPException(409, "Attach this tailor session to an application before approval.")
        application = _require_application_for_profile(application_id, scoped_profile_id)
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

        job = app.state.application_store.get_job(session.job_id)
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
            db_path=app.state.application_store.path,
        )
        try:
            saved = app.state.application_store.save_application_artifact(artifact)
            app.state.application_store.create_application_event(
                application_id=application_id,
                kind="resume_approved",
                label="Tailored resume approved",
                detail="Approved in the local web tailoring studio.",
                payload={"artifact_id": saved.artifact_id, "session_id": session_id},
            )
            record = app.state.application_store.get_application(application_id)
            if record and record.status in {
                ApplicationStatus.DISCOVERED,
                ApplicationStatus.SCORED,
                ApplicationStatus.SELECTED,
            }:
                if record.status is ApplicationStatus.DISCOVERED:
                    record = app.state.application_store.transition_application(
                        application_id,
                        ApplicationStatus.SELECTED,
                    )
                if record.status is ApplicationStatus.SCORED:
                    record = app.state.application_store.transition_application(
                        application_id,
                        ApplicationStatus.SELECTED,
                    )
                if record.status is ApplicationStatus.SELECTED:
                    app.state.application_store.transition_application(
                        application_id,
                        ApplicationStatus.RESUME_READY,
                    )
            else:
                app.state.application_store.update_application(
                    application_id,
                    {"stage": ApplicationStage.TAILORING},
                )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return saved

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
