"""Session store for the classic ``/latex/*`` resume API.

Each uploaded resume gets a UUID session holding the parsed state and, after
optimization, the ``OptimizationResult``. Sessions expire after
``SESSION_TTL_SECONDS`` of inactivity.

Persistence: when the API binds an ``ApplicationStore`` (``store.bind``),
sessions are written through to the ``latex_sessions`` table and survive API
restarts — the original LaTeX is re-parsed on load (parsing is pure) and the
optimization result is rehydrated from JSON. Without a bound store (unit
tests, scripts) the registry is in-memory only, as before.

All access is guarded by an ``asyncio.Lock`` per session so concurrent
requests for the same session serialize safely.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from latex_resume.ats import ATSResult
from latex_resume.models import ParseResult
from latex_resume.optimizer import OptimizationResult

if TYPE_CHECKING:  # pragma: no cover
    from latex_resume.application_store import ApplicationStore

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour of inactivity


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class ResumeSession:
    """State associated with one uploaded resume."""

    session_id: str
    parse_result: ParseResult
    latex_source: str  # current source (refine may replace it)
    filename: str

    # Owning profile; "" for sessions opened before ownership existed.
    profile_id: str = ""

    # The source that ``parse_result`` was built from; needed to rehydrate.
    original_latex: str = ""

    # Set after optimization
    optimization_result: OptimizationResult | None = None

    # Timestamps (wall clock, so they can be persisted)
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)

    # Per-session lock for concurrent request safety
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if not self.original_latex:
            self.original_latex = self.latex_source

    def touch(self) -> None:
        """Update last-accessed timestamp."""
        self.last_accessed = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_accessed) > SESSION_TTL_SECONDS

    def to_status_dict(self) -> dict[str, Any]:
        """Summary dict safe to return over HTTP (no raw LaTeX)."""
        opt = self.optimization_result
        return {
            "session_id": self.session_id,
            "filename": self.filename,
            "optimized": opt is not None,
            "overflow": opt.overflow if opt else None,
            "visual_overflow": opt.visual_overflow if opt else None,
            "min_text_baseline_pt": opt.min_text_baseline_pt if opt else None,
            "page_count": opt.page_count if opt else None,
            "ats_target_score": opt.ats_target_score if opt else None,
            "ats_target_met": opt.ats_target_met if opt else None,
            "ats_score": opt.ats_after.score if opt and opt.ats_after else None,
            "confirmation_required_skills": opt.confirmation_required_skills if opt else [],
            "changes_applied": len(opt.validated_changes) if opt else 0,
            "warnings": opt.warnings if opt else [],
        }

    # --- persistence ---------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "profile_id": self.profile_id,
            "original_latex": self.original_latex,
            "latex_source": self.latex_source,
            "optimization_result": serialize_optimization_result(self.optimization_result),
        }

    @classmethod
    def from_payload(cls, session_id: str, payload: dict[str, Any]) -> ResumeSession:
        from latex_resume.parser import parse

        original = str(payload.get("original_latex") or payload.get("latex_source") or "")
        filename = str(payload.get("filename") or "resume.tex")
        parse_result = parse(original, resume_id=Path(filename).stem)
        return cls(
            session_id=session_id,
            parse_result=parse_result,
            latex_source=str(payload.get("latex_source") or original),
            filename=filename,
            profile_id=str(payload.get("profile_id") or ""),
            original_latex=original,
            optimization_result=deserialize_optimization_result(payload.get("optimization_result")),
        )


def serialize_optimization_result(opt: OptimizationResult | None) -> dict[str, Any] | None:
    if opt is None:
        return None
    data = dataclasses.asdict(opt)
    data["pdf_bytes"] = base64.b64encode(opt.pdf_bytes).decode() if opt.pdf_bytes else None
    return data


def deserialize_optimization_result(data: dict[str, Any] | None) -> OptimizationResult | None:
    if not data:
        return None
    known = {f.name for f in dataclasses.fields(OptimizationResult)}
    kwargs = {key: value for key, value in data.items() if key in known}
    pdf = kwargs.get("pdf_bytes")
    kwargs["pdf_bytes"] = base64.b64decode(pdf) if isinstance(pdf, str) and pdf else None
    ats_fields = {f.name for f in dataclasses.fields(ATSResult)}
    for key in ("ats_before", "ats_after"):
        value = kwargs.get(key)
        kwargs[key] = ATSResult(**{k: v for k, v in value.items() if k in ats_fields}) if isinstance(value, dict) else None
    return OptimizationResult(**kwargs)


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------


class SessionStore:
    """In-memory registry with optional SQLite write-through."""

    def __init__(self) -> None:
        self._sessions: dict[str, ResumeSession] = {}
        self._meta_lock = asyncio.Lock()
        self._store: ApplicationStore | None = None

    def bind(self, application_store: ApplicationStore) -> None:
        """Attach the durable store used by the running API."""
        self._store = application_store

    def _persist(self, session: ResumeSession) -> None:
        if self._store is None:
            return
        self._store.save_latex_session_payload(
            session_id=session.session_id,
            profile_id=session.profile_id,
            payload=session.to_payload(),
            created_at=session.created_at,
            last_accessed=session.last_accessed,
        )

    async def create(
        self,
        parse_result: ParseResult,
        latex_source: str,
        filename: str,
        profile_id: str = "",
    ) -> ResumeSession:
        """Create and register a new session; return it."""
        session_id = str(uuid.uuid4())
        session = ResumeSession(
            session_id=session_id,
            parse_result=parse_result,
            latex_source=latex_source,
            filename=filename,
            profile_id=profile_id,
        )
        async with self._meta_lock:
            self._sessions[session_id] = session
            logger.info("Created session %s for %s", session_id, filename)
        self._persist(session)
        return session

    async def get(self, session_id: str) -> ResumeSession | None:
        """Return the session, refreshing its timestamp, or ``None``."""
        async with self._meta_lock:
            session = self._sessions.get(session_id)
        if session is None and self._store is not None:
            row = self._store.get_latex_session_row(session_id)
            if row is not None:
                try:
                    session = ResumeSession.from_payload(session_id, row["payload"])
                except Exception as exc:  # pragma: no cover - corrupt row
                    logger.warning("Could not rehydrate session %s: %s", session_id, exc)
                    return None
                session.created_at = row["created_at"]
                session.last_accessed = row["last_accessed"]
                async with self._meta_lock:
                    self._sessions[session_id] = session
        if session is None:
            return None
        if session.is_expired:
            await self.delete(session_id)
            return None
        session.touch()
        return session

    def save(self, session: ResumeSession) -> None:
        """Persist mutations (optimization result, refined source) made on a loaded session."""
        session.touch()
        self._persist(session)

    async def delete(self, session_id: str) -> bool:
        """Remove a session; return True if it existed."""
        async with self._meta_lock:
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
        if self._store is not None:
            existed = self._store.delete_latex_session(session_id) or existed
        if existed:
            logger.info("Deleted session %s", session_id)
        return existed

    async def cleanup_expired(self) -> int:
        """Remove all expired sessions; return count removed."""
        async with self._meta_lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired]
            for sid in expired:
                del self._sessions[sid]
        removed = len(expired)
        if self._store is not None:
            removed += self._store.cleanup_expired_latex_sessions(SESSION_TTL_SECONDS)
        if removed:
            logger.info("Cleaned up %d expired session(s)", removed)
        return removed

    @property
    def count(self) -> int:
        return len(self._sessions)


# Module-level singleton used by the API routes.
store = SessionStore()
