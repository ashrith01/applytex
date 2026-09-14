"""Review-gated suggestions for short application questions.

The deterministic resolver in ``form_resolution`` answers a question only when
an explicit profile fact or remembered answer matches it. This module covers
the remaining *short* required questions (yes/no, single select, one-line text)
by asking a language model to map the question onto facts the user already
saved — and nothing else. Every proposal is validated against the offered
options and must cite a real fact; the browser never fills a proposal until the
user confirms it, at which point ``remember_answer`` persists the mapping.

Authorization, sponsorship, compensation, demographic, per-record, narrative,
and file questions are never sent here (see ``is_question_proposal_eligible``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from latex_resume.form_resolution import (
    _INTENT_PROFILE_FACTS,
    boolean_from_answer_value,
    classify_question_intent,
    is_question_proposal_eligible,
)
from latex_resume.job_models import (
    AnswerProposal,
    CandidateProfile,
    FormQuestion,
    QuestionIntent,
    SavedAnswer,
)
from latex_resume.llm import complete_json
from latex_resume.option_matching import match_available_option

logger = logging.getLogger(__name__)

MAX_PROPOSAL_QUESTIONS = 20
MAX_PROPOSAL_VALUE_CHARS = 160

_SYSTEM_PROMPT = (
    "You map job-application questions onto facts a candidate already saved. "
    "You never invent, infer, or estimate a fact that is not listed. "
    "Output ONLY a valid JSON object — no prose, no markdown, no code fences."
)


def profile_fact_digest(
    profile: CandidateProfile,
    saved_answers: list[SavedAnswer],
) -> dict[str, str]:
    """Flat ``key -> value`` view of non-empty facts a proposal may cite."""
    facts: dict[str, str] = {}

    def put(key: str, value: object) -> None:
        if value is None or value == "" or value == []:
            return
        if isinstance(value, bool):
            facts[key] = "Yes" if value else "No"
        elif isinstance(value, list):
            facts[key] = ", ".join(str(item) for item in value if str(item).strip())
        else:
            facts[key] = str(value).strip()

    put("full_name", profile.full_name)
    put("email", profile.email)
    put("phone", profile.phone)
    put("location", profile.location)
    for field in ("line1", "city", "county", "state", "postal_code", "country"):
        put(f"address.{field}", getattr(profile.address, field))
    put("linkedin_url", profile.linkedin_url)
    put("github_url", profile.github_url)
    put("portfolio_url", profile.portfolio_url)
    put("skills", profile.skills)

    facts_profile = profile.application_facts
    put("application_facts.is_at_least_18", facts_profile.is_at_least_18)
    put("application_facts.willing_to_relocate", facts_profile.willing_to_relocate)
    put("application_facts.willing_to_travel", facts_profile.willing_to_travel)
    put(
        "application_facts.active_non_compete_or_non_solicit",
        facts_profile.active_non_compete_or_non_solicit,
    )

    educations = profile.educations or [profile.education]
    for index, education in enumerate(educations):
        prefix = f"education[{index}]"
        put(f"{prefix}.school", education.school)
        put(f"{prefix}.degree", education.degree)
        put(f"{prefix}.degree_level", education.degree_level)
        put(f"{prefix}.major", education.major)
        put(f"{prefix}.graduation_year", education.graduation_year)
        put(f"{prefix}.currently_studying", education.currently_studying)
    for index, work in enumerate(profile.work_experiences):
        prefix = f"work_experience[{index}]"
        put(f"{prefix}.job_title", work.job_title)
        put(f"{prefix}.company", work.company)
        put(f"{prefix}.start_date", work.start_date)
        put(f"{prefix}.end_date", work.end_date or ("Present" if work.currently_working else ""))
    for prompt, answer in profile.custom_answers.items():
        put(f"custom_answers.{prompt}", answer)
    for answer in saved_answers:
        put(f"saved_answers.{answer.prompt_text}", answer.value)
    return facts


def _proposal_prompt(
    questions: list[FormQuestion],
    facts: dict[str, str],
    *,
    provider: str,
    company: str,
    job_title: str,
) -> str:
    payload = {
        "application": {"provider": provider, "company": company, "job_title": job_title},
        "facts": facts,
        "questions": [
            {
                "field_id": question.field_id,
                "label": question.label,
                "input_type": question.input_type,
                "required": question.required,
                "options": question.options[:60],
            }
            for question in questions
        ],
    }
    return (
        "For each question, decide whether ONE listed fact answers it directly.\n"
        "Rules:\n"
        "- Use only the facts object. If no fact answers the question, set value to null.\n"
        "- Never guess age, demographics, work authorization, visa, salary, or anything not listed.\n"
        "- When options are given, value must be exactly one option string, copied verbatim.\n"
        "- Keep text values short (one line). Do not write essays.\n"
        "- evidence_key must be the exact key of the fact you used.\n"
        "- confidence is high only when the fact answers the question unambiguously.\n"
        "Return JSON: {\"answers\": [{\"field_id\": str, \"value\": str|null, "
        "\"evidence_key\": str|null, \"confidence\": \"high\"|\"medium\"|\"low\", "
        "\"reason\": str}]}\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _remember_target(intent: QuestionIntent, value: str | bool | list[str] | None) -> str:
    if value is None:
        return "none"
    if intent in _INTENT_PROFILE_FACTS and boolean_from_answer_value(value) is not None:
        return "profile_fact"
    return "saved_answer"


def _validate_proposal(
    question: FormQuestion,
    raw: dict[str, Any],
    facts: dict[str, str],
) -> AnswerProposal:
    intent = classify_question_intent(question)
    confidence = raw.get("confidence") if raw.get("confidence") in {"high", "medium", "low"} else "low"
    reason = str(raw.get("reason") or "").strip()[:300]
    evidence_key = str(raw.get("evidence_key") or "").strip()
    value = raw.get("value")

    def rejected(why: str) -> AnswerProposal:
        return AnswerProposal(
            field_id=question.field_id,
            label=question.label,
            value=None,
            intent=intent,
            confidence="low",
            evidence="",
            reason=why,
            remember_target="none",
        )

    if value is None or value == "":
        return rejected(reason or "No saved fact answers this question.")
    if evidence_key not in facts:
        return rejected("The suggestion did not cite a saved fact, so it was discarded.")
    if isinstance(value, list):
        cleaned: str | bool | list[str] = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            return rejected("The suggestion was empty.")
    elif isinstance(value, bool):
        cleaned = value
    else:
        cleaned = str(value).strip()
        if len(cleaned) > MAX_PROPOSAL_VALUE_CHARS:
            return rejected("The suggestion was too long for a short answer.")

    if question.options:
        if isinstance(cleaned, list):
            matches = [match_available_option(item, question.options) for item in cleaned]
            if any(match.value is None for match in matches):
                return rejected("The suggestion is not one of the offered options.")
            cleaned = [match.value for match in matches if match.value is not None]
        else:
            match = match_available_option(
                "Yes" if cleaned is True else "No" if cleaned is False else str(cleaned),
                question.options,
            )
            if match.value is None:
                return rejected("The suggestion is not one of the offered options.")
            cleaned = match.value
    elif question.input_type == "checkbox":
        boolean = boolean_from_answer_value(cleaned)
        if boolean is None:
            return rejected("A checkbox needs a yes/no answer.")
        cleaned = boolean

    return AnswerProposal(
        field_id=question.field_id,
        label=question.label,
        value=cleaned,
        intent=intent,
        confidence=confidence,  # type: ignore[arg-type]
        evidence=f"{evidence_key} = {facts[evidence_key]}",
        reason=reason,
        remember_target=_remember_target(intent, cleaned),  # type: ignore[arg-type]
    )


async def propose_short_answers(
    questions: list[FormQuestion],
    profile: CandidateProfile,
    saved_answers: list[SavedAnswer],
    *,
    provider: str = "",
    company: str = "",
    job_title: str = "",
) -> list[AnswerProposal]:
    """Return one validated proposal per eligible question (value may be None)."""
    eligible = [question for question in questions if is_question_proposal_eligible(question)]
    if not eligible:
        return []
    eligible = eligible[:MAX_PROPOSAL_QUESTIONS]
    facts = profile_fact_digest(profile, saved_answers)
    if not facts:
        return [
            AnswerProposal(
                field_id=question.field_id,
                label=question.label,
                intent=classify_question_intent(question),
                reason="No profile facts are saved yet.",
            )
            for question in eligible
        ]

    prompt = _proposal_prompt(
        eligible,
        facts,
        provider=provider,
        company=company,
        job_title=job_title,
    )
    result = await complete_json(prompt, system=_SYSTEM_PROMPT, task="application")
    if not isinstance(result, dict):
        raise ValueError("Answer proposal provider returned a non-object JSON value.")
    raw_answers = result.get("answers")
    by_field: dict[str, dict[str, Any]] = {}
    if isinstance(raw_answers, list):
        for item in raw_answers:
            if isinstance(item, dict) and isinstance(item.get("field_id"), str):
                by_field[item["field_id"]] = item
    proposals = [
        _validate_proposal(question, by_field.get(question.field_id, {}), facts)
        for question in eligible
    ]
    logger.info(
        "answer proposals: %d eligible, %d with values",
        len(proposals),
        sum(1 for proposal in proposals if proposal.value is not None),
    )
    return proposals
