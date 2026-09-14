"""Job routes: /jobs/*, /extension/jobs/*."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from latex_resume.api import JobSearchRequest
from latex_resume.job_models import BrowserJobCapture, JobPosting, JobSearchResult
from latex_resume.job_sources import captured_job_to_posting
from latex_resume.routers._deps import resolve_request_profile_id

router = APIRouter()


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


@router.get("/jobs/{job_id}", response_model=JobPosting)
async def get_job(
    request: Request,
    job_id: str,
) -> JobPosting:
    """Return one saved job by ID."""
    job = request.app.state.application_store.get_job(job_id)
    if job is None:
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
