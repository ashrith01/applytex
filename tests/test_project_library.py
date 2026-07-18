from __future__ import annotations

import asyncio
import base64

import httpx

from latex_resume.job_models import ProjectRecord, ProjectSource
from latex_resume.models import ParseResult
from latex_resume.project_library import (
    GitHubProjectClient,
    build_resume_project_records,
    default_selected_project_ids,
    filter_latex_projects,
    rank_project_records,
)


def test_build_resume_project_records_contains_statement_ids(parsed: ParseResult) -> None:
    projects = build_resume_project_records("default", parsed)

    assert len(projects) == 1
    project = projects[0]
    assert project.source is ProjectSource.RESUME
    assert project.resume_entry_id == "proj_0"
    assert project.statement_ids == ["proj_0_0", "proj_0_1"]
    assert project.credibility_score is not None
    assert "FastAPI" in project.description


def test_rank_defaults_top_two_resume_projects_and_keeps_github_unselectable() -> None:
    projects = [
        ProjectRecord(
            project_id="profile:resume:proj_0",
            source=ProjectSource.RESUME,
            title="RAG Search",
            description="Built Python FastAPI retrieval and vector search APIs.",
            languages=["Python", "FastAPI"],
            credibility_score=92.0,
            resume_entry_id="proj_0",
            statement_ids=["proj_0_0"],
        ),
        ProjectRecord(
            project_id="profile:resume:proj_1",
            source=ProjectSource.RESUME,
            title="SQL Forecasting",
            description="Built SQL and machine learning forecasting pipelines.",
            languages=["SQL", "Python"],
            credibility_score=84.0,
            resume_entry_id="proj_1",
            statement_ids=["proj_1_0"],
        ),
        ProjectRecord(
            project_id="profile:github:github-only",
            source=ProjectSource.GITHUB,
            title="Agent Sandbox",
            description="A public LangChain agent demo.",
            languages=["TypeScript"],
            topics=["langchain"],
            url="https://github.com/example/agent-sandbox",
        ),
    ]

    recommendations = rank_project_records(
        projects,
        {
            "required_skills": ["Python", "FastAPI", "SQL"],
            "preferred_skills": ["machine learning"],
            "keywords": ["retrieval", "forecasting", "agent"],
        },
    )
    selected = default_selected_project_ids(recommendations, limit=2)

    assert len(selected) == 2
    assert all(project_id.startswith("profile:resume:") for project_id in selected)
    assert any(not item.selectable for item in recommendations if item.project.source is ProjectSource.GITHUB)


def test_github_project_client_fetches_public_repos_with_mock_transport() -> None:
    readme_b64 = base64.b64encode(b"# ML API\nBuilds RAG answers with FastAPI.").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/ashrith/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "ml-api",
                        "full_name": "ashrith/ml-api",
                        "html_url": "https://github.com/ashrith/ml-api",
                        "description": "FastAPI RAG service",
                        "topics": ["rag", "ml"],
                        "fork": False,
                        "archived": False,
                        "owner": {"login": "ashrith"},
                    },
                    {
                        "name": "forked-demo",
                        "full_name": "ashrith/forked-demo",
                        "fork": True,
                        "archived": False,
                    },
                    {
                        "name": "old-demo",
                        "full_name": "ashrith/old-demo",
                        "fork": False,
                        "archived": True,
                    },
                ],
            )
        if path == "/repos/ashrith/ml-api/languages":
            return httpx.Response(200, json={"Python": 1000, "TypeScript": 200})
        if path == "/repos/ashrith/ml-api/readme":
            return httpx.Response(200, json={"encoding": "base64", "content": readme_b64})
        return httpx.Response(404, json={})

    async def run() -> list[ProjectRecord]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=transport,
        ) as client:
            return await GitHubProjectClient(client).fetch_public_projects(
                profile_id="default",
                github_url="https://github.com/ashrith",
            )

    projects = asyncio.run(run())

    assert len(projects) == 1
    project = projects[0]
    assert project.project_id == "default:github:ashrith/ml-api"
    assert project.source is ProjectSource.GITHUB
    assert project.languages == ["Python", "TypeScript"]
    assert "RAG answers" in project.readme_excerpt


def test_filter_latex_projects_removes_unselected_project_entry_without_touching_rest() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Projects}
\textbf{Alpha Project}
\begin{itemize}
  \item Built Python APIs for search.
\end{itemize}
\textbf{Beta Project}
\begin{itemize}
  \item Built a Java game.
\end{itemize}
\section{Skills}
\begin{itemize}
  \item Python, SQL
\end{itemize}
\end{document}
"""

    result = filter_latex_projects(tex, selected_resume_entry_ids={"proj_0"})

    assert "Alpha Project" in result.latex_source
    assert "Built Python APIs" in result.latex_source
    assert "Beta Project" not in result.latex_source
    assert "Built a Java game" not in result.latex_source
    assert "\\section{Skills}" in result.latex_source
    assert result.removed_entry_ids == ["proj_1"]
    assert result.warnings == []
