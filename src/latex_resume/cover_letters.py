"""Job-specific cover letters grounded in the tailored resume and the JD.

A letter is a ``COVER_LETTER`` application artifact. Generation produces
reviewed text (``text_content``) with a separate length policy from the
100-word screening answers; approval renders a one-page PDF when a LaTeX
engine is available and otherwise keeps the text file. The validator rejects
numbers that appear in neither the resume nor the job description, any URL,
and template placeholders, so a letter can never introduce a claim the resume
does not support.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from latex_resume.application_store import ApplicationStore
from latex_resume.artifact_files import persist_b64_pdf
from latex_resume.job_models import (
    ApplicationArtifact,
    ApplicationArtifactStatus,
    ApplicationArtifactType,
    ApplicationRecord,
    CandidateProfile,
    JobPosting,
    utc_now,
)
from latex_resume.llm import complete_json

MIN_WORDS = 150
MAX_WORDS = 400
TARGET_WORDS = "230-320"

_SYSTEM_PROMPT = (
    "You write concise, specific cover letters for a job candidate using only the "
    "candidate's resume and the job description. You never invent employers, titles, "
    "dates, metrics, tools, or credentials. Output ONLY a valid JSON object."
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def resume_text_for_application(store: ApplicationStore, application: ApplicationRecord, profile: CandidateProfile) -> tuple[str, str | None]:
    """Plain text of the approved tailored resume, else the profile resume. Returns (text, artifact_id)."""
    from latex_resume.extractor import extract_full_resume
    from latex_resume.optimizer import _build_plain_text
    from latex_resume.parser import parse

    artifact = store.get_latest_application_artifact(
        application.application_id,
        artifact_type=ApplicationArtifactType.TAILORED_RESUME,
        status=ApplicationArtifactStatus.APPROVED,
    )
    latex = artifact.latex_source if artifact and artifact.latex_source.strip() else profile.resume_latex_source
    if not latex.strip():
        return "", None
    try:
        parsed = parse(latex, resume_id=Path(profile.resume_filename or "profile_resume").stem)
        return _build_plain_text(extract_full_resume(parsed)), (artifact.artifact_id if artifact else None)
    except Exception:
        return "", None


def _letter_prompt(*, job: JobPosting, profile: CandidateProfile, resume_text: str) -> str:
    name = profile.full_name or f"{profile.first_name} {profile.last_name}".strip() or "the candidate"
    return (
        f"Write a cover letter of {TARGET_WORDS} words in three or four short paragraphs for {name}, "
        f"applying to {job.title} at {job.company}. Open with why this role and company, then two "
        "concrete resume-backed examples that map to the job's stated needs, then a brief close. "
        "Plain prose, no bullet points, no headers, no greeting line, no sign-off, no placeholders in "
        "brackets, no links. Use only facts from the resume text; every number you write must already "
        "appear in the resume or the job description.\n"
        "Return JSON: {\"letter\": str, \"evidence\": [short resume facts you relied on]}\n\n"
        f"Job description:\n{job.description[:8000]}\n\n"
        f"Resume:\n{resume_text[:8000]}"
    )


def validate_letter(letter: str, *, resume_text: str, job_description: str) -> list[str]:
    errors: list[str] = []
    text = re.sub(r"\s+", " ", letter).strip()
    words = _word_count(text)
    if words < MIN_WORDS:
        errors.append(f"letter is too short ({words} words; minimum {MIN_WORDS})")
    if words > MAX_WORDS:
        errors.append(f"letter is too long ({words} words; maximum {MAX_WORDS})")
    if re.search(r"https?://|www\.", text, flags=re.I):
        errors.append("letter includes a link")
    if re.search(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}", text):
        errors.append("letter includes a template placeholder")
    corpus = f"{resume_text} {job_description}".casefold()
    # A trailing % is part of the token ("57%"), so no word boundary after it.
    unsupported = sorted(
        {token for token in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?%?(?!\w)", text) if token.casefold() not in corpus}
    )
    if unsupported:
        errors.append("letter includes numbers not found in the resume or job description: " + ", ".join(unsupported[:5]))
    return errors


async def generate_cover_letter(
    store: ApplicationStore,
    *,
    application: ApplicationRecord,
    job: JobPosting,
    profile: CandidateProfile,
) -> ApplicationArtifact:
    """Draft a grounded letter and store it as a generated artifact (text only)."""
    resume_text, resume_artifact_id = resume_text_for_application(store, application, profile)
    if not resume_text:
        raise ValueError("Upload a .tex profile resume (or approve a tailored resume) before drafting a cover letter.")
    if not job.description.strip():
        raise ValueError("The captured job has no description to ground the letter in.")

    result = await complete_json(
        _letter_prompt(job=job, profile=profile, resume_text=resume_text),
        system=_SYSTEM_PROMPT,
        task="application",
    )
    if not isinstance(result, dict) or not isinstance(result.get("letter"), str):
        raise ValueError("The cover letter provider returned no letter text.")
    letter = re.sub(r"[ \t]+\n", "\n", str(result["letter"])).strip()
    errors = validate_letter(letter, resume_text=resume_text, job_description=job.description)
    if errors:
        raise ValueError("Cover letter draft rejected: " + "; ".join(errors))
    evidence = [str(item)[:200] for item in result.get("evidence", []) if isinstance(item, str)][:8]

    now = utc_now()
    artifact = ApplicationArtifact(
        artifact_id=str(uuid.uuid4()),
        application_id=application.application_id,
        job_id=application.job_id,
        profile_id=profile.profile_id,
        type=ApplicationArtifactType.COVER_LETTER,
        status=ApplicationArtifactStatus.GENERATED,
        filename=_letter_filename(application, "txt"),
        mime_type="text/plain",
        text_content=letter,
        evidence_notes=evidence,
        warnings=[],
        source_tailor_session_id=resume_artifact_id,
        created_at=now,
        updated_at=now,
    )
    return store.save_application_artifact(artifact)


def update_cover_letter_text(
    store: ApplicationStore,
    artifact: ApplicationArtifact,
    *,
    text: str,
    resume_text: str,
    job_description: str,
) -> ApplicationArtifact:
    """Replace the reviewed text; edits re-run the grounding validator and drop any PDF."""
    errors = validate_letter(text, resume_text=resume_text, job_description=job_description)
    if errors:
        raise ValueError("; ".join(errors))
    return store.save_application_artifact(
        artifact.model_copy(
            update={
                "text_content": text.strip(),
                "status": ApplicationArtifactStatus.GENERATED,
                "mime_type": "text/plain",
                "filename": _letter_filename_from(artifact.filename, "txt"),
                "pdf_b64": "",
                "pdf_path": "",
                "pdf_sha256": "",
                "pdf_size_bytes": 0,
                "page_count": 0,
                "approved_at": None,
            }
        )
    )


def approve_cover_letter(
    store: ApplicationStore,
    artifact: ApplicationArtifact,
    *,
    profile: CandidateProfile,
    application: ApplicationRecord,
    db_path: Path,
) -> ApplicationArtifact:
    """Render a one-page PDF when pdflatex is available; otherwise approve the text file."""
    from latex_resume.renderer import render_pdf

    latex = cover_letter_latex(artifact.text_content, profile=profile, application=application)
    updates: dict[str, Any] = {
        "status": ApplicationArtifactStatus.APPROVED,
        "approved_at": utc_now(),
        "latex_source": latex,
    }
    render = render_pdf(latex)
    if render.ok and render.pdf_bytes and render.page_count <= 1:
        pdf_path, pdf_size, pdf_sha = persist_b64_pdf(
            db_path=db_path,
            profile_id=profile.profile_id,
            name=f"{application.application_id}_{_letter_filename(application, 'pdf')}",
            data_b64=base64.b64encode(render.pdf_bytes).decode(),
        )
        updates.update(
            {
                "filename": _letter_filename(application, "pdf"),
                "mime_type": "application/pdf",
                "pdf_path": pdf_path,
                "pdf_size_bytes": pdf_size,
                "pdf_sha256": pdf_sha,
                "page_count": render.page_count,
            }
        )
    else:
        reason = (
            "the letter exceeded one page"
            if render.ok and render.page_count > 1
            else "no LaTeX engine is installed"
            if not render.ok and not render.log.strip()
            else "LaTeX failed: " + " ".join(render.log.strip().splitlines()[-3:])[:300]
        )
        updates["warnings"] = [f"PDF was not rendered ({reason}); the text version is approved."]
    return store.save_application_artifact(artifact.model_copy(update=updates))


def artifact_file_payload(artifact: ApplicationArtifact, *, db_path: Path) -> dict[str, str]:
    """Bytes for the extension to attach: the PDF when rendered, else the text."""
    from latex_resume.artifact_files import load_pdf_b64

    if artifact.mime_type == "application/pdf":
        data = load_pdf_b64(db_path=db_path, pdf_path=artifact.pdf_path, pdf_b64=artifact.pdf_b64)
        if data:
            return {"filename": artifact.filename, "mime_type": "application/pdf", "data_b64": data}
    return {
        "filename": _letter_filename_from(artifact.filename, "txt"),
        "mime_type": "text/plain",
        "data_b64": base64.b64encode(artifact.text_content.encode("utf-8")).decode(),
    }


# ---------------------------------------------------------------------------
# LaTeX letter template
# ---------------------------------------------------------------------------

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(char, char) for char in text)


def cover_letter_latex(body: str, *, profile: CandidateProfile, application: ApplicationRecord) -> str:
    name = profile.full_name or f"{profile.first_name} {profile.last_name}".strip() or "Candidate"
    contact = " \\textbar{} ".join(
        latex_escape(part) for part in (profile.email, profile.phone, profile.location) if part
    )
    paragraphs = [latex_escape(part.strip()) for part in re.split(r"\n\s*\n", body.strip()) if part.strip()]
    company = latex_escape(application.company or "Hiring Team")
    title = latex_escape(application.job_title or "the open role")
    return "\n".join(
        [
            # Only packages present in a minimal TeX Live (geometry, parskip):
            # lmodern / fontenc would fail on CI images and some laptops.
            r"\documentclass[11pt]{article}",
            r"\usepackage[margin=1in]{geometry}",
            r"\usepackage{parskip}",
            r"\pagestyle{empty}",
            r"\begin{document}",
            r"\noindent{\Large\textbf{" + latex_escape(name) + "}}\\\\",
            contact + r"\\[1.2em]" if contact else r"\\[0.6em]",
            latex_escape(date.today().strftime("%B %d, %Y")) + r"\\[1.2em]",
            f"Dear {company} Hiring Team,\\\\[0.4em]",
            *[para + "\n" for para in paragraphs],
            r"\\[0.4em]Thank you for considering my application for " + title + ".",
            r"\\[1.2em]Sincerely,\\[0.6em]",
            latex_escape(name),
            r"\end{document}",
            "",
        ]
    )


def _letter_filename(application: ApplicationRecord, extension: str) -> str:
    company = re.sub(r"[^A-Za-z0-9]+", "_", application.company or "company").strip("_") or "company"
    return f"{company}_cover_letter.{extension}"


def _letter_filename_from(filename: str, extension: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else (filename or "cover_letter")
    return f"{stem}.{extension}"


def letter_summary(artifact: ApplicationArtifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "status": artifact.status.value,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "word_count": _word_count(artifact.text_content),
        "has_pdf": bool(artifact.pdf_path or artifact.pdf_b64),
        "evidence_notes": artifact.evidence_notes,
        "warnings": artifact.warnings,
        "updated_at": artifact.updated_at,
    }


def parse_letter_json(raw: str) -> dict[str, Any]:  # pragma: no cover - helper for manual use
    return json.loads(raw)
