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
    EDITABLE_SECTION_TYPES,
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
    (SectionType.PUBLICATIONS, ("publication", "paper", "research paper", "journal", "conference", "preprint")),
    (SectionType.EDUCATION, ("education", "academic", "degree", "university", "college", "school")),
    (SectionType.CERTIFICATIONS, ("certification", "certificate", "license", "licence", "credential", "award", "honor", "honour", "achievement")),
]

_LIST_ENVIRONMENTS: frozenset[str] = frozenset({"itemize", "enumerate", "cvitems", "highlights"})

# Custom resume-template item command: \resumeItem{text}  (argument is the bullet text)
_RESUME_ITEM_RE = re.compile(r"\\resumeItem\s*\{")

_SECTION_KEY: dict[SectionType, str] = {
    SectionType.WORK_EXPERIENCE: "work",
    SectionType.PROJECTS: "proj",
    SectionType.SKILLS: "skills",
    SectionType.SUMMARY: "summary",
    SectionType.PUBLICATIONS: "pub",
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
    SectionType.PUBLICATIONS: "Publications",
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


def _is_commented_out(tex: str, pos: int) -> bool:
    """Return True when ``pos`` is on a line whose first non-space char is ``%``."""
    line_start = tex.rfind("\n", 0, pos)
    line_start = line_start + 1 if line_start != -1 else 0
    return tex[line_start:pos].lstrip().startswith("%")


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

    Also recognises custom resume-template pseudo-environments:
    ``\\resumeItemListStart`` / ``\\resumeItemListEnd`` (which expand to
    ``\\begin{itemize}`` / ``\\end{itemize}`` in the preamble).  The outer wrapper
    ``\\resumeSubHeadingListStart`` is intentionally *not* tracked here so that each
    inner ``\\resumeItemListStart…End`` block surfaces as a separate entry.
    """
    env_alt = "|".join(re.escape(e) for e in _LIST_ENVIRONMENTS)
    # Matches standard \begin{env}/\end{env} and custom \resumeItemListStart/End.
    token_re = re.compile(
        r"\\begin\s*\{(?:" + env_alt + r")\}"
        r"|\\end\s*\{(?:" + env_alt + r")\}"
        r"|\\resumeItemListStart"
        r"|\\resumeItemListEnd"
    )
    blocks: list[tuple[int, int, int]] = []
    depth = 0
    open_content_start = 0
    for m in token_re.finditer(tex, start, stop):
        tok = m.group(0)
        is_begin = tok.startswith("\\begin") or tok == "\\resumeItemListStart"
        if is_begin:
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


_NOISE_LINE_RE = re.compile(r"^(?:%|\\(?:v|h|med|big|small)space\b)")
_TEXT_WRAPPER_RE = re.compile(
    r"\\(?:small|footnotesize|scriptsize|normalsize|large|Large|LARGE)\s*\{"
)


def _trim_trailing_noise(tex: str, s: int, e: int) -> tuple[int, int]:
    """Shrink ``[s, e)`` by dropping trailing comment and spacing-command lines.

    Removes lines from the end of a section body that carry no printable content:
    LaTeX comment lines (``%…``), pure spacing commands (``\\vspace``, ``\\hspace``,
    ``\\medskip``, etc.), and blank lines.  This prevents decorative section-separator
    comments such as ``%-----------EDUCATION-----------`` — which appear *before* the
    next ``\\section`` but logically belong to the next section — from being absorbed
    into the editable span of the preceding section.
    """
    while s < e:
        nl = tex.rfind("\n", s, e)
        if nl == -1:
            break  # single line — don't trim it
        last_line = tex[nl + 1 : e].strip()
        if not last_line or _NOISE_LINE_RE.match(last_line):
            e = nl
        else:
            break
    return _trim_span(tex, s, e)


def _unwrap_text_wrapper_span(tex: str, s: int, e: int) -> tuple[int, int]:
    """Return the inner content span for common text-size wrappers.

    Resume summaries often appear as ``\\small{...}``. The editable statement span
    must cover only the text inside the braces so reconstruction preserves the
    layout command and closing brace.
    """
    m = _TEXT_WRAPPER_RE.match(tex, s, e)
    if not m:
        return s, e
    _, after = _read_braced(tex, m.end() - 1)
    if after != e:
        return s, e
    return _trim_span(tex, m.end(), after - 1)


def _parse_items(tex: str, content_start: int, content_end: int) -> list[tuple[int, int, str]]:
    """Parse ``\\item`` and ``\\resumeItem{…}`` text spans within a list-environment range.

    Returns ``(text_start, text_end, text)`` tuples, one per item, where the span
    covers only the trimmed text content:
    * For ``\\item``: text after the command (and any ``[label]``) until the next marker.
    * For ``\\resumeItem{text}``: the content of the braced argument.
    """
    # Collect all item markers sorted by position.
    # Each entry: (start_pos, kind, brace_pos) where brace_pos is the '{' offset for
    # resumeItem markers (-1 for standard \item).
    markers: list[tuple[int, str, int]] = []
    for m in _ITEM_RE.finditer(tex, content_start, content_end):
        markers.append((m.start(), "item", -1))
    for m in _RESUME_ITEM_RE.finditer(tex, content_start, content_end):
        # m.end() - 1 is the position of the opening '{' matched by the regex.
        markers.append((m.start(), "resumeItem", m.end() - 1))
    markers.sort()

    spans: list[tuple[int, int, str]] = []
    for idx, (raw_start, kind, brace_pos) in enumerate(markers):
        if kind == "resumeItem":
            # Extract the braced argument; tex_start/end are inside the braces so
            # reconstruction leaves \resumeItem{…} structurally intact.
            _, after_brace = _read_braced(tex, brace_pos)
            s, e = _trim_span(tex, brace_pos + 1, after_brace - 1)
            if s < e:
                spans.append((s, e, tex[s:e]))
        else:  # standard \item
            cursor = raw_start + len("\\item")
            # Skip an optional [label] argument.
            probe = cursor
            while probe < content_end and tex[probe].isspace():
                probe += 1
            if probe < content_end and tex[probe] == "[":
                close = tex.find("]", probe)
                cursor = close + 1 if close != -1 else probe
            # Text runs until the next marker (of either kind) or end of environment.
            next_start = markers[idx + 1][0] if idx + 1 < len(markers) else content_end
            s, e = _trim_span(tex, cursor, next_start)
            if s < e:
                spans.append((s, e, tex[s:e]))
    return spans


def parse(tex: str, resume_id: str = "", source_type: str = "tex") -> ParseResult:
    """Parse a ``.tex`` resume into a :class:`ParseResult`."""
    latex_class = detect_class(tex)
    body_start, body_stop = _document_body_span(tex)

    # Locate all section headers in the document body.
    headers: list[tuple[int, int, str]] = []  # (header_start, body_start, title)
    for m in _SECTION_RE.finditer(tex, body_start, body_stop):
        if _is_commented_out(tex, m.start()):
            continue
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
    is_locked = section_type not in EDITABLE_SECTION_TYPES
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
    else:  # WORK_EXPERIENCE, PROJECTS
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
        # Strip trailing comment lines / spacing commands so the editable span
        # covers only printable content.  Section-separator comments like
        # ``%-----------EDUCATION-----------`` sit between sections in many
        # templates and must not become part of the summary's editable text.
        s, e = _trim_trailing_noise(tex, s, e)
        s, e = _unwrap_text_wrapper_span(tex, s, e)
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


def _has_readable_content(text: str) -> bool:
    """Return True if *text* contains something beyond whitespace and spacing commands.

    Used to skip trailing ``\\vspace{…}`` / ``\\hspace{…}`` fragments that appear
    after the last ``\\\\`` in a bare-text skills section.
    """
    stripped = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("%")
    ).strip()
    if not stripped:
        return False
    # Pure spacing commands and wrappers carry no editable content by themselves.
    readable = re.sub(r"\\[vh]space\s*\*?\s*\{[^{}]*\}", "", stripped)
    readable = re.sub(r"\\small\s*\{?", "", readable)
    readable = re.sub(r"\\(?:normalsize|footnotesize|scriptsize|large|Large)\b", "", readable)
    readable = readable.replace("{", "").replace("}", "").strip()
    if not re.search(r"[A-Za-z0-9]", readable):
        return False
    return True


def _trim_bare_skill_span(tex: str, start: int, stop: int) -> tuple[int, int]:
    """Trim comment/spacing tails from a bare-text skill line span."""
    text = tex[start:stop]
    cursor = start
    saw_readable_line = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if saw_readable_line and (
            stripped.startswith("%")
            or stripped.startswith("\\vspace")
            or stripped.startswith("\\hspace")
            or stripped == "}"
        ):
            return _trim_span(tex, start, cursor)
        if _has_readable_content(line):
            saw_readable_line = True
        cursor += len(line)
    return _trim_span(tex, start, stop)


def _populate_skills(
    tex: str,
    body_start: int,
    body_stop: int,
    blocks: list[tuple[int, int, int]],
    section: Section,
    key: str,
    stmt_index: dict[str, StmtSpan],
) -> None:
    """Populate the skills section as one or more skill lines.

    When no list environment is found the section uses bare ``\\\\``-separated
    lines (e.g. ``\\textbf{Languages}{: Python, …} \\\\``).  Each line becomes its
    own ``SkillLine`` so the LLM optimizer can target individual categories.
    """
    if not blocks:
        s, e = _text_span_excluding_lists(tex, body_start, body_stop, blocks)
        if s >= e:
            return
        # Split bare-text skills by \\ line-break separators.
        separator_re = re.compile(r"\\\\")
        separators = list(separator_re.finditer(tex, s, e))
        if separators:
            counter = 0
            prev = s
            for sep in separators:
                ls, le = _trim_span(tex, prev, sep.start())
                ls, le = _trim_bare_skill_span(tex, ls, le)
                if ls < le and _has_readable_content(tex[ls:le]):
                    stmt_id = f"{key}_{counter}"
                    text = tex[ls:le]
                    section.skill_lines.append(SkillLine(stmt_id=stmt_id, text=text))
                    stmt_index[stmt_id] = StmtSpan(tex_start=ls, tex_end=le, original_text=text)
                    counter += 1
                prev = sep.end()
            # Capture anything after the final \\ (skip pure spacing fragments).
            ls, le = _trim_span(tex, prev, e)
            ls, le = _trim_bare_skill_span(tex, ls, le)
            if ls < le and _has_readable_content(tex[ls:le]):
                stmt_id = f"{key}_{counter}"
                text = tex[ls:le]
                section.skill_lines.append(SkillLine(stmt_id=stmt_id, text=text))
                stmt_index[stmt_id] = StmtSpan(tex_start=ls, tex_end=le, original_text=text)
        else:
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
        # The header is the text between the previous block's end and this block's begin.
        # Try \begin first (standard templates); fall back to \resumeItemListStart
        # (custom-command templates where the macro call IS the opening marker).
        begin_token = tex.rfind("\\begin", prev_end, content_start)
        if begin_token == -1:
            begin_token = tex.rfind("\\resumeItemListStart", prev_end, content_start)
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
