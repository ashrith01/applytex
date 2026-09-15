"""Curated employer watchlist and scheduled job ingestion.

The watchlist replaces "type a board token per search" with a passive feed:
each profile follows a set of public Greenhouse / Lever / Ashby boards, a
refresh fetches every board, keeps the postings that match the profile's
role and location preferences, scores each one against the profile resume,
and stores it with a stable ``first_seen_at`` so "new since yesterday" is a
cheap query. A bundled seed list of verified boards gets a new user started.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from latex_resume.application_store import ApplicationStore
from latex_resume.job_matching import enrich_job, preference_score
from latex_resume.job_models import (
    CandidateProfile,
    IngestionRun,
    JobPosting,
    JobProvider,
    SourceSearchError,
    WatchlistEntry,
    utc_now,
)
from latex_resume.job_sources import PublicJobBoardClient

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_MINUTES = 180
REFRESH_ENV = "APPLYTEX_WATCHLIST_REFRESH_MINUTES"
STRICT_ENV = "APPLYTEX_WATCHLIST_STRICT"


def refresh_interval_minutes() -> int:
    """Scheduled refresh cadence; ``0`` disables the background loop."""
    raw = os.environ.get(REFRESH_ENV, str(DEFAULT_REFRESH_MINUTES)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_REFRESH_MINUTES


def strict_preferences() -> bool:
    """When on (default), only jobs matching saved role/location preferences enter the feed."""
    return os.environ.get(STRICT_ENV, "1").strip().lower() not in {"0", "false", "no"}


# ---------------------------------------------------------------------------
# Seed list
# ---------------------------------------------------------------------------


def load_seed(domain_tags: list[str] | None = None) -> list[dict[str, Any]]:
    """Bundled, verified public boards; optionally filtered by domain tag."""
    seed_file = resources.files("latex_resume").joinpath("data/watchlist_seed.json")
    payload = json.loads(seed_file.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    wanted = {tag.strip().casefold() for tag in (domain_tags or []) if tag.strip()}
    if wanted:
        entries = [
            entry
            for entry in entries
            if wanted.intersection(str(tag).casefold() for tag in entry.get("domain_tags", []))
        ]
    return entries


def seed_entries_for_profile(profile_id: str, domain_tags: list[str] | None = None) -> list[WatchlistEntry]:
    return [
        WatchlistEntry(
            entry_id=str(uuid.uuid4()),
            profile_id=profile_id,
            provider=JobProvider(entry["provider"]),
            board_token=entry["board_token"],
            company=entry["company"],
            domain_tags=list(entry.get("domain_tags", [])),
        )
        for entry in load_seed(domain_tags)
    ]


# ---------------------------------------------------------------------------
# Fit scoring against the profile resume
# ---------------------------------------------------------------------------


def profile_resume_text(profile: CandidateProfile) -> str:
    """Plain-text resume for deterministic scoring, or empty when none is saved."""
    latex = profile.resume_latex_source.strip()
    if not latex:
        return ""
    # Imported lazily: the optimizer module is heavy and only needed for scoring.
    from latex_resume.extractor import extract_full_resume
    from latex_resume.optimizer import _build_plain_text
    from latex_resume.parser import parse

    try:
        parse_result = parse(latex, resume_id=Path(profile.resume_filename or "profile_resume").stem)
        return _build_plain_text(extract_full_resume(parse_result))
    except Exception as exc:  # pragma: no cover - defensive; scoring is optional
        logger.warning("Could not extract profile resume for feed scoring: %s", exc)
        return ""


def score_job_fit(resume_text: str, job: JobPosting) -> float | None:
    """Deterministic 0–100 fit of the resume against one posting (no LLM)."""
    if not resume_text or not job.description.strip():
        return None
    from latex_resume.ats import check_ats
    from latex_resume.optimizer import extract_job_keywords_fast

    try:
        keywords = extract_job_keywords_fast(job.description)
        return round(float(check_ats(resume_text, keywords).score), 1)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Fit scoring failed for %s / %s: %s", job.company, job.title, exc)
        return None


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class WatchlistIngestor:
    """Fetch every enabled board for a profile and merge matches into the feed."""

    def __init__(
        self,
        store: ApplicationStore,
        board_client: PublicJobBoardClient | None = None,
        *,
        concurrency: int = 6,
    ) -> None:
        self._store = store
        self._board_client = board_client or PublicJobBoardClient()
        self._semaphore = asyncio.Semaphore(max(1, concurrency))

    async def refresh(self, profile_id: str, *, trigger: str = "manual") -> IngestionRun:
        run = IngestionRun(run_id=str(uuid.uuid4()), profile_id=profile_id, trigger=trigger)  # type: ignore[arg-type]
        entries = self._store.list_watchlist_entries(profile_id, enabled_only=True)
        run.source_count = len(entries)
        if not entries:
            run.finished_at = utc_now()
            return self._store.save_ingestion_run(run)

        profile = self._store.get_candidate_profile(profile_id)
        resume_text = profile_resume_text(profile)
        strict = strict_preferences()
        outcomes = await asyncio.gather(*(self._fetch_entry(entry) for entry in entries))

        matched: list[JobPosting] = []
        for entry, (jobs, error) in zip(entries, outcomes, strict=True):
            entry_matches = 0
            if error is None:
                for raw in jobs:
                    job = enrich_job(raw)
                    score = preference_score(job, profile.search_preferences)
                    if strict and score < 0:
                        continue
                    entry_matches += 1
                    matched.append(
                        job.model_copy(
                            update={
                                "search_score": max(score, 0.0),
                                "captured_for_profile_id": profile_id,
                                "watchlist_entry_id": entry.entry_id,
                                "domain_tags": list(entry.domain_tags),
                                "fit_score": score_job_fit(resume_text, job),
                            }
                        )
                    )
            else:
                run.errors.append(error)
            run.fetched_jobs += len(jobs)
            self._store.update_watchlist_entry(
                profile_id,
                entry.entry_id,
                {
                    "last_checked_at": utc_now(),
                    "last_error": error.message if error else "",
                    "last_job_count": len(jobs),
                    "last_matched_count": entry_matches,
                },
            )

        run.matched_jobs = len(matched)
        run.new_jobs, run.updated_jobs = self._store.upsert_feed_jobs(matched)
        # Only prune boards that fetched successfully: an outage must not empty the feed.
        kept = {job.job_id for job in matched}
        for entry, (_, error) in zip(entries, outcomes, strict=True):
            if error is None:
                run.removed_jobs += self._store.prune_feed_jobs(profile_id, entry.entry_id, kept)
        run.finished_at = utc_now()
        logger.info(
            "watchlist refresh profile=%s sources=%d fetched=%d matched=%d new=%d removed=%d errors=%d",
            profile_id,
            run.source_count,
            run.fetched_jobs,
            run.matched_jobs,
            run.new_jobs,
            run.removed_jobs,
            len(run.errors),
        )
        return self._store.save_ingestion_run(run)

    async def refresh_all(self, *, trigger: str = "scheduled") -> list[IngestionRun]:
        runs: list[IngestionRun] = []
        for profile_id in self._store.list_profiles_with_watchlists():
            try:
                runs.append(await self.refresh(profile_id, trigger=trigger))
            except Exception as exc:  # pragma: no cover - loop must survive one bad profile
                logger.error("watchlist refresh failed for profile %s: %s", profile_id, exc)
        return runs

    async def _fetch_entry(
        self,
        entry: WatchlistEntry,
    ) -> tuple[list[JobPosting], SourceSearchError | None]:
        async with self._semaphore:
            try:
                return await self._board_client.fetch(entry.to_source()), None
            except Exception as exc:
                return [], SourceSearchError(
                    provider=entry.provider,
                    board_token=entry.board_token,
                    message=f"{type(exc).__name__}: {exc}",
                )


async def refresh_loop(ingestor: WatchlistIngestor, *, initial_delay: float = 60.0) -> None:
    """Background scheduler: refresh every profile's watchlist on a fixed cadence."""
    minutes = refresh_interval_minutes()
    if minutes <= 0:
        logger.info("watchlist scheduler disabled (%s=0)", REFRESH_ENV)
        return
    await asyncio.sleep(initial_delay)
    while True:
        try:
            await ingestor.refresh_all(trigger="scheduled")
        except Exception as exc:  # pragma: no cover
            logger.error("watchlist scheduled refresh error: %s", exc)
        await asyncio.sleep(minutes * 60)
