"""FastAPI contract tests that do not require an LLM provider."""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from latex_resume.application_store import ApplicationStore
from latex_resume.api import _fill_values_match, create_app
from latex_resume.form_resolution import resolve_form_questions
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    CandidateProfile,
    EducationProfile,
    FormScan,
    FormQuestion,
    JobSourceConfig,
    JobPosting,
    JobProvider,
    JobSearchResult,
    WorkExperienceProfile,
    utc_now,
)


def test_workday_my_experience_uses_catalog_values_and_role_descriptions() -> None:
    profile = CandidateProfile(
        skills=["Python", "FastAPI"],
        portfolio_url="https://portfolio.example.test",
        educations=[
            EducationProfile(
                school="University of Houston",
                degree="M.S. in Engineering Data Science & Artificial Intelligence",
                major="Engineering Data Science & Artificial Intelligence",
                gpa="4.0/4.0",
            )
        ],
        work_experiences=[
            WorkExperienceProfile(
                company="Accenture",
                summary="Built production AI systems.",
                bullets=[
                    "Built production AI systems.",
                    "Improved model evaluation and monitoring.",
                ],
            )
        ],
    )
    questions = [
        FormQuestion(field_id="school", label="Education School or University", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="degree", label="Education Degree", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="major", label="Education Field of Study", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="gpa", label="Education Overall Result (GPA)", input_type="text", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="description", label="Experience Role Description", input_type="textarea", profile_record_kind="work_experience", profile_record_index=0),
        FormQuestion(field_id="certification", label="Certification", input_type="text"),
        FormQuestion(field_id="language", label="Language", input_type="text"),
        FormQuestion(field_id="website", label="Website URL", input_type="text"),
        FormQuestion(field_id="skills", label="Skills", input_type="select", control_kind="multi_select"),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="internship",
        provider="workday",
    )

    assert actions[0].value == ["University of Houston"]
    assert actions[1].value == ["MS", "Master of Science", "Master's Degree"]
    assert actions[2].value == [
        "Computer and Information Science",
        "Data Science",
        "Data Processing",
        "Computer Engineering",
    ]
    assert actions[3].value == "4/4"
    assert actions[4].action == "fill"
    assert actions[4].value == "- Built production AI systems.\n- Improved model evaluation and monitoring."
    assert all(action.action == "skip" for action in actions[5:8])
    assert actions[8].action == "select_many"
    assert actions[8].value == ["Python", "FastAPI"]


def test_workday_education_catalog_candidates_preserve_institution_and_major_identity() -> None:
    profile = CandidateProfile(
        educations=[
            EducationProfile(
                school="University of Houston",
                degree="M.S. in Engineering Data Science & Artificial Intelligence",
                major="Engineering Data Science & Artificial Intelligence",
            ),
            EducationProfile(
                school="Amrita School of Engineering",
                degree="B.Tech in Computer Science and Engineering",
                major="Computer Science and Engineering",
            ),
        ],
    )
    questions = [
        FormQuestion(field_id="school-1", label="School or University", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="major-1", label="Field of Study", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="school-2", label="School or University", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=1),
        FormQuestion(field_id="major-2", label="Field of Study", input_type="select", control_kind="custom_select", profile_record_kind="education", profile_record_index=1),
    ]

    actions = resolve_form_questions(questions, profile, employment_track="internship", provider="workday")

    assert actions[0].value == ["University of Houston"]
    assert actions[1].value[:3] == ["Computer and Information Science", "Data Science", "Data Processing"]
    assert actions[2].value == ["Amrita School of Engineering", "Amrita Vishwa Vidyapeetham"]
    assert actions[3].value[:2] == ["Computer Science", "Computer and Information Science"]


def test_workday_expands_saved_skill_bundles_to_catalog_labels() -> None:
    profile = CandidateProfile(
        skills=[
            "Git/GitHub",
            "CrewAI",
            "LLM Fine-tuning (LoRA/QLoRA)",
            "LLM Evaluation (RAGAS, DeepEval)",
        ]
    )
    question = FormQuestion(
        field_id="skills",
        label="Type to Add Skills",
        input_type="select",
        control_kind="multi_select",
    )

    action = resolve_form_questions(
        [question],
        profile,
        employment_track="internship",
        provider="workday",
    )[0]

    assert action.value == [
        "Git",
        "Crew AI",
        "Fine-tuning",
        "LoRA/QLoRA",
        "RAGAS",
        "Deep-Eval",
    ]


def test_workday_formats_live_composite_work_dates_and_education_years() -> None:
    profile = CandidateProfile(
        educations=[
            EducationProfile(
                school="University of Houston",
                start_date="2025-08",
                end_date="2027-05",
            )
        ],
        work_experiences=[
            WorkExperienceProfile(
                company="Accenture",
                start_date="2023-11",
                end_date="2025-08",
            )
        ],
    )
    questions = [
        FormQuestion(
            field_id="work-from",
            label="Experience From",
            input_type="text",
            control_kind="month_year",
            profile_record_kind="work_experience",
            profile_record_index=0,
        ),
        FormQuestion(
            field_id="work-to",
            label="Experience To",
            input_type="text",
            control_kind="month_year",
            profile_record_kind="work_experience",
            profile_record_index=0,
        ),
        FormQuestion(
            field_id="education-from",
            label="Education From",
            input_type="text",
            control_kind="year",
            profile_record_kind="education",
            profile_record_index=0,
        ),
        FormQuestion(
            field_id="education-to",
            label="Education To (Actual or Expected)",
            input_type="text",
            control_kind="year",
            profile_record_kind="education",
            profile_record_index=0,
        ),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="full_time",
        provider="workday",
    )

    assert [action.value for action in actions] == ["11/2023", "08/2025", "2025", "2027"]

SAMPLE_PATH = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cors_rejects_arbitrary_web_origins() -> None:
    with TestClient(create_app()) as client:
        response = client.options(
            "/profile",
            headers={
                "Origin": "https://malicious.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_rejects_supported_ats_web_origins() -> None:
    with TestClient(create_app()) as client:
        response = client.options(
            "/profile",
            headers={
                "Origin": "https://job-boards.greenhouse.io",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_extension_origin_for_local_api() -> None:
    origin = f"chrome-extension://{'a' * 32}"
    with TestClient(create_app()) as client:
        response = client.options(
            "/profile",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_browser_only_providers_are_not_public_search_sources() -> None:
    for provider in [JobProvider.WORKDAY, JobProvider.INDEED, JobProvider.DICE]:
        try:
            JobSourceConfig(provider=provider, board_token="example", company="Example")
        except ValidationError as exc:
            assert "Chrome extension" in str(exc)
        else:
            raise AssertionError(f"{provider} should be extension-captured only")


def test_extension_capture_accepts_expanded_browser_provider(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "capture.db"))
    with TestClient(app) as client:
        captured = client.post(
            "/extension/jobs/capture",
            json={
                "provider": "workday",
                "external_id": "workday-123",
                "company": "Example",
                "title": "Machine Learning Engineer",
                "description": "Build production machine learning systems.",
                "location": "Austin, TX",
                "source_url": "https://example.myworkdayjobs.com/careers/job/workday-123",
                "apply_url": "https://example.myworkdayjobs.com/careers/job/workday-123",
                "workflow_key": "workday:example.myworkdayjobs.com:workday-123",
                "canonical_url": "https://example.myworkdayjobs.com/careers/job/workday-123",
                "description_source": "provider selector",
                "capture_confidence": 0.92,
                "warnings": ["Captured from visible job page."],
            },
        )

    assert captured.status_code == 200
    body = captured.json()
    assert body["provider"] == "workday"
    assert body["company"] == "Example"
    assert body["workflow_key"] == "workday:example.myworkdayjobs.com:workday-123"
    assert body["canonical_url"] == "https://example.myworkdayjobs.com/careers/job/workday-123"
    assert body["description_source"] == "provider selector"
    assert body["capture_confidence"] == 0.92
    assert body["warnings"] == ["Captured from visible job page."]


def test_list_endpoints_scope_by_profile_header(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "scoped.db")
    job_a = JobPosting(
        job_id="job-a",
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id="a",
        company="Acme",
        title="Engineer A",
        description="Build systems with Python and ML.",
        source_url="https://boards.greenhouse.io/acme/jobs/a",
        apply_url="https://boards.greenhouse.io/acme/jobs/a",
        captured_for_profile_id="alice",
    )
    job_b = JobPosting(
        job_id="job-b",
        provider=JobProvider.GREENHOUSE,
        board_token="beta",
        external_id="b",
        company="Beta",
        title="Engineer B",
        description="Build systems with Python and ML.",
        source_url="https://boards.greenhouse.io/beta/jobs/b",
        apply_url="https://boards.greenhouse.io/beta/jobs/b",
        captured_for_profile_id="bob",
    )
    store.save_job(job_a)
    store.save_job(job_b)
    store.create_application(job_a.job_id, profile_id="alice")
    store.create_application(job_b.job_id, profile_id="bob")

    app = create_app(application_store=store)
    with TestClient(app) as client:
        alice_jobs = client.get("/jobs", headers={"X-Profile-Id": "alice"})
        bob_apps = client.get("/applications", headers={"X-Profile-Id": "bob"})
        alice_health = client.get("/applications/health", headers={"X-Profile-Id": "alice"})

    assert alice_jobs.status_code == 200
    assert [job["job_id"] for job in alice_jobs.json()] == ["job-a"]
    assert bob_apps.status_code == 200
    assert [app["profile_id"] for app in bob_apps.json()] == ["bob"]
    assert alice_health.status_code == 200
    health = alice_health.json()
    assert health["profile_id"] == "alice"
    assert health["captured_jobs"] == 1
    assert health["total"] == 1


def test_previous_employer_question_does_not_use_current_company() -> None:
    profile = CandidateProfile(
        full_name="Asha Patel",
        work_experiences=[
            WorkExperienceProfile(company="ApplyTeX Labs", job_title="ML Engineer")
        ],
    )
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="current-company",
                label="Current company",
                input_type="text",
                required=True,
            ),
            FormQuestion(
                field_id="previous-company",
                label="Have you ever worked for this company or any affiliate before?",
                input_type="text",
                required=True,
            ),
        ],
        profile,
        employment_track="full_time",
    )

    assert actions[0].action == "fill"
    assert actions[0].value == "ApplyTeX Labs"
    assert actions[1].action == "skip"
    assert actions[1].answer_source == "user_input"


def test_common_application_custom_answer_aliases_are_fillable() -> None:
    profile = CandidateProfile(
        custom_answers={
            "Previously employed by company": "No",
            "Reliable commute": "Yes",
            "Earliest start date": "Immediately",
            "Desired salary": "Open to market-aligned compensation",
            "Security clearance": "No",
            "Available to work weekends": "No",
            "Relevant project link": "https://example.test/ml-platform",
            "Why this role": "The role matches my applied AI and product engineering work.",
            "Production AI system summary": "Built production LLM evaluation and monitoring services.",
        }
    )
    questions = [
        FormQuestion(
            field_id="previous",
            label="Have you ever worked for this company or any affiliate before?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="commute",
            label="Can you reliably commute to this job's location?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="start",
            label="What is your earliest available start date?",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="pay",
            label="What are your compensation expectations?",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="clearance",
            label="Do you hold an active security clearance?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="weekends",
            label="Are you available to work weekends if needed?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="project",
            label="Link to the project most relevant to this role",
            input_type="text",
            required=True,
        ),
        FormQuestion(
            field_id="why",
            label="Tell us why you are interested in this startup.",
            input_type="textarea",
            required=True,
        ),
        FormQuestion(
            field_id="ai-system",
            label="Briefly describe a production AI system you shipped.",
            input_type="textarea",
            required=True,
        ),
    ]

    actions = resolve_form_questions(questions, profile, employment_track="full_time")

    assert [action.action for action in actions] == [
        "select",
        "select",
        "fill",
        "fill",
        "select",
        "select",
        "fill",
        "fill",
        "fill",
    ]
    assert all(action.answer_source == "custom_answer" for action in actions)
    assert actions[0].value == "No"
    assert actions[2].value == "Immediately"


def test_workday_phone_controls_do_not_receive_the_phone_number() -> None:
    profile = CandidateProfile(
        phone="+1 202-555-0142",
        address={"line1": "123 Main Street", "city": "Houston", "state": "Texas"},
        custom_answers={"Phone device type": "Mobile"},
    )
    actions = resolve_form_questions(
        [
            FormQuestion(field_id="phone-type", label="Phone Device Type*", input_type="select", required=True),
            FormQuestion(field_id="phone-code", label="Country Phone Code*", input_type="select", required=True),
            FormQuestion(field_id="phone-number", label="Phone Number*", input_type="text", required=True),
            FormQuestion(field_id="phone-extension", label="Phone Extension", input_type="text"),
            FormQuestion(field_id="address-line-1", label="Address Line 1*", input_type="text", required=True),
        ],
        profile,
        employment_track="full_time",
    )

    assert [(action.action, action.value) for action in actions] == [
        ("select", "Mobile"),
        ("skip", None),
        ("fill", "+1 202-555-0142"),
        ("skip", None),
        ("fill", "123 Main Street"),
    ]


def test_phone_device_type_stays_unresolved_without_explicit_answer() -> None:
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="phone-type",
                label="Phone Device Type*",
                input_type="select",
                required=True,
                options=["Mobile", "Home", "Work"],
            )
        ],
        CandidateProfile(phone="+1 202-555-0142"),
        employment_track="full_time",
    )

    assert actions[0].action == "skip"
    assert actions[0].answer_source == "user_input"


def test_plain_phone_prefers_profile_phone_over_device_type_answer() -> None:
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="ashby-phone",
                label="Phone",
                input_type="tel",
                required=True,
            ),
            FormQuestion(
                field_id="phone-type",
                label="Phone Device Type*",
                input_type="select",
                required=True,
                options=["Mobile", "Home", "Work"],
            ),
        ],
        CandidateProfile(
            phone="+1 202-555-0142",
            custom_answers={"Phone device type": "Mobile"},
        ),
        employment_track="full_time",
    )

    assert [(action.action, action.value) for action in actions] == [
        ("fill", "+1 202-555-0142"),
        ("select", "Mobile"),
    ]
    assert actions[0].answer_source == "profile"


def test_fill_value_matching_normalizes_phone_formatting() -> None:
    assert _fill_values_match("12025550142", "+1-(202) 555-0142")
    assert _fill_values_match("(202) 555-0142", "+1-(202) 555-0142")


def test_fill_value_matching_recognizes_expanded_binary_options() -> None:
    assert _fill_values_match("I am not a protected veteran", "No")
    assert _fill_values_match("No, I do not have a disability", "No")
    assert _fill_values_match("Yes, I require sponsorship", "Yes")
    assert not _fill_values_match("I am not a protected veteran", "Yes")


def test_fill_value_matching_keeps_an_expanded_location_selection() -> None:
    assert _fill_values_match("Houston, Texas, United States", "Houston")
    assert not _fill_values_match("Dallas, Texas, United States", "Houston")


def test_bare_location_uses_profile_location_without_overriding_work_location() -> None:
    profile = CandidateProfile(
        location="Houston, Texas",
        work_experiences=[
            WorkExperienceProfile(
                job_title="Machine Learning Engineer",
                company="ApplyTeX Labs",
                location="Austin, Texas",
            )
        ],
    )
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="ashby-location",
                label="Location",
                input_type="select",
                required=True,
                control_kind="custom_select",
            ),
            FormQuestion(
                field_id="work-location",
                label="Experience Location",
                input_type="text",
                profile_record_kind="work_experience",
                profile_record_index=0,
            ),
        ],
        profile,
        employment_track="full_time",
    )

    assert [(action.action, action.value) for action in actions] == [
        ("select", "Houston, Texas"),
        ("fill", "Austin, Texas"),
    ]


def test_us_location_question_uses_profile_country_as_boolean() -> None:
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="ashby-us-location",
                label="Are you located in the United States?",
                input_type="radio",
                required=True,
                options=["Yes", "No"],
            )
        ],
        CandidateProfile(location="Houston", address={"country": "United States"}),
        employment_track="full_time",
    )

    assert [(action.action, action.value) for action in actions] == [("select", "Yes")]


def test_narrative_experience_questions_do_not_match_date_substrings() -> None:
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="ml-tooling",
                label="Do you have professional experience building production ML tooling?",
                input_type="radio",
                required=True,
                options=["Yes", "No"],
            ),
            FormQuestion(
                field_id="pytorch",
                label="Do you have experience deploying PyTorch in production?",
                input_type="radio",
                required=True,
                options=["Yes", "No"],
            ),
        ],
        CandidateProfile(
            work_experiences=[
                WorkExperienceProfile(start_date="2023-11", end_date="2025-08")
            ]
        ),
        employment_track="full_time",
    )

    assert [action.action for action in actions] == ["skip", "skip"]


def test_custom_radio_answer_matches_long_form_option() -> None:
    long_no = "No, I have never worked for this company or an affiliate"
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="previous-employer",
                label="Have you ever worked for this company before?",
                input_type="radio",
                required=True,
                options=["Yes, I have", long_no],
            )
        ],
        CandidateProfile(custom_answers={"Previously employed by company": "No"}),
        employment_track="full_time",
    )

    assert actions[0].action == "select"
    assert actions[0].value == long_no


def test_smartrecruiters_and_workday_profile_fields_reuse_known_answers() -> None:
    profile = CandidateProfile(
        education={"school": "Rice University"},
        skills=["Python", "FastAPI", "Azure"],
        work_experiences=[
            WorkExperienceProfile(
                job_title="Machine Learning Engineer",
                company="ApplyTeX Labs",
                location="Houston, Texas",
            )
        ],
        custom_answers={"Cover letter": "I am interested in the team's applied AI work."},
    )
    actions = resolve_form_questions(
        [
            FormQuestion(field_id="institution", label="Institution", input_type="select"),
            FormQuestion(field_id="title", label="Experience Title", input_type="select"),
            FormQuestion(field_id="office", label="Office location", input_type="select"),
            FormQuestion(field_id="skills", label="Skills", input_type="select"),
            FormQuestion(
                field_id="message",
                label="Let the company know about your interest working there",
                input_type="textarea",
            ),
        ],
        profile,
        employment_track="full_time",
    )

    assert [action.value for action in actions] == [
        "Rice University",
        "Machine Learning Engineer",
        "Houston, Texas",
        "Python; FastAPI; Azure",
        "I am interested in the team's applied AI work.",
    ]
    assert actions[3].action == "select"
    assert actions[4].answer_source == "custom_answer"


def test_additional_context_is_separate_from_cover_letter() -> None:
    profile = CandidateProfile(
        custom_answers={
            "Cover letter": "A role-specific introduction.",
            "Additional application context": "Available after the current semester.",
        }
    )
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="hiring-message",
                label="Message to the Hiring Team",
                input_type="textarea",
            ),
            FormQuestion(
                field_id="additional",
                label="Anything else we should know?",
                input_type="textarea",
            ),
        ],
        profile,
        employment_track="internship",
    )

    assert actions[0].value == "A role-specific introduction."
    assert actions[1].value == "Available after the current semester."


def test_repeated_education_and_experience_fields_use_matching_records() -> None:
    profile = CandidateProfile(
        educations=[
            {"school": "Rice University", "degree": "MS", "major": "Computer Science"},
            {"school": "UT Austin", "degree": "BS", "major": "Mathematics"},
        ],
        work_experiences=[
            WorkExperienceProfile(job_title="ML Engineer", company="Acme"),
            WorkExperienceProfile(job_title="Data Scientist", company="Beta"),
        ],
    )
    questions = [
        FormQuestion(field_id="school-1", label="Education Institution", input_type="text", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="degree-1", label="Education Degree", input_type="text", profile_record_kind="education", profile_record_index=0),
        FormQuestion(field_id="school-2", label="Education Institution", input_type="text", profile_record_kind="education", profile_record_index=1),
        FormQuestion(field_id="degree-2", label="Education Degree", input_type="text", profile_record_kind="education", profile_record_index=1),
        FormQuestion(field_id="title-1", label="Experience Title", input_type="text", profile_record_kind="work_experience", profile_record_index=0),
        FormQuestion(field_id="company-1", label="Experience Company", input_type="text", profile_record_kind="work_experience", profile_record_index=0),
        FormQuestion(field_id="title-2", label="Experience Title", input_type="text", profile_record_kind="work_experience", profile_record_index=1),
        FormQuestion(field_id="company-2", label="Experience Company", input_type="text", profile_record_kind="work_experience", profile_record_index=1),
    ]

    actions = resolve_form_questions(questions, profile, employment_track="full_time")

    assert [action.value for action in actions] == [
        "Rice University",
        "MS",
        "UT Austin",
        "BS",
        "ML Engineer",
        "Acme",
        "Data Scientist",
        "Beta",
    ]


def test_explicit_profile_record_index_supports_one_open_workday_editor() -> None:
    profile = CandidateProfile(
        educations=[
            {"school": "Rice University", "degree": "MS"},
            {"school": "UT Austin", "degree": "BS"},
        ],
        work_experiences=[
            WorkExperienceProfile(job_title="ML Engineer", company="Acme"),
            WorkExperienceProfile(job_title="Data Scientist", company="Beta"),
        ],
    )
    actions = resolve_form_questions(
        [
            FormQuestion(
                field_id="school",
                label="Education Institution",
                input_type="text",
                profile_record_kind="education",
                profile_record_index=1,
            ),
            FormQuestion(
                field_id="title",
                label="Experience Title",
                input_type="text",
                profile_record_kind="work_experience",
                profile_record_index=1,
            ),
        ],
        profile,
        employment_track="full_time",
    )

    assert [action.value for action in actions] == ["UT Austin", "Data Scientist"]


def test_workday_date_components_current_flags_and_skill_search() -> None:
    profile = CandidateProfile(
        skills=["Python", "FastAPI", "Azure"],
        educations=[
            {
                "school": "Rice University",
                "start_date": "2024-08",
                "end_date": "2026-05",
                "currently_studying": True,
            }
        ],
        work_experiences=[
            WorkExperienceProfile(
                company="ApplyTeX Labs",
                start_date="2023-11",
                end_date="2025-08",
                currently_working=False,
            )
        ],
    )
    questions = [
        FormQuestion(field_id="edu-month", label="Education From Month", input_type="select", options=["August", "September"], profile_record_kind="education", profile_record_index=0, date_boundary="start", date_component="month"),
        FormQuestion(field_id="edu-year", label="Education From Year", input_type="select", options=["2023", "2024"], profile_record_kind="education", profile_record_index=0, date_boundary="start", date_component="year"),
        FormQuestion(field_id="work-month", label="Experience To Month", input_type="select", options=["July", "August"], profile_record_kind="work_experience", profile_record_index=0, date_boundary="end", date_component="month"),
        FormQuestion(field_id="work-year", label="Experience To Year", input_type="select", options=["2025", "2026"], profile_record_kind="work_experience", profile_record_index=0, date_boundary="end", date_component="year"),
        FormQuestion(field_id="studying", label="Education Currently Enrolled", input_type="checkbox"),
        FormQuestion(field_id="working", label="Experience I currently work here", input_type="checkbox"),
        FormQuestion(field_id="skills", label="Search Skills*", input_type="select"),
    ]

    actions = resolve_form_questions(questions, profile, employment_track="full_time")

    assert [action.value for action in actions] == [
        "08",
        "2024",
        "08",
        "2025",
        True,
        False,
        "Python; FastAPI; Azure",
    ]


def test_workday_live_date_metadata_and_type_to_add_skills() -> None:
    profile = CandidateProfile(
        skills=["Python", "FastAPI", "A WS SageMaker", "G CP"],
        educations=[
            EducationProfile(
                school="University of Houston",
                start_date="2025-08",
                end_date="2027-05",
            )
        ],
        work_experiences=[
            WorkExperienceProfile(
                company="Accenture",
                start_date="2023-11",
                end_date="2025-08",
            )
        ],
    )
    questions = [
        FormQuestion(field_id="work-start-month", label="Month", input_type="text", profile_record_kind="work_experience", profile_record_index=0, date_boundary="start", date_component="month"),
        FormQuestion(field_id="work-start-year", label="Year", input_type="text", profile_record_kind="work_experience", profile_record_index=0, date_boundary="start", date_component="year"),
        FormQuestion(field_id="work-end-month", label="Month", input_type="text", profile_record_kind="work_experience", profile_record_index=0, date_boundary="end", date_component="month"),
        FormQuestion(field_id="work-end-year", label="Year", input_type="text", profile_record_kind="work_experience", profile_record_index=0, date_boundary="end", date_component="year"),
        FormQuestion(field_id="education-start", label="Year", input_type="text", profile_record_kind="education", profile_record_index=0, date_boundary="start", date_component="year"),
        FormQuestion(field_id="education-end", label="Year", input_type="text", profile_record_kind="education", profile_record_index=0, date_boundary="end", date_component="year"),
        FormQuestion(field_id="skills", label="Type to Add Skills", input_type="select", control_kind="multi_select"),
    ]

    actions = resolve_form_questions(
        questions,
        profile,
        employment_track="full_time",
        provider="workday",
    )

    assert [action.value for action in actions[:6]] == ["11", "2023", "08", "2025", "2025", "2027"]
    assert actions[6].action == "select_many"
    assert actions[6].value == ["Python", "FastAPI", "AWS SageMaker", "GCP"]


def test_skill_specific_custom_answers_are_fillable_without_guessing() -> None:
    profile = CandidateProfile(
        custom_answers={
            "LLM evaluation years": "Yes",
            "RAG systems experience": "Built RAG services with retrieval evaluation and monitoring.",
        }
    )
    questions = [
        FormQuestion(
            field_id="llm-years",
            label="Do you have at least 2 years of LLM evaluation experience?",
            input_type="radio",
            required=True,
            options=["Yes", "No"],
        ),
        FormQuestion(
            field_id="rag-summary",
            label="Please summarize your experience with RAG systems.",
            input_type="textarea",
            required=True,
        ),
        FormQuestion(
            field_id="vector-summary",
            label="Please summarize your experience with vector databases.",
            input_type="textarea",
            required=True,
        ),
    ]

    actions = resolve_form_questions(questions, profile, employment_track="full_time")

    assert actions[0].action == "select"
    assert actions[0].value == "Yes"
    assert actions[0].answer_source == "custom_answer"
    assert actions[1].action == "fill"
    assert actions[1].value == "Built RAG services with retrieval evaluation and monitoring."
    assert actions[1].answer_source == "custom_answer"
    assert actions[2].action == "skip"
    assert actions[2].answer_source == "user_input"


def test_upload_status_rerender_and_delete() -> None:
    sample = SAMPLE_PATH.read_bytes()

    with TestClient(create_app()) as client:
        uploaded = client.post(
            "/latex/upload",
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        upload_body = uploaded.json()
        session_id = upload_body["session_id"]
        assert "summary" in upload_body["editable"]
        assert upload_body["filename"] == "sample_resume.tex"

        status = client.get(f"/latex/{session_id}/status")
        assert status.status_code == 200
        assert status.json()["optimized"] is False

        rerendered = client.post(
            f"/latex/{session_id}/rerender",
            json={
                "changes": {
                    "summary_0": (
                        "Software engineer building reliable backend services "
                        "and production data pipelines."
                    )
                }
            },
        )
        assert rerendered.status_code == 200
        rerender_body = rerendered.json()
        assert rerender_body["applied"] == ["summary_0"]
        assert "production data pipelines" in rerender_body["modified_latex"]
        assert rerender_body["page_count"] == 1

        deleted = client.delete(f"/latex/{session_id}")
        assert deleted.status_code == 200
        assert client.get(f"/latex/{session_id}/status").status_code == 404


def test_upload_rejects_non_tex_file() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/latex/upload",
            files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
        )

    assert response.status_code == 400


class _StubJobSearchService:
    async def search(self, query, sources, preferences=None) -> JobSearchResult:
        return JobSearchResult(
            search_id="search-test",
            query=query,
            sources=sources,
            jobs=[
                JobPosting(
                    job_id="job-test",
                    provider=JobProvider.GREENHOUSE,
                    board_token="example",
                    external_id="123",
                    company="Example",
                    title="Machine Learning Engineer",
                    description="Build machine learning systems.",
                    location="Remote",
                    workplace_type="remote",
                    source_url="https://example.test/jobs/123",
                    apply_url="https://example.test/jobs/123",
                    retrieved_at=utc_now(),
                )
            ],
        )


def test_job_search_and_application_contract(tmp_path: Path) -> None:
    app = create_app(
        job_search_service=_StubJobSearchService(),
        application_store=ApplicationStore(tmp_path / "api.db"),
    )
    with TestClient(app) as client:
        searched = client.post(
            "/jobs/search",
            json={
                "query": {"text": "machine learning"},
                "sources": [
                    {
                        "provider": "greenhouse",
                        "board_token": "example",
                        "company": "Example",
                    }
                ],
            },
        )
        assert searched.status_code == 200
        assert searched.json()["jobs"][0]["job_id"] == "job-test"

        created = client.post("/applications", json={"job_id": "job-test"})
        assert created.status_code == 200
        application_id = created.json()["application_id"]

        unsafe = client.post(
            f"/applications/{application_id}/transition",
            json={"status": "submitting"},
        )
        assert unsafe.status_code == 409

        listed = client.get("/jobs")
        assert listed.status_code == 200
        assert listed.json()[0]["title"] == "Machine Learning Engineer"


def test_application_create_is_idempotent_per_profile_and_job(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "dedupe_api.db")
    job = JobPosting(
        job_id="job-idempotent",
        provider=JobProvider.LEVER,
        board_token="example",
        external_id="lever-1",
        company="Example",
        title="Data Science Intern",
        description="Build Python and SQL models.",
        source_url="https://jobs.lever.co/example/lever-1",
        apply_url="https://jobs.lever.co/example/lever-1/apply",
    )
    store.save_job(job)
    app = create_app(application_store=store)

    with TestClient(app) as client:
        first = client.post(
            "/applications",
            json={"job_id": job.job_id, "profile_id": "ashrith"},
        )
        second = client.post(
            "/applications",
            json={"job_id": job.job_id, "profile_id": "ashrith"},
        )
        forced = client.post(
            "/applications",
            json={"job_id": job.job_id, "profile_id": "ashrith", "force_new": True},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert forced.status_code == 200
    assert first.json()["application_id"] == second.json()["application_id"]
    assert forced.json()["application_id"] != first.json()["application_id"]
    assert first.json()["profile_id"] == "ashrith"


def test_application_dedupe_preserves_related_records(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "dedupe_store.db")
    job = JobPosting(
        job_id="job-duplicates",
        provider=JobProvider.WORKDAY,
        board_token="example",
        external_id="JR-42",
        company="Example AI",
        title="ML Engineer",
        description="Python, Azure, and SQL.",
        source_url="https://example.test/jobs/JR-42",
        apply_url="https://example.test/jobs/JR-42/apply",
    )
    store.save_job(job)
    primary = store.create_application(job.job_id, notes="Primary note")
    duplicate = store.create_application(job.job_id, notes="Duplicate note")
    store.update_application(duplicate.application_id, {"priority": "high", "missing_answers_count": 2})
    event = store.create_application_event(
        application_id=duplicate.application_id,
        kind="test_event",
        label="Duplicate event",
    )
    task = store.create_application_task(
        application_id=duplicate.application_id,
        title="Duplicate task",
    )
    artifact = ApplicationArtifact(
        artifact_id="duplicate-artifact",
        application_id=duplicate.application_id,
        job_id=job.job_id,
        type=ApplicationArtifactType.TAILORED_RESUME,
        status=ApplicationArtifactStatus.APPROVED,
        filename="duplicate.pdf",
        pdf_b64=base64.b64encode(b"%PDF-1.4\n").decode(),
        ats_after={"score": 87.0, "required_missing": ["Azure"]},
    )
    store.save_application_artifact(artifact)
    scan = FormScan(
        scan_id="duplicate-scan",
        application_id=duplicate.application_id,
        provider=JobProvider.WORKDAY,
        page_url="https://example.test/jobs/JR-42/apply",
        questions=[FormQuestion(field_id="name", label="Name", input_type="text", required=True)],
    )
    store.save_form_scan(scan)

    assert store.dedupe_applications() == 1
    applications = store.list_applications()
    assert len(applications) == 1
    canonical = applications[0]
    assert canonical.application_id == duplicate.application_id
    assert canonical.latest_resume_artifact_id == "duplicate-artifact"
    assert canonical.fit_score == 87.0
    assert canonical.tailored_resume_score == 87.0
    assert canonical.priority == "high"
    assert "Primary note" in canonical.notes
    assert "Duplicate note" in canonical.notes
    assert store.get_application(primary.application_id) is None
    assert any(item.event_id == event.event_id for item in store.list_application_events(canonical.application_id))
    assert any(item.task_id == task.task_id for item in store.list_application_tasks(canonical.application_id))
    latest_scan = store.get_latest_form_scan(canonical.application_id)
    assert latest_scan is not None
    assert latest_scan.scan_id == "duplicate-scan"


def test_application_score_endpoint_persists_current_resume_snapshot(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "score.db")
    job = JobPosting(
        job_id="job-score",
        provider=JobProvider.GREENHOUSE,
        board_token="example",
        external_id="101",
        company="Example Data",
        title="Machine Learning Engineer",
        description="Python SQL machine learning Azure data pipelines.",
        source_url="https://boards.greenhouse.io/example/jobs/101",
        apply_url="https://boards.greenhouse.io/example/jobs/101",
    )
    store.save_job(job)
    store.save_candidate_profile(
        CandidateProfile(
            profile_id="default",
            resume_filename="sample_resume.tex",
            resume_latex_source=SAMPLE_PATH.read_text(encoding="utf-8"),
        )
    )
    app = create_app(application_store=store)

    with TestClient(app) as client:
        created = client.post("/applications", json={"job_id": job.job_id})
        assert created.status_code == 200
        application_id = created.json()["application_id"]
        scored = client.post(f"/applications/{application_id}/score", json={})
        health = client.get("/applications/health")

    assert scored.status_code == 200
    body = scored.json()
    assert body["application"]["current_resume_score"] is not None
    assert body["application"]["score_updated_at"]
    assert isinstance(body["analysis"]["baseline_ats"]["required_missing"], list)
    assert health.status_code == 200
    assert health.json()["average_current_resume_score"] is not None


def test_tailor_session_ranks_and_persists_project_selection(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "tailor-projects.db")
    job = JobPosting(
        job_id="job-tailor-projects",
        provider=JobProvider.GREENHOUSE,
        board_token="example",
        external_id="tailor-projects",
        company="Example Vision",
        title="Machine Learning Engineer",
        description="Need Python FastAPI model inference, classifier APIs, and AWS deployment.",
        source_url="https://boards.greenhouse.io/example/jobs/tailor-projects",
        apply_url="https://boards.greenhouse.io/example/jobs/tailor-projects",
    )
    store.save_job(job)
    store.save_candidate_profile(
        CandidateProfile(
            profile_id="default",
            resume_filename="sample_resume.tex",
            resume_latex_source=SAMPLE_PATH.read_text(encoding="utf-8"),
        )
    )
    app = create_app(application_store=store)

    with TestClient(app) as client:
        created = client.post("/tailor/sessions", json={"job_id": job.job_id})
        assert created.status_code == 200
        body = created.json()
        session_id = body["session_id"]
        recommendations = body["project_recommendations"]
        selected = body["selected_project_ids"]
        assert recommendations
        assert selected == [recommendations[0]["project"]["project_id"]]

        patched = client.patch(
            f"/tailor/sessions/{session_id}/projects",
            json={"selected_project_ids": selected},
        )
        loaded = client.get(f"/tailor/sessions/{session_id}")
        projects = client.get("/profile/projects")

    assert patched.status_code == 200
    assert patched.json()["selected_project_ids"] == selected
    assert loaded.status_code == 200
    assert loaded.json()["selected_project_ids"] == selected
    assert projects.status_code == 200
    assert projects.json()[0]["source"] == "resume"


def test_profile_browser_capture_and_read_only_fill_plan(tmp_path: Path) -> None:
    app = create_app(
        job_search_service=_StubJobSearchService(),
        application_store=ApplicationStore(tmp_path / "extension.db"),
    )
    with TestClient(app) as client:
        profile = client.get("/profile")
        assert profile.status_code == 200
        assert profile.json()["work_authorization"] == {
            "authorized_to_work_in_us": None,
            "requires_sponsorship": None,
            "current_requires_sponsorship": None,
            "future_requires_sponsorship": None,
            "internship_requires_sponsorship": None,
            "full_time_requires_sponsorship": None,
        }
        updated_profile = profile.json()
        updated_profile["full_name"] = "Test Candidate"
        updated_profile["first_name"] = "Test"
        updated_profile["last_name"] = "Candidate"
        updated_profile["work_authorization"] = {
            "authorized_to_work_in_us": True,
            "requires_sponsorship": False,
            "current_requires_sponsorship": False,
            "future_requires_sponsorship": False,
            "internship_requires_sponsorship": False,
            "full_time_requires_sponsorship": False,
        }
        updated_profile["equal_opportunity"]["allow_autofill"] = True
        updated_profile["equal_opportunity"]["hispanic_or_latino"] = "No"
        assert client.put("/profile", json=updated_profile).status_code == 200

        setup = client.get("/profile/setup-questions")
        assert setup.status_code == 200
        assert "Full legal name" not in setup.json()["missing_required"]

        captured = client.post(
            "/extension/jobs/capture",
            json={
                "provider": "linkedin",
                "external_id": "linkedin-123",
                "company": "Example",
                "title": "Machine Learning Intern",
                "description": "US internship building ML systems.",
                "location": "Houston, TX",
                "source_url": "https://www.linkedin.com/jobs/view/123",
                "apply_url": "https://www.linkedin.com/jobs/view/123",
            },
        )
        assert captured.status_code == 200
        assert captured.json()["target_role"] == "ml_intern"
        assert captured.json()["employment_track"] == "internship"

        application = client.post(
            "/applications",
            json={"job_id": captured.json()["job_id"]},
        )
        assert application.status_code == 200

        scan = client.post(
            "/extension/forms/scan",
            json={
                "application_id": application.json()["application_id"],
                "provider": "linkedin",
                "page_url": "https://www.linkedin.com/jobs/view/123/apply",
                "page_title": "Apply",
                "questions": [
                    {
                        "field_id": "name",
                        "label": "First name",
                        "input_type": "text",
                        "required": True,
                    },
                    {
                        "field_id": "sponsor",
                        "label": "Will you require sponsorship?",
                        "input_type": "select",
                        "required": True,
                    },
                    {
                        "field_id": "hispanic",
                        "label": "Are you Hispanic/Latino?",
                        "input_type": "radio",
                        "required": False,
                        "sensitive": True,
                        "options": ["Yes", "No", "Prefer not to answer"],
                    },
                ],
            },
        )
        assert scan.status_code == 200

        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")
        assert plan.status_code == 200
        plan_body = plan.json()
        assert plan_body["page_url"] == "https://www.linkedin.com/jobs/view/123/apply"
        assert plan_body["can_submit"] is False
        assert plan_body["can_fill"] is True
        assert plan_body["actions"][0]["value"] == "Test"
        assert plan_body["actions"][1]["value"] == "No"
        assert plan_body["actions"][2]["value"] == "No"
        assert plan_body["unresolved_required"] == []


def test_workday_application_questions_plan_keeps_all_fields_and_typed_answers(
    tmp_path: Path,
) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "workday-questions.db"))
    future_sponsorship = (
        "Will you in the future require sponsorship for work visa status (e.g. H-1B visa) in the country for which you are applying? "
        "Please note that if you currently have CPT or OPT work authorization and will not have any other basis for work authorization "
        "after the expiration of your OPT, you must answer yes to this question.*"
    )
    labels = [
        "Are you legally eligible to work in the country to which you are applying?*",
        "Do you currently require sponsorship for work visa status (e.g. H-1B visa) to work in the country you are applying?*",
        future_sponsorship,
        "Are you at least 18 years of age?*",
        "What is your highest level of completed education?*",
        "What is your desired income? (Hourly, Monthly, or Annual)*",
        "Are you currently employed by Daikin Applied? Internal candidates must apply through the internal Workday Jobs Hub.*",
        "Are you currently employed by a Daikin Subsidiary, Daikin Majority Owned Representative, or Member of Daikin Group?*",
        "Are you willing to relocate if required by the position?*",
        "Are you willing to travel if required by the position?*",
        "Do you currently have an active non-compete and/or non-solicit?*",
    ]
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["work_authorization"].update(
            {
                "authorized_to_work_in_us": True,
                "current_requires_sponsorship": False,
                "future_requires_sponsorship": True,
            }
        )
        profile["application_facts"] = {
            "is_at_least_18": True,
            "willing_to_relocate": True,
            "willing_to_travel": True,
            "active_non_compete_or_non_solicit": False,
            "company_relationships": {
                "Daikin": {
                    "currently_employed": False,
                    "employed_by_affiliate": False,
                    "previously_employed": False,
                }
            },
            "compensation_preferences": [
                {
                    "employment_type": "internship",
                    "amount": "75000",
                    "currency": "USD",
                    "period": "annual",
                }
            ],
        }
        profile["educations"] = [
            {
                "school": "University of Houston",
                "degree": "M.S. in Engineering Data Science & Artificial Intelligence",
                "degree_level": "MS",
                "end_date": "2027-05",
            },
            {
                "school": "Amrita School of Engineering",
                "degree": "B.Tech in Computer Science & Engineering",
                "degree_level": "BS",
                "end_date": "2023-05",
            },
        ]
        assert client.put("/profile", json=profile).status_code == 200

        captured = client.post(
            "/extension/jobs/capture",
            json={
                "provider": "workday",
                "external_id": "daikin-questions",
                "company": "Daikin Careers",
                "title": "Graduate Engineering Intern",
                "description": "Engineering internship in building controls modeling and simulation.",
                "source_url": "https://daikincomfort.wd1.myworkdayjobs.com/DaikinCareers/job/daikin-questions",
                "apply_url": "https://daikincomfort.wd1.myworkdayjobs.com/DaikinCareers/job/daikin-questions/apply",
            },
        )
        application = client.post("/applications", json={"job_id": captured.json()["job_id"]})
        questions = []
        for index, label in enumerate(labels):
            is_income = "desired income" in label.casefold()
            questions.append(
                {
                    "field_id": f"primaryQuestionnaire--{index}",
                    "label": label,
                    "input_type": "textarea" if is_income else "select",
                    "control_kind": "scalar" if is_income else "custom_select",
                    "required": True,
                    "options": [] if is_income else ["Yes", "No", "Bachelor's Degree"],
                    "current_value_present": index in {0, 8},
                    "current_value": "No" if index in {0, 8} else None,
                }
            )
        scan = client.post(
            "/extension/forms/scan",
            json={
                "application_id": application.json()["application_id"],
                "provider": "workday",
                "page_url": "https://daikincomfort.wd1.myworkdayjobs.com/apply/questions",
                "page_title": "Application Questions",
                "step_key": "Application Questions",
                "form_signature": "workday:application-questions:11:abc123",
                "questions": questions,
            },
        )
        assert scan.status_code == 200
        assert len(scan.json()["questions"]) == 11
        assert len([item for item in scan.json()["questions"] if item["required"]]) == 11
        assert scan.json()["questions"][2]["label"] == future_sponsorship
        assert len(scan.json()["questions"][2]["label"]) > 320
        assert scan.json()["form_signature"] == "workday:application-questions:11:abc123"

        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")
        assert plan.status_code == 200
        body = plan.json()

    assert [action["value"] for action in body["actions"]] == [
        "Yes",
        "No",
        "Yes",
        "Yes",
        "Bachelor's Degree",
        "75000",
        "No",
        "No",
        "Yes",
        "Yes",
        "No",
    ]
    assert body["unresolved_required"] == []
    assert body["ready_action_count"] == 11
    assert body["review_items"][0]["change_kind"] == "replace"
    assert body["review_items"][8]["change_kind"] == "replace"
    assert body["review_items"][2]["question_intent"] == "future_sponsorship"
    assert body["review_items"][5]["question_intent"] == "compensation"
    assert body["review_items"][5]["draft_eligible"] is False
    assert "explicit profile fact" in body["review_items"][0]["resolution_reason"]


def test_fill_plan_allows_partial_fill_and_overrides(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "partial-fill.db")
    store.save_candidate_profile(
        CandidateProfile(
            profile_id="default",
            full_name="Test Candidate",
            first_name="Test",
            last_name="Candidate",
        )
    )
    app = create_app(application_store=store)
    with TestClient(app) as client:
        scan = client.post(
            "/extension/forms/scan",
            json={
                "provider": "greenhouse",
                "page_url": "https://boards.greenhouse.io/example/jobs/1",
                "questions": [
                    {
                        "field_id": "name",
                        "label": "First name",
                        "input_type": "text",
                        "required": True,
                    },
                    {
                        "field_id": "why",
                        "label": "Why do you want this role?",
                        "input_type": "textarea",
                        "required": True,
                    },
                ],
            },
        )
        assert scan.status_code == 200
        scan_id = scan.json()["scan_id"]
        plan = client.get(f"/extension/forms/{scan_id}/plan")
        assert plan.status_code == 200
        body = plan.json()
        assert body["ready_action_count"] >= 1
        assert body["can_fill"] is True
        assert body["unresolved_required"]
        assert any(action["field_id"] == "name" and action["action"] == "fill" for action in body["actions"])
        assert any(action["field_id"] == "why" and action["action"] == "skip" for action in body["actions"])

        overridden = client.post(
            f"/extension/forms/{scan_id}/plan",
            json={"overrides": {"why": "I build production ML systems."}},
        )
        assert overridden.status_code == 200
        override_body = overridden.json()
        why_action = next(action for action in override_body["actions"] if action["field_id"] == "why")
        assert why_action["action"] == "fill"
        assert why_action["value"] == "I build production ML systems."
        assert why_action["answer_source"] == "user_input"
        assert override_body["unresolved_required"] == []

    app = create_app(application_store=ApplicationStore(tmp_path / "existing-values.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["email"] = "profile@example.test"
        assert client.put("/profile", json=profile).status_code == 200
        scan = client.post(
            "/extension/forms/scan",
            json={
                "provider": "greenhouse",
                "page_url": "https://job-boards.greenhouse.io/example/jobs/123",
                "questions": [
                    {
                        "field_id": "email",
                        "label": "Email",
                        "input_type": "email",
                        "required": True,
                        "current_value_present": True,
                    }
                ],
            },
        )
        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")

    assert plan.status_code == 200
    body = plan.json()
    assert body["actions"][0]["action"] == "skip"
    assert body["actions"][0]["value"] is None
    assert body["review_items"][0]["answer_source"] == "already_on_page"
    assert body["review_items"][0]["value_preview"] == "Already filled"


def test_fill_plan_keeps_equivalent_country_and_state_values(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "geo-keep.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["address"] = {
            **profile.get("address", {}),
            "country": "United States",
            "state": "Texas",
        }
        assert client.put("/profile", json=profile).status_code == 200
        scan = client.post(
            "/extension/forms/scan",
            json={
                "provider": "workday",
                "page_url": "https://example.myworkdayjobs.com/apply",
                "questions": [
                    {
                        "field_id": "country",
                        "label": "Country",
                        "input_type": "select",
                        "required": True,
                        "options": ["United States of America", "Canada"],
                        "current_value_present": True,
                        "current_value": "United States of America",
                    },
                    {
                        "field_id": "state",
                        "label": "State",
                        "input_type": "select",
                        "required": True,
                        "options": ["TX", "CA"],
                        "current_value_present": True,
                        "current_value": "TX",
                    },
                ],
            },
        )
        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")

    body = plan.json()
    assert body["actions"][0]["action"] == "skip"
    assert body["review_items"][0]["change_kind"] == "keep"
    assert body["review_items"][0]["answer_source"] == "already_on_page"
    assert body["actions"][1]["action"] == "skip"
    assert body["review_items"][1]["change_kind"] == "keep"
    assert body["review_items"][1]["answer_source"] == "already_on_page"


def test_fill_plan_accepts_generated_multi_select_override(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "multi-select-override.db")
    app = create_app(application_store=store)
    with TestClient(app) as client:
        scan_response = client.post(
            "/extension/forms/scan",
            json={
                "provider": "ashby",
                "page_url": "https://jobs.ashbyhq.com/cohere/application",
                "questions": [
                    {
                        "field_id": "engineering-interests",
                        "label": "What area of software engineering interests you the most?",
                        "input_type": "checkbox",
                        "control_kind": "multi_select",
                        "required": True,
                        "options": [
                            "Backend Development",
                            "Frontend Development",
                            "Full-stack Development",
                        ],
                    }
                ],
            },
        )
        scan_id = scan_response.json()["scan_id"]
        response = client.post(
            f"/extension/forms/{scan_id}/plan",
            json={
                "overrides": {
                    "engineering-interests": ["Backend Development", "Full-stack Development"]
                },
                "answer_source": "generated",
            },
        )

    assert response.status_code == 200
    action = response.json()["actions"][0]
    assert action["action"] == "select_many"
    assert action["value"] == ["Backend Development", "Full-stack Development"]
    assert action["answer_source"] == "generated"
    saved = store.get_form_scan(scan_id)
    assert saved is not None
    assert saved.plan_overrides["engineering-interests"].answer_source == "generated"


def test_form_scan_loads_legacy_string_plan_overrides() -> None:
    scan = FormScan.model_validate(
        {
            "scan_id": "legacy-override",
            "provider": "ashby",
            "page_url": "https://jobs.ashbyhq.com/example/application",
            "plan_overrides": {"why": "A concise saved answer."},
        }
    )

    assert scan.plan_overrides["why"].value == "A concise saved answer."
    assert scan.plan_overrides["why"].answer_source == "user_input"


def test_fill_plan_keeps_an_already_uploaded_resume(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "resume-keep.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["resume_pdf_b64"] = base64.b64encode(b"saved resume").decode("ascii")
        assert client.put("/profile", json=profile).status_code == 200
        scan = client.post(
            "/extension/forms/scan",
            json={
                "provider": "ashby",
                "page_url": "https://jobs.ashbyhq.com/example/application",
                "questions": [
                    {
                        "field_id": "resume",
                        "label": "Resume",
                        "input_type": "file",
                        "required": True,
                        "current_value_present": True,
                        "current_value": "resume.pdf",
                    }
                ],
            },
        )
        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")

    body = plan.json()
    assert body["actions"][0]["action"] == "skip"
    assert body["review_items"][0]["change_kind"] == "keep"
    assert body["review_items"][0]["answer_source"] == "already_on_page"


def test_workday_fill_plan_replaces_conflicts_but_keeps_exact_values(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "workday-values.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["educations"] = [
            {
                "school": "University of Houston",
                "degree": "M.S. in Engineering Data Science & Artificial Intelligence",
                "degree_level": "MS",
                "major": "Engineering Data Science & Artificial Intelligence",
                "field_of_study_candidates": ["Data Science", "Computer Engineering"],
                "gpa": "4.0/4.0",
            }
        ]
        profile["education"] = profile["educations"][0]
        assert client.put("/profile", json=profile).status_code == 200
        scan = client.post(
            "/extension/forms/scan",
            json={
                "provider": "workday",
                "page_url": "https://example.myworkdayjobs.com/apply",
                "questions": [
                    {
                        "field_id": "school",
                        "label": "Education School or University",
                        "input_type": "text",
                        "current_value_present": True,
                        "current_value": "University of Houston",
                        "profile_record_kind": "education",
                        "profile_record_index": 0,
                    },
                    {
                        "field_id": "gpa",
                        "label": "Education Overall Result (GPA)",
                        "input_type": "text",
                        "current_value_present": True,
                        "current_value": "4.0/4.0",
                        "profile_record_kind": "education",
                        "profile_record_index": 0,
                    },
                ],
            },
        )
        plan = client.get(f"/extension/forms/{scan.json()['scan_id']}/plan")

    body = plan.json()
    assert body["actions"][0]["action"] == "skip"
    assert body["review_items"][0]["change_kind"] == "keep"
    assert body["actions"][1]["value"] == "4/4"
    assert body["review_items"][1]["change_kind"] == "replace"
    assert body["review_items"][1]["current_value_preview"] == "4.0/4.0"
    assert body["review_items"][1]["planned_value_preview"] == "4/4"


def test_profile_resume_upload_prefills_from_tex(tmp_path: Path) -> None:
    sample = SAMPLE_PATH.read_bytes()
    app = create_app(application_store=ApplicationStore(tmp_path / "prefill.db"))
    with TestClient(app) as client:
        uploaded = client.post(
            "/profile/resume",
            params={"overwrite": "true"},
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["has_latex_source"] is True
        assert "prefill_applied" in body
        assert len(body["prefill_applied"]) > 0

        profile = client.get("/profile").json()
        assert profile.get("full_name") or profile.get("email") or profile.get("skills")


def test_profile_view_excludes_raw_resume_payloads_and_patch_preserves_them(
    tmp_path: Path,
) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "profile_view.db"))
    profile = CandidateProfile(
        profile_id="tester",
        full_name="Original Name",
        email="original@example.test",
        resume_filename="resume.tex",
        resume_latex_source="\\documentclass{article}\\begin{document}secret\\end{document}",
        resume_pdf_filename="resume.pdf",
        resume_pdf_b64="JVBERi0xLjQKsecret",
        resume_updated_at="2026-07-03T00:00:00+00:00",
        skills=["Python"],
    )

    with TestClient(app) as client:
        replaced = client.put("/profile", json=profile.model_dump(mode="json"))
        assert replaced.status_code == 200

        full = client.get("/profile", params={"profile_id": "tester"})
        assert full.status_code == 200
        assert full.json()["resume_latex_source"] == profile.resume_latex_source
        assert full.json()["resume_pdf_b64"] == profile.resume_pdf_b64

        view = client.get("/profile/view", params={"profile_id": "tester"})
        assert view.status_code == 200
        view_body = view.json()
        assert "resume_latex_source" not in view_body
        assert "resume_pdf_b64" not in view_body
        assert view_body["has_latex_source"] is True
        assert view_body["has_pdf"] is True
        assert view_body["resume_filename"] == "resume.tex"

        patched = client.patch(
            "/profile",
            params={"profile_id": "tester"},
            json={
                "full_name": "Updated Name",
                "email": "updated@example.test",
                "skills": ["Python", "FastAPI"],
            },
        )
        assert patched.status_code == 200
        patched_body = patched.json()
        assert patched_body["full_name"] == "Updated Name"
        assert "resume_latex_source" not in patched_body
        assert "resume_pdf_b64" not in patched_body

        after = client.get("/profile", params={"profile_id": "tester"}).json()
        assert after["full_name"] == "Updated Name"
        assert after["email"] == "updated@example.test"
        assert after["skills"] == ["Python", "FastAPI"]
        assert after["resume_filename"] == profile.resume_filename
        assert after["resume_latex_source"] == profile.resume_latex_source
        assert after["resume_pdf_filename"] == profile.resume_pdf_filename
        assert after["resume_pdf_b64"] == profile.resume_pdf_b64
        assert after["resume_updated_at"] == profile.resume_updated_at


def test_profile_view_repairs_stale_resume_derived_work_metadata(tmp_path: Path) -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading
  {AI/ML Engineer}
  {Nov 2023 -- Aug 2025}
  {Accenture -- GenLite (Internal Enterprise AI Platform for Client Delivery)}
  {Hyderabad, India}
\begin{itemize}
  \item Built production RAG pipelines for enterprise clients.
  \item Automated code transformation workflows across programming languages.
\end{itemize}
\end{document}
"""
    app = create_app(application_store=ApplicationStore(tmp_path / "profile_repair.db"))
    profile = CandidateProfile(
        profile_id="tester",
        resume_filename="resume.tex",
        resume_latex_source=tex,
        work_experiences=[
            WorkExperienceProfile(
                company="AI/ML Engineer",
                job_title="Accenture – GenLite (Internal Enterprise AI Platform for Client Delivery)",
                location="Hyderabad, India",
                start_date="2023-11",
                end_date="2025-08",
                bullets=[],
            )
        ],
    )

    with TestClient(app) as client:
        assert client.put("/profile", json=profile.model_dump(mode="json")).status_code == 200

        view = client.get("/profile/view", params={"profile_id": "tester"})
        assert view.status_code == 200
        work = view.json()["work_experiences"][0]
        assert work["company"] == "Accenture"
        assert work["job_title"] == "AI/ML Engineer"
        assert work["location"] == "Hyderabad, India"
        assert work["bullets"] == [
            "Built production RAG pipelines for enterprise clients.",
            "Automated code transformation workflows across programming languages.",
        ]

        persisted = client.get("/profile", params={"profile_id": "tester"}).json()
        assert persisted["work_experiences"][0]["company"] == "Accenture"
        assert persisted["work_experiences"][0]["bullets"] == work["bullets"]


def test_application_store_enables_sqlite_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "wal.db"
    ApplicationStore(db_path)

    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_analyze_and_active_profile(tmp_path: Path) -> None:
    sample = SAMPLE_PATH.read_bytes()
    app = create_app(application_store=ApplicationStore(tmp_path / "analyze.db"))
    with TestClient(app) as client:
        assert client.put("/profile/active", json={"profile_id": "tester"}).status_code == 200

        uploaded = client.post(
            "/latex/upload",
            files={"file": ("sample_resume.tex", sample, "text/plain")},
        )
        assert uploaded.status_code == 200
        session_id = uploaded.json()["session_id"]

        analyzed = client.post(
            "/latex/analyze",
            json={
                "session_id": session_id,
                "job_description": "Looking for Python, FastAPI, and machine learning experience.",
                "analysis_mode": "fast",
            },
        )
        assert analyzed.status_code == 200
        body = analyzed.json()
        assert "baseline_ats" in body
        assert body["editable_statement_count"] > 0

        report = client.get(f"/latex/{session_id}/report")
        assert report.status_code == 200
        assert report.json()["optimized"] is False


def test_list_profiles_marks_usable_accounts(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "profiles-list.db"))
    with TestClient(app) as client:
        profile = client.get("/profile").json()
        profile["profile_id"] = "ashrith"
        profile["full_name"] = "Ashrith Vadde"
        profile["email"] = "ashrith@example.test"
        assert client.put("/profile", json=profile).status_code == 200
        assert client.put("/profile/active", json={"profile_id": "empty_shell"}).status_code == 200

        listed = client.get("/profiles")
        assert listed.status_code == 200
        profiles = {item["profile_id"]: item for item in listed.json()["profiles"]}
        assert profiles["ashrith"]["usable"] is True
        assert profiles["ashrith"]["full_name"] == "Ashrith Vadde"
        assert profiles["empty_shell"]["usable"] is False


def test_patch_profile_deep_merges_equal_opportunity(tmp_path: Path) -> None:
    app = create_app(application_store=ApplicationStore(tmp_path / "patch.db"))
    with TestClient(app) as client:
        base = client.get("/profile?profile_id=merge-test").json()
        base["profile_id"] = "merge-test"
        base["equal_opportunity"]["gender"] = "Male"
        base["equal_opportunity"]["disability"] = "No"
        base["equal_opportunity"]["allow_autofill"] = False
        assert client.put("/profile", json=base).status_code == 200

        patched = client.patch(
            "/profile?profile_id=merge-test",
            json={"equal_opportunity": {"allow_autofill": True}},
        )
        assert patched.status_code == 200
        eeo = patched.json()["equal_opportunity"]
        assert eeo["allow_autofill"] is True
        assert eeo["gender"] == "Male"
        assert eeo["disability"] == "No"


def test_application_detail_tracks_events_tasks_and_artifacts(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "tracker.db")
    job = JobPosting(
        job_id="job-tracker-1",
        provider=JobProvider.WORKDAY,
        board_token="example",
        external_id="JR-1",
        company="Example AI",
        title="Machine Learning Engineer",
        description="Build applied AI systems.",
        location="Austin, TX",
        source_url="https://example.test/jobs/JR-1",
        apply_url="https://example.test/jobs/JR-1/apply",
    )
    store.save_job(job)
    app = create_app(application_store=store)

    with TestClient(app) as client:
        created = client.post("/applications", json={"job_id": job.job_id})
        assert created.status_code == 200
        application_id = created.json()["application_id"]

        patched = client.patch(
            f"/applications/{application_id}",
            json={
                "stage": "selected",
                "priority": "high",
                "excitement": 5,
                "salary_range": "$120k-$140k",
                "notes": "Referral possible.",
            },
        )
        assert patched.status_code == 200
        assert patched.json()["stage"] == "selected"
        assert patched.json()["priority"] == "high"

        event = client.post(
            f"/applications/{application_id}/events",
            json={"kind": "referral", "label": "Referral requested"},
        )
        assert event.status_code == 200

        task = client.post(
            f"/applications/{application_id}/tasks",
            json={"title": "Follow up with recruiter", "category": "follow_up"},
        )
        assert task.status_code == 200

        artifact = ApplicationArtifact(
            artifact_id="artifact-1",
            application_id=application_id,
            job_id=job.job_id,
            type=ApplicationArtifactType.TAILORED_RESUME,
            status=ApplicationArtifactStatus.APPROVED,
            filename="example_ai_resume.pdf",
            pdf_b64=base64.b64encode(b"%PDF-1.4\n").decode(),
            ats_after={"score": 88.0},
        )
        store.save_application_artifact(artifact)

        latest = client.get(
            f"/applications/{application_id}/artifacts/latest",
            params={"type": "tailored_resume", "status": "approved"},
        )
        assert latest.status_code == 200
        assert latest.json()["artifact_id"] == "artifact-1"

        detail = client.get(f"/applications/{application_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["job"]["company"] == "Example AI"
        assert body["application"]["latest_resume_artifact_id"] == "artifact-1"
        assert body["application"]["fit_score"] == 88.0
        assert body["artifacts"][0]["artifact_id"] == "artifact-1"
        assert any(item["kind"] == "referral" for item in body["events"])
        assert body["tasks"][0]["title"] == "Follow up with recruiter"


def test_extension_prepare_prefers_approved_application_artifact(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "artifact_prepare.db")
    job = JobPosting(
        job_id="job-artifact-1",
        provider=JobProvider.GREENHOUSE,
        board_token="example",
        external_id="123",
        company="Example Robotics",
        title="AI Engineer",
        description="Python and reinforcement learning.",
        source_url="https://boards.greenhouse.io/example/jobs/123",
        apply_url="https://boards.greenhouse.io/example/jobs/123",
    )
    store.save_job(job)
    application = store.create_application(job.job_id)
    artifact = ApplicationArtifact(
        artifact_id="artifact-approved",
        application_id=application.application_id,
        job_id=job.job_id,
        type=ApplicationArtifactType.TAILORED_RESUME,
        status=ApplicationArtifactStatus.APPROVED,
        filename="approved_tailored.pdf",
        pdf_b64=base64.b64encode(b"%PDF-1.4 approved\n").decode(),
        ats_after={"score": 91.5},
    )
    store.save_application_artifact(artifact)
    app = create_app(application_store=store)

    with TestClient(app) as client:
        prepared = client.post(
            "/extension/resume/prepare",
            json={
                "application_id": application.application_id,
                "job_description": job.description,
                "customize": False,
            },
        )

    assert prepared.status_code == 200
    body = prepared.json()
    assert body["filename"] == "approved_tailored.pdf"
    assert body["customized"] is True
    assert body["artifact_id"] == "artifact-approved"
    assert body["artifact_status"] == "approved"
    assert body["ats_score"] == 91.5


def test_application_routes_enforce_profile_ownership_without_auth(tmp_path: Path) -> None:
    """With auth off, X-Profile-Id still cannot read another profile's application."""
    store = ApplicationStore(tmp_path / "ownership-off.db")
    job = JobPosting(
        job_id="job-alice",
        provider=JobProvider.GREENHOUSE,
        board_token="acme",
        external_id="own-1",
        company="Acme",
        title="Engineer",
        description="Python.",
        source_url="https://boards.greenhouse.io/acme/jobs/own-1",
        apply_url="https://boards.greenhouse.io/acme/jobs/own-1",
        captured_for_profile_id="alice",
    )
    store.save_job(job)
    application = store.create_application(job.job_id, profile_id="alice")
    artifact = ApplicationArtifact(
        artifact_id="artifact-alice",
        application_id=application.application_id,
        job_id=job.job_id,
        type=ApplicationArtifactType.TAILORED_RESUME,
        status=ApplicationArtifactStatus.APPROVED,
        filename="alice.pdf",
        pdf_b64=base64.b64encode(b"%PDF-1.4 alice\n").decode(),
    )
    store.save_application_artifact(artifact)
    app = create_app(application_store=store)
    app_id = application.application_id
    alice_headers = {"X-Profile-Id": "alice"}
    bob_headers = {"X-Profile-Id": "bob"}

    with TestClient(app) as client:
        assert client.get(f"/applications/{app_id}", headers=alice_headers).status_code == 200
        assert client.get(f"/applications/{app_id}", headers=bob_headers).status_code == 404
        assert (
            client.patch(
                f"/applications/{app_id}",
                headers=bob_headers,
                json={"notes": "nope"},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/applications/{app_id}/artifacts/latest",
                headers=bob_headers,
            ).status_code
            == 404
        )
        scan = client.post(
            "/extension/forms/scan",
            headers=alice_headers,
            json={
                "application_id": app_id,
                "provider": "greenhouse",
                "page_url": "https://boards.greenhouse.io/acme/jobs/own-1/apply",
                "questions": [
                    {
                        "field_id": "name",
                        "label": "Full name",
                        "input_type": "text",
                        "required": True,
                    }
                ],
            },
        )
        assert scan.status_code == 200
        scan_id = scan.json()["scan_id"]
        assert (
            client.get(
                f"/extension/forms/{scan_id}/plan",
                headers=bob_headers,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/extension/forms/{scan_id}/plan",
                headers=alice_headers,
            ).status_code
            == 200
        )
