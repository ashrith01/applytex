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

### Resume engine (the six-stage core)

| File | Responsibility |
|------|----------------|
| `src/latex_resume/models.py` | Pydantic schema: `LatexResumeDoc`, `Section`, `Entry`, `Statement`, `SkillLine`, `StmtSpan`, `LayoutParams`, `PageBudget`, `ParseResult`. `SectionType` enum + editable/locked frozensets. |
| `src/latex_resume/parser.py` | `parse()` → `ParseResult`. Brace-aware section scan, keyword classification, `\item` span extraction, `stmt_id` assignment. |
| `src/latex_resume/reconstructor.py` | `apply_changes()` (splice), `set_layout_params()` (preamble block insert/replace). |
| `src/latex_resume/renderer.py` | `render_pdf()` (pdflatex subprocess + pypdf page count), `check_one_page()` (page count + lowest-baseline geometry, word-count fallback). |
| `src/latex_resume/engine.py` | Facade (`parse_file`, `reconstruct`) + CLI smoke test. |
| `src/latex_resume/extractor.py` | Full read-only resume extraction with LaTeX stripped for display/ATS context. |
| `src/latex_resume/optimizer.py` | LLM optimization orchestration, reviewer loop, overflow repair, ATS before/after scoring. |
| `src/latex_resume/change_validation.py` | Truthfulness / claim-drift gates applied to every proposed statement edit. |
| `src/latex_resume/ats.py`, `screening.py` | Deterministic keyword/skill fit scoring and five-category recruiter-style analysis. |
| `src/latex_resume/llm.py`, `llm_routing.py` | JSON LLM backends (Groq, Anthropic, Ollama, Codex SDK; OpenAI for answer drafting only; Gemini placeholder) and per-stage routing. |

### HTTP layer

| File | Responsibility |
|------|----------------|
| `src/latex_resume/api.py` | App factory (`create_app`), lifespan, rate limiter, and the shared request/response Pydantic models + helpers the routers import. Still large; schemas/services extraction is pending. |
| `src/latex_resume/routers/` | Route modules: `latex` (classic upload/optimize), `tailor` (guided sessions), `profiles`, `jobs`, `applications`, `extension`, `auth`. `_deps.py` holds profile-ownership dependencies (`require_*_for_profile` → 404, never 403). |
| `src/latex_resume/session.py` | **In-memory** store for classic `/latex/*` sessions (lost on restart). |
| `src/latex_resume/tailor_store.py` | SQLite-backed tailor sessions. |
| `src/latex_resume/local_auth.py` | Optional bearer-token auth (`APPLYTEX_REQUIRE_AUTH=1`), scrypt passwords, in-process token dict. |
| `src/latex_resume/logging_config.py` | structlog setup (console or JSON). |

### Job application platform

| File | Responsibility |
|------|----------------|
| `src/latex_resume/job_models.py` | Domain contracts: `JobPosting`, `CandidateProfile`, `FormQuestion`, `FillAction`, `ApplicationRecord`, and `ALLOWED_APPLICATION_TRANSITIONS` state machine. |
| `src/latex_resume/application_store.py` | SQLite persistence for searches, jobs, applications, artifacts, events, tasks, profiles, form scans, tailor sessions, settings. |
| `src/latex_resume/job_sources.py`, `job_matching.py` | Public Greenhouse/Lever/Ashby board adapters; role/location preference matching. |
| `src/latex_resume/form_resolution.py`, `option_matching.py` | Deterministic answer resolution for scanned forms. Profile facts first, then remembered `SavedAnswer` rows (exact prompt → alias → intent → token match); unknown → `skip` with `answer_source="user_input"`; select options matched exact/alias only. `remember_answer()` writes a reviewed override back as a typed profile fact (age, relocation, travel, non-compete, authorization, sponsorship) or an answers-bank row. |
| `src/latex_resume/answer_proposals.py` | Review-gated LLM suggestions for *short* required questions the resolver skipped. Cites a saved fact per proposal, validates against offered options, never covers authorization / sponsorship / compensation / EEO / narrative. Nothing is filled until the user confirms. |
| `src/latex_resume/application_answers.py` | Grounded ≤100-word drafts for narrative screening questions with a claim validator. |
| `src/latex_resume/profile_extraction.py`, `project_library.py`, `project_scoring.py` | Resume → profile prefill; project library and per-job project ranking. |
| `extension/` | MV3 Chrome extension. `panel.js` (scan / plan / fill / review UI, large IIFE), `panel-profile.js`, `providers.js` (13 host adapters), `background.js` (API proxy to `127.0.0.1:8000`). Never clicks the final Submit button. |
| `frontend/` | Next.js 15 UI: profile, jobs, applications kanban, guided tailor, Resume Lab. |
| `src/latex_resume/streamlit_app.py` | Legacy Streamlit UI. Kept intentionally for now. |
| `scripts/autofill_lab.py` + `scripts/autofill_lab_qa.mjs` | Synthetic application lab (real API + real panel scripts, fictional profile) and its Playwright QA runner. |

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
uv run applytex-api                                      # FastAPI on :8000
cd frontend && npm run typecheck && npm run lint         # frontend gates (CI runs these)
uv run python scripts/autofill_lab.py                    # synthetic lab on :8765, then:
node scripts/autofill_lab_qa.mjs                         # Playwright QA (needs frontend/node_modules)
```

LaTeX-dependent tests are marked and auto-skip when `pdflatex` is not on PATH.
`pytest` `pythonpath` is `["src", "."]` — the `.` is required so tests can
import `scripts.autofill_lab`.

---

## Roadmap

- **Increment 1 (done):** core engine — parse, classify, reconstruct, render, one-page check.
- **Increment 2 (MVP):** LLM optimization, skill confirmation, JD extraction,
  recruiter review, Streamlit UI, FastAPI routes, tracing, and benchmark tooling
  exist for local development. Remaining work: persistence, authentication,
  production hardening, durable approval states, and optional direct
  OpenAI/Gemini backend implementations.
- **Increment 3 (done):** Next.js frontend — side-by-side PDF.js view, layout controls,
  SyncTeX hover-highlight overlay (green box per changed statement).
- **Increment 4 (in progress):** job application platform — extension capture/scan/
  reviewed fill, SQLite application tracker, profile-driven answers. Next phases are
  in `docs/TSENTA_PARITY_PLAN.md`: answers bank + review-gated resolver, curated
  watchlist feed, submission receipt, local review-gated executor.

---

## Out of Scope (for now)

- Custom resume-template commands beyond `\resumeItem`, `\cventry`, `\cvevent`.
  Standard `\item`, `\resumeItem{...}`, and list environments (`itemize`,
  `enumerate`, `cvitems`, `highlights`) are parsed. Add new command grammars in
  `parser.py` as needed.
- PDF → LaTeX recovery (only `.tex` upload is supported in the engine core).
