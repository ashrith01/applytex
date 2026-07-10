"""Independent evidence and rewrite-quality audits for benchmark outputs."""

from __future__ import annotations

import re
from typing import Any, Iterable

from latex_resume.ats import _normalise, _skill_found
from latex_resume.benchmark.models import EvidenceLedger, JobFixture
from latex_resume.extractor import extract_full_resume
from latex_resume.optimizer import _build_plain_text
from latex_resume.parser import parse

METRIC_RE = re.compile(
    r"(?<![\w])(?:\$?\d+(?:\.\d+)?(?:\\?%|\+)?|sub-\d+\s*ms)(?![\w])",
    re.IGNORECASE,
)


def audit_optimized_resume(
    original_latex: str,
    modified_latex: str,
    job: JobFixture,
    ledger: EvidenceLedger,
    diff: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return independent truthfulness and contextual-coverage measurements."""
    original_plain = _plain_text(original_latex, ledger.resume_id)
    modified_plain = _plain_text(modified_latex, ledger.resume_id)
    original_norm = _normalise(original_plain)
    modified_norm = _normalise(modified_plain)

    unsupported: list[str] = []
    for skill in list(job.required_skills) + list(job.preferred_skills):
        newly_present = (
            _skill_found(skill, modified_norm)
            and not _skill_found(skill, original_norm)
        )
        if newly_present and not _ledger_supports(skill, ledger):
            unsupported.append(skill)

    original_metrics = {_clean_metric(item) for item in METRIC_RE.findall(original_plain)}
    modified_metrics = {_clean_metric(item) for item in METRIC_RE.findall(modified_plain)}
    introduced_metrics = sorted(modified_metrics - original_metrics)
    evidence_items = (
        ledger.skills
        + ledger.employers
        + ledger.education
        + ledger.certifications
        + [_clean_metric(item) for item in ledger.metrics]
    )
    evidence_preserved = sum(
        1
        for item in evidence_items
        if _normalise(item) in modified_norm or _skill_found(item, modified_norm)
    )
    preservation_score = (
        evidence_preserved / len(evidence_items)
        if evidence_items
        else 1.0
    )
    contextual, standalone = _keyword_coverage(modified_latex, job)
    semantic_similarity = _diff_similarity(diff)
    return {
        "unsupported_claims": sorted(dict.fromkeys(unsupported)),
        "introduced_metrics": introduced_metrics,
        "evidence_preservation_score": round(preservation_score, 4),
        "contextual_keyword_coverage": round(contextual, 4),
        "standalone_keyword_coverage": round(standalone, 4),
        "semantic_similarity": round(semantic_similarity, 4),
    }


def _keyword_coverage(latex: str, job: JobFixture) -> tuple[float, float]:
    parsed = parse(latex, resume_id="audit")
    prose: list[str] = []
    skills: list[str] = []
    for stmt_id, span in parsed.stmt_index.items():
        if stmt_id.startswith("skills_"):
            skills.append(span.original_text)
        else:
            prose.append(span.original_text)
    prose_norm = _normalise(" ".join(prose))
    skills_norm = _normalise(" ".join(skills))
    phrases = list(dict.fromkeys(
        list(job.required_skills) + list(job.preferred_skills) + list(job.keywords)
    ))
    if not phrases:
        return 1.0, 1.0
    contextual = sum(_skill_found(item, prose_norm) for item in phrases) / len(phrases)
    standalone = sum(_skill_found(item, skills_norm) for item in phrases) / len(phrases)
    return contextual, standalone


def _diff_similarity(diff: Iterable[dict[str, Any]]) -> float:
    scores: list[float] = []
    for change in diff:
        original = set(_normalise(str(change.get("original", ""))).split())
        value = set(_normalise(str(change.get("value", ""))).split())
        if not original and not value:
            scores.append(1.0)
            continue
        union = original | value
        scores.append(len(original & value) / len(union) if union else 1.0)
    return sum(scores) / len(scores) if scores else 1.0


def _ledger_supports(skill: str, ledger: EvidenceLedger) -> bool:
    evidence = _normalise(
        " ".join(ledger.skills + ledger.allowed_claims + ledger.domains)
    )
    return _skill_found(skill, evidence, ledger.supported_equivalents)


def _plain_text(latex: str, resume_id: str) -> str:
    return _build_plain_text(extract_full_resume(parse(latex, resume_id=resume_id)))


def _clean_metric(metric: str) -> str:
    return metric.lower().replace("\\", "").replace(" ", "")
