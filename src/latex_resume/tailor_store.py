"""Durable tailor wizard sessions backed by ApplicationStore SQLite."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import ProjectRecommendation

SESSION_TTL_SECONDS = 7200


@dataclass
class TailorSession:
    """State for one guided tailor flow."""

    session_id: str
    job_id: str
    profile_id: str
    application_id: str | None
    latex_session_id: str | None = None
    source_latex: str = ""
    current_latex: str = ""
    confirmed_skills: list[str] = field(default_factory=list)
    project_recommendations: list[ProjectRecommendation] = field(default_factory=list)
    selected_project_ids: list[str] = field(default_factory=list)
    project_filter_warnings: list[str] = field(default_factory=list)
    diff: list[dict[str, Any]] = field(default_factory=list)
    change_history: list[dict[str, Any]] = field(default_factory=list)
    last_result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_accessed = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_accessed) > SESSION_TTL_SECONDS

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["project_recommendations"] = [
            recommendation.model_dump(mode="json")
            if hasattr(recommendation, "model_dump")
            else recommendation
            for recommendation in self.project_recommendations
        ]
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TailorSession":
        recommendations = []
        for item in payload.get("project_recommendations") or []:
            if isinstance(item, ProjectRecommendation):
                recommendations.append(item)
            elif isinstance(item, dict):
                recommendations.append(ProjectRecommendation.model_validate(item))
        return cls(
            session_id=str(payload["session_id"]),
            job_id=str(payload["job_id"]),
            profile_id=str(payload["profile_id"]),
            application_id=payload.get("application_id"),
            latex_session_id=payload.get("latex_session_id"),
            source_latex=str(payload.get("source_latex") or ""),
            current_latex=str(payload.get("current_latex") or ""),
            confirmed_skills=list(payload.get("confirmed_skills") or []),
            project_recommendations=recommendations,
            selected_project_ids=list(payload.get("selected_project_ids") or []),
            project_filter_warnings=list(payload.get("project_filter_warnings") or []),
            diff=list(payload.get("diff") or []),
            change_history=list(payload.get("change_history") or []),
            last_result=payload.get("last_result"),
            created_at=float(payload.get("created_at") or time.time()),
            last_accessed=float(payload.get("last_accessed") or time.time()),
        )


class TailorStore:
    """Persist tailor sessions in SQLite via ApplicationStore."""

    def __init__(self, application_store: ApplicationStore | None = None) -> None:
        self._store = application_store
        self._memory: dict[str, TailorSession] = {}

    def bind(self, application_store: ApplicationStore) -> None:
        """Attach the durable store used by the running API."""
        self._store = application_store

    def _persist(self, session: TailorSession) -> None:
        if self._store is None:
            self._memory[session.session_id] = session
            return
        self._store.save_tailor_session_payload(
            session_id=session.session_id,
            profile_id=session.profile_id,
            payload=session.to_payload(),
            created_at=session.created_at,
            last_accessed=session.last_accessed,
        )

    def create(
        self,
        *,
        job_id: str,
        profile_id: str,
        application_id: str | None = None,
        current_latex: str = "",
    ) -> TailorSession:
        session_id = str(uuid.uuid4())
        session = TailorSession(
            session_id=session_id,
            job_id=job_id,
            profile_id=profile_id,
            application_id=application_id,
            source_latex=current_latex,
            current_latex=current_latex,
        )
        self._persist(session)
        return session

    def get(self, session_id: str) -> TailorSession | None:
        if self._store is None:
            session = self._memory.get(session_id)
            if session is None:
                return None
            if session.is_expired:
                del self._memory[session_id]
                return None
            session.touch()
            return session

        row = self._store.get_tailor_session_row(session_id)
        if row is None:
            return None
        session = TailorSession.from_payload(row["payload"])
        session.last_accessed = row["last_accessed"]
        session.created_at = row["created_at"]
        if session.is_expired:
            self._store.delete_tailor_session(session_id)
            return None
        session.touch()
        self._persist(session)
        return session

    def save(self, session: TailorSession) -> None:
        """Persist mutations made on a loaded session."""
        session.touch()
        self._persist(session)

    def delete(self, session_id: str) -> bool:
        if self._store is None:
            return self._memory.pop(session_id, None) is not None
        return self._store.delete_tailor_session(session_id)

    def cleanup_expired(self) -> int:
        if self._store is None:
            expired = [sid for sid, session in self._memory.items() if session.is_expired]
            for sid in expired:
                del self._memory[sid]
            return len(expired)
        return self._store.cleanup_expired_tailor_sessions(SESSION_TTL_SECONDS)


tailor_store = TailorStore()
