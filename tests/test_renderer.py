"""Regression tests for PDF page-fit geometry and renderer resilience."""

from __future__ import annotations

import subprocess
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from latex_resume.renderer import (
    MIN_BOTTOM_TEXT_BASELINE_PT,
    RenderResult,
    _minimum_text_baseline,
    check_one_page,
    estimate_word_count,
    pdflatex_available,
    render_pdf,
)


# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, baselines: list[tuple[str, float]]) -> None:
        self.baselines = baselines

    def extract_text(self, *, visitor_text: Callable[..., None]) -> str:
        for text, y in self.baselines:
            visitor_text(text, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, y], None, 9.0)
        return ""


class _FakeReader:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages


# ---------------------------------------------------------------------------
# _minimum_text_baseline
# ---------------------------------------------------------------------------


def test_minimum_text_baseline_detects_content_below_page() -> None:
    reader: Any = _FakeReader(
        [_FakePage([("Summary", 720.0), ("Certifications", -1.9)])]
    )

    assert _minimum_text_baseline(reader) == -1.9
    assert _minimum_text_baseline(reader) < MIN_BOTTOM_TEXT_BASELINE_PT


def test_minimum_text_baseline_ignores_empty_and_annotation_zero() -> None:
    reader: Any = _FakeReader(
        [_FakePage([("", -20.0), ("Link annotation", 0.0), ("Resume text", 8.5)])]
    )

    assert _minimum_text_baseline(reader) == 8.5


def test_minimum_text_baseline_returns_none_for_empty_pages() -> None:
    reader: Any = _FakeReader([_FakePage([])])
    assert _minimum_text_baseline(reader) is None


def test_minimum_text_baseline_multi_page_takes_global_min() -> None:
    reader: Any = _FakeReader([
        _FakePage([("Page 1 header", 720.0)]),
        _FakePage([("Page 2 footer", 3.5)]),
    ])
    assert _minimum_text_baseline(reader) == 3.5


# ---------------------------------------------------------------------------
# estimate_word_count
# ---------------------------------------------------------------------------


def test_estimate_word_count_strips_commands_and_counts_words() -> None:
    tex = r"""
\begin{document}
\item Python, JavaScript, TypeScript
\item Designed and deployed scalable microservices using Docker and Kubernetes.
\end{document}
"""
    count = estimate_word_count(tex)
    assert count > 0
    # Should not count LaTeX commands as words
    assert count < 30


def test_estimate_word_count_uses_document_body_only() -> None:
    preamble = r"\usepackage{fontenc}\usepackage{geometry}"
    body = r"\begin{document}Hello World\end{document}"
    assert estimate_word_count(preamble + body) == estimate_word_count(body)


def test_estimate_word_count_handles_no_document_env() -> None:
    tex = r"\item skill one \item skill two"
    assert estimate_word_count(tex) > 0


# ---------------------------------------------------------------------------
# render_pdf — engine unavailable
# ---------------------------------------------------------------------------


def test_render_pdf_returns_error_when_engine_not_found() -> None:
    with patch("latex_resume.renderer.pdflatex_available", return_value=False):
        result = render_pdf("\\begin{document}Hello\\end{document}")
    assert result.ok is False
    assert result.error == "engine_not_found"
    assert result.pdf_bytes is None


# ---------------------------------------------------------------------------
# render_pdf — subprocess.TimeoutExpired
# ---------------------------------------------------------------------------


def test_render_pdf_returns_timeout_error_on_hung_pdflatex() -> None:
    with (
        patch("latex_resume.renderer.pdflatex_available", return_value=True),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pdflatex", timeout=60),
        ),
    ):
        result = render_pdf("\\begin{document}Hello\\end{document}", timeout=60)

    assert result.ok is False
    assert result.error == "timeout"
    assert "60" in result.log


# ---------------------------------------------------------------------------
# render_pdf — compile failure (no PDF produced)
# ---------------------------------------------------------------------------


def test_render_pdf_returns_compile_failed_when_no_pdf_produced() -> None:
    fake_proc = MagicMock()
    fake_proc.stdout = "! LaTeX Error: File not found.\n"
    fake_proc.stderr = ""

    with (
        patch("latex_resume.renderer.pdflatex_available", return_value=True),
        patch("subprocess.run", return_value=fake_proc),
    ):
        result = render_pdf("\\begin{document}bad tex\\end{document}")

    assert result.ok is False
    assert result.error == "compile_failed"
    assert "LaTeX Error" in result.log


# ---------------------------------------------------------------------------
# render_pdf — --no-shell-escape enforced
# ---------------------------------------------------------------------------


def test_render_pdf_passes_no_shell_escape_flag() -> None:
    """Verify pdflatex is always called with --no-shell-escape."""
    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    with (
        patch("latex_resume.renderer.pdflatex_available", return_value=True),
        patch("subprocess.run", side_effect=fake_run),
    ):
        render_pdf("\\begin{document}x\\end{document}", timeout=1)

    assert "--no-shell-escape" in captured_cmd


# ---------------------------------------------------------------------------
# check_one_page — word-count fallback (no pdflatex)
# ---------------------------------------------------------------------------


def test_check_one_page_word_count_fallback_under_budget() -> None:
    short_tex = r"\begin{document}" + "Word " * 100 + r"\end{document}"
    with patch("latex_resume.renderer.pdflatex_available", return_value=False):
        result = check_one_page(short_tex, word_budget=420)
    assert result.estimated is True
    assert result.overflow is False
    assert result.ok is True


def test_check_one_page_word_count_fallback_over_budget() -> None:
    long_tex = r"\begin{document}" + "Word " * 500 + r"\end{document}"
    with patch("latex_resume.renderer.pdflatex_available", return_value=False):
        result = check_one_page(long_tex, word_budget=420)
    assert result.estimated is True
    assert result.overflow is True
    assert result.page_count == 2


# ---------------------------------------------------------------------------
# check_one_page — one-page enforcement (pdflatex available, mocked)
# ---------------------------------------------------------------------------


def test_check_one_page_delegates_to_render_pdf_when_engine_available() -> None:
    mock_result = RenderResult(ok=True, page_count=1, overflow=False, pdf_bytes=b"%PDF")

    with (
        patch("latex_resume.renderer.pdflatex_available", return_value=True),
        patch("latex_resume.renderer.render_pdf", return_value=mock_result) as mock_render,
    ):
        result = check_one_page("any tex", engine="pdflatex")

    mock_render.assert_called_once()
    assert result.overflow is False
    assert result.estimated is False


def test_check_one_page_overflow_flagged_on_two_page_result() -> None:
    mock_result = RenderResult(ok=True, page_count=2, overflow=True, pdf_bytes=b"%PDF")

    with (
        patch("latex_resume.renderer.pdflatex_available", return_value=True),
        patch("latex_resume.renderer.render_pdf", return_value=mock_result),
    ):
        result = check_one_page("fat resume tex")

    assert result.overflow is True
    assert result.page_count == 2
