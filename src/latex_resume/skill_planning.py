"""Skill target planning for resume optimization."""

from __future__ import annotations

import json
from typing import Any

from latex_resume.llm import _sanitize_user_input, complete_json
from latex_resume.prompts import SKILL_TARGET_PLAN_PROMPT

MAX_TARGET_SKILLS = 12


async def generate_skill_target_plan(
    resume_plain_text: str,
    existing_skills: str,
    job_keywords: dict[str, Any],
    job_description: str,
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM to build a concise list of target skills for tailoring."""
    safe_jd = _sanitize_user_input(job_description)
    safe_resume = _sanitize_user_input(resume_plain_text)
    safe_skills = _sanitize_user_input(existing_skills)

    prompt = SKILL_TARGET_PLAN_PROMPT.format(
        existing_skills=safe_skills,
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        job_description=safe_jd,
        resume_plain_text=safe_resume,
    )
    return await complete_json(
        prompt,
        task="plan",
        backend_override=llm_backend,
        model_override=llm_model,
    )


def generate_skill_target_plan_fast(
    existing_skills: str,
    job_keywords: dict[str, Any],
    confirmed_skills: list[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic skill plan when the planning LLM is unavailable."""
    existing_norm = existing_skills.lower()
    confirmed = {s.strip().lower() for s in (confirmed_skills or []) if s.strip()}
    target_skills: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(skill: str, reason: str) -> None:
        if not skill.strip():
            return
        key = skill.strip().lower()
        if key in seen:
            return
        seen.add(key)
        target_skills.append({"skill": skill.strip(), "reason": reason})

    for skill in [str(s) for s in job_keywords.get("required_skills", [])]:
        if skill.lower() in existing_norm:
            reason = "Required by the JD and already present in resume skills."
        elif skill.lower() in confirmed:
            reason = "Required by the JD and user-confirmed as truthful for this run."
        else:
            reason = "Required by the JD; add only if supported or user-confirmed."
        add(skill, reason)

    for skill in [str(s) for s in job_keywords.get("preferred_skills", [])]:
        if len(target_skills) >= MAX_TARGET_SKILLS:
            break
        if skill.lower() in existing_norm:
            reason = "Preferred by the JD and already present in resume skills."
        elif skill.lower() in confirmed:
            reason = "Preferred by the JD and user-confirmed as truthful for this run."
        else:
            reason = "Preferred by the JD; use only if truthful and supported."
        add(skill, reason)

    return {
        "target_skills": target_skills[:MAX_TARGET_SKILLS],
        "strategy_notes": (
            "Local fallback plan: prioritize required JD skills, then preferred "
            "skills, with confirmation required for absent skills."
        ),
        "planning_method": "fast_local_fallback",
    }


def verify_skill_target_plan(plan: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate the skill target plan structure without calling the LLM."""
    errors: list[str] = []

    target_skills = plan.get("target_skills")
    if not isinstance(target_skills, list):
        errors.append("'target_skills' must be a list")
        return False, errors

    if len(target_skills) > MAX_TARGET_SKILLS:
        errors.append(
            f"Too many target skills: {len(target_skills)} > {MAX_TARGET_SKILLS}"
        )

    for i, item in enumerate(target_skills):
        if not isinstance(item, dict):
            errors.append(f"target_skills[{i}] is not a dict")
            continue
        if "skill" not in item:
            errors.append(f"target_skills[{i}] missing 'skill' key")
        if "reason" not in item:
            errors.append(f"target_skills[{i}] missing 'reason' key")

    if "strategy_notes" not in plan:
        errors.append("'strategy_notes' missing from plan")

    return len(errors) == 0, errors
