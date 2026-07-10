"""Deterministic role, internship, and geography matching."""

from __future__ import annotations

import re

from latex_resume.job_models import (
    JobPosting,
    SearchPreferences,
    TargetRole,
)

ROLE_ALIASES: dict[TargetRole, tuple[str, ...]] = {
    TargetRole.AI_INTERN: (
        "ai intern",
        "artificial intelligence intern",
        "ai internship",
    ),
    TargetRole.ML_INTERN: (
        "machine learning intern",
        "ml intern",
        "machine learning internship",
    ),
    TargetRole.NLP_INTERN: (
        "nlp intern",
        "natural language processing intern",
        "language ai intern",
    ),
    TargetRole.AGENTIC_AI_INTERN: (
        "agentic ai intern",
        "ai agent intern",
        "llm agent intern",
        "generative ai intern",
    ),
    TargetRole.DATA_SCIENCE_INTERN: (
        "data science intern",
        "data scientist intern",
        "data science internship",
    ),
    TargetRole.AI_ENGINEER: (
        "ai engineer",
        "artificial intelligence engineer",
        "generative ai engineer",
        "agentic ai engineer",
    ),
    TargetRole.ML_ENGINEER: (
        "machine learning engineer",
        "ml engineer",
        "applied machine learning engineer",
    ),
    TargetRole.DATA_SCIENTIST: (
        "data scientist",
        "applied scientist",
    ),
}

ROLE_MATCH_PRIORITY: tuple[TargetRole, ...] = (
    TargetRole.AGENTIC_AI_INTERN,
    TargetRole.NLP_INTERN,
    TargetRole.DATA_SCIENCE_INTERN,
    TargetRole.ML_INTERN,
    TargetRole.AI_INTERN,
    TargetRole.DATA_SCIENTIST,
    TargetRole.ML_ENGINEER,
    TargetRole.AI_ENGINEER,
)

_TEXAS_LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "Houston, TX": ("houston", "houston, tx", "houston, texas"),
    "Austin, TX": ("austin", "austin, tx", "austin, texas"),
    "Dallas, TX": (
        "dallas",
        "dallas, tx",
        "dallas, texas",
        "dallas-fort worth",
        "dfw",
    ),
}


def classify_target_role(title: str, description: str = "") -> TargetRole | None:
    """Classify a posting into one selected role family."""
    title_text = _normalize(title)
    combined = _normalize(f"{title} {description[:1500]}")
    internship = bool(re.search(r"\bintern(ship)?\b|co-op", title_text))

    for role in ROLE_MATCH_PRIORITY:
        if any(alias in title_text for alias in ROLE_ALIASES[role]):
            return role

    if internship:
        if any(term in combined for term in ("agentic", "ai agent", "llm agent", "generative ai")):
            return TargetRole.AGENTIC_AI_INTERN
        if any(term in combined for term in ("natural language", "nlp", "language model")):
            return TargetRole.NLP_INTERN
        if "data scien" in combined:
            return TargetRole.DATA_SCIENCE_INTERN
        if any(term in combined for term in ("machine learning", " ml ")):
            return TargetRole.ML_INTERN
        if any(term in combined for term in ("artificial intelligence", " ai ")):
            return TargetRole.AI_INTERN

    if "data scientist" in title_text or "applied scientist" in title_text:
        return TargetRole.DATA_SCIENTIST
    if "machine learning" in title_text or re.search(r"\bml engineer\b", title_text):
        return TargetRole.ML_ENGINEER
    if any(term in title_text for term in ("ai engineer", "generative ai", "agentic ai")):
        return TargetRole.AI_ENGINEER
    return None


def classify_employment_track(title: str, description: str = "") -> str:
    """Distinguish internships so authorization answers remain scoped."""
    text = _normalize(f"{title} {description[:1000]}")
    if re.search(r"\bintern(ship)?\b|co-op", text):
        return "internship"
    if any(term in text for term in ("full-time", "full time", "permanent position")):
        return "full_time"
    return "unknown"


def location_matches(job: JobPosting, preferences: SearchPreferences) -> bool:
    """Return whether a job fits Houston, Austin, Dallas, or US remote rules."""
    location = _normalize(job.location)
    description = _normalize(job.description[:1000])
    if job.workplace_type == "remote" and preferences.allow_remote_us:
        foreign_markers = (
            "canada",
            "united kingdom",
            "europe",
            "emea",
            "india",
            "australia",
        )
        return not any(marker in location for marker in foreign_markers)
    if job.workplace_type == "hybrid" and not preferences.allow_hybrid:
        return False
    if job.workplace_type == "onsite" and not preferences.allow_onsite:
        return False
    for preferred in preferences.preferred_locations:
        aliases = _TEXAS_LOCATION_ALIASES.get(preferred, (preferred.casefold(),))
        if any(alias in location for alias in aliases):
            return True
    return (
        job.workplace_type == "unknown"
        and preferences.allow_remote_us
        and "remote" in description
        and "united states" in description
    )


def preference_score(job: JobPosting, preferences: SearchPreferences) -> float:
    """Score target-role and geography alignment, rejecting irrelevant jobs."""
    if title_is_excluded(job.title, preferences):
        return -1.0
    role = classify_target_role(job.title, job.description)
    if role is None or role not in preferences.target_roles:
        return -1.0
    if not location_matches(job, preferences):
        return -1.0
    score = 10.0
    if role.value.endswith("_intern"):
        score += 3.0
    if job.workplace_type == "remote":
        score += 1.0
    elif any(city.casefold().split(",")[0] in job.location.casefold() for city in ("Houston, TX", "Austin, TX", "Dallas, TX")):
        score += 1.0
    return score


def title_is_excluded(title: str, preferences: SearchPreferences) -> bool:
    """Reject explicitly excluded seniority or management terms in a job title."""
    normalized = _collapse(
        re.sub(r"[^a-z0-9]+", " ", title.casefold())
    )
    title_tokens = set(normalized.split())
    for term in preferences.excluded_title_terms:
        cleaned = _collapse(
            re.sub(r"[^a-z0-9]+", " ", term.casefold())
        )
        if cleaned and (
            cleaned in title_tokens
            or (len(cleaned.split()) > 1 and cleaned in normalized)
        ):
            return True
    return False


def enrich_job(job: JobPosting) -> JobPosting:
    """Attach deterministic role and employment metadata."""
    return job.model_copy(
        update={
            "target_role": classify_target_role(job.title, job.description),
            "employment_track": classify_employment_track(job.title, job.description),
        }
    )


def _normalize(value: str) -> str:
    return f" {_collapse(value.casefold())} "


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
