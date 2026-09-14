"""Profile routes: /profile*, /profiles*."""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile

from latex_resume.api import (
    ActiveProfileResponse,
    ProfileListItem,
    ProfileListResponse,
    ProfilePatch,
    ProfileResumeInfo,
    ProfileResumeUploadResponse,
    ProfileSetupQuestion,
    ProfileSetupResponse,
    ProfileView,
    ProjectSyncResponse,
    LLMSettingsUpdate,
    LLMSettingsView,
    LLMUsageView,
    SavedAnswerListResponse,
    SavedAnswerUpsertRequest,
    SetActiveProfileRequest,
    _deep_merge_profile_dict,
    _profile_has_pdf,
    _profile_resume_info,
    _profile_resume_upload_response,
    _profile_view,
    _refresh_resume_projects,
    _repair_profile_from_resume_metadata,
    _render_profile_latex_to_pdf,
)
from latex_resume.artifact_files import persist_b64_pdf
from latex_resume.form_resolution import normalize_answer_prompt, profile_setup_status
from latex_resume.job_models import (
    CandidateProfile,
    ProjectRecord,
    ProjectSource,
    SavedAnswer,
    utc_now,
)
from latex_resume.local_auth import auth_required, require_profile_match
from latex_resume.profile_extraction import profile_with_resume_prefill
from latex_resume.project_library import GitHubProjectClient
from latex_resume.renderer import render_pdf
from latex_resume.routers._deps import resolve_request_profile_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _scoped(request: Request, x_profile_id: str | None, profile_id: str | None = None) -> str:
    """Acting profile for this request; a ``profile_id`` query is honored only when it matches."""
    return resolve_request_profile_id(request=request, x_profile_id=x_profile_id, profile_id=profile_id)


@router.get("/profile", response_model=CandidateProfile)
async def get_profile(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> CandidateProfile:
    """Return locally stored candidate facts and search preferences."""
    return request.app.state.application_store.get_candidate_profile(_scoped(request, x_profile_id, profile_id))


@router.get("/profile/view", response_model=ProfileView)
async def get_profile_view(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProfileView:
    """Return editable profile facts without raw resume source or PDF bytes."""
    profile = request.app.state.application_store.get_candidate_profile(_scoped(request, x_profile_id, profile_id))
    repaired = _repair_profile_from_resume_metadata(profile)
    if repaired is not profile:
        profile = request.app.state.application_store.save_candidate_profile(repaired)
    return _profile_view(profile)


@router.get("/profile/active", response_model=ActiveProfileResponse)
async def get_active_profile(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ActiveProfileResponse:
    """Return the profile selected in the local web UI."""
    profile_id = resolve_request_profile_id(request=request, x_profile_id=x_profile_id)
    profile = request.app.state.application_store.get_candidate_profile(profile_id)
    return ActiveProfileResponse(
        profile_id=profile.profile_id,
        full_name=profile.full_name,
        email=profile.email,
        resume_filename=profile.resume_pdf_filename or profile.resume_filename,
        has_pdf=_profile_has_pdf(profile),
        has_latex_source=bool(profile.resume_latex_source),
    )


@router.get("/profiles", response_model=ProfileListResponse)
async def list_profiles(request: Request) -> ProfileListResponse:
    """List local profiles so the extension/web UI can pick an existing account.

    With auth required, only the authenticated profile is listed: other users'
    names and emails are not a picker.
    """
    items: list[ProfileListItem] = []
    profiles = request.app.state.application_store.list_candidate_profiles()
    if auth_required():
        bound = getattr(request.state, "auth_profile_id", None)
        profiles = [profile for profile in profiles if profile.profile_id == bound]
    for profile in profiles:
        has_pdf = _profile_has_pdf(profile)
        usable = bool(
            has_pdf
            or profile.resume_latex_source.strip()
            or (profile.full_name.strip() and profile.email.strip())
        )
        items.append(
            ProfileListItem(
                profile_id=profile.profile_id,
                full_name=profile.full_name,
                email=profile.email,
                has_pdf=has_pdf,
                has_latex_source=bool(profile.resume_latex_source.strip()),
                usable=usable,
            )
        )
    return ProfileListResponse(profiles=items)


@router.put("/profile/active", response_model=ActiveProfileResponse)
async def set_active_profile(
    request: Request,
    body: SetActiveProfileRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ActiveProfileResponse:
    """Select which local profile the UI and extension should use."""
    if auth_required():
        bound = resolve_request_profile_id(
            request=request,
            x_profile_id=x_profile_id,
            profile_id=body.profile_id,
        )
        profile_id = request.app.state.application_store.set_active_profile_id(bound)
    else:
        profile_id = request.app.state.application_store.set_active_profile_id(body.profile_id)
    profile = request.app.state.application_store.get_candidate_profile(profile_id)
    return ActiveProfileResponse(
        profile_id=profile.profile_id,
        full_name=profile.full_name,
        email=profile.email,
        resume_filename=profile.resume_pdf_filename or profile.resume_filename,
        has_pdf=_profile_has_pdf(profile),
        has_latex_source=bool(profile.resume_latex_source),
    )


@router.get("/profile/setup-questions", response_model=ProfileSetupResponse)
async def get_profile_setup_questions(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProfileSetupResponse:
    """Return common application questions covered by the user profile."""
    questions = [
        ProfileSetupQuestion.model_validate(item)
        for item in profile_setup_status(
            request.app.state.application_store.get_candidate_profile(_scoped(request, x_profile_id, profile_id))
        )
    ]
    missing_required = [
        question.label
        for question in questions
        if question.required and not question.value_present
    ]
    return ProfileSetupResponse(
        questions=questions,
        missing_required=missing_required,
        ready_for_basic_autofill=not missing_required,
    )


@router.get("/profile/answers", response_model=SavedAnswerListResponse)
async def list_profile_answers(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> SavedAnswerListResponse:
    """Return the answers bank: reviewed answers remembered from earlier forms."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    return SavedAnswerListResponse(
        answers=request.app.state.application_store.list_profile_answers(scoped_profile_id)
    )


@router.post("/profile/answers", response_model=SavedAnswer)
async def upsert_profile_answer(
    request: Request,
    body: SavedAnswerUpsertRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> SavedAnswer:
    """Add or replace a remembered answer by its prompt text."""
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    answer = SavedAnswer(
        answer_id=str(uuid.uuid4()),
        profile_id=scoped_profile_id,
        intent=body.intent,
        prompt_text=body.prompt_text.strip(),
        normalized_prompt=normalize_answer_prompt(body.prompt_text),
        value=body.value,
        aliases=[alias.strip() for alias in body.aliases if alias.strip()],
        source="user",
        ats_provider=body.ats_provider,
    )
    return request.app.state.application_store.upsert_profile_answer(answer)


@router.delete("/profile/answers/{answer_id}", status_code=204)
async def delete_profile_answer(
    request: Request,
    answer_id: str,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> None:
    scoped_profile_id = resolve_request_profile_id(
        request=request,
        x_profile_id=x_profile_id,
        profile_id=profile_id,
    )
    if not request.app.state.application_store.delete_profile_answer(scoped_profile_id, answer_id):
        raise HTTPException(404, f"Answer '{answer_id}' not found.")


@router.get("/profile/projects", response_model=list[ProjectRecord])
async def list_profile_projects(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> list[ProjectRecord]:
    """Return cached project evidence for the acting profile."""
    resolved_profile_id = _scoped(request, x_profile_id, profile_id)
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    if profile.resume_latex_source.strip():
        _refresh_resume_projects(
            request.app,
            profile_id=resolved_profile_id,
            latex_source=profile.resume_latex_source,
        )
    return request.app.state.application_store.list_profile_projects(resolved_profile_id)


@router.post("/profile/projects/sync/github", response_model=ProjectSyncResponse)
async def sync_profile_github_projects(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProjectSyncResponse:
    """Fetch public non-fork GitHub repositories into the local project library."""
    resolved_profile_id = _scoped(request, x_profile_id, profile_id)
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    if not profile.github_url.strip():
        raise HTTPException(409, "Add a GitHub profile URL before syncing public projects.")
    try:
        projects = await GitHubProjectClient().fetch_public_projects(
            profile_id=resolved_profile_id,
            github_url=profile.github_url,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise HTTPException(
                429,
                "GitHub rate limited this public sync. Try again later or continue with resume projects.",
            ) from exc
        raise HTTPException(
            502,
            f"GitHub project sync failed with HTTP {exc.response.status_code}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            "GitHub project sync failed. Continue with resume projects and try again later.",
        ) from exc
    saved = request.app.state.application_store.replace_profile_projects(
        resolved_profile_id,
        ProjectSource.GITHUB,
        projects,
    )
    warnings = [] if saved else ["No public non-fork GitHub repositories were found."]
    return ProjectSyncResponse(projects=saved, warnings=warnings)


@router.put("/profile", response_model=CandidateProfile)
async def update_profile(
    request: Request,
    body: CandidateProfile,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> CandidateProfile:
    """Replace the candidate profile with explicitly supplied facts (your own profile only)."""
    scoped = _scoped(request, x_profile_id, body.profile_id)
    require_profile_match(scoped, body.profile_id)
    return request.app.state.application_store.save_candidate_profile(body.model_copy(update={"profile_id": scoped}))


def _llm_settings_view(profile: CandidateProfile, encrypted: bool) -> LLMSettingsView:
    settings = profile.llm_settings
    key = settings.api_key or ""
    return LLMSettingsView(
        backend=settings.backend,
        model=settings.model,
        has_api_key=bool(key),
        api_key_tail=key[-4:] if len(key) >= 8 else "",
        daily_call_budget=settings.daily_call_budget,
        daily_token_budget=settings.daily_token_budget,
        encrypted_at_rest=encrypted,
    )


@router.get("/profile/llm", response_model=LLMSettingsView)
async def get_profile_llm_settings(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> LLMSettingsView:
    """This profile's model routing and budget; the API key is never returned."""
    store_ = request.app.state.application_store
    return _llm_settings_view(store_.get_candidate_profile(_scoped(request, x_profile_id, profile_id)), store_.protection.enabled)


@router.put("/profile/llm", response_model=LLMSettingsView)
async def update_profile_llm_settings(
    request: Request,
    body: LLMSettingsUpdate,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> LLMSettingsView:
    """Set a per-profile backend, key, model, and daily budgets."""
    store_ = request.app.state.application_store
    scoped = _scoped(request, x_profile_id, profile_id)
    profile = store_.get_candidate_profile(scoped)
    updates = body.model_dump(exclude_unset=True, exclude={"clear_budgets"})
    if "api_key" in updates and updates["api_key"] is not None and not updates["api_key"].strip():
        updates["api_key"] = None
    if body.clear_budgets:
        updates["daily_call_budget"] = None
        updates["daily_token_budget"] = None
    settings = profile.llm_settings.model_copy(update=updates)
    if settings.api_key and not store_.protection.enabled and auth_required():
        logger.warning("Storing an LLM key for %s without APPLYTEX_DATA_KEY; it is not encrypted at rest.", scoped)
    saved = store_.save_candidate_profile(profile.model_copy(update={"llm_settings": settings}))
    return _llm_settings_view(saved, store_.protection.enabled)


@router.get("/profile/llm/usage", response_model=LLMUsageView)
async def get_profile_llm_usage(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> LLMUsageView:
    store_ = request.app.state.application_store
    scoped = _scoped(request, x_profile_id, profile_id)
    settings = store_.get_candidate_profile(scoped).llm_settings
    usage = store_.get_llm_usage_today(scoped)
    return LLMUsageView(
        profile_id=scoped,
        day=str(usage["day"]),
        calls=int(usage["calls"]),
        tokens=int(usage["tokens"]),
        daily_call_budget=settings.daily_call_budget,
        daily_token_budget=settings.daily_token_budget,
    )


@router.get("/profile/export")
async def export_profile(
    request: Request,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Everything stored for this profile as portable JSON (no PDF bytes, no secrets)."""
    return request.app.state.application_store.export_profile_data(_scoped(request, x_profile_id, profile_id))


@router.delete("/profile")
async def delete_profile(
    request: Request,
    confirm: str = "",
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Delete the profile and every application, receipt, answer, run and file it owns.

    ``confirm`` must equal the profile id: a typed confirmation, not a checkbox.
    """
    scoped = _scoped(request, x_profile_id, profile_id)
    if confirm.strip() != scoped:
        raise HTTPException(409, f"Pass confirm={scoped} to delete this profile and everything it owns.")
    removed = request.app.state.application_store.delete_profile_data(scoped)
    request.app.state.auth_store.revoke_profile_tokens(scoped)
    return {"profile_id": scoped, "removed": removed}


@router.patch("/profile", response_model=ProfileView)
async def patch_profile(
    request: Request,
    body: ProfilePatch,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProfileView:
    """Merge editable profile facts while preserving stored resume payloads."""
    resolved_profile_id = _scoped(request, x_profile_id, profile_id)
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    updates = body.model_dump(exclude_unset=True)
    merged = CandidateProfile.model_validate(
        _deep_merge_profile_dict(profile.model_dump(), updates)
    )
    saved = request.app.state.application_store.save_candidate_profile(merged)
    return _profile_view(saved)


@router.get("/profile/resume", response_model=ProfileResumeInfo)
async def get_profile_resume(
    request: Request,
    profile_id: str | None = None,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProfileResumeInfo:
    """Return metadata for the resume saved with a local candidate profile."""
    resolved_profile_id = _scoped(request, x_profile_id, profile_id)
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    return _profile_resume_info(profile)


@router.post("/profile/resume", response_model=ProfileResumeUploadResponse)
async def upload_profile_resume(
    request: Request,
    file: UploadFile = File(...),
    profile_id: str | None = None,
    overwrite: bool = False,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-Id"),
) -> ProfileResumeUploadResponse:
    """Store a profile resume and extract facts into the candidate profile.

    PDF uploads are stored for direct application use. LaTeX uploads are stored
    for job-specific customization and rendered to PDF when a local LaTeX engine
    is available. Parsed resume content is mapped into profile fields when found.
    """
    if not file.filename:
        raise HTTPException(400, "Resume filename is required.")
    suffix = Path(file.filename).suffix.casefold()
    if suffix not in {".tex", ".pdf"}:
        raise HTTPException(400, "Only .tex and .pdf profile resumes are accepted.")

    raw = await file.read()
    if len(raw) > 2_500_000:
        raise HTTPException(413, "Resume file too large (max 2.5 MB).")

    resolved_profile_id = _scoped(request, x_profile_id, profile_id)
    profile = request.app.state.application_store.get_candidate_profile(resolved_profile_id)
    updates: dict[str, Any] = {
        "resume_filename": file.filename,
        "resume_updated_at": utc_now(),
    }
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF"):
            raise HTTPException(400, "Uploaded .pdf does not look like a PDF file.")
        data_b64 = base64.b64encode(raw).decode()
        pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
            db_path=request.app.state.application_store.path,
            profile_id=resolved_profile_id,
            name=file.filename,
            data_b64=data_b64,
        )
        updates.update(
            {
                "resume_pdf_filename": file.filename,
                "resume_pdf_b64": "",
                "resume_pdf_path": pdf_path,
                "resume_pdf_size_bytes": pdf_size,
                "resume_pdf_sha256": pdf_sha,
            }
        )
    else:
        try:
            latex_source = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(400, "LaTeX resume must be UTF-8 encoded.") from None
        if len(latex_source) > 500_000:
            raise HTTPException(413, "LaTeX resume too large (max 500 KB).")
        try:
            from latex_resume.parser import parse as _parse

            _parse(latex_source, resume_id=Path(file.filename).stem)
        except Exception as exc:
            raise HTTPException(422, f"Failed to parse .tex resume: {exc}") from exc
        updates["resume_latex_source"] = latex_source
        render = render_pdf(latex_source)
        if render.ok and render.pdf_bytes:
            pdf_filename = f"{Path(file.filename).stem}.pdf"
            data_b64 = base64.b64encode(render.pdf_bytes).decode()
            pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
                db_path=request.app.state.application_store.path,
                profile_id=resolved_profile_id,
                name=pdf_filename,
                data_b64=data_b64,
            )
            updates.update(
                {
                    "resume_pdf_filename": pdf_filename,
                    "resume_pdf_b64": "",
                    "resume_pdf_path": pdf_path,
                    "resume_pdf_size_bytes": pdf_size,
                    "resume_pdf_sha256": pdf_sha,
                }
            )

    updated = profile.model_copy(update=updates)
    try:
        prefilled, applied = profile_with_resume_prefill(
            updated,
            filename=file.filename,
            data=raw,
            overwrite=overwrite,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.warning("Profile prefill failed for %s: %s", file.filename, exc)
        prefilled = updated
        applied = []

    saved = request.app.state.application_store.save_candidate_profile(prefilled)
    return _profile_resume_upload_response(saved, prefill_applied=applied)
