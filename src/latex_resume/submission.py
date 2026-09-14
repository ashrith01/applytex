"""Submission receipts and workflow advancement.

A ``SubmissionBundle`` is written once, when the user confirms that an
application went out. It records the fields as they stood on every scanned
step (page values or the reviewed plan values), which resume and cover-letter
files were attached (by artifact id and hash), and how the submission was
confirmed. Later edits to the profile, resume, or answers never touch it.

``advance_application`` walks the validated state machine so a confirmation
from any earlier state lands on ``submitted`` through the required
``approved -> submitting`` gate, and ``record_fill_result`` moves an
application to ``ready_for_review`` or ``needs_input`` after a reviewed fill.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI

from latex_resume.application_store import ApplicationStore, InvalidApplicationTransition
from latex_resume.job_models import (
    ALLOWED_APPLICATION_TRANSITIONS,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationRecord,
    ApplicationStatus,
    ApplicationTask,
    FormScan,
    SubmissionBundle,
    SubmissionField,
    SubmissionStep,
    utc_now,
)

FOLLOW_UP_DAYS = 7

# Statuses a reviewed fill may move *forward* from. Never regress a later state.
_FILL_ADVANCEABLE: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.SCORED,
        ApplicationStatus.SELECTED,
        ApplicationStatus.RESUME_READY,
        ApplicationStatus.FORM_SCANNED,
        ApplicationStatus.NEEDS_INPUT,
    }
)


def transition_path(current: ApplicationStatus, target: ApplicationStatus) -> list[ApplicationStatus] | None:
    """Shortest allowed sequence of statuses from ``current`` to ``target`` (exclusive of current)."""
    if current is target:
        return []
    previous: dict[ApplicationStatus, ApplicationStatus] = {}
    queue: deque[ApplicationStatus] = deque([current])
    seen = {current}
    while queue:
        node = queue.popleft()
        for nxt in ALLOWED_APPLICATION_TRANSITIONS.get(node, frozenset()):
            if nxt in seen:
                continue
            previous[nxt] = node
            if nxt is target:
                path = [target]
                while previous[path[-1]] is not current:
                    path.append(previous[path[-1]])
                return list(reversed(path))
            seen.add(nxt)
            queue.append(nxt)
    return None


def advance_application(
    store: ApplicationStore,
    application_id: str,
    target: ApplicationStatus,
    *,
    notes: str | None = None,
) -> ApplicationRecord:
    """Move an application to ``target`` through every required intermediate state."""
    application = store.get_application(application_id)
    if application is None:
        raise KeyError(f"Unknown application_id: {application_id}")
    path = transition_path(application.status, target)
    if path is None:
        raise InvalidApplicationTransition(
            f"Cannot reach {target.value} from {application.status.value}"
        )
    for index, status in enumerate(path):
        application = store.transition_application(
            application_id,
            status,
            notes if index == len(path) - 1 else None,
        )
    return application


def _latest_scan_per_step(scans: list[FormScan]) -> list[FormScan]:
    latest: dict[str, FormScan] = {}
    for scan in scans:  # oldest first, so later scans of the same step win
        key = scan.step_key or scan.page_url
        latest[key] = scan
    return sorted(latest.values(), key=lambda scan: scan.captured_at)


def build_submission_bundle(
    app: FastAPI,
    *,
    application_id: str,
    profile_id: str,
    confirmed_by: str = "user",
    detection_evidence: str = "",
    notes: str = "",
) -> SubmissionBundle:
    """Freeze the current forms, files, and job into a receipt (not yet saved)."""
    # Imported here: api.py owns the plan builder and imports this module's callers.
    from latex_resume.api import _build_fill_plan_for_scan

    store: ApplicationStore = app.state.application_store
    application = store.get_application(application_id)
    if application is None:
        raise KeyError(f"Unknown application_id: {application_id}")
    job = store.get_job(application.job_id)
    profile = store.get_candidate_profile(profile_id)

    fields: list[SubmissionField] = []
    steps: list[SubmissionStep] = []
    for scan in _latest_scan_per_step(store.list_form_scans(application_id)):
        plan = _build_fill_plan_for_scan(app, scan_id=scan.scan_id, profile_id=profile_id)
        steps.append(
            SubmissionStep(
                scan_id=scan.scan_id,
                step_key=scan.step_key,
                page_url=scan.page_url,
                captured_at=scan.captured_at,
                field_count=len(scan.questions),
            )
        )
        for question, action, item in zip(scan.questions, plan.actions, plan.review_items, strict=True):
            if question.input_type == "file":
                value: str | bool | list[str] | None = "attached" if question.current_value_present else None
            elif question.current_value_present and action.action == "skip":
                value = question.current_value
            else:
                value = action.value
            fields.append(
                SubmissionField(
                    field_id=question.field_id,
                    label=question.label,
                    step_key=scan.step_key,
                    required=question.required,
                    input_type=question.input_type,
                    value=value,
                    source=item.answer_source,
                )
            )

    resume = store.get_latest_application_artifact(
        application_id, artifact_type=ApplicationArtifactType.TAILORED_RESUME, status=ApplicationArtifactStatus.UPLOADED
    ) or store.get_latest_application_artifact(
        application_id, artifact_type=ApplicationArtifactType.TAILORED_RESUME, status=ApplicationArtifactStatus.APPROVED
    )
    if resume is not None:
        resume_origin = "tailored_artifact"
        resume_filename, resume_sha, resume_id = resume.filename, resume.pdf_sha256, resume.artifact_id
    elif profile.resume_pdf_sha256 or profile.resume_pdf_filename:
        resume_origin = "profile_resume"
        resume_filename, resume_sha, resume_id = profile.resume_pdf_filename, profile.resume_pdf_sha256, None
    else:
        resume_origin, resume_filename, resume_sha, resume_id = "none", "", "", None

    letter = store.get_latest_application_artifact(
        application_id, artifact_type=ApplicationArtifactType.COVER_LETTER, status=ApplicationArtifactStatus.UPLOADED
    ) or store.get_latest_application_artifact(
        application_id, artifact_type=ApplicationArtifactType.COVER_LETTER, status=ApplicationArtifactStatus.APPROVED
    )

    description = job.description if job else ""
    return SubmissionBundle(
        bundle_id=str(uuid.uuid4()),
        application_id=application_id,
        profile_id=profile_id,
        job_id=application.job_id,
        job_title=application.job_title or (job.title if job else ""),
        company=application.company or (job.company if job else ""),
        provider=application.provider.value if application.provider else (job.provider.value if job else ""),
        apply_url=application.apply_url or (job.apply_url if job else ""),
        source_url=application.source_url or (job.source_url if job else ""),
        job_description_sha256=hashlib.sha256(description.encode("utf-8")).hexdigest() if description else "",
        resume_artifact_id=resume_id,
        resume_filename=resume_filename,
        resume_sha256=resume_sha,
        resume_origin=resume_origin,  # type: ignore[arg-type]
        cover_letter_artifact_id=letter.artifact_id if letter else None,
        cover_letter_filename=letter.filename if letter else "",
        cover_letter_sha256=(letter.pdf_sha256 or hashlib.sha256(letter.text_content.encode("utf-8")).hexdigest()) if letter else "",
        fields=fields,
        steps=steps,
        confirmed_by=confirmed_by,  # type: ignore[arg-type]
        detection_evidence=detection_evidence[:500],
        notes=notes[:2000],
        created_at=utc_now(),
    )


def confirm_submission(
    app: FastAPI,
    *,
    application_id: str,
    profile_id: str,
    confirmed_by: str = "user",
    detection_evidence: str = "",
    notes: str = "",
) -> tuple[ApplicationRecord, SubmissionBundle]:
    """Write the receipt, advance to ``submitted``, and schedule a follow-up."""
    store: ApplicationStore = app.state.application_store
    existing = store.get_submission_bundle(application_id)
    application = store.get_application(application_id)
    if application is None:
        raise KeyError(f"Unknown application_id: {application_id}")
    if existing is not None and application.status is ApplicationStatus.SUBMITTED:
        return application, existing

    bundle = store.save_submission_bundle(
        build_submission_bundle(
            app,
            application_id=application_id,
            profile_id=profile_id,
            confirmed_by=confirmed_by,
            detection_evidence=detection_evidence,
            notes=notes,
        )
    )
    application = advance_application(store, application_id, ApplicationStatus.SUBMITTED, notes=notes or None)
    application = store.update_application(application_id, {"submission_bundle_id": bundle.bundle_id})
    store.create_application_event(
        application_id=application_id,
        kind="submission_confirmed",
        label="Submission recorded",
        detail=(
            f"{len(bundle.fields)} fields across {len(bundle.steps)} step(s); resume: {bundle.resume_filename or 'none'}"
            + (f"; cover letter: {bundle.cover_letter_filename}" if bundle.cover_letter_filename else "")
        ),
        payload={
            "bundle_id": bundle.bundle_id,
            "confirmed_by": confirmed_by,
            "detection_evidence": detection_evidence[:200],
        },
    )
    ensure_follow_up_task(store, application)
    return application, bundle


def ensure_follow_up_task(store: ApplicationStore, application: ApplicationRecord) -> ApplicationTask | None:
    """Create the post-submission follow-up once; never duplicate an open one."""
    open_follow_ups = [
        task
        for task in store.list_application_tasks(application.application_id)
        if task.category == "follow_up" and task.status == "open"
    ]
    if open_follow_ups:
        return None
    due = (datetime.now(timezone.utc) + timedelta(days=FOLLOW_UP_DAYS)).isoformat()
    return store.create_application_task(
        application_id=application.application_id,
        title=f"Follow up with {application.company or 'the employer'} on {application.job_title or 'your application'}",
        category="follow_up",
        due_at=due,
        notes="Auto-created when the submission was recorded.",
    )


def record_fill_result(
    app: FastAPI,
    *,
    scan_id: str,
    profile_id: str,
    filled: int,
    skipped: int,
    failed_field_ids: list[str],
) -> dict[str, Any]:
    """Log a reviewed fill and advance to ready_for_review / needs_input (never backwards)."""
    from latex_resume.api import _build_fill_plan_for_scan

    store: ApplicationStore = app.state.application_store
    scan = store.get_form_scan(scan_id)
    if scan is None:
        raise KeyError(f"Unknown scan_id: {scan_id}")
    result: dict[str, Any] = {"scan_id": scan_id, "application_id": scan.application_id, "status": None, "unresolved_required": []}
    if not scan.application_id:
        return result
    plan = _build_fill_plan_for_scan(app, scan_id=scan_id, profile_id=profile_id)
    result["unresolved_required"] = list(plan.unresolved_required)
    store.create_application_event(
        application_id=scan.application_id,
        kind="fill_completed",
        label="Reviewed fields filled",
        detail=f"{filled} filled, {skipped} skipped, {len(failed_field_ids)} failed; {len(plan.unresolved_required)} required still unresolved.",
        payload={"scan_id": scan_id, "filled": filled, "skipped": skipped, "failed_field_ids": failed_field_ids[:50]},
    )
    application = store.get_application(scan.application_id)
    if application is None or application.status not in _FILL_ADVANCEABLE:
        result["status"] = application.status.value if application else None
        return result
    target = ApplicationStatus.READY_FOR_REVIEW if not plan.unresolved_required else ApplicationStatus.NEEDS_INPUT
    if application.status is target:
        result["status"] = target.value
        return result
    try:
        application = advance_application(store, scan.application_id, target)
    except InvalidApplicationTransition:
        pass
    result["status"] = application.status.value
    return result
