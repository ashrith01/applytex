"""In-memory tailor wizard sessions for the web UI."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

SESSION_TTL_SECONDS = 7200


@dataclass
class TailorSession:
    """State for one guided tailor flow."""

    session_id: str
    job_id: str
    profile_id: str
    application_id: str | None
    latex_session_id: str | None = None
    current_latex: str = ""
    confirmed_skills: list[str] = field(default_factory=list)
    diff: list[dict[str, Any]] = field(default_factory=list)
    change_history: list[dict[str, Any]] = field(default_factory=list)
    last_result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_accessed = time.monotonic()

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_accessed) > SESSION_TTL_SECONDS


class TailorStore:
    """Thread-safe enough for local FastAPI single-process use."""

    def __init__(self) -> None:
        self._sessions: dict[str, TailorSession] = {}

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
            current_latex=current_latex,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> TailorSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            del self._sessions[session_id]
            return None
        session.touch()
        return session

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def cleanup_expired(self) -> int:
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)


tailor_store = TailorStore()
