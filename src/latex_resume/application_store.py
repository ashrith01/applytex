"""SQLite persistence for job discovery and controlled application state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from latex_resume.job_models import (
    ALLOWED_APPLICATION_TRANSITIONS,
    ApplicationArtifact,
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
    JobPosting,
    JobSearchResult,
    ProjectRecord,
    ProjectSource,
    utc_now,
)


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
            return CandidateProfile.model_validate_json(row["payload_json"])
        profile = CandidateProfile(profile_id=profile_id)
        return self.save_candidate_profile(profile)

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
        return [
            CandidateProfile.model_validate_json(row["payload_json"])
            for row in rows
        ]

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
