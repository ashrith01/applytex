# ATS And AI Screening Research Notes

_Last updated: 2026-06-06_

## What Modern ATS/AI Screening Appears To Do

There is no universal ATS score or universal shortlist threshold. Enterprise
systems increasingly look like calibrated matching tools: they parse resumes,
extract entities, compare candidates to job-specific criteria, show matched and
missing skills, and leave final decisions to recruiters or hiring teams.

Key patterns to model in ApplyTeX ATS:

- **Criteria calibration.** Greenhouse Talent Matching compares applications
  against user-defined criteria, uses profile fields like skills, job title,
  company, dates, education, and employment gaps, and explicitly says recruiters
  should review results rather than auto-reject solely from matching output.
- **Matched/missing skill explanations.** Greenhouse exposes matched and missing
  skills and highlights exact and similar terms, which is more useful than a
  single opaque number.
- **Must-have vs nice-to-have criteria.** Lever's AI Screened integration uses
  must-have and nice-to-have criteria. Its docs label candidates with 75%+ of
  criteria matched as "Strong Fit", but that is an integration threshold, not a
  universal ATS rule.
- **Exact/similar keyword scoring.** ATS keyword tools often score exact keyword
  matches separately from similar matches. ATS Guide says exact matches receive
  100% and similar matches receive a partial score set by the user.
- **Human override and uncertainty.** Enterprise tools include manual review
  concepts. ApplyTeX ATS should similarly use `Needs Manual Review` when
  parser confidence, truth risk, or unsupported gaps are high.

## Research Risks To Respect

Recent algorithmic hiring research highlights risks that matter for product
design:

- **Algorithmic monoculture.** If many employers use similar screening models,
  the same candidate can be rejected repeatedly for hidden, systematic reasons.
- **Bias and proxy features.** Resume text, job history, education, and inferred
  signals can encode protected-class proxies even when a system claims to score
  only job-relevant information.
- **Retrieval and ranking sensitivity.** Modern screening can resemble
  information retrieval: phrasing, keyword coverage, and semantic similarity can
  affect which candidates are surfaced.

Product implication: ApplyTeX ATS should optimize truthfully, explain every
edit, and diversify application strategy instead of promising that any score
guarantees shortlisting.

## Useful Open-Source Concepts

Representative open-source projects in this space tend to use these ideas:

- Resume/JD keyword matching and missing keyword displays.
- Semantic similarity scoring with embeddings.
- Gap analysis by required/preferred skills.
- Resume tailoring with explicit before/after diffs.
- Provenance or evidence tracking so generated edits can be defended.
- Local-first or self-hosted LLM flows for privacy.

Concepts worth adopting:

- Store every run's before/after score, gaps, rejected edits, and latency.
- Explain why each change was proposed.
- Rank edits by expected score lift.
- Treat unsupported JD skills as confirmation prompts, not automatic additions.
- Keep a separate recruiter-readability guardrail so the resume does not become
  keyword-stuffed.

## ApplyTeX ATS Defaults

- Keep `80+` as an internal target for online applications.
- Treat `75%+` as a useful strong-fit reference because at least one enterprise
  integration exposes it, but do not claim it is universal.
- Prefer this match category mapping:
  - `Strong Fit`: score >= 80 and truth risk is not high.
  - `Good Fit`: score >= 70.
  - `Partial Fit`: score >= 50.
  - `Limited Fit`: score < 50.
  - `Needs Manual Review`: high truth risk or parser risk.
- Never invent skills, tools, industries, dates, metrics, degrees,
  certifications, companies, titles, or domain experience.

## Sources

- Greenhouse Talent Matching FAQ:
  https://support.greenhouse.io/hc/en-us/articles/41131886674075-Talent-Matching-FAQ
- Greenhouse Talent Matching data processing:
  https://support.greenhouse.io/hc/en-us/articles/44504950876315-Talent-Matching-Data-Processing-FAQ
- Lever AI Screened integration:
  https://help.lever.co/hc/en-us/articles/21614057853341-Enabling-and-using-the-AI-Screened-integration
- ATS Guide keyword comparison:
  https://ats-guide.zendesk.com/hc/en-us/articles/15340416612503-Job-Keywords-and-Keyword-Comparison-Match
- Algorithmic hiring monoculture research:
  https://algorithmichiring.github.io/paper.pdf
- Stanford HAI summary:
  https://hai.stanford.edu/news/ai-hiring-tools-can-yield-racial-bias-and-systemic-rejection
- AI hiring bias literature:
  https://arxiv.org/abs/2407.20371
  https://arxiv.org/abs/2605.27371
