"""Ground-truth cases for selecting only answers supported by saved facts."""

from itertools import permutations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.form_resolution import resolve_form_questions
from latex_resume.job_models import CandidateProfile, FormQuestion
from latex_resume.option_matching import match_available_option


@pytest.mark.parametrize(("saved", "choices", "expected"), [
    ("Java", ["JavaScript", "Java"], "Java"),
    ("Java", ["JavaScript", "TypeScript"], None),
    ("No", ["Prefer not to answer", "No", "Yes"], "No"),
    ("No", ["None of the above", "Prefer not to answer"], None),
    ("Yes", ["Yes, with restrictions", "Yes, without restrictions", "No"], None),
    ("Texas", ["Texas", " texas "], None),
    ("", ["Yes", "No"], None),
    ("Nonbinary", ["Non-binary", "Female"], "Non-binary"),
    ("MS", ["Master of Science", "Bachelor of Science"], "Master of Science"),
    ("MS", ["Master of Science", "Master's degree"], None),
    ("Prefer not to say", ["Male", "Prefer not to answer"], "Prefer not to answer"),
    ("python", ["Python", "SQL"], "Python"),
    ("C", ["C++", "C#"], None),
    ("10", ["100", "1000"], None),
])
def test_option_order_does_not_change_answer(saved: str, choices: list[str], expected: str | None) -> None:
    for ordered in permutations(choices):
        result = match_available_option(saved, list(ordered))
        assert result.value == expected
        assert result.reason


@pytest.mark.parametrize(("label", "expected"), [
    ("Are you legally authorized to work in the United States?", "Yes"),
    ("Do you currently require sponsorship for work visa status?", "No"),
    ("Will you in the future require sponsorship for work visa status?", "Yes"),
    ("Will you now or in the future require sponsorship?", "Yes"),
    ("Are you willing to relocate?", "No"),
    ("Are you willing to travel?", "Yes"),
])
def test_eligibility_uses_explicit_independent_facts(label: str, expected: str) -> None:
    profile = CandidateProfile.model_validate({
        "work_authorization": {"authorized_to_work_in_us": True, "current_requires_sponsorship": False, "future_requires_sponsorship": True},
        "application_facts": {"willing_to_relocate": False, "willing_to_travel": True},
    })
    for options in permutations(["Prefer not to answer", "No", "Yes"]):
        question = FormQuestion(field_id="fact", label=label, input_type="select", options=list(options))
        action = resolve_form_questions([question], profile, employment_track="internship")[0]
        assert action.value == expected


def test_unknown_and_sensitive_facts_are_not_inferred_from_resume() -> None:
    profile = CandidateProfile.model_validate({
        "resume_latex_source": "Python and Rust developer. US university graduate.",
        "skills": ["Rust"],
        "equal_opportunity": {"allow_autofill": False, "gender": "Non-binary"},
    })
    questions = [
        FormQuestion(field_id="auth", label="Are you legally authorized to work in the United States?", input_type="select", options=["Yes", "No"]),
        FormQuestion(field_id="years", label="How many years of Rust experience do you have?", input_type="text"),
        FormQuestion(field_id="gender", label="Gender", input_type="select", options=["Non-binary", "Prefer not to answer"]),
    ]
    actions = resolve_form_questions(questions, profile, employment_track="internship")
    assert all(action.action == "skip" and action.value is None for action in actions)
    assert actions[2].answer_source == "eeo_opt_in"


def test_multi_select_requires_every_requested_answer_to_exist() -> None:
    profile = CandidateProfile(custom_answers={"Preferred languages": "Python; SQL"})
    question = FormQuestion(field_id="languages", label="Preferred languages", input_type="select", control_kind="multi_select", options=["Python", "Rust"])
    action = resolve_form_questions([question], profile, employment_track="internship")[0]
    assert action.action == "skip"
    question.options.append("SQL")
    action = resolve_form_questions([question], profile, employment_track="internship")[0]
    assert action.value == ["Python", "SQL"]


def test_api_validates_overrides_and_preserves_answers_already_on_page(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "options.db")
    store.save_candidate_profile(CandidateProfile(custom_answers={"Preferred programming language": "Java"}))
    with TestClient(create_app(application_store=store)) as client:
        scan = client.post("/extension/forms/scan", json={
            "provider": "greenhouse", "page_url": "https://example.test/apply",
            "questions": [{"field_id": "language", "label": "Preferred programming language", "input_type": "select", "required": True, "options": ["JavaScript", "TypeScript"]}],
        }).json()
        url = f"/extension/forms/{scan['scan_id']}/plan"
        initial = client.get(url).json()
        assert initial["actions"][0]["action"] == "skip"
        assert initial["unresolved_required"] == ["Preferred programming language"]
        assert "offered options" in initial["review_items"][0]["resolution_reason"]
        invalid = client.post(url, json={"overrides": {"language": "Java"}}).json()
        assert invalid["actions"][0]["action"] == "skip"
        valid = client.post(url, json={"overrides": {"language": "TypeScript"}}).json()
        assert valid["actions"][0]["value"] == "TypeScript"
        assert valid["unresolved_required"] == []
        scan["questions"][0].update(current_value="JavaScript", current_value_present=True)
        new_scan = client.post("/extension/forms/scan", json=scan).json()
        kept = client.get(f"/extension/forms/{new_scan['scan_id']}/plan").json()
        assert kept["review_items"][0]["change_kind"] == "keep"
        assert kept["review_items"][0]["current_value_preview"] == "JavaScript"


@pytest.mark.parametrize("replace_existing", [False, True])
def test_replacement_requires_explicit_scan_choice(tmp_path: Path, replace_existing: bool) -> None:
    store = ApplicationStore(tmp_path / "preserve.db")
    store.save_candidate_profile(CandidateProfile(first_name="Avery"))
    with TestClient(create_app(application_store=store)) as client:
        scan = client.post("/extension/forms/scan", json={
            "provider": "greenhouse", "page_url": "https://example.test/apply",
            "replace_existing": replace_existing,
            "questions": [{"field_id": "name", "label": "First name", "input_type": "text", "current_value": "Reviewed name", "current_value_present": True}],
        }).json()
        plan = client.get(f"/extension/forms/{scan['scan_id']}/plan").json()
        assert scan["replace_existing"] is replace_existing
        assert plan["review_items"][0]["change_kind"] == ("replace" if replace_existing else "keep")
        assert plan["actions"][0]["value"] == ("Avery" if replace_existing else None)
