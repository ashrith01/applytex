"""Deterministic candidate-profile answers for reviewed form filling."""

from __future__ import annotations

import re

from latex_resume.job_models import CandidateProfile, FillAction, FormQuestion

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
    {"key": "address.city", "label": "City", "category": "contact", "required": True},
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
        "key": "custom_answers.Open to relocate",
        "label": "Open to relocate",
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


def resolve_form_questions(
    questions: list[FormQuestion],
    profile: CandidateProfile,
    *,
    employment_track: str,
) -> list[FillAction]:
    """Build a reviewable fill plan without guessing unknown or sensitive facts."""
    return [
        _resolve_question(question, profile, employment_track=employment_track)
        for question in questions
    ]


def _resolve_question(
    question: FormQuestion,
    profile: CandidateProfile,
    *,
    employment_track: str,
) -> FillAction:
    label = _normalize(question.label)
    is_sensitive = question.sensitive or any(
        pattern in label for pattern in _SENSITIVE_PATTERNS
    )
    if is_sensitive:
        return _resolve_equal_opportunity(question, profile)

    first_name, last_name = _name_parts(profile)
    primary_work = profile.work_experiences[0] if profile.work_experiences else None

    if question.input_type == "file" and any(term in label for term in ("resume", "cv")):
        if profile.resume_pdf_b64 or profile.resume_latex_source:
            return FillAction(
                field_id=question.field_id,
                action="upload",
                value=None,
                answer_source="resume",
            )
        return _unresolved(question)

    authorization = profile.work_authorization
    if "authorized" in label and ("work" in label or "country" in label or "united states" in label or "u.s." in label):
        if authorization.authorized_to_work_in_us is not None:
            return _boolean_action(question, authorization.authorized_to_work_in_us)

    if "sponsor" in label or "sponsorship" in label:
        value = authorization.requires_sponsorship
        if value is None:
            if employment_track == "internship":
                value = authorization.internship_requires_sponsorship
            elif employment_track == "full_time":
                value = authorization.full_time_requires_sponsorship
        if value is not None:
            return _boolean_action(question, value)

    restricted_agreement_question = any(
        pattern in label
        for pattern in (
            "bound by any agreement",
            "non-compete",
            "non-solicitation",
            "confidentiality",
            "non-disclosure",
            "contractual obligation",
            "restrict your ability",
        )
    )
    if restricted_agreement_question:
        return _boolean_action(question, False)

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
        (("phone", "mobile"), profile.phone),
        (("current location",), profile.location),
        (("city",), profile.address.city),
        (("state", "province"), profile.address.state),
        (("zip", "postal code"), profile.address.postal_code),
        (("country",), profile.address.country),
        (("linkedin",), profile.linkedin_url),
        (("github",), profile.github_url),
        (("portfolio", "website"), profile.portfolio_url),
        (("pronoun",), profile.equal_opportunity.pronouns or profile.custom_answers.get("Pronouns", "")),
        (("school", "university", "college"), profile.education.school),
        (("degree",), profile.education.degree),
        (("major", "field of study"), profile.education.major),
        (("graduation month",), profile.education.graduation_month),
        (("graduation year",), profile.education.graduation_year),
        (("gpa",), profile.education.gpa),
        (("job title", "current title", "position title"), primary_work.job_title if primary_work else ""),
        (("company", "employer"), primary_work.company if primary_work else ""),
        (("job type", "employment type"), primary_work.job_type if primary_work else ""),
        (("work location", "job location"), primary_work.location if primary_work else ""),
    )
    for aliases, value in direct_fields:
        if restricted_agreement_question and any(alias in {"company", "employer"} for alias in aliases):
            continue
        if value and any(alias in label for alias in aliases):
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type == "select" else "fill",
                value=value,
                answer_source="profile",
            )

    if "relocat" in label:
        return _boolean_action(
            question,
            profile.search_preferences.willing_to_relocate,
        )

    for prompt, answer in profile.custom_answers.items():
        normalized_prompt = _normalize(prompt)
        if (
            answer.strip()
            and (
                normalized_prompt == label
                or normalized_prompt in label
                or label in normalized_prompt
            )
        ):
            return FillAction(
                field_id=question.field_id,
                action="select" if question.input_type == "select" else "fill",
                value=answer,
                answer_source="custom_answer",
            )
    return _unresolved(question)


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
        value = eeo.race
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
            if normalized.startswith("no") or "do not" in normalized:
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
