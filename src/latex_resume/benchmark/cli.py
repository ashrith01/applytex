"""Command-line interface for the ApplyTeX ATS MVP benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from latex_resume.benchmark.corpus import (
    build_offline_jobs,
    build_synthetic_resumes,
    download_onet,
    fetch_live_jobs,
    inspect_onet_archive,
)
from latex_resume.benchmark.report import build_report
from latex_resume.benchmark.review import ingest_reviews, prepare_review_queue
from latex_resume.benchmark.runner import optimize_cases
from latex_resume.benchmark.scoring import score_matrix, select_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="applytex-benchmark",
        description="Build and evaluate the ApplyTeX ATS real-world MVP benchmark.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-jds", help="Build offline JDs and fetch current public postings.")
    fetch.add_argument("--target-live", type=int, default=60)
    fetch.add_argument("--offline-only", action="store_true")
    fetch.add_argument("--download-onet", action="store_true")
    fetch.add_argument(
        "--esco-dir",
        type=Path,
        help="Optional extracted ESCO CSV directory used to enrich skill aliases.",
    )

    subparsers.add_parser("build-resumes", help="Generate 40 evidence-controlled LaTeX resumes.")

    score = subparsers.add_parser("score", help="Score the complete 4,800-pair matrix.")
    score.add_argument("--fastembed", action="store_true", help="Use BGE embeddings instead of TF-IDF fallback.")

    subparsers.add_parser("select-cases", help="Select 120 balanced optimization cases.")

    optimize = subparsers.add_parser("optimize", help="Run cached provider-backed optimization.")
    optimize.add_argument("--providers", default="groq,codex")
    optimize.add_argument("--limit", type=int)
    optimize.add_argument("--include-holdout", action="store_true")
    optimize.add_argument("--force", action="store_true")
    optimize.add_argument("--concurrency", type=int, default=1)

    review = subparsers.add_parser("review", help="Prepare or ingest the 50-item blind review.")
    review.add_argument("--ingest", type=Path)
    review.add_argument("--count", type=int, default=50)

    subparsers.add_parser("report", help="Build JSON, CSV, and static HTML reports.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fetch-jds":
        if args.download_onet:
            archive = download_onet()
            print(f"Downloaded O*NET archive: {archive}")
            print(json.dumps(inspect_onet_archive(archive), indent=2))
        offline = build_offline_jobs(esco_dir=args.esco_dir)
        print(f"Built {len(offline)} offline job descriptions.")
        if not args.offline_only:
            jobs, errors = fetch_live_jobs(target_count=args.target_live)
            print(f"Fetched {len(jobs)} current public job descriptions.")
            for error in errors:
                print(f"WARNING: {error}")
        return 0
    if args.command == "build-resumes":
        resumes = build_synthetic_resumes()
        print(f"Built {len(resumes)} synthetic LaTeX resumes.")
        return 0
    if args.command == "score":
        matrix = score_matrix(use_fastembed=args.fastembed)
        print(f"Scored {len(matrix)} resume-job pairs.")
        return 0
    if args.command == "select-cases":
        cases = select_cases()
        print(f"Selected {len(cases)} balanced cases.")
        return 0
    if args.command == "optimize":
        providers = [item.strip() for item in args.providers.split(",") if item.strip()]
        print(
            "Starting ApplyTeX ATS benchmark optimization. "
            "Some provider calls can take 30-90 seconds.",
            flush=True,
        )
        try:
            runs = asyncio.run(
                optimize_cases(
                    providers,
                    limit=args.limit,
                    include_holdout=args.include_holdout,
                    force=args.force,
                    concurrency=args.concurrency,
                    progress=lambda message: print(message, flush=True),
                )
            )
        except KeyboardInterrupt:
            print(
                "\nBenchmark stopped. Completed results were cached and will be "
                "reused on the next run.",
                flush=True,
            )
            return 130
        print(f"Completed or loaded {len(runs)} benchmark runs.")
        return 0
    if args.command == "review":
        if args.ingest:
            reviews = ingest_reviews(args.ingest)
            print(f"Ingested {len(reviews)} human reviews.")
        else:
            path = prepare_review_queue(args.count)
            print(f"Prepared blind review queue: {path}")
        return 0
    if args.command == "report":
        summary = build_report()
        print(summary.model_dump_json(indent=2))
        return 0
    return 2
