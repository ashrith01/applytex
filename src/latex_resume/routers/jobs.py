"""Job routes: /jobs/*, /extension/jobs/*."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from latex_resume.api import JobSearchRequest
from latex_resume.job_models import BrowserJobCapture, JobPosting, JobSearchResult
from latex_resume.job_sources import captured_job_to_posting
from latex_resume.routers._deps import resolve_request_profile_id

router = APIRouter()


class JobFeedResponse(BaseModel):
    since: str | None = None
    total: int = 0
    applied_job_ids: list[str] = Field(default_factory=list)
    jobs: list[JobPosting] = Field(default_factory=list)


def parse_since(value: str | None) -> str | None:
    """Accept an ISO timestamp or a relative window such as ``24h``, ``3d``, ``2w``."""
    if not value:
        return None
    cleaned = value.strip().lower()
    unit = cleaned[-1:]
    amount = cleaned[:-1]
    if unit in {"h", "d", "w"} and amount.isdigit():
        delta = {"h": timedelta(hours=int(amount)), "d": timedelta(days=int(amount)), "w": timedelta(weeks=int(amount))}[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, "since must be an ISO timestamp or a window like 24h, 3d, 2w") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


@router.post("/jobs/search", response_model=JobSearchResult)
async def search_jobs(
    request: Request,
    body: JobSearchRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> JobSearchResult:
    """Search configured public ATS boards without browser automation."""
    profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
    profile = request.app.state.application_store.get_candidate_profile(profile_id)
    preferences = profile.search_preferences if body.use_saved_preferences else None
    result = await request.app.state.job_search_service.search(
        body.query,
        body.sources,
        preferences,
    )
    stamped_jobs = [
        job.model_copy(update={"captured_for_profile_id": profile_id})
        for job in result.jobs
    ]
    result = result.model_copy(update={"jobs": stamped_jobs})
    request.app.state.application_store.save_search(result)
    return result


@router.get("/jobs", response_model=list[JobPosting])
async def list_jobs(
    request: Request,
    limit: int = 100,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> list[JobPosting]:
    """List normalized jobs saved from previous searches."""
    bounded_limit = min(max(limit, 1), 200)
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    return request.app.state.application_store.list_jobs(
        bounded_limit,
        profile_id=scoped_profile_id,
    )


@router.get("/jobs/feed", response_model=JobFeedResponse)
async def job_feed(
    request: Request,
    since: str | None = None,
    min_fit: float | None = None,
    domain: Annotated[list[str] | None, Query()] = None,
    limit: int = 100,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> JobFeedResponse:
    """Watchlist matches for this profile, best fit first; ``since`` accepts 24h / 3d / ISO."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    since_iso = parse_since(since)
    store = request.app.state.application_store
    jobs = store.list_feed_jobs(
        scoped_profile_id,
        since=since_iso,
        min_fit=min_fit,
        domain_tags=domain,
        limit=min(max(limit, 1), 500),
    )
    applied = {
        application.job_id
        for application in store.list_applications(limit=10_000, profile_id=scoped_profile_id)
    }
    return JobFeedResponse(
        since=since_iso,
        total=len(jobs),
        applied_job_ids=[job.job_id for job in jobs if job.job_id in applied],
        jobs=jobs,
    )


@router.get("/jobs/{job_id}", response_model=JobPosting)
async def get_job(
    request: Request,
    job_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> JobPosting:
    """Return one saved job by ID. Jobs captured for another profile are not visible."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    job = request.app.state.application_store.get_job(job_id)
    if job is None or (job.captured_for_profile_id and job.captured_for_profile_id != scoped_profile_id):
        raise HTTPException(404, f"Job '{job_id}' not found.")
    return job


@router.post("/extension/jobs/capture", response_model=JobPosting)
async def capture_browser_job(
    request: Request,
    body: BrowserJobCapture,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> JobPosting:
    """Persist a job captured from a user-visible LinkedIn or ATS tab."""
    if not body.source_url.startswith("https://") or not body.apply_url.startswith("https://"):
        raise HTTPException(400, "Captured job URLs must use HTTPS.")
    profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
    job = captured_job_to_posting(body).model_copy(
        update={"captured_for_profile_id": profile_id}
    )
    request.app.state.application_store.save_job(job)
    return job
