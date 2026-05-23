"""Compile ``.tex`` to PDF and enforce the one-page constraint.

Primary path: run ``pdflatex`` in a temp dir and count pages with ``pypdf``. When no
LaTeX engine is on PATH, fall back to a crude word-count estimate so the one-page
check still returns a (clearly flagged) answer.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

# Approximate words that fit on a single page at typical resume settings.
DEFAULT_WORD_BUDGET = 420

_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?")
_BRACES_RE = re.compile(r"[{}\[\]]")


@dataclass
class RenderResult:
    """Outcome of a render / one-page check."""

    ok: bool
    page_count: int = 0
    overflow: bool = False
    pdf_bytes: bytes | None = None
    estimated: bool = False
    log: str = ""
    error: str | None = field(default=None)


def pdflatex_available(engine: str = "pdflatex") -> bool:
    """Return whether the given LaTeX engine is on PATH."""
    return shutil.which(engine) is not None


def render_pdf(
    tex: str,
    engine: str = "pdflatex",
    timeout: int = 60,
    passes: int = 1,
) -> RenderResult:
    """Compile ``tex`` to PDF and report the page count.

    Returns ``ok=False`` with ``error="engine_not_found"`` if no engine is available
    (use :func:`check_one_page` for automatic word-count fallback).
    """
    if not pdflatex_available(engine):
        return RenderResult(ok=False, error="engine_not_found")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "resume.tex").write_text(tex, encoding="utf-8")
        log = ""
        for _ in range(max(1, passes)):
            proc = subprocess.run(
                [engine, "-interaction=nonstopmode", "resume.tex"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            log = proc.stdout + proc.stderr

        pdf_path = tmp_path / "resume.pdf"
        if not pdf_path.exists():
            return RenderResult(ok=False, log=log, error="compile_failed")

        data = pdf_path.read_bytes()
        page_count = len(PdfReader(io.BytesIO(data)).pages)
        return RenderResult(
            ok=True,
            page_count=page_count,
            overflow=page_count > 1,
            pdf_bytes=data,
            log=log,
        )


def check_one_page(
    tex: str,
    word_budget: int = DEFAULT_WORD_BUDGET,
    engine: str = "pdflatex",
) -> RenderResult:
    """Verify the resume fits on one page, using pdflatex or a word-count fallback."""
    if pdflatex_available(engine):
        return render_pdf(tex, engine=engine)

    words = estimate_word_count(tex)
    overflow = words > word_budget
    return RenderResult(
        ok=True,
        page_count=2 if overflow else 1,
        overflow=overflow,
        estimated=True,
        log=f"word-count fallback: {words} words vs budget {word_budget}",
    )


def estimate_word_count(tex: str) -> int:
    """Crudely estimate visible word count by stripping comments and commands."""
    body = tex
    begin = body.find("\\begin{document}")
    if begin != -1:
        body = body[begin + len("\\begin{document}") :]
    body = _COMMENT_RE.sub("", body)
    body = _COMMAND_RE.sub(" ", body)
    body = _BRACES_RE.sub(" ", body)
    return len(body.split())
