"""Explainable recruiter-style evaluation built on deterministic ATS results.

This is a local scorecard layer, not an LLM judge. It borrows the useful shape
of rubric-based evaluation -- category scores, evidence, deductions, strengths,
and improvements -- while keeping the core ATS matching deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from latex_resume.ats import ATSResult, check_ats
from latex_resume.project_scoring import ProjectCredibilityReport, score_project_credibility
from latex_resume.screening import ScreeningAnalysis, analyze_screening_fit

EvaluationBand = Literal["strong_fit", "good_fit", "partial_fit", "manual_review"]

FAIRNESS_EXCLUDED_SIGNALS: tuple[str, ...] = (
    "name",
    "gender",
    "age",
    "photo",
    "address",
    "city",
    "region",
    "school prestige",
    "gpa",
    "cgpa",
)


@dataclass
class EvaluationCategory:
    """One category in the recruiter-style scorecard."""

    name: str
    label: str
    score: float
    max_score: float
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "score": self.score,
            "max_score": self.max_score,
            "evidence": list(self.evidence),
            "gaps": list(self.gaps),
        }


@dataclass
class ResumeEvaluationReport:
    """Full deterministic evaluation report for UI, API, or run history."""

    total_score: float
    max_score: float
    band: EvaluationBand
    categories: list[EvaluationCategory] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    areas_for_improvement: list[str] = field(default_factory=list)
    deductions: list[str] = field(default_factory=list)
    fairness_excluded_signals: tuple[str, ...] = FAIRNESS_EXCLUDED_SIGNALS
    ats: ATSResult | None = None
    screening: ScreeningAnalysis | None = None
    project_credibility: ProjectCredibilityReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "max_score": self.max_score,
            "band": self.band,
            "categories": [category.to_dict() for category in self.categories],
            "strengths": list(self.strengths),
            "areas_for_improvement": list(self.areas_for_improvement),
            "deductions": list(self.deductions),
            "fairness_excluded_signals": list(self.fairness_excluded_signals),
            "ats": self.ats.__dict__ if self.ats else None,
            "screening": self.screening.to_dict() if self.screening else None,
            "project_credibility": (
                self.project_credibility.to_dict()
                if self.project_credibility
                else None
            ),
        }


def build_resume_evaluation(
    resume_text: str,
    job_keywords: Mapping[str, Any],
    *,
    resume_data: Mapping[str, Any] | None = None,
    ats_result: ATSResult | None = None,
    screening_analysis: ScreeningAnalysis | None = None,
    confirmed_skills: list[str] | None = None,
    editable_statement_count: int = 0,
) -> ResumeEvaluationReport:
    """Build an explainable deterministic fit report.

    ``resume_data`` should be the output of ``extract_full_resume`` when
    available. If omitted, project credibility is treated as neutral.
    """
    keywords = dict(job_keywords)
    ats = ats_result or check_ats(
        resume_text,
        keywords,
        confirmed_skills=confirmed_skills,
    )
    screening = screening_analysis or analyze_screening_fit(
        resume_text,
        keywords,
        ats,
        editable_statement_count=editable_statement_count,
        confirmed_skills=confirmed_skills,
    )
    projects = (
        score_project_credibility(resume_data)
        if resume_data is not None
        else ProjectCredibilityReport(
            score=60.0,
            band="basic",
            risks=["Structured project data was not provided."],
        )
    )

    categories = [
        _category_from_ratio(
            name="required_skills",
            label="Required skills",
            ratio=ats.required_score,
            max_score=30.0,
            evidence=ats.required_found,
            gaps=ats.required_missing,
        ),
        _category_from_ratio(
            name="preferred_skills",
            label="Preferred skills",
            ratio=ats.preferred_score,
            max_score=15.0,
            evidence=ats.preferred_found,
            gaps=ats.preferred_missing,
        ),
        _category_from_ratio(
            name="jd_keywords",
            label="JD keywords",
            ratio=ats.keyword_score,
            max_score=10.0,
            evidence=ats.keyword_hits,
            gaps=ats.keyword_misses,
        ),
        _truth_safety_category(screening),
        _project_category(projects),
        _experience_category(screening),
    ]

    total = round(sum(category.score for category in categories), 1)
    deductions = _deductions(ats, screening, projects)
    strengths = _strengths(ats, screening, projects)
    improvements = _areas_for_improvement(ats, screening, projects)
    return ResumeEvaluationReport(
        total_score=total,
        max_score=100.0,
        band=_band(total, screening.truth_risk),
        categories=categories,
        strengths=strengths,
        areas_for_improvement=improvements,
        deductions=deductions,
        ats=ats,
        screening=screening,
        project_credibility=projects,
    )


def _category_from_ratio(
    *,
    name: str,
    label: str,
    ratio: float,
    max_score: float,
    evidence: list[str],
    gaps: list[str],
) -> EvaluationCategory:
    score = round(max_score * max(0.0, min(100.0, ratio)) / 100.0, 1)
    return EvaluationCategory(
        name=name,
        label=label,
        score=score,
        max_score=max_score,
        evidence=list(evidence[:8]),
        gaps=list(gaps[:8]),
    )


def _truth_safety_category(screening: ScreeningAnalysis) -> EvaluationCategory:
    blocked = [
        check.item
        for check in screening.truth_checks
        if check.status in {"user_confirmation_required", "not_supported_do_not_add"}
    ]
    themes = [
        check.item
        for check in screening.truth_checks
        if check.status == "theme_only_weave_if_truthful"
    ]
    if screening.truth_risk == "low":
        ratio = 100.0
    elif screening.truth_risk == "medium":
        ratio = 65.0
    else:
        ratio = 30.0
    return _category_from_ratio(
        name="truth_safety",
        label="Truth safety",
        ratio=ratio,
        max_score=15.0,
        evidence=["No unsupported hard-skill blockers detected."] if not blocked else [],
        gaps=blocked + themes[:3],
    )


def _project_category(projects: ProjectCredibilityReport) -> EvaluationCategory:
    evidence = projects.strengths or [
        f"Project credibility is {projects.band.replace('_', ' ')}."
    ]
    return _category_from_ratio(
        name="project_credibility",
        label="Project credibility",
        ratio=projects.score,
        max_score=15.0,
        evidence=evidence,
        gaps=projects.risks,
    )


def _experience_category(screening: ScreeningAnalysis) -> EvaluationCategory:
    if screening.match_breakdown is None:
        return _category_from_ratio(
            name="experience",
            label="Experience",
            ratio=screening.score,
            max_score=15.0,
            evidence=[],
            gaps=[],
        )
    category = screening.match_breakdown.categories["experience"]
    return _category_from_ratio(
        name="experience",
        label="Experience",
        ratio=category.score,
        max_score=15.0,
        evidence=category.evidence,
        gaps=category.missing,
    )


def _deductions(
    ats: ATSResult,
    screening: ScreeningAnalysis,
    projects: ProjectCredibilityReport,
) -> list[str]:
    out: list[str] = []
    out.extend(ats.submission_blockers)
    if screening.truth_risk != "low":
        out.append(f"Truth risk is {screening.truth_risk}; unsupported claims need review.")
    if projects.risks:
        out.extend(projects.risks[:3])
    return list(dict.fromkeys(out))[:8]


def _strengths(
    ats: ATSResult,
    screening: ScreeningAnalysis,
    projects: ProjectCredibilityReport,
) -> list[str]:
    out: list[str] = []
    if ats.required_found:
        out.append("Required skill coverage: " + ", ".join(ats.required_found[:5]))
    if ats.preferred_found:
        out.append("Preferred skill coverage: " + ", ".join(ats.preferred_found[:5]))
    out.extend(projects.strengths[:3])
    if screening.match_category == "Strong Fit":
        out.append("Overall deterministic screening category is Strong Fit.")
    return list(dict.fromkeys(out))[:5]


def _areas_for_improvement(
    ats: ATSResult,
    screening: ScreeningAnalysis,
    projects: ProjectCredibilityReport,
) -> list[str]:
    out: list[str] = []
    if ats.required_missing:
        out.append("Resolve required skill gaps: " + ", ".join(ats.required_missing[:5]))
    if ats.preferred_missing:
        out.append("Consider preferred skill gaps: " + ", ".join(ats.preferred_missing[:5]))
    if ats.keyword_misses:
        out.append("Weave supported JD keywords: " + ", ".join(ats.keyword_misses[:5]))
    out.extend(projects.risks[:2])
    out.extend(screening.recommendations[:2])
    return list(dict.fromkeys(out))[:5]


def _band(total: float, truth_risk: str) -> EvaluationBand:
    if truth_risk == "high":
        return "manual_review"
    if total >= 80.0:
        return "strong_fit"
    if total >= 65.0:
        return "good_fit"
    return "partial_fit"
