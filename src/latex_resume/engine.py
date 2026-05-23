"""High-level facade tying parse -> edit -> reconstruct -> render together.

Also provides a CLI smoke test:

    uv run python -m latex_resume.engine samples/sample_resume.tex
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from latex_resume.models import LayoutParams, ParseResult
from latex_resume.parser import parse
from latex_resume.reconstructor import ReconstructResult, apply_changes, set_layout_params
from latex_resume.renderer import check_one_page, render_pdf


def parse_file(path: str | Path, resume_id: str = "") -> ParseResult:
    """Parse a ``.tex`` file from disk into a :class:`ParseResult`."""
    text = Path(path).read_text(encoding="utf-8")
    return parse(text, resume_id=resume_id)


def reconstruct(
    parse_result: ParseResult,
    changes: dict[str, str],
    layout: LayoutParams | None = None,
) -> ReconstructResult:
    """Apply statement edits and optional layout params to a parsed resume."""
    result = apply_changes(parse_result.latex_source, changes, parse_result.stmt_index)
    if layout is not None:
        result.latex = set_layout_params(result.latex, layout)
    return result


def _print_structure(pr: ParseResult) -> None:
    doc = pr.doc
    print(f"  document class : {doc.latex_class}")
    print(f"  sections       : {len(doc.sections)}")
    if doc.classification_uncertain_sections:
        print(f"  uncertain      : {', '.join(doc.classification_uncertain_sections)}")
    print(
        f"  page budget    : ~{doc.page_budget.estimated_word_count} words, "
        f"{doc.page_budget.estimated_bullet_count} bullets\n"
    )
    for section in doc.sections:
        lock = "LOCKED" if section.is_locked else "edit"
        flag = " (uncertain)" if section.classification_uncertain else ""
        print(f"  [{lock:6}] {section.section_type.value:16} {section.display_name}{flag}")
        for stmt in section.statements:
            print(f"            - {stmt.stmt_id}: {stmt.text[:70]}")
        for entry in section.entries:
            label = entry.title or entry.header_text[:50].replace(chr(10), " ")
            print(f"            * entry {entry.entry_id}: {label}")
            for stmt in entry.statements:
                print(f"                - {stmt.stmt_id}: {stmt.text[:64]}")
        for line in section.skill_lines:
            print(f"            - {line.stmt_id}: {line.text[:70]}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LaTeX resume engine smoke test")
    parser.add_argument("tex", help="path to a .tex resume")
    parser.add_argument("-o", "--out", default=None, help="output directory")
    args = parser.parse_args(argv)

    tex_path = Path(args.tex)
    if not tex_path.exists():
        print(f"error: file not found: {tex_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else tex_path.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== Parsing {tex_path} ==")
    pr = parse_file(tex_path, resume_id="cli-demo")
    _print_structure(pr)

    # 1. No-op reconstruct must be byte-identical to the source.
    noop = apply_changes(pr.latex_source, {}, pr.stmt_index)
    identical = noop.latex == pr.latex_source
    print(f"== No-op reconstruct byte-identical: {identical} ==")

    # 2. Demo edit: rewrite the first editable statement, if any.
    editable = pr.doc.editable_statements()
    changes: dict[str, str] = {}
    if editable:
        target = editable[0]
        changes[target.stmt_id] = target.text + " [tailored to the job description]"
        print(f"== Demo edit on {target.stmt_id} ==")
    modified = reconstruct(pr, changes, layout=LayoutParams(margin_mm=15, line_spacing=1.05))

    modified_path = out_dir / "modified.tex"
    modified_path.write_text(modified.latex, encoding="utf-8")
    print(f"  wrote {modified_path} (applied={modified.applied}, rejected={modified.rejected})")

    # 3. Render and check one-page constraint.
    print("\n== Rendering ==")
    original_render = render_pdf(pr.latex_source)
    modified_check = check_one_page(modified.latex)

    if original_render.ok and original_render.pdf_bytes:
        (out_dir / "original.pdf").write_bytes(original_render.pdf_bytes)
        print(f"  original.pdf : {original_render.page_count} page(s)")
    else:
        print(f"  original render: {original_render.error or 'failed'}")

    if modified_check.pdf_bytes:
        (out_dir / "modified.pdf").write_bytes(modified_check.pdf_bytes)
    status = "estimated " if modified_check.estimated else ""
    print(
        f"  modified     : {modified_check.page_count} {status}page(s), "
        f"overflow={modified_check.overflow}"
    )
    if modified_check.overflow:
        print("  WARNING: resume overflows one page -- confirm would be blocked.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
