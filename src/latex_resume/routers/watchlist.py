"""Watchlist routes: /watchlist*, curated boards and scheduled discovery."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from latex_resume.job_models import (
    IngestionRun,
    JobProvider,
    WatchlistEntry,
    require_public_board_provider,
    validate_board_token,
)
from latex_resume.routers._deps import resolve_request_profile_id
from latex_resume.watchlist import load_seed, seed_entries_for_profile

router = APIRouter()


class WatchlistEntryInput(BaseModel):
    provider: JobProvider
    board_token: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=160)
    domain_tags: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True

    # Validate at the request boundary so a bad board is a 422, not a 500.
    @field_validator("provider")
    @classmethod
    def _public_provider(cls, value: JobProvider) -> JobProvider:
        return require_public_board_provider(value)

    @field_validator("board_token")
    @classmethod
    def _token(cls, value: str) -> str:
        return validate_board_token(value)


class WatchlistAddRequest(BaseModel):
    entries: list[WatchlistEntryInput] = Field(min_length=1, max_length=200)


class WatchlistSeedRequest(BaseModel):
    domain_tags: list[str] = Field(default_factory=list, max_length=12)


class WatchlistPatch(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=160)
    domain_tags: list[str] | None = Field(default=None, max_length=12)
    enabled: bool | None = None


class WatchlistResponse(BaseModel):
    entries: list[WatchlistEntry] = Field(default_factory=list)
    last_run: IngestionRun | None = None


class WatchlistSeedPreview(BaseModel):
    entries: list[dict] = Field(default_factory=list)
    domain_tags: list[str] = Field(default_factory=list)


def _scoped(request: Request, x_profile_id: str | None, profile_id: str | None) -> str:
    return resolve_request_profile_id(request=request, x_profile_id=x_profile_id, profile_id=profile_id)


@router.get("/watchlist", response_model=WatchlistResponse)
async def list_watchlist(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> WatchlistResponse:
    scoped = _scoped(request, x_profile_id, profile_id)
    store = request.app.state.application_store
    runs = store.list_ingestion_runs(scoped, limit=1)
    return WatchlistResponse(entries=store.list_watchlist_entries(scoped), last_run=runs[0] if runs else None)


@router.get("/watchlist/seed", response_model=WatchlistSeedPreview)
async def preview_seed(domain: Annotated[list[str] | None, Query()] = None) -> WatchlistSeedPreview:
    """Show the bundled verified boards without adding them."""
    entries = load_seed(domain)
    tags = sorted({str(tag) for entry in entries for tag in entry.get("domain_tags", [])})
    return WatchlistSeedPreview(entries=entries, domain_tags=tags)


@router.post("/watchlist/seed", response_model=WatchlistResponse)
async def seed_watchlist(
    request: Request,
    body: WatchlistSeedRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> WatchlistResponse:
    """Add the bundled verified boards (optionally only some domains) to this profile."""
    scoped = _scoped(request, x_profile_id, profile_id)
    store = request.app.state.application_store
    for entry in seed_entries_for_profile(scoped, body.domain_tags):
        store.upsert_watchlist_entry(entry)
    return WatchlistResponse(entries=store.list_watchlist_entries(scoped))


@router.post("/watchlist", response_model=WatchlistResponse)
async def add_watchlist_entries(
    request: Request,
    body: WatchlistAddRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> WatchlistResponse:
    scoped = _scoped(request, x_profile_id, profile_id)
    store = request.app.state.application_store
    for item in body.entries:
        store.upsert_watchlist_entry(
            WatchlistEntry(
                entry_id=str(uuid.uuid4()),
                profile_id=scoped,
                provider=item.provider,
                board_token=item.board_token,
                company=item.company,
                domain_tags=item.domain_tags,
                enabled=item.enabled,
            )
        )
    return WatchlistResponse(entries=store.list_watchlist_entries(scoped))


@router.patch("/watchlist/{entry_id}", response_model=WatchlistEntry)
async def patch_watchlist_entry(
    request: Request,
    entry_id: str,
    body: WatchlistPatch,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> WatchlistEntry:
    scoped = _scoped(request, x_profile_id, profile_id)
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    try:
        return request.app.state.application_store.update_watchlist_entry(scoped, entry_id, updates)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/watchlist/{entry_id}", status_code=204)
async def delete_watchlist_entry(
    request: Request,
    entry_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> None:
    scoped = _scoped(request, x_profile_id, profile_id)
    if not request.app.state.application_store.delete_watchlist_entry(scoped, entry_id):
        raise HTTPException(404, f"Watchlist entry '{entry_id}' not found.")


@router.post("/watchlist/refresh", response_model=IngestionRun)
async def refresh_watchlist(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> IngestionRun:
    """Fetch every enabled board now and merge matches into the feed."""
    scoped = _scoped(request, x_profile_id, profile_id)
    return await request.app.state.watchlist_ingestor.refresh(scoped, trigger="manual")


@router.get("/watchlist/runs", response_model=list[IngestionRun])
async def list_watchlist_runs(
    request: Request,
    limit: int = 20,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> list[IngestionRun]:
    scoped = _scoped(request, x_profile_id, profile_id)
    return request.app.state.application_store.list_ingestion_runs(scoped, limit=min(max(limit, 1), 100))
