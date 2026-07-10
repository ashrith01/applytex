"""Public job-search and controlled application workflow models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

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


class JobSourceConfig(BaseModel):
    """Configuration for one public employer job board."""

    provider: JobProvider
    board_token: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)

    @field_validator("board_token")
    @classmethod
    def validate_board_token(cls, value: str) -> str:
        """Reject tokens that could alter a provider URL."""
        cleaned = value.strip()
        if not cleaned.replace("-", "").replace("_", "").isalnum():
            raise ValueError("board_token may contain only letters, numbers, hyphens, and underscores")
        return cleaned

    @field_validator("provider")
    @classmethod
    def reject_browser_only_provider(cls, value: JobProvider) -> JobProvider:
        """Keep LinkedIn discovery in the user-visible extension."""
        if value is JobProvider.LINKEDIN:
            raise ValueError("LinkedIn jobs must be captured through the Chrome extension")
        return value


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
    "Houston, TX",
    "Austin, TX",
    "Dallas, TX",
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
    willing_to_relocate: bool = False
    accepted_employment_types: list[Literal["internship", "full_time"]] = Field(
        default_factory=lambda: ["internship", "full_time"]
    )
    prioritize_internships: bool = True
    excluded_title_terms: list[str] = Field(
        default_factory=lambda: ["senior", "sr", "staff", "manager"]
    )


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
    published_at: str | None = None
    retrieved_at: str = Field(default_factory=utc_now)
    industry: str | None = None
    target_role: TargetRole | None = None
    employment_track: Literal["internship", "full_time", "unknown"] = "unknown"
    search_score: float = Field(default=0.0, ge=0.0)


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
    job_id: str
    status: ApplicationStatus = ApplicationStatus.DISCOVERED
    resume_session_id: str | None = None
    notes: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    approved_at: str | None = None
    submitted_at: str | None = None


class WorkAuthorizationProfile(BaseModel):
    """Explicit authorization facts; unknown answers stay unknown."""

    authorized_to_work_in_us: bool | None = True
    requires_sponsorship: bool | None = False
    internship_requires_sponsorship: bool | None = False
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


class AddressProfile(BaseModel):
    """Postal address fields used only for application forms."""

    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "United States"


class EducationProfile(BaseModel):
    """Optional education facts supplied by the user."""

    school: str = ""
    degree: str = ""
    major: str = ""
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
    custom_answers: dict[str, str] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=utc_now)


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


class FormScan(BaseModel):
    """Read-only field inventory produced by the Chrome extension."""

    scan_id: str
    application_id: str | None = None
    provider: JobProvider
    page_url: str
    page_title: str = ""
    questions: list[FormQuestion] = Field(default_factory=list, max_length=300)
    captured_at: str = Field(default_factory=utc_now)


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
    published_at: str | None = None


class FillAction(BaseModel):
    """A proposed form action that is reviewed before browser execution."""

    field_id: str
    action: Literal["fill", "select", "check", "upload", "skip"]
    value: str | bool | None = None
    answer_source: Literal[
        "profile",
        "custom_answer",
        "resume",
        "user_input",
        "none",
        "eeo_opt_in",
    ]
    requires_review: bool = True
