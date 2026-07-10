"""Optional LangChain-backed recruiter reviewer.

This module is intentionally imported lazily by the optimizer. ApplyTeX ATS's
core pipeline must keep working without LangChain installed; when enabled, this
adapter uses LangChain only to generate recruiter-style review JSON, then the
normal validators decide whether any edits are safe to apply.
"""

from __future__ import annotations

import json
import os
from typing import Any

from latex_resume.llm import (
    ANTHROPIC_MODEL,
    GROQ_MODEL,
    LLM_TEMPERATURE,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL_REVIEW,
    OPENAI_MODEL,
    _extract_json,
    _sanitize_user_input,
)
from latex_resume.prompts import RECRUITER_REVIEW_PROMPT


async def generate_langchain_recruiter_review(
    *,
    ats_summary: dict[str, Any],
    confirmed_skills: list[str],
    job_keywords: dict[str, Any],
    job_description: str,
    resume_plain_text: str,
    editable_json: dict[str, str],
    backend: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Generate recruiter review JSON through a LangChain chat model."""
    chat_model = _build_chat_model(backend, model)
    prompt = RECRUITER_REVIEW_PROMPT.format(
        ats_summary=json.dumps(ats_summary, ensure_ascii=False),
        confirmed_skills=", ".join(confirmed_skills) or "None",
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        job_description=_sanitize_user_input(job_description),
        resume_plain_text=_sanitize_user_input(resume_plain_text),
        editable_json=json.dumps(editable_json, indent=2, ensure_ascii=False),
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_core.output_parsers import JsonOutputParser
    except ImportError as exc:
        raise EnvironmentError(
            "LangChain reviewer requires `langchain-core`. Install the optional "
            "LangChain packages before setting SMARTJOBAPPLY_REVIEWER_BACKEND=langchain."
        ) from exc

    messages = [
        SystemMessage(
            content=(
                "You are a precise recruiter-review agent inside ApplyTeX ATS. "
                "Return only one valid JSON object."
            )
        ),
        HumanMessage(content=prompt),
    ]
    response = await chat_model.ainvoke(messages)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(part) for part in content)
    text = str(content)
    parser = JsonOutputParser()
    try:
        parsed = parser.parse(text)
    except Exception:
        parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        raise ValueError("LangChain reviewer returned JSON that was not an object.")
    return parsed


def _build_chat_model(backend: str | None, model: str | None) -> Any:
    """Create a LangChain chat model for the selected backend."""
    normalized = (backend or os.environ.get("LANGCHAIN_REVIEWER_BACKEND") or "groq").lower()

    if normalized == "groq":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise EnvironmentError(
                "LangChain Groq reviewer requires `langchain-openai`."
            ) from exc
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is required for LangChain Groq reviewer.")
        return ChatOpenAI(
            model=model or os.environ.get("LANGCHAIN_REVIEWER_MODEL") or GROQ_MODEL,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            temperature=LLM_TEMPERATURE,
        )

    if normalized == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise EnvironmentError(
                "LangChain Ollama reviewer requires `langchain-ollama`."
            ) from exc
        return ChatOllama(
            model=model or os.environ.get("LANGCHAIN_REVIEWER_MODEL") or OLLAMA_MODEL_REVIEW,
            base_url=OLLAMA_BASE_URL,
            temperature=LLM_TEMPERATURE,
        )

    if normalized == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise EnvironmentError(
                "LangChain OpenAI reviewer requires `langchain-openai`."
            ) from exc
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is required for LangChain OpenAI reviewer.")
        return ChatOpenAI(
            model=model or os.environ.get("LANGCHAIN_REVIEWER_MODEL") or OPENAI_MODEL,
            api_key=api_key,
            temperature=LLM_TEMPERATURE,
        )

    if normalized == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise EnvironmentError(
                "LangChain Anthropic reviewer requires `langchain-anthropic`."
            ) from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is required for LangChain Anthropic reviewer.")
        return ChatAnthropic(
            model=model or os.environ.get("LANGCHAIN_REVIEWER_MODEL") or ANTHROPIC_MODEL,
            api_key=api_key,
            temperature=LLM_TEMPERATURE,
        )

    raise ValueError(
        f"Unsupported LangChain reviewer backend {normalized!r}. "
        "Choose groq, ollama, openai, or anthropic."
    )
