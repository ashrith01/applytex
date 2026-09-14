"""SQLite persistence for job discovery and controlled application state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from latex_resume.data_protection import (
    DataProtection,
    protect_profile_payload,
    protect_submission_fields,
    protection_from_env,
    unprotect_profile_payload,
    unprotect_submission_fields,
)
from latex_resume.job_models import (
    ALLOWED_APPLICATION_TRANSITIONS,
    APPLY_RUN_TRANSITIONS,
    ApplicationArtifact,
    ApplyRun,
    ApplyRunLogEntry,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationDetail,
    ApplicationEvent,
    ApplicationRecord,
    ApplicationStage,
    ApplicationStatus,
    ApplicationTask,
    CandidateProfile,
    FormScan,
    IngestionRun,
    JobPosting,
    JobSearchResult,
    ProjectRecord,
    ProjectSource,
    SavedAnswer,
    SubmissionBundle,
    WatchlistEntry,
    utc_now,
)


class InvalidApplyRunTransition(ValueError):
    """Raised when a run is moved to a status the executor/user protocol forbids."""


class InvalidApplicationTransition(ValueError):
    """Raised when an application attempts an unsafe state transition."""


def _stage_for_status(status: ApplicationStatus) -> ApplicationStage:
    """Return the default tracker bucket for a workflow state."""
    if status in {ApplicationStatus.DISCOVERED, ApplicationStatus.SCORED}:
        return ApplicationStage.SAVED
    if status is ApplicationStatus.SELECTED:
        return ApplicationStage.SELECTED
    if status is ApplicationStatus.RESUME_READY:
        return ApplicationStage.TAILORING
    if status in {ApplicationStatus.FORM_SCANNED, ApplicationStatus.NEEDS_INPUT}:
        return ApplicationStage.FORM_REVIEW
    if status in {ApplicationStatus.READY_FOR_REVIEW, ApplicationStatus.APPROVED, ApplicationStatus.SUBMITTING}:
        return ApplicationStage.READY_TO_SUBMIT
    if status is ApplicationStatus.SUBMITTED:
        return ApplicationStage.SUBMITTED
    if status is ApplicationStatus.BLOCKED:
        return ApplicationStage.BLOCKED
    if status is ApplicationStatus.SKIPPED:
        return ApplicationStage.SKIPPED
    if status is ApplicationStatus.FAILED:
        return ApplicationStage.BLOCKED
    return ApplicationStage.SAVED


_APPLICATION_STATUS_RANK: dict[ApplicationStatus, int] = {
    status: index
    for index, status in enumerate(
        [
            ApplicationStatus.DISCOVERED,
            ApplicationStatus.SCORED,
            ApplicationStatus.SELECTED,
            ApplicationStatus.RESUME_READY,
            ApplicationStatus.FORM_SCANNED,
            ApplicationStatus.NEEDS_INPUT,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.APPROVED,
            ApplicationStatus.SUBMITTING,
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.BLOCKED,
            ApplicationStatus.FAILED,
            ApplicationStatus.SKIPPED,
        ]
    )
}

_APPLICATION_STAGE_RANK: dict[ApplicationStage, int] = {
    stage: index
    for index, stage in enumerate(
        [
            ApplicationStage.SAVED,
            ApplicationStage.SELECTED,
            ApplicationStage.TAILORING,
            ApplicationStage.FORM_REVIEW,
            ApplicationStage.READY_TO_SUBMIT,
            ApplicationStage.SUBMITTED,
            ApplicationStage.INTERVIEW,
            ApplicationStage.OFFER,
            ApplicationStage.REJECTED,
            ApplicationStage.BLOCKED,
            ApplicationStage.SKIPPED,
        ]
    )
}

_PRIORITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


def _latest_text(*values: str | None) -> str:
    return max([value for value in values if value] or [""])


def _earliest_text(*values: str | None) -> str:
    return min([value for value in values if value] or [""])


def _unique_texts(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in groups:
        for item in group:
            normalized = item.strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                values.append(normalized)
    return values


def _merge_notes(primary: str, secondary: str) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for note in (primary, secondary):
        cleaned = note.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            chunks.append(cleaned)
    return "\n\n--- merged duplicate application notes ---\n\n".join(chunks)


class ApplicationStore:
    """Small local-first SQLite repository for the job application MVP."""

    def __init__(self, path: Path | str, *, protection: DataProtection | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Field-level encryption for EEO / compensation / LLM keys (no-op without a key).
        self._protection = protection or protection_from_env()
        self._initialize()

    @property
    def protection(self) -> DataProtection:
        return self._protection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_runs (
                    search_id TEXT PRIMARY KEY,
                    query_json TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    search_id TEXT,
                    payload_json TEXT NOT NULL,
                    company TEXT NOT NULL,
                    title TEXT NOT NULL,
                    apply_url TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    FOREIGN KEY(search_id) REFERENCES search_runs(search_id)
                );

                CREATE TABLE IF NOT EXISTS applications (
                    application_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS application_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(application_id)
                );

                CREATE TABLE IF NOT EXISTS application_events (
                    event_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(application_id)
                );

                CREATE TABLE IF NOT EXISTS application_tasks (
                    task_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    due_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES applications(application_id)
                );

                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    profile_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidate_projects (
                    project_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS form_scans (
                    scan_id TEXT PRIMARY KEY,
                    application_id TEXT,
                    payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tailor_sessions (
                    session_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    last_accessed REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profile_answers (
                    answer_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    normalized_prompt TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, normalized_prompt)
                );

                CREATE TABLE IF NOT EXISTS watchlist_entries (
                    entry_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    board_token TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(profile_id, provider, board_token)
                );

                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    started_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_usage (
                    profile_id TEXT NOT NULL,
                    day TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, day)
                );

                CREATE TABLE IF NOT EXISTS latex_sessions (
                    session_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    last_accessed REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS apply_runs (
                    run_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS submission_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_watchlist
                    ON jobs(json_extract(payload_json, '$.captured_for_profile_id'))
                    WHERE json_extract(payload_json, '$.watchlist_entry_id') IS NOT NULL;
                """
            )
            self._apply_migrations(connection)

    # Ordered, idempotent schema changes that the CREATE TABLE IF NOT EXISTS
    # baseline cannot express (ALTER TABLE, backfills). Each runs once per DB.
    _MIGRATIONS: tuple[tuple[int, str, str], ...] = (
        (1, "baseline", ""),
    )

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        applied = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name, sql in self._MIGRATIONS:
            if version in applied:
                continue
            if sql:
                connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utc_now()),
            )

    def schema_version(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0) if row else 0

    def save_search(self, result: JobSearchResult) -> None:
        """Atomically persist a search and all returned jobs."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO search_runs
                    (search_id, query_json, sources_json, errors_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    result.search_id,
                    result.query.model_dump_json(),
                    json.dumps(
                        [item.model_dump(mode="json") for item in result.sources],
                        ensure_ascii=True,
                    ),
                    json.dumps(
                        [item.model_dump(mode="json") for item in result.errors],
                        ensure_ascii=True,
                    ),
                    result.created_at,
                ),
            )
            for job in result.jobs:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO jobs
                        (job_id, search_id, payload_json, company, title, apply_url, retrieved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        result.search_id,
                        job.model_dump_json(),
                        job.company,
                        job.title,
                        job.apply_url,
                        job.retrieved_at,
                    ),
                )

    def get_job(self, job_id: str) -> JobPosting | None:
        """Load one normalized job."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return JobPosting.model_validate_json(row["payload_json"]) if row else None

    def save_job(self, job: JobPosting) -> None:
        """Persist a browser-captured or otherwise normalized job."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO jobs
                    (job_id, search_id, payload_json, company, title, apply_url, retrieved_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.model_dump_json(),
                    job.company,
                    job.title,
                    job.apply_url,
                    job.retrieved_at,
                ),
            )

    def list_jobs(
        self,
        limit: int = 100,
        profile_id: str | None = None,
    ) -> list[JobPosting]:
        """List most recently retrieved jobs, optionally scoped to one profile."""
        with self._lock, self._connect() as connection:
            fetch_limit = limit if profile_id is None else max(limit * 50, 1000)
            rows = connection.execute(
                "SELECT payload_json FROM jobs ORDER BY retrieved_at DESC LIMIT ?",
                (fetch_limit,),
            ).fetchall()
        jobs = [JobPosting.model_validate_json(row["payload_json"]) for row in rows]
        if profile_id is None:
            return jobs[:limit]
        application_job_ids = {
            application.job_id
            for application in self.list_applications(limit=10_000, profile_id=profile_id)
        }
        scoped = [
            job
            for job in jobs
            if job.captured_for_profile_id == profile_id or job.job_id in application_job_ids
        ]
        return scoped[:limit]

    def count_jobs(self, profile_id: str | None = None) -> int:
        """Count jobs visible to a profile (or all jobs when unscoped)."""
        if profile_id is None:
            with self._lock, self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
            return int(row["count"]) if row else 0
        return len(self.list_jobs(limit=10_000, profile_id=profile_id))

    def create_application(
        self,
        job_id: str,
        profile_id: str | None = None,
        resume_session_id: str | None = None,
        notes: str = "",
    ) -> ApplicationRecord:
        """Create an application draft for a known job."""
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        resolved_profile_id = profile_id or self.get_active_profile_id()
        record = ApplicationRecord(
            application_id=str(uuid.uuid4()),
            profile_id=resolved_profile_id,
            job_id=job_id,
            job_title=job.title,
            company=job.company,
            provider=job.provider,
            location=job.location,
            workplace_type=job.workplace_type,
            apply_url=job.apply_url,
            source_url=job.source_url,
            resume_session_id=resume_session_id,
            notes=notes,
        )
        self._save_application(record)
        self.create_application_event(
            application_id=record.application_id,
            kind="application_created",
            label="Application tracked",
            detail=f"{job.title} at {job.company}",
        )
        return record

    def find_application_for_job(
        self,
        job_id: str,
        profile_id: str | None = None,
    ) -> ApplicationRecord | None:
        """Return the richest application for a profile/job pair, if one exists."""
        resolved_profile_id = profile_id or self.get_active_profile_id()
        matches = [
            application
            for application in self._list_all_applications()
            if application.job_id == job_id and application.profile_id == resolved_profile_id
        ]
        if not matches:
            return None
        return max(matches, key=self._application_richness_score)

    def get_or_create_application(
        self,
        job_id: str,
        profile_id: str | None = None,
        resume_session_id: str | None = None,
        notes: str = "",
        force_new: bool = False,
    ) -> ApplicationRecord:
        """Return the existing profile/job application unless explicitly forced."""
        resolved_profile_id = profile_id or self.get_active_profile_id()
        if not force_new:
            self.dedupe_applications()
            existing = self.find_application_for_job(job_id, resolved_profile_id)
            if existing is not None:
                updates: dict[str, object] = {}
                if resume_session_id and not existing.resume_session_id:
                    updates["resume_session_id"] = resume_session_id
                merged_notes = _merge_notes(existing.notes, notes)
                if merged_notes != existing.notes:
                    updates["notes"] = merged_notes
                if updates:
                    return self.update_application(existing.application_id, updates)
                return existing
        return self.create_application(
            job_id=job_id,
            profile_id=resolved_profile_id,
            resume_session_id=resume_session_id,
            notes=notes,
        )

    def get_application(self, application_id: str) -> ApplicationRecord | None:
        """Load one application record."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM applications WHERE application_id = ?",
                (application_id,),
            ).fetchone()
        return ApplicationRecord.model_validate_json(row["payload_json"]) if row else None

    def list_applications(
        self,
        limit: int = 100,
        profile_id: str | None = None,
    ) -> list[ApplicationRecord]:
        """List applications ordered by their latest update."""
        with self._lock, self._connect() as connection:
            fetch_limit = limit if profile_id is None else max(limit * 50, 1000)
            rows = connection.execute(
                "SELECT payload_json FROM applications ORDER BY updated_at DESC LIMIT ?",
                (fetch_limit,),
            ).fetchall()
        applications = [
            ApplicationRecord.model_validate_json(row["payload_json"])
            for row in rows
        ]
        if profile_id is not None:
            applications = [
                application
                for application in applications
                if application.profile_id == profile_id
            ]
        return applications[:limit]

    def get_last_dedupe_count(self) -> int:
        """Return the most recent automatic duplicate cleanup count."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                ("last_dedupe_merged_count",),
            ).fetchone()
        if not row:
            return 0
        try:
            return max(0, int(row["value"]))
        except (TypeError, ValueError):
            return 0

    def dedupe_applications(self) -> int:
        """Merge accidental duplicate applications for the same profile/job."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM applications"
            ).fetchall()
            applications = [
                ApplicationRecord.model_validate_json(row["payload_json"])
                for row in rows
            ]
            groups: dict[tuple[str, str], list[ApplicationRecord]] = {}
            for application in applications:
                groups.setdefault(
                    (application.profile_id or "default", application.job_id),
                    [],
                ).append(application)

            merged_count = 0
            for group in groups.values():
                if len(group) < 2:
                    continue
                canonical = max(group, key=self._application_richness_score)
                duplicates = [
                    application
                    for application in group
                    if application.application_id != canonical.application_id
                ]
                for duplicate in duplicates:
                    canonical = self._merge_application_records(canonical, duplicate)
                    self._move_application_children(
                        connection,
                        old_application_id=duplicate.application_id,
                        new_application_id=canonical.application_id,
                    )
                    connection.execute(
                        "DELETE FROM applications WHERE application_id = ?",
                        (duplicate.application_id,),
                    )
                    merged_count += 1
                connection.execute(
                    """
                    INSERT OR REPLACE INTO applications
                        (application_id, job_id, payload_json, status, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        canonical.application_id,
                        canonical.job_id,
                        canonical.model_dump_json(),
                        canonical.status.value,
                        canonical.updated_at,
                    ),
                )

            if merged_count:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    ("last_dedupe_merged_count", str(merged_count), utc_now()),
                )
        return merged_count

    def get_application_detail(self, application_id: str) -> ApplicationDetail | None:
        """Return one application with joined job, artifacts, events, tasks, and scan."""
        application = self.get_application(application_id)
        if application is None:
            return None
        return ApplicationDetail(
            application=application,
            job=self.get_job(application.job_id),
            artifacts=self.list_application_artifacts(application_id),
            events=self.list_application_events(application_id),
            tasks=self.list_application_tasks(application_id),
            latest_form_scan=self.get_latest_form_scan(application_id),
        )

    def update_application(
        self,
        application_id: str,
        updates: dict[str, object],
    ) -> ApplicationRecord:
        """Patch tracker metadata without bypassing explicit status transitions."""
        current = self.get_application(application_id)
        if current is None:
            raise KeyError(f"Unknown application_id: {application_id}")
        if "status" in updates:
            raise ValueError("Use transition_application for status changes.")
        now = utc_now()
        updated = ApplicationRecord.model_validate(
            {
                **current.model_dump(mode="json"),
                **updates,
                "updated_at": now,
                "last_activity_at": now,
            }
        )
        self._save_application(updated)
        return updated

    def get_candidate_profile(self, profile_id: str = "default") -> CandidateProfile:
        """Return the saved profile or a conservative default profile."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM candidate_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row:
            return self._load_profile(row["payload_json"])
        profile = CandidateProfile(profile_id=profile_id)
        return self.save_candidate_profile(profile)

    def _load_profile(self, payload_json: str) -> CandidateProfile:
        return CandidateProfile.model_validate(
            unprotect_profile_payload(json.loads(payload_json), self._protection)
        )

    def list_candidate_profiles(self) -> list[CandidateProfile]:
        """Return all persisted candidate profiles, newest first."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM candidate_profiles
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._load_profile(row["payload_json"]) for row in rows]

    def candidate_profile_exists(self, profile_id: str) -> bool:
        """Return whether a profile row already exists (without creating one)."""
        cleaned = profile_id.strip()
        if not cleaned:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM candidate_profiles WHERE profile_id = ?",
                (cleaned,),
            ).fetchone()
        return row is not None

    def save_candidate_profile(self, profile: CandidateProfile) -> CandidateProfile:
        """Persist user-owned profile facts and reusable exact answers."""
        updated = profile.model_copy(update={"updated_at": utc_now()})
        payload = protect_profile_payload(updated.model_dump(mode="json"), self._protection)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO candidate_profiles
                    (profile_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    updated.profile_id,
                    json.dumps(payload, ensure_ascii=True),
                    updated.updated_at,
                ),
            )
        return updated

    # ------------------------------------------------------------------
    # LLM usage (per profile, per UTC day) for budgets
    # ------------------------------------------------------------------

    def record_llm_usage(self, profile_id: str, calls: int, tokens: int) -> dict[str, int]:
        day = utc_now()[:10]
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO llm_usage (profile_id, day, calls, tokens, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, day) DO UPDATE SET
                    calls = calls + excluded.calls,
                    tokens = tokens + excluded.tokens,
                    updated_at = excluded.updated_at
                """,
                (profile_id, day, max(0, calls), max(0, tokens), utc_now()),
            )
        return self.get_llm_usage_today(profile_id)

    def get_llm_usage_today(self, profile_id: str) -> dict[str, int]:
        day = utc_now()[:10]
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT calls, tokens FROM llm_usage WHERE profile_id = ? AND day = ?",
                (profile_id, day),
            ).fetchone()
        return {"calls": int(row["calls"]) if row else 0, "tokens": int(row["tokens"]) if row else 0, "day": day}  # type: ignore[dict-item]

    # ------------------------------------------------------------------
    # Classic /latex sessions (durable; mirrors tailor_sessions)
    # ------------------------------------------------------------------

    def save_latex_session_payload(
        self,
        *,
        session_id: str,
        profile_id: str,
        payload: dict[str, Any],
        created_at: float,
        last_accessed: float,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO latex_sessions
                    (session_id, profile_id, payload_json, last_accessed, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, profile_id, json.dumps(payload, ensure_ascii=True, default=str), last_accessed, created_at),
            )

    def get_latex_session_row(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT session_id, profile_id, payload_json, last_accessed, created_at FROM latex_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "profile_id": row["profile_id"],
            "payload": json.loads(row["payload_json"]),
            "last_accessed": float(row["last_accessed"]),
            "created_at": float(row["created_at"]),
        }

    def delete_latex_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM latex_sessions WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    def cleanup_expired_latex_sessions(self, ttl_seconds: float) -> int:
        cutoff = time.time() - ttl_seconds
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM latex_sessions WHERE last_accessed < ?", (cutoff,))
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Watchlist and feed
    # ------------------------------------------------------------------

    def list_watchlist_entries(
        self,
        profile_id: str,
        *,
        enabled_only: bool = False,
    ) -> list[WatchlistEntry]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM watchlist_entries WHERE profile_id = ? ORDER BY updated_at DESC",
                (profile_id,),
            ).fetchall()
        entries = [WatchlistEntry.model_validate_json(row["payload_json"]) for row in rows]
        if enabled_only:
            entries = [entry for entry in entries if entry.enabled]
        return sorted(entries, key=lambda entry: entry.company.casefold())

    def get_watchlist_entry(self, profile_id: str, entry_id: str) -> WatchlistEntry | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM watchlist_entries WHERE profile_id = ? AND entry_id = ?",
                (profile_id, entry_id),
            ).fetchone()
        return WatchlistEntry.model_validate_json(row["payload_json"]) if row else None

    def upsert_watchlist_entry(self, entry: WatchlistEntry) -> WatchlistEntry:
        """Insert or update by (profile, provider, board); keeps identity and stats."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM watchlist_entries
                WHERE profile_id = ? AND provider = ? AND board_token = ?
                """,
                (entry.profile_id, entry.provider.value, entry.board_token),
            ).fetchone()
            existing = WatchlistEntry.model_validate_json(row["payload_json"]) if row else None
            stored = entry.model_copy(
                update={
                    "entry_id": existing.entry_id if existing else entry.entry_id,
                    "domain_tags": list(dict.fromkeys([*(existing.domain_tags if existing else []), *entry.domain_tags]))[:12],
                    "last_checked_at": existing.last_checked_at if existing else entry.last_checked_at,
                    "last_error": existing.last_error if existing else entry.last_error,
                    "last_job_count": existing.last_job_count if existing else entry.last_job_count,
                    "last_matched_count": existing.last_matched_count if existing else entry.last_matched_count,
                    "created_at": existing.created_at if existing else entry.created_at,
                    "updated_at": utc_now(),
                }
            )
            connection.execute(
                """
                INSERT INTO watchlist_entries
                    (entry_id, profile_id, provider, board_token, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, provider, board_token) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    stored.entry_id,
                    stored.profile_id,
                    stored.provider.value,
                    stored.board_token,
                    stored.model_dump_json(),
                    stored.updated_at,
                ),
            )
        return stored

    def update_watchlist_entry(
        self,
        profile_id: str,
        entry_id: str,
        updates: dict[str, Any],
    ) -> WatchlistEntry:
        entry = self.get_watchlist_entry(profile_id, entry_id)
        if entry is None:
            raise KeyError(f"Watchlist entry '{entry_id}' not found.")
        updated = entry.model_copy(update={**updates, "updated_at": utc_now()})
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE watchlist_entries SET payload_json = ?, updated_at = ? WHERE entry_id = ?",
                (updated.model_dump_json(), updated.updated_at, entry_id),
            )
        return updated

    def delete_watchlist_entry(self, profile_id: str, entry_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM watchlist_entries WHERE profile_id = ? AND entry_id = ?",
                (profile_id, entry_id),
            )
        return cursor.rowcount > 0

    def list_profiles_with_watchlists(self) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT profile_id FROM watchlist_entries ORDER BY profile_id"
            ).fetchall()
        return [str(row["profile_id"]) for row in rows]

    def save_ingestion_run(self, run: IngestionRun) -> IngestionRun:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO ingestion_runs (run_id, profile_id, payload_json, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (run.run_id, run.profile_id, run.model_dump_json(), run.started_at),
            )
        return run

    def list_ingestion_runs(self, profile_id: str, limit: int = 20) -> list[IngestionRun]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM ingestion_runs
                WHERE profile_id = ? ORDER BY started_at DESC LIMIT ?
                """,
                (profile_id, max(1, limit)),
            ).fetchall()
        return [IngestionRun.model_validate_json(row["payload_json"]) for row in rows]

    def upsert_feed_jobs(self, jobs: list[JobPosting]) -> tuple[int, int]:
        """Persist ingested jobs, preserving ``first_seen_at``. Returns (new, updated)."""
        new_count = 0
        updated_count = 0
        now = utc_now()
        with self._lock, self._connect() as connection:
            for job in jobs:
                row = connection.execute(
                    "SELECT payload_json FROM jobs WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()
                if row:
                    existing = JobPosting.model_validate_json(row["payload_json"])
                    stored = job.model_copy(
                        update={
                            "first_seen_at": existing.first_seen_at or existing.retrieved_at,
                            "captured_for_profile_id": job.captured_for_profile_id or existing.captured_for_profile_id,
                        }
                    )
                    updated_count += 1
                else:
                    stored = job.model_copy(update={"first_seen_at": job.first_seen_at or now})
                    new_count += 1
                connection.execute(
                    """
                    INSERT OR REPLACE INTO jobs
                        (job_id, search_id, payload_json, company, title, apply_url, retrieved_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored.job_id,
                        stored.model_dump_json(),
                        stored.company,
                        stored.title,
                        stored.apply_url,
                        stored.retrieved_at,
                    ),
                )
        return new_count, updated_count

    def list_feed_jobs(
        self,
        profile_id: str,
        *,
        since: str | None = None,
        min_fit: float | None = None,
        domain_tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Watchlist-ingested jobs for a profile, best fit first, then newest."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM jobs
                WHERE json_extract(payload_json, '$.watchlist_entry_id') IS NOT NULL
                  AND json_extract(payload_json, '$.captured_for_profile_id') = ?
                """,
                (profile_id,),
            ).fetchall()
        jobs = [JobPosting.model_validate_json(row["payload_json"]) for row in rows]
        wanted_tags = {tag.casefold() for tag in (domain_tags or []) if tag.strip()}
        filtered = [
            job
            for job in jobs
            if (since is None or (job.first_seen_at or job.retrieved_at) >= since)
            and (min_fit is None or (job.fit_score is not None and job.fit_score >= min_fit))
            and (not wanted_tags or wanted_tags.intersection(tag.casefold() for tag in job.domain_tags))
        ]
        filtered.sort(
            key=lambda job: (
                -(job.fit_score if job.fit_score is not None else -1.0),
                job.first_seen_at or job.retrieved_at,
            ),
            reverse=False,
        )
        # Highest fit first; among equal fit, newest first.
        filtered.sort(key=lambda job: job.first_seen_at or job.retrieved_at, reverse=True)
        filtered.sort(key=lambda job: -(job.fit_score if job.fit_score is not None else -1.0))
        return filtered[: max(1, limit)]

    # ------------------------------------------------------------------
    # Answers bank
    # ------------------------------------------------------------------

    def list_profile_answers(self, profile_id: str) -> list[SavedAnswer]:
        """Return remembered answers for one profile, most recently updated first."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM profile_answers
                WHERE profile_id = ?
                ORDER BY updated_at DESC
                """,
                (profile_id,),
            ).fetchall()
        return [SavedAnswer.model_validate_json(row["payload_json"]) for row in rows]

    def get_profile_answer(self, profile_id: str, answer_id: str) -> SavedAnswer | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM profile_answers WHERE profile_id = ? AND answer_id = ?",
                (profile_id, answer_id),
            ).fetchone()
        return SavedAnswer.model_validate_json(row["payload_json"]) if row else None

    def upsert_profile_answer(self, answer: SavedAnswer) -> SavedAnswer:
        """Insert or replace by (profile, normalized prompt), keeping identity and usage."""
        key = answer.normalized_prompt or answer.prompt_text.casefold().strip()
        with self._lock, self._connect() as connection:
            existing_row = connection.execute(
                "SELECT payload_json FROM profile_answers WHERE profile_id = ? AND normalized_prompt = ?",
                (answer.profile_id, key),
            ).fetchone()
            existing = SavedAnswer.model_validate_json(existing_row["payload_json"]) if existing_row else None
            merged_aliases = list(dict.fromkeys([*(existing.aliases if existing else []), *answer.aliases]))
            stored = answer.model_copy(
                update={
                    "answer_id": existing.answer_id if existing else answer.answer_id,
                    "normalized_prompt": key,
                    "aliases": merged_aliases[:32],
                    "use_count": existing.use_count if existing else answer.use_count,
                    "last_used_at": existing.last_used_at if existing else answer.last_used_at,
                    "created_at": existing.created_at if existing else answer.created_at,
                    "updated_at": utc_now(),
                }
            )
            connection.execute(
                """
                INSERT INTO profile_answers
                    (answer_id, profile_id, normalized_prompt, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, normalized_prompt) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    stored.answer_id,
                    stored.profile_id,
                    stored.normalized_prompt,
                    stored.model_dump_json(),
                    stored.updated_at,
                ),
            )
        return stored

    def delete_profile_answer(self, profile_id: str, answer_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM profile_answers WHERE profile_id = ? AND answer_id = ?",
                (profile_id, answer_id),
            )
        return cursor.rowcount > 0

    def record_profile_answer_usage(self, profile_id: str, answer_ids: list[str]) -> int:
        """Bump ``use_count`` / ``last_used_at`` after a reviewed fill used the answers."""
        if not answer_ids:
            return 0
        now = utc_now()
        recorded = 0
        with self._lock, self._connect() as connection:
            for answer_id in dict.fromkeys(answer_ids):
                row = connection.execute(
                    "SELECT payload_json FROM profile_answers WHERE profile_id = ? AND answer_id = ?",
                    (profile_id, answer_id),
                ).fetchone()
                if row is None:
                    continue
                answer = SavedAnswer.model_validate_json(row["payload_json"])
                updated = answer.model_copy(
                    update={"use_count": answer.use_count + 1, "last_used_at": now, "updated_at": now}
                )
                connection.execute(
                    "UPDATE profile_answers SET payload_json = ?, updated_at = ? WHERE answer_id = ?",
                    (updated.model_dump_json(), now, answer_id),
                )
                recorded += 1
        return recorded

    def replace_profile_projects(
        self,
        profile_id: str,
        source: ProjectSource,
        projects: list[ProjectRecord],
    ) -> list[ProjectRecord]:
        """Replace cached project records for one profile/source."""
        now = utc_now()
        updated = [
            project.model_copy(
                update={
                    "profile_id": profile_id,
                    "source": source,
                    "updated_at": project.updated_at or now,
                }
            )
            for project in projects
        ]
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM candidate_projects WHERE profile_id = ? AND source = ?",
                (profile_id, source.value),
            )
            for project in updated:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO candidate_projects
                        (project_id, profile_id, source, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        profile_id,
                        source.value,
                        project.model_dump_json(),
                        project.updated_at,
                    ),
                )
        return updated

    def save_profile_projects(self, projects: list[ProjectRecord]) -> list[ProjectRecord]:
        """Upsert project records without deleting other cached projects."""
        updated = [
            project.model_copy(update={"updated_at": project.updated_at or utc_now()})
            for project in projects
        ]
        with self._lock, self._connect() as connection:
            for project in updated:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO candidate_projects
                        (project_id, profile_id, source, payload_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project.project_id,
                        project.profile_id,
                        project.source.value,
                        project.model_dump_json(),
                        project.updated_at,
                    ),
                )
        return updated

    def list_profile_projects(
        self,
        profile_id: str,
        source: ProjectSource | None = None,
    ) -> list[ProjectRecord]:
        """List cached project records for one profile."""
        clauses = ["profile_id = ?"]
        params: list[object] = [profile_id]
        if source is not None:
            clauses.append("source = ?")
            params.append(source.value)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM candidate_projects
                WHERE {' AND '.join(clauses)}
                ORDER BY source ASC, updated_at DESC
                """,
                tuple(params),
            ).fetchall()
        return [
            ProjectRecord.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_active_profile_id(self) -> str:
        """Return the profile selected in the local Streamlit app."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                ("active_profile_id",),
            ).fetchone()
        return str(row["value"]) if row and row["value"] else "default"

    def set_active_profile_id(self, profile_id: str) -> str:
        """Persist the profile selected in the local Streamlit app."""
        cleaned = profile_id.strip() or "default"
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                ("active_profile_id", cleaned, utc_now()),
            )
        return cleaned

    def get_setting(self, key: str) -> str | None:
        """Return a persisted app setting value."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row and row["value"] is not None else None

    def set_setting(self, key: str, value: str) -> None:
        """Persist an app setting value."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, utc_now()),
            )

    def save_form_scan(self, scan: FormScan) -> FormScan:
        """Persist a read-only extension form inventory."""
        if scan.application_id and self.get_application(scan.application_id) is None:
            raise KeyError(f"Unknown application_id: {scan.application_id}")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO form_scans
                    (scan_id, application_id, payload_json, captured_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    scan.scan_id,
                    scan.application_id,
                    scan.model_dump_json(),
                    scan.captured_at,
                ),
            )
        if scan.application_id:
            missing_required = len([question for question in scan.questions if question.required])
            current = self.get_application(scan.application_id)
            if current is not None:
                updates = {
                    "missing_answers_count": missing_required,
                    "stage": ApplicationStage.FORM_REVIEW,
                }
                try:
                    self.update_application(scan.application_id, updates)
                except ValueError:
                    pass
            self.create_application_event(
                application_id=scan.application_id,
                kind="form_scanned",
                label="Application form scanned",
                detail=f"{len(scan.questions)} fields captured from {scan.provider.value}.",
                payload={"scan_id": scan.scan_id, "required_fields": missing_required},
            )
        return scan

    def get_form_scan(self, scan_id: str) -> FormScan | None:
        """Load one read-only form scan."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM form_scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        return FormScan.model_validate_json(row["payload_json"]) if row else None

    def list_form_scans(self, application_id: str, limit: int = 200) -> list[FormScan]:
        """Every scan for one application, oldest first (multi-step flows produce several)."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM form_scans
                WHERE application_id = ?
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                (application_id, max(1, limit)),
            ).fetchall()
        return [FormScan.model_validate_json(row["payload_json"]) for row in rows]

    # ------------------------------------------------------------------
    # Auth sessions (bearer tokens survive API restarts)
    # ------------------------------------------------------------------

    def save_auth_session(self, *, token_hash: str, profile_id: str, created_at: str, expires_at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO auth_sessions (token_hash, profile_id, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (token_hash, profile_id, created_at, expires_at),
            )

    def get_auth_session(self, token_hash: str) -> dict[str, str] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT profile_id, created_at, expires_at, revoked_at FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None or row["revoked_at"]:
            return None
        return {"profile_id": row["profile_id"], "created_at": row["created_at"], "expires_at": row["expires_at"]}

    def revoke_auth_session(self, token_hash: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (utc_now(), token_hash),
            )
        return cursor.rowcount > 0

    def revoke_profile_auth_sessions(self, profile_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE profile_id = ? AND revoked_at IS NULL",
                (utc_now(), profile_id),
            )
        return cursor.rowcount

    def purge_expired_auth_sessions(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL",
                (utc_now(),),
            )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Data lifecycle: export and delete everything one profile owns
    # ------------------------------------------------------------------

    def export_profile_data(self, profile_id: str) -> dict[str, Any]:
        """Portable JSON of everything stored for one profile (no PDF bytes, no secrets)."""
        profile = self.get_candidate_profile(profile_id).model_dump(
            mode="json", exclude={"resume_pdf_b64": True, "llm_settings": {"api_key"}}
        )
        applications = self.list_applications(limit=10_000, profile_id=profile_id)
        application_ids = [application.application_id for application in applications]
        detail: list[dict[str, Any]] = []
        for application in applications:
            bundle = self.get_submission_bundle(application.application_id)
            detail.append(
                {
                    "application": application.model_dump(mode="json"),
                    "artifacts": [
                        artifact.model_dump(mode="json", exclude={"pdf_b64", "latex_source"})
                        for artifact in self.list_application_artifacts(application.application_id, limit=200)
                    ],
                    "events": [event.model_dump(mode="json") for event in self.list_application_events(application.application_id, limit=1000)],
                    "tasks": [task.model_dump(mode="json") for task in self.list_application_tasks(application.application_id, limit=1000)],
                    "form_scans": [scan.model_dump(mode="json") for scan in self.list_form_scans(application.application_id)],
                    "submission": bundle.model_dump(mode="json") if bundle else None,
                }
            )
        return {
            "format": "applytex-profile-export",
            "version": 1,
            "exported_at": utc_now(),
            "profile": profile,
            "answers": [answer.model_dump(mode="json") for answer in self.list_profile_answers(profile_id)],
            "projects": [project.model_dump(mode="json") for project in self.list_profile_projects(profile_id)],
            "watchlist": [entry.model_dump(mode="json") for entry in self.list_watchlist_entries(profile_id)],
            "ingestion_runs": [run.model_dump(mode="json") for run in self.list_ingestion_runs(profile_id, limit=200)],
            "jobs": [job.model_dump(mode="json") for job in self.list_jobs(limit=10_000, profile_id=profile_id)],
            "applications": detail,
            "apply_runs": [run.model_dump(mode="json") for run in self.list_apply_runs(profile_id, limit=1000)],
            "application_ids": application_ids,
        }

    def delete_profile_data(self, profile_id: str) -> dict[str, int]:
        """Remove every row and on-disk file that belongs to one profile."""
        applications = self.list_applications(limit=10_000, profile_id=profile_id)
        application_ids = [application.application_id for application in applications]
        artifact_paths = [
            artifact.pdf_path
            for application_id in application_ids
            for artifact in self.list_application_artifacts(application_id, limit=500)
            if artifact.pdf_path
        ]
        profile = self.get_candidate_profile(profile_id)
        if profile.resume_pdf_path:
            artifact_paths.append(profile.resume_pdf_path)
        run_ids = [run.run_id for run in self.list_apply_runs(profile_id, limit=10_000)]
        counts: dict[str, int] = {}
        with self._lock, self._connect() as connection:
            def run(label: str, sql: str, params: tuple[object, ...]) -> None:
                counts[label] = counts.get(label, 0) + connection.execute(sql, params).rowcount

            for application_id in application_ids:
                for table in ("application_artifacts", "application_events", "application_tasks", "form_scans", "submission_bundles", "apply_runs"):
                    run(table, f"DELETE FROM {table} WHERE application_id = ?", (application_id,))
                run("applications", "DELETE FROM applications WHERE application_id = ?", (application_id,))
            run("apply_runs", "DELETE FROM apply_runs WHERE profile_id = ?", (profile_id,))
            run("tailor_sessions", "DELETE FROM tailor_sessions WHERE profile_id = ?", (profile_id,))
            run("latex_sessions", "DELETE FROM latex_sessions WHERE profile_id = ?", (profile_id,))
            run("llm_usage", "DELETE FROM llm_usage WHERE profile_id = ?", (profile_id,))
            run("jobs", "DELETE FROM jobs WHERE json_extract(payload_json, '$.captured_for_profile_id') = ?", (profile_id,))
            for table in ("profile_answers", "candidate_projects", "watchlist_entries", "ingestion_runs", "auth_sessions"):
                run(table, f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,))
            run("app_settings", "DELETE FROM app_settings WHERE key = ?", (f"auth.password.{profile_id}",))
            run("candidate_profiles", "DELETE FROM candidate_profiles WHERE profile_id = ?", (profile_id,))
            active = connection.execute("SELECT value FROM app_settings WHERE key = 'active_profile_id'").fetchone()
            if active and active["value"] == profile_id:
                connection.execute("DELETE FROM app_settings WHERE key = 'active_profile_id'")
        removed_files = 0
        for relative in artifact_paths:
            candidate = (self.path.parent / relative).resolve()
            if self.path.parent.resolve() in candidate.parents and candidate.is_file():
                candidate.unlink()
                removed_files += 1
        for run_id in run_ids:
            directory = self.path.parent / "runs" / run_id
            if directory.is_dir():
                for file in directory.iterdir():
                    file.unlink()
                    removed_files += 1
                directory.rmdir()
        counts["files"] = removed_files
        return counts

    # ------------------------------------------------------------------
    # Executor runs
    # ------------------------------------------------------------------

    def create_apply_run(self, run: ApplyRun) -> ApplyRun:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO apply_runs
                    (run_id, application_id, profile_id, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run.run_id, run.application_id, run.profile_id, run.status, run.model_dump_json(), run.created_at, run.updated_at),
            )
        return run

    def get_apply_run(self, run_id: str) -> ApplyRun | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM apply_runs WHERE run_id = ?", (run_id,)).fetchone()
        return ApplyRun.model_validate_json(row["payload_json"]) if row else None

    def list_apply_runs(
        self,
        profile_id: str,
        *,
        statuses: list[str] | None = None,
        application_id: str | None = None,
        limit: int = 100,
    ) -> list[ApplyRun]:
        clauses = ["profile_id = ?"]
        params: list[object] = [profile_id]
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if application_id:
            clauses.append("application_id = ?")
            params.append(application_id)
        params.append(max(1, limit))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM apply_runs WHERE {' AND '.join(clauses)} ORDER BY created_at ASC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [ApplyRun.model_validate_json(row["payload_json"]) for row in rows]

    def count_apply_runs_since(self, profile_id: str, since: str) -> int:
        """Runs created since ``since`` that were not cancelled — the daily-cap counter."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM apply_runs WHERE profile_id = ? AND created_at >= ? AND status != 'cancelled'",
                (profile_id, since),
            ).fetchone()
        return int(row["count"]) if row else 0

    def transition_apply_run(
        self,
        run_id: str,
        target: str,
        *,
        actor: str,
        updates: dict[str, Any] | None = None,
        log_message: str = "",
        log_level: str = "info",
        expected_status: str | None = None,
    ) -> ApplyRun:
        """Move a run along the executor/user protocol; ``running -> running`` is a progress update."""
        run = self.get_apply_run(run_id)
        if run is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        if expected_status is not None and run.status != expected_status:
            raise InvalidApplyRunTransition(
                f"Run is {run.status}, expected {expected_status}."
            )
        allowed = APPLY_RUN_TRANSITIONS.get(run.status, {})
        if target not in allowed:
            raise InvalidApplyRunTransition(f"Cannot move run from {run.status} to {target}.")
        if allowed[target] != actor:
            raise InvalidApplyRunTransition(
                f"Only the {allowed[target]} may move a run from {run.status} to {target}."
            )
        now = utc_now()
        merged: dict[str, Any] = {**(updates or {}), "status": target, "updated_at": now}
        if target == "running" and run.started_at is None:
            merged["started_at"] = now
        if target in {"submitted", "needs_verification", "failed", "cancelled"}:
            merged["finished_at"] = now
        if log_message:
            merged["step_log"] = [
                *run.step_log,
                ApplyRunLogEntry(level=log_level, message=log_message[:1000]),  # type: ignore[arg-type]
            ][-200:]
        updated = run.model_copy(update=merged)
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE apply_runs SET status = ?, payload_json = ?, updated_at = ? WHERE run_id = ?",
                (updated.status, updated.model_dump_json(), updated.updated_at, run_id),
            )
        return updated

    # ------------------------------------------------------------------
    # Submission receipts (insert-only)
    # ------------------------------------------------------------------

    def save_submission_bundle(self, bundle: SubmissionBundle) -> SubmissionBundle:
        payload = bundle.model_dump(mode="json")
        payload["fields"] = protect_submission_fields(payload["fields"], self._protection)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO submission_bundles
                    (bundle_id, application_id, profile_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (bundle.bundle_id, bundle.application_id, bundle.profile_id, json.dumps(payload, ensure_ascii=True), bundle.created_at),
            )
        return bundle

    def get_submission_bundle(self, application_id: str) -> SubmissionBundle | None:
        """Latest receipt for an application, if it was ever confirmed submitted."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM submission_bundles
                WHERE application_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (application_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["fields"] = unprotect_submission_fields(payload.get("fields", []), self._protection)
        return SubmissionBundle.model_validate(payload)

    def list_due_tasks(
        self,
        profile_id: str,
        *,
        due_before: str,
        limit: int = 100,
    ) -> list[tuple[ApplicationTask, ApplicationRecord]]:
        """Open tasks with a due date at or before ``due_before`` for one profile."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT t.payload_json AS task_json, a.payload_json AS application_json
                FROM application_tasks t
                JOIN applications a ON a.application_id = t.application_id
                WHERE t.status = 'open' AND t.due_at IS NOT NULL AND t.due_at <= ?
                  AND json_extract(a.payload_json, '$.profile_id') = ?
                ORDER BY t.due_at ASC
                LIMIT ?
                """,
                (due_before, profile_id, max(1, limit)),
            ).fetchall()
        return [
            (
                ApplicationTask.model_validate_json(row["task_json"]),
                ApplicationRecord.model_validate_json(row["application_json"]),
            )
            for row in rows
        ]

    def complete_application_task(self, task_id: str) -> ApplicationTask:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM application_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            task = ApplicationTask.model_validate_json(row["payload_json"])
            done = task.model_copy(update={"status": "done", "completed_at": utc_now()})
            connection.execute(
                "UPDATE application_tasks SET payload_json = ?, status = ? WHERE task_id = ?",
                (done.model_dump_json(), done.status, task_id),
            )
        return done

    def get_latest_form_scan(self, application_id: str) -> FormScan | None:
        """Return the latest scan for one application, if any."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM form_scans
                WHERE application_id = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (application_id,),
            ).fetchone()
        return FormScan.model_validate_json(row["payload_json"]) if row else None

    def save_application_artifact(
        self,
        artifact: ApplicationArtifact,
    ) -> ApplicationArtifact:
        """Persist a generated or approved artifact and sync application metadata."""
        application = self.get_application(artifact.application_id)
        if application is None:
            raise KeyError(f"Unknown application_id: {artifact.application_id}")
        now = utc_now()
        updated = artifact.model_copy(update={"updated_at": now})
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO application_artifacts
                    (artifact_id, application_id, payload_json, type, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    updated.artifact_id,
                    updated.application_id,
                    updated.model_dump_json(),
                    updated.type.value,
                    updated.status.value,
                    updated.updated_at,
                ),
            )
        app_updates: dict[str, object] = {}
        if updated.type is ApplicationArtifactType.TAILORED_RESUME:
            app_updates["latest_resume_artifact_id"] = updated.artifact_id
            now_score_updated = False
            if updated.ats_before and isinstance(updated.ats_before.get("score"), (int, float)):
                app_updates.setdefault("current_resume_score", float(updated.ats_before["score"]))
            if updated.ats_after and isinstance(updated.ats_after.get("score"), (int, float)):
                tailored_score = float(updated.ats_after["score"])
                app_updates["fit_score"] = tailored_score
                app_updates["tailored_resume_score"] = tailored_score
                now_score_updated = True
            if updated.ats_after:
                app_updates["required_missing"] = list(updated.ats_after.get("required_missing") or [])
                app_updates["preferred_missing"] = list(updated.ats_after.get("preferred_missing") or [])
                app_updates["keyword_misses"] = list(updated.ats_after.get("keyword_misses") or [])
                now_score_updated = True
            if now_score_updated:
                app_updates["score_updated_at"] = now
            if updated.status in {
                ApplicationArtifactStatus.GENERATED,
                ApplicationArtifactStatus.APPROVED,
            }:
                app_updates["stage"] = ApplicationStage.TAILORING
            if updated.status is ApplicationArtifactStatus.UPLOADED:
                app_updates["stage"] = ApplicationStage.FORM_REVIEW
        elif updated.type is ApplicationArtifactType.COVER_LETTER and updated.status in {
            ApplicationArtifactStatus.APPROVED,
            ApplicationArtifactStatus.UPLOADED,
        }:
            app_updates["cover_letter_artifact_id"] = updated.artifact_id
        if app_updates:
            self.update_application(updated.application_id, app_updates)
        return updated

    def get_application_artifact(
        self,
        artifact_id: str,
    ) -> ApplicationArtifact | None:
        """Load one application artifact."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM application_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return ApplicationArtifact.model_validate_json(row["payload_json"]) if row else None

    def list_application_artifacts(
        self,
        application_id: str,
        *,
        artifact_type: ApplicationArtifactType | None = None,
        status: ApplicationArtifactStatus | None = None,
        limit: int = 50,
    ) -> list[ApplicationArtifact]:
        """List artifacts for one application, newest first."""
        clauses = ["application_id = ?"]
        params: list[object] = [application_id]
        if artifact_type is not None:
            clauses.append("type = ?")
            params.append(artifact_type.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM application_artifacts
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [
            ApplicationArtifact.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_latest_application_artifact(
        self,
        application_id: str,
        *,
        artifact_type: ApplicationArtifactType | None = None,
        status: ApplicationArtifactStatus | None = None,
    ) -> ApplicationArtifact | None:
        """Return the newest matching artifact for one application."""
        artifacts = self.list_application_artifacts(
            application_id,
            artifact_type=artifact_type,
            status=status,
            limit=1,
        )
        return artifacts[0] if artifacts else None

    def update_application_artifact_status(
        self,
        artifact_id: str,
        status: ApplicationArtifactStatus,
    ) -> ApplicationArtifact:
        """Move an artifact through draft/generated/approved/uploaded states."""
        artifact = self.get_application_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact_id: {artifact_id}")
        now = utc_now()
        updates: dict[str, object] = {"status": status, "updated_at": now}
        if status is ApplicationArtifactStatus.APPROVED:
            updates["approved_at"] = now
        if status is ApplicationArtifactStatus.UPLOADED:
            updates["uploaded_at"] = now
        updated = artifact.model_copy(update=updates)
        saved = self.save_application_artifact(updated)
        self.create_application_event(
            application_id=saved.application_id,
            kind=f"artifact_{status.value}",
            label=f"Resume artifact {status.value.replace('_', ' ')}",
            payload={"artifact_id": saved.artifact_id, "type": saved.type.value},
        )
        return saved

    def create_application_event(
        self,
        *,
        application_id: str,
        kind: str,
        label: str,
        detail: str = "",
        payload: dict[str, object] | None = None,
    ) -> ApplicationEvent:
        """Append an immutable application timeline event."""
        if self.get_application(application_id) is None:
            raise KeyError(f"Unknown application_id: {application_id}")
        event = ApplicationEvent(
            event_id=str(uuid.uuid4()),
            application_id=application_id,
            kind=kind,
            label=label,
            detail=detail,
            payload=dict(payload or {}),
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO application_events
                    (event_id, application_id, payload_json, kind, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.application_id,
                    event.model_dump_json(),
                    event.kind,
                    event.created_at,
                ),
            )
        try:
            self.update_application(application_id, {})
        except ValueError:
            pass
        return event

    def list_application_events(
        self,
        application_id: str,
        limit: int = 100,
    ) -> list[ApplicationEvent]:
        """List timeline events for one application, newest first."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM application_events
                WHERE application_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (application_id, limit),
            ).fetchall()
        return [
            ApplicationEvent.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def create_application_task(
        self,
        *,
        application_id: str,
        title: str,
        category: Literal["follow_up", "missing_answer", "interview", "manual", "deadline"] = "manual",
        due_at: str | None = None,
        notes: str = "",
    ) -> ApplicationTask:
        """Create a manual tracker task."""
        if self.get_application(application_id) is None:
            raise KeyError(f"Unknown application_id: {application_id}")
        task = ApplicationTask(
            task_id=str(uuid.uuid4()),
            application_id=application_id,
            title=title,
            category=category,
            due_at=due_at,
            notes=notes,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO application_tasks
                    (task_id, application_id, payload_json, status, due_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.application_id,
                    task.model_dump_json(),
                    task.status,
                    task.due_at,
                    task.created_at,
                ),
            )
        self.create_application_event(
            application_id=application_id,
            kind="task_created",
            label=f"Task added: {title}",
            payload={"task_id": task.task_id, "category": task.category},
        )
        return task

    def list_application_tasks(
        self,
        application_id: str,
        limit: int = 100,
    ) -> list[ApplicationTask]:
        """List tasks for one application, open tasks first."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM application_tasks
                WHERE application_id = ?
                ORDER BY status = 'open' DESC, COALESCE(due_at, created_at) ASC
                LIMIT ?
                """,
                (application_id, limit),
            ).fetchall()
        return [
            ApplicationTask.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def transition_application(
        self,
        application_id: str,
        target: ApplicationStatus,
        notes: str | None = None,
    ) -> ApplicationRecord:
        """Apply a validated state transition and preserve approval timestamps."""
        current = self.get_application(application_id)
        if current is None:
            raise KeyError(f"Unknown application_id: {application_id}")
        allowed = ALLOWED_APPLICATION_TRANSITIONS.get(current.status, frozenset())
        if target not in allowed:
            raise InvalidApplicationTransition(
                f"Cannot transition application from {current.status.value} to {target.value}"
            )

        now = utc_now()
        updated = current.model_copy(
            update={
                "status": target,
                "stage": _stage_for_status(target),
                "notes": current.notes if notes is None else notes,
                "updated_at": now,
                "last_activity_at": now,
                "approved_at": now if target is ApplicationStatus.APPROVED else current.approved_at,
                "applied_at": now if target is ApplicationStatus.SUBMITTED else current.applied_at,
                "submitted_at": now if target is ApplicationStatus.SUBMITTED else current.submitted_at,
            }
        )
        self._save_application(updated)
        self.create_application_event(
            application_id=application_id,
            kind="status_changed",
            label=f"Status changed to {target.value.replace('_', ' ')}",
            detail=notes or "",
            payload={"status": target.value},
        )
        return updated

    def _list_all_applications(self) -> list[ApplicationRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM applications"
            ).fetchall()
        return [
            ApplicationRecord.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def _application_richness_score(self, application: ApplicationRecord) -> tuple[int, int, int, int, int, str]:
        score_count = sum(
            value is not None
            for value in (
                application.current_resume_score,
                application.tailored_resume_score,
                application.fit_score,
            )
        )
        artifact_count = int(bool(application.latest_resume_artifact_id)) + int(bool(application.cover_letter_artifact_id))
        evidence_count = (
            len(application.required_missing)
            + len(application.preferred_missing)
            + len(application.keyword_misses)
            + application.missing_answers_count
        )
        workflow_rank = max(
            _APPLICATION_STATUS_RANK.get(application.status, 0),
            _APPLICATION_STAGE_RANK.get(application.stage, 0),
        )
        return (
            artifact_count,
            score_count,
            workflow_rank,
            evidence_count,
            len(application.notes.strip()),
            _latest_text(application.last_activity_at, application.updated_at, application.created_at),
        )

    def _merge_application_records(
        self,
        canonical: ApplicationRecord,
        duplicate: ApplicationRecord,
    ) -> ApplicationRecord:
        data: dict[str, Any] = canonical.model_dump(mode="json")
        duplicate_data = duplicate.model_dump(mode="json")

        for field_name in (
            "job_title",
            "company",
            "provider",
            "location",
            "workplace_type",
            "salary_range",
            "apply_url",
            "source_url",
            "resume_session_id",
            "latest_resume_artifact_id",
            "cover_letter_artifact_id",
            "deadline",
            "next_action_at",
            "approved_at",
            "applied_at",
            "submitted_at",
        ):
            if not data.get(field_name) and duplicate_data.get(field_name):
                data[field_name] = duplicate_data[field_name]

        if _APPLICATION_STATUS_RANK.get(duplicate.status, 0) > _APPLICATION_STATUS_RANK.get(canonical.status, 0):
            data["status"] = duplicate.status.value
        if _APPLICATION_STAGE_RANK.get(duplicate.stage, 0) > _APPLICATION_STAGE_RANK.get(canonical.stage, 0):
            data["stage"] = duplicate.stage.value

        data["required_missing"] = _unique_texts(canonical.required_missing, duplicate.required_missing)
        data["preferred_missing"] = _unique_texts(canonical.preferred_missing, duplicate.preferred_missing)
        data["keyword_misses"] = _unique_texts(canonical.keyword_misses, duplicate.keyword_misses)
        data["missing_answers_count"] = max(
            canonical.missing_answers_count,
            duplicate.missing_answers_count,
        )
        data["notes"] = _merge_notes(canonical.notes, duplicate.notes)
        data["created_at"] = _earliest_text(canonical.created_at, duplicate.created_at) or canonical.created_at
        data["updated_at"] = _latest_text(canonical.updated_at, duplicate.updated_at) or canonical.updated_at
        data["last_activity_at"] = _latest_text(
            canonical.last_activity_at,
            duplicate.last_activity_at,
        ) or canonical.last_activity_at

        if _PRIORITY_RANK.get(duplicate.priority, 0) > _PRIORITY_RANK.get(canonical.priority, 0):
            data["priority"] = duplicate.priority
        data["excitement"] = max(canonical.excitement, duplicate.excitement)

        canonical_score_time = canonical.score_updated_at or canonical.updated_at
        duplicate_score_time = duplicate.score_updated_at or duplicate.updated_at
        if duplicate.current_resume_score is not None and (
            canonical.current_resume_score is None or duplicate_score_time >= canonical_score_time
        ):
            data["current_resume_score"] = duplicate.current_resume_score
        if duplicate.tailored_resume_score is not None and (
            canonical.tailored_resume_score is None or duplicate_score_time >= canonical_score_time
        ):
            data["tailored_resume_score"] = duplicate.tailored_resume_score
        if duplicate.fit_score is not None and (
            canonical.fit_score is None or duplicate_score_time >= canonical_score_time
        ):
            data["fit_score"] = duplicate.fit_score
        data["score_updated_at"] = _latest_text(canonical.score_updated_at, duplicate.score_updated_at) or None

        if duplicate.deadline and canonical.deadline:
            data["deadline"] = min(canonical.deadline, duplicate.deadline)
        if duplicate.next_action_at and canonical.next_action_at:
            data["next_action_at"] = min(canonical.next_action_at, duplicate.next_action_at)

        return ApplicationRecord.model_validate(data)

    def _move_application_children(
        self,
        connection: sqlite3.Connection,
        *,
        old_application_id: str,
        new_application_id: str,
    ) -> None:
        child_tables = (
            ("application_artifacts", "artifact_id"),
            ("application_events", "event_id"),
            ("application_tasks", "task_id"),
            ("form_scans", "scan_id"),
        )
        for table_name, id_column in child_tables:
            rows = connection.execute(
                f"SELECT {id_column}, payload_json FROM {table_name} WHERE application_id = ?",
                (old_application_id,),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["application_id"] = new_application_id
                connection.execute(
                    f"""
                    UPDATE {table_name}
                    SET application_id = ?, payload_json = ?
                    WHERE {id_column} = ?
                    """,
                    (
                        new_application_id,
                        json.dumps(payload, ensure_ascii=True),
                        row[id_column],
                    ),
                )

    def _save_application(self, record: ApplicationRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO applications
                    (application_id, job_id, payload_json, status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.application_id,
                    record.job_id,
                    record.model_dump_json(),
                    record.status.value,
                    record.updated_at,
                ),
            )

    def save_tailor_session_payload(
        self,
        *,
        session_id: str,
        profile_id: str,
        payload: dict[str, Any],
        created_at: float,
        last_accessed: float,
    ) -> None:
        """Persist one tailor wizard session as JSON."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO tailor_sessions
                    (session_id, profile_id, payload_json, last_accessed, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    profile_id,
                    json.dumps(payload, ensure_ascii=True),
                    last_accessed,
                    created_at,
                ),
            )

    def get_tailor_session_row(self, session_id: str) -> dict[str, Any] | None:
        """Load a tailor session row or None."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, profile_id, payload_json, last_accessed, created_at
                FROM tailor_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "profile_id": row["profile_id"],
            "payload": json.loads(row["payload_json"]),
            "last_accessed": float(row["last_accessed"]),
            "created_at": float(row["created_at"]),
        }

    def delete_tailor_session(self, session_id: str) -> bool:
        """Delete one tailor session. Returns True when a row was removed."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tailor_sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

    def cleanup_expired_tailor_sessions(self, ttl_seconds: float) -> int:
        """Delete tailor sessions whose last_accessed is older than ttl."""
        cutoff = time.time() - ttl_seconds
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tailor_sessions WHERE last_accessed < ?",
                (cutoff,),
            )
            return cursor.rowcount
