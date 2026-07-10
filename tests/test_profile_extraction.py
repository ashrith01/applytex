from pathlib import Path

from latex_resume.job_models import CandidateProfile
from latex_resume.profile_extraction import (
    education_profile_from_extracted_item,
    extract_profile_facts_from_text,
    extract_profile_facts_from_tex,
    profile_with_resume_prefill,
)


PDF_TEXT = """
ASHRITH VADDE
+1-(713)480-6317 ashrith.vadde@gmail.com https://www.linkedin.com/in/ashrith-v https://ashrith01.github.io/
Professional Summary
AI/ML Engineer with 2+ years building LLM and RAG-based applications.
Education
Master of Science in Engineering Data Science and AI CGPA: 4/4
University of Houston - Houston, TX August 2025 - May 2027
B.Tech in Computer Science and Engineering (Artificial Intelligence) CGPA: 8.43/10
Amrita School of Engineering - Bengaluru, India June 2019 - May 2023
Experience
Accenture Nov 2023 - Aug 2025
AI/ML Computational Science Analyst Hyderabad, India
• Engineered LLM-powered code transformation features for the GenLite reverse engineering platform.
• Built and delivered 3+ end-to-end RAG pipelines and AI chatbot prototypes for enterprise clients.
Samsung Prism Sep 2021 - Apr 2022
Project Intern
• Researched model interpretability with a team of 6 using SHAP, CEM, and LIME.
• Architected a modular XAI framework supporting image, text, and tabular ML models.
Skills
Languages: Python, Java, SQL
AI/ML: Machine Learning, Deep Learning, NLP, LLMs
"""


def test_profile_prefill_extracts_all_tex_education_and_work_entries() -> None:
    tex = Path("samples/resume.tex").read_bytes()

    profile, applied = profile_with_resume_prefill(
        CandidateProfile(),
        filename="resume.tex",
        data=tex,
        overwrite=True,
    )

    assert "educations" in applied
    assert "work_experiences" in applied
    assert [education.school for education in profile.educations] == [
        "University of Houston",
        "Amrita School of Engineering",
    ]
    assert profile.educations[0].start_date == "2025-08"
    assert profile.educations[0].end_date == "2027-05"
    assert profile.educations[1].start_date == "2019-06"
    assert profile.educations[1].end_date == "2023-05"
    assert profile.github_url == ""
    assert profile.portfolio_url == "https://ashrith01.github.io/"
    assert "Python" in profile.skills
    assert all(not skill.startswith(":") for skill in profile.skills)
    assert [work.company for work in profile.work_experiences] == ["Accenture", "Samsung Prism"]
    assert profile.work_experiences[0].job_title == "AI/ML Computational Science Analyst"
    assert profile.work_experiences[0].location == "Hyderabad, India"
    assert len(profile.work_experiences[0].bullets) == 3
    assert profile.work_experiences[1].job_title == "Project Intern"
    assert len(profile.work_experiences[1].bullets) == 2


def test_simple_tex_education_maps_school_degree_and_dates() -> None:
    tex = Path("samples/sample_resume.tex").read_text(encoding="utf-8")

    facts = extract_profile_facts_from_tex(tex)

    assert facts["full_name"] == "Jane Doe"
    assert facts["email"] == "jane.doe@example.com"
    assert facts["linkedin_url"] == "https://linkedin.com/in/janedoe"
    assert facts["github_url"] == "https://github.com/janedoe"
    education = facts["educations"][0]
    assert education.school == "Massachusetts Institute of Technology"
    assert education.degree == "B.S. in Computer Science"
    assert education.major == "Computer Science"
    assert education.start_date == "2015"
    assert education.end_date == "2019"
    assert len(facts["work_experiences"][0].bullets) == 3


def test_profile_prefill_corrects_swapped_school_and_degree_fields() -> None:
    education = education_profile_from_extracted_item(
        {
            "institution": "M.S. in Engineering Data Science & Artificial Intelligence",
            "degree": "University of Houston",
            "start_date": "Aug 2025",
            "end_date": "May 2027",
            "gpa": "4.0/4.0",
        }
    )

    assert education.school == "University of Houston"
    assert education.degree == "M.S. in Engineering Data Science & Artificial Intelligence"
    assert education.gpa == "4.0/4.0"
    assert education.start_date == "2025-08"
    assert education.end_date == "2027-05"


def test_tex_education_metadata_mapping_handles_school_first_macro_order() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Education}
\resumeSubheading
  {Amrita School of Engineering - Bengaluru, India}
  {June 2019 -- May 2023}
  {B.Tech in Computer Science and Engineering (Artificial Intelligence); CGPA: 8.43/10}
  {Bengaluru, India}
\end{document}
"""

    facts = extract_profile_facts_from_tex(tex)

    education = facts["educations"][0]
    assert education.school == "Amrita School of Engineering"
    assert education.degree == "B.Tech in Computer Science and Engineering (Artificial Intelligence)"
    assert education.major == "Computer Science and Engineering (Artificial Intelligence)"
    assert education.gpa == "8.43/10"
    assert education.start_date == "2019-06"
    assert education.end_date == "2023-05"


def test_tex_work_metadata_mapping_corrects_swapped_company_and_title() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading
  {AI/ML Engineer}
  {Nov 2023 -- Aug 2025}
  {Accenture -- GenLite (Internal Enterprise AI Platform for Client Delivery)}
  {Hyderabad, India}
\begin{itemize}
  \item Built and delivered production RAG systems for enterprise clients.
  \item Automated code transformation workflows across programming languages.
\end{itemize}
\end{document}
"""

    facts = extract_profile_facts_from_tex(tex)

    work = facts["work_experiences"][0]
    assert work.company == "Accenture"
    assert work.job_title == "AI/ML Engineer"
    assert work.location == "Hyderabad, India"
    assert work.start_date == "2023-11"
    assert work.end_date == "2025-08"
    assert work.bullets == [
        "Built and delivered production RAG systems for enterprise clients.",
        "Automated code transformation workflows across programming languages.",
    ]


def test_split_skill_tokens_respects_parentheses() -> None:
    from latex_resume.profile_extraction import split_skill_tokens

    assert split_skill_tokens("LLM Evaluation (RAGAS, DeepEval), Python") == [
        "LLM Evaluation (RAGAS, DeepEval)",
        "Python",
    ]


def test_pdf_text_extraction_keeps_education_and_work_sections_separate() -> None:
    facts = extract_profile_facts_from_text(PDF_TEXT)

    educations = facts["educations"]
    work = facts["work_experiences"]
    assert [education.school for education in educations] == [
        "University of Houston",
        "Amrita School of Engineering",
    ]
    assert educations[0].degree == "Master of Science in Engineering Data Science and AI"
    assert educations[0].gpa == "4/4"
    assert educations[0].start_date == "2025-08"
    assert educations[1].end_date == "2023-05"
    assert [entry.company for entry in work] == ["Accenture", "Samsung Prism"]
    assert work[0].summary.startswith("Engineered LLM-powered code transformation")
    assert work[1].job_type == "Internship"
    assert "Python" in facts["skills"]
