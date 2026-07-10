"""Aggregate benchmark metrics and render JSON, CSV, and static HTML reports."""

from __future__ import annotations

import csv
import html
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from latex_resume.benchmark.io import (
    CASES_PATH,
    JOB_MANIFEST,
    RESULTS_DIR,
    REVIEWS_PATH,
    RUNS_PATH,
    RESUME_MANIFEST,
    read_jsonl,
)
from latex_resume.benchmark.models import (
    BenchmarkCase,
    BenchmarkRun,
    BenchmarkSummary,
    HumanReview,
    JobFixture,
    ResumeFixture,
)
from latex_resume.benchmark.scoring import fairness_score_summary
from latex_resume.benchmark.runner import _provider_degraded


def build_report() -> BenchmarkSummary:
    """Build all benchmark artifacts from current manifests and run records."""
    resumes = read_jsonl(RESUME_MANIFEST, ResumeFixture)
    jobs = read_jsonl(JOB_MANIFEST, JobFixture)
    cases = read_jsonl(CASES_PATH, BenchmarkCase)
    selected_case_ids = {case.case_id for case in cases}
    runs = [
        run
        for run in _latest_runs(read_jsonl(RUNS_PATH, BenchmarkRun))
        if run.case_id in selected_case_ids
    ]
    reviews = read_jsonl(REVIEWS_PATH, HumanReview)
    degraded = [run for run in runs if _provider_degraded(run)]
    successful = [
        run for run in runs
        if run.status == "success" and not _provider_degraded(run)
    ]
    provider_runs = [run for run in successful if run.provider in {"groq", "codex"}]
    failures = [
        run
        for run in runs
        if run.status == "failed" or _provider_degraded(run)
    ]
    cases_by_id = {case.case_id: case for case in cases}
    jobs_by_id = {job.job_id: job for job in jobs}
    resumes_by_id = {resume.resume_id: resume for resume in resumes}

    parser_success = sum(item.parser_ok for item in resumes) / max(1, len(resumes))
    render_success = sum(item.render_ok for item in resumes) / max(1, len(resumes))
    submission_ready = [run for run in provider_runs if run.target_met]
    one_page_ready = (
        sum(not run.overflow and run.page_count <= 1 for run in submission_ready)
        / len(submission_ready)
        if submission_ready
        else None
    )
    unsupported_rate = (
        sum(bool(run.unsupported_claims) for run in provider_runs) / len(provider_runs)
        if provider_runs
        else None
    )
    medium_runs = [
        run
        for run in provider_runs
        if cases_by_id.get(run.case_id)
        and cases_by_id[run.case_id].expected_fit_tier == "medium"
        and run.score_delta is not None
    ]
    supported_runs = [
        run
        for run in provider_runs
        if cases_by_id.get(run.case_id)
        and cases_by_id[run.case_id].expected_fit_tier in {"medium", "strong"}
    ]
    weak_runs = [
        run
        for run in provider_runs
        if cases_by_id.get(run.case_id)
        and cases_by_id[run.case_id].expected_fit_tier in {"weak", "incompatible"}
    ]
    weak_false_ready = [
        run
        for run in weak_runs
        if run.target_met and bool(run.unsupported_claims)
    ]
    preferred_rate = (
        sum(review.preferred_version == "optimized" for review in reviews) / len(reviews)
        if reviews
        else None
    )
    critical_review_claims = sum(review.critical_unsupported_claim for review in reviews)
    correlation = _score_review_correlation(reviews, runs)
    failure_rate = len(failures) / max(1, len(runs))
    latencies = [run.latency_ms for run in provider_runs]
    median_medium_delta = _median([run.score_delta for run in medium_runs])
    target_rate = (
        sum(run.target_met for run in supported_runs) / len(supported_runs)
        if supported_runs
        else None
    )
    full_provider_evidence = len(provider_runs) >= 240
    full_human_evidence = len(reviews) >= 50

    gates = {
        "parser_render_success": _gate(min(parser_success, render_success), 0.98, ">="),
        "submission_ready_one_page": _gate(
            one_page_ready,
            1.0,
            ">=",
            pending=not full_provider_evidence,
        ),
        "critical_human_fabrications": _gate(
            critical_review_claims,
            0,
            "==",
            pending=not full_human_evidence,
        ),
        "automated_unsupported_claim_rate": _gate(
            unsupported_rate,
            0.01,
            "<=",
            pending=not full_provider_evidence,
        ),
        "median_medium_fit_improvement": _gate(
            median_medium_delta,
            10.0,
            ">=",
            pending=not full_provider_evidence,
        ),
        "supported_cases_reaching_80": _gate(
            target_rate,
            0.70,
            ">=",
            pending=not full_provider_evidence,
        ),
        "weak_cases_false_ready": _gate(
            len(weak_false_ready),
            0,
            "==",
            pending=not full_provider_evidence,
        ),
        "human_preference_for_optimized": _gate(
            preferred_rate,
            0.65,
            ">=",
            pending=not full_human_evidence,
        ),
        "score_human_correlation": _gate(
            correlation,
            0.60,
            ">=",
            pending=not full_human_evidence,
        ),
        "pipeline_failure_rate": _gate(
            failure_rate,
            0.05,
            "<=",
            pending=not full_provider_evidence,
        ),
        "latency_p50_ms": _gate(
            _percentile(latencies, 50),
            60_000,
            "<=",
            pending=not full_provider_evidence,
        ),
        "latency_p95_ms": _gate(
            _percentile(latencies, 95),
            120_000,
            "<=",
            pending=not full_provider_evidence,
        ),
    }
    summary = BenchmarkSummary(
        corpus={
            "resumes": len(resumes),
            "jobs": len(jobs),
            "pairs": len(resumes) * len(jobs),
            "selected_cases": len(cases),
            "holdout_cases": sum(case.holdout for case in cases),
            "human_reviews": len(reviews),
        },
        execution={
            "runs": len(runs),
            "successful_runs": len(successful),
            "provider_runs": len(provider_runs),
            "failures": len(failures),
            "failure_taxonomy": dict(Counter(run.error_type or "unknown" for run in failures)),
            "provider_comparison": _provider_comparison(runs),
        },
        quality={
            "parser_success_rate": parser_success,
            "render_success_rate": render_success,
            "submission_ready_one_page_rate": one_page_ready,
            "automated_unsupported_claim_rate": unsupported_rate,
            "introduced_metric_run_count": sum(bool(run.introduced_metrics) for run in provider_runs),
            "median_medium_fit_improvement": median_medium_delta,
            "supported_cases_reaching_80_rate": target_rate,
            "weak_incompatible_false_ready_count": len(weak_false_ready),
            "optimized_human_preference_rate": preferred_rate,
            "critical_human_unsupported_claims": critical_review_claims if reviews else None,
        },
        latency={
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "stage_p50_ms": _stage_percentiles(provider_runs, 50),
            "stage_p95_ms": _stage_percentiles(provider_runs, 95),
        },
        calibration={
            "submission_fit_vs_shortlist_spearman": correlation,
        },
        fairness=fairness_score_summary(),
        slices=_build_slices(provider_runs, cases_by_id, jobs_by_id, resumes_by_id),
        acceptance_gates=gates,
        limitations=[
            "This benchmark evaluates quality proxies and does not predict actual hiring outcomes.",
            "Live employer postings are retained locally and are not redistributed.",
            "Synthetic resumes improve privacy and truth labeling but cannot reproduce every real resume style.",
            "Human-review conclusions remain pending until 50 blind reviews are ingested.",
        ],
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "summary.json").write_text(
        summary.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _write_runs_csv(runs, RESULTS_DIR / "runs.csv")
    (RESULTS_DIR / "report.html").write_text(
        _render_html(summary),
        encoding="utf-8",
    )
    return summary


def _provider_comparison(runs: Sequence[BenchmarkRun]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for provider in ("deterministic", "groq", "codex"):
        subset = [run for run in runs if run.provider == provider]
        successful = [run for run in subset if run.status == "success"]
        output[provider] = {
            "runs": len(subset),
            "success_rate": len(successful) / max(1, len(subset)),
            "median_score_delta": _median([run.score_delta for run in successful]),
            "target_rate": sum(run.target_met for run in successful) / max(1, len(successful)),
            "p50_latency_ms": _percentile([run.latency_ms for run in successful], 50),
            "p95_latency_ms": _percentile([run.latency_ms for run in successful], 95),
            "unsupported_claim_rate": sum(bool(run.unsupported_claims) for run in successful)
            / max(1, len(successful)),
        }
    return output


def _build_slices(
    runs: Sequence[BenchmarkRun],
    cases: dict[str, BenchmarkCase],
    jobs: dict[str, JobFixture],
    resumes: dict[str, ResumeFixture],
) -> dict[str, Any]:
    dimensions: dict[str, Callable[[BenchmarkRun], str]] = {
        "role_family": lambda run: jobs[run.job_id].role_family,
        "seniority": lambda run: jobs[run.job_id].seniority,
        "industry": lambda run: jobs[run.job_id].industry,
        "fit_tier": lambda run: cases[run.case_id].expected_fit_tier,
        "template": lambda run: resumes[run.resume_id].template_id,
    }
    output: dict[str, Any] = {}
    for dimension, resolver in dimensions.items():
        groups: dict[str, list[BenchmarkRun]] = defaultdict(list)
        for run in runs:
            if run.case_id in cases and run.job_id in jobs and run.resume_id in resumes:
                groups[resolver(run)].append(run)
        output[dimension] = {
            name: {
                "runs": len(items),
                "median_score_delta": _median([item.score_delta for item in items]),
                "target_rate": sum(item.target_met for item in items) / max(1, len(items)),
                "failure_or_truth_issue_rate": sum(bool(item.unsupported_claims) for item in items)
                / max(1, len(items)),
            }
            for name, items in sorted(groups.items())
        }
    return output


def _score_review_correlation(
    reviews: Sequence[HumanReview],
    runs: Sequence[BenchmarkRun],
) -> float | None:
    scores_by_run = {run.run_id: run.score_after for run in runs if run.score_after is not None}
    pairs = [
        (float(scores_by_run[review.run_id]), float(review.shortlist_likelihood))
        for review in reviews
        if review.run_id in scores_by_run
    ]
    if len(pairs) < 3:
        return None
    left, right = zip(*pairs, strict=True)
    return round(_spearman(left, right), 4)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float:
    left_ranks = _ranks(left)
    right_ranks = _ranks(right)
    left_mean = statistics.mean(left_ranks)
    right_mean = statistics.mean(right_ranks)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_ranks, right_ranks, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left_ranks)
        * sum((value - right_mean) ** 2 for value in right_ranks)
    )
    return numerator / denominator if denominator else 0.0


def _ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2
        for index, _ in indexed[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def _stage_percentiles(runs: Sequence[BenchmarkRun], percentile: int) -> dict[str, float]:
    stages: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for stage, latency in run.stage_latencies_ms.items():
            stages[stage].append(latency)
    return {
        stage: _percentile(values, percentile) or 0.0
        for stage, values in sorted(stages.items())
    }


def _latest_runs(runs: Sequence[BenchmarkRun]) -> list[BenchmarkRun]:
    latest: dict[tuple[str, str], BenchmarkRun] = {}
    for run in runs:
        key = (run.case_id, run.provider)
        if key not in latest or run.completed_at > latest[key].completed_at:
            latest[key] = run
    return list(latest.values())


def _median(values: Iterable[float | None]) -> float | None:
    cleaned = [float(value) for value in values if value is not None]
    return round(statistics.median(cleaned), 2) if cleaned else None


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 2)


def _gate(
    actual: float | int | None,
    target: float | int,
    operator: str,
    *,
    pending: bool = False,
) -> dict[str, Any]:
    if pending or actual is None:
        return {"status": "pending", "actual": actual, "target": target, "operator": operator}
    passed = {
        ">=": actual >= target,
        "<=": actual <= target,
        "==": actual == target,
    }[operator]
    return {
        "status": "pass" if passed else "fail",
        "actual": actual,
        "target": target,
        "operator": operator,
    }


def _write_runs_csv(runs: Sequence[BenchmarkRun], path: Path) -> None:
    rows = []
    for run in runs:
        payload = run.model_dump(mode="json")
        for key, value in list(payload.items()):
            if isinstance(value, (list, dict)):
                payload[key] = json.dumps(value, sort_keys=True)
        rows.append(payload)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _render_html(summary: BenchmarkSummary) -> str:
    gates = "".join(
        "<tr>"
        f"<td>{html.escape(name.replace('_', ' ').title())}</td>"
        f"<td><span class='{data['status']}'>{data['status'].upper()}</span></td>"
        f"<td>{html.escape(str(data['actual']))}</td>"
        f"<td>{html.escape(data['operator'])} {html.escape(str(data['target']))}</td>"
        "</tr>"
        for name, data in summary.acceptance_gates.items()
    )
    providers = "".join(
        "<tr>"
        f"<td>{html.escape(provider.title())}</td>"
        f"<td>{values['runs']}</td>"
        f"<td>{values['success_rate']:.1%}</td>"
        f"<td>{values['median_score_delta']}</td>"
        f"<td>{values['target_rate']:.1%}</td>"
        f"<td>{values['p50_latency_ms']}</td>"
        "</tr>"
        for provider, values in summary.execution["provider_comparison"].items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ApplyTeX ATS Benchmark</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #17202a; background: #f5f7f8; }}
header {{ background: #16324f; color: white; padding: 28px max(24px, 6vw); }}
main {{ max-width: 1120px; margin: 0 auto; padding: 28px 24px 60px; }}
.metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
.metric {{ background: white; border: 1px solid #dce3e8; padding: 16px; border-radius: 6px; }}
.metric strong {{ display: block; font-size: 26px; margin-top: 6px; }}
section {{ margin-top: 28px; }}
table {{ width: 100%; border-collapse: collapse; background: white; }}
th, td {{ text-align: left; border-bottom: 1px solid #e1e7eb; padding: 10px; }}
.pass {{ color: #176b3a; font-weight: 700; }} .fail {{ color: #a52820; font-weight: 700; }}
.pending {{ color: #815b00; font-weight: 700; }}
</style>
</head>
<body>
<header><h1>ApplyTeX ATS Real-World MVP Benchmark</h1><p>Generated {html.escape(summary.generated_at)}</p></header>
<main>
<div class="metrics">
<div class="metric">Resumes<strong>{summary.corpus.get('resumes', 0)}</strong></div>
<div class="metric">Job descriptions<strong>{summary.corpus.get('jobs', 0)}</strong></div>
<div class="metric">Scored pairs<strong>{summary.corpus.get('pairs', 0)}</strong></div>
<div class="metric">Provider runs<strong>{summary.execution.get('provider_runs', 0)}</strong></div>
</div>
<section><h2>Acceptance gates</h2><table><thead><tr><th>Gate</th><th>Status</th><th>Actual</th><th>Target</th></tr></thead><tbody>{gates}</tbody></table></section>
<section><h2>Provider comparison</h2><table><thead><tr><th>Provider</th><th>Runs</th><th>Success</th><th>Median delta</th><th>80+ rate</th><th>p50 ms</th></tr></thead><tbody>{providers}</tbody></table></section>
<section><h2>Limitations</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in summary.limitations)}</ul></section>
</main></body></html>"""
