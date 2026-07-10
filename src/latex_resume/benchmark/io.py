"""Filesystem helpers for benchmark manifests and artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "benchmark_data"
FIXTURES_DIR = DATA_DIR / "fixtures"
RESUMES_DIR = FIXTURES_DIR / "resumes"
JOBS_DIR = FIXTURES_DIR / "jobs"
LIVE_JOBS_DIR = DATA_DIR / "live_jds"
MANIFEST_DIR = DATA_DIR / "manifests"
RESULTS_DIR = DATA_DIR / "results"
CACHE_DIR = DATA_DIR / "cache"
REVIEWS_DIR = DATA_DIR / "reviews"
TAXONOMY_RAW_DIR = DATA_DIR / "taxonomy" / "raw"

RESUME_MANIFEST = MANIFEST_DIR / "resumes.jsonl"
JOB_MANIFEST = MANIFEST_DIR / "jobs.jsonl"
MATRIX_PATH = RESULTS_DIR / "matrix.jsonl"
CASES_PATH = MANIFEST_DIR / "cases.jsonl"
RUNS_PATH = RESULTS_DIR / "runs.jsonl"
REVIEWS_PATH = REVIEWS_DIR / "reviews.jsonl"


def ensure_directories() -> None:
    """Create the benchmark directory tree."""
    for path in (
        RESUMES_DIR,
        JOBS_DIR,
        LIVE_JOBS_DIR,
        MANIFEST_DIR,
        RESULTS_DIR,
        CACHE_DIR,
        REVIEWS_DIR,
        TAXONOMY_RAW_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def sha256_text(text: str) -> str:
    """Return a full SHA-256 hash for text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(*parts: str, length: int = 16) -> str:
    """Return a deterministic identifier from arbitrary string parts."""
    return sha256_text("\x1f".join(parts))[:length]


def relative_path(path: Path) -> str:
    """Return a repository-relative POSIX path."""
    return path.resolve().relative_to(ROOT).as_posix()


def write_jsonl(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    """Atomically replace a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    temp.replace(path)


def append_jsonl(path: Path, record: BaseModel | dict[str, Any]) -> None:
    """Append one JSON record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def read_jsonl(path: Path, model: type[T]) -> list[T]:
    """Load JSONL records into a Pydantic model."""
    if not path.exists():
        return []
    records: list[T] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"Invalid {path} line {line_number}: {exc}") from exc
    return records
