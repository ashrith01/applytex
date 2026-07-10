"""Deterministic matrix scoring, baselines, case selection, and fairness checks."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from latex_resume.ats import _normalise, _skill_found, check_ats
from latex_resume.benchmark.corpus import load_evidence
from latex_resume.benchmark.io import (
    CASES_PATH,
    JOB_MANIFEST,
    MATRIX_PATH,
    RESUME_MANIFEST,
    RESULTS_DIR,
    ROOT,
    read_jsonl,
    stable_id,
    write_jsonl,
)
from latex_resume.benchmark.models import (
    BenchmarkCase,
    EvidenceLedger,
    FitTier,
    JobFixture,
    ResumeFixture,
)
from latex_resume.extractor import extract_full_resume
from latex_resume.optimizer import _build_plain_text
from latex_resume.parser import parse

TOKEN_RE = re.compile(r"\b[a-z0-9][a-z0-9+#.-]*\b", re.IGNORECASE)
SENIORITY_RANK = {"junior": 0, "mid": 1, "senior": 2, "staff": 3}


def score_matrix(use_fastembed: bool = False) -> list[BenchmarkCase]:
    """Score all resume/JD pairs and persist the full matrix."""
    resumes = read_jsonl(RESUME_MANIFEST, ResumeFixture)
    jobs = read_jsonl(JOB_MANIFEST, JobFixture)
    if len(resumes) != 40:
        raise ValueError(f"Expected 40 resume fixtures, found {len(resumes)}.")
    if len(jobs) != 120:
        raise ValueError(
            f"Expected 120 JD fixtures, found {len(jobs)}. "
            "Run fetch-jds after building the 60 offline jobs."
        )

    resume_texts = {item.resume_id: _resume_plain_text(item) for item in resumes}
    job_texts = {item.job_id: _read_text(item.text_path) for item in jobs}
    embedding_scores, embedding_backend = _embedding_pair_scores(
        resumes,
        jobs,
        resume_texts,
        job_texts,
        use_fastembed=use_fastembed,
    )
    bm25_scores = _bm25_pair_scores(resumes, jobs, resume_texts, job_texts)
    records: list[BenchmarkCase] = []

    for resume in resumes:
        ledger = load_evidence(resume)
        resume_text = resume_texts[resume.resume_id]
        for job in jobs:
            payload = job.keyword_payload()
            ats = check_ats(
                resume_text,
                payload,
                confirmed_skills=ledger.skills,
                supported_equivalents=ledger.supported_equivalents,
            )
            truth_supported = {
                skill
                for skill in job.required_skills
                if _ledger_supports(skill, ledger)
            }
            predicted = set(ats.required_found)
            true_positive = len(predicted & truth_supported)
            precision = true_positive / len(predicted) if predicted else 1.0
            recall = true_positive / len(truth_supported) if truth_supported else 1.0
            overlap = (
                len(truth_supported) / len(job.required_skills)
                if job.required_skills
                else 1.0
            )
            tier = _expected_fit_tier(resume, job, overlap)
            case_id = stable_id(resume.resume_id, job.job_id)
            holdout = resume.holdout and job.holdout
            records.append(
                BenchmarkCase(
                    case_id=case_id,
                    resume_id=resume.resume_id,
                    job_id=job.job_id,
                    expected_fit_tier=tier,
                    holdout=holdout,
                    evidence_overlap=round(overlap, 4),
                    baseline_submission_score=ats.submission_score,
                    baseline_raw_score=ats.raw_score,
                    bm25_score=round(bm25_scores[(resume.resume_id, job.job_id)], 2),
                    embedding_score=round(
                        embedding_scores[(resume.resume_id, job.job_id)],
                        2,
                    ),
                    embedding_backend=embedding_backend,
                    required_skill_precision=round(precision, 4),
                    required_skill_recall=round(recall, 4),
                )
            )

    write_jsonl(MATRIX_PATH, records)
    _write_corpus_validation(resumes, jobs)
    return records


def select_cases(total: int = 120, holdout_count: int = 24) -> list[BenchmarkCase]:
    """Select a balanced subset while keeping mixed holdout/dev pairs out."""
    matrix = read_jsonl(MATRIX_PATH, BenchmarkCase)
    resumes = {item.resume_id: item for item in read_jsonl(RESUME_MANIFEST, ResumeFixture)}
    jobs = {item.job_id: item for item in read_jsonl(JOB_MANIFEST, JobFixture)}
    if not matrix:
        raise ValueError("Matrix is empty. Run `applytex-benchmark score` first.")
    if total != 120 or holdout_count != 24:
        raise ValueError("The MVP selection is fixed at 120 cases with 24 holdout cases.")

    development = [
        case
        for case in matrix
        if not resumes[case.resume_id].holdout and not jobs[case.job_id].holdout
    ]
    holdout = [
        case
        for case in matrix
        if resumes[case.resume_id].holdout and jobs[case.job_id].holdout
    ]
    selected = _select_by_fit_tier(
        development,
        resumes,
        jobs,
        per_tier=(total - holdout_count) // 4,
        split="development",
    )
    selected.extend(
        _select_by_fit_tier(
            holdout,
            resumes,
            jobs,
            per_tier=holdout_count // 4,
            split="holdout",
        )
    )
    if len(selected) != total:
        raise ValueError(f"Could select only {len(selected)} of {total} benchmark cases.")
    write_jsonl(CASES_PATH, selected)
    return selected


def _select_by_fit_tier(
    cases: list[BenchmarkCase],
    resumes: dict[str, ResumeFixture],
    jobs: dict[str, JobFixture],
    *,
    per_tier: int,
    split: str,
) -> list[BenchmarkCase]:
    selected: list[BenchmarkCase] = []
    for tier in ("strong", "medium", "weak", "incompatible"):
        tier_cases = [case for case in cases if case.expected_fit_tier == tier]
        chosen = _round_robin_select(
            tier_cases,
            resumes,
            jobs,
            per_tier,
            f"{split}|{tier}",
        )
        if len(chosen) != per_tier:
            raise ValueError(
                f"Need {per_tier} {split} {tier} cases; only {len(chosen)} available."
            )
        selected.extend(chosen)
    return selected


def fairness_score_summary() -> dict[str, object]:
    """Compare deterministic scores for name-only counterfactual resume pairs."""
    matrix = read_jsonl(MATRIX_PATH, BenchmarkCase)
    resumes = read_jsonl(RESUME_MANIFEST, ResumeFixture)
    groups: dict[str, list[ResumeFixture]] = defaultdict(list)
    for resume in resumes:
        if resume.counterfactual_group:
            groups[resume.counterfactual_group].append(resume)
    by_pair = {(case.resume_id, case.job_id): case for case in matrix}
    differences: list[float] = []
    pair_details: list[dict[str, object]] = []
    for group, members in sorted(groups.items()):
        if len(members) != 2:
            continue
        first, second = sorted(members, key=lambda item: item.resume_id)
        common_jobs = {
            job_id
            for resume_id, job_id in by_pair
            if resume_id == first.resume_id
        }
        for job_id in common_jobs:
            left = by_pair[(first.resume_id, job_id)].baseline_submission_score
            right = by_pair[(second.resume_id, job_id)].baseline_submission_score
            difference = abs(left - right)
            differences.append(difference)
            if difference:
                pair_details.append(
                    {
                        "counterfactual_group": group,
                        "job_id": job_id,
                        "absolute_score_difference": difference,
                    }
                )
    return {
        "pair_comparisons": len(differences),
        "mean_absolute_score_difference": round(
            sum(differences) / len(differences),
            4,
        ) if differences else None,
        "max_absolute_score_difference": max(differences, default=None),
        "nonzero_differences": pair_details,
    }


def _round_robin_select(
    cases: list[BenchmarkCase],
    resumes: dict[str, ResumeFixture],
    jobs: dict[str, JobFixture],
    count: int,
    split: str,
) -> list[BenchmarkCase]:
    buckets: dict[str, list[BenchmarkCase]] = defaultdict(list)
    for case in cases:
        resume = resumes[case.resume_id]
        job = jobs[case.job_id]
        score_band = min(4, int(case.baseline_submission_score // 20))
        bucket = "|".join(
            (
                job.role_family,
                job.seniority,
                job.industry,
                case.expected_fit_tier,
                resume.template_id,
                str(score_band),
            )
        )
        buckets[bucket].append(case)
    for bucket_cases in buckets.values():
        bucket_cases.sort(key=lambda item: stable_id(item.case_id, split))
    selected: list[BenchmarkCase] = []
    bucket_names = sorted(buckets, key=lambda name: stable_id(name, split))
    while len(selected) < count:
        progressed = False
        for name in bucket_names:
            if not buckets[name]:
                continue
            case = buckets[name].pop(0)
            selected.append(
                case.model_copy(
                    update={
                        "selected": True,
                        "selection_bucket": f"{split}|{name}",
                    }
                )
            )
            progressed = True
            if len(selected) >= count:
                break
        if not progressed:
            break
    return selected


def _expected_fit_tier(
    resume: ResumeFixture,
    job: JobFixture,
    overlap: float,
) -> FitTier:
    same_role = resume.role_family == job.role_family
    seniority_gap = SENIORITY_RANK[job.seniority] - SENIORITY_RANK[resume.seniority]
    if same_role and overlap >= 0.75 and seniority_gap <= 0:
        return "strong"
    if same_role and overlap >= 0.45 and seniority_gap <= 1:
        return "medium"
    if overlap >= 0.2:
        return "weak"
    return "incompatible"


def _ledger_supports(skill: str, ledger: EvidenceLedger) -> bool:
    evidence = " ".join(ledger.skills + ledger.allowed_claims + ledger.domains)
    return _skill_found(
        skill,
        _normalise(evidence),
        ledger.supported_equivalents,
    )


def _resume_plain_text(fixture: ResumeFixture) -> str:
    latex = _read_text(fixture.latex_path)
    parsed = parse(latex, resume_id=fixture.resume_id)
    return _build_plain_text(extract_full_resume(parsed))


def _read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _bm25_pair_scores(
    resumes: Sequence[ResumeFixture],
    jobs: Sequence[JobFixture],
    resume_texts: dict[str, str],
    job_texts: dict[str, str],
) -> dict[tuple[str, str], float]:
    documents = [_tokens(resume_texts[item.resume_id]) for item in resumes]
    document_frequencies: Counter[str] = Counter()
    for document in documents:
        document_frequencies.update(set(document))
    average_length = sum(map(len, documents)) / max(1, len(documents))
    raw: dict[tuple[str, str], float] = {}
    for job in jobs:
        query = set(_tokens(job_texts[job.job_id]))
        scores: list[tuple[str, float]] = []
        for resume, document in zip(resumes, documents, strict=True):
            frequencies = Counter(document)
            score = 0.0
            for term in query:
                frequency = frequencies[term]
                if not frequency:
                    continue
                df = document_frequencies[term]
                idf = math.log(1 + (len(documents) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * len(document) / max(1.0, average_length)
                )
                score += idf * frequency * 2.5 / denominator
            scores.append((resume.resume_id, score))
        maximum = max((score for _, score in scores), default=0.0)
        for resume_id, score in scores:
            raw[(resume_id, job.job_id)] = 100.0 * score / maximum if maximum else 0.0
    return raw


def _embedding_pair_scores(
    resumes: Sequence[ResumeFixture],
    jobs: Sequence[JobFixture],
    resume_texts: dict[str, str],
    job_texts: dict[str, str],
    *,
    use_fastembed: bool,
) -> tuple[dict[tuple[str, str], float], str]:
    if use_fastembed:
        try:
            return _fastembed_scores(resumes, jobs, resume_texts, job_texts), "fastembed:BAAI/bge-small-en-v1.5"
        except Exception:
            pass
    return _tfidf_scores(resumes, jobs, resume_texts, job_texts), "tfidf_cosine_fallback"


def _fastembed_scores(
    resumes: Sequence[ResumeFixture],
    jobs: Sequence[JobFixture],
    resume_texts: dict[str, str],
    job_texts: dict[str, str],
) -> dict[tuple[str, str], float]:
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    resume_vectors = list(model.embed([resume_texts[item.resume_id] for item in resumes]))
    job_vectors = list(model.query_embed([job_texts[item.job_id] for item in jobs]))
    scores: dict[tuple[str, str], float] = {}
    for resume, resume_vector in zip(resumes, resume_vectors, strict=True):
        resume_values = [float(value) for value in resume_vector]
        for job, job_vector in zip(jobs, job_vectors, strict=True):
            cosine = _cosine(resume_values, [float(value) for value in job_vector])
            scores[(resume.resume_id, job.job_id)] = max(0.0, min(100.0, cosine * 100))
    return scores


def _tfidf_scores(
    resumes: Sequence[ResumeFixture],
    jobs: Sequence[JobFixture],
    resume_texts: dict[str, str],
    job_texts: dict[str, str],
) -> dict[tuple[str, str], float]:
    all_ids = [item.resume_id for item in resumes] + [item.job_id for item in jobs]
    all_texts = [resume_texts[item.resume_id] for item in resumes] + [
        job_texts[item.job_id] for item in jobs
    ]
    tokenized = [_tokens(text) for text in all_texts]
    document_frequencies: Counter[str] = Counter()
    for tokens in tokenized:
        document_frequencies.update(set(tokens))
    vectors: dict[str, dict[str, float]] = {}
    total = len(tokenized)
    for identifier, tokens in zip(all_ids, tokenized, strict=True):
        frequencies = Counter(tokens)
        vector = {
            term: (1 + math.log(count)) * math.log((1 + total) / (1 + document_frequencies[term]))
            for term, count in frequencies.items()
        }
        vectors[identifier] = vector
    scores: dict[tuple[str, str], float] = {}
    for resume in resumes:
        for job in jobs:
            cosine = _sparse_cosine(vectors[resume.resume_id], vectors[job.job_id])
            scores[(resume.resume_id, job.job_id)] = max(0.0, min(100.0, cosine * 100))
    return scores


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _sparse_cosine(left: dict[str, float], right: dict[str, float]) -> float:
    common = left.keys() & right.keys()
    numerator = sum(left[key] * right[key] for key in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _write_corpus_validation(
    resumes: Sequence[ResumeFixture],
    jobs: Sequence[JobFixture],
) -> None:
    payload = {
        "resumes": len(resumes),
        "jobs": len(jobs),
        "pairs": len(resumes) * len(jobs),
        "resume_parser_success": sum(item.parser_ok for item in resumes),
        "resume_render_success": sum(item.render_ok for item in resumes),
        "resume_one_page": sum(not item.overflow and item.page_count <= 1 for item in resumes),
        "resume_holdout": sum(item.holdout for item in resumes),
        "job_holdout": sum(item.holdout for item in jobs),
        "job_source_counts": dict(Counter(item.source_kind for item in jobs)),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "corpus_validation.json").write_text(
        __import__("json").dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
