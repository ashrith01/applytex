# LaTeX Resume Matcher

A LaTeX-native resume tailoring engine. It edits your `.tex` source **at the bullet
level** and recompiles to a one-page PDF, preserving every macro, package, and layout
choice in your original document.

Unlike the HTML-based approach (parse → JSON → re-render with a template), this engine
treats your `.tex` as the source of truth. It reads each bullet as a uniquely-addressed
statement, lets an LLM rewrite only the high-impact ones, then splices the new text back
into the exact character positions of the original source.

## Status

**Increment 1 — core engine (this repo).** No LLM, no HTTP API, no frontend yet.

Implemented:

- Parse a `.tex` resume → `LatexResumeDoc` JSON + a `stmt_index` (char-span map per bullet)
- Classify sections as editable (summary, experience, projects, skills) or locked
  (education, certifications, personal info)
- Apply edits by `stmt_id` and reconstruct the `.tex` via position-sorted splicing
  (every non-edited byte preserved)
- Inject layout params (`\geometry`, `\linespread`, font size) into a controlled
  preamble block
- Compile with `pdflatex` and enforce the one-page constraint (with a word-count fallback)

See [CLAUDE.md](./CLAUDE.md) for the full pipeline design and roadmap.

## Requirements

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A LaTeX engine (`pdflatex`) for PDF rendering — optional; the engine falls back to a
  word-count estimate when absent.

## Quick start

```bash
uv sync
uv run pytest                                            # run the test suite
uv run python -m latex_resume.engine samples/sample_resume.tex
```

The CLI prints the parsed structure (sections, statement IDs), verifies a no-op
reconstruct is byte-identical, applies a demo edit, and renders `original.pdf` +
`modified.pdf` into `samples/out/`.

## Layout

```
src/latex_resume/
├── models.py          # Pydantic schema (LatexResumeDoc, Statement, StmtSpan, ...)
├── parser.py          # .tex → LatexResumeDoc + stmt_index
├── reconstructor.py   # edits + layout params → new .tex (splice)
├── renderer.py        # pdflatex + page-count / one-page check
└── engine.py          # facade + CLI
samples/sample_resume.tex
tests/
```
