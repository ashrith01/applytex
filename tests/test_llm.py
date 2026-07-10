"""Tests for LLM routing helpers."""

from __future__ import annotations

from latex_resume.llm import (
    backend_for_task,
    codex_model_for_task,
    model_for_backend_task,
    ollama_model_for_task,
)


def test_backend_override_wins_over_task_routing() -> None:
    assert backend_for_task("diff", override_backend="groq") == "groq"


def test_ollama_model_override_wins_over_task_model() -> None:
    assert ollama_model_for_task("plan", override_model="qwen3:4b") == "qwen3:4b"


def test_codex_model_override_wins() -> None:
    assert codex_model_for_task("diff", override_model="gpt-5.3-codex") == "gpt-5.3-codex"


def test_model_label_helper_respects_override() -> None:
    assert model_for_backend_task("groq", "jd", "llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"
