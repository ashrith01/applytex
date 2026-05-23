from __future__ import annotations

from latex_resume.models import LayoutParams, ParseResult
from latex_resume.reconstructor import apply_changes, set_layout_params


def test_single_change_only_touches_target_span(parsed: ParseResult) -> None:
    src = parsed.latex_source
    span = parsed.stmt_index["work_0_0"]
    new_text = "Designed and shipped REST APIs in Django on AWS"
    result = apply_changes(src, {"work_0_0": new_text}, parsed.stmt_index)

    expected = src[: span.tex_start] + new_text + src[span.tex_end :]
    assert result.latex == expected
    assert result.applied == ["work_0_0"]
    assert result.rejected == {}
    # Everything outside the target span is byte-identical.
    assert result.latex[: span.tex_start] == src[: span.tex_start]
    assert result.latex.endswith(src[span.tex_end :])


def test_multiple_changes_independent_offsets(parsed: ParseResult) -> None:
    src = parsed.latex_source
    changes = {
        "work_0_0": "AAAA",
        "skills_1": "BBBB",
        "summary_0": "CCCC",
    }
    result = apply_changes(src, changes, parsed.stmt_index)
    assert set(result.applied) == set(changes)

    # Build the expected string by manual descending splice.
    spans = sorted(
        ((parsed.stmt_index[k].tex_start, parsed.stmt_index[k].tex_end, v) for k, v in changes.items()),
        key=lambda t: t[0],
        reverse=True,
    )
    expected = src
    for start, end, value in spans:
        expected = expected[:start] + value + expected[end:]
    assert result.latex == expected
    assert "AAAA" in result.latex and "BBBB" in result.latex and "CCCC" in result.latex


def test_unknown_stmt_id_rejected(parsed: ParseResult) -> None:
    result = apply_changes(parsed.latex_source, {"nope_9_9": "x"}, parsed.stmt_index)
    assert result.applied == []
    assert result.rejected == {"nope_9_9": "unknown stmt_id"}
    assert result.latex == parsed.latex_source


def test_locked_id_rejected(parsed: ParseResult) -> None:
    result = apply_changes(
        parsed.latex_source,
        {"work_0_0": "x"},
        parsed.stmt_index,
        locked_ids=frozenset({"work_0_0"}),
    )
    assert result.applied == []
    assert result.rejected == {"work_0_0": "locked statement"}


def test_layout_block_inserted_then_replaced(parsed: ParseResult) -> None:
    src = parsed.latex_source
    once = set_layout_params(src, LayoutParams(margin_mm=15, line_spacing=1.05))
    assert once.count("%%LAYOUT_PARAMS_START%%") == 1
    assert "margin=15.0mm" in once or "margin=15mm" in once

    twice = set_layout_params(once, LayoutParams(margin_mm=12, line_spacing=0.95))
    assert twice.count("%%LAYOUT_PARAMS_START%%") == 1  # replaced, not duplicated
    assert "margin=12.0mm" in twice or "margin=12mm" in twice
    assert "15.0mm" not in twice and "margin=15mm" not in twice


def test_layout_block_before_begin_document(parsed: ParseResult) -> None:
    out = set_layout_params(parsed.latex_source, LayoutParams())
    assert out.index("%%LAYOUT_PARAMS_START%%") < out.index("\\begin{document}")
