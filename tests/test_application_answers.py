"""Application-answer generation contracts without live model calls."""

from __future__ import annotations

import asyncio
from pathlib import Path

from latex_resume import application_answers
from latex_resume.application_answers import generate_application_answer
from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import (
    CandidateProfile,
    FormQuestion,
    FormScan,
    JobPosting,
    JobProvider,
    WorkExperienceProfile,
)


def _answer_fixture(tmp_path: Path) -> tuple[ApplicationStore, FormScan, FormQuestion, CandidateProfile]:
    store = ApplicationStore(tmp_path / "answers.db")
    profile = CandidateProfile(
        profile_id="candidate",
        skills=["Python", "FastAPI", "LLM evaluation"],
        portfolio_url="https://candidate.example.test",
        github_url="https://github.com/candidate",
        work_experiences=[
            WorkExperienceProfile(
                company="Accenture",
                job_title="AI/ML Engineer",
                summary="Built production AI services and evaluation pipelines with Python and FastAPI.",
                bullets=["Improved release quality through automated model evaluation and monitoring."],
            )
        ],
    )
    store.save_candidate_profile(profile)
    job = JobPosting(
        job_id="cohere-role",
        provider=JobProvider.ASHBY,
        board_token="cohere",
        external_id="role-1",
        company="Cohere",
        title="Software Engineer Intern",
        description=(
            "Cohere is hiring a software engineering intern to build reliable AI platform capabilities. "
            "The role works across backend services, evaluation systems, APIs, developer tooling, and product "
            "engineering. Candidates should demonstrate strong Python skills, practical software ownership, "
            "clear communication, and enthusiasm for production generative AI systems."
        ),
        source_url="https://jobs.ashbyhq.com/cohere/role-1",
        apply_url="https://jobs.ashbyhq.com/cohere/role-1/application",
    )
    store.save_job(job)
    application = store.create_application(job.job_id, profile_id=profile.profile_id)
    question = FormQuestion(
        field_id="cohere-fit",
        label="What makes you a good fit for Cohere?",
        input_type="textarea",
        required=True,
        max_length=900,
    )
    scan = FormScan(
        scan_id="scan-cohere",
        application_id=application.application_id,
        provider=JobProvider.ASHBY,
        page_url=job.apply_url,
        questions=[question],
    )
    store.save_form_scan(scan)
    return store, scan, question, profile


def test_generated_answer_uses_verified_official_sources_and_candidate_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)

    async def fake_complete(
        _prompt: str,
        *,
        system: str,
        web_search: bool,
        preferred_provider: str | None = None,
    ) -> tuple[dict, str]:
        del system, preferred_provider
        if web_search:
            return {
                "official_domain": "cohere.com",
                "sources": [
                    {
                        "title": "Cohere Careers",
                        "url": "https://cohere.com/careers",
                        "fact": "Cohere builds secure enterprise AI products and developer platforms.",
                    },
                    {
                        "title": "Third party",
                        "url": "https://example.com/cohere",
                        "fact": "This source must be removed.",
                    },
                ],
            }, "codex"
        return {
            "answer": (
                "At Accenture, I built production AI services and evaluation pipelines with Python and FastAPI, "
                "which taught me to connect model quality with dependable software delivery. That experience, "
                "along with my hands-on work in monitoring and developer tooling, fits this internship's blend "
                "of backend engineering and applied AI. I would bring practical ownership, fast learning, and "
                "a strong interest in helping Cohere make enterprise AI systems useful and reliable."
            ),
            "evidence_ids": ["profile"],
            "warnings": [],
        }, "codex"

    monkeypatch.setattr(application_answers, "_complete_with_fallback", fake_complete)
    draft = asyncio.run(
        generate_application_answer(store, scan=scan, question=question, profile=profile)
    )

    assert draft.provider == "codex"
    assert draft.word_count <= 100
    assert [source.url for source in draft.sources] == ["https://cohere.com/careers"]
    assert [item.evidence_id for item in draft.evidence] == ["profile"]
    assert "Cohere" in draft.answer


def test_generated_answer_retries_a_false_cohere_experience_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)
    answer_attempts = 0

    async def fake_complete(
        _prompt: str,
        *,
        system: str,
        web_search: bool,
        preferred_provider: str | None = None,
    ) -> tuple[dict, str]:
        nonlocal answer_attempts
        del system, preferred_provider
        if web_search:
            return {"official_domain": "cohere.com", "sources": []}, "codex"
        answer_attempts += 1
        if answer_attempts == 1:
            return {
                "answer": "I built production applications with the Cohere API and deployed them for customers.",
                "evidence_ids": ["profile"],
                "warnings": [],
            }, "codex"
        return {
            "answer": (
                "My production AI work at Accenture combines Python services, evaluation pipelines, monitoring, "
                "and practical product ownership. Those adjacent skills match the engineering discipline behind "
                "reliable enterprise AI without overstating experience I have not yet gained. I would contribute "
                "a strong backend foundation, thoughtful model evaluation, and the curiosity to learn Cohere's "
                "platform quickly while shipping useful, well-tested improvements with the team."
            ),
            "evidence_ids": ["profile"],
            "warnings": [],
        }, "codex"

    monkeypatch.setattr(application_answers, "_complete_with_fallback", fake_complete)
    draft = asyncio.run(
        generate_application_answer(store, scan=scan, question=question, profile=profile)
    )

    assert answer_attempts == 2
    assert "Cohere API" not in draft.answer
    assert draft.word_count <= 100


def test_generated_answers_reuse_verified_research_for_the_same_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)
    research_calls = 0
    answer_calls = 0

    async def fake_complete(
        _prompt: str,
        *,
        system: str,
        web_search: bool,
        preferred_provider: str | None = None,
    ) -> tuple[dict, str]:
        nonlocal research_calls, answer_calls
        del system, preferred_provider
        if web_search:
            research_calls += 1
            return {
                "official_domain": "cohere.com",
                "sources": [
                    {
                        "title": "Cohere Careers",
                        "url": "https://cohere.com/careers",
                        "fact": "Cohere builds secure enterprise AI products and developer platforms.",
                    }
                ],
            }, "codex"
        answer_calls += 1
        return {
            "answer": (
                "At Accenture, I built production AI services and evaluation pipelines using Python and FastAPI. "
                "That work strengthened my ability to connect model quality, monitoring, and dependable backend "
                "delivery. These skills fit Cohere's focus on useful enterprise AI products, and I would bring "
                "practical ownership, careful engineering, and the curiosity to learn the platform quickly."
            ),
            "evidence_ids": ["profile"],
            "warnings": [],
        }, "codex"

    monkeypatch.setattr(application_answers, "_complete_with_fallback", fake_complete)

    asyncio.run(generate_application_answer(store, scan=scan, question=question, profile=profile))
    asyncio.run(generate_application_answer(store, scan=scan, question=question, profile=profile))

    assert research_calls == 1
    assert answer_calls == 2


def test_application_answer_rejects_non_open_ended_fields(tmp_path: Path) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)
    scalar = question.model_copy(update={"input_type": "text"})

    try:
        asyncio.run(generate_application_answer(store, scan=scan, question=scalar, profile=profile))
    except ValueError as exc:
        assert "open-ended" in str(exc)
    else:
        raise AssertionError("Expected scalar application field generation to be rejected.")


def test_application_answer_never_generates_compensation_textarea(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)
    compensation = question.model_copy(
        update={
            "field_id": "desired-income",
            "label": "What is your desired income? (Hourly, Monthly, or Annual)",
            "input_type": "textarea",
        }
    )

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("The LLM provider must not be called for compensation.")

    monkeypatch.setattr(application_answers, "_complete_with_fallback", fail_if_called)
    try:
        asyncio.run(
            generate_application_answer(
                store,
                scan=scan,
                question=compensation,
                profile=profile,
            )
        )
    except ValueError as exc:
        assert "open-ended narrative" in str(exc)
    else:
        raise AssertionError("Expected compensation draft generation to be rejected.")


def test_application_provider_falls_back_to_openai_when_configured(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_complete_json(_prompt: str, **kwargs) -> dict:
        provider = kwargs["backend_override"]
        calls.append(provider)
        if provider == "codex":
            raise RuntimeError("Codex unavailable")
        return {"answer": "ok"}

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(application_answers, "complete_json", fake_complete_json)
    result, provider = asyncio.run(
        application_answers._complete_with_fallback(
            "prompt",
            system="system",
            web_search=False,
            preferred_provider="codex",
        )
    )

    assert result == {"answer": "ok"}
    assert provider == "openai"
    assert calls == ["codex", "openai"]


def test_generated_answer_enforces_the_absolute_100_word_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, scan, question, profile = _answer_fixture(tmp_path)

    async def fake_complete(
        _prompt: str,
        *,
        system: str,
        web_search: bool,
        preferred_provider: str | None = None,
    ) -> tuple[dict, str]:
        del system, preferred_provider
        if web_search:
            return {"official_domain": "cohere.com", "sources": []}, "codex"
        return {
            "answer": " ".join(["grounded"] * 101),
            "evidence_ids": ["profile"],
            "warnings": [],
        }, "codex"

    monkeypatch.setattr(application_answers, "_complete_with_fallback", fake_complete)
    try:
        asyncio.run(generate_application_answer(store, scan=scan, question=question, profile=profile))
    except ValueError as exc:
        assert "exceeds 100 words" in str(exc)
    else:
        raise AssertionError("Expected a 101-word generated answer to be rejected.")
