# Portfolio And Interview Guide

## Short Project Description

ApplyTeX ATS is an evidence-grounded AI resume optimization system for LaTeX.
It parses resumes into exact editable spans, matches them against job
descriptions, routes structured tasks across multiple LLM backends, validates
generated claims, and recompiles a visually verified one-page PDF.

## Suggested Resume Bullets

Use only the bullets that match the version you can demonstrate:

- Built an evidence-grounded resume optimization pipeline using Python,
  Pydantic, FastAPI, Streamlit, LangChain, LangSmith, and multiple LLM backends,
  preserving LaTeX formatting through statement-level character-span edits.
- Designed anti-fabrication validation for skills, employers, education,
  certifications, metrics, and domain claims, with recruiter-style feedback and
  strict one-page PDF geometry enforcement.
- Developed a reproducible evaluation corpus with 40 synthetic resumes, 120 job
  descriptions, 4,800 deterministic resume-job pairs, adversarial fixtures, and
  holdout-based provider comparison.

## Interview Talking Points

### Why LaTeX-Native?

Template-preserving character-span replacement avoids the layout drift caused
by parsing a resume into generic JSON and rendering it through a new template.

### How Is Hallucination Controlled?

The LLM proposes changes but does not own the final document. Pydantic contracts,
locked section IDs, supported-equivalence rules, metric checks, claim-drift
checks, and compilation gates decide whether a change is accepted.

### Why Is Page Count Not Enough?

A PDF may report one page while placing text below the page boundary. The
renderer also examines text baselines and blocks clipped output.

### How Is It Evaluated?

The benchmark separates optimizer score from independent checks such as
evidence preservation, unsupported claims, contextual keyword coverage,
one-page compliance, latency, provider failure rate, and blind human review.

### What Would Production Require?

Persistent encrypted storage, authentication, isolated LaTeX compilation,
provider privacy controls, background jobs, rate limiting, audit logs, and
calibration against real application outcomes.

## Honest Positioning

Say that the project estimates resume-job fit and validates optimization
quality. Do not claim that it reverse-engineers commercial ATS products or
guarantees interviews.
