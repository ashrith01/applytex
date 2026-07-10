"""Blind human-review queue creation and validated review ingestion."""

from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

from latex_resume.benchmark.io import (
    JOB_MANIFEST,
    REVIEWS_DIR,
    REVIEWS_PATH,
    ROOT,
    RUNS_PATH,
    RESUME_MANIFEST,
    read_jsonl,
    relative_path,
    stable_id,
    write_jsonl,
)
from latex_resume.benchmark.models import (
    BenchmarkRun,
    HumanReview,
    JobFixture,
    ResumeFixture,
)
from latex_resume.benchmark.runner import _provider_degraded

QUEUE_PATH = REVIEWS_DIR / "review_queue.csv"
BLIND_MAP_PATH = REVIEWS_DIR / "blind_map.json"
PRIVATE_DIR = REVIEWS_DIR / "private"

REVIEW_FIELDS = (
    "truthfulness",
    "relevance",
    "readability",
    "specificity",
    "keyword_naturalness",
    "one_page_usability",
    "shortlist_likelihood",
)


def prepare_review_queue(count: int = 50) -> Path:
    """Create 50 provider-balanced blind A/B review packages."""
    runs = [
        run
        for run in read_jsonl(RUNS_PATH, BenchmarkRun)
        if run.status == "success"
        and run.provider in {"groq", "codex"}
        and run.modified_latex_path
        and not _provider_degraded(run)
    ]
    if len(runs) < count:
        raise ValueError(
            f"Need at least {count} successful Groq/Codex runs; found {len(runs)}."
        )
    resumes = {
        item.resume_id: item
        for item in read_jsonl(RESUME_MANIFEST, ResumeFixture)
    }
    jobs = {
        item.job_id: item
        for item in read_jsonl(JOB_MANIFEST, JobFixture)
    }
    selected = _balanced_runs(runs, count)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    queue_rows: list[dict[str, str]] = []
    mapping: dict[str, dict[str, str]] = {}

    for run in selected:
        blind_id = stable_id("review", run.run_id, length=12)
        directory = PRIVATE_DIR / blind_id
        directory.mkdir(parents=True, exist_ok=True)
        original_source = ROOT / resumes[run.resume_id].latex_path
        optimized_source = ROOT / str(run.modified_latex_path)
        job_source = ROOT / jobs[run.job_id].text_path
        optimized_is_a = int(stable_id(blind_id), 16) % 2 == 0
        a_source = optimized_source if optimized_is_a else original_source
        b_source = original_source if optimized_is_a else optimized_source
        shutil.copyfile(a_source, directory / "version_a.tex")
        shutil.copyfile(b_source, directory / "version_b.tex")
        shutil.copyfile(job_source, directory / "job_description.txt")
        mapping[blind_id] = {
            "run_id": run.run_id,
            "optimized_version": "a" if optimized_is_a else "b",
        }
        queue_rows.append(
            {
                "blind_item_id": blind_id,
                "job_description_path": relative_path(directory / "job_description.txt"),
                "version_a_path": relative_path(directory / "version_a.tex"),
                "version_b_path": relative_path(directory / "version_b.tex"),
                **{field: "" for field in REVIEW_FIELDS},
                "preferred_version": "",
                "critical_unsupported_claim": "",
                "reason": "",
                "reviewer_id": "",
            }
        )

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(queue_rows[0]))
        writer.writeheader()
        writer.writerows(queue_rows)
    BLIND_MAP_PATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return QUEUE_PATH


def ingest_reviews(path: Path = QUEUE_PATH) -> list[HumanReview]:
    """Validate a completed blind-review CSV and store normalized reviews."""
    if not BLIND_MAP_PATH.exists():
        raise ValueError("Blind map is missing. Prepare the review queue first.")
    mapping = json.loads(BLIND_MAP_PATH.read_text(encoding="utf-8"))
    reviews: list[HumanReview] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            blind_id = row.get("blind_item_id", "")
            if blind_id not in mapping:
                raise ValueError(f"Unknown blind_item_id at row {row_number}: {blind_id}")
            scores = {field: _score(row.get(field, ""), field, row_number) for field in REVIEW_FIELDS}
            reason = (row.get("reason") or "").strip()
            if any(value < 4 for value in scores.values()) and not reason:
                raise ValueError(f"Row {row_number} needs a reason because a score is below 4.")
            preferred_blind = (row.get("preferred_version") or "").strip().lower()
            if preferred_blind not in {"a", "b", "tie"}:
                raise ValueError(f"Row {row_number} preferred_version must be a, b, or tie.")
            optimized_blind = mapping[blind_id]["optimized_version"]
            preferred = (
                "tie"
                if preferred_blind == "tie"
                else "optimized"
                if preferred_blind == optimized_blind
                else "original"
            )
            critical = (row.get("critical_unsupported_claim") or "").strip().lower()
            reviews.append(
                HumanReview(
                    review_id=stable_id(blind_id, row.get("reviewer_id", "")),
                    blind_item_id=blind_id,
                    run_id=mapping[blind_id]["run_id"],
                    reviewer_id=(row.get("reviewer_id") or "anonymous").strip(),
                    preferred_version=preferred,
                    critical_unsupported_claim=critical in {"1", "true", "yes", "y"},
                    reason=reason,
                    **scores,
                )
            )
    write_jsonl(REVIEWS_PATH, reviews)
    return reviews


def _balanced_runs(runs: list[BenchmarkRun], count: int) -> list[BenchmarkRun]:
    buckets: dict[tuple[str, bool], list[BenchmarkRun]] = defaultdict(list)
    for run in runs:
        buckets[(run.provider, run.target_met)].append(run)
    for bucket in buckets.values():
        bucket.sort(key=lambda run: stable_id(run.run_id, "blind"))
    selected: list[BenchmarkRun] = []
    keys = sorted(buckets)
    while len(selected) < count:
        progressed = False
        for key in keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            break
    return selected


def _score(value: str, field: str, row_number: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"Row {row_number} has invalid {field}: {value!r}") from exc
    if not 1 <= parsed <= 5:
        raise ValueError(f"Row {row_number} {field} must be between 1 and 5.")
    return parsed
