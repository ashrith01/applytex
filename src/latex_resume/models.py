"""Pydantic models for the LaTeX resume pipeline.

The central structure is :class:`LatexResumeDoc` -- a JSON-serializable view of a
parsed ``.tex`` resume. It travels parser -> (LLM) -> reconstructor. Locked sections
are represented for completeness but are never sent to the LLM.

The :class:`StmtSpan` index (see :class:`ParseResult`) maps every editable statement's
``stmt_id`` to its exact character span in the original source, so reconstruction is a
pure string splice.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SectionType(str, Enum):
    """Classification of a resume section. Drives editable vs locked behaviour."""

    SUMMARY = "summary"
    WORK_EXPERIENCE = "work_experience"
    PROJECTS = "projects"
    SKILLS = "skills"
    PUBLICATIONS = "publications"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    PERSONAL_INFO = "personal_info"
    UNKNOWN = "unknown"


EDITABLE_SECTION_TYPES: frozenset[SectionType] = frozenset(
    {
        SectionType.SUMMARY,
        SectionType.WORK_EXPERIENCE,
        SectionType.PROJECTS,
        SectionType.SKILLS,
    }
)

LOCKED_SECTION_TYPES: frozenset[SectionType] = frozenset(
    {
        SectionType.EDUCATION,
        SectionType.CERTIFICATIONS,
        SectionType.PUBLICATIONS,
        SectionType.PERSONAL_INFO,
        SectionType.UNKNOWN,
    }
)


def word_count(text: str) -> int:
    """Count whitespace-delimited words in a piece of statement text."""
    return len(text.split())


class StmtSpan(BaseModel):
    """The exact character span of one statement's *text content* in the source.

    ``tex_start``/``tex_end`` bracket only the editable text -- not the ``\\item``
    command or surrounding whitespace -- so reconstruction replaces the slice
    ``[tex_start:tex_end]`` and leaves every other byte untouched.
    """

    tex_start: int
    tex_end: int
    item_command: str = ""
    prefix_ws: str = ""
    original_text: str


class Statement(BaseModel):
    """A single editable unit of text (a bullet, or the summary paragraph)."""

    stmt_id: str
    text: str
    is_locked: bool = False
    tex_command: str = "\\item"
    word_count: int = 0


class SkillLine(BaseModel):
    """One line within the skills section (a comma-separated list of skills)."""

    stmt_id: str
    text: str
    category: str | None = None
    is_locked: bool = False


class Entry(BaseModel):
    """An entry within an itemized section (one job, one project, one degree)."""

    entry_id: str
    header_text: str = ""
    title: str | None = None
    company: str | None = None
    years: str | None = None
    tex_start: int | None = None
    tex_end: int | None = None
    can_remove: bool = False
    statements: list[Statement] = Field(default_factory=list)


class Section(BaseModel):
    """A top-level resume section.

    Content lives in exactly one of ``statements`` (text sections like summary),
    ``entries`` (itemized sections like experience), ``skill_lines`` (skills), or
    ``fields`` (personal info).
    """

    section_id: str
    section_type: SectionType
    is_locked: bool
    display_name: str
    latex_section_command: str = ""
    lock_reason: str | None = None
    classification_uncertain: bool = False
    statements: list[Statement] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)
    skill_lines: list[SkillLine] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)


class PageBudget(BaseModel):
    """Word/bullet budget used to keep the resume to a single page."""

    estimated_word_count: int = 0
    max_word_budget: int = 420
    estimated_bullet_count: int = 0
    max_bullet_budget: int = 18


class LayoutParams(BaseModel):
    """Layout knobs injected into the controlled preamble block."""

    margin_mm: float = 20.0
    font_size_pt: float = 11.0
    line_spacing: float = 1.15


class LatexResumeDoc(BaseModel):
    """JSON-serializable structured view of a parsed ``.tex`` resume."""

    resume_id: str = ""
    source_type: str = "tex"
    latex_class: str = "unknown"
    classification_uncertain_sections: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    page_budget: PageBudget = Field(default_factory=PageBudget)

    def editable_statements(self) -> list[Statement]:
        """Flatten every editable statement across all editable sections."""
        out: list[Statement] = []
        for section in self.sections:
            if section.is_locked:
                continue
            out.extend(section.statements)
            for entry in section.entries:
                out.extend(entry.statements)
            for line in section.skill_lines:
                out.append(
                    Statement(
                        stmt_id=line.stmt_id,
                        text=line.text,
                        is_locked=line.is_locked,
                        tex_command="\\item",
                        word_count=word_count(line.text),
                    )
                )
        return [s for s in out if not s.is_locked]


class ParseResult(BaseModel):
    """Everything produced by parsing a ``.tex`` file.

    ``stmt_index`` is the splice map consumed by the reconstructor; ``latex_source``
    is the immutable original used as the splice base.
    """

    doc: LatexResumeDoc
    stmt_index: dict[str, StmtSpan] = Field(default_factory=dict)
    latex_source: str = ""
