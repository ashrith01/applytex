"""Streamlit test UI for the LaTeX resume optimizer.

Deprecated: use the Next.js frontend in ``frontend/`` instead.

Run:

    uv run streamlit run src/latex_resume/streamlit_app.py
"""

from __future__ import annotations

import asyncio
import base64
import html
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import streamlit as st

from latex_resume.ats import check_ats
from latex_resume.application_store import ApplicationStore
from latex_resume.engine import extract_editable
from latex_resume.extractor import extract_full_resume
from latex_resume.form_resolution import profile_setup_status
from latex_resume.job_models import (
    CandidateProfile,
    EducationProfile,
    TargetRole,
    WorkExperienceProfile,
    utc_now,
)
from latex_resume.optimizer import (
    DEFAULT_OPTIMIZER_STRATEGY,
    _build_plain_text,
    apply_manual_statement_edits,
    extract_job_keywords_fast,
    extract_job_keywords_with_fallback,
    LLMTaskRoute,
    refine_resume_with_instruction,
    run_optimization_pipeline,
    split_skill_confirmation_candidates,
)
from latex_resume.parser import parse
from latex_resume.llm import backend_for_task, model_for_backend_task
from latex_resume.renderer import check_one_page, render_pdf
from latex_resume.run_analysis import (
    DEFAULT_RUN_LOG,
    append_run_record,
    build_run_record,
    load_run_records,
)
from latex_resume.screening import analyze_screening_fit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESUME = ROOT / "samples" / "sample_resume.tex"
DEFAULT_JD = ROOT / "samples" / "job_descriptions" / "airops_data_scientist.md"
DEFAULT_PROFILE_DB = Path(
    os.environ.get("APPLYTEX_DB_PATH")
    or os.environ.get("SMARTJOBAPPLY_DB_PATH")
    or ROOT / ".applytex" / "applytex.db"
)
PROFILE_STORE = ApplicationStore(DEFAULT_PROFILE_DB)
COMMON_CUSTOM_ANSWERS: tuple[tuple[str, str], ...] = (
    ("Preferred name", ""),
    ("Pronouns", ""),
    ("Earliest start date", ""),
    ("Desired salary", ""),
    ("Open to relocate", ""),
    ("How did you hear about us?", "Job board"),
    ("Cover letter", ""),
)
PROFILE_PREFILL_KEY = "profile_prefill_draft"
PROFILE_PREFILL_SUMMARY_KEY = "profile_prefill_summary"
MODEL_OPTIONS: dict[str, list[str]] = {
    "ollama": [
        "qwen3:4b",
        "qwen3:8b",
        "qwen2.5-coder:7b",
        "llama3.1:8b",
        "mistral:7b-instruct-q5_K_M",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "qwen/qwen3-32b",
        "openai/gpt-oss-20b",
    ],
    "codex": [
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.4",
    ],
}


def _bool_to_choice(value: bool | None) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Prefer not to answer"


def _choice_to_bool(value: str) -> bool | None:
    if value == "Yes":
        return True
    if value == "No":
        return False
    return None


def _option_index(options: list[str], value: str | None) -> int:
    if value in options:
        return options.index(value)
    return 0


def _role_label(role: TargetRole) -> str:
    return role.value.replace("_", " ").title()


def _profile_missing_required(profile: CandidateProfile) -> list[str]:
    return [
        str(item["label"])
        for item in profile_setup_status(profile)
        if item.get("required") and not item.get("value_present")
    ]


def _normalize_profile_username(username: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", username.strip().casefold())
    normalized = normalized.strip("._-")
    return normalized or "default"


def _render_username_gate() -> str | None:
    username = st.session_state.get("profile_username")
    if isinstance(username, str) and username:
        return username

    st.title("ApplyTeX ATS")
    st.subheader("Sign in to your application profile")
    st.caption("Use the same username each time to load the education, work experience, and application answers you saved before.")
    with st.form("profile_username_login"):
        entered = st.text_input("Username", placeholder="ashrith")
        submitted = st.form_submit_button("Continue", type="primary")
    if submitted:
        normalized = _normalize_profile_username(entered)
        st.session_state["profile_username"] = normalized
        st.session_state["profile_show_setup"] = True
        st.rerun()
    st.stop()


def _render_profile_identity(profile: CandidateProfile) -> None:
    label = profile.full_name or profile.profile_id
    initials = "".join(part[:1] for part in label.split()[:2]).upper() or "P"
    st.sidebar.markdown(
        f"""
        <style>
          .profile-identity {{
            display: flex;
            align-items: center;
            gap: 0.65rem;
            padding: 0.75rem 0;
            border-bottom: 1px solid #eee;
            margin-bottom: 0.75rem;
          }}
          .profile-avatar {{
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background: #0bdc91;
            color: #06130d;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 0.95rem;
          }}
          .profile-identity-name {{
            font-weight: 800;
            line-height: 1.1;
          }}
          .profile-identity-user {{
            color: #777;
            font-size: 0.85rem;
          }}
        </style>
        <div class="profile-identity">
          <div class="profile-avatar">{initials}</div>
          <div>
            <div class="profile-identity-name">{label}</div>
            <div class="profile-identity-user">@{profile.profile_id}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Profile", use_container_width=True):
        st.session_state["profile_show_setup"] = True
        st.rerun()
    if st.sidebar.button("Change username", use_container_width=True):
        st.session_state.pop("profile_username", None)
        st.session_state.pop(PROFILE_PREFILL_KEY, None)
        st.session_state.pop(PROFILE_PREFILL_SUMMARY_KEY, None)
        st.session_state.pop("profile_edit_section", None)
        st.session_state.pop("profile_show_setup", None)
        st.rerun()


def _profile_entries(profile: CandidateProfile) -> tuple[list[EducationProfile], list[WorkExperienceProfile]]:
    educations = profile.educations or ([profile.education] if profile.education.school else [])
    return educations, list(profile.work_experiences)


def _set_profile_edit_section(section: str) -> None:
    st.session_state["profile_edit_section"] = section
    st.rerun()


def _profile_badge(label: str, value: str) -> None:
    if value:
        st.markdown(f"**{label}:** {value}")


def _date_range(start: str, end: str, current: bool = False) -> str:
    if current:
        end = "Present"
    if start and end:
        return f"{start} -> {end}"
    return start or end or "Dates not provided"


def _render_personal_view(profile: CandidateProfile) -> None:
    edit_col, _ = st.columns([1, 9])
    if edit_col.button("Edit", key="edit_personal"):
        _set_profile_edit_section("personal")
    st.header(profile.full_name or "Your Name")
    fields = [
        ("Location", profile.location or ", ".join(part for part in [profile.address.city, profile.address.state] if part)),
        ("Email", profile.email),
        ("Phone", profile.phone),
        ("LinkedIn", profile.linkedin_url),
        ("Portfolio", profile.portfolio_url),
        ("GitHub", profile.github_url),
    ]
    for row in range(0, len(fields), 2):
        cols = st.columns(2)
        for col, (label, value) in zip(cols, fields[row: row + 2]):
            with col:
                _profile_badge(label, value)


def _render_education_view(profile: CandidateProfile) -> None:
    if st.button("Edit", key="edit_education"):
        _set_profile_edit_section("education")
    st.header("Education")
    educations, _ = _profile_entries(profile)
    if not educations:
        st.info("No education entries yet. Upload a resume or edit this section.")
        return
    for education in educations:
        st.caption(_date_range(education.start_date, education.end_date, education.currently_studying))
        st.markdown(f"### {education.school or 'School not provided'}")
        details = " ".join(part for part in [education.degree, education.major] if part)
        if details:
            st.write(details)
        if education.gpa:
            st.write(education.gpa)
        st.divider()


def _render_work_view(profile: CandidateProfile) -> None:
    if st.button("Edit", key="edit_work"):
        _set_profile_edit_section("work")
    st.header("Work Experience")
    _, work_entries = _profile_entries(profile)
    if not work_entries:
        st.info("No work experience entries yet. Upload a resume or edit this section.")
        return
    for work in work_entries:
        st.caption(_date_range(work.start_date, work.end_date, work.currently_working))
        title = work.job_title or "Role not provided"
        company = f" at {work.company}" if work.company else ""
        st.markdown(f"### {title}{company}")
        meta = " | ".join(part for part in [work.job_type, work.location] if part)
        if meta:
            st.write(meta)
        if work.summary:
            st.write(work.summary)
        for bullet in work.bullets:
            st.markdown(f"- {bullet}")
        st.divider()


def _render_skills_view(profile: CandidateProfile) -> None:
    if st.button("Edit", key="edit_skills"):
        _set_profile_edit_section("skills")
    st.header("Skills")
    if not profile.skills:
        st.info("No skills extracted yet. Upload a resume or edit this section.")
        return
    st.write(", ".join(profile.skills))


def _render_resume_view(profile: CandidateProfile) -> CandidateProfile | None:
    st.header("Profile Resume")
    if profile.resume_pdf_filename:
        st.write(f"Saved PDF: {profile.resume_pdf_filename}")
    elif profile.resume_filename:
        st.write(f"Saved resume: {profile.resume_filename}")
    else:
        st.info("Upload the resume you want applications to use.")

    details: list[str] = []
    if profile.resume_latex_source:
        details.append("Customizable LaTeX source saved")
    if profile.resume_pdf_b64:
        details.append("Upload-ready PDF saved")
    if profile.resume_updated_at:
        details.append(f"Updated {profile.resume_updated_at}")
    if details:
        st.caption(" | ".join(details))

    uploaded_resume = st.file_uploader(
        "Upload profile resume",
        type=["tex", "pdf"],
        key="profile_resume_upload",
        help="Upload .tex for job-specific customization, or PDF for direct application upload.",
    )
    if uploaded_resume is None:
        return None
    if not st.button("Save profile resume", type="primary", key="save_profile_resume"):
        return None

    raw = uploaded_resume.read()
    suffix = Path(uploaded_resume.name).suffix.casefold()
    updated = profile.model_copy(deep=True)
    updated.resume_filename = uploaded_resume.name
    updated.resume_updated_at = utc_now()
    if suffix == ".pdf":
        updated.resume_pdf_filename = uploaded_resume.name
        updated.resume_pdf_b64 = base64.b64encode(raw).decode()
    else:
        try:
            latex_source = raw.decode("utf-8")
            parse(latex_source, resume_id=Path(uploaded_resume.name).stem)
        except Exception as exc:
            st.error(f"Could not read this LaTeX resume: {exc}")
            return None
        updated.resume_latex_source = latex_source
        render = render_pdf(latex_source)
        if render.ok and render.pdf_bytes:
            updated.resume_pdf_filename = f"{Path(uploaded_resume.name).stem}.pdf"
            updated.resume_pdf_b64 = base64.b64encode(render.pdf_bytes).decode()
        else:
            st.warning("LaTeX source was saved, but no PDF could be rendered on this machine.")

    saved_profile = PROFILE_STORE.save_candidate_profile(updated)
    st.success("Profile resume saved.")
    return saved_profile


def _render_equal_employment_view(profile: CandidateProfile) -> None:
    if st.button("Edit", key="edit_eeo"):
        _set_profile_edit_section("eeo")
    st.header("Equal Employment")
    rows = [
        ("Are you authorized to work in the US?", _bool_to_choice(profile.work_authorization.authorized_to_work_in_us)),
        ("Do you require sponsorship for this role?", _bool_to_choice(profile.work_authorization.requires_sponsorship)),
        ("Do you have a disability?", profile.equal_opportunity.disability or ""),
        ("What is your gender?", profile.equal_opportunity.gender or ""),
        ("Are you a veteran?", profile.equal_opportunity.veteran_status or ""),
        ("How would you identify your race?", profile.equal_opportunity.race or ""),
        ("Are you Hispanic or Latino?", profile.equal_opportunity.hispanic_or_latino or ""),
        ("Do you identify as LGBTQ+?", profile.equal_opportunity.lgbtq or ""),
        ("How would you describe your sexual orientation?", profile.equal_opportunity.sexual_orientation or ""),
    ]
    for row in range(0, len(rows), 2):
        cols = st.columns(2)
        for col, (label, value) in zip(cols, rows[row: row + 2]):
            with col:
                _profile_badge(label, value or "Not provided")


def _render_preferences_view(profile: CandidateProfile) -> None:
    if st.button("Edit", key="edit_preferences"):
        _set_profile_edit_section("preferences")
    st.header("Application Preferences")
    st.write("Roles: " + ", ".join(_role_label(role) for role in profile.search_preferences.target_roles))
    st.write("Locations: " + ", ".join(profile.search_preferences.preferred_locations))
    st.write(
        "Workplace: "
        + ", ".join(
            label
            for label, enabled in [
                ("Remote US", profile.search_preferences.allow_remote_us),
                ("Hybrid", profile.search_preferences.allow_hybrid),
                ("Onsite", profile.search_preferences.allow_onsite),
            ]
            if enabled
        )
    )
    st.write("Employment types: " + ", ".join(profile.search_preferences.accepted_employment_types))


def _render_profile_nav() -> None:
    st.markdown(
        """
        <style>
          html { scroll-behavior: smooth; }
          .profile-nav {
            position: sticky;
            top: 0;
            z-index: 999;
            background: white;
            border-bottom: 1px solid #eee;
            padding: 0.75rem 0 0.5rem 0;
            margin-bottom: 1.5rem;
          }
          .profile-nav a {
            display: inline-block;
            margin-right: 1.5rem;
            padding: 0.35rem 0;
            color: #888;
            text-decoration: none;
            font-weight: 700;
          }
          .profile-nav a:hover,
          .profile-nav a.active,
          body:has(#profile-personal:target) .profile-nav a[href="#profile-personal"],
          body:has(#profile-education:target) .profile-nav a[href="#profile-education"],
          body:has(#profile-work:target) .profile-nav a[href="#profile-work"],
          body:has(#profile-skills:target) .profile-nav a[href="#profile-skills"],
          body:has(#profile-resume:target) .profile-nav a[href="#profile-resume"],
          body:has(#profile-eeo:target) .profile-nav a[href="#profile-eeo"],
          body:has(#profile-preferences:target) .profile-nav a[href="#profile-preferences"] {
            color: #111;
            border-bottom: 4px solid #111;
          }
          .profile-anchor {
            scroll-margin-top: 90px;
            padding-top: 0.25rem;
          }
        </style>
        <nav class="profile-nav" id="profile-nav">
          <a href="#profile-personal">Personal</a>
          <a href="#profile-education">Education</a>
          <a href="#profile-work">Work Experience</a>
          <a href="#profile-skills">Skills</a>
          <a href="#profile-resume">Resume</a>
          <a href="#profile-eeo">Equal Employment</a>
          <a href="#profile-preferences">Preferences</a>
        </nav>
        """,
        unsafe_allow_html=True,
    )

def _section_anchor(anchor_id: str) -> None:
    st.markdown(f'<div id="{anchor_id}" class="profile-anchor"></div>', unsafe_allow_html=True)


def _render_profile_editor(profile: CandidateProfile) -> CandidateProfile | None:
    section = st.session_state.get("profile_edit_section")
    if not section:
        return None
    updated = profile.model_copy(deep=True)
    with st.sidebar:
        st.subheader(f"Edit {str(section).replace('_', ' ').title()}")
        if st.button("Close editor", key="close_profile_editor"):
            st.session_state.pop("profile_edit_section", None)
            st.rerun()
        with st.form(f"profile_editor_{section}"):
            if section == "personal":
                updated.full_name = st.text_input("Full legal name", value=profile.full_name).strip()
                updated.first_name = st.text_input("First name", value=profile.first_name).strip()
                updated.last_name = st.text_input("Last name", value=profile.last_name).strip()
                updated.email = st.text_input("Email", value=profile.email).strip()
                updated.phone = st.text_input("Phone", value=profile.phone).strip()
                updated.location = st.text_input("Current location", value=profile.location).strip()
                updated.address.city = st.text_input("City", value=profile.address.city).strip()
                updated.address.state = st.text_input("State", value=profile.address.state).strip()
                updated.address.postal_code = st.text_input("ZIP / postal code", value=profile.address.postal_code).strip()
                updated.address.country = st.text_input("Country", value=profile.address.country).strip() or "United States"
                updated.linkedin_url = st.text_input("LinkedIn URL", value=profile.linkedin_url).strip()
                updated.portfolio_url = st.text_input("Portfolio URL", value=profile.portfolio_url).strip()
                updated.github_url = st.text_input("GitHub URL", value=profile.github_url).strip()
            elif section == "education":
                educations, _ = _profile_entries(profile)
                education_count = int(
                    st.number_input(
                        "Number of education entries",
                        min_value=1,
                        max_value=10,
                        value=max(1, len(educations)),
                        step=1,
                    )
                )
                educations = educations + [EducationProfile() for _ in range(max(0, education_count - len(educations)))]
                updated.educations = []
                for index, education in enumerate(educations[:education_count]):
                    st.markdown(f"**Education {index + 1}**")
                    updated.educations.append(
                        EducationProfile(
                            school=st.text_input("School Name", value=education.school, key=f"edit_edu_school_{index}").strip(),
                            major=st.text_input("Major", value=education.major, key=f"edit_edu_major_{index}").strip(),
                            degree=st.text_input("Degree Type", value=education.degree, key=f"edit_edu_degree_{index}").strip(),
                            gpa=st.text_input("GPA", value=education.gpa, key=f"edit_edu_gpa_{index}").strip(),
                            start_date=st.text_input("Start Date", value=education.start_date, key=f"edit_edu_start_{index}").strip(),
                            end_date=st.text_input("End Date", value=education.end_date, key=f"edit_edu_end_{index}").strip(),
                            currently_studying=st.checkbox("I currently study here", value=education.currently_studying, key=f"edit_edu_current_{index}"),
                            graduation_month=st.text_input("Graduation month", value=education.graduation_month, key=f"edit_edu_grad_month_{index}").strip(),
                            graduation_year=st.text_input("Graduation year", value=education.graduation_year, key=f"edit_edu_grad_year_{index}").strip(),
                        )
                    )
                updated.education = updated.educations[0] if updated.educations else EducationProfile()
            elif section == "work":
                _, work_entries = _profile_entries(profile)
                work_count = int(
                    st.number_input(
                        "Number of work experience entries",
                        min_value=1,
                        max_value=15,
                        value=max(1, len(work_entries)),
                        step=1,
                    )
                )
                work_entries = work_entries + [WorkExperienceProfile() for _ in range(max(0, work_count - len(work_entries)))]
                updated.work_experiences = []
                for index, work in enumerate(work_entries[:work_count]):
                    st.markdown(f"**Work Experience {index + 1}**")
                    updated.work_experiences.append(
                        WorkExperienceProfile(
                            job_title=st.text_input("Job Title", value=work.job_title, key=f"edit_work_title_{index}").strip(),
                            company=st.text_input("Company", value=work.company, key=f"edit_work_company_{index}").strip(),
                            job_type=st.text_input("Job Type", value=work.job_type, key=f"edit_work_type_{index}").strip(),
                            location=st.text_input("Location", value=work.location, key=f"edit_work_location_{index}").strip(),
                            start_date=st.text_input("Start Date", value=work.start_date, key=f"edit_work_start_{index}").strip(),
                            end_date=st.text_input("End Date", value=work.end_date, key=f"edit_work_end_{index}").strip(),
                            currently_working=st.checkbox("I currently work here", value=work.currently_working, key=f"edit_work_current_{index}"),
                            summary=st.text_area("Experience Summary", value=work.summary, key=f"edit_work_summary_{index}").strip(),
                            bullets=[
                                item.strip()
                                for item in st.text_area(
                                    "Job Description bullet points, one per line",
                                    value="\n".join(work.bullets),
                                    key=f"edit_work_bullets_{index}",
                                ).splitlines()
                                if item.strip()
                            ],
                        )
                    )
            elif section == "skills":
                updated.skills = [
                    item.strip()
                    for item in st.text_area(
                        "Skills, one per line",
                        value="\n".join(profile.skills),
                        height=260,
                    ).splitlines()
                    if item.strip()
                ]
            elif section == "eeo":
                updated.work_authorization.authorized_to_work_in_us = _choice_to_bool(
                    st.selectbox("Are you authorized to work in the US?", ["Yes", "No"], index=_option_index(["Yes", "No"], _bool_to_choice(profile.work_authorization.authorized_to_work_in_us)))
                )
                updated.work_authorization.requires_sponsorship = _choice_to_bool(
                    st.selectbox("Do you require sponsorship for this role?", ["No", "Yes"], index=_option_index(["No", "Yes"], _bool_to_choice(profile.work_authorization.requires_sponsorship)))
                )
                updated.work_authorization.internship_requires_sponsorship = updated.work_authorization.requires_sponsorship
                updated.work_authorization.full_time_requires_sponsorship = updated.work_authorization.requires_sponsorship
                updated.equal_opportunity.allow_autofill = st.checkbox("Allow voluntary EEO autofill", value=profile.equal_opportunity.allow_autofill)
                updated.equal_opportunity.gender = st.selectbox("What is your gender?", ["", "Male", "Female", "Non-binary", "Prefer not to answer"], index=_option_index(["", "Male", "Female", "Non-binary", "Prefer not to answer"], profile.equal_opportunity.gender)) or None
                updated.equal_opportunity.disability = st.selectbox("Do you have a disability?", ["", "No", "Yes", "Prefer not to answer"], index=_option_index(["", "No", "Yes", "Prefer not to answer"], profile.equal_opportunity.disability)) or None
                updated.equal_opportunity.veteran_status = st.selectbox("Are you a veteran?", ["", "No", "Yes", "Prefer not to answer"], index=_option_index(["", "No", "Yes", "Prefer not to answer"], profile.equal_opportunity.veteran_status)) or None
                updated.equal_opportunity.race = st.selectbox("How would you identify your race?", ["", "Asian", "White", "Black or African American", "Native Hawaiian or Other Pacific Islander", "American Indian or Alaska Native", "Two or more races", "Prefer not to answer"], index=_option_index(["", "Asian", "White", "Black or African American", "Native Hawaiian or Other Pacific Islander", "American Indian or Alaska Native", "Two or more races", "Prefer not to answer"], profile.equal_opportunity.race)) or None
                updated.equal_opportunity.hispanic_or_latino = st.selectbox("Are you Hispanic or Latino?", ["", "No", "Yes", "Prefer not to answer"], index=_option_index(["", "No", "Yes", "Prefer not to answer"], profile.equal_opportunity.hispanic_or_latino)) or None
                updated.equal_opportunity.lgbtq = st.selectbox("Do you identify as LGBTQ+?", ["", "No", "Yes", "Prefer not to answer"], index=_option_index(["", "No", "Yes", "Prefer not to answer"], profile.equal_opportunity.lgbtq)) or None
                updated.equal_opportunity.sexual_orientation = st.selectbox("How would you describe your sexual orientation?", ["", "Heterosexual", "Gay", "Lesbian", "Bisexual", "Prefer not to answer"], index=_option_index(["", "Heterosexual", "Gay", "Lesbian", "Bisexual", "Prefer not to answer"], profile.equal_opportunity.sexual_orientation)) or None
            elif section == "preferences":
                updated.search_preferences.target_roles = st.multiselect("Target job families", options=list(TargetRole), default=profile.search_preferences.target_roles, format_func=_role_label) or list(TargetRole)
                updated.search_preferences.preferred_locations = [item.strip() for item in st.text_area("Preferred locations, one per line", value="\n".join(profile.search_preferences.preferred_locations)).splitlines() if item.strip()]
                updated.search_preferences.allow_remote_us = st.checkbox("Remote US", value=profile.search_preferences.allow_remote_us)
                updated.search_preferences.allow_hybrid = st.checkbox("Hybrid", value=profile.search_preferences.allow_hybrid)
                updated.search_preferences.allow_onsite = st.checkbox("Onsite", value=profile.search_preferences.allow_onsite)
                updated.search_preferences.willing_to_relocate = st.checkbox("Open to relocate", value=profile.search_preferences.willing_to_relocate)
                updated.search_preferences.accepted_employment_types = st.multiselect("Employment types", options=["internship", "full_time"], default=profile.search_preferences.accepted_employment_types) or ["internship"]
            saved = st.form_submit_button("Update", type="primary")
    if saved:
        saved_profile = PROFILE_STORE.save_candidate_profile(updated)
        st.session_state.pop("profile_edit_section", None)
        st.session_state.pop(PROFILE_PREFILL_KEY, None)
        st.session_state.pop(PROFILE_PREFILL_SUMMARY_KEY, None)
        st.success("Profile updated.")
        return saved_profile
    return None


def _render_profile_setup(profile: CandidateProfile) -> CandidateProfile | None:
    st.title("ApplyTeX ATS Profile")
    st.caption(f"Signed in as @{profile.profile_id}. Your profile data is kept private and saved locally for this username.")

    edited_profile = _render_profile_editor(profile)
    if edited_profile:
        return edited_profile

    missing_required = _profile_missing_required(profile)
    if missing_required:
        st.warning("Complete these required profile fields before using autofill: " + ", ".join(missing_required))
    else:
        st.success("Profile is ready for application autofill.")
        if st.button("Continue to resume lab", key="profile_continue_top"):
            st.session_state["profile_show_setup"] = False
            st.rerun()

    _render_profile_nav()
    _section_anchor("profile-personal")
    _render_personal_view(profile)
    st.divider()
    _section_anchor("profile-education")
    _render_education_view(profile)
    st.divider()
    _section_anchor("profile-work")
    _render_work_view(profile)
    st.divider()
    _section_anchor("profile-skills")
    _render_skills_view(profile)
    st.divider()
    _section_anchor("profile-resume")
    saved_resume_profile = _render_resume_view(profile)
    if saved_resume_profile:
        return saved_resume_profile
    st.divider()
    _section_anchor("profile-eeo")
    _render_equal_employment_view(profile)
    st.divider()
    _section_anchor("profile-preferences")
    _render_preferences_view(profile)
    return None

MODEL_PRESETS: dict[str, dict[str, tuple[str, str]]] = {
    "Ollama routed": {
        "plan": ("ollama", "qwen3:8b"),
        "diff": ("ollama", "qwen2.5-coder:7b"),
        "refine": ("ollama", "qwen3:4b"),
    },
    "Groq routed": {
        "plan": ("groq", "llama-3.3-70b-versatile"),
        "diff": ("groq", "llama-3.3-70b-versatile"),
        "refine": ("groq", "llama-3.3-70b-versatile"),
    },
    "Codex subscription routed": {
        "plan": ("codex", "gpt-5.5"),
        "diff": ("codex", "gpt-5.5"),
        "refine": ("codex", "gpt-5.5"),
    },
}
def _load_default(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


def _ats_dict(result) -> dict[str, Any]:
    return {
        "score": result.score,
        "raw_score": getattr(result, "raw_score", result.score),
        "submission_score": getattr(result, "submission_score", result.score),
        "score_mode": getattr(result, "score_mode", "submission_fit"),
        "required_score": result.required_score,
        "preferred_score": result.preferred_score,
        "keyword_score": result.keyword_score,
        "required_found": result.required_found,
        "required_missing": result.required_missing,
        "preferred_found": result.preferred_found,
        "preferred_missing": result.preferred_missing,
        "keyword_hits": result.keyword_hits,
        "keyword_misses": result.keyword_misses,
        "excluded_unconfirmed_skills": getattr(
            result,
            "excluded_unconfirmed_skills",
            [],
        ),
        "submission_blockers": getattr(result, "submission_blockers", []),
    }


def _fmt_score(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}" if signed else f"{value:.1f}"


def _pdf_link(pdf_bytes: bytes) -> str:
    b64 = base64.b64encode(pdf_bytes).decode()
    return f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="720"></iframe>'


def _display_match_breakdown(breakdown: dict[str, Any] | None) -> None:
    if not breakdown:
        st.info("Run ATS analysis to see the match breakdown.")
        return

    categories = breakdown.get("categories", {}) or {}
    st.markdown("#### Match Breakdown")
    cols = st.columns(5)
    for col, key in zip(
        cols,
        ["experience", "skills", "industry_domain", "education", "keywords"],
        strict=False,
    ):
        category = categories.get(key, {})
        col.metric(
            category.get("label", key.replace("_", " ").title()),
            f"{float(category.get('score', 0)):.1f}",
        )
        col.caption(str(category.get("status", "n/a")).replace("_", " "))

    leaks = breakdown.get("top_score_leaks") or []
    if leaks:
        st.markdown("#### Top Score Leaks")
        st.dataframe(leaks, use_container_width=True, hide_index=True)

    priority = breakdown.get("section_priority") or []
    if priority:
        st.markdown("#### Recommended Section Priority")
        st.write(" -> ".join(priority))
    focus = breakdown.get("edit_focus") or []
    if focus:
        st.markdown("#### Edit Focus")
        for item in focus:
            st.write(f"- {item}")


def _display_category_deltas(
    before_breakdown: dict[str, Any] | None,
    after_breakdown: dict[str, Any] | None,
) -> None:
    if not before_breakdown or not after_breakdown:
        _display_match_breakdown(after_breakdown)
        return
    before_categories = before_breakdown.get("categories", {}) or {}
    after_categories = after_breakdown.get("categories", {}) or {}
    rows: list[dict[str, Any]] = []
    for key in ["experience", "skills", "industry_domain", "education", "keywords"]:
        before = before_categories.get(key, {})
        after = after_categories.get(key, {})
        before_score = float(before.get("score", 0))
        after_score = float(after.get("score", 0))
        rows.append(
            {
                "category": after.get("label") or before.get("label") or key,
                "before": round(before_score, 1),
                "after": round(after_score, 1),
                "delta": round(after_score - before_score, 1),
                "status": after.get("status", ""),
            }
        )
    st.markdown("#### Category Score Deltas")
    st.dataframe(rows, use_container_width=True, hide_index=True)
    _display_match_breakdown(after_breakdown)


def _query_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _score_label(score: float | None) -> str:
    if score is None:
        return "n/a"
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Strong"
    if score >= 65:
        return "Good"
    if score >= 45:
        return "Low"
    return "Poor"


def _chip_html(label: str, present: bool = False) -> str:
    klass = "sja-chip-good" if present else "sja-chip-miss"
    icon = "✓ " if present else ""
    return f'<span class="{klass}">{icon}{html.escape(label)}</span>'


def _simple_delatex(text: str) -> str:
    text = re.sub(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:textbf|textit|emph|underline|small)\s*\{([^{}]*)\}", r"\1", text)
    text = (
        text.replace(r"\&", "&")
        .replace(r"\%", "%")
        .replace(r"\_", "_")
        .replace(r"\\", " ")
        .replace("~", " ")
    )
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", "").replace("}", "")
    return " ".join(text.split())


def _guided_css() -> None:
    st.markdown(
        """
        <style>
          :root {
            --sja-bg:#f6f7f5;
            --sja-surface:#ffffff;
            --sja-surface-2:#f2f4f1;
            --sja-ink:#161a18;
            --sja-muted:#6a726d;
            --sja-border:#e3e8e4;
            --sja-accent:#00df95;
            --sja-accent-soft:#dffbef;
            --sja-warn:#fff5e5;
            --sja-danger:#f0525f;
          }
          html, body, [data-testid="stAppViewContainer"] { background:var(--sja-bg); }
          header[data-testid="stHeader"] { background:transparent; }
          div[data-testid="stToolbar"],
          div[data-testid="stDecoration"] { visibility:hidden !important; }
          section[data-testid="stSidebar"] {
            background:#ffffff;
            border-right:1px solid var(--sja-border);
          }
          section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding:1.25rem 1rem;
          }
          .block-container {
            padding: 1.25rem 2.25rem 7rem !important;
            max-width: 1280px;
          }
          .sja-sidebar-brand {
            display:flex; align-items:center; gap:.55rem; margin:.2rem 0 1rem;
            font-size:1.18rem; font-weight:900; color:var(--sja-ink);
          }
          .sja-sidebar-logo {
            width:30px; height:30px; border-radius:9px; display:grid; place-items:center;
            background:var(--sja-accent); color:#06130d; font-weight:950;
          }
          .sja-side-nav {
            display:grid; gap:.25rem; margin:.75rem 0 1.25rem;
          }
          .sja-side-nav a {
            display:flex; align-items:center; gap:.55rem; min-height:38px;
            padding:.42rem .55rem; border-radius:10px; color:#2d3530;
            text-decoration:none; font-weight:800; font-size:.94rem;
          }
          .sja-side-nav a.active,
          .sja-side-nav a:hover {
            background:#eff6f2; color:#07140e;
          }
          .sja-sidebar-note {
            border:1px solid var(--sja-border); border-radius:14px;
            padding:.75rem; background:#f8fbf9; color:var(--sja-muted);
            font-size:.86rem; line-height:1.35;
          }
          .stButton > button,
          .stDownloadButton > button {
            min-height: 44px;
            border-radius: 999px !important;
            border: 1px solid var(--sja-border) !important;
            background: var(--sja-surface) !important;
            color: var(--sja-ink) !important;
            font-weight: 800 !important;
            box-shadow: none !important;
          }
          .stButton > button:hover,
          .stDownloadButton > button:hover {
            border-color:#b6c0ba !important;
            color:var(--sja-ink) !important;
          }
          .stButton > button[kind="primary"],
          .stButton > button[data-testid="baseButton-primary"] {
            border-color: var(--sja-accent) !important;
            background: var(--sja-accent) !important;
            color:#062015 !important;
          }
          .stTextInput input,
          .stTextArea textarea,
          .stSelectbox [data-baseweb="select"] {
            border-radius: 12px !important;
            border-color: var(--sja-border) !important;
          }
          div[data-testid="stCheckbox"] label,
          div[data-testid="stRadio"] label {
            color:var(--sja-ink) !important;
            font-weight:700;
          }
          div[data-testid="stCheckbox"] {
            padding:.28rem .1rem;
          }
          div[data-testid="stCheckbox"] input[type="checkbox"] {
            accent-color:var(--sja-accent) !important;
            width:22px !important;
            height:22px !important;
          }
          div[data-testid="stCheckbox"] label {
            min-height:28px;
            align-items:center !important;
          }
          div[data-testid="stCheckbox"] [data-baseweb="checkbox"] {
            width:auto !important;
            height:auto !important;
            display:flex !important;
            align-items:center !important;
            gap:9px !important;
            margin:0 !important;
          }
          div[data-testid="stCheckbox"] label > div:first-child,
          div[data-testid="stCheckbox"] [data-baseweb="checkbox"] > div:first-child {
            width:22px !important;
            height:22px !important;
            min-width:22px !important;
            flex:0 0 22px !important;
            display:grid !important;
            place-items:center !important;
            border-radius:6px !important;
          }
          div[data-testid="stCheckbox"] svg {
            width:17px !important;
            height:17px !important;
            display:block !important;
            margin:auto !important;
          }
          div[data-testid="stRadio"] > div {
            gap:.4rem;
          }
          div[data-testid="stRadio"] [role="radiogroup"] {
            background:var(--sja-surface-2);
            border:1px solid var(--sja-border);
            border-radius:14px;
            padding:.55rem .7rem;
          }
          div[data-testid="stTabs"] [role="tablist"] {
            gap:6px;
            background:var(--sja-surface-2);
            border:1px solid var(--sja-border);
            border-radius:16px;
            padding:5px;
          }
          div[data-testid="stTabs"] [role="tab"] {
            border-radius:12px;
            padding:8px 14px;
            font-weight:850;
            color:var(--sja-muted);
          }
          div[data-testid="stTabs"] [aria-selected="true"] {
            background:var(--sja-surface);
            color:var(--sja-ink);
            box-shadow:0 1px 2px rgba(14,18,16,.06);
          }
          .sja-topbar {
            display:flex; align-items:center; gap:12px; margin:0 auto 10px;
            max-width:1180px;
          }
          .sja-title { font-size:24px; font-weight:850; color:var(--sja-ink); margin-right:auto; }
          .sja-pill {
            display:inline-flex; align-items:center; gap:6px; padding:7px 12px;
            border-radius:999px; background:var(--sja-accent); color:#04130d; font-weight:800;
            font-size:14px;
          }
          .sja-pill-soft { background:var(--sja-surface); border:1px solid var(--sja-border); }
          .sja-stepper {
            display:flex; justify-content:center; align-items:center; gap:8px;
            margin:6px auto 16px; max-width:700px;
          }
          .sja-step {
            display:inline-flex; align-items:center; gap:5px; color:#959b97;
            font-size:12px; font-weight:850; white-space:nowrap;
            padding:3px 8px; border:1px solid transparent; border-radius:999px;
          }
          .sja-step.active { color:var(--sja-ink); }
          .sja-num {
            display:inline-grid; place-items:center; width:18px; height:18px;
            border-radius:50%; margin-right:0; background:#9aa19d; color:white;
            font-size:10px; line-height:1; font-weight:900;
          }
          .sja-step.active {
            border-color:#cfd7d2; background:#fff;
          }
          .sja-step.active .sja-num { background:var(--sja-accent); color:#04130d; }
          .sja-step-sep { color:#a9b0ac; font-size:12px; }
          .sja-panel, .sja-card {
            border:1px solid var(--sja-border); border-radius:16px; background:var(--sja-surface);
            padding:22px; box-shadow:0 1px 2px rgba(14,18,16,.04);
          }
          .sja-card h2 {
            margin:0 0 16px;
            font-size:22px;
            line-height:1.15;
            color:var(--sja-ink);
          }
          .sja-card p, .sja-card .stMarkdown {
            color:var(--sja-muted);
          }
          .sja-panel { max-width:1180px; margin:0 auto; }
          .sja-muted { color:var(--sja-muted); }
          .sja-hero {
            display:grid; grid-template-columns:minmax(0,1fr) 180px; gap:24px;
            align-items:start; padding-bottom:18px; border-bottom:1px solid var(--sja-border);
          }
          .sja-eyebrow {
            color:var(--sja-muted); font-size:13px; font-weight:800;
            text-transform:uppercase; letter-spacing:.04em;
          }
          .sja-hero h1 {
            margin:.2rem 0 .45rem; font-size:38px; line-height:1.05;
            letter-spacing:0; color:#20242c;
          }
          .sja-score-card {
            border:1px solid var(--sja-border); border-radius:16px; padding:16px;
            background:linear-gradient(180deg,#fff,#f7faf8); text-align:center;
          }
          .sja-score { font-size:54px; font-weight:900; line-height:.95; color:#262a35; }
          .sja-score-caption { color:var(--sja-muted); font-size:13px; margin-top:6px; }
          .sja-split-cards {
            display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:18px 0 8px;
          }
          .sja-info-card {
            display:grid; grid-template-columns:42px minmax(0,1fr); gap:12px;
            align-items:center; padding:16px; border-radius:14px; background:var(--sja-surface-2);
          }
          .sja-icon-tile {
            width:42px; height:42px; border-radius:12px; background:#fff;
            display:grid; place-items:center; border:1px solid var(--sja-border);
            font-weight:900;
          }
          .sja-info-label { color:var(--sja-muted); font-size:13px; font-weight:800; }
          .sja-info-title { color:var(--sja-ink); font-weight:850; overflow-wrap:anywhere; }
          .sja-scan-list { display:grid; gap:10px; margin-top:14px; }
          .sja-scan-row {
            display:grid; grid-template-columns:210px minmax(0,1fr); gap:10px;
          }
          .sja-scan-label {
            min-height:56px; border-radius:12px; padding:15px 16px;
            background:var(--sja-warn); display:flex; align-items:center;
            justify-content:space-between; font-weight:850;
          }
          .sja-scan-value {
            min-height:56px; border-radius:12px; padding:14px 16px;
            background:var(--sja-warn); display:flex; align-items:center;
            flex-wrap:wrap; gap:6px; overflow:hidden;
          }
          .sja-grid-row {
            display:grid; grid-template-columns: 220px 1fr 1fr; gap:10px;
            align-items:stretch; margin:8px 0;
          }
          .sja-cell {
            padding:18px; border-radius:10px; background:#fff7eb;
            min-height:58px; display:flex; align-items:center; gap:8px;
            flex-wrap:wrap; overflow:hidden;
          }
          .sja-cell.header { background:#f5f6f5; font-weight:850; }
          .sja-cell.warn-label { font-weight:850; justify-content:space-between; }
          .sja-chip-good, .sja-chip-miss {
            display:inline-flex; align-items:center; margin:0; padding:5px 9px;
            border-radius:7px; border:1px solid #dde5df; background:#fff;
            color:var(--sja-ink); font-size:13px; line-height:1.2;
            max-width:100%; overflow-wrap:anywhere;
          }
          .sja-chip-good { background:var(--sja-accent-soft); border-color:#b6efd7; }
          .sja-chip-miss { background:#fff; color:#3c433f; }
          .sja-resume-shell {
            background:#fff; border:1px solid #e6ebe8; border-radius:16px;
            padding:16px 22px; aspect-ratio:8.5/11; height:calc(100vh - 315px);
            min-height:520px; max-height:720px; overflow:hidden;
            font-family: Georgia, serif; font-size:8.7px;
            color:#111; line-height:1.12; box-shadow:0 8px 30px rgba(0,0,0,.06);
          }
          .sja-resume-shell ul { margin:.16rem 0 .18rem 1rem; padding:0; }
          .sja-resume-shell li { margin:.08rem 0; }
          .sja-resume-name { text-align:center; font-size:19px; font-weight:500; }
          .sja-resume-contact { text-align:center; font-size:7.4px; margin-bottom:6px; overflow-wrap:anywhere; }
          .sja-resume-section { margin-top:4px; border-top:1px solid #333; }
          .sja-resume-section h3 { margin:2px 0 3px; font-size:10.2px; }
          .sja-entry-head { display:flex; justify-content:space-between; gap:8px; font-weight:700; line-height:1.05; }
          .sja-stmt { padding:1px 2px; border-radius:4px; }
          .sja-stmt.changed {
            background:#dff4ea; outline:1px solid #4de0a8; outline-offset:1px;
          }
          .sja-stmt .sja-hover-actions {
            display:none; position:absolute; margin-top:-34px; margin-left:-4px;
            padding:6px 8px; border-radius:9px; background:#fff;
            box-shadow:0 8px 22px rgba(0,0,0,.12); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
            font-size:11px; font-weight:800;
          }
          .sja-hover-actions a {
            color:#0b5137; text-decoration:none; white-space:nowrap;
          }
          .sja-hover-actions a:hover { text-decoration:underline; }
          .sja-stmt.changed:hover .sja-hover-actions { display:inline-flex; gap:10px; }
          .sja-stmt-popover {
            border:1px solid #bfead8; background:#f4fff9; border-radius:12px;
            padding:10px 12px; margin:8px 0 10px; box-shadow:0 8px 22px rgba(21,45,33,.08);
          }
          .sja-stmt-popover .label {
            font-size:11px; font-weight:900; color:#506158; text-transform:uppercase;
            letter-spacing:.04em; margin-bottom:4px;
          }
          .sja-stmt-popover .body {
            font-size:13px; line-height:1.35; color:#15211b; overflow-wrap:anywhere;
          }
          .sja-change-row-label {
            min-width:0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
            color:#56615b; font-size:12px; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; padding-top:11px;
          }
          div[data-testid="column"] .stButton > button {
            white-space:nowrap !important;
            min-width:0 !important;
            padding-left:10px !important;
            padding-right:10px !important;
          }
          .sja-review-card {
            background:var(--sja-surface-2);
            border:1px solid var(--sja-border);
            border-radius:16px;
            padding:22px;
            margin-bottom:14px;
          }
          .sja-suggestion {
            border:1px solid #bdeedb;
            border-radius:999px;
            padding:9px 14px;
            margin:8px 0;
            text-align:right;
            font-weight:700;
            background:#fbfffd;
          }
          .sja-bottom-bar {
            position:sticky; bottom:0; z-index:5; display:flex; gap:16px;
            align-items:center; justify-content:center; padding:16px;
            background:rgba(248,249,248,.94); border-top:1px solid #e8ece9;
          }
          .sja-bottom-bar .stButton > button, .sja-primary button {
            border-radius:999px !important; min-height:46px; font-weight:850 !important;
          }
          @media (max-width: 900px) {
            .block-container { padding:1rem 1rem 7rem !important; }
            .sja-topbar, .sja-stepper { justify-content:flex-start; overflow-x:auto; }
            .sja-hero, .sja-split-cards, .sja-scan-row { grid-template-columns:1fr; }
            .sja-hero h1 { font-size:30px; }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_guided_sidebar(profile: CandidateProfile, step: int) -> None:
    missing = _profile_missing_required(profile)
    current = {1: "Match", 2: "Align", 3: "Review"}.get(step, "Match")
    st.sidebar.markdown(
        f"""
        <div class="sja-sidebar-brand">
          <div class="sja-sidebar-logo">S</div>
          <span>ApplyTeX ATS</span>
        </div>
        <nav class="sja-side-nav">
          <a class="{('active' if current == 'Match' else '')}" href="#">Match Overview</a>
          <a class="{('active' if current == 'Align' else '')}" href="#">Align Resume</a>
          <a class="{('active' if current == 'Review' else '')}" href="#">Review Resume</a>
          <a href="?">Resume Lab</a>
        </nav>
        <div class="sja-sidebar-note">
          <b>Profile status</b><br>
          {html.escape('Complete' if not missing else f'Missing: {", ".join(missing[:3])}')}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Back to profile setup", use_container_width=True, key="guided_sidebar_profile"):
        st.session_state["profile_show_setup"] = True
        st.rerun()


def _guided_topbar(step: int) -> None:
    st.markdown(
        """
        <div class="sja-topbar">
          <div class="sja-title">Generate Your Custom Resume</div>
          <div class="sja-pill sja-pill-soft">Local preview</div>
          <div class="sja-pill">One-page enforced</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    labels = ["See Your Difference", "Align Your Resume", "Review Your New Resume"]
    steps = []
    for index, label in enumerate(labels, start=1):
        active = " active" if step == index else ""
        steps.append(
            f'<span class="sja-step{active}"><span class="sja-num">{index}</span>{html.escape(label)}</span>'
        )
    sep = '<span class="sja-step-sep">-</span>'
    st.markdown(f'<div class="sja-stepper">{sep.join(steps)}</div>', unsafe_allow_html=True)


def _skill_group(skill: str) -> str:
    norm = skill.lower()
    if any(token in norm for token in ("instinct", "communication", "stakeholder", "product", "judgment")):
        return "Soft Skills"
    if any(token in norm for token in ("api", "javascript", "langchain", "autogen", "n8n", "zapier", "slack", "salesforce", "notion", "openai", "anthropic", "crew", "tool")):
        return "Tools"
    return "Functional Skills"


def _editable_section_stmt_ids(parse_result) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "Summary": [],
        "Skills": [],
        "Work Experience": [],
        "Projects": [],
    }
    for stmt_id in parse_result.stmt_index:
        if stmt_id.startswith("summary"):
            sections["Summary"].append(stmt_id)
        elif stmt_id.startswith("skills"):
            sections["Skills"].append(stmt_id)
        elif stmt_id.startswith("work"):
            sections["Work Experience"].append(stmt_id)
        elif stmt_id.startswith("proj"):
            sections["Projects"].append(stmt_id)
    return {key: value for key, value in sections.items() if value}


def _selected_guided_stmt_ids(state: dict[str, Any], parse_result) -> list[str]:
    sections = _editable_section_stmt_ids(parse_result)
    selected: list[str] = []
    for section_name, stmt_ids in sections.items():
        if not state.get("sections", {}).get(section_name, True):
            continue
        if section_name == "Work Experience" and state.get("work_mode") == "Quick Edit":
            selected.extend(
                stmt_id
                for stmt_id in stmt_ids
                if stmt_id.startswith("work_0_") or stmt_id.startswith("work_1_")
            )
        else:
            selected.extend(stmt_ids)
    return selected


def _guided_context(profile: CandidateProfile) -> dict[str, Any] | None:
    application_id = _query_value("application_id")
    job_id = _query_value("job_id")
    store = PROFILE_STORE
    job = None
    if application_id:
        application = store.get_application(application_id)
        if application:
            job = store.get_job(application.job_id)
    if job is None and job_id:
        job = store.get_job(job_id)
    if job is None:
        st.error("Could not load the captured job. Open this flow from the Chrome sidebar after the job is captured.")
        return None
    if not profile.resume_latex_source.strip():
        st.error("Upload a .tex profile resume before using guided customization.")
        return None

    parse_result = parse(
        profile.resume_latex_source,
        resume_id=Path(profile.resume_filename or "profile_resume").stem,
    )
    full_resume = extract_full_resume(parse_result)
    plain_resume = _build_plain_text(full_resume)
    job_keywords = extract_job_keywords_fast(job.description)
    baseline_ats = check_ats(plain_resume, job_keywords)
    screening = analyze_screening_fit(
        plain_resume,
        job_keywords,
        baseline_ats,
        editable_statement_count=len(parse_result.stmt_index),
    )
    raw_missing = list(dict.fromkeys(list(baseline_ats.required_missing) + list(baseline_ats.preferred_missing)))
    candidates, theme_gaps = split_skill_confirmation_candidates(raw_missing)
    groups: dict[str, list[str]] = {"Functional Skills": [], "Tools": [], "Soft Skills": []}
    for skill in candidates:
        groups[_skill_group(skill)].append(skill)
    return {
        "application_id": application_id,
        "job": job,
        "profile": profile,
        "parse_result": parse_result,
        "full_resume": full_resume,
        "plain_resume": plain_resume,
        "job_keywords": job_keywords,
        "baseline_ats": baseline_ats,
        "screening": screening.to_dict(),
        "skill_candidates": candidates,
        "skill_groups": groups,
        "theme_gaps": theme_gaps,
    }


def _guided_state(context: dict[str, Any]) -> dict[str, Any]:
    key = "guided_customize_state"
    context_key = f"{context.get('application_id') or ''}:{context['job'].job_id}:{context['profile'].profile_id}"
    if key not in st.session_state or st.session_state[key].get("context_key") != context_key:
        sections = {
            section: True
            for section in _editable_section_stmt_ids(context["parse_result"])
        }
        st.session_state[key] = {
            "context_key": context_key,
            "step": 1,
            "sections": sections,
            "work_mode": "Quick Edit",
            "confirmed_skills": [],
            "manual_skills": [],
            "original_latex": context["profile"].resume_latex_source,
            "current_latex": context["profile"].resume_latex_source,
            "diff": [],
            "change_history": [],
            "chat_messages": [],
            "active_scope": "",
            "last_result": None,
            "llm_preset": "Ollama routed",
            "llm_custom_routes": {},
        }
    return st.session_state[key]


def _guided_llm_routes(state: dict[str, Any]) -> dict[str, LLMTaskRoute]:
    preset = state.get("llm_preset", "Ollama routed")
    if preset == "Custom":
        custom = state.get("llm_custom_routes") or {}
        routes = {
            task: LLMTaskRoute(
                backend=(custom.get(task) or {}).get("backend"),
                model=(custom.get(task) or {}).get("model"),
            )
            for task in ("plan", "diff", "review", "refine")
        }
    else:
        routes = {
            task: LLMTaskRoute(backend=backend, model=model)
            for task, (backend, model) in MODEL_PRESETS.get(preset, MODEL_PRESETS["Ollama routed"]).items()
        }
        routes.setdefault("review", routes["diff"])
    return routes


def _render_guided_llm_picker(state: dict[str, Any]) -> None:
    st.markdown('<div class="sja-card"><h2>3. Choose the AI model</h2>', unsafe_allow_html=True)
    presets = ["Ollama routed", "Groq routed", "Codex subscription routed", "Custom"]
    current = state.get("llm_preset", "Ollama routed")
    state["llm_preset"] = st.selectbox(
        "LLM preset",
        options=presets,
        index=_option_index(presets, current),
        key="guided_llm_preset",
        help="This controls which model improves the resume for this customization run.",
    )
    if state["llm_preset"] == "Custom":
        custom_routes: dict[str, dict[str, str]] = {}
        for task, label in (
            ("plan", "Planning"),
            ("diff", "Resume rewrite"),
            ("review", "Quality review"),
            ("refine", "Chat edits"),
        ):
            cols = st.columns([1, 1])
            previous = (state.get("llm_custom_routes") or {}).get(task, {})
            backend_options = ["ollama", "groq", "codex"]
            backend = cols[0].selectbox(
                f"{label} backend",
                options=backend_options,
                index=_option_index(backend_options, previous.get("backend") or "ollama"),
                key=f"guided_custom_{task}_backend",
            )
            model_options = MODEL_OPTIONS[backend]
            model = cols[1].selectbox(
                f"{label} model",
                options=model_options,
                index=_option_index(model_options, previous.get("model") or model_options[0]),
                key=f"guided_custom_{task}_model_{backend}",
            )
            custom_routes[task] = {"backend": backend, "model": model}
        state["llm_custom_routes"] = custom_routes
    else:
        route = _guided_llm_routes(state)
        labels = [
            f"{task}: {item.backend}/{item.model}"
            for task, item in route.items()
        ]
        st.caption("Using " + " · ".join(labels))
    st.markdown("</div>", unsafe_allow_html=True)


def _render_guided_step1(context: dict[str, Any], state: dict[str, Any]) -> None:
    job = context["job"]
    ats = context["baseline_ats"]
    screening = context["screening"]
    breakdown = screening.get("match_breakdown", {})
    categories = breakdown.get("categories", {}) if breakdown else {}
    keyword_found = list(dict.fromkeys(ats.required_found + ats.preferred_found + ats.keyword_hits))
    keyword_missing = list(dict.fromkeys(ats.required_missing + ats.preferred_missing + ats.keyword_misses))
    resume_label = (
        context["profile"].resume_pdf_filename
        or context["profile"].resume_filename
        or "Profile resume"
    )
    role_label = (
        context["profile"].search_preferences.target_roles[0].value.replace("_", " ").title()
        if context["profile"].search_preferences.target_roles
        else "Target role"
    )
    recommendation = (
        screening.get("recommendations") or ["Improve summary and bullets with supported JD language."]
    )[0]
    domain = categories.get("industry_domain", {})
    st.markdown(
        f"""
        <div class="sja-panel">
          <div class="sja-hero">
            <div>
              <div class="sja-eyebrow">Resume match</div>
              <h1>{_score_label(ats.score)} match for this role</h1>
              <p class="sja-muted">Your current resume scores <b>{ats.score:.1f}/100</b> against this job. The next step only asks you to confirm skills you can defend.</p>
            </div>
            <div class="sja-score-card">
              <div class="sja-score">{ats.score / 10:.1f}</div>
              <b>{_score_label(ats.score)}</b>
              <div class="sja-score-caption">ATS fit score</div>
            </div>
          </div>

          <div class="sja-split-cards">
            <div class="sja-info-card">
              <div class="sja-icon-tile">J</div>
              <div>
                <div class="sja-info-label">Job</div>
                <div class="sja-info-title">{html.escape(job.company)}</div>
                <div class="sja-muted">{html.escape(job.title)}</div>
              </div>
            </div>
            <div class="sja-info-card">
              <div class="sja-icon-tile">R</div>
              <div>
                <div class="sja-info-label">Resume</div>
                <div class="sja-info-title">{html.escape(resume_label)}</div>
                <div class="sja-muted">{html.escape(role_label)}</div>
              </div>
            </div>
          </div>

          <div class="sja-scan-list">
            <div class="sja-scan-row">
              <div class="sja-scan-label">Job title <span>Review</span></div>
              <div class="sja-scan-value">{html.escape(job.title)} <span class="sja-muted">Current profile target: {html.escape(role_label)}</span></div>
            </div>
            <div class="sja-scan-row">
              <div class="sja-scan-label">Industry/domain <span>Signal</span></div>
              <div class="sja-scan-value">{" ".join(_chip_html(item, True) for item in domain.get("found", [])[:8]) or "No explicit domain requirement detected."}</div>
            </div>
            <div class="sja-scan-row">
              <div class="sja-scan-label">Keywords ({len(keyword_found)}/{len(keyword_found) + len(keyword_missing)}) <span>Gaps</span></div>
              <div class="sja-scan-value">{" ".join(_chip_html(item, True) for item in keyword_found[:18])} {" ".join(_chip_html(item, False) for item in keyword_missing[:18])}</div>
            </div>
            <div class="sja-scan-row">
              <div class="sja-scan-label">Recommended focus <span>Next</span></div>
              <div class="sja-scan-value">{html.escape(recommendation)}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, button_col, _ = st.columns([1, 1, 1])
    with button_col:
        if st.button("Improve My Resume for This Job", type="primary", use_container_width=True):
            state["step"] = 2
            st.rerun()


def _render_guided_step2(context: dict[str, Any], state: dict[str, Any]) -> None:
    left, right = st.columns(2, gap="large")
    sections = _editable_section_stmt_ids(context["parse_result"])
    with left:
        st.markdown('<div class="sja-card"><h2>1. Choose sections to enhance</h2>', unsafe_allow_html=True)
        for section_name in sections:
            state["sections"][section_name] = st.checkbox(
                section_name,
                value=state["sections"].get(section_name, True),
                key=f"guided_section_{section_name}",
            )
            if section_name == "Work Experience" and state["sections"][section_name]:
                state["work_mode"] = st.radio(
                    "Work Experience edit depth",
                    ["Quick Edit", "Full Edit"],
                    index=0 if state.get("work_mode") == "Quick Edit" else 1,
                    horizontal=False,
                    key="guided_work_mode",
                )
        st.markdown("</div>", unsafe_allow_html=True)
    with right:
        candidates = context["skill_candidates"]
        selected = set(state.get("confirmed_skills", []))
        st.markdown(
            f'<div class="sja-card"><h2>2. Add missing skill keywords ({len(selected)}/{len(candidates)})</h2>',
            unsafe_allow_html=True,
        )
        if candidates and st.button("Select all", key="guided_select_all"):
            state["confirmed_skills"] = candidates[:]
            st.rerun()
        for group, skills in context["skill_groups"].items():
            if not skills:
                continue
            st.markdown(f"**{group}**")
            cols = st.columns(2)
            for index, skill in enumerate(skills):
                with cols[index % 2]:
                    checked = st.checkbox(
                        skill,
                        value=skill in selected,
                        key=f"guided_skill_{group}_{skill}",
                    )
                    if checked:
                        selected.add(skill)
                    else:
                        selected.discard(skill)
        manual = st.text_input("Add manual skill", key="guided_manual_skill_input")
        if st.button("Add manual skill", key="guided_add_manual_skill") and manual.strip():
            manual_skill = manual.strip()
            state.setdefault("manual_skills", [])
            if manual_skill not in state["manual_skills"]:
                state["manual_skills"].append(manual_skill)
            selected.add(manual_skill)
        if state.get("manual_skills"):
            st.caption("Manual skills: " + ", ".join(state["manual_skills"]))
        state["confirmed_skills"] = list(dict.fromkeys([*selected, *state.get("manual_skills", [])]))
        st.markdown("</div>", unsafe_allow_html=True)
    _, llm_col, _ = st.columns([0.4, 2.2, 0.4])
    with llm_col:
        _render_guided_llm_picker(state)
    st.markdown('<div class="sja-bottom-bar">', unsafe_allow_html=True)
    back_col, gen_col = st.columns([1, 2])
    if back_col.button("Back", use_container_width=True):
        state["step"] = 1
        st.rerun()
    if gen_col.button("Generate My New Resume", type="primary", use_container_width=True):
        _guided_generate_resume(context, state)
        state["step"] = 3
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _guided_generate_resume(context: dict[str, Any], state: dict[str, Any]) -> None:
    allowed = _selected_guided_stmt_ids(state, context["parse_result"])
    llm_routes = _guided_llm_routes(state)
    with st.spinner("Generating your one-page optimized resume..."):
        result = _run_async(
            run_optimization_pipeline(
                parse_result=context["parse_result"],
                job_description=context["job"].description,
                confirmed_skills=state.get("confirmed_skills", []),
                allowed_stmt_ids=allowed,
                job_keywords=context["job_keywords"],
                llm_routes=llm_routes,
            )
        )
    state["last_result"] = result
    state["current_latex"] = result.modified_latex or state["original_latex"]
    state["diff"] = result.diff
    state["change_history"] = result.diff[:]


def _changed_stmt_ids(state: dict[str, Any]) -> set[str]:
    return {str(change.get("stmt_id")) for change in state.get("change_history", []) if change.get("stmt_id")}


def _guided_action_href(context: dict[str, Any], action: str, stmt_id: str) -> str:
    params = {
        "mode": "customize",
        "application_id": context.get("application_id") or "",
        "job_id": context["job"].job_id,
        "profile_id": context["profile"].profile_id,
        "action": action,
        "stmt_id": stmt_id,
    }
    return "?" + urlencode({key: value for key, value in params.items() if value})


def _change_by_stmt_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(change.get("stmt_id")): change
        for change in state.get("change_history", [])
        if change.get("stmt_id")
    }


def _render_statement_compare_popover(change: dict[str, Any]) -> str:
    original = html.escape(_simple_delatex(str(change.get("original", ""))))
    current = html.escape(_simple_delatex(str(change.get("value", ""))))
    return f"""
      <div class="sja-stmt-popover">
        <div class="label">Original</div>
        <div class="body">{original}</div>
        <div class="label" style="margin-top:8px;">Current</div>
        <div class="body">{current}</div>
      </div>
    """


def _render_resume_html(
    parse_result,
    profile: CandidateProfile,
    changed_stmt_ids: set[str],
    context: dict[str, Any],
    state: dict[str, Any],
) -> str:
    full = extract_full_resume(parse_result)
    personal = full.get("personal_info", {}) or {}
    name = personal.get("name") or profile.full_name or "Your Name"
    contact = " | ".join(
        item
        for item in [personal.get("phone") or profile.phone, personal.get("email") or profile.email, profile.linkedin_url, profile.github_url]
        if item
    )
    parts = [
        '<div class="sja-resume-shell">',
        f'<div class="sja-resume-name">{html.escape(name)}</div>',
        f'<div class="sja-resume-contact">{html.escape(contact)}</div>',
    ]
    compare_id = state.get("compare_stmt_id") or ""
    changes_by_id = _change_by_stmt_id(state)

    def render_stmt(tag: str, stmt_id: str, text: str) -> str:
        klass = "sja-stmt changed" if stmt_id in changed_stmt_ids else "sja-stmt"
        prefix = ""
        actions = ""
        if stmt_id in changed_stmt_ids:
            compare_action = "clear_compare" if compare_id == stmt_id else "compare"
            compare_label = "Hide compare" if compare_id == stmt_id else "Compare to original"
            actions = (
                '<span class="sja-hover-actions">'
                f'<a href="{html.escape(_guided_action_href(context, compare_action, stmt_id))}">{compare_label}</a>'
                f'<a href="{html.escape(_guided_action_href(context, "edit", stmt_id))}">Edit with AI</a>'
                "</span>"
            )
        if compare_id == stmt_id and stmt_id in changes_by_id:
            prefix = _render_statement_compare_popover(changes_by_id[stmt_id])
        body = f'{actions}{html.escape(_simple_delatex(text))}'
        return f'{prefix}<{tag} class="{klass}" id="{html.escape(stmt_id)}">{body}</{tag}>'

    for section in parse_result.doc.sections:
        if section.section_type.value == "personal_info":
            continue
        parts.append('<div class="sja-resume-section">')
        parts.append(f"<h3>{html.escape(section.display_name)}</h3>")
        for stmt in section.statements:
            parts.append(render_stmt("p", stmt.stmt_id, stmt.text))
        for entry in section.entries:
            entry_label = " | ".join(
                item
                for item in [
                    _simple_delatex(entry.company or ""),
                    _simple_delatex(entry.title or entry.header_text),
                ]
                if item
            )
            parts.append(
                f'<div class="sja-entry-head"><span>{html.escape(entry_label)}</span><span>{html.escape(_simple_delatex(entry.years or ""))}</span></div>'
            )
            if entry.statements:
                parts.append("<ul>")
                for stmt in entry.statements:
                    parts.append(render_stmt("li", stmt.stmt_id, stmt.text))
                parts.append("</ul>")
        for skill in section.skill_lines:
            parts.append(render_stmt("p", skill.stmt_id, skill.text))
        parts.append("</div>")
    parts.append("</div>")
    return "\n".join(parts)


def _current_result_scores(state: dict[str, Any], context: dict[str, Any]) -> tuple[float, float]:
    before = context["baseline_ats"].score
    result = state.get("last_result")
    after = result.ats_after.score if result and result.ats_after else before
    return before, after


def _apply_guided_query_action(state: dict[str, Any]) -> None:
    action = _query_value("action")
    stmt_id = _query_value("stmt_id")
    if not action or not stmt_id:
        return
    if action == "compare":
        state["compare_stmt_id"] = stmt_id
    elif action == "clear_compare":
        if state.get("compare_stmt_id") == stmt_id:
            state["compare_stmt_id"] = ""
    elif action == "edit":
        state["active_stmt_id"] = stmt_id
        state["active_scope"] = _scope_label_from_stmt_id(stmt_id)
        state["chat_draft"] = f"Improve only {stmt_id} in {state['active_scope']} for this job."


def _render_ai_rewrite_tab(context: dict[str, Any], state: dict[str, Any], current_pr) -> None:
    before, after = _current_result_scores(state, context)
    result = state.get("last_result")
    st.markdown(
        f"""
        <div class="sja-review-card">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div><h3>Great! Your score jumped<br>from {before / 10:.1f} to {after / 10:.1f}</h3></div>
            <div style="text-align:center;"><div class="sja-score">{after / 10:.1f}</div><b>{_score_label(after)}</b></div>
          </div>
          <hr>
          <h3>See What's Changed</h3>
          <ul>
            <li>Summary/work/project/skills edits are highlighted in the preview.</li>
            <li>{len(state.get("change_history", []))} statement change(s) applied.</li>
            <li>{len(state.get("confirmed_skills", []))} confirmed skill(s) considered.</li>
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    for suggestion in [
        "Use stronger action verbs for my latest experience",
        "Shorten my summary to remove filler words",
        "Remove skills not related to this job",
    ]:
        if st.button(suggestion, key=f"guided_suggestion_{suggestion}", use_container_width=True):
            state["chat_draft"] = suggestion
            st.rerun()
    changed = state.get("change_history", [])
    if changed:
        st.markdown("#### Changed Statements")
        for change in changed:
            stmt_id = str(change.get("stmt_id", ""))
            cols = st.columns([2.4, 1.15, 1.45], gap="small")
            cols[0].markdown(f'<div class="sja-change-row-label">{html.escape(stmt_id)}</div>', unsafe_allow_html=True)
            compare_label = "Hide" if state.get("compare_stmt_id") == stmt_id else "Compare"
            if cols[1].button(compare_label, key=f"compare_{stmt_id}", use_container_width=True):
                state["compare_stmt_id"] = "" if state.get("compare_stmt_id") == stmt_id else stmt_id
                st.rerun()
            if cols[2].button("Edit AI", key=f"edit_ai_{stmt_id}", use_container_width=True):
                state["active_stmt_id"] = stmt_id
                state["active_scope"] = _scope_label_from_stmt_id(stmt_id)
                state["chat_draft"] = f"Improve only {stmt_id} in {state['active_scope']} for this job."
                st.rerun()
            if state.get("compare_stmt_id") == stmt_id:
                st.markdown(_render_statement_compare_popover(change), unsafe_allow_html=True)
    if result and result.warnings:
        with st.expander("Warnings"):
            for warning in result.warnings:
                st.warning(warning)
    active_stmt_id = state.get("active_stmt_id") or ""
    scope = state.get("active_scope") or ""
    if active_stmt_id:
        st.info(f"Chat target: {active_stmt_id} ({scope})")
        if st.button("Clear target", key="clear_chat_stmt"):
            state["active_stmt_id"] = ""
            state["active_scope"] = ""
            st.rerun()
    elif scope:
        st.info(f"Chat scope: {scope}")
        if st.button("Clear scope", key="clear_chat_scope"):
            state["active_scope"] = ""
            st.rerun()
    instruction = st.text_area(
        "Tell me how you'd like to tweak your resume...",
        value=state.pop("chat_draft", ""),
        height=130,
        key="guided_chat_instruction",
    )
    if st.button("Edit With AI", type="primary", use_container_width=True, key="guided_chat_submit"):
        if state.get("active_stmt_id"):
            allowed = [state["active_stmt_id"]]
        else:
            allowed = _stmt_ids_for_scope(current_pr, state.get("active_scope")) if state.get("active_scope") else _selected_guided_stmt_ids(state, context["parse_result"])
        refine_route = _guided_llm_routes(state).get("refine", LLMTaskRoute())
        try:
            with st.spinner("Applying your AI edit and refreshing the resume..."):
                chat_result = _run_async(
                    refine_resume_with_instruction(
                        latex_source=state["current_latex"],
                        job_description=context["job"].description,
                        instruction=instruction,
                        job_keywords=context["job_keywords"],
                        confirmed_skills=state.get("confirmed_skills", []),
                        allowed_stmt_ids=allowed,
                        scope_label=state.get("active_scope") or "Selected resume sections",
                        llm_backend=refine_route.backend,
                        llm_model=refine_route.model,
                    )
                )
            _merge_guided_result(state, chat_result)
            state.setdefault("chat_messages", []).append({"user": instruction, "applied": len(chat_result.diff)})
            st.rerun()
        except Exception as exc:
            st.error(f"AI edit failed: {exc}")


def _scope_label_from_stmt_id(stmt_id: str) -> str:
    if stmt_id.startswith("summary"):
        return "Professional Summary"
    if stmt_id.startswith("skills"):
        return "Skills"
    if stmt_id.startswith("proj"):
        return "Projects"
    if stmt_id.startswith("work"):
        return "Work Experience"
    return "Selected Section"


def _stmt_ids_for_scope(parse_result, scope: str | None) -> list[str]:
    if not scope:
        return list(parse_result.stmt_index)
    scope = scope.lower()
    prefixes = []
    if "summary" in scope:
        prefixes = ["summary"]
    elif "skill" in scope:
        prefixes = ["skills"]
    elif "project" in scope:
        prefixes = ["proj"]
    elif "work" in scope or "experience" in scope:
        prefixes = ["work"]
    if not prefixes:
        return list(parse_result.stmt_index)
    return [stmt_id for stmt_id in parse_result.stmt_index if any(stmt_id.startswith(prefix) for prefix in prefixes)]


def _merge_guided_result(state: dict[str, Any], result) -> None:
    state["last_result"] = result
    state["current_latex"] = result.modified_latex or state["current_latex"]
    state["diff"] = result.diff
    by_id = {change.get("stmt_id"): change for change in state.get("change_history", [])}
    for change in result.diff:
        by_id[change.get("stmt_id")] = change
    state["change_history"] = list(by_id.values())


def _render_editor_tab(context: dict[str, Any], state: dict[str, Any], current_pr) -> None:
    st.info("Edits here apply only to this customized resume. Use Profile to update your base resume.")
    sections = _editable_section_stmt_ids(current_pr)
    selected_section = st.selectbox("Section", list(sections), key="guided_editor_section")
    stmt_ids = sections[selected_section]
    edited: dict[str, str] = {}
    for stmt_id in stmt_ids:
        span = current_pr.stmt_index[stmt_id]
        edited[stmt_id] = st.text_area(
            stmt_id,
            value=span.original_text,
            height=120 if not stmt_id.startswith("skills") else 90,
            key=f"guided_editor_{stmt_id}",
        )
    cols = st.columns(3)
    if cols[0].button("Save", type="primary", use_container_width=True, key="guided_editor_save"):
        changed = {
            stmt_id: value
            for stmt_id, value in edited.items()
            if value != current_pr.stmt_index[stmt_id].original_text
        }
        manual_result = apply_manual_statement_edits(
            latex_source=state["current_latex"],
            changes=changed,
            job_keywords=context["job_keywords"],
            confirmed_skills=state.get("confirmed_skills", []),
            allowed_stmt_ids=stmt_ids,
        )
        _merge_guided_result(state, manual_result)
        st.rerun()
    if cols[1].button("Cancel", use_container_width=True, key="guided_editor_cancel"):
        st.rerun()
    if cols[2].button("Restore original for this section", use_container_width=True, key="guided_editor_restore_section"):
        original_pr = parse(state["original_latex"], resume_id=current_pr.doc.resume_id)
        restore_changes = {
            stmt_id: original_pr.stmt_index[stmt_id].original_text
            for stmt_id in stmt_ids
            if stmt_id in original_pr.stmt_index
        }
        manual_result = apply_manual_statement_edits(
            latex_source=state["current_latex"],
            changes=restore_changes,
            job_keywords=context["job_keywords"],
            confirmed_skills=state.get("confirmed_skills", []),
            allowed_stmt_ids=stmt_ids,
        )
        _merge_guided_result(state, manual_result)
        st.rerun()
    with st.expander("Locked sections (read-only)"):
        for section in current_pr.doc.sections:
            if section.is_locked:
                st.markdown(f"**{section.display_name}**")
                values = []
                values.extend(stmt.text for stmt in section.statements)
                values.extend(line.text for line in section.skill_lines)
                values.extend(entry.header_text for entry in section.entries)
                st.write(_simple_delatex(" ".join(values)) if values else "Locked")


def _render_style_tab(context: dict[str, Any], state: dict[str, Any], current_pr) -> None:
    render = check_one_page(state["current_latex"])
    if render.overflow:
        st.error("This resume currently exceeds one page. Download and continue are blocked until it fits.")
    else:
        st.success("This resume passes the one-page gate.")
    st.metric("Pages", render.page_count or "n/a")
    st.metric("Visual overflow", "Yes" if render.visual_overflow else "No")
    actions = {
        "Auto-fit to one page": "Shorten only the changed statements enough to fit on one page while preserving all facts and important job keywords.",
        "Shorten recent edits": "Make the recently changed statements more concise without losing the core technical evidence.",
        "Tighten bullets": "Tighten long bullets and remove filler words while preserving metrics and tools.",
    }
    for label, instruction in actions.items():
        if st.button(label, use_container_width=True, key=f"style_{label}"):
            allowed = list(_changed_stmt_ids(state)) or _selected_guided_stmt_ids(state, context["parse_result"])
            refine_route = _guided_llm_routes(state).get("refine", LLMTaskRoute())
            with st.spinner(f"{label}..."):
                style_result = _run_async(
                    refine_resume_with_instruction(
                        latex_source=state["current_latex"],
                        job_description=context["job"].description,
                        instruction=instruction,
                        job_keywords=context["job_keywords"],
                        confirmed_skills=state.get("confirmed_skills", []),
                        allowed_stmt_ids=allowed,
                        scope_label="Changed statements",
                        llm_backend=refine_route.backend,
                        llm_model=refine_route.model,
                    )
                )
            _merge_guided_result(state, style_result)
            st.rerun()


def _render_guided_step3(context: dict[str, Any], state: dict[str, Any]) -> None:
    _apply_guided_query_action(state)
    current_pr = parse(state["current_latex"], resume_id=context["parse_result"].doc.resume_id)
    changed = _changed_stmt_ids(state)
    left, right = st.columns([1.45, 1], gap="large")
    with left:
        top_cols = st.columns([1, 1, 1])
        if top_cols[1].button("Fit to one page", type="primary", use_container_width=True):
            state["active_scope"] = "Changed statements"
            st.session_state["guided_chat_instruction"] = "Shorten changed statements to fit on one page."
        if top_cols[2].button("Restore Original", use_container_width=True):
            state["current_latex"] = state["original_latex"]
            state["diff"] = []
            state["change_history"] = []
            state["last_result"] = None
            st.rerun()
        st.markdown(
            _render_resume_html(current_pr, context["profile"], changed, context, state),
            unsafe_allow_html=True,
        )
        current_render = check_one_page(state["current_latex"])
        with st.expander("Clean PDF Preview"):
            if current_render.pdf_bytes and not current_render.overflow:
                st.markdown(_pdf_link(current_render.pdf_bytes), unsafe_allow_html=True)
            elif current_render.overflow:
                st.warning("PDF preview is blocked because the resume does not fit on one page.")
            else:
                st.info(current_render.log or current_render.error or "PDF preview unavailable.")
    with right:
        tab_ai, tab_editor, tab_style = st.tabs(["AI Rewrite", "Editor", "Style"])
        with tab_ai:
            _render_ai_rewrite_tab(context, state, current_pr)
        with tab_editor:
            _render_editor_tab(context, state, current_pr)
        with tab_style:
            _render_style_tab(context, state, current_pr)
    current_render = check_one_page(state["current_latex"])
    st.markdown('<div class="sja-bottom-bar">', unsafe_allow_html=True)
    back_col, down_col, continue_col = st.columns([1, 2, 2])
    if back_col.button("Back", use_container_width=True, key="guided_review_back"):
        state["step"] = 2
        st.rerun()
    if current_render.pdf_bytes and not current_render.overflow:
        down_col.download_button(
            "Download Resume",
            data=current_render.pdf_bytes,
            file_name=f"{Path(context['profile'].resume_filename or 'resume').stem}_customized.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        if continue_col.button("Continue to Autofill", type="primary", use_container_width=True):
            st.success("Resume approved. Return to the job application tab and upload/autofill from ApplyTeX ATS.")
    else:
        down_col.button("Download Resume", disabled=True, use_container_width=True)
        continue_col.button("Continue to Autofill", disabled=True, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_guided_customization(profile: CandidateProfile) -> None:
    _guided_css()
    context = _guided_context(profile)
    if context is None:
        return
    state = _guided_state(context)
    _render_guided_sidebar(profile, int(state.get("step", 1)))
    _guided_topbar(int(state.get("step", 1)))
    if state.get("step") == 1:
        _render_guided_step1(context, state)
    elif state.get("step") == 2:
        _render_guided_step2(context, state)
    else:
        _render_guided_step3(context, state)


st.set_page_config(
    page_title="ApplyTeX ATS Resume Lab",
    page_icon="",
    layout="wide",
)

query_profile_id = _query_value("profile_id")
if query_profile_id and not st.session_state.get("profile_username"):
    st.session_state["profile_username"] = _normalize_profile_username(query_profile_id)
    st.session_state["profile_show_setup"] = False

profile_username = _render_username_gate()
PROFILE_STORE.set_active_profile_id(profile_username)
stored_profile = PROFILE_STORE.get_candidate_profile(profile_username)
current_profile = (
    CandidateProfile.model_validate(st.session_state[PROFILE_PREFILL_KEY])
    if PROFILE_PREFILL_KEY in st.session_state
    else stored_profile
)
if current_profile.profile_id != profile_username:
    current_profile = current_profile.model_copy(update={"profile_id": profile_username})
_render_profile_identity(current_profile)
profile_missing = _profile_missing_required(current_profile)
show_profile_setup = bool(st.session_state.get("profile_show_setup", False))
if show_profile_setup or profile_missing:
    saved_profile = _render_profile_setup(current_profile)
    if saved_profile:
        remaining = _profile_missing_required(saved_profile)
        if remaining:
            st.warning(
                "Profile saved, but these required fields are still missing: "
                + ", ".join(remaining)
            )
        else:
            st.session_state["profile_show_setup"] = False
            st.rerun()
    st.stop()

if _query_value("mode") == "customize":
    _render_guided_customization(current_profile)
    st.stop()

st.title("ApplyTeX ATS Resume Lab")
st.caption("Local test UI for parsing, ATS scoring, and LaTeX resume optimization.")

with st.sidebar:
    st.header("Inputs")
    uploaded = st.file_uploader("Upload .tex resume", type=["tex"])
    use_sample = st.checkbox(
        "Use synthetic sample resume",
        value=uploaded is None,
    )
    st.caption("Deep JD analysis and optimization each expose their own model routing.")

if uploaded is not None:
    tex_source = uploaded.read().decode("utf-8")
elif use_sample:
    tex_source = _load_default(DEFAULT_RESUME)
else:
    tex_source = ""

jd_text = st.text_area(
    "Job description",
    value=_load_default(DEFAULT_JD),
    height=260,
    placeholder="Paste a job description here...",
)

if not tex_source:
    st.info("Upload a `.tex` file or enable the sample resume.")
    st.stop()

parse_result = parse(tex_source, resume_id="streamlit")
editable = extract_editable(parse_result)
full_resume = extract_full_resume(parse_result)
plain_resume = _build_plain_text(full_resume)
render = check_one_page(tex_source)

top = st.columns(4)
top[0].metric("Editable statements", len(parse_result.stmt_index))
top[1].metric("Editable words", editable["page_budget"]["estimated_word_count"])
top[2].metric("Pages", render.page_count or "n/a")
top[3].metric("Overflow", "Yes" if render.overflow else "No")

tabs = st.tabs(["Parse", "Fit Score", "Optimize", "Analysis", "Optimized PDF", "PDF"])

with tabs[0]:
    st.subheader("Parsed Resume")
    st.write("Editable sections:", ", ".join(editable["editable"].keys()))
    st.json(
        {
            "page_budget": editable["page_budget"],
            "sections": [
                {
                    "id": section.section_id,
                    "type": section.section_type.value,
                    "locked": section.is_locked,
                    "entries": len(section.entries),
                    "statements": len(section.statements),
                    "skill_lines": len(section.skill_lines),
                }
                for section in parse_result.doc.sections
            ],
        }
    )
    with st.expander("Editable JSON"):
        st.json(editable["editable"])
    with st.expander("Full extracted resume"):
        st.json(full_resume)

with tabs[1]:
    st.subheader("JD-Based Fit Analysis")
    if not jd_text.strip():
        st.info("Paste a job description to score.")
    else:
        st.caption(
            "Click once to extract JD requirements, compute fit against this resume, "
            "and list missing skills for confirmation."
        )
        analysis_mode = st.radio(
            "Analysis mode",
            options=["Fast local", "Deep LLM"],
            horizontal=True,
            help=(
                "Fast local avoids an LLM call and returns immediately. "
                "Deep LLM is slower but can catch nuanced JD wording."
            ),
            key="ats_analysis_mode",
        )
        deep_backend: str | None = None
        deep_model: str | None = None
        if analysis_mode == "Deep LLM":
            backend_options = list(MODEL_OPTIONS)
            configured_backend = backend_for_task("jd")
            backend_index = (
                backend_options.index(configured_backend)
                if configured_backend in backend_options
                else 0
            )
            deep_backend = st.selectbox(
                "Deep extraction backend",
                options=backend_options,
                index=backend_index,
                key="ats_deep_backend",
            )
            model_options = MODEL_OPTIONS[deep_backend]
            configured_model = model_for_backend_task(deep_backend, "jd")
            model_index = (
                model_options.index(configured_model)
                if configured_model in model_options
                else 0
            )
            deep_model = st.selectbox(
                "Deep extraction model",
                options=model_options,
                index=model_index,
                key="ats_deep_model",
            )
            st.caption(
                f"Deep extraction will call `{deep_backend}` / `{deep_model}`. "
                "If that backend is unavailable, the app falls back to fast local analysis."
            )
        if st.button("Analyze resume against JD", type="primary"):
            t0 = time.perf_counter()
            spinner = (
                "Extracting JD requirements with LLM..."
                if analysis_mode == "Deep LLM"
                else "Scanning JD locally..."
            )
            with st.spinner(spinner):
                if analysis_mode == "Deep LLM":
                    job_keywords = _run_async(
                        extract_job_keywords_with_fallback(
                            jd_text,
                            llm_backend=deep_backend,
                            llm_model=deep_model,
                        )
                    )
                else:
                    job_keywords = extract_job_keywords_fast(jd_text)
                baseline_ats = check_ats(plain_resume, job_keywords)
                screening = analyze_screening_fit(
                    plain_resume,
                    job_keywords,
                    baseline_ats,
                    editable_statement_count=len(parse_result.stmt_index),
                )
            st.session_state["jd_analysis"] = {
                "job_keywords": job_keywords,
                "baseline_ats": baseline_ats,
                "analysis_mode": analysis_mode,
                "llm_route": (
                    {"backend": deep_backend, "model": deep_model}
                    if analysis_mode == "Deep LLM"
                    else None
                ),
                "screening_analysis": screening.to_dict(),
                "latency_ms": {
                    "jd_analysis": round((time.perf_counter() - t0) * 1000, 1)
                },
            }
            st.session_state.pop("optimization_result", None)

        analysis = st.session_state.get("jd_analysis")
        if analysis:
            job_keywords = analysis["job_keywords"]
            if job_keywords.get("extraction_method") == "fast_local_fallback":
                route = analysis.get("llm_route") or {}
                route_label = " / ".join(
                    value for value in [route.get("backend"), route.get("model")] if value
                )
                route_prefix = f" via `{route_label}`" if route_label else ""
                st.warning(
                    f"Deep LLM extraction{route_prefix} failed, so this analysis used "
                    "the fast local fallback. "
                    f"LLM error: {job_keywords.get('llm_error', 'unknown')}"
                )
            baseline_ats = analysis["baseline_ats"]
            screening_analysis = analysis.get("screening_analysis", {})
            missing_required = list(baseline_ats.required_missing)
            missing_preferred = list(baseline_ats.preferred_missing)
            raw_missing = list(dict.fromkeys(missing_required + missing_preferred))
            candidates, theme_gaps = split_skill_confirmation_candidates(raw_missing)

            cols = st.columns(6)
            cols[0].metric("Submission Fit", f"{baseline_ats.score:.1f}/100")
            cols[1].metric("Required", f"{baseline_ats.required_score:.0f}%")
            cols[2].metric("Preferred", f"{baseline_ats.preferred_score:.0f}%")
            cols[3].metric("Keywords", f"{baseline_ats.keyword_score:.0f}%")
            cols[4].metric("Fit", screening_analysis.get("match_category", "n/a"))
            cols[5].metric("Latency", f"{analysis.get('latency_ms', {}).get('jd_analysis', 0):.0f} ms")

            if baseline_ats.score >= 80:
                st.success("Submission fit target met before optimization.")
            else:
                st.info(
                    "Optimization will auto-improve supported wording; confirm only skills you can defend."
                )

            st.markdown("#### Skills To Confirm Before Optimization")
            if candidates:
                confirmed = st.multiselect(
                    "Select only skills you can confidently discuss in an interview",
                    options=candidates,
                    default=[],
                    key="confirmed_skills_multiselect",
                )
                if missing_required:
                    st.caption("Required missing: " + ", ".join(missing_required))
                if missing_preferred:
                    st.caption("Preferred missing: " + ", ".join(missing_preferred))
                st.write("Selected:", confirmed or "None")
            else:
                st.success("No missing required/preferred skills found.")

            with st.expander("Extracted JD requirements"):
                st.json(analysis["job_keywords"])

            if theme_gaps:
                with st.expander("JD phrases/themes not treated as skills"):
                    st.write(
                        "These should be woven into summary or bullets only if truthful, "
                        "not added to the skills section."
                    )
                    for item in theme_gaps:
                        st.write(f"- {item}")

with tabs[2]:
    st.subheader("Optimization")
    st.write("Target submission fit score: **80+**")

    if not jd_text.strip():
        st.warning("Paste a job description first.")
    elif not st.session_state.get("jd_analysis"):
        st.info("First go to the Fit Score tab and click `Analyze resume against JD`.")
    else:
        st.markdown("#### Confirmed Skills")
        st.caption(
            "These come from the Fit Score tab selection. Go back there to change them."
        )
        st.write(st.session_state.get("confirmed_skills_multiselect", []) or "None")

    selected_strategy = DEFAULT_OPTIMIZER_STRATEGY
    selected_preset = "Ollama routed"
    llm_routes = {
        task: LLMTaskRoute(backend=backend, model=model)
        for task, (backend, model) in MODEL_PRESETS[selected_preset].items()
    }
    with st.expander("Advanced model routing"):
        use_langchain_reviewer = st.checkbox(
            "Use LangChain reviewer agent",
            value=False,
            help=(
                "Experimental: only the recruiter review loop uses LangChain. "
                "All edits still pass ApplyTeX ATS validators."
            ),
            key="use_langchain_reviewer",
        )
        selected_preset = st.selectbox(
            "Routing preset",
            options=["Ollama routed", "Groq routed", "Codex subscription routed", "Custom"],
            index=0,
            key="optimize_model_preset",
        )
        if selected_preset == "Custom":
            llm_routes = {}
            for task, label in (
                ("plan", "Planning"),
                ("diff", "LaTeX diff"),
                ("review", "Recruiter review"),
                ("refine", "Refinement"),
            ):
                cols = st.columns(2)
                backend = cols[0].selectbox(
                    f"{label} backend",
                    options=["ollama", "groq", "codex"],
                    index=0,
                    key=f"custom_{task}_backend",
                )
                model = cols[1].selectbox(
                    f"{label} model",
                    options=MODEL_OPTIONS[backend],
                    index=0,
                    key=f"custom_{task}_model_{backend}",
                )
                llm_routes[task] = LLMTaskRoute(backend=backend, model=model)
        else:
            llm_routes = {
                task: LLMTaskRoute(backend=backend, model=model)
                for task, (backend, model) in MODEL_PRESETS[selected_preset].items()
            }
            llm_routes.setdefault("review", llm_routes["diff"])
        st.json(
            {
                task: {"backend": route.backend, "model": route.model}
                for task, route in llm_routes.items()
            }
        )
        st.caption("Routing applies to this optimization run only.")

    if jd_text.strip() and st.session_state.get("jd_analysis") and st.button(
        "Regenerate optimized resume",
        type="primary",
    ):
        confirmed_skills = st.session_state.get("confirmed_skills_multiselect", [])
        t0 = time.perf_counter()
        with st.spinner("Running optimizer..."):
            result = _run_async(
                run_optimization_pipeline(
                    parse_result=parse_result,
                    job_description=jd_text,
                    confirmed_skills=confirmed_skills,
                    job_keywords=st.session_state["jd_analysis"]["job_keywords"],
                    llm_routes=llm_routes,
                    optimization_strategy=selected_strategy,
                    reviewer_backend=(
                        "langchain" if st.session_state.get("use_langchain_reviewer") else "custom"
                    ),
                )
            )
        optimize_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        st.session_state["optimization_result"] = result
        optimized_plain = plain_resume
        optimized_ats = result.ats_after or st.session_state["jd_analysis"]["baseline_ats"]
        if result.modified_latex:
            try:
                optimized_pr = parse(
                    result.modified_latex,
                    resume_id=parse_result.doc.resume_id,
                )
                optimized_plain = _build_plain_text(extract_full_resume(optimized_pr))
            except Exception:
                optimized_plain = plain_resume
        confirmed_screening = analyze_screening_fit(
            optimized_plain,
            st.session_state["jd_analysis"]["job_keywords"],
            optimized_ats,
            editable_statement_count=len(parse_result.stmt_index),
            confirmed_skills=confirmed_skills,
        )
        run_record = build_run_record(
            result,
            job_description=jd_text,
            confirmed_skills=confirmed_skills,
            resume_id=parse_result.doc.resume_id,
            screening_analysis=confirmed_screening.to_dict(),
            latency_ms={
                **st.session_state["jd_analysis"].get("latency_ms", {}),
                "optimization": optimize_latency_ms,
            },
        )
        run_record["llm_preset"] = selected_preset
        run_record["optimization_strategy"] = selected_strategy
        run_record["llm_routes"] = {
            task: {"backend": route.backend, "model": route.model}
            for task, route in llm_routes.items()
        }
        append_run_record(run_record, DEFAULT_RUN_LOG)
        st.session_state["run_record"] = run_record

    result = st.session_state.get("optimization_result")
    if result:
        cols = st.columns(4)
        after_score = result.ats_after.score if result.ats_after else 0
        cols[0].metric("Submission Fit", f"{after_score:.1f}/100")
        cols[1].metric("Target met", "Yes" if result.ats_target_met else "No")
        cols[2].metric("Changes", len(result.diff))
        page_label = (
            f"{result.page_count} (clipped)"
            if getattr(result, "visual_overflow", False)
            else str(result.page_count)
        )
        cols[3].metric("Pages", page_label)
        excluded = result.ats_after.excluded_unconfirmed_skills if result.ats_after else []
        if result.ats_target_met and not result.overflow and excluded:
            st.success("Strong fit, missing optional/unconfirmed tools.")
        elif result.ats_target_met and not result.overflow:
            st.success("Ready to submit.")
        elif result.confirmation_required_skills:
            st.warning(
                "Still below 80 because missing confirmed skills: "
                + ", ".join(result.confirmation_required_skills)
            )
        else:
            st.error(
                "Needs confirmed skills or stronger evidence."
            )

        st.subheader("Applied Statement Edits")
        if result.diff:
            for i, change in enumerate(result.diff):
                with st.expander(change["stmt_id"]):
                    st.caption(change.get("reason", ""))
                    stmt_id = change["stmt_id"]
                    st.text_area(
                        "Original",
                        change.get("original", ""),
                        height=100,
                        disabled=True,
                        key=f"diff_original_{i}_{stmt_id}",
                    )
                    st.text_area(
                        "New",
                        change.get("value", ""),
                        height=100,
                        disabled=True,
                        key=f"diff_new_{i}_{stmt_id}",
                    )
        else:
            st.info("No changes applied.")

        st.download_button(
            "Download optimized .tex",
            data=result.modified_latex,
            file_name="optimized_resume.tex",
            mime="text/plain",
        )
        if result.pdf_bytes and not result.overflow and result.ats_target_met:
            st.download_button(
                "Download optimized PDF",
                data=result.pdf_bytes,
                file_name="optimized_resume.pdf",
                mime="application/pdf",
            )

with tabs[3]:
    st.subheader("Optimization Report")
    st.caption(
        "A concise view of what changed, what improved, and what still blocks submission readiness."
    )

    record = st.session_state.get("run_record")
    if record:
        cols = st.columns(4)
        cols[0].metric("Fit Before", _fmt_score(record["score_before"]))
        cols[1].metric("Fit After", _fmt_score(record["score_after"]))
        cols[2].metric("Delta", _fmt_score(record["score_delta"], signed=True))
        cols[3].metric("Target", "Met" if record["ats_target_met"] else "Not met")

        st.markdown("#### Summary")
        st.write(record.get("report_summary", "Optimization run completed."))
        if record.get("recruiter_feedback"):
            st.markdown("#### Recruiter Review")
            for item in record["recruiter_feedback"]:
                st.write(f"- {item}")
            st.caption(
                f"Review iterations: {record.get('recruiter_iteration_count', 0)} "
                f"| Backend: {record.get('reviewer_backend', 'custom')}"
            )

        if record.get("confirmed_skills"):
            st.markdown("#### Confirmed Skills Used")
            st.write(", ".join(record["confirmed_skills"]))

        if record.get("excluded_unconfirmed_skills"):
            st.markdown("#### Unconfirmed Tools Not Added")
            st.write(", ".join(record["excluded_unconfirmed_skills"]))

        if record.get("trace_id"):
            st.caption(f"LangSmith trace: `{record['trace_id']}`")

        before_breakdown = (
            st.session_state.get("jd_analysis", {})
            .get("screening_analysis", {})
            .get("match_breakdown")
        )
        _display_category_deltas(before_breakdown, record.get("match_breakdown"))

        if record["newly_covered"]:
            st.markdown("#### Keywords/Skills Newly Covered")
            st.write(", ".join(record["newly_covered"]))

        remaining = record.get("remaining_gaps", {})
        remaining_items = (
            remaining.get("required", [])
            + remaining.get("preferred", [])
            + remaining.get("keywords", [])
        )
        if remaining_items:
            st.markdown("#### Remaining Gaps Preventing 80+")
            st.write(", ".join(list(dict.fromkeys(remaining_items))[:12]))

        if record.get("compacted_changes") or record.get("pruned_changes"):
            st.markdown("#### One-Page Fit Actions")
            if record.get("compacted_changes"):
                st.write(
                    "Compacted edits: "
                    + ", ".join(
                        str(c.get("stmt_id", "")) for c in record["compacted_changes"]
                    )
                )
            if record.get("pruned_changes"):
                st.write("Pruned edits: " + ", ".join(record["pruned_changes"]))

        st.markdown("#### Recommendations")
        for item in record["recommendations"]:
            st.write(f"- {item}")

        if record["top_score_leaks"]:
            st.markdown("#### Top Remaining Fit Leaks")
            st.dataframe(record["top_score_leaks"], width="stretch")

        with st.expander("Developer debug details"):
            if record.get("warnings"):
                st.markdown("##### Warnings")
                for warning in record["warnings"]:
                    st.write(f"- {warning}")
            if record.get("ats_after"):
                raw_score = record["ats_after"].get("raw_score")
                submission_score = record["ats_after"].get("submission_score")
                st.markdown("##### Raw vs Submission Score")
                st.json(
                    {
                        "raw_score": raw_score,
                        "submission_score": submission_score,
                        "excluded_unconfirmed_skills": record["ats_after"].get(
                            "excluded_unconfirmed_skills",
                            [],
                        ),
                    }
                )
            if record.get("stage_latencies_ms"):
                st.markdown("##### Pipeline stage latencies")
                st.dataframe(
                    [
                        {"stage": stage, "latency_ms": latency}
                        for stage, latency in record["stage_latencies_ms"].items()
                    ],
                    width="stretch",
                )
            st.json(record)
    else:
        st.info("Run an optimization to create the first analysis record.")

    history = load_run_records(DEFAULT_RUN_LOG, limit=20)
    st.markdown("#### Local Run History")
    st.caption(f"Saved to `{DEFAULT_RUN_LOG}`")
    if history:
        history_rows = [
            {
                "created_at": h["created_at"],
                "before": h["score_before"],
                "after": h["score_after"],
                "delta": h["score_delta"],
                "target_met": h["ats_target_met"],
                "strategy": h.get("optimization_strategy", ""),
                "preset": h.get("llm_preset", ""),
                "plan_model": (h.get("llm_routes", {}).get("plan", {}) or {}).get("model", ""),
                "diff_model": (h.get("llm_routes", {}).get("diff", {}) or {}).get("model", ""),
                "trace_id": h.get("trace_id", ""),
                "stage4_ms": h.get("stage_latencies_ms", {}).get("stage4_generate_latex_diffs"),
                "changes": h["change_count"],
                "rejected": h["rejected_change_count"],
                "fit": (
                    h.get("screening_analysis", {}) or {}
                ).get("match_category", ""),
                "analysis_ms": h.get("latency_ms", {}).get("jd_analysis"),
                "optimize_ms": h.get("latency_ms", {}).get("optimization"),
                "remaining_required": ", ".join(h["remaining_gaps"]["required"]),
            }
            for h in history
        ]
        st.dataframe(history_rows, width="stretch")
        with st.expander("Last 20 raw records"):
            st.json(history)
    else:
        st.info("No saved optimization runs yet.")

with tabs[4]:
    st.subheader("Optimized PDF Preview")
    optimized_result = st.session_state.get("optimization_result")
    if (
        optimized_result
        and optimized_result.pdf_bytes
        and not optimized_result.overflow
        and optimized_result.ats_target_met
    ):
        st.markdown(_pdf_link(optimized_result.pdf_bytes), unsafe_allow_html=True)
        st.download_button(
            "Download optimized PDF",
            data=optimized_result.pdf_bytes,
            file_name="optimized_resume.pdf",
            mime="application/pdf",
            key="optimized_pdf_tab_download",
        )
    elif optimized_result and optimized_result.overflow:
        if getattr(optimized_result, "visual_overflow", False):
            st.warning(
                "Optimized resume has content clipped below the bottom page "
                "boundary, so it is not previewed as submission-ready."
            )
        else:
            st.warning(
                f"Optimized resume exceeds one page "
                f"({optimized_result.page_count} page(s)), so it is not previewed "
                "as submission-ready."
            )
    elif optimized_result and not optimized_result.ats_target_met:
        st.warning(
            "Optimized PDF is hidden because the 80+ submission fit gate was not met."
        )
    elif optimized_result:
        st.info(
            "The optimized PDF is not available. Check optimizer warnings for a "
            "LaTeX render failure or missing pdflatex."
        )
    else:
        st.info("Run an optimization to preview the optimized PDF here.")

with tabs[5]:
    st.subheader("PDF Preview")
    if render.pdf_bytes and not render.overflow:
        st.markdown(_pdf_link(render.pdf_bytes), unsafe_allow_html=True)
        st.download_button(
            "Download original PDF",
            data=render.pdf_bytes,
            file_name="resume.pdf",
            mime="application/pdf",
        )
    elif render.estimated:
        st.info(render.log)
    else:
        st.error(render.error or "PDF render failed.")
