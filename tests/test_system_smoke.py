"""Thin system-smoke chains (TestClient, no live browser ATS login)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from latex_resume.application_store import ApplicationStore
from latex_resume.api import create_app
from latex_resume.job_models import JobPosting, JobProvider

ROOT = Path(__file__).parent.parent
SAMPLE_RESUME = ROOT / "samples" / "sample_resume.tex"


def test_profile_to_form_plan_smoke(tmp_path: Path) -> None:
    """Usable US/Texas profile → capture → application → scan → geo keep plan."""
    app = create_app(application_store=ApplicationStore(tmp_path / "smoke-plan.db"))
    headers = {"X-Profile-Id": "alice"}
    with TestClient(app) as client:
        profile = client.get("/profile", headers=headers).json()
        profile["profile_id"] = "alice"
        profile["full_name"] = "Alice Example"
        profile["email"] = "alice@example.test"
        profile["address"] = {
            **profile.get("address", {}),
            "country": "United States",
            "state": "Texas",
            "city": "Austin",
        }
        assert client.put("/profile", json=profile, headers=headers).status_code == 200
        assert client.put(
            "/profile/active",
            json={"profile_id": "alice"},
            headers=headers,
        ).status_code == 200

        captured = client.post(
            "/extension/jobs/capture",
            headers=headers,
            json={
                "provider": "greenhouse",
                "external_id": "smoke-1",
                "company": "Acme",
                "title": "Software Engineer",
                "description": "Build systems in Texas, United States.",
                "location": "Austin, Texas",
                "source_url": "https://boards.greenhouse.io/acme/jobs/smoke-1",
                "apply_url": "https://boards.greenhouse.io/acme/jobs/smoke-1",
            },
        )
        assert captured.status_code == 200
        job_id = captured.json()["job_id"]

        application = client.post(
            "/applications",
            headers=headers,
            json={"job_id": job_id, "profile_id": "alice"},
        )
        assert application.status_code == 200
        app_id = application.json()["application_id"]

        scan = client.post(
            "/extension/forms/scan",
            headers=headers,
            json={
                "application_id": app_id,
                "provider": "workday",
                "page_url": "https://example.myworkdayjobs.com/apply",
                "questions": [
                    {
                        "field_id": "country",
                        "label": "Country",
                        "input_type": "select",
                        "required": True,
                        "options": ["United States of America", "Canada"],
                        "current_value_present": True,
                        "current_value": "United States of America",
                    },
                    {
                        "field_id": "state",
                        "label": "State",
                        "input_type": "select",
                        "required": True,
                        "options": ["TX", "CA"],
                        "current_value_present": True,
                        "current_value": "TX",
                    },
                ],
            },
        )
        assert scan.status_code == 200
        plan = client.get(
            f"/extension/forms/{scan.json()['scan_id']}/plan",
            headers=headers,
        )
        assert plan.status_code == 200
        body = plan.json()
        assert body["actions"][0]["action"] == "skip"
        assert body["review_items"][0]["change_kind"] == "keep"
        assert body["actions"][1]["action"] == "skip"
        assert body["review_items"][1]["change_kind"] == "keep"

        bob_denied = client.get(
            f"/applications/{app_id}",
            headers={"X-Profile-Id": "bob"},
        )
        assert bob_denied.status_code == 404


def test_authenticated_profile_ownership_smoke(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    store = ApplicationStore(tmp_path / "smoke-auth.db")
    job = JobPosting(
        job_id="job-alice",
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id="auth-smoke",
        company="Acme",
        title="Engineer",
        description="Python.",
        source_url="https://boards.greenhouse.io/acme/jobs/auth-smoke",
        apply_url="https://boards.greenhouse.io/acme/jobs/auth-smoke",
        captured_for_profile_id="alice",
    )
    store.save_job(job)
    application = store.create_application(job.job_id, profile_id="alice")
    app = create_app(application_store=store)
    with TestClient(app) as client:
        alice = client.post(
            "/auth/login",
            json={"profile_id": "alice", "password": "alice-secret", "set_password": True},
        )
        bob = client.post(
            "/auth/login",
            json={"profile_id": "bob", "password": "bob-secretxx", "set_password": True},
        )
        alice_h = {
            "Authorization": f"Bearer {alice.json()['access_token']}",
            "X-Profile-Id": "alice",
        }
        bob_h = {
            "Authorization": f"Bearer {bob.json()['access_token']}",
            "X-Profile-Id": "bob",
        }
        app_id = application.application_id
        assert client.get(f"/applications/{app_id}", headers=alice_h).status_code == 200
        assert client.get(f"/applications/{app_id}", headers=bob_h).status_code == 404
        forged = client.get(
            f"/applications/{app_id}",
            headers={
                "Authorization": f"Bearer {alice.json()['access_token']}",
                "X-Profile-Id": "bob",
            },
        )
        assert forged.status_code == 403


def test_profile_listing_and_active_selection_smoke(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "smoke-profiles.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["profile_id"] = "usable_user"
        profile["full_name"] = "Usable User"
        profile["email"] = "usable@example.test"
        assert client.put("/profile", json=profile).status_code == 200
        assert client.put("/profile/active", json={"profile_id": "empty_shell"}).status_code == 200

        listed = client.get("/profiles")
        assert listed.status_code == 200
        by_id = {item["profile_id"]: item for item in listed.json()["profiles"]}
        assert by_id["usable_user"]["usable"] is True
        assert by_id["empty_shell"]["usable"] is False

        active = client.put(
            "/profile/active",
            json={"profile_id": "usable_user"},
            headers={"X-Profile-Id": "usable_user"},
        )
        assert active.status_code == 200
        assert active.json()["profile_id"] == "usable_user"
        current = client.get("/profile/active", headers={"X-Profile-Id": "usable_user"})
        assert current.status_code == 200
        assert current.json()["profile_id"] == "usable_user"


def test_latex_session_lifecycle_smoke(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "smoke-latex.db"))
    with TestClient(app) as client:
        with SAMPLE_RESUME.open("rb") as handle:
            upload = client.post(
                "/latex/upload",
                files={"file": ("resume.tex", handle, "application/x-tex")},
            )
        assert upload.status_code == 200
        session_id = upload.json()["session_id"]

        status = client.get(f"/latex/{session_id}/status")
        assert status.status_code == 200
        assert "confirmation_required_skills" in status.json()

        rerender = client.post(
            f"/latex/{session_id}/rerender",
            json={
                "changes": {
                    "summary_0": (
                        "AI/ML Engineer with hands-on experience building LLM, RAG, "
                        "and production-oriented machine learning systems."
                    )
                }
            },
        )
        assert rerender.status_code == 200
        assert "applied" in rerender.json()

        deleted = client.delete(f"/latex/{session_id}")
        assert deleted.status_code == 200
        assert client.get(f"/latex/{session_id}/status").status_code == 404
