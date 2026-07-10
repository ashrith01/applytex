"""Regression tests for the ApplyTeX ATS MVP benchmark subsystem."""

from __future__ import annotations

from latex_resume.benchmark.audit import audit_optimized_resume
from latex_resume.benchmark.corpus import (
    ROLE_PROFILES,
    _build_evidence_ledger,
    _classify_role,
    _render_resume,
    _strip_html,
)
from latex_resume.benchmark.io import JOB_MANIFEST, RESUME_MANIFEST, read_jsonl
from latex_resume.benchmark.models import (
    EvidenceLedger,
    JobFixture,
    ResumeFixture,
)
from latex_resume.benchmark.report import _spearman
from latex_resume.benchmark.runner import _provider_error_from_warnings
from latex_resume.llm import _record_usage, get_usage, reset_usage
from latex_resume.parser import parse


def test_all_resume_template_variants_parse() -> None:
    for index, template in enumerate(
        ("classic", "compact", "custom_commands", "project_first")
    ):
        ledger = _build_evidence_ledger(
            f"fixture-{index}",
            "ai_engineer",
            "mid",
            "strong",
            index,
            __import__("random").Random(7),
        )
        latex = _render_resume(
            resume_id=f"fixture-{index}",
            name="Synthetic Candidate",
            role="ai_engineer",
            seniority="mid",
            template_id=template,
            ledger=ledger,
            profile=ROLE_PROFILES["ai_engineer"],
            index=index,
        )
        parsed = parse(latex, resume_id=f"fixture-{index}")
        assert parsed.stmt_index
        assert any(stmt_id.startswith("work_") for stmt_id in parsed.stmt_index)
        assert any(stmt_id.startswith("skills_") for stmt_id in parsed.stmt_index)


def test_audit_rejects_new_unsupported_tool_and_metric() -> None:
    original = r"""
\documentclass{article}
\begin{document}
\section*{Summary}
AI engineer using Python.
\section*{Experience}
\begin{itemize}
\item Built Python services with 10\% lower latency.
\end{itemize}
\section*{Skills}
\begin{itemize}
\item Python
\end{itemize}
\end{document}
"""
    modified = original.replace(
        "Built Python services with 10\\% lower latency.",
        "Built Amazon Bedrock services with 25\\% lower latency.",
    )
    job = JobFixture(
        job_id="j1",
        title="AI Engineer",
        company="Synthetic",
        role_family="ai_engineer",
        seniority="mid",
        industry="technology",
        source_kind="adversarial",
        provider="synthetic",
        source_url="benchmark://test",
        captured_at="2026-01-01T00:00:00+00:00",
        text_path="unused",
        content_sha256="x",
        required_skills=["Python", "Amazon Bedrock"],
    )
    ledger = EvidenceLedger(
        resume_id="r1",
        skills=["Python"],
        metrics=["10\\%"],
        education=[],
        employers=[],
    )
    audit = audit_optimized_resume(
        original,
        modified,
        job,
        ledger,
        [
            {
                "stmt_id": "work_0_0",
                "original": "Built Python services with 10\\% lower latency.",
                "value": "Built Amazon Bedrock services with 25\\% lower latency.",
            }
        ],
    )
    assert audit["unsupported_claims"] == ["Amazon Bedrock"]
    assert "25%" in audit["introduced_metrics"]


def test_live_job_helpers_normalize_and_classify() -> None:
    assert _strip_html("<p>Build ML systems</p><li>Python</li>") == "Build ML systems\n Python"
    assert _classify_role("Senior Machine Learning Engineer", "") == "ml_engineer"
    assert _classify_role("Data Scientist", "") == "data_scientist"


def test_spearman_handles_ties() -> None:
    assert _spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert _spearman([1, 2, 3], [3, 2, 1]) == -1.0


def test_llm_usage_accumulates_per_execution_context() -> None:
    reset_usage()
    _record_usage(10, 4)
    _record_usage(5, 2)
    assert get_usage() == {
        "prompt_tokens": 15,
        "completion_tokens": 6,
        "total_tokens": 21,
    }


def test_provider_fallback_warning_is_not_valid_benchmark_output() -> None:
    warning = "Stage 4 failed: You've hit your usage limit. Try again later."
    assert _provider_error_from_warnings("codex", [warning]) == warning
    assert _provider_error_from_warnings("groq", ["Ordinary optimizer warning"]) is None


def test_generated_mvp_manifests_have_expected_shape() -> None:
    resumes = read_jsonl(RESUME_MANIFEST, ResumeFixture)
    jobs = read_jsonl(JOB_MANIFEST, JobFixture)
    assert len(resumes) == 40
    assert len(jobs) == 120
    assert sum(item.holdout for item in resumes) == 8
    assert sum(item.holdout for item in jobs) == 24
    assert all(item.parser_ok and item.render_ok and not item.overflow for item in resumes)
    assert {item.source_kind for item in jobs} == {
        "live_public",
        "taxonomy_derived",
        "adversarial",
    }
