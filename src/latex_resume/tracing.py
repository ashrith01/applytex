"""Optional LangSmith tracing for ApplyTeX ATS pipeline runs.

Tracing is opt-in.  Set ``SMARTJOBAPPLY_LANGSMITH_TRACE=true`` or
``LANGSMITH_TRACING=true`` plus the usual LangSmith credentials/environment.
When LangSmith is unavailable or disabled, this module becomes a no-op so local
development and tests do not depend on a hosted service.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def is_langsmith_enabled() -> bool:
    """Return True when tracing is explicitly enabled by environment."""
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("SMARTJOBAPPLY_LANGSMITH_TRACE_IN_TESTS", "").lower()
        not in _TRUTHY
    ):
        return False
    return (
        os.environ.get("SMARTJOBAPPLY_LANGSMITH_TRACE", "").lower() in _TRUTHY
        or os.environ.get("LANGSMITH_TRACING", "").lower() in _TRUTHY
        or os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in _TRUTHY
    )


def hash_text(text: str) -> str:
    """Return a short stable hash for PII-safe run correlation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compact_text(text: str, max_chars: int = 300) -> str:
    """Return a short whitespace-normalized excerpt."""
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rsplit(" ", 1)[0].rstrip() + " ..."


@dataclass
class TraceStage:
    """One traced pipeline stage."""

    name: str
    run_type: str = "chain"
    inputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _ctx: Any | None = None
    _run: Any | None = None
    _start: float = field(default_factory=time.perf_counter)

    def end(
        self,
        outputs: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> float:
        """Close the stage and return elapsed milliseconds."""
        elapsed_ms = round((time.perf_counter() - self._start) * 1000, 1)
        safe_outputs = {**(outputs or {}), "elapsed_ms": elapsed_ms}
        if error is not None:
            safe_outputs["error"] = str(error)

        if self._run is not None:
            try:
                self._run.end(outputs=safe_outputs)
            except Exception as exc:
                logger.debug("LangSmith stage end failed for %s: %s", self.name, exc)

        if self._ctx is not None:
            try:
                self._ctx.__exit__(
                    type(error) if error is not None else None,
                    error,
                    error.__traceback__ if error is not None else None,
                )
            except Exception as exc:
                logger.debug("LangSmith stage exit failed for %s: %s", self.name, exc)

        return elapsed_ms


class PipelineTracer:
    """Small wrapper around LangSmith trace spans plus local stage timings."""

    def __init__(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.inputs = inputs or {}
        self.metadata = metadata or {}
        self.tags = tags or []
        self.stage_latencies_ms: dict[str, float] = {}
        self.enabled = False
        self.trace_id: str | None = None
        self._root_ctx: Any | None = None
        self._root_run: Any | None = None
        self._trace_factory: Any | None = None

        if not is_langsmith_enabled():
            return

        try:
            from langsmith import trace  # type: ignore
        except Exception as exc:
            logger.warning("LangSmith tracing enabled but SDK is unavailable: %s", exc)
            return

        self._trace_factory = trace
        project_name = os.environ.get("LANGSMITH_PROJECT", "applytex-local")
        try:
            self._root_ctx = trace(
                name,
                run_type="chain",
                inputs=self.inputs,
                metadata=self.metadata,
                tags=self.tags,
                project_name=project_name,
            )
            self._root_run = self._root_ctx.__enter__()
            self.trace_id = str(getattr(self._root_run, "id", "") or "") or None
            self.enabled = True
        except Exception as exc:
            logger.warning("LangSmith root trace could not be started: %s", exc)
            self._root_ctx = None
            self._root_run = None

    def stage(
        self,
        name: str,
        run_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceStage:
        """Start a child stage, or a local timing-only stage if disabled."""
        stage = TraceStage(
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            metadata=metadata or {},
        )
        if not self.enabled or self._trace_factory is None:
            return stage

        project_name = os.environ.get("LANGSMITH_PROJECT", "applytex-local")
        try:
            stage._ctx = self._trace_factory(
                name,
                run_type=run_type,
                inputs=stage.inputs,
                metadata=stage.metadata,
                tags=self.tags,
                project_name=project_name,
                parent=self._root_run,
            )
            stage._run = stage._ctx.__enter__()
        except Exception as exc:
            logger.debug("LangSmith stage trace could not be started for %s: %s", name, exc)
            stage._ctx = None
            stage._run = None
        return stage

    def end_stage(
        self,
        stage: TraceStage,
        outputs: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Close a stage and record local latency."""
        self.stage_latencies_ms[stage.name] = stage.end(outputs=outputs, error=error)

    def finish(
        self,
        outputs: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Close the root trace."""
        safe_outputs = {
            **(outputs or {}),
            "stage_latencies_ms": self.stage_latencies_ms,
        }
        if error is not None:
            safe_outputs["error"] = str(error)

        if self._root_run is not None:
            try:
                self._root_run.end(outputs=safe_outputs)
            except Exception as exc:
                logger.debug("LangSmith root end failed: %s", exc)

        if self._root_ctx is not None:
            try:
                self._root_ctx.__exit__(
                    type(error) if error is not None else None,
                    error,
                    error.__traceback__ if error is not None else None,
                )
            except Exception as exc:
                logger.debug("LangSmith root exit failed: %s", exc)
