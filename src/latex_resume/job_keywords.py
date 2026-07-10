"""Job-description keyword extraction for ATS analysis and optimization."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from latex_resume.llm import _sanitize_user_input, complete_json
from latex_resume.prompts import EXTRACT_KEYWORDS_PROMPT

logger = logging.getLogger(__name__)


_FAST_SKILL_CATALOG: tuple[str, ...] = (
    "Python", "SQL", "Java", "JavaScript", "TypeScript", "R", "Go", "Rust", "C++",
    "PyTorch", "TensorFlow", "Keras", "Scikit-learn", "Pandas", "NumPy",
    "Spark", "Databricks", "MLflow", "Airflow", "FastAPI", "Streamlit",
    "Docker", "Kubernetes", "Git", "GitHub", "CI/CD",
    "AWS", "Azure", "GCP", "Snowflake", "PostgreSQL", "MongoDB",
    "Pinecone", "ChromaDB", "LangChain", "LangGraph", "LlamaIndex",
    "CrewAI", "AutoGen", "OpenAI", "Claude", "Gemini", "Hugging Face",
    "Transformers", "RAG", "LLM", "LLMs", "Generative AI", "NLP",
    "Computer Vision", "Machine Learning", "Deep Learning", "MLOps",
    "Model Deployment", "Model Monitoring", "Feature Engineering",
    "A/B Testing", "Statistics", "Predictive Modeling", "Recommendation Systems",
    "Embeddings", "Vector Databases", "Responsible AI", "Explainable AI", "XAI",
    "VS Code", "GitHub Copilot", "Codex", "Claude Code",
)

_FAST_KEYWORD_CATALOG: tuple[str, ...] = (
    "cloud-native", "agentic AI", "AI agents", "responsible AI", "ethical AI",
    "model interpretability", "latency", "cost optimization", "scale",
    "high-volume", "consumer facing", "eCommerce", "digital experience",
    "healthcare", "stakeholder", "communication", "cross-functional",
    "production", "enterprise",
)

_REQUIRED_SIGNAL_RE = re.compile(
    r"\b(required|requirement|requirements|must|need|required qualifications|"
    r"minimum qualifications|responsibilities|you will|experience with|proficient)\b",
    re.IGNORECASE,
)
_PREFERRED_SIGNAL_RE = re.compile(
    r"\b(preferred|nice to have|nice-to-have|bonus|plus|desired|would be a plus)\b",
    re.IGNORECASE,
)
_YEARS_RE = re.compile(r"\b(\d+)\+?\s*(?:years|yrs)\b", re.IGNORECASE)


def extract_job_keywords_fast(job_description: str) -> dict[str, Any]:
    """Fast local JD extractor for interactive ATS analysis."""
    jd = _sanitize_user_input(job_description)
    sentences = _split_jd_sentences(jd)
    required: list[str] = []
    preferred: list[str] = []
    keywords: list[str] = []

    for skill in _FAST_SKILL_CATALOG:
        matched = [s for s in sentences if _phrase_in_text(skill, s)]
        if not matched:
            continue
        if any(_PREFERRED_SIGNAL_RE.search(s) for s in matched):
            _append_unique(preferred, skill)
        elif any(_REQUIRED_SIGNAL_RE.search(s) for s in matched):
            _append_unique(required, skill)
        else:
            _append_unique(required, skill)

    for phrase in _FAST_KEYWORD_CATALOG:
        if _phrase_in_text(phrase, jd):
            _append_unique(keywords, phrase)

    years = [int(m.group(1)) for m in _YEARS_RE.finditer(jd)]
    return {
        "required_skills": required,
        "preferred_skills": preferred,
        "experience_requirements": [f"{min(years)}+ years"] if years else [],
        "education_requirements": _extract_education_requirements(jd),
        "key_responsibilities": [],
        "keywords": keywords,
        "experience_years": min(years) if years else None,
        "seniority_level": _infer_seniority(jd),
        "extraction_method": "fast_local",
    }


async def extract_job_keywords(job_description: str) -> dict[str, Any]:
    """Call the LLM to extract structured requirements from *job_description*."""
    safe_jd = _sanitize_user_input(job_description)
    prompt = EXTRACT_KEYWORDS_PROMPT.format(job_description=safe_jd)
    result: dict[str, Any] = await complete_json(prompt, task="jd")
    logger.info(
        "Keywords extracted: %d required, %d preferred, %d keywords",
        len(result.get("required_skills", [])),
        len(result.get("preferred_skills", [])),
        len(result.get("keywords", [])),
    )
    return result


async def extract_job_keywords_with_fallback(job_description: str) -> dict[str, Any]:
    """Use LLM keyword extraction, falling back to the deterministic extractor."""
    try:
        result = await extract_job_keywords(job_description)
        result.setdefault("extraction_method", "deep_llm")
        return result
    except Exception as exc:
        logger.warning("Deep JD keyword extraction failed; using fast fallback: %s", exc)
        fallback = extract_job_keywords_fast(job_description)
        fallback["extraction_method"] = "fast_local_fallback"
        fallback["llm_error"] = str(exc)
        return fallback


def _split_jd_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _phrase_in_text(phrase: str, text: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(phrase).replace(r"\ ", r"[\s\-/]+") + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _append_unique(items: list[str], item: str) -> None:
    if item.lower() not in {existing.lower() for existing in items}:
        items.append(item)


def _infer_seniority(text: str) -> str:
    norm = text.lower()
    if "principal" in norm:
        return "principal"
    if "staff" in norm:
        return "staff"
    if "senior" in norm or "sr." in norm or "lead" in norm:
        return "senior"
    if "intern" in norm:
        return "intern"
    if "junior" in norm or "entry level" in norm:
        return "junior"
    return "mid"


def _extract_education_requirements(text: str) -> list[str]:
    out: list[str] = []
    if re.search(r"\b(bachelor'?s?|bs|b\.s\.)\b", text, re.IGNORECASE):
        out.append("Bachelor's degree")
    if re.search(r"\b(master'?s?|ms|m\.s\.)\b", text, re.IGNORECASE):
        out.append("Master's degree")
    if re.search(r"\b(phd|ph\.d|doctorate)\b", text, re.IGNORECASE):
        out.append("PhD")
    return out
