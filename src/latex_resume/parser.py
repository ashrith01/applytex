"""Parse a ``.tex`` resume into a :class:`LatexResumeDoc` plus a splice index.

Design notes
------------
* The parser is heuristic, not a full LaTeX grammar. Resume documents have shallow,
  predictable nesting, so a brace-aware scanner over ``\\section`` headers and list
  environments is sufficient and robust.
* Statement spans cover *only the editable text content* -- never the ``\\item``
  command or surrounding whitespace. That makes reconstruction a pure slice
  replacement that leaves all other bytes byte-for-byte identical.
* The parser never mutates the source. Layout-parameter injection is a separate,
  opt-in step in :mod:`latex_resume.reconstructor`, so a no-op round trip is exactly
  the original bytes.
"""

from __future__ import annotations

import re

from latex_resume.models import (
    Entry,
    LatexResumeDoc,
    PageBudget,
    ParseResult,
    Section,
    SectionType,
    SkillLine,
    Statement,
    StmtSpan,
    word_count,
)

# Section title keyword groups, checked in priority order. Editable types are
# checked before locked ones so e.g. "Academic Projects" classifies as projects.
_CLASSIFICATION_ORDER: list[tuple[SectionType, tuple[str, ...]]] = [
    (SectionType.WORK_EXPERIENCE, ("experience", "employment", "work history", "career")),
    (SectionType.PROJECTS, ("project", "portfolio", "open source")),
    (SectionType.SKILLS, ("skill", "technical", "technolog", "tool", "stack", "expertise", "competenc")),
    (SectionType.SUMMARY, ("summary", "objective", "profile", "about", "overview")),
    (SectionType.EDUCATION, ("education", "academic", "degree", "university", "college", "school")),
    (SectionType.CERTIFICATIONS, ("certification", "certificate", "license", "licence", "credential", "award", "honor", "honour", "achievement")),
]

_LIST_ENVIRONMENTS: frozenset[str] = frozenset({"itemize", "enumerate", "cvitems", "highlights"})

_SECTION_KEY: dict[SectionType, str] = {
    SectionType.WORK_EXPERIENCE: "work",
    SectionType.PROJECTS: "proj",
    SectionType.SKILLS: "skills",
    SectionType.SUMMARY: "summary",
    SectionType.EDUCATION: "edu",
    SectionType.CERTIFICATIONS: "cert",
    SectionType.PERSONAL_INFO: "personal",
    SectionType.UNKNOWN: "unknown",
}

_DISPLAY_NAME: dict[SectionType, str] = {
    SectionType.WORK_EXPERIENCE: "Work Experience",
    SectionType.PROJECTS: "Projects",
    SectionType.SKILLS: "Skills",
    SectionType.SUMMARY: "Summary",
    SectionType.EDUCATION: "Education",
    SectionType.CERTIFICATIONS: "Certifications",
    SectionType.PERSONAL_INFO: "Personal Information",
    SectionType.UNKNOWN: "Section",
}

_SECTION_RE = re.compile(r"\\section\*?\s*\{")
_ITEM_RE = re.compile(r"\\item(?![a-zA-Z])")
_DOCCLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def detect_class(tex: str) -> str:
    """Return the lowercased ``\\documentclass`` name, or ``"unknown"``."""
    match = _DOCCLASS_RE.search(tex)
    return match.group(1).strip().lower() if match else "unknown"


def _read_braced(tex: str, open_idx: int) -> tuple[str, int]:
    """Read a brace-balanced group starting at ``tex[open_idx] == '{'``.

    Returns the inner content and the index immediately after the closing brace.
    """
    assert tex[open_idx] == "{"
    depth = 0
    i = open_idx
    n = len(tex)
    while i < n:
        c = tex[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return tex[open_idx + 1 : i], i + 1
        i += 1
    return tex[open_idx + 1 :], n


def _classify(title: str) -> tuple[SectionType, bool]:
    """Classify a section by its title. Returns ``(type, uncertain)``."""
    norm = re.sub(r"[^a-z ]", "", title.lower())
    for section_type, keywords in _CLASSIFICATION_ORDER:
        if any(kw in norm for kw in keywords):
            return section_type, False
    return SectionType.UNKNOWN, True


def _document_body_span(tex: str) -> tuple[int, int]:
    """Return ``(start, end)`` of the document body, or the whole string."""
    begin = tex.find("\\begin{document}")
    end = tex.find("\\end{document}")
    start = begin + len("\\begin{document}") if begin != -1 else 0
    stop = end if end != -1 else len(tex)
    return start, stop


def _find_list_environments(tex: str, start: int, stop: int) -> list[tuple[int, int, int]]:
    """Find top-level list environments in ``tex[start:stop]``.

    Returns a list of ``(content_start, content_end, block_end)`` absolute offsets:
    ``content_*`` brackets the span between ``\\begin{env}`` and ``\\end{env}``, while
    ``block_end`` is the offset just after the closing ``\\end{env}``. Nesting of list
    environments is handled by tracking depth.
    """
    env_alt = "|".join(re.escape(e) for e in _LIST_ENVIRONMENTS)
    token_re = re.compile(r"\\(begin|end)\s*\{(" + env_alt + r")\}")
    blocks: list[tuple[int, int, int]] = []
    depth = 0
    open_content_start = 0
    for m in token_re.finditer(tex, start, stop):
        if m.group(1) == "begin":
            if depth == 0:
                open_content_start = m.end()
            depth += 1
        else:  # end
            depth -= 1
            if depth == 0:
                blocks.append((open_content_start, m.start(), m.end()))
            elif depth < 0:
                depth = 0
    return blocks


def _trim_span(tex: str, start: int, end: int) -> tuple[int, int]:
    """Shrink ``[start, end)`` inward past leading/trailing whitespace."""
    while start < end and tex[start].isspace():
        start += 1
    while end > start and tex[end - 1].isspace():
        end -= 1
    return start, end


def _parse_items(tex: str, content_start: int, content_end: int) -> list[tuple[int, int, str]]:
    """Parse ``\\item`` text spans within a list-environment content range.

    Returns ``(text_start, text_end, text)`` tuples, one per item, where the span
    covers only the trimmed text content after ``\\item`` (and any ``[label]``).
    """
    item_starts = [m.start() for m in _ITEM_RE.finditer(tex, content_start, content_end)]
    spans: list[tuple[int, int, str]] = []
    for idx, raw_start in enumerate(item_starts):
        # Skip the \item command itself.
        cursor = raw_start + len("\\item")
        # Skip an optional [label] argument.
        probe = cursor
        while probe < content_end and tex[probe].isspace():
            probe += 1
        if probe < content_end and tex[probe] == "[":
            close = tex.find("]", probe)
            cursor = close + 1 if close != -1 else probe
        # Text runs until the next \item or the end of the environment.
        text_end = item_starts[idx + 1] if idx + 1 < len(item_starts) else content_end
        s, e = _trim_span(tex, cursor, text_end)
        spans.append((s, e, tex[s:e]))
    return spans


def parse(tex: str, resume_id: str = "", source_type: str = "tex") -> ParseResult:
    """Parse a ``.tex`` resume into a :class:`ParseResult`."""
    latex_class = detect_class(tex)
    body_start, body_stop = _document_body_span(tex)

    # Locate all section headers in the document body.
    headers: list[tuple[int, int, str]] = []  # (header_start, body_start, title)
    for m in _SECTION_RE.finditer(tex, body_start, body_stop):
        brace_idx = m.end() - 1
        title, after = _read_braced(tex, brace_idx)
        headers.append((m.start(), after, title.strip()))

    sections: list[Section] = []
    stmt_index: dict[str, StmtSpan] = {}
    uncertain: list[str] = []

    # Everything between \begin{document} and the first \section is the header block.
    first_header = headers[0][0] if headers else body_stop
    personal = _build_personal_info(tex, body_start, first_header)
    if personal is not None:
        sections.append(personal)

    for i, (_, hbody_start, title) in enumerate(headers):
        sec_body_stop = headers[i + 1][0] if i + 1 < len(headers) else body_stop
        section_type, is_uncertain = _classify(title)
        section = _build_section(
            tex=tex,
            title=title,
            section_type=section_type,
            is_uncertain=is_uncertain,
            body_start=hbody_start,
            body_stop=sec_body_stop,
            section_ordinal=i,
            stmt_index=stmt_index,
        )
        sections.append(section)
        if is_uncertain:
            uncertain.append(section.section_id)

    doc = LatexResumeDoc(
        resume_id=resume_id,
        source_type=source_type,
        latex_class=latex_class,
        classification_uncertain_sections=uncertain,
        sections=sections,
        page_budget=_estimate_budget(sections),
    )
    return ParseResult(doc=doc, stmt_index=stmt_index, latex_source=tex)


def _build_personal_info(tex: str, start: int, stop: int) -> Section | None:
    """Build the locked personal-info section from the pre-section header block."""
    s, e = _trim_span(tex, start, stop)
    if s >= e:
        return None
    raw = tex[s:e]
    fields: dict[str, str] = {}
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw)
    if email:
        fields["email"] = email.group(0)
    name = re.search(r"\\(?:textbf|Huge|LARGE|Large)\s*\{([^}]+)\}", raw)
    if name:
        fields["name"] = name.group(1).strip()
    return Section(
        section_id="personal_info",
        section_type=SectionType.PERSONAL_INFO,
        is_locked=True,
        lock_reason="personal_info_section",
        display_name=_DISPLAY_NAME[SectionType.PERSONAL_INFO],
        fields=fields,
    )


def _build_section(
    *,
    tex: str,
    title: str,
    section_type: SectionType,
    is_uncertain: bool,
    body_start: int,
    body_stop: int,
    section_ordinal: int,
    stmt_index: dict[str, StmtSpan],
) -> Section:
    """Build one section, populating statements/entries/skill_lines and the index."""
    is_locked = section_type not in {
        SectionType.SUMMARY,
        SectionType.WORK_EXPERIENCE,
        SectionType.PROJECTS,
        SectionType.SKILLS,
    }
    key = _SECTION_KEY[section_type]
    section_id = key if section_type != SectionType.UNKNOWN else f"unknown_{section_ordinal}"
    display = title or _DISPLAY_NAME[section_type]

    section = Section(
        section_id=section_id,
        section_type=section_type,
        is_locked=is_locked,
        lock_reason=f"{section_type.value}_section" if is_locked else None,
        display_name=display,
        latex_section_command=f"\\section{{{title}}}",
        classification_uncertain=is_uncertain,
    )

    if is_locked:
        return section  # Locked sections are never indexed for editing.

    blocks = _find_list_environments(tex, body_start, body_stop)

    if section_type == SectionType.SUMMARY:
        _populate_text_or_items(
            tex, body_start, body_stop, blocks, section, key, stmt_index, as_statements=True
        )
    elif section_type == SectionType.SKILLS:
        _populate_skills(tex, body_start, body_stop, blocks, section, key, stmt_index)
    else:  # WORK_EXPERIENCE or PROJECTS
        _populate_entries(tex, body_start, body_stop, blocks, section, key, stmt_index)

    return section


def _populate_text_or_items(
    tex: str,
    body_start: int,
    body_stop: int,
    blocks: list[tuple[int, int, int]],
    section: Section,
    key: str,
    stmt_index: dict[str, StmtSpan],
    *,
    as_statements: bool,
) -> None:
    """Populate a text section (summary): one statement, or items if itemized."""
    if not blocks:
        s, e = _text_span_excluding_lists(tex, body_start, body_stop, blocks)
        if s >= e:
            return
        stmt_id = f"{key}_0"
        text = tex[s:e]
        section.statements.append(
            Statement(stmt_id=stmt_id, text=text, tex_command="", word_count=word_count(text))
        )
        stmt_index[stmt_id] = StmtSpan(
            tex_start=s, tex_end=e, item_command="", original_text=text
        )
        return
    counter = 0
    for content_start, content_end, _ in blocks:
        for s, e, text in _parse_items(tex, content_start, content_end):
            stmt_id = f"{key}_{counter}"
            section.statements.append(
                Statement(stmt_id=stmt_id, text=text, word_count=word_count(text))
            )
            stmt_index[stmt_id] = StmtSpan(
                tex_start=s, tex_end=e, item_command="\\item", original_text=text
            )
            counter += 1


def _populate_skills(
    tex: str,
    body_start: int,
    body_stop: int,
    blocks: list[tuple[int, int, int]],
    section: Section,
    key: str,
    stmt_index: dict[str, StmtSpan],
) -> None:
    """Populate the skills section as one or more skill lines."""
    if not blocks:
        s, e = _text_span_excluding_lists(tex, body_start, body_stop, blocks)
        if s >= e:
            return
        stmt_id = f"{key}_0"
        text = tex[s:e]
        section.skill_lines.append(SkillLine(stmt_id=stmt_id, text=text))
        stmt_index[stmt_id] = StmtSpan(tex_start=s, tex_end=e, original_text=text)
        return
    counter = 0
    for content_start, content_end, _ in blocks:
        for s, e, text in _parse_items(tex, content_start, content_end):
            stmt_id = f"{key}_{counter}"
            section.skill_lines.append(SkillLine(stmt_id=stmt_id, text=text))
            stmt_index[stmt_id] = StmtSpan(
                tex_start=s, tex_end=e, item_command="\\item", original_text=text
            )
            counter += 1


def _populate_entries(
    tex: str,
    body_start: int,
    body_stop: int,
    blocks: list[tuple[int, int, int]],
    section: Section,
    key: str,
    stmt_index: dict[str, StmtSpan],
) -> None:
    """Populate experience/projects: one entry per list environment block."""
    if not blocks:
        # Text-only section: treat as a single entry with one statement.
        s, e = _trim_span(tex, body_start, body_stop)
        if s >= e:
            return
        stmt_id = f"{key}_0_0"
        text = tex[s:e]
        entry = Entry(entry_id=f"{key}_0")
        entry.statements.append(Statement(stmt_id=stmt_id, text=text, word_count=word_count(text)))
        section.entries.append(entry)
        stmt_index[stmt_id] = StmtSpan(tex_start=s, tex_end=e, original_text=text)
        return

    prev_end = body_start
    for entry_idx, (content_start, content_end, block_end) in enumerate(blocks):
        # The header is the text between the previous block's \end and this \begin.
        begin_token = tex.rfind("\\begin", prev_end, content_start)
        header_stop = begin_token if begin_token != -1 else content_start
        hs, he = _trim_span(tex, prev_end, header_stop)
        header_text = tex[hs:he]
        entry = Entry(entry_id=f"{key}_{entry_idx}", header_text=header_text)
        for bullet_idx, (s, e, text) in enumerate(_parse_items(tex, content_start, content_end)):
            stmt_id = f"{key}_{entry_idx}_{bullet_idx}"
            entry.statements.append(
                Statement(stmt_id=stmt_id, text=text, word_count=word_count(text))
            )
            stmt_index[stmt_id] = StmtSpan(
                tex_start=s, tex_end=e, item_command="\\item", original_text=text
            )
        section.entries.append(entry)
        prev_end = block_end


def _text_span_excluding_lists(
    tex: str, body_start: int, body_stop: int, blocks: list[tuple[int, int, int]]
) -> tuple[int, int]:
    """Trimmed span of the section body (used for non-itemized text sections)."""
    return _trim_span(tex, body_start, body_stop)


def _estimate_budget(sections: list[Section]) -> PageBudget:
    """Estimate word/bullet counts across editable sections."""
    words = 0
    bullets = 0
    for section in sections:
        if section.is_locked:
            continue
        for stmt in section.statements:
            words += stmt.word_count
            bullets += 1
        for entry in section.entries:
            for stmt in entry.statements:
                words += stmt.word_count
                bullets += 1
        for line in section.skill_lines:
            words += word_count(line.text)
    return PageBudget(estimated_word_count=words, estimated_bullet_count=bullets)
