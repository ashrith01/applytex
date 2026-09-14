"""Phase 3: submission receipts, auto-advance, follow-ups, cover letters."""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from latex_resume import cover_letters
from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore, InvalidApplicationTransition
from latex_resume.cover_letters import cover_letter_latex, validate_letter
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationStatus,
    CandidateProfile,
    JobPosting,
    JobProvider,
)
from latex_resume.submission import advance_application, transition_path

SAMPLE_TEX = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


def _job(store: ApplicationStore, job_id: str = "job-1") -> JobPosting:
    job = JobPosting(
        job_id=job_id,
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id="1",
        company="Acme Robotics",
        title="Machine Learning Engineer",
        description="We need Python, PyTorch, and 3 years of ML experience. 40% remote.",
        location="Remote - US",
        source_url="https://boards.greenhouse.io/acme/jobs/1",
        apply_url="https://boards.greenhouse.io/acme/jobs/1#app",
    )
    store.save_job(job)
    return job


def _scan(client: TestClient, application_id: str, step_key: str, questions: list[dict[str, Any]]) -> str:
    response = client.post(
        "/extension/forms/scan",
        json={
            "application_id": application_id,
            "provider": "greenhouse",
            "page_url": f"https://boards.greenhouse.io/acme/jobs/1/{step_key}",
            "step_key": step_key,
            "questions": questions,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scan_id"]


# ---------------------------------------------------------------------------
# state machine walking
# ---------------------------------------------------------------------------


def test_transition_path_walks_through_the_approval_gate() -> None:
    assert transition_path(ApplicationStatus.FORM_SCANNED, ApplicationStatus.SUBMITTED) == [
        ApplicationStatus.READY_FOR_REVIEW,
        ApplicationStatus.APPROVED,
        ApplicationStatus.SUBMITTING,
        ApplicationStatus.SUBMITTED,
    ]
    assert transition_path(ApplicationStatus.DISCOVERED, ApplicationStatus.SUBMITTED)[:2] == [
        ApplicationStatus.SELECTED,
        ApplicationStatus.RESUME_READY,
    ]
    assert transition_path(ApplicationStatus.SUBMITTED, ApplicationStatus.SUBMITTED) == []
    assert transition_path(ApplicationStatus.SKIPPED, ApplicationStatus.SUBMITTED) is None


def test_advance_application_records_every_intermediate_status(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "adv.db")
    _job(store)
    application = store.create_application("job-1")
    advanced = advance_application(store, application.application_id, ApplicationStatus.READY_FOR_REVIEW)
    assert advanced.status is ApplicationStatus.READY_FOR_REVIEW
    statuses = [
        event.payload.get("status")
        for event in reversed(store.list_application_events(application.application_id))
        if event.kind == "status_changed"
    ]
    assert statuses == ["selected", "resume_ready", "form_scanned", "ready_for_review"]
    store.transition_application(application.application_id, ApplicationStatus.SKIPPED)
    with pytest.raises(InvalidApplicationTransition):
        advance_application(store, application.application_id, ApplicationStatus.SUBMITTED)


# ---------------------------------------------------------------------------
# submission receipt
# ---------------------------------------------------------------------------


def test_confirm_submission_freezes_fields_files_and_schedules_follow_up(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "receipt.db")
    _job(store)
    profile = store.get_candidate_profile("default")
    profile.full_name = "Avery Morgan"
    profile.email = "avery@example.test"
    profile.resume_pdf_filename = "avery.pdf"
    profile.resume_pdf_sha256 = "abc123"
    store.save_candidate_profile(profile)
    app = create_app(application_store=store)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        step1 = _scan(
            client,
            application_id,
            "contact",
            [
                {"field_id": "name", "label": "Full name", "input_type": "text", "required": True},
                {"field_id": "email", "label": "Email", "input_type": "email", "required": True, "current_value_present": True, "current_value": "typed@example.test"},
                {"field_id": "resume", "label": "Resume", "input_type": "file", "required": True, "current_value_present": True},
            ],
        )
        client.post(f"/extension/forms/{step1}/plan", json={"overrides": {"name": "Avery M."}})
        # A later rescan of the same step supersedes the earlier one.
        step1b = _scan(
            client,
            application_id,
            "contact",
            [
                {"field_id": "name", "label": "Full name", "input_type": "text", "required": True, "current_value_present": True, "current_value": "Avery Morgan"},
                {"field_id": "email", "label": "Email", "input_type": "email", "required": True, "current_value_present": True, "current_value": "typed@example.test"},
                {"field_id": "resume", "label": "Resume", "input_type": "file", "required": True, "current_value_present": True},
            ],
        )
        assert step1b != step1
        _scan(
            client,
            application_id,
            "questions",
            [
                {"field_id": "hear", "label": "How did you hear about us?", "input_type": "select", "options": ["LinkedIn", "Referral"], "required": True},
            ],
        )
        client.post(
            f"/extension/forms/{client.get(f'/applications/{application_id}').json()['latest_form_scan']['scan_id']}/plan",
            json={"overrides": {"hear": "Referral"}},
        )

        assert client.get(f"/applications/{application_id}/submission").status_code == 404
        response = client.post(
            f"/applications/{application_id}/submission",
            json={"confirmed_by": "detected_confirmed", "detection_evidence": "Thank you for applying"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["application"]["status"] == "submitted"
        assert body["application"]["submitted_at"]
        bundle = body["bundle"]
        assert body["application"]["submission_bundle_id"] == bundle["bundle_id"]
        assert bundle["confirmed_by"] == "detected_confirmed"
        assert bundle["company"] == "Acme Robotics" and bundle["job_description_sha256"]
        assert bundle["resume_origin"] == "profile_resume" and bundle["resume_sha256"] == "abc123"
        assert [step["step_key"] for step in bundle["steps"]] == ["contact", "questions"]
        fields = {field["field_id"]: field for field in bundle["fields"]}
        assert fields["name"]["value"] == "Avery Morgan", "the later scan's page value wins"
        assert fields["email"]["value"] == "typed@example.test" and fields["email"]["source"] == "already_on_page"
        assert fields["resume"]["value"] == "attached"
        assert fields["hear"]["value"] == "Referral" and fields["hear"]["source"] == "user_input"

        # Status walked through the approval gate, one event per hop.
        events = client.get(f"/applications/{application_id}").json()["events"]
        hops = [event["payload"].get("status") for event in reversed(events) if event["kind"] == "status_changed"]
        assert hops[-4:] == ["ready_for_review", "approved", "submitting", "submitted"]
        assert any(event["kind"] == "submission_confirmed" for event in events)

        # Follow-up scheduled once, due in a week; visible in the due list with a horizon.
        tasks = client.get(f"/applications/{application_id}").json()["tasks"]
        follow_ups = [task for task in tasks if task["category"] == "follow_up"]
        assert len(follow_ups) == 1 and follow_ups[0]["due_at"]
        assert client.get("/applications/tasks/due").json() == []
        due = client.get("/applications/tasks/due", params={"within_days": 8}).json()
        assert [item["task"]["task_id"] for item in due] == [follow_ups[0]["task_id"]]
        assert due[0]["application"]["application_id"] == application_id
        done = client.post(f"/applications/{application_id}/tasks/{follow_ups[0]['task_id']}/complete")
        assert done.status_code == 200 and done.json()["status"] == "done"
        assert client.get("/applications/tasks/due", params={"within_days": 8}).json() == []

        # Confirming again is idempotent and the receipt is immutable.
        again = client.post(f"/applications/{application_id}/submission", json={})
        assert again.status_code == 200 and again.json()["bundle"]["bundle_id"] == bundle["bundle_id"]
        profile_after = client.get("/profile").json()
        profile_after["full_name"] = "Someone Else"
        client.put("/profile", json=profile_after)
        assert client.get(f"/applications/{application_id}/submission").json()["fields"] == bundle["fields"]


def test_receipt_prefers_the_approved_tailored_resume(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "receipt-artifact.db")
    _job(store)
    app = create_app(application_store=store)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        store.save_application_artifact(
            ApplicationArtifact(
                artifact_id="art-1",
                application_id=application_id,
                job_id="job-1",
                type=ApplicationArtifactType.TAILORED_RESUME,
                status=ApplicationArtifactStatus.APPROVED,
                filename="acme_tailored.pdf",
                pdf_sha256="deadbeef",
            )
        )
        bundle = client.post(f"/applications/{application_id}/submission", json={"notes": "sent via portal"}).json()["bundle"]
        assert bundle["resume_origin"] == "tailored_artifact"
        assert bundle["resume_artifact_id"] == "art-1" and bundle["resume_sha256"] == "deadbeef"
        assert bundle["notes"] == "sent via portal" and bundle["steps"] == []


def test_submission_is_scoped_to_the_owning_profile(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "scope.db")
    _job(store)
    app = create_app(application_store=store)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}, headers={"X-Profile-Id": "alice"}).json()["application_id"]
        assert client.post(f"/applications/{application_id}/submission", json={}, headers={"X-Profile-Id": "bob"}).status_code == 404
        assert client.get(f"/applications/{application_id}/submission", headers={"X-Profile-Id": "bob"}).status_code == 404


# ---------------------------------------------------------------------------
# fill result auto-advance
# ---------------------------------------------------------------------------


def test_fill_result_advances_to_ready_for_review_or_needs_input_only_forward(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "fill.db")
    _job(store)
    profile = store.get_candidate_profile("default")
    profile.full_name = "Avery Morgan"
    store.save_candidate_profile(profile)
    app = create_app(application_store=store)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        scan_id = _scan(
            client,
            application_id,
            "contact",
            [
                {"field_id": "name", "label": "Full name", "input_type": "text", "required": True},
                {"field_id": "hear", "label": "How did you hear about us?", "input_type": "select", "options": ["LinkedIn"], "required": True},
            ],
        )
        needs = client.post(f"/extension/forms/{scan_id}/fill-result", json={"filled": 1, "skipped": 1})
        assert needs.status_code == 200, needs.text
        assert needs.json()["status"] == "needs_input"
        assert needs.json()["unresolved_required"] == ["How did you hear about us?"]

        client.post(f"/extension/forms/{scan_id}/plan", json={"overrides": {"hear": "LinkedIn"}})
        ready = client.post(f"/extension/forms/{scan_id}/fill-result", json={"filled": 2, "skipped": 0})
        assert ready.json()["status"] == "ready_for_review" and ready.json()["unresolved_required"] == []

        # A later state is never regressed by another fill.
        client.post(f"/applications/{application_id}/transition", json={"status": "approved"})
        after = client.post(f"/extension/forms/{scan_id}/fill-result", json={"filled": 2, "skipped": 0})
        assert after.json()["status"] == "approved"
        events = client.get(f"/applications/{application_id}").json()["events"]
        assert sum(1 for event in events if event["kind"] == "fill_completed") == 3


# ---------------------------------------------------------------------------
# cover letters
# ---------------------------------------------------------------------------


GOOD_LETTER = (
    "I am applying for the Machine Learning Engineer role at Acme Robotics because your work on autonomous "
    "systems matches the production ML experience on my resume. " * 2
    + "In my current role I built model training pipelines in Python and PyTorch, owned evaluation, and "
    "shipped models that other teams depend on every day. I care about reliability and clear tradeoffs. " * 2
    + "Earlier, I led a small team through a migration to a modern feature store and wrote the runbooks that "
    "kept it stable. I would bring the same discipline to your perception stack. " * 2
    + "I would welcome the chance to discuss how my background fits the team's roadmap."
)


def test_validate_letter_rejects_unsupported_numbers_links_and_placeholders() -> None:
    resume = "Python PyTorch 3 years"
    jd = "40% remote"
    assert validate_letter(GOOD_LETTER, resume_text=resume, job_description=jd) == []
    errors = validate_letter(GOOD_LETTER + " I improved latency by 57% for 12 teams.", resume_text=resume, job_description=jd)
    assert any("numbers not found" in error and "57%" in error for error in errors)
    assert any("link" in error for error in validate_letter(GOOD_LETTER + " See https://example.test", resume_text=resume, job_description=jd))
    assert any("placeholder" in error for error in validate_letter(GOOD_LETTER + " Sincerely, [Your Name]", resume_text=resume, job_description=jd))
    assert any("too short" in error for error in validate_letter("Hire me.", resume_text=resume, job_description=jd))


def test_cover_letter_draft_edit_approve_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApplicationStore(tmp_path / "letter.db")
    _job(store)
    profile = store.get_candidate_profile("default")
    profile.full_name = "Avery Morgan"
    profile.email = "avery@example.test"
    profile.resume_latex_source = SAMPLE_TEX.read_text(encoding="utf-8")
    profile.resume_filename = "sample_resume.tex"
    store.save_candidate_profile(profile)
    app = create_app(application_store=store)
    prompts: list[str] = []

    async def fake_complete(prompt: str, **kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"letter": GOOD_LETTER, "evidence": ["Python and PyTorch pipelines"]}

    monkeypatch.setattr(cover_letters, "complete_json", fake_complete)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        assert client.get(f"/applications/{application_id}/cover-letter").status_code == 404

        drafted = client.post(f"/applications/{application_id}/cover-letter")
        assert drafted.status_code == 200, drafted.text
        assert "Acme Robotics" in prompts[0] and "Machine Learning Engineer" in prompts[0]
        artifact = drafted.json()["artifact"]
        summary = drafted.json()["summary"]
        assert artifact["type"] == "cover_letter" and artifact["status"] == "generated"
        assert artifact["mime_type"] == "text/plain" and artifact["filename"] == "Acme_Robotics_cover_letter.txt"
        assert summary["word_count"] >= 150 and summary["evidence_notes"] == ["Python and PyTorch pipelines"]
        assert client.get(f"/applications/{application_id}").json()["application"]["cover_letter_artifact_id"] is None

        # Edits are validated too.
        bad = client.patch(f"/applications/{application_id}/cover-letter/{artifact['artifact_id']}", json={"text": GOOD_LETTER + " I cut costs 99%."})
        assert bad.status_code == 409 and "99%" in bad.json()["detail"]
        edited = client.patch(f"/applications/{application_id}/cover-letter/{artifact['artifact_id']}", json={"text": GOOD_LETTER + "\n\nBest regards."})
        assert edited.status_code == 200 and edited.json()["artifact"]["text_content"].endswith("Best regards.")

        approved = client.post(f"/applications/{application_id}/cover-letter/{artifact['artifact_id']}/approve")
        assert approved.status_code == 200, approved.text
        approved_artifact = approved.json()["artifact"]
        assert approved_artifact["status"] == "approved" and approved_artifact["approved_at"]
        assert "\\documentclass" in approved_artifact["latex_source"]
        if shutil.which("pdflatex"):
            assert approved_artifact["mime_type"] == "application/pdf" and approved_artifact["pdf_sha256"]
            assert approved_artifact["filename"].endswith(".pdf") and approved_artifact["page_count"] == 1
        else:
            assert approved_artifact["mime_type"] == "text/plain" and approved_artifact["warnings"]
        assert client.get(f"/applications/{application_id}").json()["application"]["cover_letter_artifact_id"] == artifact["artifact_id"]

        file_response = client.get(f"/applications/{application_id}/artifacts/{artifact['artifact_id']}/file")
        assert file_response.status_code == 200
        payload = file_response.json()
        assert payload["mime_type"] == approved_artifact["mime_type"]
        raw = base64.b64decode(payload["data_b64"])
        assert raw.startswith(b"%PDF") if payload["mime_type"] == "application/pdf" else b"Acme Robotics" in raw

        # The receipt records the approved letter.
        bundle = client.post(f"/applications/{application_id}/submission", json={}).json()["bundle"]
        assert bundle["cover_letter_artifact_id"] == artifact["artifact_id"]
        assert bundle["cover_letter_sha256"] and bundle["cover_letter_filename"] == approved_artifact["filename"]


def test_cover_letter_requires_a_resume_and_rejects_ungrounded_drafts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApplicationStore(tmp_path / "letter-guard.db")
    _job(store)
    app = create_app(application_store=store)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        assert client.post(f"/applications/{application_id}/cover-letter").status_code == 409

    profile = store.get_candidate_profile("default")
    profile.resume_latex_source = SAMPLE_TEX.read_text(encoding="utf-8")
    store.save_candidate_profile(profile)

    async def ungrounded(prompt: str, **kwargs: Any) -> dict[str, Any]:
        return {"letter": GOOD_LETTER + " I raised revenue 300% across 17 markets.", "evidence": []}

    monkeypatch.setattr(cover_letters, "complete_json", ungrounded)
    with TestClient(app) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        rejected = client.post(f"/applications/{application_id}/cover-letter")
        assert rejected.status_code == 409 and "300%" in rejected.json()["detail"]


def test_cover_letter_latex_escapes_and_structures() -> None:
    profile = CandidateProfile(full_name="Avery & Co", email="a@b.test")
    from latex_resume.job_models import ApplicationRecord

    application = ApplicationRecord(application_id="a", job_id="j", company="Acme 100% Robotics", job_title="ML_Engineer")
    latex = cover_letter_latex("First paragraph with 50% and #tags.\n\nSecond paragraph.", profile=profile, application=application)
    assert r"Avery \& Co" in latex and r"Acme 100\% Robotics" in latex and r"ML\_Engineer" in latex
    assert r"50\% and \#tags" in latex and latex.count("\n\n") >= 1 and r"\end{document}" in latex


def test_cover_letter_from_user_text_skips_the_model_but_not_the_validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApplicationStore(tmp_path / "letter-text.db")
    _job(store)
    profile = store.get_candidate_profile("default")
    profile.resume_latex_source = SAMPLE_TEX.read_text(encoding="utf-8")
    store.save_candidate_profile(profile)

    async def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("no model call for user-written text")

    monkeypatch.setattr(cover_letters, "complete_json", explode)
    with TestClient(create_app(application_store=store)) as client:
        application_id = client.post("/applications", json={"job_id": "job-1"}).json()["application_id"]
        rejected = client.post(f"/applications/{application_id}/cover-letter", json={"text": GOOD_LETTER + " I grew revenue by 500%."})
        assert rejected.status_code == 409 and "500%" in rejected.json()["detail"]
        created = client.post(f"/applications/{application_id}/cover-letter", json={"text": GOOD_LETTER})
        assert created.status_code == 200, created.text
        assert created.json()["artifact"]["evidence_notes"] == ["Written by the candidate."]
        assert created.json()["artifact"]["status"] == "generated"
