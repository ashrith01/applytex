"""Full structured extraction from a ``.tex`` resume.

Produces a clean, human-readable JSON with *every* section — personal info,
summary, education, work experience, skills, projects, publications,
certifications — with all LaTeX commands stripped to plain text.

This is the "read" view of the resume, separate from the splice-index produced
by the parser (which preserves raw LaTeX for reconstruction).  Neither module
mutates the source.

Usage::

    from latex_resume.parser import parse
    from latex_resume.extractor import extract_full_resume

    pr = parse(tex, resume_id="demo")
    data = extract_full_resume(pr)
    print(json.dumps(data, indent=2))
"""

from __future__ import annotations

import re

from latex_resume.models import ParseResult, SectionType


# ---------------------------------------------------------------------------
# LaTeX → plain text
# ---------------------------------------------------------------------------

_FORMATTING_RE = re.compile(
    r"\\(?:"
    r"textbf|textit|textrm|texttt|textsc|textmd|textnormal|textup"
    r"|underline|emph|uline"
    r"|small|large|Large|LARGE|huge|Huge|normalsize|footnotesize|scriptsize"
    r"|bfseries|itshape|scshape|mdseries|upshape|normalfont"
    r"|sffamily|ttfamily|rmfamily"
    r")\s*\{"
)


def _delatex(text: str) -> str:
    """Strip LaTeX formatting commands and return readable plain text.

    Applies up to 8 iterative passes so nested commands like
    ``\\textbf{\\underline{foo}}`` are fully unwrapped.
    """
    # 1. Handle \raisebox{dim}[opt]{content} or bare \raisebox{dim}\ first.
    text = re.sub(
        r"\\raisebox\s*\{[^{}]*\}\s*(?:\[[^\]]*\])?\s*(?:\{([^{}]*)\})?",
        lambda m: (m.group(1) or ""),
        text,
    )

    for _ in range(8):
        prev = text

        # Inline formatting: \cmd{ → {  (strips the command, keeps the brace group)
        text = _FORMATTING_RE.sub("{", text)

        # \href{url}{display} → display
        text = re.sub(r"\\href\s*\{[^{}]*\}\s*\{", "{", text)

        # \url{url} → url
        text = re.sub(r"\\url\s*\{([^{}]*)\}", r"\1", text)

        # Spacing commands → remove
        text = re.sub(r"\\[vh]space\s*\*?\s*\{[^{}]*\}", "", text)

        # Bare braced groups {simple content} → content
        # [^{}]* ensures we only match innermost groups; outer unwrap next pass
        text = re.sub(r"(?<!\\)\{([^{}]*)\}", r"\1", text)

        if text == prev:
            break

    # Special LaTeX characters
    text = (
        text.replace("\\&", " & ")
        .replace("\\%", "%")
        .replace("\\$", "$")
        .replace("\\_", "_")
        .replace("\\#", "#")
    )
    # Dashes: before the catch-all \cmd removal so we don't mangle them
    text = text.replace("---", "—").replace("--", "–")
    # Line breaks / spacing
    text = text.replace("\\\\", " ").replace("\\ ", " ").replace("~", " ")
    # Math: $content$ → content (with | preserved)
    text = re.sub(r"\$([^$]*)\$", lambda m: m.group(1).replace("|", " | "), text)
    # Remove all remaining backslash commands
    text = re.sub(r"\\[a-zA-Z@]+\*?\s*", " ", text)
    # Remove stray braces
    text = text.replace("{", "").replace("}", "")
    # Normalise whitespace
    text = " ".join(text.split())
    return text.strip()


# ---------------------------------------------------------------------------
# Brace-balanced reading helpers (local, not imported from parser)
# ---------------------------------------------------------------------------


def _read_braced(tex: str, open_idx: int) -> tuple[str, int]:
    """Read a brace-balanced group at ``tex[open_idx] == '{'``.

    Returns ``(inner_content, after_close_index)``.
    """
    depth, i, n = 0, open_idx, len(tex)
    while i < n:
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
            if depth == 0:
                return tex[open_idx + 1 : i], i + 1
        i += 1
    return tex[open_idx + 1 :], n


def _read_n_args(tex: str, cursor: int, n: int, stop: int) -> tuple[list[str], int]:
    """Read *n* consecutive brace-balanced arguments from ``tex[cursor:stop]``."""
    args: list[str] = []
    for _ in range(n):
        while cursor < stop and tex[cursor].isspace():
            cursor += 1
        if cursor < stop and tex[cursor] == "{":
            content, cursor = _read_braced(tex, cursor)
            args.append(content)
        else:
            break
    return args, cursor


def _find_cmd_args(
    tex: str, cmd: str, n_args: int, start: int, stop: int
) -> list[list[str]]:
    """Find all occurrences of ``\\cmd`` in ``tex[start:stop]`` and read *n_args* each."""
    results: list[list[str]] = []
    pattern = re.compile(re.escape(cmd) + r"(?![a-zA-Z])")
    for m in pattern.finditer(tex, start, stop):
        args, _ = _read_n_args(tex, m.end(), n_args, stop)
        if len(args) == n_args:
            results.append(args)
    return results


# ---------------------------------------------------------------------------
# Section boundary helpers
# ---------------------------------------------------------------------------

_SECTION_HEADING_RE = re.compile(r"\\section\*?\s*\{")


def _document_body_span(tex: str) -> tuple[int, int]:
    begin = tex.find("\\begin{document}")
    end = tex.find("\\end{document}")
    start = begin + len("\\begin{document}") if begin != -1 else 0
    stop = end if end != -1 else len(tex)
    return start, stop


def _is_commented_out(tex: str, pos: int) -> bool:
    """Return True if *pos* is on a line that starts with ``%``."""
    line_start = tex.rfind("\n", 0, pos)
    line_start = line_start + 1 if line_start != -1 else 0
    return tex[line_start:pos].lstrip().startswith("%")


def _section_spans(tex: str) -> list[tuple[str, int, int]]:
    """Return ``[(title, body_start, body_end), …]`` for every active ``\\section`` in *tex*.

    Skips ``\\section`` commands that appear on commented-out lines (``%``).
    """
    body_start, body_stop = _document_body_span(tex)
    headers: list[tuple[int, int, str]] = []
    for m in _SECTION_HEADING_RE.finditer(tex, body_start, body_stop):
        if _is_commented_out(tex, m.start()):
            continue
        content, after = _read_braced(tex, m.end() - 1)
        headers.append((m.start(), after, content.strip()))

    spans: list[tuple[str, int, int]] = []
    for i, (_, hbody_start, title) in enumerate(headers):
        sec_stop = headers[i + 1][0] if i + 1 < len(headers) else body_stop
        spans.append((title, hbody_start, sec_stop))
    return spans


def _find_section_body(
    section_spans: list[tuple[str, int, int]], section: object
) -> tuple[int, int] | None:
    """Match a parsed :class:`Section` to its raw body span."""
    display = section.display_name.lower()
    for title, s, e in section_spans:
        if title.lower() == display or display in title.lower():
            return s, e
    return None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _split_dates(dates: str) -> tuple[str, str]:
    """Split ``'start – end'`` into ``(start, end)``."""
    dates = dates.replace("–", "-").replace("—", "-")
    for sep in (" – ", " — ", " - ", "–", "—"):
        if sep in dates:
            parts = dates.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return dates.strip(), ""


_DATE_HINT_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\.?\s+\d{4}\b|\b(?:19|20)\d{2}\b|\bPresent\b",
    re.I,
)
_GPA_HINT_RE = re.compile(
    r"\b(?:CGPA|GPA)\s*:?\s*(?P<score>[0-9.]+\s*/\s*[0-9.]+|[0-9.]+)",
    re.I,
)
_SCHOOL_HINT_RE = re.compile(
    r"\b(?:university|college|institute|school(?:\s+of)?|academy|polytechnic)\b",
    re.I,
)
_DEGREE_HINT_RE = re.compile(
    r"\b(?:master|bachelor|b\.?\s?tech|m\.?\s?s\.?|b\.?\s?s\.?|ph\.?\s?d|"
    r"degree|computer science|data science|artificial intelligence|engineering)\b",
    re.I,
)


def _has_date_hint(value: str) -> bool:
    return bool(_DATE_HINT_RE.search(value))


def _clean_gpa_hint(value: str) -> str:
    match = _GPA_HINT_RE.search(value)
    return match.group("score").strip() if match else value.strip()


def _remove_gpa_hint(value: str) -> str:
    return _GPA_HINT_RE.sub("", value).strip(" ,;")


def _looks_like_school(value: str) -> bool:
    return bool(_SCHOOL_HINT_RE.search(value))


def _looks_like_degree(value: str) -> bool:
    return bool(_DEGREE_HINT_RE.search(value)) and not _looks_like_school(value)


def _split_institution_location(value: str) -> tuple[str, str]:
    for sep in (" - ", " – ", " — "):
        if sep in value:
            institution, location = value.split(sep, 1)
            return institution.strip(), location.strip()
    if ", " in value and not _looks_like_school(value.rsplit(", ", 1)[-1]):
        institution, location = value.rsplit(", ", 1)
        return institution.strip(), location.strip()
    return value.strip(), ""


def _education_entry_from_args(args: list[str]) -> dict:
    """Classify education macro args by meaning instead of relying on fixed order."""
    values = [_delatex(arg) for arg in args]
    used_indexes: set[int] = set()
    dates = ""
    gpa = ""

    for index, value in enumerate(values):
        if _has_date_hint(value) and ("-" in value or "–" in value or "—" in value or value.casefold() == "present"):
            dates = value
            used_indexes.add(index)
            break

    for index, value in enumerate(values):
        if index in used_indexes:
            continue
        if _GPA_HINT_RE.search(value):
            gpa = _clean_gpa_hint(value)
            values[index] = _remove_gpa_hint(value)
            if not values[index]:
                used_indexes.add(index)
            break

    remaining = [(index, value) for index, value in enumerate(values) if index not in used_indexes and value]
    degree = next((value for _, value in remaining if _looks_like_degree(value)), "")
    institution = next((value for _, value in remaining if _looks_like_school(value)), "")

    if not degree:
        degree = next((value for _, value in remaining if value != institution), "")
    if not institution:
        institution = next((value for _, value in remaining if value != degree), "")

    institution, location = _split_institution_location(institution)
    start_date, end_date = _split_dates(dates) if dates else ("", "")

    entry: dict = {}
    if degree:      entry["degree"] = degree
    if institution: entry["institution"] = institution
    if location:    entry["location"] = location
    if start_date:  entry["start_date"] = start_date
    if end_date:    entry["end_date"] = end_date
    if gpa:         entry["gpa"] = gpa
    return entry


# ---------------------------------------------------------------------------
# Personal information
# ---------------------------------------------------------------------------


def _extract_personal_info(tex: str, header_start: int, header_end: int) -> dict:
    """Parse name, email, phone, and social links from the pre-section header block."""
    block = tex[header_start:header_end]

    # Name — typically {\Huge \scshape NAME} or \textbf{Jane Doe}
    name = ""
    for pat in (
        r"\{\\Huge\s+\\scshape\s+([^{}\\]+)\}",
        r"\\Huge\s+\\scshape\s+([A-Z][A-Z\s]+)",
        r"\\(?:Large|LARGE|Huge|HUGE)\s*\\(?:textbf|textbf|scshape)\s*\{([^{}]+)\}",
        r"\\LARGE\s*\{([^{}]+)\}",
        r"\\textbf\s*\{([^{}]+)\}",
    ):
        m = re.search(pat, block)
        if m:
            candidate = " ".join(m.group(1).split())
            if candidate and "@" not in candidate and "http" not in candidate.casefold():
                name = candidate
                break

    # Email — prefer mailto: href, fall back to bare address
    email = ""
    m = re.search(r"mailto:([\w.+\-]+@[\w.\-]+\.\w+)", block)
    if m:
        email = m.group(1)
    else:
        m = re.search(r"[\w.+\-]+@[\w.\-]+\.\w+", block)
        if m:
            email = m.group(0)

    # Phone — digits with separators, not an email/URL
    phone = ""
    for pat in (
        r"(?<![/@\w])(\+?\d[\d\s\-\(\)\.]{6,}\d)(?!\w)",
        r"(\+?[\d][\d\s\-\(\)\.]{7,})",
    ):
        m = re.search(pat, block)
        if m:
            candidate = " ".join(m.group(1).split())
            if "@" not in candidate and "http" not in candidate:
                phone = candidate
                break

    # URLs from all \href{url} occurrences
    all_urls: list[str] = re.findall(r"\\href\s*\{([^{}]+)\}", block)
    linkedin = next((u for u in all_urls if "linkedin.com" in u), "")
    github = next(
        (u for u in all_urls if "github.com" in u and "mailto" not in u), ""
    )
    other_sites = [
        u
        for u in all_urls
        if u != linkedin
        and u != github
        and "mailto:" not in u.casefold()
        and not u.casefold().startswith("tel:")
    ]
    website = other_sites[0] if other_sites else ""

    plain = _delatex(block)
    if not linkedin:
        linkedin_match = re.search(
            r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+",
            plain,
            flags=re.I,
        )
        if linkedin_match:
            linkedin = linkedin_match.group(0)
    if not github:
        github_match = re.search(
            r"(?:https?://)?(?:www\.)?github\.com/[\w\-]+",
            plain,
            flags=re.I,
        )
        if github_match:
            github = github_match.group(0)
    if not website:
        site_match = re.search(r"https?://[^\s)\]}>,]+", plain)
        if site_match:
            candidate = site_match.group(0)
            if candidate not in {linkedin, github} and "mailto:" not in candidate:
                website = candidate

    info: dict = {}
    if name:     info["name"] = name
    if email:    info["email"] = email
    if phone:    info["phone"] = phone
    if linkedin: info["linkedin"] = linkedin
    if github:   info["github"] = github
    if website:  info["website"] = website
    return info


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------


def _extract_education(tex: str, body_start: int, body_end: int) -> list[dict]:
    """Parse ``\\resumeSubheading`` entries from the education section body."""
    entries: list[dict] = []
    for args in _find_cmd_args(tex, "\\resumeSubheading", 4, body_start, body_end):
        entry = _education_entry_from_args(args)
        entries.append(entry)
    return entries or _extract_plain_education(tex[body_start:body_end])


def _extract_plain_education(section_tex: str) -> list[dict]:
    """Parse simple education lines such as ``\\textbf{B.S. CS}, University \\hfill 2021``."""
    entries: list[dict] = []
    body_no_comments = re.sub(r"%[^\n]*", "", section_tex)
    date_tail_re = re.compile(
        r"(?P<body>.+?)\s+(?P<dates>"
        r"(?:[A-Za-z]{3,9}\.?\s+)?\d{4}\s*(?:--|–|—|-)\s*"
        r"(?:Present|(?:[A-Za-z]{3,9}\.?\s+)?\d{4})"
        r"|\d{4})$",
        re.I,
    )

    for raw_line in body_no_comments.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("\\begin") or raw_line.lstrip().startswith("\\end"):
            continue
        cleaned = _delatex(raw_line.replace("\\hfill", " "))
        cleaned = re.sub(r"^\s*[-•]\s*", "", cleaned).strip(" ,;")
        if not cleaned:
            continue

        dates = ""
        date_match = date_tail_re.match(cleaned)
        if date_match:
            cleaned = date_match.group("body").strip(" ,;")
            dates = date_match.group("dates").strip()
        start_date, end_date = _split_dates(dates) if dates else ("", "")

        gpa = ""
        gpa_match = re.search(r"\b(?:CGPA|GPA)\s*:?\s*([0-9.]+\s*/\s*[0-9.]+|[0-9.]+)", cleaned, re.I)
        if gpa_match:
            gpa = gpa_match.group(1).strip()
            cleaned = (cleaned[: gpa_match.start()] + cleaned[gpa_match.end() :]).strip(" ,;")

        degree = ""
        institution = ""
        if "," in cleaned:
            degree, institution = [part.strip() for part in cleaned.split(",", 1)]
        elif " - " in cleaned:
            degree, institution = [part.strip() for part in cleaned.split(" - ", 1)]
        else:
            degree = cleaned

        entry: dict = {}
        if degree:      entry["degree"] = degree
        if institution: entry["institution"] = institution
        if start_date:  entry["start_date"] = start_date
        if end_date:    entry["end_date"] = end_date
        if gpa:         entry["gpa"] = gpa
        if entry:
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------


def _extract_certifications(tex: str, body_start: int, body_end: int) -> list[str]:
    """Extract certification names from ``\\item`` lines in the section body.

    Strips inline ``%`` comments so that items followed by commented lines
    don't bleed comment characters into the output.
    """
    certs: list[str] = []
    body = tex[body_start:body_end]
    # Remove LaTeX comment tails (% to end of line) before parsing
    body_no_comments = re.sub(r"%[^\n]*", "", body)
    for m in re.finditer(r"\\item\b(.+?)(?=\\item|\\end|\Z)", body_no_comments, re.DOTALL):
        text = _delatex(m.group(1)).strip()
        if text:
            certs.append(text)
    return certs


# ---------------------------------------------------------------------------
# Work experience / Projects / Publications entry headers
# ---------------------------------------------------------------------------


def _parse_subheading_header(header_tex: str) -> dict:
    """Parse ``\\resumeSubheading{co}{dates}{role}{loc}`` into a dict."""
    matches = _find_cmd_args(header_tex, "\\resumeSubheading", 4, 0, len(header_tex))
    if not matches:
        return {"header": _delatex(header_tex)}
    a, b, c, d = matches[0]
    company = _delatex(a)
    dates = _delatex(b)
    role = _delatex(c)
    location = _delatex(d)
    start_date, end_date = _split_dates(dates)
    result: dict = {}
    if company:    result["company"] = company
    if role:       result["role"] = role
    if location:   result["location"] = location
    if start_date: result["start_date"] = start_date
    if end_date:   result["end_date"] = end_date
    return result


# Labels used as link display text — not real venue names
_LINK_LABELS: frozenset[str] = frozenset(
    {"source code", "code", "github", "results", "demo", "link", "paper", "pdf", "arxiv"}
)


def _parse_project_header(header_tex: str) -> dict:
    """Parse ``\\resumeProjectHeading{title}{date}`` into a dict.

    Collects all ``\\href`` URLs from the title field as ``urls``.
    Pipe-separated segments that are link labels ("Source Code", "Results",
    etc.) are moved to ``urls`` rather than treated as a venue.
    """
    matches = _find_cmd_args(header_tex, "\\resumeProjectHeading", 2, 0, len(header_tex))
    if not matches:
        return {"title": _delatex(header_tex)}
    title_raw, date_raw = matches[0]

    # Collect all hrefs from title before LaTeX stripping
    all_urls: list[str] = re.findall(r"\\href\s*\{([^{}]+)\}", title_raw)

    title_clean = _delatex(title_raw)
    date_clean = _delatex(date_raw)

    # Split on " | " separators
    segments = [s.strip() for s in title_clean.split(" | ")]
    title_part = segments[0]

    # Remaining segments: real venue vs link labels
    venue_parts: list[str] = []
    for seg in segments[1:]:
        seg_clean = seg.strip("()").strip()
        if seg_clean.lower() in _LINK_LABELS:
            pass  # it's a link label — URL already captured above
        elif seg_clean:
            venue_parts.append(seg_clean)

    result: dict = {"title": title_part}
    if venue_parts:
        result["venue"] = ", ".join(venue_parts)
    if date_clean:
        result["date"] = date_clean
    if all_urls:
        result["urls"] = all_urls
    return result


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


_SKILL_CAT_RE = re.compile(
    r"\\textbf\s*\{([^}]+)\}\s*\{:\s*([^}]+)\}",
    re.DOTALL,
)


def _clean_skill_raw(raw: str) -> str:
    raw = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("%"))
    raw = re.sub(r"\[[^\]]*\]", "", raw)
    raw = re.sub(r"\\[vh]space\s*\*?\s*\{[^{}]*\}", "", raw)
    raw = re.sub(r"\\small\s*\{?", "", raw)
    raw = raw.strip()
    if raw.endswith("}"):
        raw = raw[:-1].rstrip()
    return raw


def _parse_skills(skill_lines: list) -> dict | list[str]:
    """Return ``{category: items}`` dict, or flat list if no categories found."""
    result: dict[str, str] = {}
    for line in skill_lines:
        raw = _clean_skill_raw(line.text)
        m = _SKILL_CAT_RE.match(raw.strip())
        if m:
            category = _delatex(m.group(1)).rstrip(":")
            items = _delatex(m.group(2)).rstrip(",").strip()
            result[category] = items
        else:
            # Try \textbf{Cat}: items or \textbf{Cat:} items.
            m2 = re.match(r"\\textbf\s*\{([^}]+?)\s*:?\}\s*:?\s*(.+)", raw.strip(), re.DOTALL)
            if m2:
                result[_delatex(m2.group(1)).rstrip(":")] = _delatex(m2.group(2)).strip()
            else:
                clean = _delatex(raw).strip()
                if clean:
                    result[clean] = ""
    if all(v == "" for v in result.values()):
        return list(result.keys())
    return result


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_full_resume(parse_result: ParseResult) -> dict:
    """Extract all resume data into a clean, structured dict.

    Returns a dict with the following top-level keys (only those present in the
    resume are included):

    ``personal_info``, ``summary``, ``education``, ``work_experience``,
    ``skills``, ``projects``, ``publications``, ``certifications``.

    All string values are plain text — LaTeX commands, braces, and special
    sequences are stripped.
    """
    tex = parse_result.latex_source
    doc = parse_result.doc

    body_start, _ = _document_body_span(tex)
    spans = _section_spans(tex)

    # Header block is everything between \begin{document} and the first \section.
    first_section_pos = spans[0][1] if spans else len(tex)
    # Walk back to the \section{ command start
    first_sec_cmd = tex.rfind("\\section", body_start, first_section_pos)
    header_end = first_sec_cmd if first_sec_cmd != -1 else first_section_pos

    result: dict = {}

    # ------------------------------------------------------------------
    # Personal information  (pre-section header block)
    # ------------------------------------------------------------------
    result["personal_info"] = _extract_personal_info(tex, body_start, header_end)

    # ------------------------------------------------------------------
    # Walk every parsed section
    # ------------------------------------------------------------------
    for section in doc.sections:
        stype = section.section_type
        span = _find_section_body(spans, section)
        body_s, body_e = span if span else (0, 0)

        if stype == SectionType.PERSONAL_INFO:
            continue  # already handled

        elif stype == SectionType.SUMMARY:
            raw = " ".join(s.text for s in section.statements)
            result["summary"] = _delatex(raw)

        elif stype == SectionType.EDUCATION:
            result["education"] = _extract_education(tex, body_s, body_e)

        elif stype == SectionType.WORK_EXPERIENCE:
            jobs: list[dict] = []
            for entry in section.entries:
                job = _parse_subheading_header(entry.header_text)
                job["bullets"] = [_delatex(s.text) for s in entry.statements]
                jobs.append(job)
            result["work_experience"] = jobs

        elif stype == SectionType.SKILLS:
            result["skills"] = _parse_skills(section.skill_lines)

        elif stype == SectionType.PROJECTS:
            projects: list[dict] = []
            for entry in section.entries:
                proj = _parse_project_header(entry.header_text)
                proj["bullets"] = [_delatex(s.text) for s in entry.statements]
                projects.append(proj)
            result["projects"] = projects

        elif stype == SectionType.PUBLICATIONS:
            pubs: list[dict] = []
            for entry in section.entries:
                pub = _parse_project_header(entry.header_text)
                pub["bullets"] = [_delatex(s.text) for s in entry.statements]
                pubs.append(pub)
            result["publications"] = pubs

        elif stype == SectionType.CERTIFICATIONS:
            new_certs = _extract_certifications(tex, body_s, body_e)
            existing = result.get("certifications", [])
            combined = existing + new_certs
            # Deduplicate while preserving order (guards against commented-out
            # duplicate \section{Certifications} blocks in the source)
            result["certifications"] = list(dict.fromkeys(combined))

    return result
