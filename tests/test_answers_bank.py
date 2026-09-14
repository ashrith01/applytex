"""Answers bank: remembered answers resolve later forms; proposals stay review-gated."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from latex_resume import answer_proposals
from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.form_resolution import (
    boolean_from_answer_value,
    is_question_proposal_eligible,
    normalize_answer_prompt,
    profile_setup_status,
    remember_answer,
    resolve_form_questions,
)
from latex_resume.job_models import (
    CandidateProfile,
    FormQuestion,
    QuestionIntent,
    SavedAnswer,
)


def _question(field_id: str, label: str, **kwargs: Any) -> FormQuestion:
    return FormQuestion(field_id=field_id, label=label, input_type=kwargs.pop("input_type", "text"), **kwargs)


def _saved(prompt: str, value: str | bool | list[str], **kwargs: Any) -> SavedAnswer:
    return SavedAnswer(
        answer_id=kwargs.pop("answer_id", f"id-{prompt[:8]}"),
        prompt_text=prompt,
        normalized_prompt=normalize_answer_prompt(prompt),
        value=value,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# remember_answer
# ---------------------------------------------------------------------------


def test_remember_typed_boolean_becomes_a_profile_fact_not_a_bank_row() -> None:
    profile = CandidateProfile()
    question = _question("age", "Are you at least 18 years of age?", input_type="radio", options=["Yes", "No"])
    updated, saved = remember_answer(profile, question, "Yes")
    assert saved is None
    assert updated.application_facts.is_at_least_18 is True
    assert profile.application_facts.is_at_least_18 is None, "original profile is not mutated"

    relocate = _question("reloc", "Are you willing to relocate if required by the position?", input_type="select", options=["Yes", "No"])
    updated, saved = remember_answer(updated, relocate, "No")
    assert saved is None
    assert updated.application_facts.willing_to_relocate is False


def test_remember_free_text_question_creates_saved_answer_with_intent() -> None:
    profile = CandidateProfile(profile_id="p1")
    question = _question("hear", "How did you hear about us?", input_type="select", options=["LinkedIn", "Referral"])
    updated, saved = remember_answer(profile, question, "LinkedIn", provider="greenhouse")
    assert updated is profile
    assert saved is not None
    assert saved.profile_id == "p1"
    assert saved.prompt_text == "How did you hear about us?"
    assert saved.normalized_prompt == "how did you hear about us?"
    assert saved.ats_provider == "greenhouse"
    assert saved.intent == QuestionIntent.UNKNOWN


def test_remember_never_stores_sensitive_or_record_or_file_answers() -> None:
    profile = CandidateProfile()
    for question in (
        _question("gender", "Gender", input_type="select", options=["Woman", "Man"]),
        _question("vet", "Veteran status", sensitive=True),
        _question("school", "School", profile_record_kind="education"),
        _question("resume", "Resume", input_type="file"),
    ):
        updated, saved = remember_answer(profile, question, "anything")
        assert updated is profile
        assert saved is None


def test_boolean_from_answer_value_reads_yes_no_variants() -> None:
    assert boolean_from_answer_value("Yes") is True
    assert boolean_from_answer_value("yes, I am") is True
    assert boolean_from_answer_value("No") is False
    assert boolean_from_answer_value(["No"]) is False
    assert boolean_from_answer_value("Maybe") is None
    assert boolean_from_answer_value(["Yes", "No"]) is None


# ---------------------------------------------------------------------------
# resolution from saved answers
# ---------------------------------------------------------------------------


def test_saved_answer_resolves_by_exact_prompt_alias_and_intent() -> None:
    profile = CandidateProfile()
    saved = [
        _saved("How did you hear about us?", "LinkedIn", aliases=["where did you find this posting"]),
        _saved("Years of Rust experience", "0"),
        _saved("Willing to travel", "Yes", intent=QuestionIntent.TRAVEL),
    ]
    questions = [
        _question("hear", "How did you hear about us? *", input_type="select", options=["LinkedIn", "Indeed"]),
        _question("find", "Where did you find this posting", input_type="select", options=["LinkedIn", "Indeed"]),
        _question("rust", "How many years of Rust experience do you have?", required=True),
        _question("travel", "Are you willing to travel if required by the position?", input_type="radio", options=["Yes", "No"]),
    ]
    actions = resolve_form_questions(questions, profile, employment_track="full_time", saved_answers=saved)
    by_id = {action.field_id: action for action in actions}
    assert by_id["hear"].action == "select" and by_id["hear"].value == "LinkedIn"
    assert by_id["hear"].answer_source == "saved_answer"
    assert by_id["hear"].saved_answer_id == saved[0].answer_id
    assert by_id["find"].value == "LinkedIn", "alias match"
    assert by_id["rust"].action == "fill" and by_id["rust"].value == "0", "fuzzy token match"
    assert by_id["travel"].action == "select" and by_id["travel"].value == "Yes", "intent match"


def test_profile_fact_wins_over_saved_answer() -> None:
    profile = CandidateProfile()
    profile.application_facts.willing_to_travel = False
    saved = [_saved("Willing to travel", "Yes", intent=QuestionIntent.TRAVEL)]
    question = _question("travel", "Are you willing to travel if required?", input_type="radio", options=["Yes", "No"])
    action = resolve_form_questions([question], profile, employment_track="full_time", saved_answers=saved)[0]
    assert action.value == "No"
    assert action.answer_source == "profile"


def test_saved_answer_that_does_not_match_options_is_left_for_review() -> None:
    profile = CandidateProfile()
    saved = [_saved("How did you hear about us?", "LinkedIn")]
    question = _question("hear", "How did you hear about us?", input_type="select", options=["Job board", "Referral"], required=True)
    action = resolve_form_questions([question], profile, employment_track="full_time", saved_answers=saved)[0]
    assert action.action == "skip"


def test_saved_answers_never_answer_sensitive_questions() -> None:
    profile = CandidateProfile()
    saved = [_saved("Gender", "Woman")]
    question = _question("gender", "Gender", input_type="select", options=["Woman", "Man"])
    action = resolve_form_questions([question], profile, employment_track="full_time", saved_answers=saved)[0]
    assert action.action == "skip"


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_upsert_keeps_identity_usage_and_merges_aliases(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "answers.db")
    first = store.upsert_profile_answer(_saved("How did you hear about us?", "LinkedIn", profile_id="p1", answer_id="a1", aliases=["source"]))
    assert store.record_profile_answer_usage("p1", ["a1"]) == 1
    second = store.upsert_profile_answer(_saved("How did you hear about us?", "Referral", profile_id="p1", answer_id="a2", aliases=["referral source"]))
    assert second.answer_id == first.answer_id == "a1"
    assert second.value == "Referral"
    assert second.use_count == 1
    assert second.aliases == ["source", "referral source"]
    assert [answer.answer_id for answer in store.list_profile_answers("p1")] == ["a1"]
    assert store.list_profile_answers("other") == []
    assert store.delete_profile_answer("other", "a1") is False
    assert store.delete_profile_answer("p1", "a1") is True
    assert store.list_profile_answers("p1") == []


def test_setup_catalog_lists_eligibility_facts() -> None:
    profile = CandidateProfile()
    status = {item["key"]: item for item in profile_setup_status(profile)}
    assert status["application_facts.is_at_least_18"]["value_present"] is False
    profile.application_facts.is_at_least_18 = False
    status = {item["key"]: item for item in profile_setup_status(profile)}
    assert status["application_facts.is_at_least_18"]["value_present"] is True, "an explicit No counts as answered"
    assert "application_facts.willing_to_relocate" in status
    assert "education.degree_level" in status


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


def test_proposal_eligibility_excludes_legal_money_narrative_and_sensitive() -> None:
    assert is_question_proposal_eligible(_question("hear", "How did you hear about us?", required=True))
    assert is_question_proposal_eligible(_question("age", "Are you at least 18 years of age?", input_type="radio", options=["Yes", "No"]))
    assert not is_question_proposal_eligible(_question("auth", "Are you authorized to work in the United States?"))
    assert not is_question_proposal_eligible(_question("visa", "Will you now or in the future require sponsorship?"))
    assert not is_question_proposal_eligible(_question("pay", "What is your desired salary?"))
    assert not is_question_proposal_eligible(_question("why", "Why do you want this role?", input_type="textarea"))
    assert not is_question_proposal_eligible(_question("race", "Race"))
    assert not is_question_proposal_eligible(_question("cv", "Resume", input_type="file"))
    assert not is_question_proposal_eligible(_question("school", "School", profile_record_kind="education"))
    assert not is_question_proposal_eligible(_question("filled", "Preferred name", current_value_present=True))


def test_proposals_are_validated_against_options_and_cited_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = CandidateProfile(
        full_name="Avery Morgan",
        location="Austin, TX",
        skills=["Python", "Rust"],
    )
    profile.application_facts.willing_to_travel = True
    questions = [
        _question("travel", "Are you willing to travel if required by the position?", input_type="radio", options=["Yes", "No"], required=True),
        _question("hear", "How did you hear about us?", input_type="select", options=["LinkedIn", "Referral"], required=True),
        _question("rust", "How many years of Rust experience do you have?", required=True),
        _question("city", "What city are you based in?", required=True),
    ]
    captured: dict[str, Any] = {}

    async def fake_complete(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "answers": [
                {"field_id": "travel", "value": "yes", "evidence_key": "application_facts.willing_to_travel", "confidence": "high", "reason": "saved fact"},
                {"field_id": "hear", "value": "Twitter", "evidence_key": "location", "confidence": "high", "reason": "guess"},
                {"field_id": "rust", "value": "3 years", "evidence_key": "made.up", "confidence": "medium", "reason": "invented"},
                {"field_id": "city", "value": "Austin", "evidence_key": "location", "confidence": "medium", "reason": "from location"},
            ]
        }

    monkeypatch.setattr(answer_proposals, "complete_json", fake_complete)
    proposals = asyncio.run(answer_proposals.propose_short_answers(questions, profile, [], provider="workday"))
    assert captured["kwargs"]["task"] == "application"
    assert "Avery Morgan" in captured["prompt"]
    by_id = {proposal.field_id: proposal for proposal in proposals}

    assert by_id["travel"].value == "Yes", "boolean text is normalized to the offered option"
    assert by_id["travel"].confidence == "high"
    assert by_id["travel"].remember_target == "profile_fact"
    assert "application_facts.willing_to_travel" in by_id["travel"].evidence

    assert by_id["hear"].value is None, "value outside the offered options is discarded"
    assert by_id["rust"].value is None, "uncited fact is discarded"
    assert by_id["city"].value == "Austin"
    assert by_id["city"].remember_target == "saved_answer"


def test_proposals_skip_llm_when_nothing_is_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(answer_proposals, "complete_json", explode)
    questions = [_question("pay", "Desired salary", required=True), _question("why", "Why us?", input_type="textarea", required=True)]
    assert asyncio.run(answer_proposals.propose_short_answers(questions, CandidateProfile(), [])) == []


# ---------------------------------------------------------------------------
# API round trip
# ---------------------------------------------------------------------------


def _scan(client: TestClient, questions: list[dict[str, Any]], url: str = "https://boards.greenhouse.io/example/jobs/1") -> str:
    response = client.post(
        "/extension/forms/scan",
        json={"provider": "greenhouse", "page_url": url, "questions": questions},
    )
    assert response.status_code == 200
    return response.json()["scan_id"]


def test_remembered_override_resolves_the_next_form(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "bank-api.db")
    app = create_app(application_store=store)
    questions = [
        {"field_id": "hear", "label": "How did you hear about us?", "input_type": "select", "options": ["LinkedIn", "Referral"], "required": True},
        {"field_id": "age", "label": "Are you at least 18 years of age?", "input_type": "radio", "options": ["Yes", "No"], "required": True},
    ]
    with TestClient(app) as client:
        first = _scan(client, questions)
        plan = client.get(f"/extension/forms/{first}/plan").json()
        assert sorted(plan["unresolved_required"]) == ["Are you at least 18 years of age?", "How did you hear about us?"]
        assert all(item["proposal_eligible"] for item in plan["review_items"])

        remembered = client.post(
            f"/extension/forms/{first}/plan",
            json={"overrides": {"hear": "LinkedIn", "age": "Yes"}, "remember": True},
        )
        assert remembered.status_code == 200
        assert remembered.json()["unresolved_required"] == []

        bank = client.get("/profile/answers").json()["answers"]
        assert [answer["prompt_text"] for answer in bank] == ["How did you hear about us?"]
        assert client.get("/profile").json()["application_facts"]["is_at_least_18"] is True

        second = _scan(client, questions, url="https://boards.greenhouse.io/other/jobs/2")
        plan = client.get(f"/extension/forms/{second}/plan").json()
        assert plan["unresolved_required"] == []
        by_id = {action["field_id"]: action for action in plan["actions"]}
        assert by_id["hear"]["answer_source"] == "saved_answer"
        assert by_id["hear"]["value"] == "LinkedIn"
        assert by_id["age"]["answer_source"] == "profile"
        items = {item["field_id"]: item for item in plan["review_items"]}
        assert items["hear"]["proposal_eligible"] is False
        assert "remembered" in items["hear"]["resolution_reason"]

        used = client.post(f"/extension/forms/{second}/answers/used", json={"field_ids": ["hear"]})
        assert used.status_code == 200 and used.json()["recorded"] == 1
        bank = client.get("/profile/answers").json()["answers"]
        assert bank[0]["use_count"] == 1 and bank[0]["last_used_at"]

        answer_id = bank[0]["answer_id"]
        assert client.delete(f"/profile/answers/{answer_id}").status_code == 204
        assert client.get("/profile/answers").json()["answers"] == []


def test_override_without_remember_does_not_touch_profile_or_bank(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "once.db"))
    with TestClient(app) as client:
        scan_id = _scan(client, [{"field_id": "age", "label": "Are you at least 18 years of age?", "input_type": "radio", "options": ["Yes", "No"], "required": True}])
        assert client.post(f"/extension/forms/{scan_id}/plan", json={"overrides": {"age": "Yes"}}).status_code == 200
        assert client.get("/profile").json()["application_facts"]["is_at_least_18"] is None
        assert client.get("/profile/answers").json()["answers"] == []


def test_profile_answers_upsert_route_and_isolation(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "upsert.db"))
    with TestClient(app) as client:
        created = client.post(
            "/profile/answers",
            json={"prompt_text": "Security clearance", "value": "None", "aliases": ["active clearance"]},
            headers={"X-Profile-Id": "alice"},
        )
        assert created.status_code == 200
        assert created.json()["normalized_prompt"] == "security clearance"
        assert client.get("/profile/answers", headers={"X-Profile-Id": "alice"}).json()["answers"][0]["value"] == "None"
        assert client.get("/profile/answers", headers={"X-Profile-Id": "bob"}).json()["answers"] == []
        assert client.delete(f"/profile/answers/{created.json()['answer_id']}", headers={"X-Profile-Id": "bob"}).status_code == 404


def test_propose_route_returns_validated_suggestions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApplicationStore(tmp_path / "propose.db")
    profile = store.get_candidate_profile("default")
    # A fact the deterministic resolver cannot map onto this wording, so the
    # question stays unresolved and becomes a proposal candidate.
    profile.custom_answers["Onsite availability"] = "Hybrid, 3 days onsite"
    store.save_candidate_profile(profile)
    app = create_app(application_store=store)
    seen: dict[str, Any] = {}

    async def fake_complete(prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["prompt"] = prompt
        return {"answers": [{"field_id": "hybrid", "value": "Yes", "evidence_key": "custom_answers.Onsite availability", "confidence": "high", "reason": "saved availability"}]}

    monkeypatch.setattr(answer_proposals, "complete_json", fake_complete)
    with TestClient(app) as client:
        scan_id = _scan(
            client,
            [
                {"field_id": "hybrid", "label": "Are you comfortable with a hybrid schedule (3 days per week at our office)?", "input_type": "select", "options": ["Yes", "No"], "required": True},
                {"field_id": "visa", "label": "Do you require visa sponsorship?", "input_type": "select", "options": ["Yes", "No"], "required": True},
            ],
        )
        before = client.get(f"/extension/forms/{scan_id}/plan").json()
        assert len(before["unresolved_required"]) == 2
        response = client.post(f"/extension/forms/{scan_id}/answers/propose", json={})
        assert response.status_code == 200
        proposals = response.json()["proposals"]
        assert [proposal["field_id"] for proposal in proposals] == ["hybrid"], "sponsorship is never proposed"
        assert '"visa"' not in seen["prompt"], "excluded questions are not even sent to the model"
        assert proposals[0]["value"] == "Yes"
        assert proposals[0]["remember_target"] == "saved_answer"
        assert "Onsite availability" in proposals[0]["evidence"]
        # Nothing is filled until the user confirms.
        after = client.get(f"/extension/forms/{scan_id}/plan").json()
        assert after["unresolved_required"] == before["unresolved_required"]
