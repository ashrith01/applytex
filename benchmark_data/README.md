# SmartJobApply MVP Benchmark

This directory contains the reproducible, privacy-safe benchmark manifests and
synthetic fixtures.

Committed:

- `fixtures/resumes/`: 40 synthetic LaTeX resumes and evidence ledgers.
- `fixtures/jobs/`: 40 taxonomy-derived and 20 adversarial JDs.
- `manifests/`: versioned resume, JD, source, and selected-case records.
- `CHANGELOG.md`: required record of prompt, scoring, and corpus changes.

Not committed:

- `live_jds/`: current public employer postings retained locally for 90 days.
- `results/`: matrices, provider artifacts, CSV, JSON, and HTML reports.
- `reviews/private/`: blind A/B review packages.
- `taxonomy/raw/`: downloaded O*NET archives.

## Reproduce

```bash
uv sync
uv run python -m latex_resume.benchmark build-resumes
uv run python -m latex_resume.benchmark fetch-jds
uv run python -m latex_resume.benchmark score
uv run python -m latex_resume.benchmark select-cases

# Free deterministic baseline across all selected development and holdout cases
uv run python -m latex_resume.benchmark optimize \
  --providers deterministic --include-holdout

# Paid/authenticated model comparison
uv run python -m latex_resume.benchmark optimize --providers groq,codex

uv run python -m latex_resume.benchmark review
uv run python -m latex_resume.benchmark review \
  --ingest benchmark_data/reviews/review_queue.csv
uv run python -m latex_resume.benchmark report
```

The benchmark measures quality proxies. It must not be presented as proof that
an applicant will be shortlisted or hired.

Use `fetch-jds --download-onet` once to download the official O*NET 30.3 text
archive. If you have downloaded and extracted ESCO 1.2.1 CSV files separately,
pass their directory with `--esco-dir`; the generator will use preferred and
alternative skill labels without committing the raw export.
