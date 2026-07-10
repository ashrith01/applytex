"""ATS (Applicant Tracking System) keyword-match scorer.

Pure function — no LLM calls, no I/O.  Compares a resume's plain text
against the structured ``job_keywords`` dict produced by Stage 1 of the
optimizer and returns a weighted score breakdown.

Typical usage
-------------
::

    from latex_resume.ats import check_ats, ATSResult

    before: ATSResult = check_ats(resume_plain_text, job_keywords)
    after:  ATSResult = check_ats(modified_plain_text, job_keywords)
    print(f"Score delta: {after.score - before.score:+.1f} pts")

Scoring weights
---------------
* Required skills  — 60 %
* Preferred skills — 25 %
* JD keywords      — 15 %

Each category is scored 0–100 as ``100 * found / total``.  An empty
category scores 100 (nothing to miss).  The weighted sum gives the overall
0–100 ATS score.

Matching
--------
1. Both the skill and the resume text are *normalised*: lowercased, all
   punctuation except hyphens collapsed to spaces, internal whitespace
   collapsed.
2. For each skill, an alias table expands it to a set of search forms
   (e.g. ``"nlp"`` also matches ``"natural language processing"``).
3. Single-token search forms use a regex word-boundary match; multi-token
   forms use plain substring containment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

_W_REQUIRED:  float = 0.60
_W_PREFERRED: float = 0.25
_W_KEYWORDS:  float = 0.15

_HARD_UNCONFIRMED_SKILLS: frozenset[str] = frozenset(
    {
        "amazon bedrock",
        "bedrock",
        "aws bedrock",
        "vertex ai",
        "google vertex ai",
        "gcp vertex ai",
        "kubernetes",
        "golang",
        "go",
        "kotlin",
    }
)


# ---------------------------------------------------------------------------
# Alias / synonym table
# Each key is the *canonical* normalised form; values are additional forms
# that should also count as a match.
# ---------------------------------------------------------------------------

_RAW_ALIASES: dict[str, list[str]] = {
    "nlp": ["natural language processing"],
    "natural language processing": ["nlp"],
    "machine learning": ["ml", "ml dl", "ml/dl"],
    "llm": ["llms", "large language model", "large language models"],
    "llms": ["llm", "large language models", "large language model"],
    "rag": ["retrieval augmented generation", "retrieval-augmented generation"],
    "hugging face": ["huggingface"],
    "hugging face transformers": [
        "huggingface transformers", "hugging face", "huggingface",
    ],
    "langchain": ["lang chain"],
    "langgraph": ["lang graph"],
    "openai": ["open ai"],
    "openai api": ["openai api", "openai", "open ai api", "open ai"],
    "github": ["git hub"],
    "github copilot": ["copilot"],
    # "VS Code with GitHub Copilot or Codex or Claude Code" is how the JD phrases it;
    # split on any of the alternatives so the preferred skill can match.
    "vs code with github copilot or codex or claude code": [
        "vs code", "vscode", "github copilot", "claude code", "copilot", "codex",
    ],
    "vs code": ["vscode", "visual studio code"],
    "azure": ["microsoft azure", "ms azure"],
    "pytorch": ["torch"],
    "scikit-learn": ["scikit learn", "sklearn"],
    "ci/cd": ["cicd", "ci cd", "continuous integration continuous delivery"],
    "xai": ["explainable ai", "explainability", "model interpretability"],
    "agentic ai": ["agentic framework", "ai agents", "autonomous agents"],
    # JD keyword variants — map the exact JD phrase to its closest resume equivalent
    "agentic frameworks": [
        "agentic ai",
        "agentic framework",
        "ai agents",
        "autonomous agents",
        "crewai",
        "langgraph",
        "autogen",
    ],
    "modular and scalable ai agents": [
        "modular",
        "agentic ai",
        "agentic framework",
        "ai agents",
    ],
    "responsible ai": [
        "xai",
        "explainable ai",
        "model interpretability",
        "responsible ai practices",
        "responsible use of ai",
        "bias",
        "fairness",
    ],
    "ethical ai principles": [
        "xai",
        "explainable ai",
        "responsible ai",
        "ethical ai",
        "fairness",
        "accountability",
    ],
    "ai-first development approach": [
        "ai-powered",
        "ai powered",
        "ai-first",
        "ai first",
        "ai-driven",
        "llm-powered",
    ],
    "cloud-native environments": [
        "cloud-native",
        "cloud native",
        "aws sagemaker",
        "pinecone",
        "chromadb",
        "scalable pipelines",
    ],
    "cloud-native": ["cloud native", "aws sagemaker", "pinecone", "chromadb"],
    # Performance / scale / cost keywords (JD: "optimize for cost, latency, and scale")
    "cost optimization": ["optimize", "scalable", "efficient", "cost-effective", "reduction"],
    "latency reduction": ["latency", "performance", "optimize", "real-time", "fast"],
    "scale in high-volume consumer facing digital applications": [
        "enterprise-scale", "enterprise scale", "scalable", "production", "high-volume",
    ],
    "high-volume consumer facing": ["enterprise-scale", "scalable", "production"],
    # JD often writes "Language Models" meaning LLMs
    "language models": ["llms", "llm", "large language models", "large language model"],
    "large language models": ["llms", "llm", "language models"],
    # "Generative AI" is often shortened to "Gen AI"
    "generative ai": ["gen ai", "llms", "llm", "genai"],
    "gen ai": ["generative ai", "llms", "genai"],
    # "embeddings" → vector databases / RAG all use embeddings
    "embeddings": [
        "vector database",
        "vector databases",
        "pinecone",
        "chromadb",
        "rag",
        "retrieval augmented",
        "semantic search",
    ],
    "bedrock": ["amazon bedrock", "aws bedrock"],
    "amazon bedrock": ["bedrock", "aws bedrock"],
    "vertex ai": ["google vertex ai", "gcp vertex ai"],
    "ml pipelines": [
        "machine learning pipelines",
        "ml pipeline",
        "machine learning pipeline",
        "preprocessing pipelines",
        "model pipelines",
    ],
    "machine learning pipelines": ["ml pipelines", "ml pipeline"],
    "api development": [
        "fastapi",
        "postman",
        "api",
        "apis",
        "application programming interface",
        "application programming interfaces",
    ],
    "model deployment": [
        "production deployment",
        "full-scale production deployment",
        "deployment",
        "business-ready solutions",
    ],
    "model monitoring": [
        "mlflow",
        "model evaluation",
        "monitoring",
        "production model reviews",
        "model reviews",
    ],
    "inference services": [
        "model inference",
        "inference apis",
        "serving APIs",
        "serving apis",
        "model serving",
        "fastapi",
    ],
    "inference apis": ["inference services", "model inference", "model serving"],
    "communication skills": [
        "translating business data",
        "translating complex ai concepts",
        "business-ready solutions",
        "stakeholder",
        "stakeholders",
    ],
    "conversational workflows": [
        "conversational interfaces",
        "ai chatbot",
        "chatbot prototypes",
    ],
    "devops": [
        "production deployment",
        "enterprise-scale performance",
        "mlflow",
    ],
    "bias assessment": [
        "bias",
        "fairness",
        "xai",
        "model interpretability",
        "black-box model auditing",
    ],
    "auditability": [
        "model auditing",
        "black-box model auditing",
        "xai",
        "explainability",
        "interpretability",
    ],
    "governance": [
        "responsible ai",
        "model auditing",
        "production model reviews",
        "xai",
    ],
    "transparency": [
        "xai",
        "explainability",
        "interpretability",
        "model interpretability",
    ],
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation except hyphens, collapse whitespace."""
    text = text.lower()
    # keep word chars (letters, digits, _), spaces, hyphens
    text = re.sub(r"[^\w\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Pre-normalised alias lookup: norm_skill -> [norm_variant, ...]
def _build_alias_lookup() -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for key, aliases in _RAW_ALIASES.items():
        nkey = _normalise(key)
        forms = [nkey] + [_normalise(a) for a in aliases]
        # merge with any existing entry (two raw keys may normalise the same)
        existing = lookup.get(nkey, [])
        merged = list(dict.fromkeys(existing + forms))  # dedup, preserve order
        lookup[nkey] = merged
        for alias in aliases:
            nalias = _normalise(alias)
            ex2 = lookup.get(nalias, [])
            lookup[nalias] = list(dict.fromkeys(ex2 + merged))
    return lookup


_ALIAS_LOOKUP: dict[str, list[str]] = _build_alias_lookup()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _search_forms(
    skill: str,
    supported_equivalents: Mapping[str, list[str]] | None = None,
) -> list[str]:
    """Return all normalised search forms for *skill* (itself + aliases)."""
    norm = _normalise(skill)
    forms = _ALIAS_LOOKUP.get(norm, [norm])
    if supported_equivalents:
        extra = supported_equivalents.get(skill, supported_equivalents.get(norm, []))
        forms = list(forms) + [_normalise(str(item)) for item in extra]
    # Always include the normalised form itself in case alias lookup missed it
    return list(dict.fromkeys([norm] + forms))


def _skill_found(
    skill: str,
    norm_resume: str,
    supported_equivalents: Mapping[str, list[str]] | None = None,
) -> bool:
    """Return True if *skill* (or any alias) appears in the normalised resume.

    Handles two common JD phrasing patterns before alias lookup:

    1. **Parenthetical abbreviation** — e.g. ``"Natural Language Processing (NLP)"``
       Both the full phrase and the abbreviation inside the parens are checked.

    2. **"like X, Y, Z" enumeration** — e.g. ``"Agentic frameworks like LangGraph,
       AutoGen"`` ��� the leading noun phrase (``"Agentic frameworks"``) and each
       individual example (``"LangGraph"``, ``"AutoGen"``) are all checked.
    """
    all_forms: list[str] = list(_search_forms(skill, supported_equivalents))

    # ── Pattern 1: "Foo (BAR)" → also check "Foo" and "BAR" separately ──────
    paren_match = re.search(r"\(([^)]+)\)", skill)
    if paren_match:
        abbrev = paren_match.group(1).strip()
        base = re.sub(r"\s*\([^)]*\)", "", skill).strip()
        if base:
            all_forms += _search_forms(base, supported_equivalents)
        if abbrev:
            all_forms += _search_forms(abbrev, supported_equivalents)

    # ── Pattern 2: "Foo like A, B, C" → check "Foo", "A", "B", "C" ──────────
    like_match = re.match(r"^(.+?)\s+like\s+(.+)$", skill, re.IGNORECASE)
    if like_match:
        base_phrase = like_match.group(1).strip()
        examples_raw = like_match.group(2)
        all_forms += _search_forms(base_phrase, supported_equivalents)
        for example in re.split(r"[,/]| or ", examples_raw):
            example = example.strip()
            if example:
                all_forms += _search_forms(example, supported_equivalents)

    # ── De-duplicate while preserving order ──────────────────────────────────
    seen: set[str] = set()
    deduped: list[str] = []
    for f in all_forms:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    for form in deduped:
        tokens = form.split()
        if len(tokens) == 1:
            # Single-token: word-boundary match (avoids "python" inside "cpython")
            if re.search(r"\b" + re.escape(form) + r"\b", norm_resume):
                return True
        else:
            # Multi-token phrase: substring containment
            if form in norm_resume:
                return True
    return False


def _classify(
    skills: list[str],
    norm_resume: str,
    supported_equivalents: Mapping[str, list[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Split *skills* into (found, missing) against the normalised resume."""
    found: list[str] = []
    missing: list[str] = []
    for skill in skills:
        (found if _skill_found(skill, norm_resume, supported_equivalents) else missing).append(skill)
    return found, missing


def _is_hard_unconfirmed_skill(skill: str, confirmed_skills: set[str]) -> bool:
    """Return True when a missing hard tool should be excluded from submission score."""
    forms = {_normalise(skill), *_search_forms(skill)}
    if forms & confirmed_skills:
        return False
    return bool(forms & _HARD_UNCONFIRMED_SKILLS)


def _weighted_score(required_score: float, preferred_score: float, keyword_score: float) -> float:
    return (
        required_score * _W_REQUIRED
        + preferred_score * _W_PREFERRED
        + keyword_score * _W_KEYWORDS
    )


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ATSResult:
    """Keyword-match score for one resume snapshot against one job description.

    Fields
    ------
    score
        Weighted overall score, 0–100.
    required_score / preferred_score / keyword_score
        Per-category scores, 0–100.
    required_found / required_missing
        Required skills present / absent in the resume.
    preferred_found / preferred_missing
        Preferred skills present / absent in the resume.
    keyword_hits / keyword_misses
        JD domain keywords present / absent in the resume.
    """

    score: float = 0.0
    raw_score: float = 0.0
    submission_score: float = 0.0
    score_mode: str = "submission_fit"
    required_score:  float = 0.0
    preferred_score: float = 0.0
    keyword_score:   float = 0.0

    required_found:   list[str] = field(default_factory=list)
    required_missing: list[str] = field(default_factory=list)
    preferred_found:   list[str] = field(default_factory=list)
    preferred_missing: list[str] = field(default_factory=list)
    keyword_hits:  list[str] = field(default_factory=list)
    keyword_misses: list[str] = field(default_factory=list)
    excluded_unconfirmed_skills: list[str] = field(default_factory=list)
    submission_blockers: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Display helpers                                                      #
    # ------------------------------------------------------------------ #

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the ATS result."""
        lines = [f"Submission Fit Score: {self.score:.0f}/100"]
        if self.raw_score and self.raw_score != self.score:
            lines.append(f"  Raw debug score: {self.raw_score:.0f}/100")
        lines.append(
            f"  Required  ({self.required_score:.0f}%):  "
            f"{len(self.required_found)} found, {len(self.required_missing)} missing"
        )
        if self.required_missing:
            lines.append(f"    ✗ {', '.join(self.required_missing)}")
        if self.required_found:
            lines.append(f"    ✓ {', '.join(self.required_found)}")

        lines.append(
            f"  Preferred ({self.preferred_score:.0f}%):  "
            f"{len(self.preferred_found)} found, {len(self.preferred_missing)} missing"
        )
        if self.preferred_missing:
            lines.append(f"    ✗ {', '.join(self.preferred_missing)}")
        if self.preferred_found:
            lines.append(f"    ✓ {', '.join(self.preferred_found)}")

        lines.append(
            f"  Keywords  ({self.keyword_score:.0f}%):  "
            f"{len(self.keyword_hits)} hit, {len(self.keyword_misses)} missed"
        )
        if self.keyword_misses:
            lines.append(f"    ✗ {', '.join(self.keyword_misses)}")
        if self.keyword_hits:
            lines.append(f"    ✓ {', '.join(self.keyword_hits)}")

        if self.excluded_unconfirmed_skills:
            lines.append(
                "  Excluded unconfirmed hard skills: "
                + ", ".join(self.excluded_unconfirmed_skills)
            )

        return "\n".join(lines)

    def delta_summary(self, after: "ATSResult") -> str:
        """Return a one-line delta string comparing *self* (before) to *after*."""
        delta = after.score - self.score
        sign = "+" if delta >= 0 else ""
        lines = [
            f"Submission Fit Score: {self.score:.0f} → {after.score:.0f}  ({sign}{delta:.1f} pts)"
        ]

        # Newly found skills
        newly_found = [s for s in self.required_missing if s in after.required_found]
        newly_found += [s for s in self.preferred_missing if s in after.preferred_found]
        newly_found += [s for s in self.keyword_misses if s in after.keyword_hits]
        if newly_found:
            lines.append(f"  ✓ newly covered: {', '.join(newly_found)}")

        # Skills still missing
        still_missing = after.required_missing + after.preferred_missing
        if still_missing:
            lines.append(f"  ✗ still missing: {', '.join(still_missing)}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def check_ats(
    resume_text: str,
    job_keywords: dict[str, Any],
    *,
    confirmed_skills: list[str] | None = None,
    supported_equivalents: Mapping[str, list[str]] | None = None,
    score_mode: str = "submission_fit",
) -> ATSResult:
    """Score *resume_text* against *job_keywords* (from Stage 1 of the optimizer).

    Parameters
    ----------
    resume_text:
        Plain-text representation of the resume (LaTeX commands already
        stripped).  Produced by ``optimizer._build_plain_text``.
    job_keywords:
        Structured keywords dict as returned by ``extract_job_keywords``.
        Uses ``required_skills``, ``preferred_skills``, and ``keywords``
        keys; all other keys are ignored.

    Returns
    -------
    ATSResult
        Fully populated result with per-category scores and skill lists.
    """
    norm_resume = _normalise(resume_text)
    confirmed = {
        _normalise(skill)
        for skill in (confirmed_skills or [])
        if str(skill).strip()
    }

    required  = [str(s) for s in job_keywords.get("required_skills",  [])]
    preferred = [str(s) for s in job_keywords.get("preferred_skills", [])]
    keywords  = [str(s) for s in job_keywords.get("keywords", [])]

    raw_req_found,  raw_req_miss  = _classify(required,  norm_resume, supported_equivalents)
    raw_pref_found, raw_pref_miss = _classify(preferred, norm_resume, supported_equivalents)
    kw_hits,        kw_miss       = _classify(keywords,  norm_resume, supported_equivalents)

    raw_req_score  = 100.0 * len(raw_req_found)  / len(required)  if required  else 100.0
    raw_pref_score = 100.0 * len(raw_pref_found) / len(preferred) if preferred else 100.0
    kw_score   = 100.0 * len(kw_hits)   / len(keywords)  if keywords  else 100.0

    raw_overall = _weighted_score(raw_req_score, raw_pref_score, kw_score)

    excluded_required = [
        skill for skill in raw_req_miss
        if _is_hard_unconfirmed_skill(skill, confirmed)
    ]
    excluded_preferred = [
        skill for skill in raw_pref_miss
        if _is_hard_unconfirmed_skill(skill, confirmed)
    ]
    excluded = list(dict.fromkeys(excluded_required + excluded_preferred))

    if score_mode == "raw":
        req_found, req_miss = raw_req_found, raw_req_miss
        pref_found, pref_miss = raw_pref_found, raw_pref_miss
        req_denominator = len(required)
        pref_denominator = len(preferred)
    else:
        req_found = list(raw_req_found)
        req_miss = [s for s in raw_req_miss if s not in excluded_required]
        pref_found = list(raw_pref_found)
        pref_miss = [s for s in raw_pref_miss if s not in excluded_preferred]
        req_denominator = len(req_found) + len(req_miss)
        pref_denominator = len(pref_found) + len(pref_miss)

    req_score = 100.0 * len(req_found) / req_denominator if req_denominator else 100.0
    pref_score = 100.0 * len(pref_found) / pref_denominator if pref_denominator else 100.0
    submission_overall = _weighted_score(req_score, pref_score, kw_score)
    final_score = raw_overall if score_mode == "raw" else submission_overall
    blockers = [
        f"Unconfirmed hard skill excluded from score: {skill}"
        for skill in excluded
    ]

    return ATSResult(
        score=round(final_score, 1),
        raw_score=round(raw_overall, 1),
        submission_score=round(submission_overall, 1),
        score_mode=score_mode,
        required_score=round(req_score, 1),
        preferred_score=round(pref_score, 1),
        keyword_score=round(kw_score, 1),
        required_found=req_found,
        required_missing=req_miss,
        preferred_found=pref_found,
        preferred_missing=pref_miss,
        keyword_hits=kw_hits,
        keyword_misses=kw_miss,
        excluded_unconfirmed_skills=excluded,
        submission_blockers=blockers,
    )
