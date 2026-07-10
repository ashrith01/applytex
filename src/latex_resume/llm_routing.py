"""Runtime LLM task routing for the optimizer pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMTaskRoute:
    """Runtime model choice for one optimizer LLM task."""

    backend: str | None = None
    model: str | None = None


LLMTaskRoutes = dict[str, LLMTaskRoute]


def route_for_task(routes: LLMTaskRoutes | None, task: str) -> LLMTaskRoute:
    """Return the configured route for *task*, or an empty route."""
    return (routes or {}).get(task, LLMTaskRoute())
