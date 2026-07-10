"""In-memory session store for the LaTeX resume API.

Each uploaded resume gets a UUID session that holds the parsed state and,
after optimization, the ``OptimizationResult``.  Sessions expire after
``SESSION_TTL_SECONDS`` of inactivity.

All access is guarded by an ``asyncio.Lock`` per session so concurrent
requests for the same session serialize safely.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from latex_resume.models import ParseResult
from latex_resume.optimizer import OptimizationResult

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
    latex_source: str  # original source (before any edits)
    filename: str

    # Set after optimization
    optimization_result: OptimizationResult | None = None

    # Timestamps
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)

    # Per-session lock for concurrent request safety
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        """Update last-accessed timestamp."""
        self.last_accessed = time.monotonic()

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_accessed) > SESSION_TTL_SECONDS

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


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------


class SessionStore:
    """Thread-safe (asyncio) in-memory session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, ResumeSession] = {}
        self._meta_lock = asyncio.Lock()

    async def create(
        self,
        parse_result: ParseResult,
        latex_source: str,
        filename: str,
    ) -> ResumeSession:
        """Create and register a new session; return it."""
        session_id = str(uuid.uuid4())
        session = ResumeSession(
            session_id=session_id,
            parse_result=parse_result,
            latex_source=latex_source,
            filename=filename,
        )
        async with self._meta_lock:
            self._sessions[session_id] = session
            logger.info("Created session %s for %s", session_id, filename)
        return session

    async def get(self, session_id: str) -> ResumeSession | None:
        """Return the session, refreshing its timestamp, or ``None``."""
        async with self._meta_lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            await self.delete(session_id)
            return None
        session.touch()
        return session

    async def delete(self, session_id: str) -> bool:
        """Remove a session; return True if it existed."""
        async with self._meta_lock:
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
            if existed:
                logger.info("Deleted session %s", session_id)
        return existed

    async def cleanup_expired(self) -> int:
        """Remove all expired sessions; return count removed."""
        async with self._meta_lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired]
            for sid in expired:
                del self._sessions[sid]
        if expired:
            logger.info("Cleaned up %d expired session(s)", len(expired))
        return len(expired)

    @property
    def count(self) -> int:
        return len(self._sessions)


# ---------------------------------------------------------------------------
# Global store instance (imported by api.py)
# ---------------------------------------------------------------------------

store = SessionStore()
