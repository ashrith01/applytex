"""Public job-search and controlled application workflow models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class JobProvider(str, Enum):
    """Supported public ATS job-board providers."""

    LINKEDIN = "linkedin"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    ICIMS = "icims"
    SMARTRECRUITERS = "smartrecruiters"
    WORKABLE = "workable"
    INDEED = "indeed"
    ZIPRECRUITER = "ziprecruiter"
    GLASSDOOR = "glassdoor"
    WELLFOUND = "wellfound"
    DICE = "dice"


PUBLIC_BOARD_PROVIDERS: frozenset[JobProvider] = frozenset(
    {JobProvider.GREENHOUSE, JobProvider.LEVER, JobProvider.ASHBY}
)


def validate_board_token(value: str) -> str:
    """Reject tokens that could alter a provider URL."""
    cleaned = value.strip()
    if not cleaned.replace("-", "").replace("_", "").isalnum():
        raise ValueError("board_token may contain only letters, numbers, hyphens, and underscores")
    return cleaned


def require_public_board_provider(value: JobProvider) -> JobProvider:
    """Keep providers without public board adapters in the user-visible extension."""
    if value not in PUBLIC_BOARD_PROVIDERS:
        raise ValueError(f"{value.value} jobs must be captured through the Chrome extension")
    return value


class JobSourceConfig(BaseModel):
    """Configuration for one public employer job board."""

    provider: JobProvider
    board_token: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)

    @field_validator("board_token")
    @classmethod
    def validate_board_token(cls, value: str) -> str:
        return validate_board_token(value)

    @field_validator("provider")
    @classmethod
    def reject_browser_only_provider(cls, value: JobProvider) -> JobProvider:
        return require_public_board_provider(value)


class TargetRole(str, Enum):
    """Job families selected for the ApplyTeX ATS MVP."""

    AI_INTERN = "ai_intern"
    ML_INTERN = "ml_intern"
    NLP_INTERN = "nlp_intern"
    AGENTIC_AI_INTERN = "agentic_ai_intern"
    DATA_SCIENCE_INTERN = "data_science_intern"
    AI_ENGINEER = "ai_engineer"
    ML_ENGINEER = "ml_engineer"
    DATA_SCIENTIST = "data_scientist"


DEFAULT_TARGET_ROLES: tuple[TargetRole, ...] = tuple(TargetRole)
DEFAULT_PREFERRED_LOCATIONS: tuple[str, ...] = (
    "Remote - US",
)


class SearchPreferences(BaseModel):
    """Persisted role and geography preferences."""

    target_roles: list[TargetRole] = Field(
        default_factory=lambda: list(DEFAULT_TARGET_ROLES)
    )
    preferred_locations: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PREFERRED_LOCATIONS)
    )
    allow_remote_us: bool = True
    allow_hybrid: bool = True
    allow_onsite: bool = True
    willing_to_relocate: bool | None = None
    accepted_employment_types: list[Literal["internship", "full_time"]] = Field(
        default_factory=lambda: ["internship", "full_time"]
    )
    prioritize_internships: bool = True
    excluded_title_terms: list[str] = Field(default_factory=list)


class JobSearchQuery(BaseModel):
    """User-owned search filters applied after provider retrieval."""

    text: str = Field(default="", max_length=300)
    role_keywords: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=20)
    remote_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    target_roles: list[TargetRole] = Field(default_factory=list)


class JobPosting(BaseModel):
    """Normalized job posting returned by any supported source."""

    job_id: str
    provider: JobProvider
    board_token: str
    external_id: str
    company: str
    title: str
    description: str
    location: str = ""
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    source_url: str
    apply_url: str
    workflow_key: str = ""
    canonical_url: str = ""
    description_source: str = ""
    capture_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    published_at: str | None = None
    retrieved_at: str = Field(default_factory=utc_now)
    industry: str | None = None
    target_role: TargetRole | None = None
    employment_track: Literal["internship", "full_time", "unknown"] = "unknown"
    search_score: float = Field(default=0.0, ge=0.0)
    captured_for_profile_id: str | None = None
    # Watchlist feed fields. ``first_seen_at`` survives re-ingestion so "new
    # since" queries stay stable; ``fit_score`` is the deterministic ATS fit
    # against the profile resume at ingestion time.
    first_seen_at: str | None = None
    fit_score: float | None = Field(default=None, ge=0.0, le=100.0)
    watchlist_entry_id: str | None = None
    domain_tags: list[str] = Field(default_factory=list)


class SourceSearchError(BaseModel):
    """A source failure retained without discarding successful sources."""

    provider: JobProvider
    board_token: str
    message: str


class JobSearchResult(BaseModel):
    """One deterministic multi-source search result."""

    search_id: str
    query: JobSearchQuery
    sources: list[JobSourceConfig]
    jobs: list[JobPosting]
    errors: list[SourceSearchError] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class WatchlistEntry(BaseModel):
    """One employer board a profile follows for scheduled discovery."""

    entry_id: str
    profile_id: str = "default"
    provider: JobProvider
    board_token: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=160)
    domain_tags: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    last_checked_at: str | None = None
    last_error: str = ""
    last_job_count: int = Field(default=0, ge=0)
    last_matched_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("board_token")
    @classmethod
    def _validate_board_token(cls, value: str) -> str:
        return validate_board_token(value)

    @field_validator("provider")
    @classmethod
    def _require_public_provider(cls, value: JobProvider) -> JobProvider:
        return require_public_board_provider(value)

    @field_validator("domain_tags", mode="before")
    @classmethod
    def _clean_tags(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(str(tag).strip().casefold() for tag in value if str(tag).strip()))

    def to_source(self) -> JobSourceConfig:
        return JobSourceConfig(
            provider=self.provider,
            board_token=self.board_token,
            company=self.company,
            industry=self.domain_tags[0] if self.domain_tags else None,
        )


class IngestionRun(BaseModel):
    """One watchlist refresh: what was fetched, matched, and newly seen."""

    run_id: str
    profile_id: str = "default"
    trigger: Literal["scheduled", "manual", "cli"] = "manual"
    started_at: str = Field(default_factory=utc_now)
    finished_at: str | None = None
    source_count: int = 0
    fetched_jobs: int = 0
    matched_jobs: int = 0
    new_jobs: int = 0
    updated_jobs: int = 0
    errors: list[SourceSearchError] = Field(default_factory=list)


class ApplicationStatus(str, Enum):
    """Durable workflow states for a human-approved application."""

    DISCOVERED = "discovered"
    SCORED = "scored"
    SELECTED = "selected"
    RESUME_READY = "resume_ready"
    FORM_SCANNED = "form_scanned"
    NEEDS_INPUT = "needs_input"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class ApplicationStage(str, Enum):
    """Human-facing tracker buckets shown in the local web app."""

    SAVED = "saved"
    SELECTED = "selected"
    TAILORING = "tailoring"
    FORM_REVIEW = "form_review"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ApplicationArtifactType(str, Enum):
    """Durable files generated or approved for an application."""

    TAILORED_RESUME = "tailored_resume"
    COVER_LETTER = "cover_letter"


class ApplicationArtifactStatus(str, Enum):
    """Review state for an application artifact."""

    DRAFT = "draft"
    GENERATED = "generated"
    APPROVED = "approved"
    UPLOADED = "uploaded"


TERMINAL_APPLICATION_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.BLOCKED,
        ApplicationStatus.FAILED,
        ApplicationStatus.SKIPPED,
    }
)


ALLOWED_APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DISCOVERED: frozenset(
        {ApplicationStatus.SCORED, ApplicationStatus.SELECTED, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.SCORED: frozenset(
        {ApplicationStatus.SELECTED, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.SELECTED: frozenset(
        {ApplicationStatus.RESUME_READY, ApplicationStatus.BLOCKED, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.RESUME_READY: frozenset(
        {ApplicationStatus.FORM_SCANNED, ApplicationStatus.BLOCKED, ApplicationStatus.FAILED}
    ),
    ApplicationStatus.FORM_SCANNED: frozenset(
        {
            ApplicationStatus.NEEDS_INPUT,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.BLOCKED,
            ApplicationStatus.FAILED,
        }
    ),
    ApplicationStatus.NEEDS_INPUT: frozenset(
        {ApplicationStatus.READY_FOR_REVIEW, ApplicationStatus.BLOCKED, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.READY_FOR_REVIEW: frozenset(
        {ApplicationStatus.APPROVED, ApplicationStatus.NEEDS_INPUT, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.APPROVED: frozenset(
        {ApplicationStatus.SUBMITTING, ApplicationStatus.SKIPPED}
    ),
    ApplicationStatus.SUBMITTING: frozenset(
        {ApplicationStatus.SUBMITTED, ApplicationStatus.BLOCKED, ApplicationStatus.FAILED}
    ),
}


class ApplicationRecord(BaseModel):
    """Persisted state for one job application."""

    application_id: str
    profile_id: str = "default"
    job_id: str
    status: ApplicationStatus = ApplicationStatus.DISCOVERED
    stage: ApplicationStage = ApplicationStage.SAVED
    job_title: str = ""
    company: str = ""
    provider: JobProvider | None = None
    location: str = ""
    workplace_type: str = ""
    salary_range: str = ""
    apply_url: str = ""
    source_url: str = ""
    resume_session_id: str | None = None
    latest_resume_artifact_id: str | None = None
    cover_letter_artifact_id: str | None = None
    fit_score: float | None = None
    current_resume_score: float | None = None
    tailored_resume_score: float | None = None
    required_missing: list[str] = Field(default_factory=list)
    preferred_missing: list[str] = Field(default_factory=list)
    keyword_misses: list[str] = Field(default_factory=list)
    score_updated_at: str | None = None
    missing_answers_count: int = Field(default=0, ge=0)
    priority: Literal["low", "medium", "high"] = "medium"
    excitement: int = Field(default=3, ge=1, le=5)
    deadline: str | None = None
    next_action_at: str | None = None
    notes: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    last_activity_at: str = Field(default_factory=utc_now)
    approved_at: str | None = None
    applied_at: str | None = None
    submitted_at: str | None = None
    submission_bundle_id: str | None = None


class SubmissionField(BaseModel):
    """One form field as it stood when the submission was confirmed."""

    field_id: str
    label: str
    step_key: str = ""
    required: bool = False
    input_type: str = ""
    value: str | bool | list[str] | None = None
    source: str = ""


class SubmissionStep(BaseModel):
    scan_id: str
    step_key: str = ""
    page_url: str = ""
    captured_at: str = ""
    field_count: int = 0


class SubmissionBundle(BaseModel):
    """Immutable receipt: exactly what was on the forms and which files were attached.

    Written once when the user confirms a submission. Later profile, resume,
    or answer edits never change it.
    """

    bundle_id: str
    application_id: str
    profile_id: str = "default"
    job_id: str
    job_title: str = ""
    company: str = ""
    provider: str = ""
    apply_url: str = ""
    source_url: str = ""
    job_description_sha256: str = ""
    resume_artifact_id: str | None = None
    resume_filename: str = ""
    resume_sha256: str = ""
    resume_origin: Literal["tailored_artifact", "profile_resume", "none"] = "none"
    cover_letter_artifact_id: str | None = None
    cover_letter_filename: str = ""
    cover_letter_sha256: str = ""
    fields: list[SubmissionField] = Field(default_factory=list)
    steps: list[SubmissionStep] = Field(default_factory=list)
    confirmed_by: Literal["user", "detected_confirmed"] = "user"
    detection_evidence: str = ""
    notes: str = ""
    created_at: str = Field(default_factory=utc_now)


ApplyRunStatus = Literal[
    "queued",
    "running",
    "awaiting_input",
    "paused_for_review",
    "approved",
    "submitting",
    "submitted",
    "needs_verification",
    "failed",
    "cancelled",
]

ACTIVE_APPLY_RUN_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "awaiting_input", "paused_for_review", "approved", "submitting"}
)

# Providers whose fill adapters are verified well enough for unattended filling.
# Others need --allow-provider on the executor and stay experimental.
EXECUTOR_PROVIDERS: frozenset[JobProvider] = frozenset(
    {JobProvider.GREENHOUSE, JobProvider.LEVER, JobProvider.ASHBY}
)

# who may cause each hop: "user" (API caller), "executor" (the local browser worker)
APPLY_RUN_TRANSITIONS: dict[str, dict[str, str]] = {
    "queued": {"running": "executor", "cancelled": "user"},
    "running": {
        "running": "executor",
        "awaiting_input": "executor",
        "paused_for_review": "executor",
        "failed": "executor",
        "cancelled": "user",
    },
    "awaiting_input": {"queued": "user", "cancelled": "user", "failed": "executor"},
    "paused_for_review": {"approved": "user", "cancelled": "user", "failed": "executor"},
    "approved": {"submitting": "executor", "cancelled": "user"},
    "submitting": {
        "submitting": "executor",
        "submitted": "executor",
        "needs_verification": "executor",
        "failed": "executor",
    },
    "needs_verification": {"submitted": "user", "cancelled": "user"},
}


class ApplyRunLogEntry(BaseModel):
    at: str = Field(default_factory=utc_now)
    level: Literal["info", "warn", "error"] = "info"
    message: str = Field(min_length=1, max_length=1000)


class ApplyRun(BaseModel):
    """One executor attempt to fill (and, only after approval, submit) an application.

    The executor is a local browser worker on the owner's machine. A run may
    click the employer's final Submit button only while it is ``submitting``,
    which requires the ``approval_token`` issued when the user approved the
    paused review.
    """

    run_id: str
    application_id: str
    profile_id: str = "default"
    job_id: str
    provider: str = ""
    apply_url: str = ""
    status: ApplyRunStatus = "queued"
    step: str = "queued"
    step_log: list[ApplyRunLogEntry] = Field(default_factory=list)
    unresolved_required: list[str] = Field(default_factory=list)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    screenshot_path: str = ""
    approval_token: str | None = None
    approved_at: str | None = None
    executor_id: str = ""
    error: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None


class ApplicationArtifact(BaseModel):
    """Persisted generated file or document linked to one application."""

    artifact_id: str
    application_id: str
    job_id: str
    profile_id: str = "default"
    type: ApplicationArtifactType = ApplicationArtifactType.TAILORED_RESUME
    status: ApplicationArtifactStatus = ApplicationArtifactStatus.GENERATED
    filename: str = ""
    mime_type: str = "application/pdf"
    latex_source: str = ""
    # Cover letters keep their reviewed text here; the PDF is rendered on approve.
    text_content: str = ""
    evidence_notes: list[str] = Field(default_factory=list)
    pdf_b64: str = ""
    pdf_path: str = ""
    pdf_size_bytes: int = 0
    pdf_sha256: str = ""
    diff: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_skills: list[str] = Field(default_factory=list)
    ats_before: dict[str, Any] | None = None
    ats_after: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    page_count: int = 0
    overflow: bool = False
    visual_overflow: bool = False
    min_text_baseline_pt: float | None = None
    source_tailor_session_id: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    approved_at: str | None = None
    uploaded_at: str | None = None


class ApplicationEvent(BaseModel):
    """Timeline entry for application activity."""

    event_id: str
    application_id: str
    kind: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ApplicationTask(BaseModel):
    """Manual follow-up or missing-answer task for one application."""

    task_id: str
    application_id: str
    title: str = Field(min_length=1, max_length=240)
    category: Literal["follow_up", "missing_answer", "interview", "manual", "deadline"] = "manual"
    status: Literal["open", "done", "dismissed"] = "open"
    due_at: str | None = None
    notes: str = Field(default="", max_length=4000)
    created_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None


class WorkAuthorizationProfile(BaseModel):
    """Explicit authorization facts; unknown answers stay unknown."""

    authorized_to_work_in_us: bool | None = None
    requires_sponsorship: bool | None = None
    current_requires_sponsorship: bool | None = None
    future_requires_sponsorship: bool | None = None
    internship_requires_sponsorship: bool | None = None
    full_time_requires_sponsorship: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_sponsorship_from_legacy(
        cls,
        data: object,
    ) -> object:
        """Infer the single sponsorship answer when loading older profile JSON."""
        if isinstance(data, dict) and "requires_sponsorship" not in data:
            legacy_values = [
                data.get("internship_requires_sponsorship"),
                data.get("full_time_requires_sponsorship"),
            ]
            if any(value is not None for value in legacy_values):
                data = {
                    **data,
                    "requires_sponsorship": any(value is True for value in legacy_values),
                }
        return data

    @model_validator(mode="after")
    def sync_legacy_sponsorship_fields(self) -> "WorkAuthorizationProfile":
        """Keep old internship/full-time fields compatible with the single answer."""
        legacy_values = [
            self.internship_requires_sponsorship,
            self.full_time_requires_sponsorship,
        ]
        if self.requires_sponsorship is None and any(value is not None for value in legacy_values):
            self.requires_sponsorship = any(value is True for value in legacy_values)
        if self.requires_sponsorship is not None:
            self.internship_requires_sponsorship = self.requires_sponsorship
            self.full_time_requires_sponsorship = self.requires_sponsorship
        return self


class CompanyRelationshipProfile(BaseModel):
    """Explicit employment relationships for one hiring company or group."""

    currently_employed: bool | None = None
    employed_by_affiliate: bool | None = None
    previously_employed: bool | None = None


class CompensationPreference(BaseModel):
    """Reusable compensation fact for one employment track."""

    application_id: str | None = Field(default=None, max_length=128)
    employment_type: Literal["any", "internship", "full_time"] = "any"
    amount: str = Field(default="", max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    period: Literal["hourly", "monthly", "annual"] = "annual"

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_amount(cls, value: object) -> str:
        return "" if value is None else str(value).strip()


class ApplicationFactsProfile(BaseModel):
    """Tri-state application facts that must never be guessed from a resume."""

    is_at_least_18: bool | None = None
    willing_to_relocate: bool | None = None
    willing_to_travel: bool | None = None
    active_non_compete_or_non_solicit: bool | None = None
    company_relationships: dict[str, CompanyRelationshipProfile] = Field(
        default_factory=dict
    )
    compensation_preferences: list[CompensationPreference] = Field(
        default_factory=list
    )


class AddressProfile(BaseModel):
    """Postal address fields used only for application forms."""

    line1: str = ""
    line2: str = ""
    city: str = ""
    county: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "United States"


class EducationProfile(BaseModel):
    """Optional education facts supplied by the user."""

    school: str = ""
    degree: str = ""
    degree_level: str = ""
    major: str = ""
    field_of_study_candidates: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    currently_studying: bool = False
    graduation_month: str = ""
    graduation_year: str = ""
    gpa: str = ""


class WorkExperienceProfile(BaseModel):
    """Reusable work experience facts supplied by the user."""

    job_title: str = ""
    company: str = ""
    job_type: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    currently_working: bool = False
    summary: str = ""
    bullets: list[str] = Field(default_factory=list)


class ProjectSource(str, Enum):
    """Where a reusable project record came from."""

    RESUME = "resume"
    GITHUB = "github"


class ProjectRecord(BaseModel):
    """Normalized project evidence available during resume tailoring."""

    project_id: str
    profile_id: str = "default"
    source: ProjectSource = ProjectSource.RESUME
    title: str
    url: str = ""
    description: str = ""
    languages: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    readme_excerpt: str = ""
    credibility_score: float | None = Field(default=None, ge=0.0, le=100.0)
    resume_entry_id: str | None = None
    statement_ids: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class ProjectRecommendation(BaseModel):
    """JD-specific project ranking shown in Tailor Studio."""

    project: ProjectRecord
    fit_score: float = Field(default=0.0, ge=0.0, le=100.0)
    matched_terms: list[str] = Field(default_factory=list)
    summary_points: list[str] = Field(default_factory=list)
    default_selected: bool = False
    selectable: bool = True
    rationale: str = ""


class EqualOpportunityProfile(BaseModel):
    """Explicit voluntary EEO answers, excluded from matching and scoring."""

    allow_autofill: bool = False
    disability: str | None = None
    gender: str | None = None
    lgbtq: str | None = None
    veteran_status: str | None = None
    race: str | None = None
    hispanic_or_latino: str | None = None
    sexual_orientation: list[str] = Field(default_factory=list)
    pronouns: str | None = None

    @field_validator("sexual_orientation", mode="before")
    @classmethod
    def _coerce_sexual_orientation(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return []
            return [part.strip() for part in cleaned.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class LLMSettings(BaseModel):
    """Per-profile model routing, key, and daily budget.

    ``api_key`` is encrypted at rest when ``APPLYTEX_DATA_KEY`` is set and is
    never returned by the API (only a masked tail). A ``backend`` here
    overrides the server-wide ``LLM_BACKEND`` for this profile's requests.
    """

    backend: Literal["groq", "anthropic", "openai", "ollama", "codex"] | None = None
    api_key: str | None = Field(default=None, max_length=400)
    model: str | None = Field(default=None, max_length=120)
    daily_call_budget: int | None = Field(default=None, ge=0)
    daily_token_budget: int | None = Field(default=None, ge=0)


class CandidateProfile(BaseModel):
    """User-controlled facts available to future form-filling code."""

    profile_id: str = "default"
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
    resume_filename: str = ""
    resume_latex_source: str = ""
    resume_pdf_filename: str = ""
    resume_pdf_b64: str = ""
    resume_pdf_path: str = ""
    resume_pdf_size_bytes: int = 0
    resume_pdf_sha256: str = ""
    resume_updated_at: str = ""
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
    llm_settings: LLMSettings = Field(default_factory=LLMSettings)
    updated_at: str = Field(default_factory=utc_now)


class QuestionIntent(str, Enum):
    """Semantic purpose of an application question."""

    AUTHORIZATION = "authorization"
    CURRENT_SPONSORSHIP = "current_sponsorship"
    FUTURE_SPONSORSHIP = "future_sponsorship"
    SPONSORSHIP = "sponsorship"
    AGE = "age"
    COMPLETED_EDUCATION = "completed_education"
    COMPENSATION = "compensation"
    COMPANY_EMPLOYMENT = "company_employment"
    AFFILIATE_EMPLOYMENT = "affiliate_employment"
    RELOCATION = "relocation"
    TRAVEL = "travel"
    RESTRICTIVE_AGREEMENT = "restrictive_agreement"
    RECORD_FIELD = "record_field"
    NARRATIVE = "narrative"
    UNKNOWN = "unknown"


class SavedAnswer(BaseModel):
    """A reusable answer the user confirmed once and asked to remember.

    Unlike ``CandidateProfile.custom_answers`` (a flat prompt -> text map), a
    saved answer carries the question intent, learned prompt aliases, where it
    came from, and usage so the fill plan can resolve the same question on the
    next form deterministically.
    """

    answer_id: str
    profile_id: str = "default"
    intent: QuestionIntent = QuestionIntent.UNKNOWN
    prompt_text: str = Field(min_length=1, max_length=500)
    normalized_prompt: str = ""
    value: str | bool | list[str]
    aliases: list[str] = Field(default_factory=list, max_length=32)
    source: Literal["user", "resolved", "llm_reviewed"] = "user"
    ats_provider: str = ""
    use_count: int = Field(default=0, ge=0)
    last_used_at: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class AnswerProposal(BaseModel):
    """A short-answer suggestion grounded in saved facts; never applied without review."""

    field_id: str
    label: str
    value: str | bool | list[str] | None = None
    intent: QuestionIntent = QuestionIntent.UNKNOWN
    confidence: Literal["high", "medium", "low"] = "low"
    evidence: str = ""
    reason: str = ""
    remember_target: Literal["profile_fact", "saved_answer", "none"] = "none"


class FormQuestion(BaseModel):
    """Normalized application form field discovered in the browser."""

    field_id: str
    label: str
    input_type: str
    required: bool = False
    options: list[str] = Field(default_factory=list)
    sensitive: bool = False
    autocomplete: str | None = None
    current_value_present: bool = False
    current_value: str | bool | list[str] | None = None
    control_kind: Literal[
        "scalar",
        "custom_select",
        "multi_select",
        "month_year",
        "year",
    ] = "scalar"
    max_length: int | None = Field(default=None, ge=1)
    profile_record_kind: Literal["education", "work_experience"] | None = None
    profile_record_index: int | None = Field(default=None, ge=0)
    date_boundary: Literal["start", "end"] | None = None
    date_component: Literal["month", "year"] | None = None


class PlanOverride(BaseModel):
    """Application-scoped reviewed value layered over a generated fill plan."""

    value: str | bool | list[str]
    answer_source: Literal["user_input", "generated"] = "user_input"
    research_sources: list[str] = Field(default_factory=list, max_length=12)


class FormScan(BaseModel):
    """Read-only field inventory produced by the Chrome extension."""

    scan_id: str
    application_id: str | None = None
    provider: JobProvider
    page_url: str
    page_title: str = ""
    step_key: str = ""
    form_signature: str = ""
    replace_existing: bool = False
    questions: list[FormQuestion] = Field(default_factory=list, max_length=300)
    plan_overrides: dict[str, PlanOverride] = Field(default_factory=dict)
    captured_at: str = Field(default_factory=utc_now)

    @field_validator("plan_overrides", mode="before")
    @classmethod
    def load_legacy_plan_overrides(cls, value: object) -> object:
        """Keep scans written before typed override metadata was introduced readable."""
        if not isinstance(value, dict):
            return value
        return {
            str(field_id): (
                override
                if isinstance(override, dict) and "value" in override
                else {"value": override, "answer_source": "user_input"}
            )
            for field_id, override in value.items()
        }


class ApplicationDetail(BaseModel):
    """Joined view for the application tracker and detail pages."""

    application: ApplicationRecord
    job: JobPosting | None = None
    artifacts: list[ApplicationArtifact] = Field(default_factory=list)
    events: list[ApplicationEvent] = Field(default_factory=list)
    tasks: list[ApplicationTask] = Field(default_factory=list)
    latest_form_scan: FormScan | None = None


class BrowserJobCapture(BaseModel):
    """Job details captured from a user-visible browser tab."""

    provider: JobProvider
    external_id: str = ""
    company: str
    title: str
    description: str
    location: str = ""
    source_url: str
    apply_url: str
    workflow_key: str = ""
    canonical_url: str = ""
    description_source: str = ""
    capture_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    published_at: str | None = None


class FillAction(BaseModel):
    """A proposed form action that is reviewed before browser execution."""

    field_id: str
    action: Literal["fill", "select", "select_many", "check", "upload", "skip"]
    value: str | bool | list[str] | None = None
    answer_source: Literal[
        "profile",
        "custom_answer",
        "saved_answer",
        "resume",
        "user_input",
        "generated",
        "none",
        "eeo_opt_in",
    ]
    requires_review: bool = True
    resolution_reason: str = ""
    saved_answer_id: str | None = None
