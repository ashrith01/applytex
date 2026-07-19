"""Optional local auth gate and profile-ownership tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from latex_resume.application_store import ApplicationStore
from latex_resume.api import create_app
from latex_resume.job_models import JobPosting, JobProvider


def test_auth_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("APPLYTEX_REQUIRE_AUTH", raising=False)
    app = create_app(application_store=ApplicationStore(tmp_path / "auth-off.db"))
    with TestClient(app) as client:
        status = client.get("/auth/status")
        assert status.status_code == 200
        assert status.json()["auth_required"] is False
        assert client.get("/jobs").status_code == 200


def test_auth_required_rejects_and_accepts_bearer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    app = create_app(application_store=ApplicationStore(tmp_path / "auth-on.db"))
    with TestClient(app) as client:
        assert client.get("/jobs").status_code == 401
        login = client.post(
            "/auth/login",
            json={"profile_id": "alice", "password": "local-secret", "set_password": True},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        allowed = client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert allowed.status_code == 200
        status = client.get("/auth/status", headers={"Authorization": f"Bearer {token}"})
        assert status.json()["authenticated"] is True
        assert status.json()["profile_id"] == "alice"


def test_auth_forged_profile_header_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    app = create_app(application_store=ApplicationStore(tmp_path / "auth-forge.db"))
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"profile_id": "alice", "password": "local-secret", "set_password": True},
        )
        token = login.json()["access_token"]
        forged = client.get(
            "/jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Profile-Id": "bob",
            },
        )
        assert forged.status_code == 403


def test_auth_blocks_cross_profile_application_access(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    store = ApplicationStore(tmp_path / "auth-own.db")
    job = JobPosting(
        job_id="job-alice",
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id="1",
        company="Acme",
        title="Engineer",
        description="Python and ML.",
        source_url="https://boards.greenhouse.io/acme/jobs/1",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
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
        assert alice.status_code == 200
        assert bob.status_code == 200
        alice_token = alice.json()["access_token"]
        bob_token = bob.json()["access_token"]
        app_id = application.application_id

        ok = client.get(
            f"/applications/{app_id}",
            headers={
                "Authorization": f"Bearer {alice_token}",
                "X-Profile-Id": "alice",
            },
        )
        assert ok.status_code == 200

        denied = client.get(
            f"/applications/{app_id}",
            headers={
                "Authorization": f"Bearer {bob_token}",
                "X-Profile-Id": "bob",
            },
        )
        assert denied.status_code == 404

        patch_denied = client.patch(
            f"/applications/{app_id}",
            headers={
                "Authorization": f"Bearer {bob_token}",
                "X-Profile-Id": "bob",
            },
            json={"notes": "hijack"},
        )
        assert patch_denied.status_code == 404
