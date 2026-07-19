from __future__ import annotations

from latex_resume.models import ParseResult, SectionType
from latex_resume.extractor import extract_full_resume
from latex_resume.parser import detect_class, parse


def _section(pr: ParseResult, section_type: SectionType):
    return next(s for s in pr.doc.sections if s.section_type == section_type)


def test_detect_class(sample_tex: str) -> None:
    assert detect_class(sample_tex) == "article"


def test_all_section_types_present(parsed: ParseResult) -> None:
    types = {s.section_type for s in parsed.doc.sections}
    assert {
        SectionType.PERSONAL_INFO,
        SectionType.SUMMARY,
        SectionType.WORK_EXPERIENCE,
        SectionType.PROJECTS,
        SectionType.SKILLS,
        SectionType.EDUCATION,
        SectionType.CERTIFICATIONS,
    } <= types


def test_locked_vs_editable_flags(parsed: ParseResult) -> None:
    assert _section(parsed, SectionType.PERSONAL_INFO).is_locked
    assert _section(parsed, SectionType.EDUCATION).is_locked
    assert _section(parsed, SectionType.CERTIFICATIONS).is_locked
    assert not _section(parsed, SectionType.SUMMARY).is_locked
    assert not _section(parsed, SectionType.WORK_EXPERIENCE).is_locked
    assert not _section(parsed, SectionType.PROJECTS).is_locked
    assert not _section(parsed, SectionType.SKILLS).is_locked


def test_summary_single_statement(parsed: ParseResult) -> None:
    summary = _section(parsed, SectionType.SUMMARY)
    assert len(summary.statements) == 1
    stmt = summary.statements[0]
    assert stmt.stmt_id == "summary_0"
    assert stmt.text.startswith("Software engineer with five years")


def test_work_experience_entries_and_ids(parsed: ParseResult) -> None:
    work = _section(parsed, SectionType.WORK_EXPERIENCE)
    assert len(work.entries) == 2
    assert [s.stmt_id for s in work.entries[0].statements] == ["work_0_0", "work_0_1", "work_0_2"]
    assert [s.stmt_id for s in work.entries[1].statements] == ["work_1_0", "work_1_1"]
    assert "Acme Corp" in work.entries[0].header_text
    assert "Startup Inc" in work.entries[1].header_text


def test_projects_ids(parsed: ParseResult) -> None:
    proj = _section(parsed, SectionType.PROJECTS)
    assert len(proj.entries) == 1
    assert [s.stmt_id for s in proj.entries[0].statements] == ["proj_0_0", "proj_0_1"]
    entry = proj.entries[0]
    assert entry.can_remove
    assert entry.tex_start is not None
    assert entry.tex_end is not None
    entry_source = parsed.latex_source[entry.tex_start : entry.tex_end]
    assert "ImageClassifier" in entry_source
    assert "\\begin{itemize}" in entry_source


def test_skills_lines(parsed: ParseResult) -> None:
    skills = _section(parsed, SectionType.SKILLS)
    assert [line.stmt_id for line in skills.skill_lines] == ["skills_0", "skills_1"]
    assert "Python" in skills.skill_lines[0].text


def test_skills_section_handles_small_wrapper_and_commented_tail() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Skills}
\small{
\textbf{LLMs \& Agentic AI:} OpenAI, Claude, LangChain, AutoGen \\[-1pt]
\textbf{ML / DL:} PyTorch, Hugging Face Transformers \\[-1pt]
\textbf{Languages:} Python, SQL
}
\vspace{-4mm}
%----------CERTIFICATIONS----------
% \section{Certifications}
% \textbf{AWS Certified Cloud Practitioner}
\end{document}
"""
    parsed = parse(tex)
    skills = _section(parsed, SectionType.SKILLS)
    extracted = extract_full_resume(parsed)["skills"]

    assert len(skills.skill_lines) == 3
    assert "LLMs" in skills.skill_lines[0].text
    assert "AWS Certified" not in " ".join(line.text for line in skills.skill_lines)
    assert extracted["LLMs & Agentic AI"] == "OpenAI, Claude, LangChain, AutoGen"
    assert extracted["ML / DL"] == "PyTorch, Hugging Face Transformers"


def test_locked_sections_not_indexed(parsed: ParseResult) -> None:
    for stmt_id in parsed.stmt_index:
        assert not stmt_id.startswith("edu_")
        assert not stmt_id.startswith("cert_")
        assert not stmt_id.startswith("pub_")
        assert not stmt_id.startswith("personal")


def test_spans_match_source_text(parsed: ParseResult) -> None:
    src = parsed.latex_source
    for stmt_id, span in parsed.stmt_index.items():
        sliced = src[span.tex_start : span.tex_end]
        assert sliced == span.original_text, f"span mismatch for {stmt_id}"


def test_editable_statements_excludes_locked(parsed: ParseResult) -> None:
    editable = parsed.doc.editable_statements()
    ids = {s.stmt_id for s in editable}
    # 1 summary + 5 work + 2 project + 2 skills = 10
    assert len(editable) == 10
    assert "summary_0" in ids
    assert "work_0_2" in ids
    assert not any(i.startswith("edu_") or i.startswith("cert_") for i in ids)


def test_commented_out_sections_are_ignored(sample_tex: str) -> None:
    tex = sample_tex.replace(
        "\\section{Experience}",
        "% \\section{Projects}\n"
        "% \\begin{itemize}\n"
        "%   \\item This old commented bullet must not be parsed.\n"
        "% \\end{itemize}\n"
        "\\section{Experience}",
        1,
    )
    pr = parse(tex)
    assert "This old commented bullet must not be parsed." not in {
        span.original_text for span in pr.stmt_index.values()
    }
    project_sections = [
        s for s in pr.doc.sections if s.section_type == SectionType.PROJECTS
    ]
    assert len(project_sections) == 1


def test_summary_span_preserves_text_size_wrapper() -> None:
    resume_tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI/ML Engineer building evidence-grounded systems.}
\section{Education}
B.S. Computer Science
\end{document}
"""
    pr = parse(resume_tex)
    span = pr.stmt_index["summary_0"]
    assert not span.original_text.startswith("\\small{")
    assert span.original_text.startswith("AI/ML Engineer")
    assert resume_tex[span.tex_start - len("\\small{") : span.tex_start] == "\\small{"
    assert resume_tex[span.tex_end] == "}"
