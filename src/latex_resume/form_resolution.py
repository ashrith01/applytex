"""Deterministic candidate-profile answers for reviewed form filling."""

from __future__ import annotations

import re
from calendar import month_name
from datetime import date

from latex_resume.job_models import (
    CandidateProfile,
    CompanyRelationshipProfile,
    EducationProfile,
    FillAction,
    FormQuestion,
    QuestionIntent,
    WorkExperienceProfile,
)
from latex_resume.profile_extraction import degree_level_from_degree, field_of_study_candidates

_US_STATE_CODE_BY_NAME = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_US_STATE_CODES = frozenset(_US_STATE_CODE_BY_NAME.values())
_US_COUNTRY_ALIASES = frozenset(
    {
        "united states",
        "united states of america",
        "usa",
        "us",
        "u s",
        "u s a",
    }
)

COMMON_PROFILE_SETUP_QUESTIONS: tuple[dict[str, object], ...] = (
    {
        "key": "full_name",
        "label": "Full legal name",
        "category": "contact",
        "required": True,
    },
    {"key": "first_name", "label": "First name", "category": "contact", "required": False},
    {"key": "last_name", "label": "Last name", "category": "contact", "required": False},
    {"key": "email", "label": "Email", "category": "contact", "required": True},
    {"key": "phone", "label": "Phone", "category": "contact", "required": True},
    {
        "key": "location",
        "label": "Where are you currently located?",
        "category": "contact",
        "required": True,
    },
    {"key": "address.line1", "label": "Address line 1", "category": "contact", "required": False},
    {"key": "address.line2", "label": "Address line 2", "category": "contact", "required": False},
    {"key": "address.city", "label": "City", "category": "contact", "required": True},
    {"key": "address.county", "label": "County", "category": "contact", "required": False},
    {"key": "address.state", "label": "State", "category": "contact", "required": True},
    {"key": "address.postal_code", "label": "ZIP/postal code", "category": "contact", "required": True},
    {"key": "linkedin_url", "label": "LinkedIn URL", "category": "links", "required": True},
    {"key": "portfolio_url", "label": "Portfolio URL", "category": "links", "required": False},
    {"key": "github_url", "label": "GitHub URL", "category": "links", "required": False},
    {
        "key": "resume_pdf_b64",
        "label": "Profile resume",
        "category": "resume",
        "required": True,
    },
    {"key": "education.school", "label": "School", "category": "education", "required": True},
    {"key": "education.degree", "label": "Degree", "category": "education", "required": True},
    {"key": "education.major", "label": "Major / field of study", "category": "education", "required": True},
    {
        "key": "education.graduation_month",
        "label": "Graduation month",
        "category": "education",
        "required": False,
    },
    {
        "key": "education.graduation_year",
        "label": "Graduation year",
        "category": "education",
        "required": True,
    },
    {"key": "education.gpa", "label": "GPA", "category": "education", "required": False},
    {
        "key": "work_experiences",
        "label": "Work experience",
        "category": "work_experience",
        "required": False,
    },
    {
        "key": "work_authorization.authorized_to_work_in_us",
        "label": "Authorized to work in the US",
        "category": "authorization",
        "required": True,
    },
    {
        "key": "work_authorization.requires_sponsorship",
        "label": "Requires sponsorship",
        "category": "authorization",
        "required": True,
    },
    {
        "key": "custom_answers.Preferred name",
        "label": "Preferred name",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Phone device type",
        "label": "Phone device type",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Pronouns",
        "label": "Pronouns",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Earliest start date",
        "label": "Earliest start date",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Desired salary",
        "label": "Desired salary",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Compensation expectations",
        "label": "Compensation expectations",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Open to relocate",
        "label": "Open to relocate",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Reliable commute",
        "label": "Reliable commute",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Previously employed by company",
        "label": "Previously employed by company",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Security clearance",
        "label": "Security clearance",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Available to work weekends",
        "label": "Available to work weekends",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Onsite availability",
        "label": "Able to work onsite or hybrid",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.SMS consent",
        "label": "Consent to application SMS updates",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Relevant project link",
        "label": "Relevant project link",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Why this role",
        "label": "Why this role",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Production AI system summary",
        "label": "Production AI system summary",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.How did you hear about us?",
        "label": "How did you hear about us?",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Cover letter",
        "label": "Reusable short cover letter",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "custom_answers.Additional application context",
        "label": "Additional application context",
        "category": "common_application",
        "required": False,
    },
    {
        "key": "equal_opportunity.allow_autofill",
        "label": "Allow voluntary EEO autofill",
        "category": "voluntary_eeo",
        "required": False,
    },
    {"key": "equal_opportunity.gender", "label": "Gender", "category": "voluntary_eeo", "required": False},
    {"key": "equal_opportunity.race", "label": "Race", "category": "voluntary_eeo", "required": False},
    {
        "key": "equal_opportunity.hispanic_or_latino",
        "label": "Hispanic or Latino",
        "category": "voluntary_eeo",
        "required": False,
    },
    {
        "key": "equal_opportunity.disability",
        "label": "Disability status",
        "category": "voluntary_eeo",
        "required": False,
    },
    {
        "key": "equal_opportunity.veteran_status",
        "label": "Veteran status",
        "category": "voluntary_eeo",
        "required": False,
    },
    {
        "key": "equal_opportunity.lgbtq",
        "label": "LGBTQ+ identity",
        "category": "voluntary_eeo",
        "required": False,
    },
    {
        "key": "equal_opportunity.sexual_orientation",
        "label": "Sexual orientation",
        "category": "voluntary_eeo",
        "required": False,
    },
    {
        "key": "equal_opportunity.pronouns",
        "label": "Pronouns",
        "category": "voluntary_eeo",
        "required": False,
    },
)

_SENSITIVE_PATTERNS = (
    "race",
    "ethnicity",
    "gender",
    "disability",
    "veteran",
    "sexual orientation",
    "date of birth",
    "social security",
)

_CUSTOM_ANSWER_ALIASES: dict[str, tuple[str, ...]] = {
    "Phone device type": (
        "phone type",
        "telephone device type",
    ),
    "Earliest start date": (
        "earliest available start date",
        "available start date",
        "when can you start",
    ),
    "Desired salary": (
        "compensation expectations",
        "salary expectations",
        "desired income",
        "desired pay range",
        "pay range",
    ),
    "Compensation expectations": (
        "desired salary",
        "salary expectations",
        "desired income",
        "desired pay range",
        "pay range",
    ),
    "Open to relocate": (
        "willing to relocate",
        "relocation",
    ),
    "Reliable commute": (
        "reliably commute",
        "commute to this job",
        "commute to",
    ),
    "Previously employed by company": (
        "previously employed",
        "previous employer",
        "former employee",
        "ever worked",
        "worked for this company",
        "worked at this company",
        "worked for us",
        "worked at our company",
    ),
    "Bound by restrictive agreements": (
        "bound by any agreements",
        "non-compete",
        "non-solicitation",
        "confidentiality",
        "non-disclosure",
        "contractual obligation",
        "restrict your ability",
        "restrictive agreements",
    ),
    "Security clearance": (
        "active security clearance",
        "security clearance",
        "clearance",
    ),
    "Available to work weekends": (
        "available to work weekends",
        "work weekends",
        "weekend availability",
    ),
    "Onsite availability": (
        "able to work from our office",
        "work from our office",
        "work onsite",
        "work on-site",
        "in office",
        "in-office",
    ),
    "SMS consent": (
        "consent to receive sms",
        "receive sms updates",
        "text message updates",
        "application sms",
    ),
    "Relevant project link": (
        "project most relevant",
        "relevant project",
        "project link",
    ),
    "Why this role": (
        "why are you interested",
        "why interested in this role",
        "interested in this role",
        "interested in this startup",
    ),
    "Production AI system summary": (
        "production ai system",
        "ai system you shipped",
    ),
    "Cover letter": (
        "message to the hiring team",
        "let the company know about your interest",
    ),
    "Additional application context": (
        "anything else we should know",
        "additional information",
        "additional context",
    ),
}


def classify_question_intent(question: FormQuestion) -> QuestionIntent:
    """Classify a question before applying any candidate-profile heuristic."""
    if question.profile_record_kind:
        return QuestionIntent.RECORD_FIELD

    label = _normalize(_clean_required_marker(question.label))
    if any(pattern in label for pattern in _SENSITIVE_PATTERNS):
        return QuestionIntent.UNKNOWN

    sponsorship = "sponsor" in label or "sponsorship" in label
    if sponsorship:
        if re.search(r"\b(now|currently)\b.*\b(in the )?future\b", label):
            return QuestionIntent.SPONSORSHIP
        if re.search(r"\b(in the )?future\b", label):
            return QuestionIntent.FUTURE_SPONSORSHIP
        if re.search(r"\b(currently|current)\b", label):
            return QuestionIntent.CURRENT_SPONSORSHIP
        return QuestionIntent.SPONSORSHIP

    if (
        ("eligible" in label or "authorized" in label)
        and "work" in label
        and any(term in label for term in ("country", "united states", "u.s.", "us"))
    ):
        return QuestionIntent.AUTHORIZATION
    if re.search(r"\bat least\s+(?:18|eighteen)\b", label) or "18 years of age" in label:
        return QuestionIntent.AGE
    if "highest" in label and "completed education" in label:
        return QuestionIntent.COMPLETED_EDUCATION
    if any(
        phrase in label
        for phrase in (
            "desired income",
            "desired salary",
            "salary expectation",
            "compensation expectation",
            "desired compensation",
            "desired pay",
            "pay expectation",
        )
    ):
        return QuestionIntent.COMPENSATION
    if "employ" in label and any(
        term in label
        for term in ("subsidiary", "affiliate", "majority owned", "member of")
    ):
        return QuestionIntent.AFFILIATE_EMPLOYMENT
    if re.search(r"\bcurrently employed by\b", label):
        return QuestionIntent.COMPANY_EMPLOYMENT
    if "relocat" in label:
        return QuestionIntent.RELOCATION
    if re.search(r"\bwilling to travel\b|\btravel if required\b", label):
        return QuestionIntent.TRAVEL
    if any(
        pattern in label
        for pattern in (
            "non-compete",
            "non-solicit",
            "non-solicitation",
            "restrictive agreement",
            "bound by any agreement",
            "restrict your ability",
        )
    ):
        return QuestionIntent.RESTRICTIVE_AGREEMENT
    if question.input_type in {"textarea", "contenteditable"} and any(
        phrase in label
        for phrase in (
            "why ",
            "describe ",
            "tell us",
            "what makes",
            "cover letter",
            "additional information",
            "additional context",
            "interest in",
            "experience with",
            "provide an example",
            "please explain",
        )
    ):
        return QuestionIntent.NARRATIVE
    return QuestionIntent.UNKNOWN


def is_question_draft_eligible(question: FormQuestion) -> bool:
    """Return whether the field is a genuine narrative writing prompt."""
    return (
        not question.sensitive
        and question.input_type in {"textarea", "contenteditable"}
        and classify_question_intent(question) == QuestionIntent.NARRATIVE
    )


def resolve_form_questions(
    questions: list[FormQuestion],
    profile: CandidateProfile,
    *,
    employment_track: str,
    provider: str = "",
    company: str = "",
    application_id: str = "",
) -> list[FillAction]:
    """Build a reviewable fill plan without guessing unknown or sensitive facts."""
    education_records = profile.educations or [profile.education]
    work_records = profile.work_experiences
    education_counts: dict[str, int] = {}
    work_counts: dict[str, int] = {}
    actions: list[FillAction] = []
    for question in questions:
        label = _normalize(question.label)
        education_key = (
            _education_field_key(label)
            if question.profile_record_kind == "education"
            else None
        )
        work_key = (
            _work_field_key(label)
            if question.profile_record_kind == "work_experience"
            else None
        )
        education_counter_key = _record_counter_key(education_key, label)
        work_counter_key = _record_counter_key(work_key, label)
        explicit_education_index = (
            question.profile_record_index
            if question.profile_record_kind == "education"
            else None
        )
        explicit_work_index = (
            question.profile_record_index
            if question.profile_record_kind == "work_experience"
            else None
        )
        education_index = (
            explicit_education_index
            if explicit_education_index is not None
            else education_counts.get(education_counter_key, 0) if education_counter_key else 0
        )
        work_index = (
            explicit_work_index
            if explicit_work_index is not None
            else work_counts.get(work_counter_key, 0) if work_counter_key else 0
        )
        if education_counter_key and explicit_education_index is None:
            education_counts[education_counter_key] = education_index + 1
        if work_counter_key and explicit_work_index is None:
            work_counts[work_counter_key] = work_index + 1
        education = education_records[education_index] if education_index < len(education_records) else profile.education
        work = work_records[work_index] if work_index < len(work_records) else None
        actions.append(
            _resolve_question(
                question,
                profile,
                employment_track=employment_track,
                provider=provider,
                company=company,
                application_id=application_id,
                education=education,
                work=work,
            )
        )
    return actions


def _resolve_question(
    question: FormQuestion,
    profile: CandidateProfile,
    *,
    employment_track: str,
    provider: str,
    company: str,
    application_id: str,
    education: EducationProfile,
    work: WorkExperienceProfile | None,
) -> FillAction:
    label = _normalize(question.label)
    is_sensitive = question.sensitive or any(
        pattern in label for pattern in _SENSITIVE_PATTERNS
    )
    if is_sensitive:
        return _resolve_equal_opportunity(question, profile)

    first_name, last_name = _name_parts(profile)
    education_record = education
    work_record = work
    is_workday = provider == "workday"

    if question.input_type == "file" and any(term in label for term in ("resume", "cv")):
        if profile.resume_pdf_b64 or profile.resume_pdf_path or profile.resume_latex_source:
            return FillAction(
                field_id=question.field_id,
                action="upload",
                value=None,
                answer_source="resume",
            )
        return _unresolved(question)

    authorization = profile.work_authorization
    intent = classify_question_intent(question)
    if intent == QuestionIntent.AUTHORIZATION:
        if authorization.authorized_to_work_in_us is not None:
            return _boolean_action(question, authorization.authorized_to_work_in_us)
        return _unresolved(question)

    if intent == QuestionIntent.CURRENT_SPONSORSHIP:
        if authorization.current_requires_sponsorship is not None:
            return _boolean_action(question, authorization.current_requires_sponsorship)
        return _unresolved(question)

    if intent == QuestionIntent.FUTURE_SPONSORSHIP:
        if authorization.future_requires_sponsorship is not None:
            return _boolean_action(question, authorization.future_requires_sponsorship)
        return _unresolved(question)

    if intent == QuestionIntent.SPONSORSHIP:
        value = None
        if (
            authorization.current_requires_sponsorship is not None
            and authorization.future_requires_sponsorship is not None
        ):
            value = (
                authorization.current_requires_sponsorship
                or authorization.future_requires_sponsorship
            )
        if value is None:
            value = authorization.requires_sponsorship
        if value is None:
            if employment_track == "internship":
                value = authorization.internship_requires_sponsorship
            elif employment_track == "full_time":
                value = authorization.full_time_requires_sponsorship
        if value is not None:
            return _boolean_action(question, value)
        return _unresolved(question)

    if intent == QuestionIntent.AGE:
        value = profile.application_facts.is_at_least_18
        return _boolean_action(question, value) if value is not None else _unresolved(question)

    if intent == QuestionIntent.COMPLETED_EDUCATION:
        degree_level = _highest_completed_degree_level(profile)
        if not degree_level:
            return _unresolved(question)
        candidates = _workday_degree_candidates(degree_level)
        return FillAction(
            field_id=question.field_id,
            action="select" if question.input_type in {"select", "radio"} else "fill",
            value=_preferred_option_value(question, candidates, provider),
            answer_source="profile",
        )

    if intent == QuestionIntent.COMPENSATION:
        compensation = _compensation_value(profile, employment_track, application_id)
        if compensation:
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type in {"select", "radio"} else "fill",
                value=_match_option(compensation, question.options),
                answer_source="profile",
            )
        custom_compensation = _custom_answer_action(question, profile, label)
        return custom_compensation or _unresolved(question)

    if intent in {QuestionIntent.COMPANY_EMPLOYMENT, QuestionIntent.AFFILIATE_EMPLOYMENT}:
        relationship = _company_relationship(profile, company, label)
        if relationship is None:
            return _unresolved(question)
        value = (
            relationship.currently_employed
            if intent == QuestionIntent.COMPANY_EMPLOYMENT
            else relationship.employed_by_affiliate
        )
        return _boolean_action(question, value) if value is not None else _unresolved(question)

    if intent == QuestionIntent.RELOCATION:
        value = profile.application_facts.willing_to_relocate
        return _boolean_action(question, value) if value is not None else _unresolved(question)

    if intent == QuestionIntent.TRAVEL:
        value = profile.application_facts.willing_to_travel
        return _boolean_action(question, value) if value is not None else _unresolved(question)

    restricted_agreement_question = intent == QuestionIntent.RESTRICTIVE_AGREEMENT
    if restricted_agreement_question:
        explicit = profile.application_facts.active_non_compete_or_non_solicit
        if explicit is not None:
            return _boolean_action(question, explicit)
        restricted_custom = _custom_answer_action(question, profile, label)
        if restricted_custom is not None:
            return restricted_custom
        return _unresolved(question)

    skill_specific_answer = _skill_specific_custom_answer_action(question, profile, label)
    if skill_specific_answer is not None:
        return skill_specific_answer
    if _is_skill_specific_question(label):
        return _unresolved(question)

    if _is_plain_phone_question(question, label) and profile.phone:
        return FillAction(
            field_id=question.field_id,
            action="fill",
            value=profile.phone,
            answer_source="profile",
        )

    if _is_candidate_location_question(question, label) and profile.location:
        return FillAction(
            field_id=question.field_id,
            action="select" if question.input_type == "select" else "fill",
            value=_match_option(profile.location, question.options),
            answer_source="profile",
        )

    if _is_us_location_question(question, label):
        country = _normalize(profile.address.country)
        if country:
            return _boolean_action(
                question,
                country in {"united states", "united states of america", "usa", "us"},
            )

    custom_answer = _custom_answer_action(question, profile, label)
    if custom_answer is not None:
        return custom_answer

    if profile.skills and re.fullmatch(
        r"(?:(?:type to add|search|add|select|choose) )?(?:professional |relevant )?skills?\*?",
        label,
    ):
        skills = (
            [candidate for skill in profile.skills for candidate in _workday_skill_values(skill)]
            if is_workday
            else list(profile.skills)
        )
        return FillAction(
            field_id=question.field_id,
            action="select_many" if question.control_kind == "multi_select" or is_workday else "select" if question.input_type == "select" else "fill",
            value=skills if question.control_kind == "multi_select" or is_workday else "; ".join(skills),
            answer_source="profile",
        )

    if (
        is_workday
        and question.profile_record_kind == "work_experience"
        and work_record
        and "role description" in label
    ):
        description = _workday_role_description(work_record)
        if description:
            return FillAction(
                field_id=question.field_id,
                action="fill",
                value=description,
                answer_source="profile",
            )

    if is_workday and not question.required and any(
        term in label
        for term in (
            "certification",
            "language",
            "website",
        )
    ):
        return _unresolved(question)

    if (
        is_workday
        and question.profile_record_kind == "education"
        and _contains_any_label_word(label, ("school", "university", "institution"))
    ):
        if education_record.school:
            candidates = _workday_school_candidates(education_record.school)
            uses_catalog = question.control_kind in {"custom_select", "multi_select"} or question.input_type == "select"
            return FillAction(
                field_id=question.field_id,
                action="select" if uses_catalog else "fill",
                value=candidates if uses_catalog else candidates[0],
                answer_source="profile",
            )

    if (
        is_workday
        and question.profile_record_kind == "education"
        and _contains_label_phrase(label, "degree")
    ):
        degree_level = education_record.degree_level or degree_level_from_degree(education_record.degree)
        if degree_level:
            return FillAction(
                field_id=question.field_id,
                action="select",
                value=_workday_degree_candidates(degree_level),
                answer_source="profile",
            )

    if (
        is_workday
        and question.profile_record_kind == "education"
        and any(
            _contains_label_phrase(label, term)
            for term in ("field of study", "major")
        )
    ):
        candidates = education_record.field_of_study_candidates or field_of_study_candidates(
            education_record.school,
            education_record.degree,
            education_record.major,
        )
        if candidates:
            return FillAction(
                field_id=question.field_id,
                action="select",
                value=_workday_field_of_study_candidates(candidates),
                answer_source="profile",
            )

    if (
        is_workday
        and question.profile_record_kind == "education"
        and any(_contains_label_phrase(label, term) for term in ("overall result", "gpa"))
        and education_record.gpa
    ):
        return FillAction(
            field_id=question.field_id,
            action="fill",
            value=_workday_gpa_value(education_record.gpa),
            answer_source="profile",
        )

    if work_record and any(
        phrase in label
        for phrase in ("currently work here", "currently working", "current position")
    ):
        return _boolean_action(question, work_record.currently_working)
    if any(
        phrase in label
        for phrase in ("currently studying", "currently attend", "currently enrolled")
    ):
        return _boolean_action(question, education_record.currently_studying)

    record_date = (
        _record_date_for_question(
            question,
            label,
            education_record,
            work_record,
        )
        if question.profile_record_kind
        else ""
    )
    if record_date:
        value = _date_value_for_question(record_date, question, label)
        if value:
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type == "select" else "fill",
                value=_match_option(value, question.options),
                answer_source="profile",
            )

    previous_employer_question = any(
        pattern in label
        for pattern in (
            "previously employed",
            "previous employer",
            "former employee",
            "ever worked",
            "worked for this company",
            "worked at this company",
            "worked for us",
            "worked at our company",
        )
    )
    if previous_employer_question:
        return _unresolved(question)

    if any(
        pattern in label
        for pattern in (
            "country phone code",
            "phone country code",
            "dialing code",
            "dial code",
            "phone extension",
            "phone device type",
            "phone type",
        )
    ):
        return _unresolved(question)

    if label in {"name", "your name", "candidate name", "applicant name"} and profile.full_name:
        return FillAction(
            field_id=question.field_id,
            action="fill",
            value=profile.full_name,
            answer_source="profile",
        )

    direct_fields = (
        (("first name", "given name"), first_name),
        (("last name", "family name", "surname"), last_name),
        (("full name", "legal name"), profile.full_name),
        (("email", "email address"), profile.email),
        (("phone number", "telephone number", "mobile phone"), profile.phone),
        (("current location", "currently located"), profile.location),
        (("address line 1", "street address", "address 1"), profile.address.line1),
        (("address line 2", "address 2"), profile.address.line2),
        (("city",), profile.address.city),
        (("county",), profile.address.county),
        (("state", "province"), profile.address.state),
        (("zip", "postal code"), profile.address.postal_code),
        (("country",), profile.address.country),
        (("linkedin",), profile.linkedin_url),
        (("github",), profile.github_url),
        (("portfolio", "website"), profile.portfolio_url),
        (("pronoun", "pronouns"), profile.equal_opportunity.pronouns or profile.custom_answers.get("Pronouns", "")),
        (("school", "university", "college", "institution"), education_record.school),
        (("degree",), education_record.degree),
        (("major", "field of study"), education_record.major),
        (("graduation month",), education_record.graduation_month),
        (("graduation year",), education_record.graduation_year),
        (("gpa", "overall result"), education_record.gpa),
        (("education start date", "education from"), education_record.start_date),
        (("education end date", "education to"), education_record.end_date),
        (("job title", "current title", "position title", "experience title"), work_record.job_title if work_record else ""),
        (("company", "employer"), work_record.company if work_record else ""),
        (("job type", "employment type"), work_record.job_type if work_record else ""),
        (("work location", "job location", "office location", "experience location"), work_record.location if work_record else ""),
        (("experience start date", "experience from", "work start date"), work_record.start_date if work_record else ""),
        (("experience end date", "experience to", "work end date"), work_record.end_date if work_record else ""),
        (("experience description", "work description"), work_record.summary if work_record else ""),
    )
    for aliases, value in direct_fields:
        if restricted_agreement_question and any(alias in {"company", "employer"} for alias in aliases):
            continue
        if value and any(_contains_label_phrase(label, alias) for alias in aliases):
            selected_value = value
            if question.input_type == "select":
                if any(alias in {"state", "province"} for alias in aliases):
                    selected_value = _match_state_option(str(value), question.options)
                elif any(alias == "country" for alias in aliases):
                    selected_value = _match_country_option(str(value), question.options)
                else:
                    selected_value = _match_option(str(value), question.options)
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type == "select" else "fill",
                value=selected_value,
                answer_source="profile",
            )

    return _unresolved(question)


def _highest_completed_degree_level(profile: CandidateProfile) -> str:
    records = profile.educations or [profile.education]
    ranking = {
        "HS": 1,
        "AA": 2,
        "AS": 2,
        "BA": 3,
        "BS": 3,
        "BTECH": 3,
        "MA": 4,
        "MS": 4,
        "MBA": 4,
        "PHD": 5,
    }
    completed: list[tuple[int, str]] = []
    today = date.today()
    for record in records:
        level = (record.degree_level or degree_level_from_degree(record.degree)).upper()
        if not level:
            continue
        normalized_level = re.sub(r"[^A-Z0-9]+", "", level)
        end_date = _profile_date(record.end_date)
        if record.currently_studying or (end_date is not None and end_date > today):
            continue
        completed.append((ranking.get(normalized_level, 0), level))
    if not completed:
        return ""
    return max(completed, key=lambda item: item[0])[1]


def _profile_date(value: str) -> date | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}", cleaned):
            year, month = (int(part) for part in cleaned.split("-"))
            return date(year, month, 1)
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return None


def _preferred_option_value(
    question: FormQuestion,
    candidates: list[str],
    provider: str,
) -> str | list[str]:
    if question.options:
        for candidate in candidates:
            matched = _match_option(candidate, question.options)
            if matched in question.options:
                return matched
    if provider == "workday" and question.input_type == "select":
        return candidates
    return candidates[0]


def _compensation_value(
    profile: CandidateProfile,
    employment_track: str,
    application_id: str,
) -> str:
    preferences = [
        preference
        for preference in profile.application_facts.compensation_preferences
        if preference.amount
    ]
    application_exact = next(
        (
            preference
            for preference in preferences
            if application_id
            and preference.application_id == application_id
            and preference.employment_type == employment_track
        ),
        None,
    )
    application_fallback = next(
        (
            preference
            for preference in preferences
            if application_id
            and preference.application_id == application_id
            and preference.employment_type == "any"
        ),
        None,
    )
    exact = next(
        (
            preference
            for preference in preferences
            if preference.application_id is None
            and preference.employment_type == employment_track
        ),
        None,
    )
    fallback = next(
        (
            preference
            for preference in preferences
            if preference.application_id is None
            and preference.employment_type == "any"
        ),
        None,
    )
    selected = application_exact or application_fallback or exact or fallback
    return selected.amount if selected else ""


def _company_relationship(
    profile: CandidateProfile,
    company: str,
    label: str,
) -> CompanyRelationshipProfile | None:
    company_key = _company_key(company)
    label_key = _company_key(label)
    matches: list[tuple[int, CompanyRelationshipProfile]] = []
    for saved_company, relationship in profile.application_facts.company_relationships.items():
        saved_key = _company_key(saved_company)
        if not saved_key:
            continue
        if saved_key == company_key:
            matches.append((3, relationship))
        elif saved_key in company_key or company_key in saved_key:
            matches.append((2, relationship))
        elif saved_key in label_key:
            matches.append((1, relationship))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _company_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _workday_degree_candidates(degree_level: str) -> list[str]:
    """Return exact Workday labels in preferred order for a canonical degree."""
    aliases = {
        "MS": ["MS", "Master of Science", "Master's Degree"],
        "MA": ["MA", "Master of Arts", "Master's Degree"],
        "MBA": ["MBA", "Master of Business Administration", "Master's Degree"],
        "BS": ["BS", "Bachelor of Science", "Bachelor's Degree"],
        "BA": ["BA", "Bachelor of Arts", "Bachelor's Degree"],
        "AS": ["AS", "Associate of Science", "Associate's Degree"],
        "AA": ["AA", "Associate of Arts", "Associate's Degree"],
        "PhD": ["PhD", "Doctor of Philosophy", "Doctorate"],
    }
    return aliases.get(degree_level, [degree_level])


def _workday_school_candidates(school: str) -> list[str]:
    """Return stored and catalog-friendly institution names in preferred order."""
    normalized = _normalize(school)
    aliases = {
        "amrita school of engineering": [
            "Amrita School of Engineering",
            "Amrita Vishwa Vidyapeetham",
        ],
    }
    return aliases.get(normalized, [school])


def _workday_field_of_study_candidates(candidates: list[str]) -> list[str]:
    """Expand resume majors to exact labels commonly exposed by Workday catalogs."""
    aliases = {
        "data science": ["Computer and Information Science", "Data Science", "Data Processing"],
        "engineering data science artificial intelligence": ["Computer and Information Science", "Data Science", "Data Processing"],
        "computer science": ["Computer Science", "Computer and Information Science"],
        "computer science and engineering": ["Computer Science", "Computer and Information Science"],
    }
    expanded: list[str] = []
    for candidate in candidates:
        expanded.extend(aliases.get(_normalize(candidate), [candidate]))
    expanded.extend(candidates)
    return list(dict.fromkeys(value for value in expanded if value.strip()))


def _workday_gpa_value(value: str) -> str:
    """Preserve the GPA scale while removing display-only trailing zeroes."""
    parts = [part.strip() for part in re.split(r"\s*/\s*", value) if part.strip()]
    normalized = [re.sub(r"\.0+$", "", part) for part in parts]
    return "/".join(normalized)


def _workday_role_description(work: WorkExperienceProfile) -> str:
    """Format a concise, resume-grounded Workday role description."""
    bullets = [re.sub(r"\s+", " ", bullet).strip(" -•") for bullet in work.bullets]
    selected = [bullet for bullet in bullets if bullet][:3]
    if selected:
        return "\n".join(f"- {bullet}" for bullet in selected)
    return re.sub(r"\s+", " ", work.summary).strip()


def _education_field_key(label: str) -> str | None:
    fields = {
        "school": ("school", "university", "college", "institution"),
        "degree": ("degree",),
        "major": ("major", "field of study"),
        "gpa": ("gpa", "overall result"),
        "start": ("education start date", "education from"),
        "end": ("graduation", "education end date", "education to"),
    }
    return next((key for key, aliases in fields.items() if any(alias in label for alias in aliases)), None)


def _record_counter_key(field_key: str | None, label: str) -> str | None:
    if field_key not in {"start", "end"}:
        return field_key
    component = next(
        (part for part in ("month", "year", "day") if part in label),
        "date",
    )
    return f"{field_key}:{component}"


def _record_date_for_label(
    label: str,
    education: EducationProfile,
    work: WorkExperienceProfile | None,
) -> str:
    if "education" in label:
        if _contains_any_label_word(label, ("from", "start")):
            return education.start_date
        if _contains_any_label_word(label, ("to", "end", "graduation")):
            return education.end_date
    if work and any(term in label for term in ("experience", "work")):
        if _contains_any_label_word(label, ("from", "start")):
            return work.start_date
        if _contains_any_label_word(label, ("to", "end")):
            return work.end_date
    return ""


def _record_date_for_question(
    question: FormQuestion,
    label: str,
    education: EducationProfile,
    work: WorkExperienceProfile | None,
) -> str:
    """Resolve structured date controls before falling back to label heuristics."""
    if question.date_boundary:
        record = work if question.profile_record_kind == "work_experience" else education
        if record is not None:
            return record.start_date if question.date_boundary == "start" else record.end_date
    return _record_date_for_label(label, education, work)


def _date_component_value(value: str, label: str) -> str:
    if "year" in label:
        match = re.search(r"\b(?:19|20)\d{2}\b", value)
        return match.group(0) if match else value
    if "month" in label:
        numeric = re.search(r"(?:^|[-/\s])(?:19|20)\d{2}[-/](\d{1,2})(?:$|[-/\s])", value)
        if numeric:
            month = int(numeric.group(1))
            if 1 <= month <= 12:
                return month_name[month]
        textual = next(
            (name for name in month_name[1:] if re.search(rf"\b{name}\b", value, flags=re.I)),
            "",
        )
        return textual or value
    return value


def _workday_skill_values(value: str) -> list[str]:
    """Return catalog-friendly Workday skill labels for one saved skill."""
    cleaned = re.sub(r"\bA\s+WS\b", "AWS", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bG\s+CP\b", "GCP", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    aliases = {
        "crewai": ["Crew AI"],
        "git github": ["Git"],
        "llm evaluation ragas deepeval": ["RAGAS", "Deep-Eval"],
        "llm fine tuning lora qlora": ["Fine-tuning", "LoRA/QLoRA"],
    }
    catalog_key = re.sub(r"[^a-z0-9]+", " ", cleaned.casefold()).strip()
    return aliases.get(catalog_key, [cleaned])


def _date_value_for_question(
    value: str,
    question: FormQuestion,
    label: str,
) -> str:
    """Format a normalized profile date for the concrete browser control."""
    if question.date_component == "month":
        numeric = re.fullmatch(r"((?:19|20)\d{2})-(\d{2})", value.strip())
        return numeric.group(2) if numeric else value
    if question.date_component == "year":
        year = re.search(r"\b(?:19|20)\d{2}\b", value)
        return year.group(0) if year else value
    if question.control_kind == "month_year":
        numeric = re.fullmatch(r"((?:19|20)\d{2})-(\d{2})", value.strip())
        if numeric:
            return f"{numeric.group(2)}/{numeric.group(1)}"
    if question.control_kind == "year":
        year = re.search(r"\b(?:19|20)\d{2}\b", value)
        return year.group(0) if year else value
    return _date_component_value(value, label)


def _work_field_key(label: str) -> str | None:
    fields = {
        "title": ("job title", "position title", "experience title"),
        "company": ("company", "employer"),
        "location": ("work location", "job location", "office location", "experience location"),
        "start": ("experience start date", "experience from", "work start date"),
        "end": ("experience end date", "experience to", "work end date"),
        "description": ("experience description", "work description", "role description"),
    }
    return next((key for key, aliases in fields.items() if any(alias in label for alias in aliases)), None)


def _custom_answer_action(
    question: FormQuestion,
    profile: CandidateProfile,
    label: str,
) -> FillAction | None:
    for prompt, answer in profile.custom_answers.items():
        if not answer.strip():
            continue
        if _is_phone_device_type_prompt(prompt) and not _is_phone_device_type_question(question, label):
            continue
        aliases = _custom_answer_aliases(prompt)
        if any(_custom_prompt_matches_label(alias, label) for alias in aliases):
            if question.control_kind == "multi_select":
                values = [part.strip() for part in re.split(r"[,;\n]+", answer) if part.strip()]
                return FillAction(
                    field_id=question.field_id,
                    action="select_many",
                    value=[_match_option(value, question.options) for value in values],
                    answer_source="custom_answer",
                )
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type in {"select", "radio"} else "fill",
                value=_match_option(answer, question.options),
                answer_source="custom_answer",
            )
    return None


def _clean_required_marker(label: str) -> str:
    return re.sub(r"\s*\*+\s*$", "", label).strip()


def _is_plain_phone_question(question: FormQuestion, label: str) -> bool:
    if question.input_type in {"checkbox", "file", "radio", "select"}:
        return False
    if _is_phone_device_type_question(question, label):
        return False
    if any(
        phrase in label
        for phrase in (
            "country phone code",
            "phone country code",
            "dialing code",
            "dial code",
            "phone extension",
        )
    ):
        return False
    base = _clean_required_marker(label)
    return base in {
        "phone",
        "phone number",
        "mobile phone",
        "mobile number",
        "telephone",
        "telephone number",
    }


def _is_candidate_location_question(question: FormQuestion, label: str) -> bool:
    if question.input_type in {"checkbox", "file", "radio"}:
        return False
    if question.profile_record_kind:
        return False
    if any(
        phrase in label
        for phrase in (
            "job location",
            "office location",
            "work location",
            "experience location",
            "preferred location",
            "desired location",
            "relocation",
        )
    ):
        return False
    base = _clean_required_marker(label)
    return base in {
        "location",
        "current location",
        "your location",
        "where are you currently located",
        "where are you located",
    } or "currently located" in label


def _is_us_location_question(question: FormQuestion, label: str) -> bool:
    if question.input_type not in {"checkbox", "radio", "select"}:
        return False
    return "located" in label and any(
        country in label
        for country in ("united states", "u.s.", "usa")
    )


def _is_phone_device_type_prompt(prompt: str) -> bool:
    return _normalize(prompt) in {
        "phone device type",
        "phone type",
        "telephone device type",
    }


def _is_phone_device_type_question(question: FormQuestion, label: str) -> bool:
    if question.input_type not in {"select", "radio"}:
        return False
    return any(
        phrase in label
        for phrase in (
            "phone device type",
            "telephone device type",
            "phone type",
        )
    )


def _skill_specific_custom_answer_action(
    question: FormQuestion,
    profile: CandidateProfile,
    label: str,
) -> FillAction | None:
    years_match = _skill_years_match(label)
    if years_match:
        years, skill = years_match
        candidates = (
            label,
            f"{skill} years",
            f"{skill} experience years",
            f"{skill} years experience",
            f"{skill} {years} years",
            f"{years} years {skill}",
            f"at least {years} years of {skill}",
        )
        return _custom_answer_for_candidates(question, profile, candidates)

    summary_skill = _skill_summary_match(label)
    if summary_skill:
        candidates = (
            label,
            f"{summary_skill} experience summary",
            f"{summary_skill} summary",
            f"{summary_skill} experience",
            f"experience with {summary_skill}",
            f"summarize {summary_skill}",
            f"describe {summary_skill}",
        )
        return _custom_answer_for_candidates(question, profile, candidates)
    return None


def _custom_answer_for_candidates(
    question: FormQuestion,
    profile: CandidateProfile,
    candidates: tuple[str, ...],
) -> FillAction | None:
    normalized_candidates = {_normalize(candidate) for candidate in candidates}
    for prompt, answer in profile.custom_answers.items():
        if not answer.strip():
            continue
        normalized_prompt = _normalize(prompt)
        if normalized_prompt in normalized_candidates:
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type in {"select", "radio"} else "fill",
                value=_match_option(answer, question.options),
                answer_source="custom_answer",
            )
    return None


def _is_skill_specific_question(label: str) -> bool:
    return _skill_years_match(label) is not None or _skill_summary_match(label) is not None


def _skill_years_match(label: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?:do you have )?(?:at least )?(\d+)\+? years? of (.+?) experience",
        label,
    )
    if not match:
        return None
    skill = _clean_skill_fragment(match.group(2))
    return (match.group(1), skill) if skill else None


def _skill_summary_match(label: str) -> str | None:
    match = re.search(
        r"(?:please )?(?:summarize|describe) your experience with (.+)",
        label,
    )
    if not match:
        return None
    skill = _clean_skill_fragment(match.group(1))
    return skill or None


def _clean_skill_fragment(value: str) -> str:
    return re.sub(r"[\s.?!:;]+$", "", value).strip()


def _custom_answer_aliases(prompt: str) -> tuple[str, ...]:
    normalized_prompt = _normalize(prompt)
    aliases = [normalized_prompt]
    aliases.extend(
        _normalize(alias)
        for canonical, values in _CUSTOM_ANSWER_ALIASES.items()
        if _normalize(canonical) == normalized_prompt
        for alias in values
    )
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _custom_prompt_matches_label(prompt: str, label: str) -> bool:
    if prompt == label or prompt in label or label in prompt:
        return True
    prompt_tokens = _meaningful_tokens(prompt)
    label_tokens = set(_meaningful_tokens(label))
    return bool(prompt_tokens) and set(prompt_tokens).issubset(label_tokens)


def _meaningful_tokens(value: str) -> list[str]:
    stop_words = {
        "are",
        "can",
        "did",
        "for",
        "have",
        "how",
        "the",
        "this",
        "what",
        "when",
        "with",
        "you",
        "your",
    }
    return [
        token
        for token in _normalize(value).split()
        if len(token) > 2 and token not in stop_words
    ]


def _resolve_equal_opportunity(
    question: FormQuestion,
    profile: CandidateProfile,
) -> FillAction:
    eeo = profile.equal_opportunity
    label = _normalize(question.label)
    value: str | None = None
    if "disability" in label:
        value = eeo.disability
    elif "hispanic" in label or "latino" in label:
        value = eeo.hispanic_or_latino
    elif "gender" in label:
        value = eeo.gender
    elif "veteran" in label:
        value = eeo.veteran_status
    elif "sexual orientation" in label:
        value = _sexual_orientation_answer(eeo.sexual_orientation, question.options)
    elif "lgbtq" in label or "lgbt" in label:
        value = eeo.lgbtq
    elif "race" in label or "ethnicity" in label:
        hispanic_option = next(
            (
                option
                for option in question.options
                if _normalize(option).startswith("hispanic or latino")
            ),
            None,
        )
        value = (
            hispanic_option
            if hispanic_option and _normalize(eeo.hispanic_or_latino or "") == "yes"
            else eeo.race
        )
    if not value:
        return _unresolved(question)
    if not eeo.allow_autofill:
        return FillAction(
            field_id=question.field_id,
            action="skip",
            value=None,
            answer_source="eeo_opt_in",
        )
    return FillAction(
        field_id=question.field_id,
        action="select" if question.input_type in {"select", "radio"} else "fill",
        value=_match_option(value, question.options),
        answer_source="profile",
    )


def _sexual_orientation_answer(
    orientations: list[str],
    options: list[str],
) -> str | None:
    if not orientations:
        return None
    if not options:
        return ", ".join(orientations)
    for orientation in orientations:
        matched = _match_option(orientation, options)
        if matched in options:
            return matched
    return ", ".join(orientations)


def _boolean_action(question: FormQuestion, value: bool) -> FillAction:
    if question.input_type == "checkbox":
        action = "check"
        answer: str | bool = value
    else:
        action = "select" if question.input_type in {"radio", "select"} else "fill"
        answer = _match_option("Yes" if value else "No", question.options)
    return FillAction(
        field_id=question.field_id,
        action=action,
        value=answer,
        answer_source="profile",
    )


def _unresolved(question: FormQuestion) -> FillAction:
    return FillAction(
        field_id=question.field_id,
        action="skip",
        value=None,
        answer_source="user_input" if question.required else "none",
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_label_phrase(label: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
    return re.search(pattern, label) is not None


def _contains_any_label_word(label: str, words: tuple[str, ...]) -> bool:
    return any(_contains_label_phrase(label, word) for word in words)


def _match_option(value: str, options: list[str]) -> str:
    """Prefer an available option while keeping deterministic behavior."""
    if not options:
        return value
    wanted = _normalize(value)
    for option in options:
        if _normalize(option) == wanted:
            return option
    for option in options:
        normalized = _normalize(option)
        if wanted in normalized or normalized in wanted:
            return option
    if wanted == "no":
        for option in options:
            normalized = _normalize(option)
            if (
                normalized.startswith("no")
                or "do not" in normalized
                or "don't" in normalized
                or "am not" in normalized
            ):
                return option
    if wanted == "yes":
        for option in options:
            if _normalize(option).startswith("yes"):
                return option
    if wanted in {"decline to state", "prefer not to say", "prefer not to answer"}:
        for option in options:
            normalized = _normalize(option)
            if any(
                phrase in normalized
                for phrase in (
                    "decline",
                    "prefer not",
                    "do not wish",
                    "don't wish",
                    "choose not",
                    "not wish to",
                )
            ):
                return option
    if wanted == "non-binary":
        for option in options:
            normalized = _normalize(option)
            if normalized in {"non-binary", "nonbinary", "non binary"}:
                return option
    return value


def _match_state_option(value: str, options: list[str]) -> str:
    """Return the portal's state representation for a name or postal code."""
    if not options:
        return value
    wanted_code = _us_state_code(value)
    if wanted_code:
        for option in options:
            if _us_state_code(option) == wanted_code:
                return option
    return _match_option(value, options)


def _match_country_option(value: str, options: list[str]) -> str:
    """Return the portal's country label for common United States aliases."""
    if not options:
        return value
    for option in options:
        if _countries_equivalent(value, option):
            return option
    return _match_option(value, options)


def _normalize_geo_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _countries_equivalent(left: str, right: str) -> bool:
    left_norm = _normalize_geo_text(left)
    right_norm = _normalize_geo_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    return left_norm in _US_COUNTRY_ALIASES and right_norm in _US_COUNTRY_ALIASES


def _states_equivalent(left: str, right: str) -> bool:
    left_code = _us_state_code(left)
    right_code = _us_state_code(right)
    return bool(left_code and right_code and left_code == right_code)


def _geo_values_equivalent(left: str, right: str) -> bool:
    if _countries_equivalent(left, right) or _states_equivalent(left, right):
        return True
    left_parts = [
        _normalize_geo_text(part)
        for part in left.split(",")
        if _normalize_geo_text(part)
    ]
    right_parts = [
        _normalize_geo_text(part)
        for part in right.split(",")
        if _normalize_geo_text(part)
    ]
    if len(left_parts) == 1 and left_parts[0] in right_parts:
        return True
    return len(right_parts) == 1 and right_parts[0] in left_parts


def _us_state_code(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    upper = re.sub(r"[.]", "", raw).upper().strip()
    if upper in _US_STATE_CODES:
        return upper
    decorated = re.search(r"(?:^|[-–(]\s*)([A-Z]{2})(?:\s*[-–)]|$)", upper)
    if decorated and decorated.group(1) in _US_STATE_CODES:
        return decorated.group(1)
    normalized = re.sub(r"[^a-z ]+", "", raw.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) == 2 and normalized.upper() in _US_STATE_CODES:
        return normalized.upper()
    code = _US_STATE_CODE_BY_NAME.get(normalized)
    if code:
        return code
    # "texas tx" / "tx texas" after punctuation strip
    tokens = normalized.split()
    for token in tokens:
        if len(token) == 2 and token.upper() in _US_STATE_CODES:
            return token.upper()
        mapped = _US_STATE_CODE_BY_NAME.get(token)
        if mapped:
            return mapped
    return None


def _name_parts(profile: CandidateProfile) -> tuple[str, str]:
    first_name = profile.first_name.strip()
    last_name = profile.last_name.strip()
    if first_name and last_name:
        return first_name, last_name
    parts = profile.full_name.split()
    if not first_name and parts:
        first_name = parts[0]
    if not last_name and len(parts) > 1:
        last_name = parts[-1]
    return first_name, last_name


def profile_setup_status(profile: CandidateProfile) -> list[dict[str, object]]:
    """Return common application questions and whether the profile can answer them."""
    return [
        {
            **question,
            "value_present": _profile_value_present(profile, str(question["key"])),
        }
        for question in COMMON_PROFILE_SETUP_QUESTIONS
    ]


def _profile_value_present(profile: CandidateProfile, key: str) -> bool:
    value: object = profile
    for part in key.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""
