"""SQLite persistence for job discovery and controlled application state."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

from latex_resume.job_models import (
    ALLOWED_APPLICATION_TRANSITIONS,
    ApplicationRecord,
    ApplicationStatus,
    CandidateProfile,
    FormScan,
    JobPosting,
    JobSearchResult,
    utc_now,
)


class InvalidApplicationTransition(ValueError):
    """Raised when an application attempts an unsafe state transition."""


class ApplicationStore:
    """Small local-first SQLite repository for the job application MVP."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

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

                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    profile_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS form_scans (
                    scan_id TEXT PRIMARY KEY,
                    application_id TEXT,
                    payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

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

    def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List most recently retrieved jobs."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM jobs ORDER BY retrieved_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [JobPosting.model_validate_json(row["payload_json"]) for row in rows]

    def create_application(
        self,
        job_id: str,
        resume_session_id: str | None = None,
        notes: str = "",
    ) -> ApplicationRecord:
        """Create an application draft for a known job."""
        if self.get_job(job_id) is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        record = ApplicationRecord(
            application_id=str(uuid.uuid4()),
            job_id=job_id,
            resume_session_id=resume_session_id,
            notes=notes,
        )
        self._save_application(record)
        return record

    def get_application(self, application_id: str) -> ApplicationRecord | None:
        """Load one application record."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM applications WHERE application_id = ?",
                (application_id,),
            ).fetchone()
        return ApplicationRecord.model_validate_json(row["payload_json"]) if row else None

    def list_applications(self, limit: int = 100) -> list[ApplicationRecord]:
        """List applications ordered by their latest update."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM applications ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ApplicationRecord.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_candidate_profile(self, profile_id: str = "default") -> CandidateProfile:
        """Return the saved profile or a conservative default profile."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM candidate_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row:
            return CandidateProfile.model_validate_json(row["payload_json"])
        profile = CandidateProfile(profile_id=profile_id)
        return self.save_candidate_profile(profile)

    def save_candidate_profile(self, profile: CandidateProfile) -> CandidateProfile:
        """Persist user-owned profile facts and reusable exact answers."""
        updated = profile.model_copy(update={"updated_at": utc_now()})
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO candidate_profiles
                    (profile_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    updated.profile_id,
                    updated.model_dump_json(),
                    updated.updated_at,
                ),
            )
        return updated

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
        return scan

    def get_form_scan(self, scan_id: str) -> FormScan | None:
        """Load one read-only form scan."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM form_scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        return FormScan.model_validate_json(row["payload_json"]) if row else None

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
                "notes": current.notes if notes is None else notes,
                "updated_at": now,
                "approved_at": now if target is ApplicationStatus.APPROVED else current.approved_at,
                "submitted_at": now if target is ApplicationStatus.SUBMITTED else current.submitted_at,
            }
        )
        self._save_application(updated)
        return updated

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
