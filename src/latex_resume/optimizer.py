"""LLM optimization pipeline for the LaTeX resume matcher.

Stages
------
1. ``extract_job_keywords``      — JD → structured skills/keywords JSON
2. ``generate_skill_target_plan`` — resume + JD keywords → target skill list
3. ``verify_skill_target_plan``  — pure validation (no LLM call)
4. ``generate_latex_diffs``      — target skills + editable JSON → change list
5. ``validate_changes``          — 4-gate pure validation of each change
6. ``run_optimization_pipeline`` — orchestrates all stages end-to-end

``OptimizationResult`` holds all intermediate artifacts for the API layer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from latex_resume.ats import ATSResult, check_ats
from latex_resume.change_validation import (
    _escape_latex_specials,
    _fabricated_metrics,
    _prepare_raw_changes_for_validation,
    _preserve_existing_textbf,
    _strip_item_wrapper,
    _unsupported_claim_drifts,
    validate_changes,
)
from latex_resume.engine import extract_editable, reconstruct
from latex_resume.extractor import extract_full_resume
from latex_resume.job_keywords import extract_job_keywords_fast
from latex_resume.llm import _sanitize_user_input, complete_json
from latex_resume.llm_routing import LLMTaskRoute, LLMTaskRoutes, route_for_task
from latex_resume.models import PageBudget, ParseResult
from latex_resume.naturalness import (
    NATURALNESS_THRESHOLD,
    naturalness_check,
    refine_bullet,
)
from latex_resume.parser import parse as _reparse
from latex_resume.prompts import (
    CHAT_REFINE_PROMPT,
    COMPACT_GENERATE_LATEX_DIFFS_PROMPT,
    COMPACT_REWRITE_PROMPT,
    EXTRACT_KEYWORDS_PROMPT,
    RECRUITER_REVIEW_PROMPT,
    REPAIR_CONTEXT_DRIFT_PROMPT,
)
from latex_resume.renderer import RenderResult, check_one_page
from latex_resume.skill_planning import (
    generate_skill_target_plan,
    generate_skill_target_plan_fast,
    verify_skill_target_plan,
)
from latex_resume.tracing import PipelineTracer, compact_text as trace_compact_text
from latex_resume.tracing import hash_text as trace_hash_text

logger = logging.getLogger(__name__)

ATS_TARGET_SCORE = 80.0
OptimizerStrategy = Literal[
    "supported_inference_ats_80_one_page",
    "conservative",
    "ats_aggressive",
    "recruiter_readable",
    "one_page_strict",
]
ReviewerBackend = Literal["custom", "langchain"]
DEFAULT_OPTIMIZER_STRATEGY: OptimizerStrategy = "supported_inference_ats_80_one_page"
DEFAULT_REVIEWER_BACKEND: ReviewerBackend = os.environ.get(
    "SMARTJOBAPPLY_REVIEWER_BACKEND",
    "custom",
).lower()  # type: ignore[assignment]
OPTIMIZER_STRATEGY_LABELS: dict[OptimizerStrategy, str] = {
    "supported_inference_ats_80_one_page": "Supported inference",
    "conservative": "Conservative",
    "ats_aggressive": "ATS aggressive",
    "recruiter_readable": "Recruiter-readable",
    "one_page_strict": "One-page strict",
}
AUTO_PATCH_UNCONFIRMED_SKILLS = (
    os.environ.get("SMARTJOBAPPLY_AUTO_PATCH_UNCONFIRMED_SKILLS", "false").lower()
    in {"1", "true", "yes"}
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class OptimizationResult:
    """All artifacts produced by ``run_optimization_pipeline``."""

    # Intermediate LLM outputs
    job_keywords: dict[str, Any] = field(default_factory=dict)
    skill_target_plan: dict[str, Any] = field(default_factory=dict)
    raw_changes: list[dict[str, Any]] = field(default_factory=list)
    strategy_notes: str = ""
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY
    compacted_changes: list[dict[str, Any]] = field(default_factory=list)
    pruned_changes: list[str] = field(default_factory=list)
    recruiter_feedback: list[str] = field(default_factory=list)
    recruiter_iteration_count: int = 0
    reviewer_backend: ReviewerBackend = DEFAULT_REVIEWER_BACKEND

    # Validated changes ready to apply
    validated_changes: dict[str, str] = field(default_factory=dict)
    rejected_changes: list[dict[str, Any]] = field(default_factory=list)

    # Reconstruction output
    modified_latex: str = ""
    overflow: bool = False
    visual_overflow: bool = False
    min_text_baseline_pt: float | None = None
    page_count: int = 0
    pdf_bytes: bytes | None = None

    # Human-readable diff (stmt_id → {original, value, reason})
    diff: list[dict[str, Any]] = field(default_factory=list)

    # ATS keyword-match scores (before and after applying changes)
    ats_before: ATSResult | None = None
    ats_after:  ATSResult | None = None
    ats_target_score: float = ATS_TARGET_SCORE
    ats_target_met: bool = False
    confirmed_skills: list[str] = field(default_factory=list)
    confirmation_required_skills: list[str] = field(default_factory=list)
    model_routes: dict[str, dict[str, str | None]] = field(default_factory=dict)
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    trace_id: str | None = None

    # Errors encountered (non-fatal; collected rather than raised)
    warnings: list[str] = field(default_factory=list)


def _route_for_task(routes: LLMTaskRoutes | None, task: str) -> LLMTaskRoute:
    """Compatibility wrapper for older optimizer imports/tests."""
    return route_for_task(routes, task)


def _routes_to_dict(routes: LLMTaskRoutes | None) -> dict[str, dict[str, str | None]]:
    """Return a JSON-safe summary of per-task model routes."""
    return {
        task: {"backend": route.backend, "model": route.model}
        for task, route in (routes or {}).items()
    }


def _filter_editable_json_by_stmt_ids(
    editable_json: dict[str, Any],
    allowed_stmt_ids: set[str] | None,
) -> dict[str, Any]:
    """Return editable JSON containing only allowed statement IDs."""
    if allowed_stmt_ids is None:
        return editable_json

    scoped: dict[str, Any] = {}
    for section_name, section_value in editable_json.items():
        if isinstance(section_value, dict):
            kept = {
                stmt_id: text
                for stmt_id, text in section_value.items()
                if stmt_id in allowed_stmt_ids
            }
            if kept:
                scoped[section_name] = kept
            continue

        if isinstance(section_value, list):
            entries: list[dict[str, Any]] = []
            for entry in section_value:
                if not isinstance(entry, dict):
                    continue
                bullets = entry.get("bullets", {})
                if not isinstance(bullets, dict):
                    continue
                kept_bullets = {
                    stmt_id: text
                    for stmt_id, text in bullets.items()
                    if stmt_id in allowed_stmt_ids
                }
                if kept_bullets:
                    entries.append({**entry, "bullets": kept_bullets})
            if entries:
                scoped[section_name] = entries
    return scoped


def _filter_changes_by_stmt_ids(
    changes: list[dict[str, Any]],
    allowed_stmt_ids: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split accepted/rejected changes by the selected editable scope."""
    if allowed_stmt_ids is None:
        return changes, []

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for change in changes:
        stmt_id = change.get("stmt_id")
        if isinstance(stmt_id, str) and stmt_id in allowed_stmt_ids:
            accepted.append(change)
        else:
            rejected.append(
                {
                    **change,
                    "rejection_reason": (
                        f"stmt_id '{stmt_id}' is outside the selected edit scope"
                    ),
                }
            )
    return accepted, rejected


def _strategy_guidance(strategy: OptimizerStrategy) -> str:
    """Return prompt guidance for a regenerate strategy."""
    guidance: dict[OptimizerStrategy, str] = {
        "conservative": (
            "Conservative: make the smallest truthful edits possible. Prefer "
            "1-2 keyword insertions per changed statement, avoid broad rewrites, "
            "and leave already-good statements unchanged."
        ),
        "ats_aggressive": (
            "ATS aggressive: close every supported required, preferred, and "
            "keyword gap. Adjacent evidence-backed wording is allowed, but never "
            "add unconfirmed hard tools, platforms, metrics, or domain claims."
        ),
        "recruiter_readable": (
            "Recruiter-readable: prioritize natural, human bullet quality while "
            "keeping important JD keywords. Avoid dense keyword stuffing and "
            "keep each statement easy to scan."
        ),
        "one_page_strict": (
            "One-page strict: default mode. Produce the shortest valid high-score "
            "edit set. Prefer compact rewrites, avoid length growth, and assume "
            "overflowing edits will be pruned before the result is shown."
        ),
        "supported_inference_ats_80_one_page": (
            "Supported inference: aggressively target ATS 80+ using adjacent "
            "evidence-backed wording, while always preserving a one-page resume. "
            "Rewrite supported bullets compactly before pruning. Do not add "
            "unconfirmed hard tools, platforms, certifications, degrees, metrics, "
            "employers, or domain experience."
        ),
    }
    return guidance[strategy]


async def extract_job_keywords(
    job_description: str,
    *,
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper that uses this module's patchable LLM client."""
    safe_jd = _sanitize_user_input(job_description)
    prompt = EXTRACT_KEYWORDS_PROMPT.format(job_description=safe_jd)
    result: dict[str, Any] = await complete_json(
        prompt,
        task="jd",
        backend_override=llm_backend,
        model_override=llm_model,
    )
    logger.info(
        "Keywords extracted: %d required, %d preferred, %d keywords",
        len(result.get("required_skills", [])),
        len(result.get("preferred_skills", [])),
        len(result.get("keywords", [])),
    )
    return result


async def extract_job_keywords_with_fallback(
    job_description: str,
    *,
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Extract JD keywords with LLM fallback while preserving monkeypatch hooks."""
    try:
        result = await extract_job_keywords(
            job_description,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        result.setdefault("extraction_method", "deep_llm")
        return result
    except Exception as exc:
        logger.warning("Deep JD keyword extraction failed; using fast fallback: %s", exc)
        fallback = extract_job_keywords_fast(job_description)
        fallback["extraction_method"] = "fast_local_fallback"
        fallback["llm_error"] = str(exc)
        fallback["llm_backend"] = llm_backend
        fallback["llm_model"] = llm_model
        return fallback


# ---------------------------------------------------------------------------
# Stage 4 — LaTeX diff generation
# ---------------------------------------------------------------------------


async def generate_latex_diffs(
    editable_json: dict[str, Any],
    resume_plain_text: str,
    skill_target_plan: dict[str, Any],
    job_keywords: dict[str, Any],
    job_description: str,
    ats_before: "ATSResult | None" = None,
    confirmed_skills: list[str] | None = None,
    page_budget: PageBudget | None = None,
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY,
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM to generate surgical changes to editable resume statements.

    Returns a dict with ``changes`` (list of ``{stmt_id, original, value, reason}``)
    and ``strategy_notes``.

    When *ats_before* is provided, a targeted remediation block is injected into
    the prompt listing every required skill not yet covered and every JD keyword
    phrase still missing from the resume.  This significantly improves the LLM's
    ability to close concrete ATS gaps rather than making only stylistic tweaks.
    """
    safe_jd = _sanitize_user_input(job_description)
    safe_resume = _sanitize_user_input(resume_plain_text)

    # Flatten skill targets to a readable list for the prompt
    target_skills_txt = "\n".join(
        f"- {s['skill']}: {s.get('reason', '')}"
        for s in skill_target_plan.get("target_skills", [])
    )

    # Build targeted ATS remediation block (empty string when no gaps)
    ats_remediation = (
        _build_ats_remediation(ats_before, skill_target_plan, confirmed_skills)
        if ats_before is not None
        else ""
    )

    prompt = COMPACT_GENERATE_LATEX_DIFFS_PROMPT.format(
        skill_targets=target_skills_txt,
        ats_remediation=ats_remediation,
        strategy_guidance=_strategy_guidance(optimization_strategy),
        page_budget=_format_page_budget(page_budget),
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        job_description=_compact_text(safe_jd, 3000),
        editable_json=json.dumps(editable_json, indent=2, ensure_ascii=False),
        resume_plain_text=_compact_text(safe_resume, 3000),
    )
    logger.info("generate_latex_diffs prompt length: %d chars (~%d tokens)", len(prompt), len(prompt) // 4)
    return await complete_json(
        prompt,
        task="diff",
        backend_override=llm_backend,
        model_override=llm_model,
    )


# ---------------------------------------------------------------------------
# Stage 6 — full pipeline
# ---------------------------------------------------------------------------


async def run_optimization_pipeline(
    parse_result: ParseResult,
    job_description: str,
    confirmed_skills: list[str] | None = None,
    allowed_stmt_ids: list[str] | set[str] | None = None,
    job_keywords: dict[str, Any] | None = None,
    llm_backend: str | None = None,
    llm_model: str | None = None,
    llm_routes: LLMTaskRoutes | None = None,
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY,
    reviewer_backend: ReviewerBackend | None = None,
) -> OptimizationResult:
    """Orchestrate all stages and return an :class:`OptimizationResult`.

    This function is the single entry point used by the API layer.  It is
    intentionally verbose in its logging so operators can trace each stage.

    Parameters
    ----------
    parse_result:
        The parsed resume (output of ``engine.parse_file``).
    job_description:
        The raw job description text (may be multi-paragraph).
    job_keywords:
        Optional pre-extracted JD requirements. Passing this skips Stage 1's LLM
        extraction call, which keeps the Streamlit analyze → optimize loop fast.

    Returns
    -------
    OptimizationResult
        All intermediate artifacts plus the final modified LaTeX and
        overflow flag.
    """
    result = OptimizationResult()
    result.optimization_strategy = optimization_strategy
    result.reviewer_backend = reviewer_backend or DEFAULT_REVIEWER_BACKEND
    result.confirmed_skills = list(dict.fromkeys(confirmed_skills or []))
    scoped_stmt_ids = set(allowed_stmt_ids) if allowed_stmt_ids is not None else None
    routes = dict(llm_routes or {})
    if llm_backend or llm_model:
        fallback_route = LLMTaskRoute(llm_backend, llm_model)
        for task in ("plan", "diff", "refine", "review"):
            routes.setdefault(task, fallback_route)
    result.model_routes = _routes_to_dict(routes)
    tracer = PipelineTracer(
        "smartjobapply.optimize",
        inputs={
            "jd_hash": trace_hash_text(job_description),
            "jd_excerpt": trace_compact_text(job_description, 300),
            "confirmed_skills": result.confirmed_skills,
            "allowed_stmt_ids": sorted(scoped_stmt_ids) if scoped_stmt_ids is not None else None,
            "preextracted_job_keywords": job_keywords is not None,
            "optimization_strategy": optimization_strategy,
            "reviewer_backend": result.reviewer_backend,
        },
        metadata={
            "model_routes": result.model_routes,
            "ats_target_score": result.ats_target_score,
            "optimization_strategy": optimization_strategy,
            "reviewer_backend": result.reviewer_backend,
            "resume_id": parse_result.doc.resume_id,
            "section_count": len(parse_result.doc.sections),
        },
        tags=["smartjobapply", "latex-resume"],
    )
    result.trace_id = tracer.trace_id

    # ------------------------------------------------------------------ #
    # Extract structured data for prompts                                 #
    # ------------------------------------------------------------------ #
    stage = tracer.stage(
        "extract_resume_context",
        run_type="tool",
        inputs={
            "stmt_count": len(parse_result.stmt_index),
            "latex_chars": len(parse_result.latex_source),
        },
    )
    full_data = extract_full_resume(parse_result)
    editable_data = extract_editable(parse_result)
    if scoped_stmt_ids is not None:
        editable_data = {
            **editable_data,
            "editable": _filter_editable_json_by_stmt_ids(
                editable_data.get("editable", {}),
                scoped_stmt_ids,
            ),
        }
    tracer.end_stage(
        stage,
        outputs={
            "editable_groups": list(editable_data.get("editable", {}).keys()),
            "section_count": len(parse_result.doc.sections),
        },
    )

    # Plain text view of the resume for LLM context
    resume_plain_text = _build_plain_text(full_data)

    # Existing skills as a comma-separated string
    skills_data = full_data.get("skills", {})
    if isinstance(skills_data, dict):
        existing_skills = "; ".join(
            f"{cat}: {items}" for cat, items in skills_data.items()
        )
    else:
        existing_skills = "; ".join(str(item) for item in skills_data)

    if job_keywords is not None:
        logger.info("[optimizer] Stage 1: using pre-extracted job keywords")
        stage = tracer.stage("stage1_job_keywords_preextracted", run_type="chain")
        result.job_keywords = job_keywords
        tracer.end_stage(
            stage,
            outputs={
                "required_count": len(result.job_keywords.get("required_skills", [])),
                "preferred_count": len(result.job_keywords.get("preferred_skills", [])),
                "keyword_count": len(result.job_keywords.get("keywords", [])),
            },
        )
    else:
        logger.info("[optimizer] Stage 1: extracting job keywords")
        stage = tracer.stage(
            "stage1_extract_job_keywords",
            run_type="llm",
            metadata={"task": "keyword"},
        )
        try:
            result.job_keywords = await extract_job_keywords(job_description)
            tracer.end_stage(
                stage,
                outputs={
                    "required_count": len(result.job_keywords.get("required_skills", [])),
                    "preferred_count": len(result.job_keywords.get("preferred_skills", [])),
                    "keyword_count": len(result.job_keywords.get("keywords", [])),
                },
            )
        except Exception as exc:
            tracer.end_stage(stage, error=exc)
            msg = f"Stage 1 (keyword extraction) failed: {exc}"
            logger.error(msg)
            result.warnings.append(msg)
            _fallback_to_original(result, parse_result)
            _finish_trace(tracer, result, error=exc)
            return result

    # ── ATS before ────────────────────────────────────────────────────── #
    logger.info("[optimizer] ATS check (before)")
    stage = tracer.stage("ats_before", run_type="tool")
    result.ats_before = check_ats(
        resume_plain_text,
        result.job_keywords,
        confirmed_skills=result.confirmed_skills,
    )
    tracer.end_stage(
        stage,
        outputs={
            "score": result.ats_before.score,
            "required_score": result.ats_before.required_score,
            "preferred_score": result.ats_before.preferred_score,
            "keyword_score": result.ats_before.keyword_score,
            "required_missing_count": len(result.ats_before.required_missing),
            "keyword_miss_count": len(result.ats_before.keyword_misses),
        },
    )
    logger.info(
        "[optimizer] ATS before: %.0f/100  (req %.0f%% | pref %.0f%% | kw %.0f%%)",
        result.ats_before.score,
        result.ats_before.required_score,
        result.ats_before.preferred_score,
        result.ats_before.keyword_score,
    )

    logger.info("[optimizer] Stage 2: generating skill target plan")
    plan_route = _route_for_task(routes, "plan")
    stage = tracer.stage(
        "stage2_skill_target_plan",
        run_type="llm",
        metadata={"backend": plan_route.backend, "model": plan_route.model},
    )
    try:
        result.skill_target_plan = await generate_skill_target_plan(
            resume_plain_text=resume_plain_text,
            existing_skills=existing_skills,
            job_keywords=result.job_keywords,
            job_description=job_description,
            llm_backend=plan_route.backend,
            llm_model=plan_route.model,
        )
        tracer.end_stage(
            stage,
            outputs={
                "planning_method": result.skill_target_plan.get("planning_method", "llm"),
                "target_skill_count": len(result.skill_target_plan.get("target_skills", [])),
            },
        )
    except Exception as exc:
        tracer.end_stage(stage, error=exc)
        msg = (
            "Stage 2 LLM skill plan unavailable; using deterministic local "
            f"fallback plan: {exc}"
        )
        logger.warning(msg)
        result.warnings.append(msg)
        stage = tracer.stage("stage2_skill_target_plan_fallback", run_type="tool")
        result.skill_target_plan = generate_skill_target_plan_fast(
            existing_skills=existing_skills,
            job_keywords=result.job_keywords,
            confirmed_skills=result.confirmed_skills,
        )
        tracer.end_stage(
            stage,
            outputs={
                "planning_method": result.skill_target_plan.get("planning_method"),
                "target_skill_count": len(result.skill_target_plan.get("target_skills", [])),
            },
        )

    logger.info("[optimizer] Stage 3: verifying skill target plan")
    stage = tracer.stage("stage3_verify_skill_plan", run_type="tool")
    ok, errors = verify_skill_target_plan(result.skill_target_plan)
    tracer.end_stage(stage, outputs={"ok": ok, "error_count": len(errors)})
    if not ok:
        for err in errors:
            result.warnings.append(f"Skill plan verification: {err}")
        # Non-fatal: proceed with whatever plan we have; just warn

    logger.info("[optimizer] Stage 4: generating LaTeX diffs")
    diff_route = _route_for_task(routes, "diff")
    stage = tracer.stage(
        "stage4_generate_latex_diffs",
        run_type="llm",
        metadata={
            "backend": diff_route.backend,
            "model": diff_route.model,
            "optimization_strategy": optimization_strategy,
        },
    )
    try:
        diff_response = await generate_latex_diffs(
            editable_json=editable_data["editable"],
            resume_plain_text=resume_plain_text,
            skill_target_plan=result.skill_target_plan,
            job_keywords=result.job_keywords,
            job_description=job_description,
            ats_before=result.ats_before,
            confirmed_skills=result.confirmed_skills,
            page_budget=parse_result.doc.page_budget,
            optimization_strategy=optimization_strategy,
            llm_backend=diff_route.backend,
            llm_model=diff_route.model,
        )
        tracer.end_stage(
            stage,
            outputs={
                "raw_change_count": len(diff_response.get("changes", [])),
                "has_strategy_notes": bool(diff_response.get("strategy_notes")),
            },
        )
    except Exception as exc:
        tracer.end_stage(stage, error=exc)
        msg = f"Stage 4 (LaTeX diffs) failed: {exc}"
        logger.error(msg)
        result.warnings.append(msg)
        result.ats_after = result.ats_before
        _fallback_to_original(result, parse_result)
        _finish_trace(tracer, result, error=exc)
        return result

    result.raw_changes = diff_response.get("changes", [])
    if scoped_stmt_ids is not None:
        result.raw_changes, scoped_rejected = _filter_changes_by_stmt_ids(
            result.raw_changes,
            scoped_stmt_ids,
        )
        result.rejected_changes.extend(scoped_rejected)
        for rejected_change in scoped_rejected:
            result.warnings.append(
                f"Rejected change for '{rejected_change.get('stmt_id')}': "
                f"{rejected_change.get('rejection_reason')}"
            )
    result.strategy_notes = diff_response.get("strategy_notes", "")

    logger.info("[optimizer] Stage 5: validating %d change(s)", len(result.raw_changes))
    stage = tracer.stage(
        "stage5_validate_changes",
        run_type="tool",
        inputs={"raw_change_count": len(result.raw_changes)},
    )
    validation_input = _prepare_raw_changes_for_validation(
        result.raw_changes,
        parse_result.stmt_index,
    )
    accepted, rejected = validate_changes(validation_input, parse_result.stmt_index)
    if scoped_stmt_ids is not None:
        accepted, scoped_rejected = _filter_changes_by_stmt_ids(accepted, scoped_stmt_ids)
        rejected = rejected + scoped_rejected
    initial_rejected = list(result.rejected_changes)
    repair_route = _route_for_task(routes, "refine")
    repaired_count = 0
    unrepaired_rejected = initial_rejected + rejected
    drift_rejections = [
        r for r in unrepaired_rejected
        if "unsupported claim/domain/platform" in str(r.get("rejection_reason", ""))
    ]
    if drift_rejections:
        repair_stage = tracer.stage(
            "stage5_repair_context_drift",
            run_type="llm",
            inputs={"drift_rejection_count": len(drift_rejections)},
            metadata={"backend": repair_route.backend, "model": repair_route.model},
        )
        try:
            accepted_stmt_ids = {c["stmt_id"] for c in accepted}
            repaired_changes: list[dict[str, Any]] = []
            for rejected_change in drift_rejections:
                stmt_id = rejected_change.get("stmt_id")
                if not isinstance(stmt_id, str) or stmt_id in accepted_stmt_ids:
                    continue
                repaired = await _repair_context_drift_change(
                    rejected_change=rejected_change,
                    parse_result=parse_result,
                    job_keywords=result.job_keywords,
                    ats_before=result.ats_before,
                    skill_target_plan=result.skill_target_plan,
                    confirmed_skills=result.confirmed_skills,
                    llm_backend=repair_route.backend,
                    llm_model=repair_route.model,
                )
                if repaired:
                    repaired_changes.append(repaired)

            if repaired_changes:
                repair_accepted, repair_rejected = validate_changes(
                    repaired_changes,
                    parse_result.stmt_index,
                )
                accepted.extend(repair_accepted)
                repaired_count = len(repair_accepted)
                repaired_stmt_ids = {c["stmt_id"] for c in repair_accepted}
                unrepaired_rejected = [
                    r for r in rejected
                    if r.get("stmt_id") not in repaired_stmt_ids
                ] + initial_rejected + repair_rejected
            tracer.end_stage(
                repair_stage,
                outputs={
                    "repair_attempt_count": len(drift_rejections),
                    "repair_accepted_count": repaired_count,
                    "unrepaired_rejected_count": len(unrepaired_rejected),
                },
            )
        except Exception as exc:
            tracer.end_stage(repair_stage, error=exc)
            result.warnings.append(
                f"Context-preserving repair pass failed; unsafe rewrites stayed rejected: {exc}"
            )
            unrepaired_rejected = rejected
    accepted, budget_rejected = _filter_wordy_changes_for_page_budget(
        accepted,
        parse_result.doc.page_budget,
    )
    compacted_count = 0
    if budget_rejected:
        compact_stage = tracer.stage(
            "stage5_compact_page_budget_rejections",
            run_type="llm",
            inputs={"budget_rejected_count": len(budget_rejected)},
            metadata={"backend": repair_route.backend, "model": repair_route.model},
        )
        try:
            compacted, compact_rejected = await _compact_page_budget_rejections(
                budget_rejected,
                parse_result=parse_result,
                page_budget=parse_result.doc.page_budget,
                job_keywords=result.job_keywords,
                ats_before=result.ats_before,
                skill_target_plan=result.skill_target_plan,
                confirmed_skills=result.confirmed_skills,
                llm_backend=repair_route.backend,
                llm_model=repair_route.model,
            )
            accepted.extend(compacted)
            compacted_count = len(compacted)
            result.compacted_changes.extend(compacted)
            unrepaired_rejected = unrepaired_rejected + compact_rejected
            tracer.end_stage(
                compact_stage,
                outputs={
                    "compacted_count": compacted_count,
                    "still_rejected_count": len(compact_rejected),
                },
            )
        except Exception as exc:
            tracer.end_stage(compact_stage, error=exc)
            unrepaired_rejected = unrepaired_rejected + budget_rejected
            result.warnings.append(f"Page-budget compact retry failed: {exc}")
    else:
        unrepaired_rejected = unrepaired_rejected + budget_rejected
    tracer.end_stage(
        stage,
        outputs={
            "accepted_count": len(accepted),
            "repaired_count": repaired_count,
            "compacted_count": compacted_count,
            "budget_rejected_count": len(budget_rejected),
            "rejected_count": len(unrepaired_rejected),
            "rejection_reasons": [
                r.get("rejection_reason", "") for r in unrepaired_rejected[:10]
            ],
        },
    )
    result.rejected_changes = unrepaired_rejected
    if unrepaired_rejected:
        for r in unrepaired_rejected:
            result.warnings.append(
                f"Rejected change for '{r.get('stmt_id')}': {r.get('rejection_reason')}"
            )
    if repaired_count:
        result.warnings.append(
            f"Repaired {repaired_count} unsafe rewrite(s) into context-preserving edits."
        )

    # ── Stage 5.5: naturalness refinement ─────────────────────────────── #
    # For each accepted change, check whether the rewrite sounds AI-generated.
    # Changes below the naturalness threshold get a second LLM pass that is
    # instructed to keep the JD keywords but restore the candidate's voice.
    logger.info("[optimizer] Stage 5.5: checking naturalness of %d accepted change(s)", len(accepted))
    stage = tracer.stage(
        "stage5_5_naturalness_refinement",
        run_type="chain",
        inputs={"accepted_count": len(accepted)},
        metadata={"refine_route": _routes_to_dict({"refine": _route_for_task(routes, "refine")})},
    )
    refined_count = 0
    for change in accepted:
        score, issues = naturalness_check(change["value"], change["original"], stmt_id=change["stmt_id"])
        if score < NATURALNESS_THRESHOLD:
            logger.info(
                "[optimizer] Stage 5.5: %s naturalness=%.2f — refining. issues=%s",
                change["stmt_id"], score, issues,
            )
            try:
                refined = await refine_bullet(
                    original=change["original"],
                    ai_rewrite=change["value"],
                    problems=issues,
                    llm_backend=_route_for_task(routes, "refine").backend,
                    llm_model=_route_for_task(routes, "refine").model,
                )
                if refined:
                    refined_safe = _escape_latex_specials(
                        _preserve_existing_textbf(
                            _strip_item_wrapper(refined),
                            change["original"],
                        )
                    )
                    unsupported_claims = _unsupported_claim_drifts(
                        refined_safe,
                        change["original"],
                        change["stmt_id"],
                    )
                    # Accept only if: non-empty, different from original, no new metrics,
                    # and no unsupported JD-specific claim drift.
                    if (
                        refined_safe
                        and refined_safe != change["original"]
                        and not _fabricated_metrics(refined_safe, change["original"])
                        and not unsupported_claims
                    ):
                        refined_score, _ = naturalness_check(refined_safe, change["original"], stmt_id=change["stmt_id"])
                        if refined_score >= score:
                            logger.info(
                                "[optimizer] Stage 5.5: %s refined  naturalness %.2f → %.2f",
                                change["stmt_id"], score, refined_score,
                            )
                            change["value"] = refined_safe
                            change["reason"] = change.get("reason", "") + " [voice-refined]"
                            refined_count += 1
                        else:
                            logger.info(
                                "[optimizer] Stage 5.5: %s refined version worse (%.2f < %.2f) — keeping AI draft",
                                change["stmt_id"], refined_score, score,
                            )
                    else:
                        logger.info(
                            "[optimizer] Stage 5.5: %s refined value unusable — keeping AI draft",
                            change["stmt_id"],
                        )
            except Exception as exc:
                logger.warning(
                    "[optimizer] Stage 5.5: refinement failed for %s: %s",
                    change["stmt_id"], exc,
                )
        else:
            logger.info(
                "[optimizer] Stage 5.5: %s naturalness=%.2f — OK",
                change["stmt_id"], score,
            )
    tracer.end_stage(
        stage,
        outputs={"accepted_count": len(accepted), "refined_count": refined_count},
    )

    # Build {stmt_id: value} map for reconstruction
    result.validated_changes = {c["stmt_id"]: c["value"] for c in accepted}
    result.diff = accepted  # include reason for display

    stage = tracer.stage(
        "reconstruct_render_ats_after_llm",
        run_type="chain",
        inputs={"validated_change_count": len(result.validated_changes)},
    )
    if not result.validated_changes:
        result.warnings.append("No valid LLM changes to apply — checking deterministic ATS patches")
        result.modified_latex = parse_result.latex_source
    else:
        logger.info(
            "[optimizer] Applying %d validated change(s)", len(result.validated_changes)
        )
        recon = reconstruct(parse_result, result.validated_changes)
        result.modified_latex = recon.latex

    logger.info("[optimizer] Checking one-page constraint + rendering PDF")
    _record_render(result, check_one_page(result.modified_latex))

    # ── ATS after ─────────────────────────────────────────────────────── #
    logger.info("[optimizer] ATS check (after LLM changes)")
    modified_pr = _reparse(result.modified_latex, resume_id=parse_result.doc.resume_id)
    modified_plain_text = _build_plain_text(extract_full_resume(modified_pr))
    result.ats_after = check_ats(
        modified_plain_text,
        result.job_keywords,
        confirmed_skills=result.confirmed_skills,
    )
    tracer.end_stage(
        stage,
        outputs={
            "score_after_llm": result.ats_after.score,
            "page_count": result.page_count,
            "overflow": result.overflow,
            "pdf_rendered": result.pdf_bytes is not None,
        },
    )
    logger.info(
        "[optimizer] ATS after LLM: %.0f/100  (req %.0f%% | pref %.0f%% | kw %.0f%%)",
        result.ats_after.score,
        result.ats_after.required_score,
        result.ats_after.preferred_score,
        result.ats_after.keyword_score,
    )

    # ── Deterministic skills patch (fallback for required + actionable preferred) ─ #
    # Non-technical preferred items (soft skills, domain experience, certifications)
    # cannot be patched into a skills line — exclude them.
    _NON_PATCHABLE_PREFERRED_KW = frozenset({
        "communication", "healthcare", "ecommerce", "experience",
        "industry", "stakeholder", "background",
    })

    def _is_patchable_preferred(skill: str) -> bool:
        norm = skill.lower()
        return not any(kw in norm for kw in _NON_PATCHABLE_PREFERRED_KW)

    actionable_preferred = [
        s for s in result.ats_after.preferred_missing
        if _is_patchable_preferred(s)
    ]
    raw_missing = result.ats_after.required_missing + actionable_preferred
    candidate_skills_to_patch, non_skill_gaps = split_skill_confirmation_candidates(
        raw_missing
    )
    if non_skill_gaps:
        result.warnings.append(
            "JD themes/phrases should be woven into bullets, not added as skills: "
            + ", ".join(non_skill_gaps)
        )
    skills_to_patch, skipped_unconfirmed = _filter_confirmed_patch_skills(
        candidate_skills_to_patch,
        confirmed_skills=result.confirmed_skills,
    )
    result.confirmation_required_skills = skipped_unconfirmed
    if skipped_unconfirmed:
        result.warnings.append(
            "Missing skills require user confirmation before patching: "
            + ", ".join(skipped_unconfirmed)
        )

    stage = tracer.stage(
        "deterministic_skills_patch",
        run_type="tool",
        inputs={
            "candidate_skill_count": len(candidate_skills_to_patch),
            "confirmed_patch_count": len(skills_to_patch),
            "skipped_unconfirmed_count": len(skipped_unconfirmed),
        },
    )
    patched_count = 0
    if skills_to_patch:
        logger.info(
            "[optimizer] Score-aware deterministic skills patch for: %s",
            skills_to_patch,
        )
        patched_count = _apply_score_aware_skills_patch(
            result,
            parse_result,
            editable_data,
            skills_to_patch,
        )
    tracer.end_stage(
        stage,
        outputs={
            "patched_stmt_count": patched_count,
            "score_after_patch": result.ats_after.score if result.ats_after else None,
            "confirmation_required_skills": result.confirmation_required_skills,
        },
    )

    stage = tracer.stage(
        "automatic_keyword_equivalence_patch",
        run_type="tool",
        inputs={
            "score_before": result.ats_after.score if result.ats_after else None,
            "keyword_miss_count": (
                len(result.ats_after.keyword_misses) if result.ats_after else 0
            ),
            "optimization_strategy": optimization_strategy,
        },
    )
    auto_keyword_patches = _apply_supported_keyword_equivalence_patch(
        result,
        parse_result,
        editable_data,
        optimization_strategy=optimization_strategy,
    )
    tracer.end_stage(
        stage,
        outputs={
            "patched_stmt_count": len(auto_keyword_patches),
            "patched_stmt_ids": list(auto_keyword_patches),
            "score_after": result.ats_after.score if result.ats_after else None,
        },
    )

    stage = tracer.stage(
        "overflow_repair",
        run_type="tool",
        inputs={
            "overflow": result.overflow,
            "page_count": result.page_count,
            "score_after": result.ats_after.score if result.ats_after else None,
            "change_count": len(result.validated_changes),
        },
    )
    ats_remediation = (
        _build_ats_remediation(
            result.ats_after,
            result.skill_target_plan,
            result.confirmed_skills,
        )
        if result.ats_after is not None
        else ""
    )
    overflow_compacted = []
    if result.overflow:
        overflow_compacted = await _compact_overflowing_applied_changes(
            result,
            parse_result,
            result.job_keywords,
            ats_remediation=ats_remediation,
            llm_backend=_route_for_task(routes, "refine").backend,
            llm_model=_route_for_task(routes, "refine").model,
        )
    removed_for_overflow = _repair_overflow_by_pruning_low_roi_changes(
        result,
        parse_result,
        result.job_keywords,
    )
    tracer.end_stage(
        stage,
        outputs={
            "compacted_change_count": len(overflow_compacted),
            "compacted_stmt_ids": overflow_compacted,
            "removed_change_count": len(removed_for_overflow),
            "removed_stmt_ids": removed_for_overflow,
            "overflow_after": result.overflow,
            "page_count_after": result.page_count,
            "score_after": result.ats_after.score if result.ats_after else None,
        },
    )

    stage = tracer.stage(
        "recruiter_review_loop",
        run_type="chain",
        inputs={
            "score_before": result.ats_after.score if result.ats_after else None,
            "target": result.ats_target_score,
            "overflow": result.overflow,
        },
        metadata={
            "backend": _route_for_task(routes, "review").backend,
            "model": _route_for_task(routes, "review").model,
            "reviewer_backend": result.reviewer_backend,
        },
    )
    try:
        review_iterations = await _run_recruiter_review_loop(
            result,
            parse_result,
            job_description,
            routes,
            reviewer_backend=result.reviewer_backend,
            max_iterations=2,
        )
        tracer.end_stage(
            stage,
            outputs={
                "iterations": review_iterations,
                "score_after": result.ats_after.score if result.ats_after else None,
                "target_met": (
                    result.ats_after.score >= result.ats_target_score
                    if result.ats_after
                    else False
                ),
                "overflow_after": result.overflow,
            },
        )
    except Exception as exc:
        tracer.end_stage(stage, error=exc)
        result.warnings.append(f"Recruiter review loop failed: {exc}")

    stage = tracer.stage(
        "final_strict_one_page_fit",
        run_type="chain",
        inputs={
            "overflow": result.overflow,
            "visual_overflow": result.visual_overflow,
            "page_count": result.page_count,
            "score_before": result.ats_after.score if result.ats_after else None,
        },
    )
    strict_compacted: list[str] = []
    if result.overflow:
        strict_compacted = await _compact_original_resume_for_strict_fit(
            result,
            parse_result,
            result.job_keywords,
            ats_remediation=ats_remediation,
            llm_backend=_route_for_task(routes, "refine").backend,
            llm_model=_route_for_task(routes, "refine").model,
        )
    tracer.end_stage(
        stage,
        outputs={
            "compacted_stmt_ids": strict_compacted,
            "overflow_after": result.overflow,
            "visual_overflow_after": result.visual_overflow,
            "page_count_after": result.page_count,
            "score_after": result.ats_after.score if result.ats_after else None,
        },
    )

    _record_ats_target_status(result)

    if result.overflow:
        if result.visual_overflow:
            result.warnings.append(
                "Modified resume has text clipped below the PDF bottom safety "
                "margin. The PDF is blocked until more editable content is shortened."
            )
        else:
            result.warnings.append(
                f"Modified resume overflows one page ({result.page_count} page(s)). "
                "Consider shortening bullet text."
            )
        logger.warning("[optimizer] Overflow detected — %d page(s)", result.page_count)
    elif result.pdf_bytes:
        logger.info(
            "[optimizer] One-page check passed (%d page(s)) — PDF %d bytes",
            result.page_count, len(result.pdf_bytes),
        )

    _finish_trace(tracer, result)
    return result


async def refine_resume_with_instruction(
    *,
    latex_source: str,
    job_description: str,
    instruction: str,
    job_keywords: dict[str, Any],
    confirmed_skills: list[str] | None = None,
    allowed_stmt_ids: list[str] | set[str] | None = None,
    scope_label: str = "Selected resume statements",
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> OptimizationResult:
    """Apply one user chat instruction to the current LaTeX resume.

    The LLM is constrained to return statement-level edits only.  Returned edits
    still pass the same validator and one-page render gate as the main pipeline.
    """
    parse_result = _reparse(latex_source, resume_id="chat_refine")
    result = OptimizationResult()
    result.job_keywords = job_keywords
    result.confirmed_skills = list(dict.fromkeys(confirmed_skills or []))
    scoped_stmt_ids = set(allowed_stmt_ids) if allowed_stmt_ids is not None else None

    full_data = extract_full_resume(parse_result)
    resume_plain_text = _build_plain_text(full_data)
    result.ats_before = check_ats(
        resume_plain_text,
        job_keywords,
        confirmed_skills=result.confirmed_skills,
    )
    editable_data = extract_editable(parse_result)
    editable_json = _filter_editable_json_by_stmt_ids(
        editable_data.get("editable", {}),
        scoped_stmt_ids,
    )

    if not instruction.strip():
        result.warnings.append("No chat instruction was provided.")
        result.modified_latex = latex_source
        result.ats_after = result.ats_before
        _record_render(result, check_one_page(result.modified_latex))
        _record_ats_target_status(result)
        return result

    if not editable_json:
        result.warnings.append("No editable statements are available for this scope.")
        result.modified_latex = latex_source
        result.ats_after = result.ats_before
        _record_render(result, check_one_page(result.modified_latex))
        _record_ats_target_status(result)
        return result

    prompt = CHAT_REFINE_PROMPT.format(
        instruction=_sanitize_user_input(instruction),
        scope_label=_sanitize_user_input(scope_label),
        confirmed_skills=json.dumps(result.confirmed_skills, ensure_ascii=False),
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        job_description=_compact_text(_sanitize_user_input(job_description), 2500),
        editable_json=json.dumps(editable_json, indent=2, ensure_ascii=False),
        resume_plain_text=_compact_text(_sanitize_user_input(resume_plain_text), 2500),
    )
    data = await complete_json(
        prompt,
        task="refine",
        backend_override=llm_backend,
        model_override=llm_model,
    )
    result.raw_changes = data.get("changes", []) if isinstance(data, dict) else []
    result.strategy_notes = (
        str(data.get("strategy_notes", "")) if isinstance(data, dict) else ""
    )
    if scoped_stmt_ids is not None:
        result.raw_changes, scoped_rejected = _filter_changes_by_stmt_ids(
            result.raw_changes,
            scoped_stmt_ids,
        )
        result.rejected_changes.extend(scoped_rejected)

    accepted, rejected = validate_changes(
        _prepare_raw_changes_for_validation(result.raw_changes, parse_result.stmt_index),
        parse_result.stmt_index,
    )
    if scoped_stmt_ids is not None:
        accepted, scoped_rejected = _filter_changes_by_stmt_ids(accepted, scoped_stmt_ids)
        rejected = rejected + scoped_rejected
    result.rejected_changes.extend(rejected)
    result.validated_changes = {c["stmt_id"]: c["value"] for c in accepted}
    result.diff = accepted

    if result.validated_changes:
        recon = reconstruct(parse_result, result.validated_changes)
        result.modified_latex = recon.latex
    else:
        result.modified_latex = latex_source
        result.warnings.append("No valid chat refinement changes were applied.")

    _record_render(result, check_one_page(result.modified_latex))
    modified_pr = _reparse(result.modified_latex, resume_id=parse_result.doc.resume_id)
    modified_plain_text = _build_plain_text(extract_full_resume(modified_pr))
    result.ats_after = check_ats(
        modified_plain_text,
        job_keywords,
        confirmed_skills=result.confirmed_skills,
    )
    _record_ats_target_status(result)
    return result


def apply_manual_statement_edits(
    *,
    latex_source: str,
    changes: dict[str, str],
    job_keywords: dict[str, Any],
    confirmed_skills: list[str] | None = None,
    allowed_stmt_ids: list[str] | set[str] | None = None,
) -> OptimizationResult:
    """Apply user-edited statement text to current LaTeX via normal validation."""
    parse_result = _reparse(latex_source, resume_id="manual_edit")
    result = OptimizationResult()
    result.job_keywords = job_keywords
    result.confirmed_skills = list(dict.fromkeys(confirmed_skills or []))
    scoped_stmt_ids = set(allowed_stmt_ids) if allowed_stmt_ids is not None else None

    current_plain = _build_plain_text(extract_full_resume(parse_result))
    result.ats_before = check_ats(
        current_plain,
        job_keywords,
        confirmed_skills=result.confirmed_skills,
    )

    raw_changes = [
        {"stmt_id": stmt_id, "value": value, "reason": "Manual editor change"}
        for stmt_id, value in changes.items()
    ]
    if scoped_stmt_ids is not None:
        raw_changes, scoped_rejected = _filter_changes_by_stmt_ids(
            raw_changes,
            scoped_stmt_ids,
        )
        result.rejected_changes.extend(scoped_rejected)

    accepted, rejected = validate_changes(
        _prepare_raw_changes_for_validation(raw_changes, parse_result.stmt_index),
        parse_result.stmt_index,
    )
    if scoped_stmt_ids is not None:
        accepted, scoped_rejected = _filter_changes_by_stmt_ids(accepted, scoped_stmt_ids)
        rejected = rejected + scoped_rejected
    result.rejected_changes.extend(rejected)
    result.validated_changes = {c["stmt_id"]: c["value"] for c in accepted}
    result.diff = accepted

    if result.validated_changes:
        recon = reconstruct(parse_result, result.validated_changes)
        result.modified_latex = recon.latex
    else:
        result.modified_latex = latex_source
        result.warnings.append("No valid manual editor changes were applied.")

    _record_render(result, check_one_page(result.modified_latex))
    modified_pr = _reparse(result.modified_latex, resume_id=parse_result.doc.resume_id)
    modified_plain = _build_plain_text(extract_full_resume(modified_pr))
    result.ats_after = check_ats(
        modified_plain,
        job_keywords,
        confirmed_skills=result.confirmed_skills,
    )
    _record_ats_target_status(result)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _repair_context_drift_change(
    rejected_change: dict[str, Any],
    parse_result: ParseResult,
    job_keywords: dict[str, Any],
    ats_before: ATSResult | None,
    skill_target_plan: dict[str, Any],
    confirmed_skills: list[str],
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any] | None:
    """Repair an unsafe JD-platform rewrite into a context-preserving edit.

    The returned change is not trusted.  Callers must run it through
    ``validate_changes`` before applying it.
    """
    stmt_id = rejected_change.get("stmt_id")
    if not isinstance(stmt_id, str) or stmt_id not in parse_result.stmt_index:
        return None

    span = parse_result.stmt_index[stmt_id]
    original = span.original_text
    ats_remediation = (
        _build_ats_remediation(ats_before, skill_target_plan, confirmed_skills)
        if ats_before is not None
        else ""
    )
    prompt = REPAIR_CONTEXT_DRIFT_PROMPT.format(
        original=original,
        rejected_value=str(rejected_change.get("value", "")),
        rejection_reason=str(rejected_change.get("rejection_reason", "")),
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        ats_remediation=ats_remediation,
    )

    data = await complete_json(
        prompt,
        task="refine",
        backend_override=llm_backend,
        model_override=llm_model,
    )
    value = data.get("value")
    if not isinstance(value, str) or not value.strip():
        return None

    reason = data.get("reason")
    return {
        "stmt_id": stmt_id,
        "value": value.strip(),
        "reason": (
            str(reason)
            if isinstance(reason, str) and reason.strip()
            else "Repaired unsafe JD-platform rewrite while preserving original context."
        ),
    }


async def _compact_rewrite_change(
    change: dict[str, Any],
    *,
    max_words: int,
    job_keywords: dict[str, Any],
    ats_remediation: str,
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any] | None:
    """Ask the LLM to compress a useful-but-too-long statement rewrite."""
    stmt_id = change.get("stmt_id")
    original = str(change.get("original", ""))
    candidate = str(change.get("value", ""))
    if not isinstance(stmt_id, str) or not original or not candidate:
        return None

    prompt = COMPACT_REWRITE_PROMPT.format(
        original=original,
        candidate=candidate,
        reason=str(change.get("rejection_reason") or change.get("reason") or ""),
        max_words=max_words,
        job_keywords=json.dumps(job_keywords, ensure_ascii=False),
        ats_remediation=ats_remediation,
    )
    data = await complete_json(
        prompt,
        task="refine",
        backend_override=llm_backend,
        model_override=llm_model,
    )
    value = data.get("value")
    if not isinstance(value, str) or not value.strip():
        return None

    reason = data.get("reason")
    return {
        "stmt_id": stmt_id,
        "value": value.strip(),
        "reason": (
            str(reason)
            if isinstance(reason, str) and reason.strip()
            else "Compacted rewrite to preserve one-page fit."
        ),
    }


async def _compact_page_budget_rejections(
    rejected_changes: list[dict[str, Any]],
    *,
    parse_result: ParseResult,
    page_budget: PageBudget,
    job_keywords: dict[str, Any],
    ats_before: ATSResult | None,
    skill_target_plan: dict[str, Any],
    confirmed_skills: list[str],
    llm_backend: str | None,
    llm_model: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retry page-budget rejections with compact rewrites."""
    if not rejected_changes:
        return [], []

    ats_remediation = (
        _build_ats_remediation(ats_before, skill_target_plan, confirmed_skills)
        if ats_before is not None
        else ""
    )
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for change in rejected_changes:
        max_words = _allowed_words_for_change(change, page_budget)
        try:
            compacted = await _compact_rewrite_change(
                change,
                max_words=max_words,
                job_keywords=job_keywords,
                ats_remediation=ats_remediation,
                llm_backend=llm_backend,
                llm_model=llm_model,
            )
        except Exception as exc:
            rejected.append(
                {
                    **change,
                    "rejection_reason": (
                        f"{change.get('rejection_reason', 'page budget rejection')}; "
                        f"compact retry failed: {exc}"
                    ),
                }
            )
            continue

        if not compacted:
            rejected.append(
                {
                    **change,
                    "rejection_reason": (
                        f"{change.get('rejection_reason', 'page budget rejection')}; "
                        "compact retry returned no usable rewrite"
                    ),
                }
            )
            continue

        compact_accepted, compact_rejected = validate_changes(
            [compacted],
            parse_result.stmt_index,
        )
        compact_accepted, compact_budget_rejected = _filter_wordy_changes_for_page_budget(
            compact_accepted,
            page_budget,
        )
        if compact_accepted:
            compact_accepted[0]["reason"] = (
                compact_accepted[0].get("reason", "")
                + " [compacted for one page]"
            )
            accepted.extend(compact_accepted)
        else:
            rejected.extend(compact_rejected or compact_budget_rejected or [change])

    return accepted, rejected


def _repair_overflow_by_pruning_low_roi_changes(
    result: OptimizationResult,
    parse_result: ParseResult,
    job_keywords: dict[str, Any],
) -> list[str]:
    """Remove low-ATS-impact edits until the resume fits one page.

    The optimizer is allowed to improve ATS only inside the one-page invariant.
    This repair loop greedily removes one applied change at a time. If the
    current result already meets the ATS target, removals must preserve that
    target. If the result is below target, the invariant still wins: remove the
    smallest-score-drop edits needed to make the resume one page, then explain
    the remaining blockers.
    """
    if not result.overflow or not result.validated_changes or result.ats_after is None:
        return []

    current_changes = dict(result.validated_changes)
    current_score = result.ats_after.score
    enforce_target = current_score >= result.ats_target_score
    removed: list[str] = []

    while current_changes and result.overflow:
        candidates: list[dict[str, Any]] = []
        for stmt_id in list(current_changes):
            trial_changes = {
                sid: value for sid, value in current_changes.items() if sid != stmt_id
            }
            trial = _evaluate_change_set(
                parse_result,
                trial_changes,
                job_keywords,
                result.confirmed_skills,
            )
            trial_score = trial["ats"].score
            if enforce_target and trial_score < result.ats_target_score:
                continue
            candidates.append(
                {
                    "stmt_id": stmt_id,
                    "changes": trial_changes,
                    "latex": trial["latex"],
                    "render": trial["render"],
                    "ats": trial["ats"],
                    "score_drop": max(0.0, current_score - trial_score),
                    "priority": _overflow_prune_priority(stmt_id),
                }
            )

        if not candidates:
            break

        # Prefer an edit removal that fixes the page count now. Otherwise remove
        # the lowest score-impact edit and continue the loop.
        candidates.sort(
            key=lambda c: (
                c["render"].overflow,
                c["score_drop"],
                c["priority"],
                c["stmt_id"],
            )
        )
        best = candidates[0]
        current_changes = best["changes"]
        current_score = best["ats"].score
        removed.append(best["stmt_id"])
        result.modified_latex = best["latex"]
        result.ats_after = best["ats"]
        _record_render(result, best["render"])

    if removed:
        result.validated_changes = current_changes
        result.pruned_changes.extend(removed)
        removed_set = set(removed)
        result.diff = [c for c in result.diff if c.get("stmt_id") not in removed_set]
        if enforce_target:
            result.warnings.append(
                "Overflow repair removed low-impact edit(s) to preserve one page: "
                + ", ".join(removed)
            )
        else:
            result.warnings.append(
                "Overflow repair removed edit(s) to enforce the one-page output: "
                + ", ".join(removed)
            )
        logger.info(
            "[optimizer] Overflow repair removed %d edit(s): %s",
            len(removed),
            ", ".join(removed),
        )

    if result.overflow:
        result.warnings.append(
            "Overflow repair could not fit the resume on one page. This result "
            "is not submission-ready until statement text is shortened manually."
        )

    return removed


async def _compact_overflowing_applied_changes(
    result: OptimizationResult,
    parse_result: ParseResult,
    job_keywords: dict[str, Any],
    *,
    ats_remediation: str,
    llm_backend: str | None,
    llm_model: str | None,
) -> list[str]:
    """Try shortening applied prose edits before pruning them for overflow."""
    if not result.overflow or not result.validated_changes or result.ats_after is None:
        return []

    current_changes = dict(result.validated_changes)
    current_score = result.ats_after.score
    enforce_target = current_score >= result.ats_target_score
    compacted_stmt_ids: list[str] = []

    for stmt_id in sorted(current_changes, key=_overflow_prune_priority):
        if stmt_id.startswith("skills_") or stmt_id not in parse_result.stmt_index:
            continue
        span = parse_result.stmt_index[stmt_id]
        current_value = current_changes[stmt_id]
        original_words = _plain_word_count(span.original_text)
        max_words = max(6, original_words)
        compacted = await _compact_rewrite_change(
            {
                "stmt_id": stmt_id,
                "original": span.original_text,
                "value": current_value,
                "rejection_reason": "modified resume overflows one page",
            },
            max_words=max_words,
            job_keywords=job_keywords,
            ats_remediation=ats_remediation,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        if not compacted:
            continue

        accepted, rejected = validate_changes([compacted], parse_result.stmt_index)
        if not accepted or rejected:
            continue
        compact_value = accepted[0]["value"]
        trial_changes = {**current_changes, stmt_id: compact_value}
        trial = _evaluate_change_set(
            parse_result,
            trial_changes,
            job_keywords,
            result.confirmed_skills,
        )
        trial_score = trial["ats"].score
        if enforce_target and trial_score < result.ats_target_score:
            continue
        if not enforce_target and trial_score < current_score:
            continue

        current_changes = trial_changes
        current_score = trial_score
        result.validated_changes = current_changes
        result.modified_latex = trial["latex"]
        result.ats_after = trial["ats"]
        _record_render(result, trial["render"])
        compacted_stmt_ids.append(stmt_id)
        result.compacted_changes.append(
            {
                "stmt_id": stmt_id,
                "reason": "Compacted applied edit to preserve one-page fit.",
                "original": span.original_text,
                "value": compact_value,
            }
        )
        _upsert_diff_change(
            result,
            stmt_id,
            span.original_text,
            compact_value,
            "[Compact retry] shortened applied edit to preserve one-page fit",
        )

        if not result.overflow:
            break

    if compacted_stmt_ids:
        result.warnings.append(
            "Compacted applied edit(s) to preserve one-page fit: "
            + ", ".join(compacted_stmt_ids)
        )
    return compacted_stmt_ids


async def _compact_original_resume_for_strict_fit(
    result: OptimizationResult,
    parse_result: ParseResult,
    job_keywords: dict[str, Any],
    *,
    ats_remediation: str,
    llm_backend: str | None,
    llm_model: str | None,
    max_attempts: int = 8,
) -> list[str]:
    """Compact original editable content when removing generated edits is insufficient.

    Page counting alone can report one page while text is positioned below the
    PDF media box. This final pass also handles that visual clipping case. It
    preserves locked certifications/publications and recovers space from longer,
    lower-priority editable statements instead.
    """
    if not result.overflow or result.ats_after is None:
        return []

    current_changes = dict(result.validated_changes)
    current_score = result.ats_after.score
    enforce_target = current_score >= result.ats_target_score
    compacted_ids: list[str] = []
    attempts = 0
    candidates = sorted(
        parse_result.stmt_index,
        key=lambda stmt_id: (
            _strict_compaction_priority(stmt_id),
            -_plain_word_count(
                current_changes.get(
                    stmt_id,
                    parse_result.stmt_index[stmt_id].original_text,
                )
            ),
            stmt_id,
        ),
    )

    for stmt_id in candidates:
        if attempts >= max_attempts or not result.overflow:
            break
        span = parse_result.stmt_index[stmt_id]
        current_value = current_changes.get(stmt_id, span.original_text)
        current_words = _plain_word_count(current_value)
        minimum_words = 8 if not stmt_id.startswith("skills_") else 6
        if current_words <= minimum_words + 3:
            continue

        attempts += 1
        max_words = max(minimum_words, int(current_words * 0.78))
        compacted = await _compact_rewrite_change(
            {
                "stmt_id": stmt_id,
                "original": span.original_text,
                "value": current_value,
                "reason": (
                    "Final strict one-page fit: shorten lower-value wording while "
                    "preserving supported JD keywords and factual evidence."
                ),
            },
            max_words=max_words,
            job_keywords=job_keywords,
            ats_remediation=ats_remediation,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        if not compacted:
            compacted_value = _deterministic_compact_statement(
                current_value,
                max_words,
            )
            if compacted_value:
                compacted = {
                    "stmt_id": stmt_id,
                    "value": compacted_value,
                    "reason": "Deterministically shortened for strict one-page fit.",
                }
        if not compacted:
            continue

        accepted, rejected = validate_changes([compacted], parse_result.stmt_index)
        if not accepted or rejected:
            continue
        compact_value = accepted[0]["value"]
        if _plain_word_count(compact_value) >= current_words:
            continue

        trial_changes = {**current_changes, stmt_id: compact_value}
        trial = _evaluate_change_set(
            parse_result,
            trial_changes,
            job_keywords,
            result.confirmed_skills,
        )
        trial_score = trial["ats"].score
        if enforce_target and trial_score < result.ats_target_score:
            continue
        if not enforce_target and trial_score < current_score - 2.0:
            continue

        current_changes = trial_changes
        current_score = trial_score
        result.validated_changes = current_changes
        result.modified_latex = trial["latex"]
        result.ats_after = trial["ats"]
        _record_render(result, trial["render"])
        compacted_ids.append(stmt_id)
        result.compacted_changes.append(
            {
                "stmt_id": stmt_id,
                "reason": "Final strict compaction to keep all text inside one page.",
                "original": span.original_text,
                "value": compact_value,
            }
        )
        _upsert_diff_change(
            result,
            stmt_id,
            span.original_text,
            compact_value,
            "[Strict fit] shortened lower-value wording to preserve one page",
        )

    if compacted_ids:
        result.warnings.append(
            "Strict one-page fit compacted statement(s): "
            + ", ".join(compacted_ids)
        )
    return compacted_ids


def _strict_compaction_priority(stmt_id: str) -> int:
    """Prefer shortening project prose, then summary/work, with skills last."""
    if stmt_id.startswith("proj_"):
        return 0
    if stmt_id.startswith("summary"):
        return 1
    if stmt_id.startswith("work_"):
        return 2
    if stmt_id.startswith("skills_"):
        return 3
    return 4


def _deterministic_compact_statement(text: str, max_words: int) -> str | None:
    """Safely trim a trailing clause without cutting through LaTeX groups."""
    if _plain_word_count(text) <= max_words:
        return text
    cut_points = [
        match.start()
        for match in re.finditer(r"(?:,\s+|;\s+|\s+--\s+|\s+—\s+)", text)
    ]
    for cut in reversed(cut_points):
        candidate = text[:cut].rstrip(" ,;—-")
        if (
            _plain_word_count(candidate) <= max_words
            and _plain_word_count(candidate) >= 8
            and candidate.count("{") == candidate.count("}")
        ):
            return candidate.rstrip(".") + "."
    return None


async def _generate_recruiter_review(
    *,
    result: OptimizationResult,
    parse_result: ParseResult,
    job_description: str,
    resume_plain_text: str,
    llm_backend: str | None,
    llm_model: str | None,
    reviewer_backend: ReviewerBackend = "custom",
) -> dict[str, Any]:
    """Ask a recruiter-style reviewer for the next best supported edits."""
    ats = result.ats_after
    ats_summary = (
        {
            "score": ats.score,
            "required_score": ats.required_score,
            "preferred_score": ats.preferred_score,
            "keyword_score": ats.keyword_score,
            "required_missing": ats.required_missing,
            "preferred_missing": ats.preferred_missing,
            "keyword_misses": ats.keyword_misses,
        }
        if ats
        else {}
    )
    editable_statements = _current_editable_statement_json(parse_result, result)
    if reviewer_backend == "langchain":
        from latex_resume.langchain_reviewer import generate_langchain_recruiter_review

        return await generate_langchain_recruiter_review(
            ats_summary=ats_summary,
            confirmed_skills=result.confirmed_skills,
            job_keywords=result.job_keywords,
            job_description=job_description,
            resume_plain_text=resume_plain_text,
            editable_json=editable_statements,
            backend=llm_backend,
            model=llm_model,
        )

    prompt = RECRUITER_REVIEW_PROMPT.format(
        ats_summary=json.dumps(ats_summary, ensure_ascii=False),
        confirmed_skills=", ".join(result.confirmed_skills) or "None",
        job_keywords=json.dumps(result.job_keywords, ensure_ascii=False),
        job_description=_compact_text(_sanitize_user_input(job_description), 3000),
        resume_plain_text=_compact_text(_sanitize_user_input(resume_plain_text), 3000),
        editable_json=json.dumps(
            editable_statements,
            indent=2,
            ensure_ascii=False,
        ),
    )
    return await complete_json(
        prompt,
        task="diff",
        backend_override=llm_backend,
        model_override=llm_model,
    )


def _current_editable_statement_json(
    parse_result: ParseResult,
    result: OptimizationResult,
) -> dict[str, str]:
    """Return stmt_id -> current text after applied changes."""
    return {
        stmt_id: result.validated_changes.get(stmt_id, span.original_text)
        for stmt_id, span in parse_result.stmt_index.items()
    }


async def _run_recruiter_review_loop(
    result: OptimizationResult,
    parse_result: ParseResult,
    job_description: str,
    routes: LLMTaskRoutes,
    *,
    reviewer_backend: ReviewerBackend = "custom",
    max_iterations: int = 2,
) -> int:
    """Iteratively ask a recruiter-style reviewer for supported 80+ fixes."""
    if result.ats_after is None:
        return 0
    if result.ats_after.score >= result.ats_target_score and not result.overflow:
        return 0
    if result.confirmation_required_skills:
        result.recruiter_feedback.append(
            "Recruiter review paused because unconfirmed hard skills still block the score: "
            + ", ".join(result.confirmation_required_skills)
        )
        return 0

    iterations = 0
    review_route = _route_for_task(routes, "review")
    refine_route = _route_for_task(routes, "refine")
    for iteration in range(max_iterations):
        if result.ats_after.score >= result.ats_target_score and not result.overflow:
            break

        current_pr = _reparse(result.modified_latex or parse_result.latex_source, resume_id=parse_result.doc.resume_id)
        resume_plain_text = _build_plain_text(extract_full_resume(current_pr))
        try:
            review = await _generate_recruiter_review(
                result=result,
                parse_result=parse_result,
                job_description=job_description,
                resume_plain_text=resume_plain_text,
                llm_backend=review_route.backend,
                llm_model=review_route.model,
                reviewer_backend=reviewer_backend,
            )
        except Exception as exc:
            if reviewer_backend != "langchain":
                raise
            result.warnings.append(
                f"LangChain reviewer unavailable; falling back to custom reviewer: {exc}"
            )
            result.recruiter_feedback.append(
                "LangChain reviewer was unavailable, so ApplyTeX ATS used the built-in reviewer loop."
            )
            review = await _generate_recruiter_review(
                result=result,
                parse_result=parse_result,
                job_description=job_description,
                resume_plain_text=resume_plain_text,
                llm_backend=review_route.backend,
                llm_model=review_route.model,
                reviewer_backend="custom",
            )
        feedback = review.get("feedback")
        if isinstance(feedback, str) and feedback.strip():
            result.recruiter_feedback.append(feedback.strip())

        applied = await _apply_recruiter_review_changes(
            result,
            parse_result,
            review.get("changes", []),
            llm_backend=refine_route.backend,
            llm_model=refine_route.model,
        )
        iterations += 1
        result.recruiter_iteration_count = iterations
        if not applied:
            break

    return iterations


async def _apply_recruiter_review_changes(
    result: OptimizationResult,
    parse_result: ParseResult,
    raw_changes: Any,
    *,
    llm_backend: str | None,
    llm_model: str | None,
) -> bool:
    """Validate and apply recruiter-review changes only when they improve fit."""
    if not isinstance(raw_changes, list) or result.ats_after is None:
        return False

    prepared = _prepare_raw_changes_for_validation(raw_changes, parse_result.stmt_index)
    accepted, rejected = validate_changes(prepared, parse_result.stmt_index)
    accepted, budget_rejected = _filter_wordy_changes_for_page_budget(
        accepted,
        parse_result.doc.page_budget,
    )
    if budget_rejected:
        compacted, compact_rejected = await _compact_page_budget_rejections(
            budget_rejected,
            parse_result=parse_result,
            page_budget=parse_result.doc.page_budget,
            job_keywords=result.job_keywords,
            ats_before=result.ats_after,
            skill_target_plan=result.skill_target_plan,
            confirmed_skills=result.confirmed_skills,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        accepted.extend(compacted)
        rejected.extend(compact_rejected)
        result.compacted_changes.extend(compacted)
    result.rejected_changes.extend(rejected)

    if not accepted:
        return False

    current_changes = dict(result.validated_changes)
    current_score = result.ats_after.score
    trial_changes = {**current_changes, **{c["stmt_id"]: c["value"] for c in accepted}}
    trial = _evaluate_change_set(
        parse_result,
        trial_changes,
        result.job_keywords,
        result.confirmed_skills,
    )
    if trial["ats"].score < current_score:
        return False

    result.validated_changes = trial_changes
    result.modified_latex = trial["latex"]
    result.ats_after = trial["ats"]
    _record_render(result, trial["render"])
    for change in accepted:
        stmt_id = change["stmt_id"]
        span = parse_result.stmt_index[stmt_id]
        _upsert_diff_change(
            result,
            stmt_id,
            span.original_text,
            change["value"],
            "[Recruiter review] " + str(change.get("reason", "")),
        )

    ats_remediation = _build_ats_remediation(
        result.ats_after,
        result.skill_target_plan,
        result.confirmed_skills,
    )
    if result.overflow:
        await _compact_overflowing_applied_changes(
            result,
            parse_result,
            result.job_keywords,
            ats_remediation=ats_remediation,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        _repair_overflow_by_pruning_low_roi_changes(
            result,
            parse_result,
            result.job_keywords,
        )

    return result.ats_after.score >= current_score and not result.overflow


def _evaluate_change_set(
    parse_result: ParseResult,
    changes: dict[str, str],
    job_keywords: dict[str, Any],
    confirmed_skills: list[str] | None = None,
) -> dict[str, Any]:
    """Return latex/render/ATS artifacts for a proposed change set."""
    latex = reconstruct(parse_result, changes).latex if changes else parse_result.latex_source
    render = check_one_page(latex)
    parsed = _reparse(latex, resume_id=parse_result.doc.resume_id)
    plain_text = _build_plain_text(extract_full_resume(parsed))
    ats = check_ats(plain_text, job_keywords, confirmed_skills=confirmed_skills)
    return {"latex": latex, "render": render, "ats": ats}


def _overflow_prune_priority(stmt_id: str) -> int:
    """Prefer pruning prose before skills when score impact is equal."""
    if stmt_id.startswith(("summary", "work", "proj")):
        return 0
    if stmt_id.startswith("skills"):
        return 1
    return 2


def _finish_trace(
    tracer: PipelineTracer,
    result: OptimizationResult,
    error: BaseException | None = None,
) -> None:
    """Close tracing and copy trace metadata back to the result."""
    tracer.finish(outputs=_trace_result_summary(result), error=error)
    result.stage_latencies_ms = dict(tracer.stage_latencies_ms)
    result.trace_id = tracer.trace_id


def _trace_result_summary(result: OptimizationResult) -> dict[str, Any]:
    """Return a compact, PII-light summary for LangSmith/local analytics."""
    return {
        "trace_id": result.trace_id,
        "optimization_strategy": result.optimization_strategy,
        "reviewer_backend": result.reviewer_backend,
        "model_routes": result.model_routes,
        "score_before": result.ats_before.score if result.ats_before else None,
        "score_after": result.ats_after.score if result.ats_after else None,
        "raw_score_before": result.ats_before.raw_score if result.ats_before else None,
        "raw_score_after": result.ats_after.raw_score if result.ats_after else None,
        "excluded_unconfirmed_skills": (
            result.ats_after.excluded_unconfirmed_skills if result.ats_after else []
        ),
        "score_delta": (
            round(result.ats_after.score - result.ats_before.score, 1)
            if result.ats_before and result.ats_after
            else None
        ),
        "ats_target_met": result.ats_target_met,
        "ats_target_score": result.ats_target_score,
        "change_count": len(result.diff),
        "validated_change_count": len(result.validated_changes),
        "rejected_change_count": len(result.rejected_changes),
        "confirmation_required_count": len(result.confirmation_required_skills),
        "recruiter_iteration_count": result.recruiter_iteration_count,
        "recruiter_feedback_count": len(result.recruiter_feedback),
        "warning_count": len(result.warnings),
        "page_count": result.page_count,
        "overflow": result.overflow,
        "visual_overflow": result.visual_overflow,
        "min_text_baseline_pt": result.min_text_baseline_pt,
    }


def _record_render(result: OptimizationResult, check: RenderResult) -> None:
    """Copy a :class:`RenderResult` onto *result*, surfacing compile failures.

    Handles the three outcomes of :func:`check_one_page`:

    * **word-count fallback** (``estimated``) — no engine present; record the
      page estimate but leave ``pdf_bytes`` unset.
    * **success** — record page count and the rendered bytes (also for the
      overflow case, where ``ok`` is still ``True`` with ``page_count > 1``).
    * **compile failure** — the modified LaTeX did not compile.  Leave
      ``pdf_bytes`` unset and append a warning so the failure is never silently
      reported as a passing one-page check.
    """
    result.overflow = check.overflow
    result.visual_overflow = check.visual_overflow
    result.min_text_baseline_pt = check.min_text_baseline_pt
    result.page_count = check.page_count

    if check.estimated:
        return
    if check.ok:
        result.pdf_bytes = check.pdf_bytes
        return

    detail = check.error or "unknown error"
    msg = (
        f"PDF render failed ({detail}) — the modified LaTeX did not compile. "
        "The one-page check could not be verified."
    )
    result.warnings.append(msg)
    logger.warning("[optimizer] %s", msg)


def _record_ats_target_status(result: OptimizationResult) -> None:
    """Set 80+ target status and warn when truthful optimization falls short."""
    if result.ats_after is None:
        result.ats_target_met = False
        return
    result.ats_target_met = result.ats_after.score >= result.ats_target_score
    if not result.ats_target_met:
        blocker_note = ""
        if result.confirmation_required_skills:
            blocker_note = (
                " Remaining score is blocked by unconfirmed required/technical "
                "skills: " + ", ".join(result.confirmation_required_skills) + "."
            )
        elif result.ats_after.excluded_unconfirmed_skills:
            blocker_note = (
                " Unconfirmed hard tools were excluded from the submission score "
                "instead of fabricated: "
                + ", ".join(result.ats_after.excluded_unconfirmed_skills)
                + "."
            )
        result.warnings.append(
            f"Submission fit target not met: {result.ats_after.score:.1f}/100 "
            f"(target {result.ats_target_score:.0f}+). "
            "The app has already auto-handled supported JD wording."
            f"{blocker_note} Do not submit automatically; add only truthful skills "
            "or use human outreach."
        )


def _fallback_to_original(
    result: OptimizationResult,
    parse_result: ParseResult,
) -> None:
    """Return the original resume as the artifact when optimization cannot finish."""
    result.modified_latex = parse_result.latex_source
    _record_render(result, check_one_page(result.modified_latex))
    _record_ats_target_status(result)


def _compact_text(text: str, max_chars: int) -> str:
    """Collapse whitespace and cap long prompt fields without cutting mid-word."""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    cut = clean[:max_chars].rsplit(" ", 1)[0].rstrip()
    return cut + " ..."


def _format_page_budget(page_budget: PageBudget | None) -> str:
    """Return compact instructions for keeping the optimized resume to one page."""
    if page_budget is None:
        return (
            "- Treat this as a one-page resume. Prefer replacing words over "
            "adding clauses."
        )

    words = page_budget.estimated_word_count
    max_words = page_budget.max_word_budget
    bullets = page_budget.estimated_bullet_count
    max_bullets = page_budget.max_bullet_budget
    remaining = max_words - words
    tight = remaining <= 40 or bullets >= max_bullets
    mode = (
        "TIGHT: do not increase total resume length; every prose edit should "
        "be equal length or shorter."
        if tight
        else "MODERATE: keep total added wording very small and prefer concise replacements."
    )
    return "\n".join(
        [
            f"- Estimated editable words: {words}/{max_words} ({remaining:+d} remaining).",
            f"- Estimated bullets/statements: {bullets}/{max_bullets}.",
            f"- Budget mode: {mode}",
            "- Add confirmed skills only as concise comma-separated skill items.",
            "- For summary/work/project statements, replace generic wording with JD keywords; do not append long clauses.",
        ]
    )


def _plain_word_count(text: str) -> int:
    """Count words in LaTeX-ish text after removing common commands."""
    clean = re.sub(r"\\textbf\s*\{([^{}]*)\}", r" \1 ", text)
    clean = re.sub(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}", r" \1 ", clean)
    clean = re.sub(r"\\[a-zA-Z]+\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}", r" \1 ", clean)
    clean = re.sub(r"\\[a-zA-Z]+", " ", clean)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+\-/#.]*", clean))


def _allowed_words_for_change(change: dict[str, Any], page_budget: PageBudget) -> int:
    """Return the statement-level word cap for a change under current page pressure."""
    remaining_words = page_budget.max_word_budget - page_budget.estimated_word_count
    tight = (
        remaining_words <= 40
        or page_budget.estimated_bullet_count >= page_budget.max_bullet_budget
    )
    original_words = _plain_word_count(str(change.get("original", "")))
    if tight:
        return max(original_words + 2, int(original_words * 1.08))
    return max(original_words + 6, int(original_words * 1.20))


def _filter_wordy_changes_for_page_budget(
    changes: list[dict[str, Any]],
    page_budget: PageBudget,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reject prose rewrites that expand too much for a one-page resume.

    Skills lines are exempt because confirmed skills often need short comma-item
    additions. Work/project/summary edits are held to a stricter limit when the
    parser estimates that the resume is already close to the one-page budget.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for change in changes:
        stmt_id = str(change.get("stmt_id", ""))
        if stmt_id.startswith("skills_"):
            accepted.append(change)
            continue

        new_words = _plain_word_count(str(change.get("value", "")))
        allowed_words = _allowed_words_for_change(change, page_budget)

        if new_words > allowed_words:
            rejected.append(
                {
                    **change,
                    "rejection_reason": (
                        "rewrite expands prose beyond the one-page page budget "
                        f"({new_words} words > allowed {allowed_words}) for '{stmt_id}'"
                    ),
                }
            )
            continue

        accepted.append(change)

    return accepted, rejected


def _confirmed_skills_from_env() -> set[str]:
    raw = os.environ.get("SMARTJOBAPPLY_CONFIRMED_SKILLS", "")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


_NON_SKILL_PHRASE_HINTS = (
    "experience",
    "use case",
    "use cases",
    "platform",
    "platforms",
    "communication",
    "stakeholder",
    "healthcare",
    "ecommerce",
    "e-commerce",
    "industry",
    "business",
    "customer",
)


def _expand_skill_candidate(skill: str) -> list[str]:
    """Normalize noisy ATS requirement phrases into concrete skill/tool options."""
    stripped = skill.strip()
    norm = stripped.lower()
    if "azure" in norm and ("cloud" in norm or "platform" in norm):
        return ["Azure"]
    if "aws" in norm and ("cloud" in norm or "platform" in norm):
        return ["AWS"]
    if "gcp" in norm and ("cloud" in norm or "platform" in norm):
        return ["GCP"]
    if "vs code" in norm and ("codex" in norm or "copilot" in norm or "claude code" in norm):
        # The JD may say "VS Code with GitHub Copilot or Codex or Claude Code".
        # Ask the user about the concrete editor/tool pairing rather than the
        # whole prose phrase.
        return ["VS Code", "Codex"]
    return [stripped] if stripped else []


def _is_skill_confirmation_candidate(skill: str) -> bool:
    """Return True when a missing phrase is suitable for a skills-line checkbox."""
    norm = skill.lower().strip()
    if not norm:
        return False
    if norm.startswith("conversational ai for "):
        return False
    if any(hint in norm for hint in _NON_SKILL_PHRASE_HINTS):
        # Keep concise technical nouns like "Digital Twin" if they appear later;
        # these current hints are mostly JD context/industry phrases.
        return False
    if len(norm.split()) > 5:
        return False
    return True


def build_skill_confirmation_candidates(missing_items: list[str]) -> list[str]:
    """Return clean skill/tool candidates to ask the user to confirm."""
    candidates, _non_skill_gaps = split_skill_confirmation_candidates(missing_items)
    return candidates


def split_skill_confirmation_candidates(
    missing_items: list[str],
) -> tuple[list[str], list[str]]:
    """Split missing ATS items into skill candidates and non-skill JD themes."""
    out: list[str] = []
    seen: set[str] = set()
    non_skill_gaps: list[str] = []
    for item in missing_items:
        accepted_from_item = False
        for candidate in _expand_skill_candidate(item):
            if not _is_skill_confirmation_candidate(candidate):
                continue
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                out.append(candidate)
            accepted_from_item = True
        if not accepted_from_item:
            non_skill_gaps.append(item)
    return out, non_skill_gaps


def _filter_confirmed_patch_skills(
    skills: list[str],
    confirmed_skills: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split missing skills into confirmed-to-add and skipped-for-confirmation."""
    if AUTO_PATCH_UNCONFIRMED_SKILLS:
        return skills, []

    confirmed = {s.strip().lower() for s in (confirmed_skills or []) if s.strip()}
    confirmed.update(_confirmed_skills_from_env())
    if not confirmed:
        return [], skills

    allowed: list[str] = []
    skipped: list[str] = []
    for skill in skills:
        (allowed if skill.lower() in confirmed else skipped).append(skill)
    return allowed, skipped


# ---------------------------------------------------------------------------
# Deterministic skills patcher (post-LLM fallback)
# ---------------------------------------------------------------------------

# Maps lower-case keywords found inside a skill name to the substring that
# identifies the right skills-line category.  Checked in order; first match wins.
_SKILL_CATEGORY_HINTS: list[tuple[list[str], str]] = [
    # Gen-AI / Agents line
    (
        ["langchain", "langgraph", "crewai", "openai", "claude", "gemini",
         "autogen", "smolagent", "semantic kernel", "mcp"],
        "gen ai",
    ),
    # Frameworks / Libraries line
    (
        ["hugging face", "huggingface", "pytorch", "tensorflow", "keras",
         "fastapi", "llamaindex", "llama index", "streamlit", "mlflow",
         "scikit", "sklearn", "pandas", "numpy", "matplotlib",
         "ml pipeline", "ml pipelines", "machine learning pipeline",
         "machine learning pipelines", "inference service",
         "inference services", "model inference", "model serving"],
        "framework",
    ),
    # Frameworks / Libraries line — version-control tools live here
    (["github", "git", "docker", "kubernetes", "ci/cd", "cicd"], "framework"),
    # Cloud / APIs line — IDE tools + cloud
    (
        ["vs code", "vscode", "copilot", "claude code", "codex",
         "postman", "pinecone", "chromadb", "aws", "gcp",
         "bedrock", "amazon bedrock", "vertex ai", "google vertex ai"],
        "cloud",
    ),
    (["azure"], "cloud"),
    # Languages line
    (["python", "java", "sql", "javascript", "typescript", "go", "rust"], "lang"),
]


def _best_skills_stmt_id(
    skill: str,
    skills_map: dict[str, dict[str, str]],
) -> str | None:
    """Return the stmt_id of the skills line most appropriate for *skill*.

    *skills_map* is ``{stmt_id: {"category": lower_cat, "items": raw_items}}``.
    """
    norm = skill.lower()
    for kw_list, category_fragment in _SKILL_CATEGORY_HINTS:
        if any(kw in norm for kw in kw_list):
            for sid, info in skills_map.items():
                if category_fragment in info["category"]:
                    return sid
            break  # matched a hint bucket but no line found → fall through

    # Fallback: frameworks line, then any line
    for sid, info in skills_map.items():
        if "framework" in info["category"] or "librar" in info["category"]:
            return sid
    return next(iter(skills_map), None)


def _parse_skills_line(latex_text: str) -> tuple[str, str, str] | None:
    """Split a skills LaTeX line into (prefix, items, suffix).

    The expected format is ``\\textbf{Category}{: item1, item2, ...}``.
    Returns ``None`` when the text doesn't match the expected shape.
    """
    # Locate the first `{: ` which introduces the items group
    marker = "{: "
    idx = latex_text.find(marker)
    if idx == -1:
        marker = "{:"
        idx = latex_text.find(marker)
    if idx == -1:
        return None

    prefix = latex_text[: idx + len(marker)]  # everything up to and including `{: `
    rest = latex_text[idx + len(marker):]

    # The items run up to the LAST `}` (closing the second brace group)
    close = rest.rfind("}")
    if close == -1:
        return None

    items = rest[:close]
    suffix = rest[close:]  # `}` and anything after
    return prefix, items, suffix


def _deterministic_skills_patch(
    missing_required: list[str],
    parse_result: ParseResult,
    editable_data: dict[str, Any],
    existing_changes: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build ``{stmt_id: new_latex}`` that adds *missing_required* skills.

    For each skill:
    1. Identify the best-matching skills line via category heuristics.
    2. Append the skill to that line's comma-separated items.
       Special-case: if "GitHub" is missing but "Git" is already in the items,
       insert "GitHub" immediately after "Git" instead of appending.
    3. Return the new full LaTeX text for every modified stmt_id.

    *existing_changes* — if provided, the patcher starts from the already-modified
    LaTeX text for any skills stmt_id that was changed by the LLM in Stage 4,
    rather than the original parsed text.  This prevents the patcher from
    inadvertently erasing skills the LLM added (e.g. Langchain to skills_2).

    Returns an empty dict when there is nothing to patch.
    """
    if not missing_required:
        return {}

    # Build skills_map: stmt_id → {category, items}
    # Use the LLM-modified version of each skills line when available.
    skills_map: dict[str, dict[str, str]] = {}
    skills_raw: dict[str, str] = (
        editable_data.get("editable", {}).get("skills", {})
    )
    for stmt_id, latex_text in skills_raw.items():
        # Start from the LLM's already-modified version of this line if it exists
        effective_latex = (
            existing_changes.get(stmt_id, latex_text)
            if existing_changes else latex_text
        )
        parsed = _parse_skills_line(effective_latex)
        if parsed is None:
            continue
        prefix, items, suffix = parsed
        # Extract a normalised category from the prefix for heuristic matching
        cat_match = re.search(r"\\textbf\s*\{([^}]+)\}", prefix)
        category = cat_match.group(1).lower() if cat_match else prefix.lower()
        skills_map[stmt_id] = {
            "category": category,
            "items": items.strip(),
            "prefix": prefix,
            "suffix": suffix,
        }

    if not skills_map:
        return {}

    # Accumulate additions per stmt_id
    additions: dict[str, list[str]] = {}
    for skill in missing_required:
        sid = _best_skills_stmt_id(skill, skills_map)
        if sid is None:
            logger.warning("[skills-patch] No target line found for skill %r", skill)
            continue
        additions.setdefault(sid, []).append(skill)

    # Build new LaTeX text for each modified line
    result: dict[str, str] = {}
    for sid, new_skills in additions.items():
        info = skills_map[sid]
        items = info["items"]

        for skill in new_skills:
            # Special-case: insert "GitHub" directly after "Git" so they stay together
            if skill.lower() == "github" and re.search(r"\bGit\b", items):
                items = re.sub(r"\bGit\b", "Git, GitHub", items, count=1)
            else:
                items = items.rstrip(", ") + f", {skill}"

        result[sid] = info["prefix"] + items + info["suffix"]
        logger.info(
            "[skills-patch] %s: added %s",
            sid, ", ".join(repr(s) for s in new_skills),
        )

    return result


def _build_ats_remediation(
    ats_before: ATSResult,
    skill_target_plan: dict[str, Any],
    confirmed_skills: list[str] | None = None,
) -> str:
    """Build a targeted ATS gap-remediation block for the Stage 4 diff prompt.

    Returns a formatted multi-line string (leading + trailing newline) listing:
    - Every required skill with zero ATS coverage → must be added to skills lines.
    - Preferred skills already validated by the Stage 2 plan → add if truthful.
    - JD keyword phrases still absent from the resume → weave into bullets.

    Returns an empty string when there are no actionable gaps, so the prompt
    template can interpolate it without adding spurious blank lines.
    """
    confirmed = {
        s.strip().lower()
        for s in (confirmed_skills or [])
        if isinstance(s, str) and s.strip()
    }
    required_missing: list[str] = ats_before.required_missing
    keyword_misses: list[str] = ats_before.keyword_misses

    # Only surface preferred skills the Stage 2 plan already flagged as targets
    # (filters out domain-experience items like "healthcare" that can't be added)
    plan_skill_names: set[str] = {
        s["skill"].lower()
        for s in skill_target_plan.get("target_skills", [])
        if isinstance(s, dict) and "skill" in s
    }
    preferred_actionable: list[str] = [
        s for s in ats_before.preferred_missing
        if s.lower() in plan_skill_names
    ]
    skill_gaps = list(dict.fromkeys(required_missing + preferred_actionable))
    confirmed_skill_gaps = [s for s in skill_gaps if s.lower() in confirmed]
    unconfirmed_skill_gaps = [s for s in skill_gaps if s.lower() not in confirmed]

    if not confirmed_skill_gaps and not unconfirmed_skill_gaps and not keyword_misses:
        return ""

    lines: list[str] = [
        "",
        "══════════ SUBMISSION FIT GAP — REQUIRED CHANGES ══════════",
        f"Current submission fit: {ats_before.score:.0f}/100  (target: 80+).",
        "The changes listed below have the highest score-per-edit ROI.",
        "You MUST address ALL items in this block.",
    ]

    if confirmed_skill_gaps:
        lines += [
            "",
            "USER-CONFIRMED SKILLS — the user selected these as truthful for",
            "this resume. Add each once to the most appropriate existing skills",
            "line and weave naturally into bullets only where the existing work",
            "supports it (Rule 6 applies). Examples:",
            "  LangChain / LangGraph → Gen AI & Agents line",
            "  Hugging Face Transformers → Frameworks & Libraries line",
            "  GitHub → Frameworks & Libraries line (alongside Git)",
            "  VS Code / Claude Code / GitHub Copilot → Cloud & APIs line",
        ]
        for skill in confirmed_skill_gaps:
            lines.append(f"  • {skill}")

    if unconfirmed_skill_gaps:
        lines += [
            "",
            "UNCONFIRMED SKILL GAPS — do NOT add these to skills or bullets.",
            "They require user confirmation before they are treated as truthful:",
        ]
        for skill in unconfirmed_skill_gaps:
            lines.append(f"  • {skill}")

    if keyword_misses:
        lines += [
            "",
            "JD KEYWORD PHRASES NOT YET IN THE RESUME — weave at least two",
            "into appropriate work bullets or the summary (only where truthful):",
            "  Hints: enterprise AI deployments → 'cloud-native environments';",
            "  XAI / interpretability work → 'responsible AI' or 'ethical AI';",
            "  RAG / agentic pipelines → 'AI-first development approach'.",
        ]
        for kw in keyword_misses:
            lines.append(f"  • \"{kw}\"")

    lines += [
        "═══════════════════════════════════════════════════════════════",
        "",
    ]
    return "\n".join(lines)


def _upsert_diff_change(
    result: OptimizationResult,
    stmt_id: str,
    original: str,
    value: str,
    reason: str,
) -> None:
    """Insert or update a displayed diff entry for a statement."""
    for change in result.diff:
        if change.get("stmt_id") == stmt_id:
            change["value"] = value
            existing_reason = str(change.get("reason", ""))
            if reason not in existing_reason:
                change["reason"] = (existing_reason + " " + reason).strip()
            return
    result.diff.append(
        {
            "stmt_id": stmt_id,
            "original": original,
            "value": value,
            "reason": reason,
        }
    )


def _apply_score_aware_skills_patch(
    result: OptimizationResult,
    parse_result: ParseResult,
    editable_data: dict[str, Any],
    skills_to_patch: list[str],
) -> int:
    """Patch confirmed skills one at a time, stopping once target+page fit."""
    current_changes = dict(result.validated_changes)
    patched_count = 0
    skipped_for_page: list[str] = []

    for skill in skills_to_patch:
        if (
            result.ats_after is not None
            and result.ats_after.score >= result.ats_target_score
            and not result.overflow
        ):
            break

        skill_patch = _deterministic_skills_patch(
            [skill],
            parse_result,
            editable_data,
            existing_changes=current_changes,
        )
        if not skill_patch:
            continue

        trial_changes = {**current_changes, **skill_patch}
        trial = _evaluate_change_set(
            parse_result,
            trial_changes,
            result.job_keywords,
            result.confirmed_skills,
        )
        current_score = result.ats_after.score if result.ats_after else 0.0

        if not result.overflow and trial["render"].overflow:
            skipped_for_page.append(skill)
            continue
        if trial["ats"].score <= current_score:
            continue

        current_changes = trial_changes
        result.validated_changes = current_changes
        result.modified_latex = trial["latex"]
        result.ats_after = trial["ats"]
        _record_render(result, trial["render"])
        patched_count += 1

        for sid, new_val in skill_patch.items():
            span = parse_result.stmt_index.get(sid)
            _upsert_diff_change(
                result,
                sid,
                span.original_text if span else "",
                new_val,
                "[ATS patch] added confirmed skill with page-budget check",
            )

        logger.info(
            "[optimizer] ATS after skill patch %r: %.0f/100  (req %.0f%% | pref %.0f%% | kw %.0f%%)",
            skill,
            result.ats_after.score,
            result.ats_after.required_score,
            result.ats_after.preferred_score,
            result.ats_after.keyword_score,
        )

    if skipped_for_page:
        result.warnings.append(
            "Skipped confirmed skill patch(es) because they would overflow one page: "
            + ", ".join(skipped_for_page)
        )

    return patched_count


def _apply_supported_keyword_equivalence_patch(
    result: OptimizationResult,
    parse_result: ParseResult,
    editable_data: dict[str, Any],
    *,
    optimization_strategy: OptimizerStrategy = DEFAULT_OPTIMIZER_STRATEGY,
) -> dict[str, str]:
    """Automatically weave supported JD phrases without asking the user.

    This is aggressive only where evidence exists: it rewrites text when the
    resume already contains a close supporting claim. It never patches missing
    concrete tools/platforms such as Bedrock or Vertex AI; those stay behind the
    user's skills confirmation gate.
    """
    if result.ats_after is None:
        return {}

    current_changes = dict(result.validated_changes)
    applied: dict[str, str] = {}
    strategy_allows_equal_score = optimization_strategy in {
        "ats_aggressive",
        "one_page_strict",
        "supported_inference_ats_80_one_page",
    }

    def current_text(stmt_id: str) -> str:
        span = parse_result.stmt_index[stmt_id]
        return current_changes.get(stmt_id, span.original_text)

    def try_patch(
        stmt_id: str,
        value: str,
        reason: str,
        *,
        allow_equal_score: bool = False,
    ) -> bool:
        if stmt_id not in parse_result.stmt_index:
            return False
        if value == current_text(stmt_id):
            return False
        trial_changes = {**current_changes, stmt_id: value}
        trial = _evaluate_change_set(
            parse_result,
            trial_changes,
            result.job_keywords,
            result.confirmed_skills,
        )
        current_score = result.ats_after.score if result.ats_after else 0.0
        if trial["ats"].score < current_score:
            return False
        if trial["ats"].score == current_score and (
            not allow_equal_score or not strategy_allows_equal_score
        ):
            return False
        if not result.overflow and trial["render"].overflow:
            return False

        current_changes.update(trial_changes)
        result.validated_changes = current_changes
        result.modified_latex = trial["latex"]
        result.ats_after = trial["ats"]
        _record_render(result, trial["render"])
        applied[stmt_id] = value
        span = parse_result.stmt_index[stmt_id]
        _upsert_diff_change(result, stmt_id, span.original_text, value, reason)
        return True

    gaps = {
        g.lower()
        for g in (
            list(result.ats_after.keyword_misses)
            + list(result.ats_after.preferred_missing)
            + list(result.ats_after.required_missing)
            + [str(s) for s in result.job_keywords.get("keywords", [])]
            + [str(s) for s in result.job_keywords.get("preferred_skills", [])]
            + [str(s) for s in result.job_keywords.get("required_skills", [])]
        )
    }

    summary_id = "summary_0"
    if summary_id in parse_result.stmt_index:
        text = current_text(summary_id)
        if "communication skills" in gaps and "translating business data" in text:
            try_patch(
                summary_id,
                text.replace(
                    "translating business data",
                    "communicating AI/ML concepts and translating business data",
                    1,
                ),
                "[Auto keyword] framed existing business translation experience as communication skills",
                allow_equal_score=True,
            )
            text = current_text(summary_id)
        if "cloud-native" in gaps and "scalable pipelines" in text:
            try_patch(
                summary_id,
                text.replace("scalable pipelines", "cloud-native scalable pipelines", 1),
                "[Auto keyword] connected existing scalable pipeline work to cloud-native JD wording",
                allow_equal_score=True,
            )
            text = current_text(summary_id)
        if (
            ("cloud platform" in gaps or "cloud" in gaps)
            and "azure" in text.lower()
            and "cloud platform" not in text.lower()
        ):
            try_patch(
                summary_id,
                re.sub(r"\bAzure\b", "Azure cloud platform", text, count=1),
                "[Auto keyword] mapped existing Azure evidence to cloud platform wording",
                allow_equal_score=True,
            )

    for stmt_id in parse_result.stmt_index:
        if stmt_id.startswith("skills_"):
            continue
        text = current_text(stmt_id)
        lower = text.lower()

        if (
            "api development" in gaps
            and ("fastapi" in lower or "postman" in lower or "api" in lower)
            and "api development" not in lower
        ):
            value = (
                re.sub(r"\bFastAPI\b", "FastAPI API development", text, count=1)
                if "fastapi" in lower
                else re.sub(r"\bAPIs?\b", "API development", text, count=1)
            )
            try_patch(
                stmt_id,
                value,
                "[Auto keyword] mapped existing FastAPI/API evidence to API development wording",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            ("cloud platform" in gaps or "cloud-native" in gaps or "cloud" in gaps)
            and ("azure" in lower or "pinecone" in lower or "vector" in lower)
            and "cloud platform" not in lower
            and "cloud-native" not in lower
        ):
            value = (
                re.sub(r"\bAzure\b", "Azure cloud platform", text, count=1)
                if "azure" in lower
                else text.replace("scalable", "cloud-native scalable", 1)
                if "scalable" in lower
                else text
            )
            try_patch(
                stmt_id,
                value,
                "[Auto keyword] mapped existing cloud/vector evidence to cloud platform wording",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            ("model monitoring" in gaps or "ml lifecycle" in gaps)
            and ("mlflow" in lower or "model evaluation" in lower or "model review" in lower)
            and "model monitoring" not in lower
            and "ml lifecycle" not in lower
        ):
            value = (
                re.sub(r"\bMLflow\b", "MLflow model monitoring", text, count=1)
                if "mlflow" in lower
                else text.replace("model evaluation", "model evaluation and monitoring", 1)
                if "model evaluation" in lower
                else text.replace("model review", "model review and monitoring", 1)
            )
            try_patch(
                stmt_id,
                value,
                "[Auto keyword] mapped existing MLflow/model evaluation evidence to model monitoring wording",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            "summarization" in gaps
            and "llm-generated answers" in lower
            and "summarization" not in lower
        ):
            try_patch(
                stmt_id,
                re.sub(
                    r"LLM-generated answers",
                    "LLM-generated summarization and answers",
                    text,
                    count=1,
                ),
                "[Auto keyword] added summarization to an existing LLM-generated answers project",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            "llm applications" in gaps
            and ("rag" in lower or "llm" in lower)
            and "llm applications" not in lower
        ):
            try_patch(
                stmt_id,
                re.sub(r"\bLLM\b", "LLM applications", text, count=1)
                if "llm" in lower
                else re.sub(r"\bRAG\b", "RAG/LLM applications", text, count=1),
                "[Auto keyword] mapped existing RAG/LLM evidence to LLM applications wording",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            ("conversational workflows" in gaps or "conversational ai" in gaps)
            and "ai chatbot prototypes" in lower
            and "conversational" not in lower
        ):
            try_patch(
                stmt_id,
                re.sub(
                    r"AI chatbot prototypes",
                    "conversational AI workflow and AI chatbot prototypes",
                    text,
                    count=1,
                ),
                "[Auto keyword] aligned existing chatbot work with conversational workflow wording",
                allow_equal_score=True,
            )
            text = current_text(stmt_id)
            lower = text.lower()

        if (
            ("bias assessment" in gaps or "transparency" in gaps or "responsible ai" in gaps)
            and ("xai" in lower or "model interpretability" in lower)
            and "bias assessment" not in lower
            and "transparency" not in lower
            and "responsible ai" not in lower
        ):
            try_patch(
                stmt_id,
                text.replace(
                    r"\textbf{XAI framework}",
                    r"\textbf{Responsible AI/XAI framework} for transparency and bias assessment",
                    1,
                )
                if r"\textbf{XAI framework}" in text
                else text.replace(
                    "model interpretability",
                    "responsible AI model interpretability, transparency, and bias assessment",
                    1,
                ),
                "[Auto keyword] mapped existing XAI/model interpretability work to Responsible AI keywords",
                allow_equal_score=True,
            )

    if applied:
        logger.info(
            "[optimizer] Automatic keyword equivalence patch applied to: %s",
            ", ".join(applied),
        )

    return applied


def _build_plain_text(full_data: dict[str, Any]) -> str:
    """Convert the full resume dict to a compact plain-text representation."""
    lines: list[str] = []

    pi = full_data.get("personal_info", {})
    if pi.get("name"):
        lines.append(pi["name"])

    if summary := full_data.get("summary"):
        lines.append(f"\nSUMMARY\n{summary}")

    for job in full_data.get("work_experience", []):
        lines.append(
            f"\n{job.get('company', '')} | {job.get('role', '')} "
            f"({job.get('start_date', '')} – {job.get('end_date', '')})"
        )
        for b in job.get("bullets", []):
            lines.append(f"  • {b}")

    for proj in full_data.get("projects", []):
        lines.append(f"\n{proj.get('title', '')}")
        for b in proj.get("bullets", []):
            lines.append(f"  • {b}")

    skills = full_data.get("skills", {})
    if skills:
        lines.append("\nSKILLS")
        if isinstance(skills, dict):
            for cat, items in skills.items():
                lines.append(f"  {cat}: {items}")
        else:
            for item in skills:
                lines.append(f"  {item}")

    for pub in full_data.get("publications", []):
        lines.append(f"\n{pub.get('title', '')} ({pub.get('venue', '')})")
        for b in pub.get("bullets", []):
            lines.append(f"  • {b}")

    return "\n".join(lines)
