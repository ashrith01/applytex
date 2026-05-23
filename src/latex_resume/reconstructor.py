"""Rebuild ``.tex`` source from edited statements via position-sorted splicing.

The core guarantee: only the character spans of *changed* statements are replaced.
Sorting replacements by start position descending means each splice cannot shift the
offsets of any not-yet-applied splice, so every other byte of the document -- every
command, environment, package, comment -- survives unchanged.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from latex_resume.models import LayoutParams, StmtSpan

_LAYOUT_START = "%%LAYOUT_PARAMS_START%%"
_LAYOUT_END = "%%LAYOUT_PARAMS_END%%"
_LAYOUT_BLOCK_RE = re.compile(
    re.escape(_LAYOUT_START) + r".*?" + re.escape(_LAYOUT_END), re.DOTALL
)


class ReconstructResult(BaseModel):
    """Outcome of applying statement edits to a source document."""

    latex: str
    applied: list[str] = Field(default_factory=list)
    rejected: dict[str, str] = Field(default_factory=dict)


def apply_changes(
    latex_source: str,
    changes: dict[str, str],
    stmt_index: dict[str, StmtSpan],
    locked_ids: frozenset[str] | None = None,
) -> ReconstructResult:
    """Splice ``changes`` (``stmt_id -> new text``) into ``latex_source``.

    A change is rejected (and reported, not applied) when its ``stmt_id`` is unknown
    or is in ``locked_ids``. Accepted changes are spliced in descending start order.
    """
    locked = locked_ids or frozenset()
    applied: list[str] = []
    rejected: dict[str, str] = {}
    spans: list[tuple[int, int, str, str]] = []  # (start, end, new_text, stmt_id)

    for stmt_id, new_text in changes.items():
        if stmt_id in locked:
            rejected[stmt_id] = "locked statement"
            continue
        span = stmt_index.get(stmt_id)
        if span is None:
            rejected[stmt_id] = "unknown stmt_id"
            continue
        spans.append((span.tex_start, span.tex_end, new_text, stmt_id))

    spans.sort(key=lambda s: s[0], reverse=True)

    result = latex_source
    for start, end, new_text, stmt_id in spans:
        result = result[:start] + new_text + result[end:]
        applied.append(stmt_id)

    applied.reverse()  # report in document order
    return ReconstructResult(latex=result, applied=applied, rejected=rejected)


def set_layout_params(tex: str, params: LayoutParams) -> str:
    """Insert or replace the controlled layout-parameter block in the preamble.

    On first call the block (and a ``geometry`` package load) is inserted just before
    ``\\begin{document}``. Subsequent calls replace only the block content, leaving the
    rest of the preamble untouched.
    """
    block = _layout_block(params)
    if _LAYOUT_BLOCK_RE.search(tex):
        return _LAYOUT_BLOCK_RE.sub(lambda _m: block, tex, count=1)

    insertion = f"\\usepackage{{geometry}}\n{block}\n"
    begin = tex.find("\\begin{document}")
    if begin == -1:
        return tex  # Nothing sensible to do without a document body.
    return tex[:begin] + insertion + tex[begin:]


def _layout_block(params: LayoutParams) -> str:
    """Render the layout block from :class:`LayoutParams`."""
    baseline = round(params.font_size_pt * 1.2, 2)
    return (
        f"{_LAYOUT_START}\n"
        f"\\geometry{{margin={params.margin_mm}mm}}\n"
        f"\\linespread{{{params.line_spacing}}}\n"
        f"\\AtBeginDocument{{\\fontsize{{{params.font_size_pt}}}{{{baseline}}}\\selectfont}}\n"
        f"{_LAYOUT_END}"
    )
