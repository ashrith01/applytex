"""Phase 5a: ownership across every route, durable tokens, export/delete."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import JobPosting, JobProvider
from latex_resume.local_auth import LocalAuthStore, rate_limit_key

SAMPLE_TEX = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


def _job(store: ApplicationStore, job_id: str, captured_for: str | None = None) -> JobPosting:
    job = JobPosting(
        job_id=job_id,
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id=job_id,
        company="Acme",
        title="ML Engineer",
        description="Python. " * 40,
        source_url=f"https://example.test/{job_id}",
        apply_url=f"https://example.test/{job_id}/apply",
        captured_for_profile_id=captured_for,
    )
    store.save_job(job)
    return job


def _login(client: TestClient, profile_id: str, password: str = "correct-horse-battery") -> dict[str, str]:
    response = client.post("/auth/login", json={"profile_id": profile_id, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}", "X-Profile-Id": profile_id}


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def test_tokens_survive_restart_expire_and_revoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    db = tmp_path / "tokens.db"
    with TestClient(create_app(application_store=ApplicationStore(db))) as client:
        alice = _login(client, "alice")
        assert client.get("/profile", headers=alice).status_code == 200
        status = client.get("/auth/status", headers=alice).json()
        assert status["authenticated"] and status["profile_id"] == "alice" and status["has_password"]

    # A fresh process with the same database still honors the token.
    with TestClient(create_app(application_store=ApplicationStore(db))) as client:
        assert client.get("/profile", headers=alice).status_code == 200
        assert client.get("/profile").status_code == 401
        assert client.post("/auth/logout", headers=alice).json()["revoked"] == 1
        assert client.get("/profile", headers=alice).status_code == 401
        assert client.post("/auth/logout", headers=alice).status_code == 401

    # Expiry is enforced from the stored timestamp.
    store = ApplicationStore(db)
    auth = LocalAuthStore(store)
    session = auth.issue_token("alice")
    assert auth.resolve_token(session.token) is not None
    store.save_auth_session(
        token_hash=__import__("hashlib").sha256(session.token.encode()).hexdigest(),
        profile_id="alice",
        created_at=session.created_at,
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    assert auth.resolve_token(session.token) is None
    assert store.purge_expired_auth_sessions() >= 1

    # logout everywhere revokes every token for the profile.
    with TestClient(create_app(application_store=ApplicationStore(db))) as client:
        first = _login(client, "alice")
        second = _login(client, "alice")
        assert client.post("/auth/logout", params={"everywhere": True}, headers=first).json()["revoked"] == 2
        assert client.get("/profile", headers=second).status_code == 401


def test_password_change_requires_current_password_or_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    with TestClient(create_app(application_store=ApplicationStore(tmp_path / "pw.db"))) as client:
        _login(client, "alice", "first-password!")
        hijack = client.post("/auth/login", json={"profile_id": "alice", "password": "attacker-pass", "set_password": True})
        assert hijack.status_code == 401
        assert client.post("/auth/login", json={"profile_id": "alice", "password": "first-password!"}).status_code == 200
        # An authenticated owner may rotate.
        alice = _login(client, "alice", "first-password!")
        rotated = client.post("/auth/login", json={"profile_id": "alice", "password": "second-password!", "set_password": True}, headers=alice)
        assert rotated.status_code == 200
        assert client.post("/auth/login", json={"profile_id": "alice", "password": "second-password!"}).status_code == 200


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------


def test_profile_routes_never_expose_another_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    store = ApplicationStore(tmp_path / "own.db")
    bob = store.get_candidate_profile("bob")
    bob.full_name = "Bob Secret"
    bob.email = "bob@example.test"
    store.save_candidate_profile(bob)
    with TestClient(create_app(application_store=store)) as client:
        alice = _login(client, "alice")
        # Query-string profile ids cannot reach Bob.
        assert client.get("/profile", params={"profile_id": "bob"}, headers=alice).status_code == 403
        assert client.get("/profile/view", params={"profile_id": "bob"}, headers=alice).status_code == 403
        assert client.get("/profile/setup-questions", params={"profile_id": "bob"}, headers=alice).status_code == 403
        assert client.get("/profile/resume", params={"profile_id": "bob"}, headers=alice).status_code == 403
        assert client.get("/profile/answers", params={"profile_id": "bob"}, headers=alice).status_code == 403
        assert client.patch("/profile", params={"profile_id": "bob"}, json={"full_name": "Pwned"}, headers=alice).status_code == 403
        # Whole-profile PUT cannot overwrite Bob.
        payload = client.get("/profile", headers=alice).json()
        payload["profile_id"] = "bob"
        payload["full_name"] = "Pwned"
        assert client.put("/profile", json=payload, headers=alice).status_code == 403
        assert store.get_candidate_profile("bob").full_name == "Bob Secret"
        # The picker lists only the caller.
        assert [item["profile_id"] for item in client.get("/profiles", headers=alice).json()["profiles"]] == ["alice"]
        # Auth status never reveals whether another profile has a password.
        assert client.get("/auth/status", params={"profile_id": "bob"}, headers=alice).json()["profile_id"] == "alice"


def test_jobs_tailor_and_latex_sessions_are_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    store = ApplicationStore(tmp_path / "scoped.db")
    _job(store, "public-job")
    _job(store, "bobs-job", captured_for="bob")
    bob_profile = store.get_candidate_profile("bob")
    bob_profile.resume_latex_source = SAMPLE_TEX.read_text(encoding="utf-8")
    store.save_candidate_profile(bob_profile)
    with TestClient(create_app(application_store=store)) as client:
        alice = _login(client, "alice")
        bob = _login(client, "bob")
        assert client.get("/jobs/public-job", headers=alice).status_code == 200
        assert client.get("/jobs/bobs-job", headers=alice).status_code == 404
        assert client.get("/jobs/bobs-job", headers=bob).status_code == 200

        # Tailor sessions belong to whoever opened them.
        created = client.post("/tailor/sessions", json={"job_id": "bobs-job"}, headers=bob)
        assert created.status_code == 200, created.text
        session_id = created.json()["session_id"]
        assert client.get(f"/tailor/sessions/{session_id}", headers=bob).status_code == 200
        assert client.get(f"/tailor/sessions/{session_id}", headers=alice).status_code == 404
        assert client.patch(f"/tailor/sessions/{session_id}", json={"confirmed_skills": ["Python"]}, headers=alice).status_code == 404
        # Nobody can open a session as someone else by body.
        assert client.post("/tailor/sessions", json={"job_id": "bobs-job", "profile_id": "bob"}, headers=alice).status_code == 403

        # Classic /latex sessions too.
        upload = client.post("/latex/upload", files={"file": ("r.tex", SAMPLE_TEX.read_bytes(), "text/plain")}, headers=bob)
        assert upload.status_code == 200, upload.text
        latex_session = upload.json()["session_id"]
        assert client.get(f"/latex/{latex_session}/status", headers=bob).status_code == 200
        assert client.get(f"/latex/{latex_session}/status", headers=alice).status_code == 404
        assert client.delete(f"/latex/{latex_session}", headers=alice).status_code == 404
        assert client.delete(f"/latex/{latex_session}", headers=bob).status_code == 204


def test_local_mode_keeps_header_scoping_and_legacy_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLYTEX_REQUIRE_AUTH", raising=False)
    store = ApplicationStore(tmp_path / "local.db")
    _job(store, "job-1")
    with TestClient(create_app(application_store=store)) as client:
        # Without auth, X-Profile-Id is the documented local scoping mechanism.
        assert client.get("/profile", headers={"X-Profile-Id": "alice"}).json()["profile_id"] == "alice"
        assert client.get("/profile", params={"profile_id": "bob"}).json()["profile_id"] == "bob"
        assert len(client.get("/profiles").json()["profiles"]) >= 2


def test_rate_limit_key_prefers_profile_over_ip() -> None:
    class _Req:
        def __init__(self, bound: str | None, header: str | None) -> None:
            self.state = type("S", (), {})()
            if bound:
                self.state.auth_profile_id = bound
            self.headers = {"x-profile-id": header} if header else {}
            self.client = type("C", (), {"host": "10.0.0.1"})()

    assert rate_limit_key(_Req("alice", "mallory")) == "profile:alice"  # type: ignore[arg-type]
    assert rate_limit_key(_Req(None, "bob")) == "profile:bob"  # type: ignore[arg-type]
    assert rate_limit_key(_Req(None, None)) == "ip:10.0.0.1"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# export / delete
# ---------------------------------------------------------------------------


def test_export_and_delete_remove_everything_the_profile_owns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_REQUIRE_AUTH", "1")
    store = ApplicationStore(tmp_path / "lifecycle.db")
    _job(store, "job-1")
    _job(store, "job-2")
    _job(store, "bobs-job", captured_for="bob")
    with TestClient(create_app(application_store=store)) as client:
        alice = _login(client, "alice")
        bob = _login(client, "bob")
        profile = client.get("/profile", headers=alice).json()
        profile["full_name"] = "Alice Example"
        client.put("/profile", json=profile, headers=alice)
        client.post("/profile/answers", json={"prompt_text": "Security clearance", "value": "None"}, headers=alice)
        client.post("/watchlist", json={"entries": [{"provider": "greenhouse", "board_token": "acme", "company": "Acme"}]}, headers=alice)
        application_id = client.post("/applications", json={"job_id": "job-1"}, headers=alice).json()["application_id"]
        client.post(f"/applications/{application_id}/submission", json={}, headers=alice)
        second_id = client.post("/applications", json={"job_id": "job-2"}, headers=alice).json()["application_id"]
        assert client.post(f"/applications/{second_id}/apply-runs", json={}, headers=alice).status_code == 200
        bob_application = client.post("/applications", json={"job_id": "bobs-job"}, headers=bob).json()["application_id"]

        export = client.get("/profile/export", headers=alice)
        assert export.status_code == 200
        data = export.json()
        assert data["format"] == "applytex-profile-export" and data["profile"]["full_name"] == "Alice Example"
        assert "resume_pdf_b64" not in data["profile"]
        assert [answer["prompt_text"] for answer in data["answers"]] == ["Security clearance"]
        assert len(data["watchlist"]) == 1 and len(data["applications"]) == 2 and len(data["apply_runs"]) == 1
        assert any(item["submission"] for item in data["applications"])
        assert all(item["application"]["profile_id"] == "alice" for item in data["applications"])

        assert client.delete("/profile", params={"confirm": "wrong"}, headers=alice).status_code == 409
        deleted = client.delete("/profile", params={"confirm": "alice"}, headers=alice)
        assert deleted.status_code == 200, deleted.text
        removed = deleted.json()["removed"]
        assert removed["applications"] == 2 and removed["profile_answers"] == 1 and removed["watchlist_entries"] == 1
        assert removed["submission_bundles"] == 1 and removed["apply_runs"] == 1 and removed["candidate_profiles"] == 1
        # The token died with the profile; Bob is untouched.
        assert client.get("/profile", headers=alice).status_code == 401
        assert client.get(f"/applications/{bob_application}", headers=bob).status_code == 200
        assert store.list_applications(limit=100, profile_id="alice") == []
        assert store.get_candidate_profile("bob").profile_id == "bob"
        assert store.candidate_profile_exists("alice") is False


def test_schema_migrations_are_recorded(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "migrate.db")
    assert store.schema_version() == 1
    # Re-opening the same database does not re-apply anything.
    assert ApplicationStore(tmp_path / "migrate.db").schema_version() == 1
