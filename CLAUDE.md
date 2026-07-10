# CLAUDE.md — LaTeX Resume Matcher

> Context file for Claude Code. This repo is a standalone sibling of `Resume-Matcher`.
> It builds a LaTeX-native resume tailoring engine. The full visual architecture
> reference lives in `../Resume-Matcher/latex-resume-architecture.html`.

---

## Project Purpose

Tailor a LaTeX resume to a job description by editing the `.tex` source directly,
at the individual bullet (`\item`) level, and recompiling to a **one-page** PDF.
The original formatting — every command, environment, package, and margin — is
preserved. Only the text content of selected statements changes.

This is the inverse philosophy of the HTML pipeline in `Resume-Matcher`, which
discards the original document and re-renders structured JSON through a template.

---

## The Pipeline (6 stages)

```
1. PARSE       .tex  ->  LatexResumeDoc (JSON) + stmt_index (char-span map)
2. CLASSIFY    each section -> editable | locked
3. OPTIMIZE    editable statements + JD  ->  LLM  ->  {stmt_id: new_text}   [Increment 2]
4. RECONSTRUCT splice new text into original .tex char spans (descending sort)
5. RENDER      pdflatex -> PDF, count pages
6. ENFORCE     page_count > 1  ->  block confirm / suggest layout changes
```

Stage 3 (LLM), the local HTTP layer, Streamlit MVP, model routing, tracing, and
benchmark harness are implemented for local development. SmartJobApply
persistence, authentication, and production approval workflows are not built yet.

---

## Core Invariants (do not break these)

1. **Splice preservation.** Reconstruction replaces only the character spans of
   *changed* statements. Statement spans cover *only the editable text content* —
   never the `\item` command or surrounding whitespace. A no-op reconstruct must be
   byte-identical to the input. (`reconstructor.apply_changes`, sorted descending by
   `tex_start` so earlier splices never shift later offsets.)

2. **Editable vs locked.** Only these section types are editable:
   `summary`, `work_experience`, `projects`, `skills`. These are permanently locked
   and never enter `stmt_index`: `education`, `certifications`, `publications`,
   `personal_info`, and any `unknown`-classified section. Locked statements are also
   rejected defensively in `apply_changes` / optimizer validation.

3. **One-page hard limit.** A tailored resume that compiles to more than one page must
   not be confirmable. `renderer.check_one_page` returns `overflow=True` (via pdflatex
   page count, or a word-count estimate when pdflatex is absent).

4. **The parser never mutates the source.** Layout-param injection is a separate,
   opt-in step (`reconstructor.set_layout_params`), so parsing is a pure read.

---

## Module Map

| File | Responsibility |
|------|----------------|
| `src/latex_resume/models.py` | Pydantic schema: `LatexResumeDoc`, `Section`, `Entry`, `Statement`, `SkillLine`, `StmtSpan`, `LayoutParams`, `PageBudget`, `ParseResult`. `SectionType` enum + editable/locked frozensets. |
| `src/latex_resume/parser.py` | `parse()` → `ParseResult`. Brace-aware section scan, keyword classification, `\item` span extraction, `stmt_id` assignment. |
| `src/latex_resume/reconstructor.py` | `apply_changes()` (splice), `set_layout_params()` (preamble block insert/replace). |
| `src/latex_resume/renderer.py` | `render_pdf()` (pdflatex subprocess + pypdf page count), `check_one_page()` (with word-count fallback). |
| `src/latex_resume/engine.py` | Facade (`parse_file`, `reconstruct`) + CLI smoke test. |
| `src/latex_resume/extractor.py` | Full read-only resume extraction with LaTeX stripped for display/ATS context. |
| `src/latex_resume/optimizer.py` | Experimental LLM optimization orchestration, validation, ATS before/after scoring. |
| `src/latex_resume/llm.py` | Wired JSON LLM backends: Groq, Anthropic, Ollama. OpenAI/Gemini are placeholders. |
| `src/latex_resume/ats.py` | Deterministic keyword/skill match scoring. |
| `src/latex_resume/session.py` | In-memory FastAPI session store. |
| `src/latex_resume/api.py` | Local FastAPI upload/optimize/status/rerender/delete routes. |

---

## Statement ID Scheme

```
summary_0              # summary text block
work_<entry>_<bullet>  # e.g. work_0_2  = 3rd bullet of 1st job
proj_<entry>_<bullet>  # e.g. proj_1_0
skills_<line>          # e.g. skills_0  = first skills line
```

`stmt_index[stmt_id] = StmtSpan(tex_start, tex_end, item_command, prefix_ws, original_text)`.
The span text content satisfies `latex_source[tex_start:tex_end] == original_text`.

---

## Conventions

- **All Python functions have type hints** (carried over from the parent project rule).
- Pydantic v2 models for all schema; plain dataclass only for `RenderResult` (carries `bytes`).
- Heuristic parsing, not a full LaTeX grammar — safe for shallow resume nesting.
- Keep the parser pure (no source mutation); all mutation lives in the reconstructor.

---

## Commands

```bash
uv sync                                                  # install deps
uv run pytest                                            # full suite (latex tests skip if no pdflatex)
uv run pytest tests/test_parser.py -q                    # one file
uv run python -m latex_resume.engine samples/sample_resume.tex   # CLI smoke test
```

LaTeX-dependent tests are marked and auto-skip when `pdflatex` is not on PATH.

---

## Roadmap

- **Increment 1 (done):** core engine — parse, classify, reconstruct, render, one-page check.
- **Increment 2 (MVP):** LLM optimization, skill confirmation, JD extraction,
  recruiter review, Streamlit UI, FastAPI routes, tracing, and benchmark tooling
  exist for local development. Remaining work: persistence, authentication,
  production hardening, durable approval states, and optional direct
  OpenAI/Gemini backend implementations.
- **Increment 3:** Next.js frontend — side-by-side PDF.js view, layout controls, SyncTeX
  hover-highlight overlay (green box per changed statement).

---

## Out of Scope (for now)

- Custom resume-template commands beyond `\resumeItem`, `\cventry`, `\cvevent`.
  Standard `\item`, `\resumeItem{...}`, and list environments (`itemize`,
  `enumerate`, `cvitems`, `highlights`) are parsed. Add new command grammars in
  `parser.py` as needed.
- PDF → LaTeX recovery (only `.tex` upload is supported in the engine core).
