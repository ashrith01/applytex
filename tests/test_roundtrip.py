from __future__ import annotations

import pytest

from latex_resume.models import LayoutParams, ParseResult
from latex_resume.reconstructor import apply_changes, set_layout_params
from latex_resume.renderer import pdflatex_available, render_pdf

needs_latex = pytest.mark.skipif(
    not pdflatex_available(), reason="pdflatex not installed"
)


def test_noop_reconstruct_is_byte_identical(parsed: ParseResult) -> None:
    result = apply_changes(parsed.latex_source, {}, parsed.stmt_index)
    assert result.latex == parsed.latex_source


@needs_latex
def test_original_renders_single_page(parsed: ParseResult) -> None:
    result = render_pdf(parsed.latex_source)
    assert result.ok, result.log[-800:]
    assert result.page_count == 1
    assert not result.overflow


@needs_latex
def test_small_edit_still_compiles_one_page(parsed: ParseResult) -> None:
    target = parsed.doc.editable_statements()[0]
    changes = {target.stmt_id: target.text + " using Python and FastAPI"}
    modified = apply_changes(parsed.latex_source, changes, parsed.stmt_index)
    result = render_pdf(modified.latex)
    assert result.ok, result.log[-800:]
    assert result.page_count == 1


@needs_latex
def test_layout_params_compile(parsed: ParseResult) -> None:
    tex = set_layout_params(parsed.latex_source, LayoutParams(margin_mm=15, font_size_pt=10.5, line_spacing=1.05))
    result = render_pdf(tex)
    assert result.ok, result.log[-800:]
    assert result.page_count == 1
