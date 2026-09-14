"""Executor run policy: who may enqueue what, daily caps, and screenshot storage.

The executor itself is a Node/Playwright worker (``scripts/executor.mjs``)
running on the owner's machine in a dedicated Chrome profile. This module owns
the rules the API enforces around it:

* only verified providers run unattended (``EXECUTOR_PROVIDERS``) unless the
  caller explicitly allows an experimental one;
* one active run per application;
* a daily cap on runs per profile (``APPLYTEX_EXECUTOR_DAILY_CAP``);
* an approval token is minted only when the user approves a paused review, and
  the executor must present it to record a submission.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import (
    ACTIVE_APPLY_RUN_STATUSES,
    EXECUTOR_PROVIDERS,
    ApplicationRecord,
    ApplicationStatus,
    ApplyRun,
    JobPosting,
    JobProvider,
    utc_now,
)

DAILY_CAP_ENV = "APPLYTEX_EXECUTOR_DAILY_CAP"
DEFAULT_DAILY_CAP = 20


class ApplyRunPolicyError(ValueError):
    """The request is well-formed but the executor policy refuses it."""


def daily_cap() -> int:
    raw = os.environ.get(DAILY_CAP_ENV, str(DEFAULT_DAILY_CAP)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DAILY_CAP


def enqueue_apply_run(
    store: ApplicationStore,
    *,
    application: ApplicationRecord,
    job: JobPosting,
    profile_id: str,
    allow_experimental_provider: bool = False,
) -> ApplyRun:
    if application.status is ApplicationStatus.SUBMITTED:
        raise ApplyRunPolicyError("This application is already submitted.")
    if application.status in {ApplicationStatus.SKIPPED, ApplicationStatus.BLOCKED}:
        raise ApplyRunPolicyError(f"This application is {application.status.value}; reopen it first.")
    provider = application.provider or job.provider
    if provider not in EXECUTOR_PROVIDERS and not allow_experimental_provider:
        raise ApplyRunPolicyError(
            f"{provider.value} is not a verified executor provider "
            f"({', '.join(sorted(item.value for item in EXECUTOR_PROVIDERS))}). "
            "Pass allow_experimental_provider to try it anyway."
        )
    apply_url = application.apply_url or job.apply_url
    if not apply_url.startswith("https://"):
        raise ApplyRunPolicyError("The application has no HTTPS apply URL.")
    active = store.list_apply_runs(
        profile_id, statuses=sorted(ACTIVE_APPLY_RUN_STATUSES), application_id=application.application_id
    )
    if active:
        raise ApplyRunPolicyError(f"Run {active[0].run_id} is already {active[0].status} for this application.")
    cap = daily_cap()
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    if cap and store.count_apply_runs_since(profile_id, since) >= cap:
        raise ApplyRunPolicyError(f"Daily executor cap of {cap} runs reached; raise {DAILY_CAP_ENV} to continue.")
    run = ApplyRun(
        run_id=str(uuid.uuid4()),
        application_id=application.application_id,
        profile_id=profile_id,
        job_id=application.job_id,
        provider=provider.value if isinstance(provider, JobProvider) else str(provider),
        apply_url=apply_url,
    )
    run = store.create_apply_run(run)
    store.create_application_event(
        application_id=application.application_id,
        kind="apply_run_queued",
        label="Executor run queued",
        detail="A local browser run will fill this application and pause for your review before anything is submitted.",
        payload={"run_id": run.run_id},
    )
    return run


def mint_approval_token() -> str:
    return secrets.token_urlsafe(24)


def screenshots_root(db_path: Path) -> Path:
    return db_path.parent / "runs"


def save_screenshot(db_path: Path, run_id: str, step: str, data_b64: str) -> str:
    """Write PNG bytes for a run step; returns the path relative to the DB directory."""
    raw = base64.b64decode(data_b64)
    if not raw.startswith(b"\x89PNG"):
        raise ValueError("Screenshot must be a PNG.")
    safe_step = re.sub(r"[^A-Za-z0-9_-]+", "_", step).strip("_") or "step"
    directory = screenshots_root(db_path) / re.sub(r"[^A-Za-z0-9_-]+", "_", run_id)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "").replace("-", "")[:15]
    path = directory / f"{stamp}_{safe_step}.png"
    path.write_bytes(raw)
    return str(path.relative_to(db_path.parent))


def resolve_screenshot(db_path: Path, relative: str) -> Path | None:
    if not relative:
        return None
    candidate = (db_path.parent / relative).resolve()
    root = screenshots_root(db_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate
