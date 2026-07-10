"""Enterprise-style screening analysis and truth checks.

This module stays deterministic and local.  It translates ATS keyword results
into recruiter/vendor-like criteria categories, then flags unsupported JD gaps
that should not be patched into a resume without user confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from latex_resume.ats import ATSResult, _skill_found, _normalise

CriterionKind = Literal["must_have", "nice_to_have", "keyword"]
CriterionStatus = Literal["found", "similar", "unclear", "missing"]
BreakdownCategoryName = Literal[
    "experience",
    "skills",
    "industry_domain",
    "education",
    "keywords",
]
BreakdownStatus = Literal["strong", "good", "partial", "blocked"]


@dataclass
class ScreeningCriterion:
    """One enterprise-style matching criterion."""

    text: str
    kind: CriterionKind
    status: CriterionStatus
    evidence: list[str] = field(default_factory=list)
    estimated_points: float = 0.0


@dataclass
class TruthCheck:
    """Whether a JD gap is supported by the existing resume."""

    item: str
    category: str
    status: Literal[
        "already_supported",
        "user_confirmation_required",
        "theme_only_weave_if_truthful",
        "not_supported_do_not_add",
    ]
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class MatchBreakdownCategory:
    """One Jobright-style category score with evidence and gaps."""

    name: BreakdownCategoryName
    label: str
    score: float
    status: BreakdownStatus
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    estimated_points_lost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "score": self.score,
            "status": self.status,
            "found": list(self.found),
            "missing": list(self.missing),
            "evidence": list(self.evidence),
            "estimated_points_lost": self.estimated_points_lost,
        }


@dataclass
class MatchBreakdown:
    """Category breakdown used by ATS, optimization analysis, and run history."""

    overall_score: float
    categories: dict[BreakdownCategoryName, MatchBreakdownCategory]
    top_score_leaks: list[dict[str, Any]] = field(default_factory=list)
    truth_blocked: list[TruthCheck] = field(default_factory=list)
    auto_fixable: list[TruthCheck] = field(default_factory=list)
    section_priority: list[str] = field(default_factory=list)
    edit_focus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "categories": {
                name: category.to_dict()
                for name, category in self.categories.items()
            },
            "top_score_leaks": list(self.top_score_leaks),
            "truth_blocked": [t.__dict__ for t in self.truth_blocked],
            "auto_fixable": [t.__dict__ for t in self.auto_fixable],
            "section_priority": list(self.section_priority),
            "edit_focus": list(self.edit_focus),
        }


@dataclass
class ScreeningAnalysis:
    """Screening simulator output inspired by enterprise AI matching products."""

    score: float
    match_category: str
    must_have_coverage: float
    nice_to_have_coverage: float
    keyword_coverage: float
    lever_strong_fit: bool
    parser_risk: str
    truth_risk: str
    criteria: list[ScreeningCriterion] = field(default_factory=list)
    truth_checks: list[TruthCheck] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    match_breakdown: MatchBreakdown | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "match_category": self.match_category,
            "must_have_coverage": self.must_have_coverage,
            "nice_to_have_coverage": self.nice_to_have_coverage,
            "keyword_coverage": self.keyword_coverage,
            "lever_strong_fit": self.lever_strong_fit,
            "parser_risk": self.parser_risk,
            "truth_risk": self.truth_risk,
            "criteria": [c.__dict__ for c in self.criteria],
            "truth_checks": [t.__dict__ for t in self.truth_checks],
            "recommendations": list(self.recommendations),
            "match_breakdown": (
                self.match_breakdown.to_dict() if self.match_breakdown else None
            ),
        }


_THEME_HINTS = (
    "experience",
    "industry",
    "healthcare",
    "ecommerce",
    "e-commerce",
    "communication",
    "stakeholder",
    "business",
    "customer",
    "team",
    "platform",
    "cloud",
    "enterprise",
    "security",
    "digital",
)

_DOMAIN_HINTS = (
    "healthcare",
    "ecommerce",
    "e-commerce",
    "retail",
    "finance",
    "fintech",
    "banking",
    "insurance",
    "cloud",
    "enterprise",
    "security",
    "sales",
    "digital",
    "platform",
)


def analyze_screening_fit(
    resume_text: str,
    job_keywords: dict[str, Any],
    ats_result: ATSResult,
    editable_statement_count: int = 0,
    confirmed_skills: list[str] | None = None,
) -> ScreeningAnalysis:
    """Build an enterprise-style fit analysis from ATS output."""
    norm_resume = _normalise(resume_text)
    criteria = _build_criteria(job_keywords, ats_result, norm_resume)
    truth_checks = _build_truth_checks(ats_result, norm_resume, confirmed_skills)
    truth_risk = _truth_risk(truth_checks)
    parser_risk = _parser_risk(editable_statement_count)
    score = ats_result.score

    analysis = ScreeningAnalysis(
        score=score,
        match_category=_match_category(score, truth_risk),
        must_have_coverage=ats_result.required_score,
        nice_to_have_coverage=ats_result.preferred_score,
        keyword_coverage=ats_result.keyword_score,
        lever_strong_fit=score >= 75.0,
        parser_risk=parser_risk,
        truth_risk=truth_risk,
        criteria=criteria,
        truth_checks=truth_checks,
    )
    analysis.match_breakdown = _build_match_breakdown(
        resume_text=resume_text,
        norm_resume=norm_resume,
        job_keywords=job_keywords,
        ats_result=ats_result,
        truth_checks=truth_checks,
    )
    analysis.recommendations = _recommendations(analysis)
    return analysis


def _build_match_breakdown(
    resume_text: str,
    norm_resume: str,
    job_keywords: dict[str, Any],
    ats_result: ATSResult,
    truth_checks: list[TruthCheck],
) -> MatchBreakdown:
    """Build five category scores and the most useful next actions."""
    categories: dict[BreakdownCategoryName, MatchBreakdownCategory] = {
        "experience": _experience_category(resume_text, norm_resume, job_keywords),
        "skills": _skills_category(ats_result),
        "industry_domain": _domain_category(norm_resume, job_keywords),
        "education": _education_category(norm_resume, job_keywords),
        "keywords": _keywords_category(ats_result),
    }
    truth_blocked = [
        t for t in truth_checks
        if t.status in {"user_confirmation_required", "not_supported_do_not_add"}
    ]
    auto_fixable = [
        t for t in truth_checks
        if t.status in {"already_supported", "theme_only_weave_if_truthful"}
    ]
    leaks = _top_score_leaks(ats_result)
    section_priority = _section_priority(categories, ats_result)
    return MatchBreakdown(
        overall_score=ats_result.score,
        categories=categories,
        top_score_leaks=leaks,
        truth_blocked=truth_blocked,
        auto_fixable=auto_fixable,
        section_priority=section_priority,
        edit_focus=_edit_focus(categories, truth_blocked, auto_fixable),
    )


def _experience_category(
    resume_text: str,
    norm_resume: str,
    job_keywords: dict[str, Any],
) -> MatchBreakdownCategory:
    requested_years = _requested_years(job_keywords)
    resume_years = _resume_years(resume_text)
    seniority = str(job_keywords.get("seniority_level", "") or "").lower()
    evidence: list[str] = []
    missing: list[str] = []

    if resume_years is not None:
        evidence.append(f"{resume_years:g}+ years visible in resume")

    if requested_years is None:
        score = 100.0
    elif resume_years is None:
        score = 55.0
        missing.append(f"{requested_years:g}+ years requirement")
    elif resume_years >= requested_years:
        score = 100.0
    elif resume_years + 1 >= requested_years:
        score = 78.0
        missing.append(f"slightly below {requested_years:g}+ years requirement")
    else:
        score = 45.0
        missing.append(f"{requested_years:g}+ years requirement")

    if seniority in {"intern", "junior", "new-grad", "new grad"}:
        if any(token in norm_resume for token in ("student", "intern", "graduate", "b.tech", "master")):
            score = max(score, 85.0)
            evidence.append("student/intern/new-grad signal visible")
    elif seniority in {"senior", "staff", "principal"} and score < 85:
        missing.append(f"{seniority} seniority signal")

    return MatchBreakdownCategory(
        name="experience",
        label="Experience",
        score=round(score, 1),
        status=_status_from_score(score),
        found=evidence,
        missing=missing,
        evidence=evidence[:4],
        estimated_points_lost=round(max(0.0, 100.0 - score) * 0.2, 1),
    )


def _skills_category(ats_result: ATSResult) -> MatchBreakdownCategory:
    score = round((ats_result.required_score * 0.7) + (ats_result.preferred_score * 0.3), 1)
    missing = list(dict.fromkeys(ats_result.required_missing + ats_result.preferred_missing))
    found = list(dict.fromkeys(ats_result.required_found + ats_result.preferred_found))
    return MatchBreakdownCategory(
        name="skills",
        label="Skills",
        score=score,
        status=_status_from_score(score),
        found=found[:12],
        missing=missing[:12],
        evidence=found[:6],
        estimated_points_lost=round((100.0 - score) * 0.45, 1),
    )


def _domain_category(
    norm_resume: str,
    job_keywords: dict[str, Any],
) -> MatchBreakdownCategory:
    domain_terms = _domain_terms(job_keywords)
    if not domain_terms:
        return MatchBreakdownCategory(
            name="industry_domain",
            label="Industry/domain",
            score=100.0,
            status="strong",
            found=["no explicit domain constraint in JD"],
            evidence=["no explicit domain constraint in JD"],
        )
    found = [term for term in domain_terms if _skill_found(term, norm_resume) or _similar_or_missing(term, norm_resume) == "similar"]
    missing = [term for term in domain_terms if term not in found]
    score = round((len(found) / len(domain_terms)) * 100, 1)
    return MatchBreakdownCategory(
        name="industry_domain",
        label="Industry/domain",
        score=score,
        status=_status_from_score(score),
        found=found,
        missing=missing,
        evidence=found[:5],
        estimated_points_lost=round((100.0 - score) * 0.15, 1),
    )


def _education_category(
    norm_resume: str,
    job_keywords: dict[str, Any],
) -> MatchBreakdownCategory:
    requirements = [
        str(req) for req in job_keywords.get("education_requirements", [])
        if str(req).strip()
    ]
    if not requirements:
        return MatchBreakdownCategory(
            name="education",
            label="Education",
            score=100.0,
            status="strong",
            found=["no explicit education constraint in JD"],
            evidence=["no explicit education constraint in JD"],
        )
    education_evidence = [
        token for token in (
            "bachelor",
            "b tech",
            "b.tech",
            "master",
            "m s",
            "m.s.",
            "computer science",
            "engineering",
        )
        if token in norm_resume
    ]
    score = 100.0 if education_evidence else 40.0
    return MatchBreakdownCategory(
        name="education",
        label="Education",
        score=score,
        status=_status_from_score(score),
        found=education_evidence,
        missing=[] if education_evidence else requirements,
        evidence=education_evidence[:4],
        estimated_points_lost=round((100.0 - score) * 0.1, 1),
    )


def _keywords_category(ats_result: ATSResult) -> MatchBreakdownCategory:
    return MatchBreakdownCategory(
        name="keywords",
        label="Keywords",
        score=ats_result.keyword_score,
        status=_status_from_score(ats_result.keyword_score),
        found=list(ats_result.keyword_hits),
        missing=list(ats_result.keyword_misses),
        evidence=list(ats_result.keyword_hits[:8]),
        estimated_points_lost=round((100.0 - ats_result.keyword_score) * 0.25, 1),
    )


def _build_criteria(
    job_keywords: dict[str, Any],
    ats_result: ATSResult,
    norm_resume: str,
) -> list[ScreeningCriterion]:
    criteria: list[ScreeningCriterion] = []
    groups: tuple[tuple[CriterionKind, list[str], list[str], float], ...] = (
        (
            "must_have",
            [str(s) for s in job_keywords.get("required_skills", [])],
            ats_result.required_found,
            60.0,
        ),
        (
            "nice_to_have",
            [str(s) for s in job_keywords.get("preferred_skills", [])],
            ats_result.preferred_found,
            25.0,
        ),
        (
            "keyword",
            [str(s) for s in job_keywords.get("keywords", [])],
            ats_result.keyword_hits,
            15.0,
        ),
    )

    for kind, items, found_items, weight in groups:
        impact = round(weight / len(items), 1) if items else 0.0
        for item in items:
            found = item in found_items
            status: CriterionStatus = "found" if found else _similar_or_missing(item, norm_resume)
            criteria.append(
                ScreeningCriterion(
                    text=item,
                    kind=kind,
                    status=status,
                    evidence=_evidence_snippets(item, norm_resume) if found else [],
                    estimated_points=impact,
                )
            )
    return criteria


def _requested_years(job_keywords: dict[str, Any]) -> float | None:
    raw = job_keywords.get("experience_years")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    for req in job_keywords.get("experience_requirements", []):
        match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", str(req), re.I)
        if match:
            return float(match.group(1))
    return None


def _resume_years(resume_text: str) -> float | None:
    matches = [
        float(match.group(1))
        for match in re.finditer(
            r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)",
            resume_text,
            re.I,
        )
    ]
    return max(matches) if matches else None


def _domain_terms(job_keywords: dict[str, Any]) -> list[str]:
    raw_items: list[str] = []
    for key in (
        "keywords",
        "preferred_skills",
        "required_skills",
        "key_responsibilities",
    ):
        raw_items.extend(str(item) for item in job_keywords.get(key, []))
    out: list[str] = []
    for item in raw_items:
        norm = item.lower()
        if any(hint in norm for hint in _DOMAIN_HINTS):
            out.append(item)
    return list(dict.fromkeys(out))


def _top_score_leaks(ats_result: ATSResult) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for category, found, missing, weight in (
        ("skills", ats_result.required_found, ats_result.required_missing, 60.0),
        ("skills", ats_result.preferred_found, ats_result.preferred_missing, 25.0),
        ("keywords", ats_result.keyword_hits, ats_result.keyword_misses, 15.0),
    ):
        total = len(found) + len(missing)
        if not total:
            continue
        impact = round(weight / total, 1)
        leaks.extend(
            {
                "item": item,
                "category": category,
                "estimated_points": impact,
            }
            for item in missing
        )
    leaks.sort(key=lambda item: item["estimated_points"], reverse=True)
    return leaks[:10]


def _section_priority(
    categories: dict[BreakdownCategoryName, MatchBreakdownCategory],
    ats_result: ATSResult,
) -> list[str]:
    priority: list[str] = []
    if categories["skills"].score < 90 or ats_result.required_missing:
        priority.append("Skills")
    if categories["experience"].score < 85 or ats_result.keyword_misses:
        priority.append("Experience")
    if categories["keywords"].score < 85:
        priority.append("Projects")
    if categories["industry_domain"].score < 85:
        priority.append("Professional Summary")
    if categories["education"].score < 90:
        priority.append("Education")
    priority.extend(["Skills", "Experience", "Projects", "Professional Summary", "Education"])
    return list(dict.fromkeys(priority))


def _edit_focus(
    categories: dict[BreakdownCategoryName, MatchBreakdownCategory],
    truth_blocked: list[TruthCheck],
    auto_fixable: list[TruthCheck],
) -> list[str]:
    focus: list[str] = []
    if categories["skills"].missing:
        focus.append("Confirm only truthful technical skills, then patch the skills section.")
    if categories["keywords"].missing or auto_fixable:
        focus.append("Auto-weave supported JD keywords into summary, work, and projects.")
    if categories["experience"].score < 85:
        focus.append("Lead bullets with the most role-relevant existing experience.")
    if categories["industry_domain"].score < 85:
        focus.append("Use domain wording only where existing evidence supports it.")
    if truth_blocked:
        focus.append("Explain why unconfirmed skills may keep the score below 80.")
    return focus or ["Maintain one-page recruiter readability after scoring passes."]


def _status_from_score(score: float) -> BreakdownStatus:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "good"
    if score >= 50:
        return "partial"
    return "blocked"


def _build_truth_checks(
    ats_result: ATSResult,
    norm_resume: str,
    confirmed_skills: list[str] | None = None,
) -> list[TruthCheck]:
    out: list[TruthCheck] = []
    confirmed = {s.strip().lower() for s in (confirmed_skills or []) if s.strip()}
    for category, items in (
        ("required", ats_result.required_missing),
        ("preferred", ats_result.preferred_missing),
        ("keyword", ats_result.keyword_misses),
    ):
        for item in items:
            if _is_theme(item):
                status = "theme_only_weave_if_truthful"
                reason = "JD theme/domain phrase, not a standalone skill."
            elif item.lower() in confirmed:
                status = "already_supported"
                reason = "User confirmed this skill as truthful for this run."
            elif _skill_found(item, norm_resume):
                status = "already_supported"
                reason = "Matched by alias or equivalent phrasing."
            elif category == "keyword":
                status = "theme_only_weave_if_truthful"
                reason = "Keyword phrase can be woven into bullets only if supported."
            else:
                status = "user_confirmation_required"
                reason = "Skill is absent from resume text and needs user confirmation."
            out.append(TruthCheck(item=item, category=category, status=status, reason=reason))
    for item in getattr(ats_result, "excluded_unconfirmed_skills", []):
        if item.lower() in confirmed:
            continue
        out.append(
            TruthCheck(
                item=item,
                category="excluded_hard_skill",
                status="not_supported_do_not_add",
                reason=(
                    "Hard tool/platform is unconfirmed, so it is excluded from "
                    "submission scoring and must not be fabricated."
                ),
            )
        )
    return out


def _similar_or_missing(item: str, norm_resume: str) -> CriterionStatus:
    tokens = [t for t in _normalise(item).split() if len(t) > 2]
    if not tokens:
        return "missing"
    hits = sum(1 for token in tokens if re.search(r"\b" + re.escape(token) + r"\b", norm_resume))
    if hits == len(tokens):
        return "found"
    if hits > 0:
        return "similar"
    return "missing"


def _evidence_snippets(item: str, norm_resume: str) -> list[str]:
    forms = _normalise(item).split()
    if not forms:
        return []
    return [item]


def _is_theme(item: str) -> bool:
    norm = item.lower()
    return any(hint in norm for hint in _THEME_HINTS)


def _truth_risk(truth_checks: list[TruthCheck]) -> str:
    unsupported = [
        t for t in truth_checks
        if t.status in {"user_confirmation_required", "not_supported_do_not_add"}
    ]
    themes = [t for t in truth_checks if t.status == "theme_only_weave_if_truthful"]
    if len(unsupported) >= 3:
        return "high"
    if unsupported or len(themes) >= 3:
        return "medium"
    return "low"


def _parser_risk(editable_statement_count: int) -> str:
    if editable_statement_count <= 0:
        return "high"
    if editable_statement_count < 5:
        return "medium"
    return "low"


def _match_category(score: float, truth_risk: str) -> str:
    if truth_risk == "high":
        return "Needs Manual Review"
    if score >= 80:
        return "Strong Fit"
    if score >= 70:
        return "Good Fit"
    if score >= 50:
        return "Partial Fit"
    return "Limited Fit"


def _recommendations(analysis: ScreeningAnalysis) -> list[str]:
    recs: list[str] = []
    missing_must = [
        c.text for c in analysis.criteria
        if c.kind == "must_have" and c.status in {"missing", "similar", "unclear"}
    ]
    if missing_must:
        recs.append("Prioritize must-have gaps: " + ", ".join(missing_must[:5]))
    confirm = [
        t.item for t in analysis.truth_checks
        if t.status == "user_confirmation_required"
    ]
    if confirm:
        recs.append("Confirm before adding: " + ", ".join(confirm[:5]))
    themes = [
        t.item for t in analysis.truth_checks
        if t.status == "theme_only_weave_if_truthful"
    ]
    if themes:
        recs.append("Weave only if truthful: " + ", ".join(themes[:5]))
    if analysis.parser_risk != "low":
        recs.append("Parser risk is not low; inspect editable statement extraction.")
    if not recs and analysis.match_category == "Strong Fit":
        recs.append("Strong fit. Focus next on recruiter readability and specificity.")
    return recs
