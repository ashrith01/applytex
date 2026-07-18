"""Durability tests for tailor sessions and on-disk PDF artifacts."""

from __future__ import annotations

import base64
from pathlib import Path

from latex_resume.application_store import ApplicationStore
from latex_resume.artifact_files import load_pdf_b64, persist_b64_pdf
from latex_resume.tailor_store import TailorStore


def test_tailor_session_survives_store_rebind(tmp_path: Path) -> None:
    db_path = tmp_path / "durable.db"
    store = ApplicationStore(db_path)
    tailor = TailorStore(store)
    session = tailor.create(
        job_id="job-1",
        profile_id="alice",
        application_id="app-1",
        current_latex="\\documentclass{article}\\begin{document}Hi\\end{document}",
    )
    session.confirmed_skills = ["Python"]
    tailor.save(session)
    session_id = session.session_id

    rebound = TailorStore(ApplicationStore(db_path))
    restored = rebound.get(session_id)
    assert restored is not None
    assert restored.profile_id == "alice"
    assert restored.confirmed_skills == ["Python"]
    assert "Hi" in restored.current_latex


def test_persist_and_load_pdf_from_disk(tmp_path: Path) -> None:
    db_path = tmp_path / "files.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    pdf_bytes = b"%PDF-1.4 durable-test"
    data_b64 = base64.b64encode(pdf_bytes).decode()
    relative, size, checksum = persist_b64_pdf(
        db_path=db_path,
        profile_id="alice",
        name="resume.pdf",
        data_b64=data_b64,
    )
    assert size == len(pdf_bytes)
    assert checksum
    loaded = load_pdf_b64(db_path=db_path, pdf_path=relative, pdf_b64=None)
    assert base64.b64decode(loaded) == pdf_bytes
