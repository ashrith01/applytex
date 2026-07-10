"""Versioned schemas shared by the ApplyTeX ATS benchmark commands."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

RoleFamily = Literal[
    "ai_engineer",
    "ml_engineer",
    "data_scientist",
    "mlops_engineer",
    "nlp_llm_engineer",
    "data_engineer",
]
Seniority = Literal["junior", "mid", "senior", "staff"]
FitTier = Literal["strong", "medium", "weak", "incompatible"]
SourceKind = Literal["live_public", "taxonomy_derived", "adversarial"]


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class EvidenceLedger(BaseModel):
    """Immutable truth source used to audit generated resume changes."""

    schema_version: str = SCHEMA_VERSION
    resume_id: str
    skills: list[str] = Field(default_factory=list)
    employers: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    supported_equivalents: dict[str, list[str]] = Field(default_factory=dict)
    allowed_claims: list[str] = Field(default_factory=list)
    synthetic: bool = True


class ResumeFixture(BaseModel):
    """One generated LaTeX resume plus its evidence metadata."""

    schema_version: str = SCHEMA_VERSION
    resume_id: str
    role_family: RoleFamily
    seniority: Seniority
    profile_fit: FitTier
    template_id: str
    latex_path: str
    evidence_path: str
    content_sha256: str
    holdout: bool = False
    counterfactual_group: str | None = None
    counterfactual_attribute: str | None = None
    word_count: int = 0
    parser_ok: bool = False
    render_ok: bool = False
    page_count: int = 0
    overflow: bool = False


class JobFixture(BaseModel):
    """One benchmark job description and its structured labels."""

    schema_version: str = SCHEMA_VERSION
    job_id: str
    title: str
    company: str
    role_family: RoleFamily
    seniority: Seniority
    industry: str
    source_kind: SourceKind
    provider: str
    source_url: str
    captured_at: str
    text_path: str
    content_sha256: str
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    education_requirements: list[str] = Field(default_factory=list)
    holdout: bool = False
    license_or_usage: str = ""
    expires_at: str | None = None
    adversarial_tags: list[str] = Field(default_factory=list)

    def keyword_payload(self) -> dict[str, Any]:
        """Return the structure consumed by the existing ATS scorer."""
        return {
            "required_skills": list(self.required_skills),
            "preferred_skills": list(self.preferred_skills),
            "keywords": list(self.keywords),
            "experience_years": self.experience_years,
            "experience_requirements": (
                [f"{self.experience_years:g}+ years"]
                if self.experience_years is not None
                else []
            ),
            "education_requirements": list(self.education_requirements),
            "seniority_level": self.seniority,
            "extraction_method": f"benchmark_{self.source_kind}",
        }


class BenchmarkCase(BaseModel):
    """A selected resume/JD pair and all deterministic baseline scores."""

    schema_version: str = SCHEMA_VERSION
    case_id: str
    resume_id: str
    job_id: str
    expected_fit_tier: FitTier
    holdout: bool
    evidence_overlap: float
    baseline_submission_score: float
    baseline_raw_score: float
    bm25_score: float
    embedding_score: float
    embedding_backend: str
    required_skill_precision: float
    required_skill_recall: float
    selected: bool = False
    selection_bucket: str = ""


class BenchmarkRun(BaseModel):
    """One provider-backed optimization attempt, including failures."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    cache_key: str
    case_id: str
    resume_id: str
    job_id: str
    provider: Literal["groq", "codex", "deterministic"]
    model: str | None = None
    prompt_version: str
    code_version: str
    started_at: str
    completed_at: str
    status: Literal["success", "failed", "cached"]
    error_type: str | None = None
    error_message: str | None = None
    latency_ms: float = 0.0
    stage_latencies_ms: dict[str, float] = Field(default_factory=dict)
    score_before: float | None = None
    score_after: float | None = None
    raw_score_before: float | None = None
    raw_score_after: float | None = None
    score_delta: float | None = None
    target_met: bool = False
    page_count: int = 0
    overflow: bool = False
    change_count: int = 0
    rejected_change_count: int = 0
    unsupported_claims: list[str] = Field(default_factory=list)
    introduced_metrics: list[str] = Field(default_factory=list)
    evidence_preservation_score: float = 0.0
    contextual_keyword_coverage: float = 0.0
    standalone_keyword_coverage: float = 0.0
    semantic_similarity: float = 0.0
    confirmed_skills: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    modified_latex_path: str | None = None
    modified_pdf_path: str | None = None
    trace_id: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost_usd: float | None = None


class HumanReview(BaseModel):
    """Blind recruiter-style assessment for one optimized output."""

    schema_version: str = SCHEMA_VERSION
    review_id: str
    blind_item_id: str
    run_id: str
    reviewer_id: str
    truthfulness: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    readability: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    keyword_naturalness: int = Field(ge=1, le=5)
    one_page_usability: int = Field(ge=1, le=5)
    shortlist_likelihood: int = Field(ge=1, le=5)
    preferred_version: Literal["original", "optimized", "tie"]
    critical_unsupported_claim: bool = False
    reason: str = ""
    created_at: str = Field(default_factory=utc_now)


class BenchmarkSummary(BaseModel):
    """Top-level report data and acceptance-gate outcomes."""

    schema_version: str = SCHEMA_VERSION
    generated_at: str = Field(default_factory=utc_now)
    corpus: dict[str, int] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    fairness: dict[str, Any] = Field(default_factory=dict)
    slices: dict[str, Any] = Field(default_factory=dict)
    acceptance_gates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
