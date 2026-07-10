"""Tests for enterprise-style screening simulation."""

from __future__ import annotations

from latex_resume.ats import check_ats
from latex_resume.screening import analyze_screening_fit


def test_screening_analysis_strong_fit_with_low_truth_risk() -> None:
    resume = "AI engineer with Python, Azure, RAG, LangChain, and production ML."
    job_keywords = {
        "required_skills": ["Python", "Azure", "RAG"],
        "preferred_skills": ["LangChain"],
        "keywords": ["production"],
    }
    ats = check_ats(resume, job_keywords)

    analysis = analyze_screening_fit(
        resume,
        job_keywords,
        ats,
        editable_statement_count=10,
    )

    assert analysis.match_category == "Strong Fit"
    assert analysis.lever_strong_fit is True
    assert analysis.truth_risk == "low"
    assert analysis.parser_risk == "low"
    assert analysis.match_breakdown is not None
    assert set(analysis.match_breakdown.categories) == {
        "experience",
        "skills",
        "industry_domain",
        "education",
        "keywords",
    }
    assert analysis.match_breakdown.categories["skills"].score == 100.0


def test_screening_analysis_needs_manual_review_for_high_truth_risk() -> None:
    resume = "Data analyst with SQL and dashboards."
    job_keywords = {
        "required_skills": ["Python", "Azure", "RAG", "LangChain"],
        "preferred_skills": [],
        "keywords": [],
    }
    ats = check_ats(resume, job_keywords)

    analysis = analyze_screening_fit(
        resume,
        job_keywords,
        ats,
        editable_statement_count=10,
    )

    assert analysis.truth_risk == "high"
    assert analysis.match_category == "Needs Manual Review"
    assert [
        t.status for t in analysis.truth_checks
        if t.status == "user_confirmation_required"
    ]


def test_screening_treats_domain_phrases_as_theme_only() -> None:
    resume = "AI engineer with Python and RAG systems."
    job_keywords = {
        "required_skills": ["Python"],
        "preferred_skills": ["Healthcare industry knowledge"],
        "keywords": ["communication"],
    }
    ats = check_ats(resume, job_keywords)

    analysis = analyze_screening_fit(
        resume,
        job_keywords,
        ats,
        editable_statement_count=10,
    )

    statuses = {t.item: t.status for t in analysis.truth_checks}
    assert statuses["Healthcare industry knowledge"] == "theme_only_weave_if_truthful"
    assert statuses["communication"] == "theme_only_weave_if_truthful"


def test_screening_treats_confirmed_missing_skill_as_supported() -> None:
    resume = "AI engineer with Python systems."
    job_keywords = {
        "required_skills": ["Python", "LangChain"],
        "preferred_skills": ["GitHub Copilot"],
        "keywords": [],
    }
    ats = check_ats(resume, job_keywords)

    analysis = analyze_screening_fit(
        resume,
        job_keywords,
        ats,
        editable_statement_count=10,
        confirmed_skills=["LangChain", "GitHub Copilot"],
    )

    statuses = {t.item: t.status for t in analysis.truth_checks}
    assert statuses["LangChain"] == "already_supported"
    assert statuses["GitHub Copilot"] == "already_supported"
    assert analysis.truth_risk == "low"


def test_match_breakdown_separates_truth_blocked_and_auto_fixable_gaps() -> None:
    resume = "AI engineer with Python, FastAPI APIs, Azure pipelines, and B.Tech education."
    job_keywords = {
        "required_skills": ["Python", "Bedrock"],
        "preferred_skills": ["API development", "Cloud Platform - Azure"],
        "education_requirements": ["Bachelor's degree"],
        "keywords": ["enterprise platform", "communication"],
        "experience_years": 2,
    }
    ats = check_ats(resume, job_keywords)

    analysis = analyze_screening_fit(
        resume,
        job_keywords,
        ats,
        editable_statement_count=10,
    )

    assert analysis.match_breakdown is not None
    breakdown = analysis.match_breakdown
    assert breakdown.categories["education"].score == 100.0
    assert any(item.item == "Bedrock" for item in breakdown.truth_blocked)
    assert any(item.item == "communication" for item in breakdown.auto_fixable)
    assert "Skills" in breakdown.section_priority
    assert breakdown.top_score_leaks
