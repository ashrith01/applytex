"""Public ATS job-board adapters and deterministic search orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from html.parser import HTMLParser
from typing import Any

import httpx

from latex_resume.job_models import (
    BrowserJobCapture,
    JobPosting,
    JobProvider,
    JobSearchQuery,
    JobSearchResult,
    JobSourceConfig,
    SearchPreferences,
    SourceSearchError,
)
from latex_resume.job_matching import enrich_job, preference_score


class _TextExtractor(HTMLParser):
    """Small HTML-to-text parser for public job descriptions."""

    _BLOCK_TAGS = {"br", "div", "li", "p", "section", "ul", "ol", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        return "\n".join(
            line.strip()
            for line in joined.splitlines()
            if line.strip()
        )


def html_to_text(value: str) -> str:
    """Convert provider HTML into readable plaintext."""
    parser = _TextExtractor()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def _stable_job_id(provider: JobProvider, board_token: str, external_id: str) -> str:
    payload = f"{provider.value}\x1f{board_token}\x1f{external_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _workplace_type(title: str, location: str, description: str) -> str:
    structured = f"{title} {location}".lower()
    if "hybrid" in structured:
        return "hybrid"
    if re.search(r"\bremote\b|work from home|distributed", structured):
        return "remote"
    if location.strip():
        return "onsite"
    description_text = description[:1000].lower()
    if "hybrid" in description_text:
        return "hybrid"
    if re.search(r"\bremote\b|work from home|distributed", description_text):
        return "remote"
    return "unknown"


class PublicJobBoardClient:
    """Fetch and normalize jobs from Greenhouse, Lever, and Ashby."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._timeout = timeout

    async def fetch(self, source: JobSourceConfig) -> list[JobPosting]:
        """Fetch every published job from one configured board."""
        if self._client is not None:
            return await self._fetch_with_client(self._client, source)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "ApplyTeX-ATS/0.3 public-job-search"},
        ) as client:
            return await self._fetch_with_client(client, source)

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        source: JobSourceConfig,
    ) -> list[JobPosting]:
        if source.provider is JobProvider.GREENHOUSE:
            return await self._fetch_greenhouse(client, source)
        if source.provider is JobProvider.LEVER:
            return await self._fetch_lever(client, source)
        if source.provider is JobProvider.ASHBY:
            return await self._fetch_ashby(client, source)
        raise ValueError(f"Unsupported job provider: {source.provider}")

    async def _fetch_greenhouse(
        self,
        client: httpx.AsyncClient,
        source: JobSourceConfig,
    ) -> list[JobPosting]:
        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{source.board_token}/jobs?content=true"
        )
        payload = (await client.get(url)).raise_for_status().json()
        return [
            self._posting(
                source=source,
                external_id=str(item.get("id", "")),
                title=str(item.get("title", "")).strip(),
                description=html_to_text(str(item.get("content", ""))),
                location=str((item.get("location") or {}).get("name", "")).strip(),
                source_url=str(item.get("absolute_url", "")).strip(),
                apply_url=str(item.get("absolute_url", "")).strip(),
                published_at=item.get("updated_at"),
            )
            for item in payload.get("jobs", [])
            if item.get("id") and item.get("title")
        ]

    async def _fetch_lever(
        self,
        client: httpx.AsyncClient,
        source: JobSourceConfig,
    ) -> list[JobPosting]:
        url = f"https://api.lever.co/v0/postings/{source.board_token}?mode=json"
        payload = (await client.get(url)).raise_for_status().json()
        return [
            self._posting(
                source=source,
                external_id=str(item.get("id", "")),
                title=str(item.get("text", "")).strip(),
                description=str(item.get("descriptionPlain", "")).strip()
                or html_to_text(str(item.get("description", ""))),
                location=str((item.get("categories") or {}).get("location", "")).strip(),
                source_url=str(item.get("hostedUrl", "")).strip(),
                apply_url=str(item.get("applyUrl") or item.get("hostedUrl") or "").strip(),
                published_at=None,
            )
            for item in payload
            if item.get("id") and item.get("text")
        ]

    async def _fetch_ashby(
        self,
        client: httpx.AsyncClient,
        source: JobSourceConfig,
    ) -> list[JobPosting]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{source.board_token}"
        payload = (await client.get(url)).raise_for_status().json()
        return [
            self._posting(
                source=source,
                external_id=str(item.get("id", "")).strip()
                or hashlib.sha256(
                    f"{item.get('title', '')}\x1f{item.get('jobUrl', '')}".encode("utf-8")
                ).hexdigest()[:20],
                title=str(item.get("title", "")).strip(),
                description=str(item.get("descriptionPlain", "")).strip()
                or html_to_text(
                    str(item.get("descriptionHtml") or item.get("description") or "")
                ),
                location=str(item.get("location", "")).strip(),
                source_url=str(item.get("jobUrl") or item.get("applyUrl") or "").strip(),
                apply_url=str(item.get("applyUrl") or item.get("jobUrl") or "").strip(),
                published_at=item.get("publishedAt"),
            )
            for item in payload.get("jobs", [])
            if item.get("title")
        ]

    @staticmethod
    def _posting(
        *,
        source: JobSourceConfig,
        external_id: str,
        title: str,
        description: str,
        location: str,
        source_url: str,
        apply_url: str,
        published_at: str | None,
    ) -> JobPosting:
        return JobPosting(
            job_id=_stable_job_id(source.provider, source.board_token, external_id),
            provider=source.provider,
            board_token=source.board_token,
            external_id=external_id,
            company=source.company,
            title=title,
            description=description,
            location=location,
            workplace_type=_workplace_type(title, location, description),
            source_url=source_url,
            apply_url=apply_url,
            published_at=published_at,
            industry=source.industry,
        )


class JobSearchService:
    """Search configured public boards and rank matching normalized postings."""

    def __init__(self, board_client: PublicJobBoardClient | None = None) -> None:
        self._board_client = board_client or PublicJobBoardClient()

    async def search(
        self,
        query: JobSearchQuery,
        sources: list[JobSourceConfig],
        preferences: SearchPreferences | None = None,
    ) -> JobSearchResult:
        """Fetch sources concurrently, filter locally, and retain source failures."""
        outcomes = await asyncio.gather(
            *(self._fetch_source(source) for source in sources)
        )
        jobs: list[JobPosting] = []
        errors: list[SourceSearchError] = []
        for source_jobs, error in outcomes:
            jobs.extend(source_jobs)
            if error is not None:
                errors.append(error)

        deduped = [enrich_job(job) for job in self._deduplicate(jobs)]
        ranked = [
            job.model_copy(update={"search_score": score})
            for job in deduped
            if (score := self._combined_score(job, query, preferences)) >= 0
        ]
        ranked.sort(
            key=lambda item: (
                -item.search_score,
                item.company.casefold(),
                item.title.casefold(),
            )
        )
        return JobSearchResult(
            search_id=str(uuid.uuid4()),
            query=query,
            sources=sources,
            jobs=ranked[: query.limit],
            errors=errors,
        )

    @classmethod
    def _combined_score(
        cls,
        job: JobPosting,
        query: JobSearchQuery,
        preferences: SearchPreferences | None,
    ) -> float:
        query_score = cls._score(job, query)
        if query_score < 0:
            return -1
        if preferences is None:
            return query_score
        preferred_score = preference_score(job, preferences)
        if preferred_score < 0:
            return -1
        return round(query_score + preferred_score, 3)

    async def _fetch_source(
        self,
        source: JobSourceConfig,
    ) -> tuple[list[JobPosting], SourceSearchError | None]:
        try:
            return await self._board_client.fetch(source), None
        except Exception as exc:
            return [], SourceSearchError(
                provider=source.provider,
                board_token=source.board_token,
                message=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _deduplicate(jobs: list[JobPosting]) -> list[JobPosting]:
        seen: set[str] = set()
        result: list[JobPosting] = []
        for job in jobs:
            key = job.apply_url.casefold().rstrip("/") or (
                f"{job.company}|{job.title}|{job.location}".casefold()
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(job)
        return result

    @staticmethod
    def _score(job: JobPosting, query: JobSearchQuery) -> float:
        searchable = f"{job.title} {job.description}".casefold()
        title = job.title.casefold()
        location = job.location.casefold()

        if query.target_roles and job.target_role not in query.target_roles:
            return -1
        if query.remote_only and job.workplace_type not in {"remote", "hybrid"}:
            return -1
        if query.locations and not any(
            expected.casefold() in location for expected in query.locations
        ):
            return -1

        score = 0.0
        terms = [term for term in re.findall(r"[\w+#.-]+", query.text.casefold()) if len(term) > 1]
        role_keywords = [item.casefold().strip() for item in query.role_keywords if item.strip()]
        if terms and not any(term in searchable for term in terms):
            return -1
        for term in terms:
            if term in title:
                score += 3.0
            elif term in searchable:
                score += 1.0
        for keyword in role_keywords:
            if keyword in title:
                score += 5.0
            elif keyword in searchable:
                score += 2.0
        if job.workplace_type == "remote":
            score += 0.25
        return round(score, 3)


def captured_job_to_posting(capture: BrowserJobCapture) -> JobPosting:
    """Normalize a job captured from a user-visible browser tab."""
    external_id = capture.external_id.strip() or hashlib.sha256(
        f"{capture.source_url}\x1f{capture.title}".encode("utf-8")
    ).hexdigest()[:20]
    posting = JobPosting(
        job_id=_stable_job_id(capture.provider, "browser", external_id),
        provider=capture.provider,
        board_token="browser",
        external_id=external_id,
        company=capture.company.strip(),
        title=capture.title.strip(),
        description=capture.description.strip(),
        location=capture.location.strip(),
        workplace_type=_workplace_type(
            capture.title,
            capture.location,
            capture.description,
        ),
        source_url=capture.source_url,
        apply_url=capture.apply_url,
        workflow_key=capture.workflow_key,
        canonical_url=capture.canonical_url,
        description_source=capture.description_source,
        capture_confidence=capture.capture_confidence,
        warnings=list(capture.warnings),
        published_at=capture.published_at,
    )
    return enrich_job(posting)
