"""Request and response schemas for the HTTP API.

Routers import these by name. They were extracted from ``api.py`` so the app
factory no longer owns the wire contract; ``api.py`` re-exports them for
compatibility.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from latex_resume.job_models import (
    AddressProfile,
    AnswerProposal,
    ApplicationArtifactStatus,
    ApplicationFactsProfile,
    ApplicationRecord,
    ApplicationStage,
    ApplicationStatus,
    EducationProfile,
    EqualOpportunityProfile,
    FillAction,
    FormQuestion,
    JobPosting,
    JobProvider,
    JobSearchQuery,
    JobSourceConfig,
    ProjectRecommendation,
    ProjectRecord,
    QuestionIntent,
    SavedAnswer,
    SearchPreferences,
    WorkAuthorizationProfile,
    WorkExperienceProfile,
)
from latex_resume.local_auth import (
    auth_required,
)
from latex_resume.optimizer import (
    DEFAULT_OPTIMIZER_STRATEGY,
    OptimizerStrategy,
    ReviewerBackend,
)

__all__ = [
    "OptimizeRequest",
    "RerenderRequest",
    "UploadResponse",
    "OptimizeResponse",
    "RerenderResponse",
    "StatusResponse",
    "JobSearchRequest",
    "CreateApplicationRequest",
    "ScoreApplicationRequest",
    "ApplicationsHealthResponse",
    "ApplicationScoreResponse",
    "TransitionApplicationRequest",
    "PatchApplicationRequest",
    "CreateApplicationEventRequest",
    "CreateApplicationTaskRequest",
    "UpdateArtifactStatusRequest",
    "FormScanRequest",
    "FillReviewItem",
    "FillPlanResponse",
    "FillPlanOverrideRequest",
    "AnswerProposalRequest",
    "AnswerProposalResponse",
    "AnswerUsageRequest",
    "AnswerUsageResponse",
    "SavedAnswerUpsertRequest",
    "SavedAnswerListResponse",
    "ApplicationAnswerDraftRequest",
    "AuthLoginRequest",
    "AuthLoginResponse",
    "AuthStatusResponse",
    "ProfileSetupQuestion",
    "ProfileSetupResponse",
    "ProfileResumeInfo",
    "ProfileResumeUploadResponse",
    "ProfileView",
    "ProfilePatch",
    "ActiveProfileResponse",
    "ProfileListItem",
    "ProfileListResponse",
    "PreparedResumeResponse",
    "PrepareResumeRequest",
    "ResumeCustomizationPreviewRequest",
    "ResumeCustomizationPreviewResponse",
    "SetActiveProfileRequest",
    "AnalyzeRequest",
    "AnalyzeResponse",
    "RefineRequest",
    "ReportResponse",
    "CreateTailorSessionRequest",
    "TailorSessionResponse",
    "ProjectSyncResponse",
    "ProjectRankResponse",
    "UpdateTailorProjectsRequest",
    "UpdateTailorSessionRequest",
    "TailorOptimizeRequest",
    "TailorRefineRequest",
    "ApproveTailorSessionRequest",
]

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
