"""Tests for local optimization-run analytics."""

from __future__ import annotations

from types import SimpleNamespace

from latex_resume.ats import ATSResult
from latex_resume.run_analysis import (
    append_run_record,
    build_run_record,
    load_run_records,
)


def test_build_run_record_captures_ats_delta_and_gaps() -> None:
    before = ATSResult(
        score=62.5,
        required_score=50.0,
        preferred_score=50.0,
        keyword_score=100.0,
        required_found=["Python"],
        required_missing=["Azure"],
        preferred_found=[],
        preferred_missing=["LangChain"],
        keyword_hits=["ML"],
        keyword_misses=[],
    )
    after = ATSResult(
        score=87.5,
        required_score=100.0,
        preferred_score=50.0,
        keyword_score=100.0,
        required_found=["Python", "Azure"],
        required_missing=[],
        preferred_found=[],
        preferred_missing=["LangChain"],
        keyword_hits=["ML"],
        keyword_misses=[],
    )
    result = SimpleNamespace(
        ats_before=before,
        ats_after=after,
        confirmation_required_skills=["LangChain"],
        ats_target_score=80.0,
        ats_target_met=True,
        diff=[{"stmt_id": "skills_0", "reason": "x", "original": "Python", "value": "Python, Azure"}],
        rejected_changes=[],
        warnings=[],
        page_count=1,
        overflow=False,
        trace_id="trace-123",
        model_routes={"diff": {"backend": "groq", "model": "qwen"}},
        stage_latencies_ms={"stage4_generate_latex_diffs": 1200.0},
        optimization_strategy="one_page_strict",
        reviewer_backend="langchain",
    )

    record = build_run_record(
        result,
        "Need Python Azure LangChain",
        ["Azure"],
        "r1",
        screening_analysis={
            "match_category": "Strong Fit",
            "match_breakdown": {"overall_score": 87.5},
        },
        latency_ms={"jd_analysis": 10.0, "optimization": 20.0},
    )

    assert record["score_delta"] == 25.0
    assert record["newly_covered"] == ["Azure"]
    assert record["remaining_gaps"]["preferred"] == ["LangChain"]
    assert record["top_score_leaks"][0]["item"] == "LangChain"
    assert record["screening_analysis"]["match_category"] == "Strong Fit"
    assert record["latency_ms"]["optimization"] == 20.0
    assert record["trace_id"] == "trace-123"
    assert record["model_routes"]["diff"]["backend"] == "groq"
    assert record["stage_latencies_ms"]["stage4_generate_latex_diffs"] == 1200.0
    assert record["optimization_strategy"] == "one_page_strict"
    assert record["reviewer_backend"] == "langchain"
    assert record["match_breakdown"]["overall_score"] == 87.5
    assert "Confirm truthful skills" in record["recommendations"][0]


def test_append_and_load_run_records(tmp_path) -> None:
    path = tmp_path / "runs.jsonl"
    append_run_record({"run_id": "old", "created_at": "1"}, path)
    append_run_record({"run_id": "new", "created_at": "2"}, path)

    records = load_run_records(path)

    assert [r["run_id"] for r in records] == ["new", "old"]
