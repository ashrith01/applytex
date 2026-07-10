"""Tests for deterministic recruiter-style evaluation reports."""

from __future__ import annotations

from latex_resume.evaluation import build_resume_evaluation


def test_resume_evaluation_combines_ats_truth_and_project_evidence() -> None:
    resume_text = (
        "AI engineer with Python, Azure, RAG, LangChain, production ML, and "
        "2+ years of experience. Built FastAPI RAG support agent with Docker "
        "deployment and reduced latency by 35%."
    )
    job_keywords = {
        "required_skills": ["Python", "Azure", "RAG"],
        "preferred_skills": ["LangChain"],
        "keywords": ["production", "latency"],
        "experience_years": 2,
    }
    resume_data = {
        "projects": [
            {
                "title": "RAG Support Agent",
                "urls": ["https://example.com/demo"],
                "bullets": [
                    "Built Python FastAPI RAG pipeline with Docker deployment.",
                    "Reduced latency by 35%.",
                ],
            }
        ]
    }

    report = build_resume_evaluation(
        resume_text,
        job_keywords,
        resume_data=resume_data,
        editable_statement_count=8,
    )

    assert report.total_score >= 85
    assert report.band == "strong_fit"
    assert [category.name for category in report.categories] == [
        "required_skills",
        "preferred_skills",
        "jd_keywords",
        "truth_safety",
        "project_credibility",
        "experience",
    ]
    assert report.project_credibility is not None
    assert report.project_credibility.band in {"strong", "good"}
    assert "gpa" in report.to_dict()["fairness_excluded_signals"]


def test_resume_evaluation_manual_review_when_truth_risk_is_high() -> None:
    resume_text = "Data analyst with SQL dashboards."
    job_keywords = {
        "required_skills": ["Python", "Azure", "RAG", "LangChain"],
        "preferred_skills": [],
        "keywords": [],
    }

    report = build_resume_evaluation(
        resume_text,
        job_keywords,
        resume_data={"projects": []},
        editable_statement_count=6,
    )

    assert report.band == "manual_review"
    assert any("Truth risk is high" in deduction for deduction in report.deductions)
    truth = next(category for category in report.categories if category.name == "truth_safety")
    assert truth.score < truth.max_score
