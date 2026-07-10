"""High-level facade tying parse → extract → reconstruct → render together.

CLI usage
---------
Parse and extract editable JSON::

    uv run python -m latex_resume.engine resume.tex

Apply a changes file (JSON mapping stmt_id → new_text) and render::

    uv run python -m latex_resume.engine resume.tex --changes changes.json

The engine **never** modifies layout, spacing, or formatting.  Only the text
content of editable statements is touched, and only those char-spans listed in
the changes file.  ``LayoutParams`` is a separate opt-in used by the LLM
optimizer when overflow recovery is needed -- it is not applied here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from latex_resume.extractor import extract_full_resume
from latex_resume.models import ParseResult
from latex_resume.parser import parse
from latex_resume.reconstructor import ReconstructResult, apply_changes
from latex_resume.renderer import check_one_page, render_pdf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_file(path: str | Path, resume_id: str = "") -> ParseResult:
    """Parse a ``.tex`` file from disk into a :class:`ParseResult`."""
    text = Path(path).read_text(encoding="utf-8")
    return parse(text, resume_id=resume_id)


def extract_editable(parse_result: ParseResult) -> dict:
    """Return a clean, JSON-serialisable dict of every editable statement.

    Shape::

        {
          "resume_id": "...",
          "page_budget": {"estimated_word_count": 390, "estimated_bullet_count": 10, ...},
          "editable": {
            "summary":          {"summary_0": "AI/ML Engineer with..."},
            "work_experience":  [{"entry_id": "work_0", "header": "...",
                                  "bullets": {"work_0_0": "...", ...}}, ...],
            "projects":         [{"entry_id": "proj_0", "header": "...",
                                  "bullets": {"proj_0_0": "..."}}, ...],
            "skills":           {"skills_0": "Python, Django...", ...}
          }
        }

    This dict is the contract passed to the LLM optimizer (Increment 2).  The
    optimizer returns ``{stmt_id: new_text}`` which is fed directly to
    :func:`reconstruct`.
    """
    doc = parse_result.doc
    editable: dict = {}

    for section in doc.sections:
        if section.is_locked:
            continue
        stype = section.section_type.value

        if section.statements:
            # Summary-style: flat {stmt_id: text}
            editable[stype] = {stmt.stmt_id: stmt.text for stmt in section.statements}

        elif section.entries:
            # Experience / Projects: list of entry objects
            editable[stype] = [
                {
                    "entry_id": entry.entry_id,
                    "header": entry.header_text.strip(),
                    "bullets": {stmt.stmt_id: stmt.text for stmt in entry.statements},
                }
                for entry in section.entries
            ]

        elif section.skill_lines:
            # Skills: flat {stmt_id: text}
            editable[stype] = {line.stmt_id: line.text for line in section.skill_lines}

    return {
        "resume_id": doc.resume_id,
        "page_budget": doc.page_budget.model_dump(),
        "editable": editable,
    }


def reconstruct(
    parse_result: ParseResult,
    changes: dict[str, str],
) -> ReconstructResult:
    """Apply statement edits to a parsed resume.

    Only the char-spans of changed statements are touched; every other byte —
    ``\\resumeSubheading``, ``\\vspace``, margins, fonts, comments — is preserved
    verbatim.  Layout params are **not** applied here; pass the result to
    :func:`latex_resume.reconstructor.set_layout_params` only when overflow
    recovery is explicitly requested.
    """
    return apply_changes(parse_result.latex_source, changes, parse_result.stmt_index)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
        lock = "LOCKED" if section.is_locked else "edit  "
        flag = " (uncertain)" if section.classification_uncertain else ""
        print(f"  [{lock}] {section.section_type.value:16} {section.display_name}{flag}")
        for stmt in section.statements:
            print(f"            - {stmt.stmt_id}: {stmt.text[:70]}")
        for entry in section.entries:
            label = (entry.title or entry.header_text[:50]).replace("\n", " ")
            print(f"            * entry {entry.entry_id}: {label}")
            for stmt in entry.statements:
                print(f"                - {stmt.stmt_id}: {stmt.text[:64]}")
        for line in section.skill_lines:
            print(f"            - {line.stmt_id}: {line.text[:70]}")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LaTeX resume engine")
    ap.add_argument("tex", help="path to a .tex resume")
    ap.add_argument("-o", "--out", default=None, help="output directory (default: <tex_dir>/out)")
    ap.add_argument(
        "--changes",
        default=None,
        help="JSON file mapping stmt_id → new_text to apply",
    )
    ap.add_argument(
        "--no-render",
        action="store_true",
        help="skip PDF rendering (useful when pdflatex is unavailable)",
    )
    args = ap.parse_args(argv)

    tex_path = Path(args.tex)
    if not tex_path.exists():
        print(f"error: file not found: {tex_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else tex_path.parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Parse
    # ------------------------------------------------------------------
    print(f"\n== Parsing {tex_path} ==")
    pr = parse_file(tex_path, resume_id=tex_path.stem)
    _print_structure(pr)

    # ------------------------------------------------------------------
    # 2. Extract JSONs
    # ------------------------------------------------------------------
    # Full structured data (all sections, LaTeX stripped to plain text)
    full_data = extract_full_resume(pr)
    full_json_path = out_dir / "resume_data.json"
    full_json_path.write_text(
        json.dumps(full_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"== Full resume JSON → {full_json_path} ==")

    # Editable-only JSON (stmt_id → raw LaTeX text, for LLM optimizer)
    editable_data = extract_editable(pr)
    json_path = out_dir / "editable.json"
    json_path.write_text(json.dumps(editable_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"== Editable JSON   → {json_path} ==\n")

    # ------------------------------------------------------------------
    # 3. No-op round-trip sanity check
    # ------------------------------------------------------------------
    noop = apply_changes(pr.latex_source, {}, pr.stmt_index)
    identical = noop.latex == pr.latex_source
    print(f"== No-op reconstruct byte-identical: {identical} ==")
    if not identical:
        print("  ERROR: no-op reconstruct is not byte-identical — parser bug!", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # 4. Apply changes (if provided)
    # ------------------------------------------------------------------
    changes: dict[str, str] = {}
    if args.changes:
        changes_path = Path(args.changes)
        if not changes_path.exists():
            print(f"error: changes file not found: {changes_path}", file=sys.stderr)
            return 1
        changes = json.loads(changes_path.read_text(encoding="utf-8"))
        print(f"\n== Applying {len(changes)} change(s) from {changes_path} ==")

    result = reconstruct(pr, changes)
    if changes:
        print(f"  applied  : {result.applied}")
        if result.rejected:
            print(f"  rejected : {result.rejected}  ← locked or unknown stmt_ids")

    modified_tex_path = out_dir / "modified.tex"
    modified_tex_path.write_text(result.latex, encoding="utf-8")
    print(f"  wrote {modified_tex_path}")

    # ------------------------------------------------------------------
    # 5. Render + one-page check
    # ------------------------------------------------------------------
    if args.no_render:
        print("\n== Rendering skipped (--no-render) ==")
        return 0

    print("\n== Rendering ==")

    original_render = render_pdf(pr.latex_source)
    if original_render.ok and original_render.pdf_bytes:
        (out_dir / "original.pdf").write_bytes(original_render.pdf_bytes)
        print(f"  original.pdf : {original_render.page_count} page(s)  ✓")
    else:
        print(f"  original render failed: {original_render.error or 'unknown error'}")

    modified_check = check_one_page(result.latex)
    if modified_check.pdf_bytes:
        (out_dir / "modified.pdf").write_bytes(modified_check.pdf_bytes)

    est = " (estimated)" if modified_check.estimated else ""
    page_str = f"{modified_check.page_count} page(s){est}"

    if modified_check.overflow:
        print(f"  modified.pdf : {page_str}  ✗  OVERFLOW — changes not confirmable")
        print("  The edited resume exceeds one page.  Shorten bullet text or")
        print("  request layout adjustment via LayoutParams (opt-in, Increment 2).")
        return 2
    else:
        print(f"  modified.pdf : {page_str}  ✓")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
