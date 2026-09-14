"""Executor run protocol: policy, approval gate, and the user/executor split."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore, InvalidApplyRunTransition
from latex_resume.apply_runs import ApplyRunPolicyError, enqueue_apply_run
from latex_resume.job_models import ApplyRun, JobPosting, JobProvider

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + bytes(range(64))
).decode()


def _job(store: ApplicationStore, job_id: str = "job-1", provider: JobProvider = JobProvider.GREENHOUSE) -> JobPosting:
    job = JobPosting(
        job_id=job_id,
        provider=provider,
        board_token="acme",
        external_id=job_id,
        company="Acme",
        title="ML Engineer",
        description="Python and PyTorch. " * 12,
        source_url=f"https://example.test/{job_id}",
        apply_url=f"https://example.test/{job_id}/apply",
    )
    store.save_job(job)
    return job


def _client(tmp_path: Path, name: str) -> tuple[TestClient, ApplicationStore]:
    store = ApplicationStore(tmp_path / f"{name}.db")
    return TestClient(create_app(application_store=store)), store


def test_enqueue_policy_provider_active_run_and_daily_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApplicationStore(tmp_path / "policy.db")
    job = _job(store)
    workday = _job(store, "job-wd", JobProvider.WORKDAY)
    application = store.create_application("job-1")
    experimental = store.create_application("job-wd")

    with pytest.raises(ApplyRunPolicyError, match="not a verified executor provider"):
        enqueue_apply_run(store, application=experimental, job=workday, profile_id="default")
    run = enqueue_apply_run(store, application=experimental, job=workday, profile_id="default", allow_experimental_provider=True)
    assert run.provider == "workday" and run.status == "queued"

    first = enqueue_apply_run(store, application=application, job=job, profile_id="default")
    with pytest.raises(ApplyRunPolicyError, match="already queued"):
        enqueue_apply_run(store, application=application, job=job, profile_id="default")
    store.transition_apply_run(first.run_id, "cancelled", actor="user")
    monkeypatch.setenv("APPLYTEX_EXECUTOR_DAILY_CAP", "1")
    with pytest.raises(ApplyRunPolicyError, match="Daily executor cap"):
        enqueue_apply_run(store, application=application, job=job, profile_id="default")
    monkeypatch.setenv("APPLYTEX_EXECUTOR_DAILY_CAP", "0")
    assert enqueue_apply_run(store, application=application, job=job, profile_id="default").status == "queued"


def test_transition_protocol_separates_user_and_executor(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "protocol.db")
    _job(store)
    application = store.create_application("job-1")
    run = store.create_apply_run(ApplyRun(run_id="r1", application_id=application.application_id, job_id="job-1", provider="greenhouse", apply_url="https://example.test/apply"))

    with pytest.raises(InvalidApplyRunTransition, match="Only the executor"):
        store.transition_apply_run("r1", "running", actor="user")
    running = store.transition_apply_run("r1", "running", actor="executor", log_message="claimed")
    assert running.started_at and running.step_log[-1].message == "claimed"
    with pytest.raises(InvalidApplyRunTransition, match="Only the user"):
        store.transition_apply_run("r1", "cancelled", actor="executor")
    with pytest.raises(InvalidApplyRunTransition, match="Cannot move"):
        store.transition_apply_run("r1", "submitted", actor="executor")
    paused = store.transition_apply_run("r1", "paused_for_review", actor="executor", expected_status="running")
    with pytest.raises(InvalidApplyRunTransition, match="expected"):
        store.transition_apply_run("r1", "approved", actor="user", expected_status="running")
    assert paused.status == "paused_for_review"
    with pytest.raises(InvalidApplyRunTransition, match="Only the user"):
        store.transition_apply_run("r1", "approved", actor="executor")
    approved = store.transition_apply_run("r1", "approved", actor="user")
    submitting = store.transition_apply_run("r1", "submitting", actor="executor")
    assert submitting.finished_at is None
    done = store.transition_apply_run("r1", "submitted", actor="executor")
    assert done.finished_at
    assert store.list_apply_runs("default", statuses=["submitted"])[0].run_id == "r1"
    assert store.count_apply_runs_since("default", "2000-01-01T00:00:00+00:00") == 1


def test_api_flow_enqueue_claim_pause_approve_submit(tmp_path: Path) -> None:
    client, store = _client(tmp_path, "flow")
    _job(store)
    with client:
        profile = client.get("/profile").json()
        profile["full_name"] = "Avery Morgan"
        client.put("/profile", json=profile)
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        run = client.post(f"/applications/{application_id}/apply-runs", json={})
        assert run.status_code == 200, run.text
        run_id = run.json()["run_id"]
        assert client.post(f"/applications/{application_id}/apply-runs", json={}).status_code == 409
        assert [item["run_id"] for item in client.get("/apply-runs", params={"status": ["queued"]}).json()] == [run_id]

        # Executor claims and reports progress.
        claim = client.post(f"/apply-runs/{run_id}/claim", json={"executor_id": "test"})
        assert claim.status_code == 200, claim.text
        assert claim.json()["run"]["status"] == "running" and claim.json()["approval_token"] is None
        assert claim.json()["job"]["job_id"] == "job-1"
        assert client.post(f"/apply-runs/{run_id}/claim", json={}).status_code == 409

        # A scan + fill on the way (so the receipt has fields).
        scan = client.post(
            "/extension/forms/scan",
            json={"application_id": application_id, "provider": "greenhouse", "page_url": "https://example.test/job-1/apply",
                  "questions": [{"field_id": "name", "label": "Full name", "input_type": "text", "required": True}]},
        ).json()
        assert client.get(f"/extension/forms/{scan['scan_id']}/plan").json()["unresolved_required"] == []

        progress = client.patch(f"/apply-runs/{run_id}", json={"step": "fill-1", "message": "filled"})
        assert progress.json()["status"] == "running" and progress.json()["step_log"][-1]["message"] == "filled"
        paused = client.patch(
            f"/apply-runs/{run_id}",
            json={"status": "paused_for_review", "step": "review", "review_summary": {"ready": 1}, "screenshot_b64": PNG, "message": "ready"},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["screenshot_path"].startswith("runs/")
        shot = client.get(f"/apply-runs/{run_id}/screenshot")
        assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
        # Progress reports can never claim "submitted"; that needs the token route.
        assert client.patch(f"/apply-runs/{run_id}", json={"status": "submitted"}).status_code == 422

        # Nobody can record a submission before approval; the executor cannot approve.
        assert client.post(f"/apply-runs/{run_id}/submitted", json={"approval_token": "x"}).status_code == 409
        assert client.get(f"/applications/{application_id}").json()["application"]["status"] != "submitted"

        approved = client.post(f"/apply-runs/{run_id}/approve", json={"notes": "looks right"})
        assert approved.status_code == 200, approved.text
        token = approved.json()["approval_token"]
        assert token and approved.json()["approved_at"]

        claim2 = client.post(f"/apply-runs/{run_id}/claim", json={"executor_id": "test"})
        assert claim2.json()["run"]["status"] == "submitting" and claim2.json()["approval_token"] == token

        forged = client.post(f"/apply-runs/{run_id}/submitted", json={"approval_token": "forged", "detection_evidence": "x"})
        assert forged.status_code == 403
        assert client.get(f"/applications/{application_id}").json()["application"]["status"] != "submitted"

        done = client.post(f"/apply-runs/{run_id}/submitted", json={"approval_token": token, "detection_evidence": "Thank you for applying", "screenshot_b64": PNG})
        assert done.status_code == 200, done.text
        assert done.json()["run"]["status"] == "submitted" and done.json()["run"]["approval_token"] is None
        assert done.json()["application"]["status"] == "submitted"
        receipt = client.get(f"/applications/{application_id}/submission").json()
        assert receipt["bundle_id"] == done.json()["bundle_id"]
        assert [field["field_id"] for field in receipt["fields"]] == ["name"]
        assert "executor" in receipt["notes"]
        events = [event["kind"] for event in client.get(f"/applications/{application_id}").json()["events"]]
        assert {"apply_run_queued", "apply_run_paused", "apply_run_approved", "submission_confirmed"} <= set(events)

        # A submitted application cannot be re-queued.
        assert client.post(f"/applications/{application_id}/apply-runs", json={}).status_code == 409


def test_awaiting_input_resume_cancel_and_needs_verification(tmp_path: Path) -> None:
    client, store = _client(tmp_path, "resume")
    _job(store)
    with client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        run_id = client.post(f"/applications/{application_id}/apply-runs", json={}).json()["run_id"]
        client.post(f"/apply-runs/{run_id}/claim", json={})
        waiting = client.patch(f"/apply-runs/{run_id}", json={"status": "awaiting_input", "unresolved_required": ["Desired salary"], "message": "need salary"})
        assert waiting.json()["status"] == "awaiting_input" and waiting.json()["unresolved_required"] == ["Desired salary"]
        assert client.post(f"/apply-runs/{run_id}/approve", json={}).status_code == 409
        resumed = client.post(f"/apply-runs/{run_id}/resume", json={})
        assert resumed.json()["status"] == "queued"
        client.post(f"/apply-runs/{run_id}/claim", json={})
        client.patch(f"/apply-runs/{run_id}", json={"status": "paused_for_review"})
        client.post(f"/apply-runs/{run_id}/approve", json={})
        client.post(f"/apply-runs/{run_id}/claim", json={})
        unverified = client.patch(f"/apply-runs/{run_id}", json={"status": "needs_verification", "message": "no confirmation seen"})
        assert unverified.json()["status"] == "needs_verification"
        # Without a confirmed application the run stays open ...
        assert client.post(f"/apply-runs/{run_id}/submitted", json={}).status_code == 409
        # ... until the user confirms through the normal path.
        assert client.post(f"/applications/{application_id}/submission", json={}).status_code == 200
        closed = client.post(f"/apply-runs/{run_id}/submitted", json={})
        assert closed.status_code == 200 and closed.json()["run"]["status"] == "submitted"

        other_id = client.post("/applications", json={"job_id": "job-1", "force_new": True}).json()["application_id"]
        other_run = client.post(f"/applications/{other_id}/apply-runs", json={}).json()["run_id"]
        cancelled = client.post(f"/apply-runs/{other_run}/cancel", json={"notes": "changed my mind"})
        assert cancelled.json()["status"] == "cancelled" and cancelled.json()["finished_at"]
        assert client.post(f"/apply-runs/{other_run}/claim", json={}).status_code == 409


def test_runs_are_scoped_to_profiles(tmp_path: Path) -> None:
    client, store = _client(tmp_path, "scope")
    _job(store)
    with client:
        application_id = client.post("/applications", json={"job_id": "job-1"}, headers={"X-Profile-Id": "alice"}).json()["application_id"]
        run_id = client.post(f"/applications/{application_id}/apply-runs", json={}, headers={"X-Profile-Id": "alice"}).json()["run_id"]
        assert client.get(f"/apply-runs/{run_id}", headers={"X-Profile-Id": "bob"}).status_code == 404
        assert client.get("/apply-runs", headers={"X-Profile-Id": "bob"}).json() == []
        assert client.post(f"/apply-runs/{run_id}/approve", json={}, headers={"X-Profile-Id": "bob"}).status_code == 404
        assert client.get(f"/apply-runs/{run_id}", headers={"X-Profile-Id": "alice"}).status_code == 200
