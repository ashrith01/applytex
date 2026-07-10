# Contributing

Thanks for helping improve ApplyTeX ATS.

## Setup

```bash
uv sync --locked
uv run pytest
```

Install `pdflatex` to run the render-dependent tests. Without it, those tests
skip and the renderer uses its explicit word-count fallback.

## Change Guidelines

- Add type hints to Python functions.
- Use Pydantic v2 models for structured contracts.
- Keep parser operations pure; source mutation belongs in the reconstructor.
- Preserve existing LaTeX commands, whitespace, and layout outside edited spans.
- Never make education, certifications, publications, personal information, or
  unknown sections editable.
- Treat LLM output as untrusted and validate it before reconstruction.
- Do not weaken metric, employer, degree, certification, or hard-skill checks to
  improve a score.
- Keep the final PDF to one visible page, including the bottom geometry check.

## Tests

Run the full suite:

```bash
uv run pytest
```

Useful focused commands:

```bash
uv run pytest tests/test_parser.py -q
uv run pytest tests/test_optimizer.py -q
uv run pytest tests/test_renderer.py -q
uv run pytest tests/test_benchmark.py -q
```

Add focused regression coverage for parser grammars, validation rules, scoring
changes, and overflow behavior.

## Data And Privacy

Do not commit:

- real resumes or personal contact information;
- API keys or `.env`;
- generated PDFs and run artifacts;
- full text from live employer job postings;
- private human-review files.

Synthetic fixtures must use fictional identities and an evidence ledger.

## Pull Requests

Describe:

- the user-visible behavior changed;
- invariants and risks affected;
- tests run;
- model/provider impact, if any;
- benchmark impact, if scoring or prompts changed.

Prompt, scoring, or benchmark-corpus changes must also update
`benchmark_data/CHANGELOG.md`.
