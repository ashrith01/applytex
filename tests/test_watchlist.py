"""Watchlist: curated boards, scheduled ingestion, stable first-seen, ranked feed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from latex_resume import watchlist_cli
from latex_resume.api import create_app
from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import (
    CandidateProfile,
    JobPosting,
    JobProvider,
    JobSourceConfig,
    TargetRole,
    WatchlistEntry,
)
from latex_resume.job_sources import JobSearchService, PublicJobBoardClient
from latex_resume.watchlist import (
    WatchlistIngestor,
    load_seed,
    refresh_interval_minutes,
    seed_entries_for_profile,
)


def _posting(source: JobSourceConfig, external_id: str, title: str, *, location: str = "Remote - US", description: str = "") -> JobPosting:
    return JobPosting(
        job_id=f"{source.provider.value}:{source.board_token}:{external_id}",
        provider=source.provider,
        board_token=source.board_token,
        external_id=external_id,
        company=source.company,
        title=title,
        description=description or f"{title}. Python, PyTorch, LLM evaluation. Remote in the United States.",
        location=location,
        workplace_type="remote" if "remote" in location.casefold() else "onsite",
        source_url=f"https://example.test/{external_id}",
        apply_url=f"https://example.test/{external_id}/apply",
    )


class FakeBoardClient(PublicJobBoardClient):
    """Deterministic board fetcher: one board fails, the others return fixed jobs."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def fetch(self, source: JobSourceConfig) -> list[JobPosting]:
        self.calls.append(source.board_token)
        if source.board_token == "broken":
            raise RuntimeError("HTTP 404")
        if source.board_token == "acme":
            return [
                _posting(source, "1", "Machine Learning Engineer"),
                _posting(source, "2", "Senior Accountant", description="GAAP reporting and audits."),
                _posting(source, "3", "Data Scientist", location="Austin, TX"),
            ]
        return [_posting(source, "9", "Robotics Perception ML Engineer")]


def _entry(profile_id: str, token: str, company: str, tags: list[str] | None = None, provider: JobProvider = JobProvider.GREENHOUSE) -> WatchlistEntry:
    return WatchlistEntry(entry_id=f"e-{token}", profile_id=profile_id, provider=provider, board_token=token, company=company, domain_tags=tags or [])


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def test_seed_is_verified_public_boards_only() -> None:
    entries = load_seed()
    assert 50 <= len(entries) <= 150
    assert all(entry["provider"] in {"greenhouse", "lever", "ashby"} for entry in entries)
    assert all(entry["ai_roles_at_verification"] > 0 for entry in entries)
    assert all(entry["domain_tags"] for entry in entries)
    robotics = load_seed(["robotics"])
    assert robotics and all("robotics" in entry["domain_tags"] for entry in robotics)
    autonomous = load_seed(["autonomous_driving"])
    assert {entry["company"] for entry in autonomous} >= {"Waymo", "Zoox", "Nuro"}
    assert len({(entry["provider"], entry["board_token"]) for entry in entries}) == len(entries), "no duplicate boards"
    for entry in seed_entries_for_profile("p1", ["llm"])[:3]:
        assert entry.profile_id == "p1" and entry.enabled


def test_refresh_interval_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLYTEX_WATCHLIST_REFRESH_MINUTES", raising=False)
    assert refresh_interval_minutes() == 180
    monkeypatch.setenv("APPLYTEX_WATCHLIST_REFRESH_MINUTES", "0")
    assert refresh_interval_minutes() == 0
    monkeypatch.setenv("APPLYTEX_WATCHLIST_REFRESH_MINUTES", "nope")
    assert refresh_interval_minutes() == 180


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_upsert_by_board_keeps_identity_and_merges_tags(tmp_path: Path) -> None:
    store = ApplicationStore(tmp_path / "wl.db")
    first = store.upsert_watchlist_entry(_entry("p1", "acme", "Acme", ["llm"]))
    second = store.upsert_watchlist_entry(WatchlistEntry(entry_id="other", profile_id="p1", provider=JobProvider.GREENHOUSE, board_token="acme", company="Acme Inc", domain_tags=["robotics"]))
    assert second.entry_id == first.entry_id
    assert second.company == "Acme Inc"
    assert second.domain_tags == ["llm", "robotics"]
    assert [entry.entry_id for entry in store.list_watchlist_entries("p1")] == [first.entry_id]
    assert store.list_watchlist_entries("p2") == []
    disabled = store.update_watchlist_entry("p1", first.entry_id, {"enabled": False})
    assert disabled.enabled is False
    assert store.list_watchlist_entries("p1", enabled_only=True) == []
    assert store.list_profiles_with_watchlists() == ["p1"]
    with pytest.raises(KeyError):
        store.update_watchlist_entry("p2", first.entry_id, {"enabled": True})
    assert store.delete_watchlist_entry("p1", first.entry_id) is True
    assert store.delete_watchlist_entry("p1", first.entry_id) is False


def test_watchlist_entry_rejects_browser_only_providers() -> None:
    with pytest.raises(ValueError):
        WatchlistEntry(entry_id="x", provider=JobProvider.LINKEDIN, board_token="a", company="A")
    with pytest.raises(ValueError):
        WatchlistEntry(entry_id="x", provider=JobProvider.GREENHOUSE, board_token="a/../b", company="A")


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------


def _profile_with_preferences(store: ApplicationStore, profile_id: str = "default") -> CandidateProfile:
    profile = store.get_candidate_profile(profile_id)
    profile.search_preferences.target_roles = [TargetRole.ML_ENGINEER, TargetRole.DATA_SCIENTIST]
    profile.search_preferences.preferred_locations = ["Austin, TX"]
    return store.save_candidate_profile(profile)


def test_refresh_filters_by_preferences_scores_and_keeps_first_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPLYTEX_WATCHLIST_STRICT", raising=False)
    store = ApplicationStore(tmp_path / "ingest.db")
    _profile_with_preferences(store)
    store.upsert_watchlist_entry(_entry("default", "acme", "Acme", ["llm"]))
    store.upsert_watchlist_entry(_entry("default", "robo", "Robo", ["robotics"]))
    store.upsert_watchlist_entry(_entry("default", "broken", "Broken Co"))
    client = FakeBoardClient()
    ingestor = WatchlistIngestor(store, board_client=client)

    run = asyncio.run(ingestor.refresh("default", trigger="cli"))
    assert sorted(client.calls) == ["acme", "broken", "robo"]
    assert run.source_count == 3
    assert run.fetched_jobs == 4
    assert run.matched_jobs == 3, "the accountant role is filtered out by target-role preferences"
    assert run.new_jobs == 3 and run.updated_jobs == 0
    assert [error.board_token for error in run.errors] == ["broken"]

    entries = {entry.board_token: entry for entry in store.list_watchlist_entries("default")}
    assert entries["acme"].last_job_count == 3 and entries["acme"].last_matched_count == 2
    assert entries["broken"].last_error.startswith("RuntimeError")
    assert entries["acme"].last_checked_at

    feed = store.list_feed_jobs("default")
    assert {job.title for job in feed} == {"Machine Learning Engineer", "Data Scientist", "Robotics Perception ML Engineer"}
    assert all(job.first_seen_at and job.watchlist_entry_id and job.captured_for_profile_id == "default" for job in feed)
    assert all(job.fit_score is None for job in feed), "no resume saved -> no fit score, never a fake one"
    first_seen = {job.job_id: job.first_seen_at for job in feed}

    second = asyncio.run(ingestor.refresh("default"))
    assert second.new_jobs == 0 and second.updated_jobs == 3
    assert {job.job_id: job.first_seen_at for job in store.list_feed_jobs("default")} == first_seen
    assert store.list_feed_jobs("default", domain_tags=["robotics"])[0].company == "Robo"
    assert store.list_feed_jobs("default", since="2999-01-01T00:00:00+00:00") == []
    assert len(store.list_ingestion_runs("default")) == 2


def test_refresh_scores_fit_when_profile_resume_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_WATCHLIST_STRICT", "0")
    store = ApplicationStore(tmp_path / "fit.db")
    profile = store.get_candidate_profile("default")
    profile.resume_latex_source = (Path(__file__).parent.parent / "samples" / "sample_resume.tex").read_text(encoding="utf-8")
    profile.resume_filename = "sample_resume.tex"
    store.save_candidate_profile(profile)
    store.upsert_watchlist_entry(_entry("default", "acme", "Acme"))
    run = asyncio.run(WatchlistIngestor(store, board_client=FakeBoardClient()).refresh("default"))
    assert run.matched_jobs == 3, "strict=0 keeps every posting"
    feed = store.list_feed_jobs("default")
    assert all(job.fit_score is not None for job in feed)
    assert feed[0].fit_score >= feed[-1].fit_score, "best fit first"
    assert store.list_feed_jobs("default", min_fit=101) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_watchlist_routes_and_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLYTEX_WATCHLIST_REFRESH_MINUTES", "0")
    monkeypatch.delenv("APPLYTEX_WATCHLIST_STRICT", raising=False)
    store = ApplicationStore(tmp_path / "api.db")
    _profile_with_preferences(store)
    app = create_app(application_store=store, job_search_service=JobSearchService(board_client=FakeBoardClient()))
    with TestClient(app) as client:
        assert client.get("/watchlist").json() == {"entries": [], "last_run": None}
        preview = client.get("/watchlist/seed", params={"domain": ["robotics"]}).json()
        assert preview["entries"] and "robotics" in preview["domain_tags"]
        assert all("robotics" in entry["domain_tags"] for entry in preview["entries"]), "domain filter applies"

        added = client.post("/watchlist", json={"entries": [
            {"provider": "greenhouse", "board_token": "acme", "company": "Acme", "domain_tags": ["LLM"]},
            {"provider": "ashby", "board_token": "robo", "company": "Robo", "domain_tags": ["robotics"]},
        ]})
        assert added.status_code == 200
        entries = added.json()["entries"]
        assert [entry["company"] for entry in entries] == ["Acme", "Robo"]
        assert entries[0]["domain_tags"] == ["llm"], "tags are normalized"

        rejected = client.post("/watchlist", json={"entries": [{"provider": "linkedin", "board_token": "x", "company": "X"}]})
        assert rejected.status_code == 422

        run = client.post("/watchlist/refresh").json()
        assert run["trigger"] == "manual" and run["new_jobs"] == 3 and run["errors"] == []

        feed = client.get("/jobs/feed", params={"since": "24h"}).json()
        assert feed["total"] == 3 and feed["applied_job_ids"] == []
        assert feed["since"]
        assert client.get("/jobs/feed", params={"since": "garbage"}).status_code == 400
        only_robotics = client.get("/jobs/feed", params={"domain": ["robotics"]}).json()
        assert [job["company"] for job in only_robotics["jobs"]] == ["Robo"]

        job_id = feed["jobs"][0]["job_id"]
        assert client.post("/applications", json={"job_id": job_id}).status_code in {200, 201}
        assert client.get("/jobs/feed").json()["applied_job_ids"] == [job_id]

        acme_id = entries[0]["entry_id"]
        assert client.patch(f"/watchlist/{acme_id}", json={"enabled": False}).json()["enabled"] is False
        assert client.get("/watchlist").json()["last_run"]["run_id"] == run["run_id"]
        assert client.get("/watchlist/runs").json()[0]["run_id"] == run["run_id"]
        assert client.delete(f"/watchlist/{acme_id}").status_code == 204
        assert client.delete(f"/watchlist/{acme_id}").status_code == 404
        assert client.get("/watchlist", headers={"X-Profile-Id": "someone-else"}).json()["entries"] == []

        seeded = client.post("/watchlist/seed", json={"domain_tags": ["autonomous_driving"]}).json()["entries"]
        assert {entry["company"] for entry in seeded} >= {"Waymo", "Nuro"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_seed_list_refresh_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("APPLYTEX_WATCHLIST_STRICT", raising=False)
    db = tmp_path / "cli.db"
    store = ApplicationStore(db)
    _profile_with_preferences(store)
    store.upsert_watchlist_entry(_entry("default", "acme", "Acme", ["llm"]))
    monkeypatch.setattr(watchlist_cli, "WatchlistIngestor", lambda store: WatchlistIngestor(store, board_client=FakeBoardClient()))

    assert watchlist_cli.main(["--db", str(db), "list"]) == 0
    assert "acme" in capsys.readouterr().out

    assert watchlist_cli.main(["--db", str(db), "refresh"]) == 0
    out = capsys.readouterr().out
    assert "matched 2" in out and "new 2" in out

    assert watchlist_cli.main(["--db", str(db), "feed", "--since", "7d"]) == 0
    out = capsys.readouterr().out
    assert "Machine Learning Engineer" in out and "2 matches" in out

    digest = tmp_path / "digest.md"
    assert watchlist_cli.main(["--db", str(db), "feed", "--markdown", str(digest)]) == 0
    text = digest.read_text(encoding="utf-8")
    assert text.startswith("# ApplyTeX job feed") and "[apply](https://example.test/1/apply)" in text

    # Seeding adds the bundled boards on top of the existing entry.
    assert watchlist_cli.main(["--db", str(db), "seed", "--domain", "robotics"]) == 0
    assert "boards for profile 'default'" in capsys.readouterr().out
    assert len(store.list_watchlist_entries("default")) > 10
    assert watchlist_cli.main(["--db", str(db), "seed", "--domain", "no-such-domain"]) == 1
