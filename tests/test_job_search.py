"""Tests for public job discovery and controlled application persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from latex_resume.application_store import (
    ApplicationStore,
    InvalidApplicationTransition,
)
from latex_resume.job_models import (
    ApplicationStatus,
    CandidateProfile,
    EqualOpportunityProfile,
    FormQuestion,
    JobProvider,
    JobPosting,
    JobSearchQuery,
    JobSourceConfig,
    SearchPreferences,
    TargetRole,
    WorkAuthorizationProfile,
)
from latex_resume.form_resolution import resolve_form_questions
from latex_resume.job_matching import (
    classify_employment_track,
    classify_target_role,
    location_matches,
    preference_score,
    title_is_excluded,
)
from latex_resume.job_sources import JobSearchService, PublicJobBoardClient, html_to_text


def _transport(request: httpx.Request) -> httpx.Response:
    if "greenhouse.io" in str(request.url):
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 101,
                        "title": "Machine Learning Engineer",
                        "content": "<p>Build Python ML APIs.</p><p>Remote role.</p>",
                        "absolute_url": "https://example.test/jobs/101",
                        "location": {"name": "Remote - US"},
                        "updated_at": "2026-06-01T00:00:00Z",
                    },
                    {
                        "id": 102,
                        "title": "Account Executive",
                        "content": "<p>Sell enterprise software.</p>",
                        "absolute_url": "https://example.test/jobs/102",
                        "location": {"name": "Chicago, IL"},
                    },
                ]
            },
        )
    if "lever.co" in str(request.url):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "lever-1",
                    "text": "Senior AI Engineer",
                    "descriptionPlain": "Develop Python and RAG applications.",
                    "hostedUrl": "https://example.test/jobs/201",
                    "applyUrl": "https://example.test/jobs/201/apply",
                    "categories": {"location": "Remote"},
                }
            ],
        )
    return httpx.Response(503, text="source unavailable")


def test_searches_and_normalizes_public_boards() -> None:
    async def run() -> object:
        transport = httpx.MockTransport(_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            service = JobSearchService(PublicJobBoardClient(client))
            return await service.search(
                JobSearchQuery(
                    text="AI machine learning",
                    role_keywords=["engineer"],
                    remote_only=True,
                ),
                [
                    JobSourceConfig(
                        provider=JobProvider.GREENHOUSE,
                        board_token="example",
                        company="Example",
                    ),
                    JobSourceConfig(
                        provider=JobProvider.LEVER,
                        board_token="second-example",
                        company="Second Example",
                    ),
                ],
            )

    result = asyncio.run(run())

    assert [job.title for job in result.jobs] == [
        "Machine Learning Engineer",
        "Senior AI Engineer",
    ]
    assert all(job.workplace_type == "remote" for job in result.jobs)
    assert result.jobs[0].description == "Build Python ML APIs.\nRemote role."
    assert result.errors == []


def test_source_failure_is_retained_without_losing_successes() -> None:
    async def run() -> object:
        transport = httpx.MockTransport(_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            service = JobSearchService(PublicJobBoardClient(client))
            return await service.search(
                JobSearchQuery(text="engineer"),
                [
                    JobSourceConfig(
                        provider=JobProvider.LEVER,
                        board_token="example",
                        company="Example",
                    ),
                    JobSourceConfig(
                        provider=JobProvider.ASHBY,
                        board_token="unavailable",
                        company="Unavailable",
                    ),
                ],
            )

    result = asyncio.run(run())

    assert len(result.jobs) == 1
    assert len(result.errors) == 1
    assert result.errors[0].provider is JobProvider.ASHBY


def test_store_enforces_human_approval_before_submission(tmp_path: Path) -> None:
    async def run() -> object:
        transport = httpx.MockTransport(_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            service = JobSearchService(PublicJobBoardClient(client))
            return await service.search(
                JobSearchQuery(text="machine learning"),
                [
                    JobSourceConfig(
                        provider=JobProvider.GREENHOUSE,
                        board_token="example",
                        company="Example",
                    )
                ],
            )

    search = asyncio.run(run())

    store = ApplicationStore(tmp_path / "jobs.db")
    store.save_search(search)
    application = store.create_application(search.jobs[0].job_id)

    application = store.transition_application(
        application.application_id,
        ApplicationStatus.SELECTED,
    )
    application = store.transition_application(
        application.application_id,
        ApplicationStatus.RESUME_READY,
    )
    application = store.transition_application(
        application.application_id,
        ApplicationStatus.FORM_SCANNED,
    )
    application = store.transition_application(
        application.application_id,
        ApplicationStatus.READY_FOR_REVIEW,
    )
    with pytest.raises(InvalidApplicationTransition):
        store.transition_application(
            application.application_id,
            ApplicationStatus.SUBMITTING,
        )

    application = store.transition_application(
        application.application_id,
        ApplicationStatus.APPROVED,
    )
    application = store.transition_application(
        application.application_id,
        ApplicationStatus.SUBMITTING,
    )
    application = store.transition_application(
        application.application_id,
        ApplicationStatus.SUBMITTED,
    )
    assert application.approved_at is not None
    assert application.submitted_at is not None
    assert store.list_applications()[0].status is ApplicationStatus.SUBMITTED


def test_html_conversion_and_source_token_validation() -> None:
    assert html_to_text("<p>Hello <strong>ML</strong></p><li>Python</li>") == (
        "Hello ML\nPython"
    )
    with pytest.raises(ValueError):
        JobSourceConfig(
            provider=JobProvider.GREENHOUSE,
            board_token="../unsafe",
            company="Unsafe",
        )


def test_structured_location_wins_over_description_mentions() -> None:
    from latex_resume.job_sources import _workplace_type

    assert _workplace_type(
        "AI Engineer",
        "New York, NY",
        "Collaborate with remote customers across the United States.",
    ) == "onsite"


def test_target_role_and_texas_location_matching() -> None:
    assert classify_target_role("Agentic AI Intern") is TargetRole.AGENTIC_AI_INTERN
    assert classify_target_role("Machine Learning Engineer") is TargetRole.ML_ENGINEER
    assert classify_employment_track("NLP Internship") == "internship"

    job = JobPosting(
        job_id="austin-job",
        provider=JobProvider.LEVER,
        board_token="example",
        external_id="1",
        company="Example",
        title="AI Engineer",
        description="Build AI systems.",
        location="Austin, Texas",
        workplace_type="onsite",
        source_url="https://example.test/job",
        apply_url="https://example.test/apply",
    )
    assert location_matches(job, SearchPreferences()) is True
    assert location_matches(
        job.model_copy(
            update={
                "location": "New York, NY",
                "description": "New York onsite role supporting remote customers in the United States.",
            }
        ),
        SearchPreferences(),
    ) is False


def test_senior_staff_and_manager_titles_are_excluded() -> None:
    preferences = SearchPreferences()
    assert title_is_excluded("Senior AI Engineer", preferences) is True
    assert title_is_excluded("Sr. Machine Learning Engineer", preferences) is True
    assert title_is_excluded("Staff Data Scientist", preferences) is True
    assert title_is_excluded("Manager, Applied AI", preferences) is True
    assert title_is_excluded("Machine Learning Engineer", preferences) is False
    assert title_is_excluded("Machine Learning Intern", preferences) is False

    senior_job = JobPosting(
        job_id="senior-job",
        provider=JobProvider.GREENHOUSE,
        board_token="example",
        external_id="senior-1",
        company="Example",
        title="Senior AI Engineer",
        description="Build AI systems.",
        location="Houston, TX",
        workplace_type="onsite",
        source_url="https://example.test/job",
        apply_url="https://example.test/apply",
    )
    assert preference_score(senior_job, preferences) == -1.0


def test_form_resolution_uses_single_sponsorship_answer() -> None:
    profile = CandidateProfile(
        full_name="Test Candidate",
        email="candidate@example.test",
        work_authorization=WorkAuthorizationProfile(
            authorized_to_work_in_us=True,
            requires_sponsorship=False,
        ),
    )
    questions = [
        FormQuestion(
            field_id="name",
            label="Full name",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="authorized",
            label="Are you authorized to work in the United States?",
            input_type="select",
            required=True,
        ),
        FormQuestion(
            field_id="sponsor",
            label="Will you require sponsorship?",
            input_type="select",
            required=True,
        ),
        FormQuestion(
            field_id="race",
            label="Race / ethnicity",
            input_type="select",
            required=False,
        ),
    ]

    internship = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
    )
    assert internship[0].value == "Test Candidate"
    assert internship[1].value == "Yes"
    assert internship[2].value == "No"
    assert internship[3].action == "skip"

    full_time = resolve_form_questions(
        questions,
        profile,
        employment_track="full_time",
    )
    assert full_time[2].value == "No"

    profile.work_authorization.requires_sponsorship = True
    known_full_time = resolve_form_questions(
        questions,
        profile,
        employment_track="full_time",
    )
    assert known_full_time[2].value == "Yes"


def test_equal_opportunity_answers_require_explicit_opt_in() -> None:
    question = FormQuestion(
        field_id="race",
        label="How would you identify your race?",
        input_type="select",
        sensitive=True,
        options=["Select", "Asian", "Prefer not to answer"],
    )
    disabled = resolve_form_questions(
        [question],
        CandidateProfile(
            equal_opportunity=EqualOpportunityProfile(
                allow_autofill=False,
                race="Asian",
            )
        ),
        employment_track="internship",
    )
    assert disabled[0].action == "skip"
    assert disabled[0].answer_source == "eeo_opt_in"

    enabled = resolve_form_questions(
        [question],
        CandidateProfile(
            equal_opportunity=EqualOpportunityProfile(
                allow_autofill=True,
                race="Asian",
            )
        ),
        employment_track="internship",
    )
    assert enabled[0].action == "select"
    assert enabled[0].value == "Asian"


def test_split_name_and_hispanic_latino_resolution() -> None:
    profile = CandidateProfile(
        full_name="Test Candidate",
        equal_opportunity=EqualOpportunityProfile(
            allow_autofill=True,
            hispanic_or_latino="No",
        ),
    )
    questions = [
        FormQuestion(
            field_id="first",
            label="First Name",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="last",
            label="Last Name",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="hispanic",
            label="Are you Hispanic/Latino?",
            input_type="radio",
            required=False,
            sensitive=True,
            options=["Yes", "No", "Prefer not to answer"],
        ),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
    )

    assert actions[0].value == "Test"
    assert actions[1].value == "Candidate"
    assert actions[2].action == "select"
    assert actions[2].value == "No"


def test_relocation_and_long_form_no_option_resolution() -> None:
    profile = CandidateProfile()
    profile.search_preferences.willing_to_relocate = True
    questions = [
        FormQuestion(
            field_id="relocate",
            label="Are you willing to relocate?",
            input_type="select",
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="sponsor",
            label="Will you require sponsorship?",
            input_type="select",
            options=[
                "Yes, I will require sponsorship",
                "No, I do not require sponsorship",
            ],
        ),
    ]
    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
    )
    assert actions[0].value == "Yes"
    assert actions[1].value == "No, I do not require sponsorship"


def test_restrictive_employer_agreement_questions_resolve_to_no() -> None:
    profile = CandidateProfile()
    question = FormQuestion(
        field_id="agreement",
        label=(
            "Are you currently bound by any agreements with a current or former "
            "employer that may restrict your ability to work for Scale AI or "
            "perform the duties of the position for which you are applying? "
            "This includes, but is not limited to, non-compete agreements, "
            "non-solicitation agreements, confidentiality or non-disclosure "
            "agreements, or any other contractual obligations that could limit "
            "your employment activities."
        ),
        input_type="select",
        required=True,
        options=["Yes", "No"],
    )

    actions = resolve_form_questions(
        [question],
        profile,
        employment_track="internship",
    )

    assert actions[0].action == "select"
    assert actions[0].value == "No"


def test_generic_name_resume_and_boolean_radio_resolution() -> None:
    profile = CandidateProfile(
        full_name="Test Candidate",
        resume_pdf_b64="ZmFrZS1wZGY=",
        work_authorization=WorkAuthorizationProfile(
            authorized_to_work_in_us=True,
            requires_sponsorship=False,
        ),
    )
    questions = [
        FormQuestion(
            field_id="name",
            label="Name",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="resume",
            label="Resume",
            input_type="file",
            required=True,
        ),
        FormQuestion(
            field_id="authorized",
            label="Are you legally authorized to work in the country where the job is located?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="sponsorship",
            label="Will you now or in the future require company sponsorship?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
    )

    assert actions[0].action == "fill"
    assert actions[0].value == "Test Candidate"
    assert actions[1].action == "upload"
    assert actions[1].answer_source == "resume"
    assert actions[2].action == "select"
    assert actions[2].value == "Yes"
    assert actions[3].action == "select"
    assert actions[3].value == "No"


def test_common_custom_answers_are_reused_with_partial_label_match() -> None:
    profile = CandidateProfile(
        custom_answers={
            "Desired salary": "Open to discussion",
            "How did you hear about us?": "Job board",
        }
    )
    questions = [
        FormQuestion(
            field_id="salary",
            label="What is your desired salary?",
            input_type="text",
        ),
        FormQuestion(
            field_id="source",
            label="How did you hear about us?",
            input_type="select",
            options=["Referral", "Job board", "LinkedIn"],
        ),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
    )

    assert actions[0].value == "Open to discussion"
    assert actions[1].action == "select"
    assert actions[1].value == "Job board"


def test_equal_opportunity_coerces_legacy_sexual_orientation_string() -> None:
    profile = EqualOpportunityProfile.model_validate(
        {"sexual_orientation": "Heterosexual, Bisexual"}
    )
    assert profile.sexual_orientation == ["Heterosexual", "Bisexual"]


def test_pronouns_and_sexual_orientation_resolution() -> None:
    profile = CandidateProfile(
        equal_opportunity=EqualOpportunityProfile(
            allow_autofill=True,
            sexual_orientation=["Heterosexual", "Pansexual"],
            pronouns="They/Them",
        )
    )
    pronoun_question = FormQuestion(
        field_id="pronouns",
        label="What are your pronouns?",
        input_type="select",
        options=["He/Him", "She/Her", "They/Them", "Other"],
    )
    orientation_question = FormQuestion(
        field_id="orientation",
        label="How would you describe your sexual orientation?",
        input_type="select",
        sensitive=True,
        options=["Heterosexual", "Gay", "Lesbian", "Bisexual"],
    )

    actions = resolve_form_questions(
        [pronoun_question, orientation_question],
        profile,
        employment_track="internship",
    )

    assert actions[0].value == "They/Them"
    assert actions[1].value == "Heterosexual"
