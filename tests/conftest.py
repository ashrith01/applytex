from __future__ import annotations

from pathlib import Path

import pytest

from latex_resume.models import ParseResult
from latex_resume.parser import parse

SAMPLE_PATH = Path(__file__).parent.parent / "samples" / "sample_resume.tex"


@pytest.fixture
def sample_tex() -> str:
    return SAMPLE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def parsed(sample_tex: str) -> ParseResult:
    return parse(sample_tex, resume_id="test")
