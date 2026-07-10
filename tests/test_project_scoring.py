"""Tests for deterministic project credibility scoring."""

from __future__ import annotations

from latex_resume.project_scoring import score_project_credibility


def test_project_credibility_rewards_links_technical_depth_and_impact() -> None:
    resume_data = {
        "projects": [
            {
                "title": "RAG Support Agent",
                "urls": ["https://example.com/demo"],
                "bullets": [
                    "Built Python FastAPI RAG pipeline with vector retrieval, "
                    "evaluation, Docker deployment, and monitoring.",
                    "Reduced answer latency by 35% for 500+ support queries.",
                ],
            }
        ]
    }

    report = score_project_credibility(resume_data)

    assert report.score >= 80
    assert report.band == "strong"
    assert report.projects[0].band == "strong"
    assert any("Technical stack" in signal for signal in report.projects[0].signals)
    assert any("Quantified outcome" in signal for signal in report.projects[0].signals)


def test_project_credibility_flags_generic_unverified_projects() -> None:
    resume_data = {
        "projects": [
            {
                "title": "Todo App",
                "bullets": ["Created a todo app with basic CRUD features."],
            }
        ]
    }

    report = score_project_credibility(resume_data)

    assert report.score < 45
    assert report.band == "weak"
    assert "Projects lack verification links in the resume." in report.risks
    assert any("generic" in risk for risk in report.projects[0].risks)


def test_project_credibility_is_neutral_when_projects_are_missing() -> None:
    report = score_project_credibility({})

    assert report.score == 50.0
    assert report.band == "basic"
    assert report.risks
