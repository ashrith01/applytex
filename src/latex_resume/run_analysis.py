"""Local optimization-run analytics for ApplyTeX ATS.

The app is still local-first, so each optimization call is recorded as one JSONL
row.  This gives us a lightweight feedback loop before introducing persistence:
which ATS gaps closed, which stayed open, and what the next highest-ROI fix is.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_RUN_LOG = Path("samples") / "out" / "optimization_runs.jsonl"


def ats_to_dict(ats: Any | None) -> dict[str, Any] | None:
    """Return a JSON-safe ATS dict from an ``ATSResult``-like object."""
    if ats is None:
        return None
    return {
        "score": ats.score,
        "raw_score": getattr(ats, "raw_score", ats.score),
        "submission_score": getattr(ats, "submission_score", ats.score),
        "score_mode": getattr(ats, "score_mode", "submission_fit"),
        "required_score": ats.required_score,
        "preferred_score": ats.preferred_score,
        "keyword_score": ats.keyword_score,
        "required_found": list(ats.required_found),
        "required_missing": list(ats.required_missing),
        "preferred_found": list(ats.preferred_found),
        "preferred_missing": list(ats.preferred_missing),
        "keyword_hits": list(ats.keyword_hits),
        "keyword_misses": list(ats.keyword_misses),
        "excluded_unconfirmed_skills": list(
            getattr(ats, "excluded_unconfirmed_skills", [])
        ),
        "submission_blockers": list(getattr(ats, "submission_blockers", [])),
    }


def build_run_record(
    result: Any,
    job_description: str,
    confirmed_skills: list[str] | None = None,
    resume_id: str | None = None,
    source: str = "streamlit",
    screening_analysis: dict[str, Any] | None = None,
    latency_ms: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Create one analytics record from an optimization result."""
    before = ats_to_dict(result.ats_before)
    after = ats_to_dict(result.ats_after)
    before_score = before["score"] if before else None
    after_score = after["score"] if after else None
    match_breakdown = (
        screening_analysis.get("match_breakdown")
        if isinstance(screening_analysis, dict)
        else None
    )

    return {
        "run_id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "resume_id": resume_id,
        "jd_hash": hashlib.sha256(job_description.encode("utf-8")).hexdigest()[:16],
        "jd_excerpt": _compact(job_description, 500),
        "confirmed_skills": list(confirmed_skills or []),
        "confirmation_required_skills": list(result.confirmation_required_skills),
        "optimization_strategy": getattr(result, "optimization_strategy", None),
        "reviewer_backend": getattr(result, "reviewer_backend", None),
        "trace_id": getattr(result, "trace_id", None),
        "model_routes": getattr(result, "model_routes", {}),
        "stage_latencies_ms": getattr(result, "stage_latencies_ms", {}),
        "ats_target_score": result.ats_target_score,
        "ats_target_met": result.ats_target_met,
        "score_before": before_score,
        "score_after": after_score,
        "score_delta": (
            round(after_score - before_score, 1)
            if before_score is not None and after_score is not None
            else None
        ),
        "ats_before": before,
        "ats_after": after,
        "newly_covered": _newly_covered(before, after),
        "remaining_gaps": _remaining_gaps(after),
        "excluded_unconfirmed_skills": (
            list(after.get("excluded_unconfirmed_skills", [])) if after else []
        ),
        "top_score_leaks": _top_score_leaks(after),
        "change_count": len(result.diff),
        "compacted_changes": list(getattr(result, "compacted_changes", [])),
        "pruned_changes": list(getattr(result, "pruned_changes", [])),
        "recruiter_feedback": list(getattr(result, "recruiter_feedback", [])),
        "recruiter_iteration_count": getattr(result, "recruiter_iteration_count", 0),
        "changes": [
            {
                "stmt_id": c.get("stmt_id"),
                "reason": c.get("reason", ""),
                "original": c.get("original", ""),
                "value": c.get("value", ""),
            }
            for c in result.diff
        ],
        "rejected_change_count": len(result.rejected_changes),
        "rejected_changes": [
            {
                "stmt_id": c.get("stmt_id"),
                "reason": c.get("rejection_reason", ""),
                "value": c.get("value", ""),
            }
            for c in result.rejected_changes
        ],
        "warnings": list(result.warnings),
        "page_count": result.page_count,
        "overflow": result.overflow,
        "visual_overflow": getattr(result, "visual_overflow", False),
        "min_text_baseline_pt": getattr(result, "min_text_baseline_pt", None),
        "screening_analysis": screening_analysis,
        "match_breakdown": match_breakdown,
        "latency_ms": latency_ms or {},
        "report_summary": _report_summary(result, before, after),
        "recommendations": build_recommendations(result),
    }


def _report_summary(
    result: Any,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    """Return a recruiter-readable summary of what the optimizer changed."""
    if not before or not after:
        return "Optimization completed, but ATS before/after details were unavailable."
    delta = round(after["score"] - before["score"], 1)
    if result.ats_target_met and not result.overflow:
        excluded = after.get("excluded_unconfirmed_skills", [])
        if excluded:
            return (
                f"Raised submission fit by {delta:+.1f} points while preserving a "
                "one-page resume. Some unconfirmed hard tools were kept as recruiter "
                "risk instead of being fabricated: " + ", ".join(excluded[:6]) + "."
            )
        return (
            f"Raised submission fit by {delta:+.1f} points while preserving a one-page resume. "
            "The edits emphasize supported JD wording and confirmed skills."
        )
    if result.confirmation_required_skills:
        return (
            "The optimizer improved supported wording, but the score is still gated "
            "by missing skills that need confirmation."
        )
    return (
        "The optimizer produced the strongest supported one-page rewrite it could, "
        "but the 80+ gate was not reached without unsupported claims."
    )


def build_recommendations(result: Any) -> list[str]:
    """Return concise next actions to improve ATS after this run."""
    after = ats_to_dict(result.ats_after)
    if not after:
        return ["Run ATS scoring before optimizing so gaps can be measured."]

    recommendations: list[str] = []

    if result.confirmation_required_skills:
        recommendations.append(
            "Confirm truthful skills before patching: "
            + ", ".join(result.confirmation_required_skills[:6])
        )

    required_missing = after["required_missing"]
    if required_missing:
        recommendations.append(
            "Highest ROI: close required-skill gaps: "
            + ", ".join(required_missing[:6])
        )

    keyword_misses = after["keyword_misses"]
    if keyword_misses:
        recommendations.append(
            "Weave truthful JD keywords into summary/bullets: "
            + ", ".join(keyword_misses[:6])
        )

    if result.rejected_changes:
        recommendations.append(
            "Some generated edits were not used because they were too long or unsupported: "
            + ", ".join(
                str(c.get("stmt_id", "unknown")) for c in result.rejected_changes[:5]
            )
        )

    if result.overflow:
        if getattr(result, "visual_overflow", False):
            recommendations.append(
                "Shorten editable content before submission; text is clipped at "
                "the bottom of the one-page PDF."
            )
        else:
            recommendations.append(
                "Shorten edits before submission; the optimized PDF exceeds one page."
            )

    if getattr(result, "compacted_changes", []):
        recommendations.append("One-page compaction was applied before finalizing edits.")

    if getattr(result, "pruned_changes", []):
        recommendations.append("Some low-impact edits were pruned to preserve the one-page limit.")

    if not recommendations and result.ats_target_met:
        recommendations.append(
            "Submission fit target met. Next improvement is recruiter-readability, not keyword stuffing."
        )
    elif not recommendations:
        recommendations.append(
            "Submission fit target not met; inspect remaining gaps and confirm only truthful additions."
        )

    return recommendations


def append_run_record(record: dict[str, Any], path: Path = DEFAULT_RUN_LOG) -> Path:
    """Append one JSONL run record and return the log path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_run_records(path: Path = DEFAULT_RUN_LOG, limit: int | None = None) -> list[dict[str, Any]]:
    """Load local run records, newest first."""
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    records.reverse()
    return records[:limit] if limit else records


def _newly_covered(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    if not before or not after:
        return []
    out: list[str] = []
    for missing_key, found_key in (
        ("required_missing", "required_found"),
        ("preferred_missing", "preferred_found"),
        ("keyword_misses", "keyword_hits"),
    ):
        out.extend(item for item in before[missing_key] if item in after[found_key])
    return list(dict.fromkeys(out))


def _remaining_gaps(after: dict[str, Any] | None) -> dict[str, list[str]]:
    if not after:
        return {"required": [], "preferred": [], "keywords": []}
    return {
        "required": list(after["required_missing"]),
        "preferred": list(after["preferred_missing"]),
        "keywords": list(after["keyword_misses"]),
    }


def _top_score_leaks(after: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not after:
        return []

    leaks: list[dict[str, Any]] = []
    groups = (
        ("required", "required_found", "required_missing", 60.0),
        ("preferred", "preferred_found", "preferred_missing", 25.0),
        ("keyword", "keyword_hits", "keyword_misses", 15.0),
    )
    for group, found_key, missing_key, weight in groups:
        total = len(after[found_key]) + len(after[missing_key])
        if total == 0:
            continue
        impact = round(weight / total, 1)
        for item in after[missing_key]:
            leaks.append({"item": item, "category": group, "estimated_points": impact})

    return sorted(leaks, key=lambda x: x["estimated_points"], reverse=True)


def _compact(text: str, max_chars: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rsplit(" ", 1)[0].rstrip() + " ..."
