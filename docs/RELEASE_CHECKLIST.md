# Public Release Checklist

## Repository

- [x] Apache 2.0 license selected and committed
- [ ] README reflects current behavior and limitations
- [ ] `.env`, private resumes, local JDs, and generated output are ignored
- [ ] No full live job-description snapshots are staged
- [ ] `uv.lock` is committed
- [ ] Repository contains no API keys or private contact details

## Verification

- [ ] `uv sync --locked` succeeds from a clean environment
- [ ] `uv run pytest` passes
- [ ] `uv build` succeeds
- [ ] Streamlit launches using public synthetic defaults
- [ ] Core CLI parses and renders `samples/sample_resume.tex`
- [ ] API `/health` responds locally

## Product Quality

- [ ] No-op reconstruction remains byte-identical
- [ ] Locked sections cannot be edited
- [ ] Unsupported hard claims and metrics are rejected
- [ ] Submission-ready PDFs are one visible page
- [ ] Fit-score documentation avoids promises of hiring outcomes

## GitHub

- [ ] CI is green
- [ ] Repository description and topics are configured
- [ ] A release tag is created after the default branch is green
- [ ] Optional UI screenshot or demo is added without personal data

## Rollback Triggers

Do not publish a release if:

- tests or package build fail;
- secrets or personal resume data are present;
- a generated resume can be marked ready while clipped or over one page;
- validation permits fabricated employers, degrees, certifications, or metrics.
