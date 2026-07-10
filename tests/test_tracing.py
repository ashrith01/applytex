"""Tests for optional ApplyTeX ATS tracing."""

from __future__ import annotations

from latex_resume.tracing import PipelineTracer, hash_text, is_langsmith_enabled


def test_langsmith_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SMARTJOBAPPLY_LANGSMITH_TRACE", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    assert not is_langsmith_enabled()


def test_langsmith_disabled_during_pytest_even_when_env_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_optimizer.py::test_x")
    monkeypatch.setenv("SMARTJOBAPPLY_LANGSMITH_TRACE", "true")
    monkeypatch.delenv("SMARTJOBAPPLY_LANGSMITH_TRACE_IN_TESTS", raising=False)

    assert not is_langsmith_enabled()


def test_pipeline_tracer_noops_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SMARTJOBAPPLY_LANGSMITH_TRACE", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    tracer = PipelineTracer("test", inputs={"jd_hash": hash_text("jd")})
    stage = tracer.stage("stage1", run_type="tool")
    tracer.end_stage(stage, outputs={"ok": True})
    tracer.finish(outputs={"score_after": 90.0})

    assert not tracer.enabled
    assert tracer.stage_latencies_ms["stage1"] >= 0
    assert hash_text("jd") == hash_text("jd")
