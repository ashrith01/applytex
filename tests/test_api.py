"""FastAPI contract tests that do not require an LLM provider."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from latex_resume.application_store import ApplicationStore
from latex_resume.api import create_app
from latex_resume.job_models import (
    CandidateProfile,
    JobPosting,
    JobProvider,
    JobSearchResult,
    WorkExperienceProfile,
    utc_now,
)

SAMPLE_PATH = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cors_rejects_arbitrary_web_origins() -> None:
    with TestClient(create_app()) as client:
        response = client.options(
            "/profile",
            headers={
                "Origin": "https://malicious.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_private_network_preflight_for_supported_ats() -> None:
    with TestClient(create_app()) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://job-boards.greenhouse.io",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://job-boards.greenhouse.io"
    assert response.headers["access-control-allow-private-network"] == "true"


def test_upload_status_rerender_and_delete() -> None:
    sample = SAMPLE_PATH.read_bytes()

    with TestClient(create_app()) as client:
        uploaded = client.post(
            "/latex/upload",
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        upload_body = uploaded.json()
        session_id = upload_body["session_id"]
        assert "summary" in upload_body["editable"]
        assert upload_body["filename"] == "sample_resume.tex"

        status = client.get(f"/latex/{session_id}/status")
        assert status.status_code == 200
        assert status.json()["optimized"] is False

        rerendered = client.post(
            f"/latex/{session_id}/rerender",
            json={
                "changes": {
                    "summary_0": (
                        "Software engineer building reliable backend services "
                        "and production data pipelines."
                    )
                }
            },
        )
        assert rerendered.status_code == 200
        rerender_body = rerendered.json()
        assert rerender_body["applied"] == ["summary_0"]
        assert "production data pipelines" in rerender_body["modified_latex"]
        assert rerender_body["page_count"] == 1

        deleted = client.delete(f"/latex/{session_id}")
        assert deleted.status_code == 200
        assert client.get(f"/latex/{session_id}/status").status_code == 404


def test_upload_rejects_non_tex_file() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/latex/upload",
            files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
        )

    assert response.status_code == 400


class _StubJobSearchService:
    async def search(self, query, sources, preferences=None) -> JobSearchResult:
        return JobSearchResult(
            search_id="search-test",
            query=query,
            sources=sources,
            jobs=[
                JobPosting(
                    job_id="job-test",
                    provider=JobProvider.GREENHOUSE,
                    board_token="example",
                    external_id="123",
                    company="Example",
                    title="Machine Learning Engineer",
                    description="Build machine learning systems.",
                    location="Remote",
                    workplace_type="remote",
                    source_url="https://example.test/jobs/123",
                    apply_url="https://example.test/jobs/123",
                    retrieved_at=utc_now(),
                )
            ],
        )


def test_job_search_and_application_contract(tmp_path: Path) -> None:
    app = create_app(
        job_search_service=_StubJobSearchService(),
        application_store=ApplicationStore(tmp_path / "api.db"),
    )
    with TestClient(app) as client:
        searched = client.post(
            "/jobs/search",
            json={
                "query": {"text": "machine learning"},
                "sources": [
                    {
                        "provider": "greenhouse",
                        "board_token": "example",
                        "company": "Example",
                    }
                ],
            },
        )
        assert searched.status_code == 200
        assert searched.json()["jobs"][0]["job_id"] == "job-test"

        created = client.post("/applications", json={"job_id": "job-test"})
        assert created.status_code == 200
        application_id = created.json()["application_id"]

        unsafe = client.post(
            f"/applications/{application_id}/transition",
            json={"status": "submitting"},
        )
        assert unsafe.status_code == 409

        listed = client.get("/jobs")
        assert listed.status_code == 200
        assert listed.json()[0]["title"] == "Machine Learning Engineer"


def test_profile_browser_capture_and_read_only_fill_plan(tmp_path: Path) -> None:
    app = create_app(
        job_search_service=_StubJobSearchService(),
        application_store=ApplicationStore(tmp_path / "extension.db"),
    )
    with TestClient(app) as client:
        profile = client.get("/profile")
        assert profile.status_code == 200
        assert profile.json()["work_authorization"] == {
            "authorized_to_work_in_us": True,
            "requires_sponsorship": False,
            "internship_requires_sponsorship": False,
            "full_time_requires_sponsorship": False,
        }
        updated_profile = profile.json()
        updated_profile["full_name"] = "Test Candidate"
        updated_profile["first_name"] = "Test"
        updated_profile["last_name"] = "Candidate"
        updated_profile["equal_opportunity"]["allow_autofill"] = True
        updated_profile["equal_opportunity"]["hispanic_or_latino"] = "No"
        assert client.put("/profile", json=updated_profile).status_code == 200

        setup = client.get("/profile/setup-questions")
        assert setup.status_code == 200
        assert "Full legal name" not in setup.json()["missing_required"]

        captured = client.post(
            "/extension/jobs/capture",
            json={
                "provider": "linkedin",
                "external_id": "linkedin-123",
                "company": "Example",
                "title": "Machine Learning Intern",
                "description": "US internship building ML systems.",
                "location": "Houston, TX",
                "source_url": "https://www.linkedin.com/jobs/view/123",
                "apply_url": "https://www.linkedin.com/jobs/view/123",
            },
        )
        assert captured.status_code == 200
        assert captured.json()["target_role"] == "ml_intern"
        assert captured.json()["employment_track"] == "internship"

        application = client.post(
            "/applications",
            json={"job_id": captured.json()["job_id"]},
        )
        assert application.status_code == 200

        scan = client.post(
            "/extension/forms/scan",
            json={
                "application_id": application.json()["application_id"],
                "provider": "linkedin",
                "page_url": "https://www.linkedin.com/jobs/view/123/apply",
                "page_title": "Apply",
                "questions": [
                    {
                        "field_id": "name",
                        "label": "First name",
                        "input_type": "text",
                        "required": True,
                    },
                    {
                        "field_id": "sponsor",
                        "label": "Will you require sponsorship?",
                        "input_type": "select",
                        "required": True,
                    },
                    {
                        "field_id": "hispanic",
                        "label": "Are you Hispanic/Latino?",
                        "input_type": "radio",
                        "required": False,
                        "sensitive": True,
                        "options": ["Yes", "No", "Prefer not to answer"],
                    },
                ],
            },
        )
        assert scan.status_code == 200

        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")
        assert plan.status_code == 200
        plan_body = plan.json()
        assert plan_body["page_url"] == "https://www.linkedin.com/jobs/view/123/apply"
        assert plan_body["can_submit"] is False
        assert plan_body["can_fill"] is True
        assert plan_body["actions"][0]["value"] == "Test"
        assert plan_body["actions"][1]["value"] == "No"
        assert plan_body["actions"][2]["value"] == "No"
        assert plan_body["unresolved_required"] == []


def test_profile_resume_upload_prefills_from_tex(tmp_path: Path) -> None:
    sample = SAMPLE_PATH.read_bytes()
    app = create_app(application_store=ApplicationStore(tmp_path / "prefill.db"))
    with TestClient(app) as client:
        uploaded = client.post(
            "/profile/resume",
            params={"overwrite": "true"},
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["has_latex_source"] is True
        assert "prefill_applied" in body
        assert len(body["prefill_applied"]) > 0

        profile = client.get("/profile").json()
        assert profile.get("full_name") or profile.get("email") or profile.get("skills")


def test_profile_view_excludes_raw_resume_payloads_and_patch_preserves_them(
    tmp_path: Path,
) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "profile_view.db"))
    profile = CandidateProfile(
        profile_id="tester",
        full_name="Original Name",
        email="original@example.test",
        resume_filename="resume.tex",
        resume_latex_source="\\documentclass{article}\\begin{document}secret\\end{document}",
        resume_pdf_filename="resume.pdf",
        resume_pdf_b64="JVBERi0xLjQKsecret",
        resume_updated_at="2026-07-03T00:00:00+00:00",
        skills=["Python"],
    )

    with TestClient(app) as client:
        replaced = client.put("/profile", json=profile.model_dump(mode="json"))
        assert replaced.status_code == 200

        full = client.get("/profile", params={"profile_id": "tester"})
        assert full.status_code == 200
        assert full.json()["resume_latex_source"] == profile.resume_latex_source
        assert full.json()["resume_pdf_b64"] == profile.resume_pdf_b64

        view = client.get("/profile/view", params={"profile_id": "tester"})
        assert view.status_code == 200
        view_body = view.json()
        assert "resume_latex_source" not in view_body
        assert "resume_pdf_b64" not in view_body
        assert view_body["has_latex_source"] is True
        assert view_body["has_pdf"] is True
        assert view_body["resume_filename"] == "resume.tex"

        patched = client.patch(
            "/profile",
            params={"profile_id": "tester"},
            json={
                "full_name": "Updated Name",
                "email": "updated@example.test",
                "skills": ["Python", "FastAPI"],
            },
        )
        assert patched.status_code == 200
        patched_body = patched.json()
        assert patched_body["full_name"] == "Updated Name"
        assert "resume_latex_source" not in patched_body
        assert "resume_pdf_b64" not in patched_body

        after = client.get("/profile", params={"profile_id": "tester"}).json()
        assert after["full_name"] == "Updated Name"
        assert after["email"] == "updated@example.test"
        assert after["skills"] == ["Python", "FastAPI"]
        assert after["resume_filename"] == profile.resume_filename
        assert after["resume_latex_source"] == profile.resume_latex_source
        assert after["resume_pdf_filename"] == profile.resume_pdf_filename
        assert after["resume_pdf_b64"] == profile.resume_pdf_b64
        assert after["resume_updated_at"] == profile.resume_updated_at


def test_profile_view_repairs_stale_resume_derived_work_metadata(tmp_path: Path) -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading
  {AI/ML Engineer}
  {Nov 2023 -- Aug 2025}
  {Accenture -- GenLite (Internal Enterprise AI Platform for Client Delivery)}
  {Hyderabad, India}
\begin{itemize}
  \item Built production RAG pipelines for enterprise clients.
  \item Automated code transformation workflows across programming languages.
\end{itemize}
\end{document}
"""
    app = create_app(application_store=ApplicationStore(tmp_path / "profile_repair.db"))
    profile = CandidateProfile(
        profile_id="tester",
        resume_filename="resume.tex",
        resume_latex_source=tex,
        work_experiences=[
            WorkExperienceProfile(
                company="AI/ML Engineer",
                job_title="Accenture – GenLite (Internal Enterprise AI Platform for Client Delivery)",
                location="Hyderabad, India",
                start_date="2023-11",
                end_date="2025-08",
                bullets=[],
            )
        ],
    )

    with TestClient(app) as client:
        assert client.put("/profile", json=profile.model_dump(mode="json")).status_code == 200

        view = client.get("/profile/view", params={"profile_id": "tester"})
        assert view.status_code == 200
        work = view.json()["work_experiences"][0]
        assert work["company"] == "Accenture"
        assert work["job_title"] == "AI/ML Engineer"
        assert work["location"] == "Hyderabad, India"
        assert work["bullets"] == [
            "Built production RAG pipelines for enterprise clients.",
            "Automated code transformation workflows across programming languages.",
        ]

        persisted = client.get("/profile", params={"profile_id": "tester"}).json()
        assert persisted["work_experiences"][0]["company"] == "Accenture"
        assert persisted["work_experiences"][0]["bullets"] == work["bullets"]


def test_application_store_enables_sqlite_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "wal.db"
    ApplicationStore(db_path)

    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_analyze_and_active_profile(tmp_path: Path) -> None:
    sample = SAMPLE_PATH.read_bytes()
    app = create_app(application_store=ApplicationStore(tmp_path / "analyze.db"))
    with TestClient(app) as client:
        assert client.put("/profile/active", json={"profile_id": "tester"}).status_code == 200

        uploaded = client.post(
            "/latex/upload",
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        session_id = uploaded.json()["session_id"]

        analyzed = client.post(
            "/latex/analyze",
            json={
                "session_id": session_id,
                "job_description": "Looking for Python, FastAPI, and machine learning experience.",
                "analysis_mode": "fast",
            },
        )
        assert analyzed.status_code == 200
        body = analyzed.json()
        assert "baseline_ats" in body
        assert body["editable_statement_count"] > 0

        report = client.get(f"/latex/{session_id}/report")
        assert report.status_code == 200
        assert report.json()["optimized"] is False


def test_patch_profile_deep_merges_equal_opportunity(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "patch.db"))
    with TestClient(app) as client:
        base = client.get("/profile?profile_id=merge-test").json()
        base["profile_id"] = "merge-test"
        base["equal_opportunity"]["gender"] = "Male"
        base["equal_opportunity"]["disability"] = "No"
        base["equal_opportunity"]["allow_autofill"] = False
        assert client.put("/profile", json=base).status_code == 200

        patched = client.patch(
            "/profile?profile_id=merge-test",
            json={"equal_opportunity": {"allow_autofill": True}},
        )
        assert patched.status_code == 200
        eeo = patched.json()["equal_opportunity"]
        assert eeo["allow_autofill"] is True
        assert eeo["gender"] == "Male"
        assert eeo["disability"] == "No"
