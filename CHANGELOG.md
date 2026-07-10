# Changelog

All notable changes to ApplyTeX ATS will be documented here.

## Unreleased

### Added

- Next.js web UI with dashboard, profile setup, guided 3-step tailor, jobs, applications, and Resume Lab.
- FastAPI endpoints for analyze, refine, tailor sessions, active profile, and job lookup.

### Changed

- Streamlit UI deprecated in favor of the Next.js frontend (legacy Streamlit remains for one release).
- Chrome extension opens the web tailor flow at `localhost:3000` instead of Streamlit.

### Added (prior)

- Streamlit MVP for ATS analysis, truthful skill confirmation, optimization
  reporting, and PDF preview.
- Groq, Anthropic, Ollama, and Codex SDK model routes.
- Recruiter-style review loop and LangSmith tracing.
- Evidence-grounded submission-fit scoring and match breakdown.
- Synthetic benchmark corpus, provider runner, audit, and reporting tools.
- Visual PDF overflow detection and strict one-page compaction.
- Public Greenhouse, Lever, and Ashby job-board adapters.
- Local SQLite storage for searches, normalized jobs, and application states.
- Human-approved application state machine that blocks unsafe submission jumps.
- Persistent internship-focused search preferences and work-authorization facts.
- Read-only Chrome extension for job capture and application-form scanning.
- Deterministic form answer resolution that leaves sensitive or unknown fields
  for explicit user input.
- Reviewed Chrome form filling with final submission permanently left to the
  user.

### Changed

- Expanded the original LaTeX parser/reconstructor into an end-to-end
  ApplyTeX ATS optimization MVP.
- Updated active Codex defaults to current recommended models.
