"""Required attachments must not be mistaken for resolved text answers."""

from pathlib import Path

from fastapi.testclient import TestClient

from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.form_resolution import resolve_form_questions
from latex_resume.job_models import CandidateProfile, FormQuestion


def test_document_fields_do_not_reuse_resume_or_text_answers() -> None:
    profile = CandidateProfile(
        resume_latex_source="sample resume",
        custom_answers={"Cover Letter": "My saved letter", "Transcript": "My transcript"},
    )
    labels = ["Resume/CV", "Curriculum Vitae", "Cover Letter", "Resume and cover letter", "Transcript"]
    questions = [FormQuestion(field_id=str(i), label=label, input_type="file", required=True)
                 for i, label in enumerate(labels)]
    actions = resolve_form_questions(questions, profile, employment_track="internship")
    assert [action.action for action in actions] == ["upload", "upload", "skip", "skip", "skip"]
    assert all(action.value is None for action in actions)


def test_required_cover_letter_stays_missing_until_a_file_is_selected(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "documents.db"))
    question = {"field_id": "attachment", "label": "Cover Letter*", "input_type": "file", "required": True}
    scan_body = {"provider": "greenhouse", "page_url": "https://job-boards.greenhouse.io/example", "questions": [question]}
    with TestClient(app) as client:
        scan = client.post("/extension/forms/scan", json=scan_body)
        assert scan.status_code == 200
        plan_url = f"/extension/forms/{scan.json()['scan_id']}/plan"
        initial = client.get(plan_url).json()
        assert initial["unresolved_required"] == ["Cover Letter*"]
        override = client.post(plan_url, json={"overrides": {"attachment": "A typed answer is not a file"}})
        assert override.status_code == 200
        assert override.json()["unresolved_required"] == ["Cover Letter*"]
        assert override.json()["actions"][0]["action"] == "skip"
        question.update(current_value_present=True, current_value="letter.pdf")
        uploaded_scan = client.post("/extension/forms/scan", json=scan_body)
        uploaded = client.get(f"/extension/forms/{uploaded_scan.json()['scan_id']}/plan").json()
        assert uploaded["unresolved_required"] == []
        assert uploaded["review_items"][0]["answer_source"] == "already_on_page"
        assert uploaded["review_items"][0]["change_kind"] == "keep"
