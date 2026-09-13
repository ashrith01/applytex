"""The demonstration must not use the real profile or external generation."""

from pathlib import Path

from fastapi.testclient import TestClient

from scripts.autofill_lab import PROFILE_ID, create_lab


def test_lab_uses_isolated_synthetic_profile_and_blocks_external_operations(tmp_path: Path) -> None:
    with TestClient(create_lab(tmp_path / "isolated-lab.db")) as client:
        profile = client.get(f"/profile?profile_id={PROFILE_ID}").json()
        assert profile["email"] == "avery@example.test"
        assert profile["equal_opportunity"]["allow_autofill"] is False
        assert profile["work_authorization"]["current_requires_sponsorship"] is False
        assert profile["work_authorization"]["future_requires_sponsorship"] is True
        assert client.post("/extension/resume/prepare", json={}).status_code == 403
        assert client.post("/extension/resume/prepare", json={"customize": True}).status_code == 403
        assert client.post("/extension/forms/fake/answer-draft", json={}).status_code == 403
        assert client.post("/profile/projects/sync/github", json={}).status_code == 403
        assert client.post("/auth/login", json={}).status_code == 403
        assert client.post("/jobs/search", json={}).status_code == 403
        response = client.get("/lab/greenhouse/edge-cases")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
