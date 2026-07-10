"""Regression tests for PDF page-fit geometry."""

from __future__ import annotations

from typing import Any, Callable

from latex_resume.renderer import MIN_BOTTOM_TEXT_BASELINE_PT, _minimum_text_baseline


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
