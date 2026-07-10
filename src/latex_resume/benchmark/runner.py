"""Cached deterministic and provider-backed benchmark execution."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from latex_resume.ats import check_ats
from latex_resume.benchmark.audit import audit_optimized_resume
from latex_resume.benchmark.corpus import load_evidence
from latex_resume.benchmark.io import (
    CACHE_DIR,
    CASES_PATH,
    JOB_MANIFEST,
    RESULTS_DIR,
    ROOT,
    RUNS_PATH,
    RESUME_MANIFEST,
    append_jsonl,
    read_jsonl,
    relative_path,
    sha256_text,
    stable_id,
)
from latex_resume.benchmark.models import (
    BenchmarkCase,
    BenchmarkRun,
    JobFixture,
    ResumeFixture,
    utc_now,
)
from latex_resume.extractor import extract_full_resume
from latex_resume.llm import CODEX_MODEL, GROQ_MODEL, get_usage, reset_usage
from latex_resume.llm_routing import LLMTaskRoute
from latex_resume.optimizer import _build_plain_text, run_optimization_pipeline
from latex_resume.parser import parse
from latex_resume.reconstructor import apply_changes
from latex_resume.renderer import check_one_page

PROMPT_VERSION = "benchmark-supported-inference-v1"


async def optimize_cases(
    providers: Iterable[str] = ("groq", "codex"),
    *,
    limit: int | None = None,
    include_holdout: bool = False,
    force: bool = False,
    concurrency: int = 1,
    progress: Callable[[str], None] | None = None,
) -> list[BenchmarkRun]:
    """Run selected cases through deterministic and requested provider routes."""
    cases = read_jsonl(CASES_PATH, BenchmarkCase)
    if not include_holdout:
        cases = [case for case in cases if not case.holdout]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("No selected cases found. Run `select-cases` first.")
    normalized_providers = [provider.strip().lower() for provider in providers]
    invalid = set(normalized_providers) - {"groq", "codex", "deterministic"}
    if invalid:
        raise ValueError(f"Unsupported providers: {', '.join(sorted(invalid))}")

    resumes = {item.resume_id: item for item in read_jsonl(RESUME_MANIFEST, ResumeFixture)}
    jobs = {item.job_id: item for item in read_jsonl(JOB_MANIFEST, JobFixture)}
    existing = {
        item.cache_key: item
        for item in read_jsonl(RUNS_PATH, BenchmarkRun)
        if item.status == "success" and not _provider_degraded(item)
    }
    output: list[BenchmarkRun] = []

    for case in cases:
        deterministic = _run_deterministic_baseline(
            case,
            resumes[case.resume_id],
            jobs[case.job_id],
            force=force,
            existing=existing,
        )
        output.append(deterministic)

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_provider(case: BenchmarkCase, provider: str) -> BenchmarkRun:
        async with semaphore:
            return await _run_provider_case(
                case,
                resumes[case.resume_id],
                jobs[case.job_id],
                provider,
                force=force,
                existing=existing,
                progress=progress,
            )

    model_providers = [
        provider for provider in normalized_providers
        if provider in {"groq", "codex"}
    ]
    total_provider_runs = len(cases) * len(model_providers)
    code_version = _code_version()
    cached_provider_runs = sum(
        1
        for case in cases
        for provider in model_providers
        if _cache_key(
            case,
            resumes[case.resume_id],
            jobs[case.job_id],
            provider,
            GROQ_MODEL if provider == "groq" else CODEX_MODEL,
            code_version,
        ) in existing
    )
    if progress:
        progress(
            f"Prepared {len(cases)} cases and {total_provider_runs} provider runs. "
            f"Reusing {cached_provider_runs} cached results; "
            f"{total_provider_runs - cached_provider_runs} calls remain."
        )
    completed = 0

    tasks = [
        asyncio.create_task(run_provider(case, provider))
        for case in cases
        for provider in model_providers
    ]
    if tasks:
        for task in asyncio.as_completed(tasks):
            run = await task
            output.append(run)
            completed += 1
            if progress and run.status != "cached":
                score_delta = (
                    f"{run.score_delta:+.1f}"
                    if run.score_delta is not None
                    else "n/a"
                )
                progress(
                    f"[DONE {completed}/{total_provider_runs}] "
                    f"{run.provider.upper()} {run.case_id} | {run.status} | "
                    f"{run.latency_ms / 1000:.1f}s | score delta {score_delta}"
                )
    return output


async def _run_provider_case(
    case: BenchmarkCase,
    resume: ResumeFixture,
    job: JobFixture,
    provider: str,
    *,
    force: bool,
    existing: dict[str, BenchmarkRun],
    progress: Callable[[str], None] | None = None,
) -> BenchmarkRun:
    model = GROQ_MODEL if provider == "groq" else CODEX_MODEL
    code_version = _code_version()
    cache_key = _cache_key(case, resume, job, provider, model, code_version)
    if not force and cache_key in existing:
        return existing[cache_key].model_copy(update={"status": "cached"})
    if progress:
        progress(f"[START] {provider.upper()} {case.case_id}")

    started_at = utc_now()
    start = time.perf_counter()
    original_latex = _read(resume.latex_path)
    job_text = _read(job.text_path)
    ledger = load_evidence(resume)
    parsed = parse(original_latex, resume_id=resume.resume_id)
    artifact_dir = RESULTS_DIR / "artifacts" / case.case_id / provider
    artifact_dir.mkdir(parents=True, exist_ok=True)
    routes = {
        task: LLMTaskRoute(provider, model)
        for task in ("plan", "diff", "refine", "review")
    }
    reset_usage()
    try:
        result = await run_optimization_pipeline(
            parsed,
            job_text,
            confirmed_skills=ledger.skills,
            job_keywords=job.keyword_payload(),
            llm_routes=routes,
            reviewer_backend="custom",
        )
        provider_error = _provider_error_from_warnings(provider, result.warnings)
        if provider_error:
            raise ProviderUnavailableError(provider_error)
        modified_latex = result.modified_latex or original_latex
        tex_path = artifact_dir / "optimized.tex"
        tex_path.write_text(modified_latex, encoding="utf-8")
        pdf_path: Path | None = None
        if result.pdf_bytes:
            pdf_path = artifact_dir / "optimized.pdf"
            pdf_path.write_bytes(result.pdf_bytes)
        audit = audit_optimized_resume(
            original_latex,
            modified_latex,
            job,
            ledger,
            result.diff,
        )
        before = result.ats_before
        after = result.ats_after
        run = BenchmarkRun(
            run_id=stable_id(cache_key, started_at),
            cache_key=cache_key,
            case_id=case.case_id,
            resume_id=resume.resume_id,
            job_id=job.job_id,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            code_version=code_version,
            started_at=started_at,
            completed_at=utc_now(),
            status="success",
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
            stage_latencies_ms=result.stage_latencies_ms,
            score_before=before.score if before else None,
            score_after=after.score if after else None,
            raw_score_before=before.raw_score if before else None,
            raw_score_after=after.raw_score if after else None,
            score_delta=(
                round(after.score - before.score, 1)
                if before and after
                else None
            ),
            target_met=result.ats_target_met,
            page_count=result.page_count,
            overflow=result.overflow,
            change_count=len(result.diff),
            rejected_change_count=len(result.rejected_changes),
            unsupported_claims=audit["unsupported_claims"],
            introduced_metrics=audit["introduced_metrics"],
            evidence_preservation_score=audit["evidence_preservation_score"],
            contextual_keyword_coverage=audit["contextual_keyword_coverage"],
            standalone_keyword_coverage=audit["standalone_keyword_coverage"],
            semantic_similarity=audit["semantic_similarity"],
            confirmed_skills=ledger.skills,
            warnings=result.warnings,
            modified_latex_path=relative_path(tex_path),
            modified_pdf_path=relative_path(pdf_path) if pdf_path else None,
            trace_id=result.trace_id,
            token_usage=get_usage(),
            estimated_cost_usd=_estimate_cost(provider, get_usage()),
        )
    except Exception as exc:
        run = BenchmarkRun(
            run_id=stable_id(cache_key, started_at),
            cache_key=cache_key,
            case_id=case.case_id,
            resume_id=resume.resume_id,
            job_id=job.job_id,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            code_version=code_version,
            started_at=started_at,
            completed_at=utc_now(),
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:2000],
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
            confirmed_skills=ledger.skills,
            token_usage=get_usage(),
            estimated_cost_usd=_estimate_cost(provider, get_usage()),
        )
    append_jsonl(RUNS_PATH, run)
    return run


def _run_deterministic_baseline(
    case: BenchmarkCase,
    resume: ResumeFixture,
    job: JobFixture,
    *,
    force: bool,
    existing: dict[str, BenchmarkRun],
) -> BenchmarkRun:
    code_version = _code_version()
    cache_key = _cache_key(case, resume, job, "deterministic", None, code_version)
    if not force and cache_key in existing:
        return existing[cache_key].model_copy(update={"status": "cached"})

    started_at = utc_now()
    start = time.perf_counter()
    original_latex = _read(resume.latex_path)
    ledger = load_evidence(resume)
    parsed = parse(original_latex, resume_id=resume.resume_id)
    original_plain = _build_plain_text(extract_full_resume(parsed))
    before = check_ats(
        original_plain,
        job.keyword_payload(),
        confirmed_skills=ledger.skills,
        supported_equivalents=ledger.supported_equivalents,
    )
    changes = _deterministic_supported_changes(parsed, job)
    reconstruction = apply_changes(
        parsed.latex_source,
        changes,
        parsed.stmt_index,
    )
    modified_latex = reconstruction.latex
    render = check_one_page(modified_latex)
    if render.overflow:
        changes = {}
        modified_latex = original_latex
        render = check_one_page(modified_latex)
    modified_plain = _build_plain_text(
        extract_full_resume(parse(modified_latex, resume_id=resume.resume_id))
    )
    after = check_ats(
        modified_plain,
        job.keyword_payload(),
        confirmed_skills=ledger.skills,
        supported_equivalents=ledger.supported_equivalents,
    )
    diff = [
        {
            "stmt_id": stmt_id,
            "original": parsed.stmt_index[stmt_id].original_text,
            "value": value,
        }
        for stmt_id, value in changes.items()
    ]
    audit = audit_optimized_resume(original_latex, modified_latex, job, ledger, diff)
    artifact_dir = RESULTS_DIR / "artifacts" / case.case_id / "deterministic"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    tex_path = artifact_dir / "optimized.tex"
    tex_path.write_text(modified_latex, encoding="utf-8")
    run = BenchmarkRun(
        run_id=stable_id(cache_key, started_at),
        cache_key=cache_key,
        case_id=case.case_id,
        resume_id=resume.resume_id,
        job_id=job.job_id,
        provider="deterministic",
        prompt_version=PROMPT_VERSION,
        code_version=code_version,
        started_at=started_at,
        completed_at=utc_now(),
        status="success",
        latency_ms=round((time.perf_counter() - start) * 1000, 1),
        score_before=before.score,
        score_after=after.score,
        raw_score_before=before.raw_score,
        raw_score_after=after.raw_score,
        score_delta=round(after.score - before.score, 1),
        target_met=after.score >= 80 and not render.overflow,
        page_count=render.page_count,
        overflow=render.overflow,
        change_count=len(changes),
        unsupported_claims=audit["unsupported_claims"],
        introduced_metrics=audit["introduced_metrics"],
        evidence_preservation_score=audit["evidence_preservation_score"],
        contextual_keyword_coverage=audit["contextual_keyword_coverage"],
        standalone_keyword_coverage=audit["standalone_keyword_coverage"],
        semantic_similarity=audit["semantic_similarity"],
        confirmed_skills=ledger.skills,
        modified_latex_path=relative_path(tex_path),
    )
    append_jsonl(RUNS_PATH, run)
    return run


def _deterministic_supported_changes(parsed: Any, job: JobFixture) -> dict[str, str]:
    gaps = {
        item.lower()
        for item in list(job.required_skills) + list(job.preferred_skills) + list(job.keywords)
    }
    changes: dict[str, str] = {}
    for stmt_id, span in parsed.stmt_index.items():
        if stmt_id.startswith("skills_"):
            continue
        value = span.original_text
        lower = value.lower()
        if "api development" in gaps and "fastapi" in lower and "api development" not in lower:
            value = re.sub(r"\bFastAPI\b", "FastAPI API development", value, count=1)
        if "model monitoring" in gaps and "mlflow" in lower and "model monitoring" not in lower:
            value = re.sub(r"\bMLflow\b", "MLflow model monitoring", value, count=1)
        if "llm applications" in gaps and "rag" in lower and "llm applications" not in lower:
            value = re.sub(r"\bRAG\b", "RAG and LLM applications", value, count=1)
        if "cloud-native" in gaps and "azure" in lower and "cloud-native" not in lower:
            value = re.sub(r"\bAzure\b", "Azure cloud-native platform", value, count=1)
        if value != span.original_text:
            changes[stmt_id] = value
    return changes


def _cache_key(
    case: BenchmarkCase,
    resume: ResumeFixture,
    job: JobFixture,
    provider: str,
    model: str | None,
    code_version: str,
) -> str:
    return sha256_text(
        "|".join(
            (
                case.case_id,
                resume.content_sha256,
                job.content_sha256,
                provider,
                model or "",
                PROMPT_VERSION,
                code_version,
            )
        )
    )


def _code_version() -> str:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=ROOT,
            check=False,
        ).returncode != 0
        return f"{output[:12]}{'-dirty' if dirty else ''}"
    except Exception:
        return os.environ.get("SMARTJOBAPPLY_CODE_VERSION", "unknown")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _estimate_cost(provider: str, usage: dict[str, int]) -> float | None:
    """Estimate API cost only when explicit per-million-token rates are configured."""
    prefix = f"BENCHMARK_{provider.upper()}"
    input_rate = os.environ.get(f"{prefix}_INPUT_USD_PER_M")
    output_rate = os.environ.get(f"{prefix}_OUTPUT_USD_PER_M")
    if input_rate is None or output_rate is None:
        return None
    cost = (
        usage.get("prompt_tokens", 0) * float(input_rate)
        + usage.get("completion_tokens", 0) * float(output_rate)
    ) / 1_000_000
    return round(cost, 6)


class ProviderUnavailableError(RuntimeError):
    """Raised when the optimizer silently fell back after a provider failure."""


def _provider_error_from_warnings(
    provider: str,
    warnings: list[str],
) -> str | None:
    markers = (
        "usage limit",
        "too many requests",
        "429",
        "rate limit",
        "all connection attempts failed",
        f"{provider} connection error",
    )
    for warning in warnings:
        normalized = warning.lower()
        if any(marker in normalized for marker in markers):
            return warning[:2000]
    return None


def _provider_degraded(run: BenchmarkRun) -> bool:
    return _provider_error_from_warnings(run.provider, run.warnings) is not None
