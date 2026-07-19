"""Resume-to-profile extraction helpers for ApplyTeX ATS autofill."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from latex_resume.extractor import extract_full_resume
from latex_resume.job_models import CandidateProfile, EducationProfile, WorkExperienceProfile
from latex_resume.parser import parse


_MONTHS: dict[str, str] = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}
_MONTH_PATTERN = (
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"January|February|March|April|June|July|August|September|October|November|December"
)
_DATE_PART_PATTERN = rf"(?:{_MONTH_PATTERN})[a-z]*\.?\s+\d{{4}}|\d{{4}}-\d{{2}}|Present"
_DATE_RANGE_PATTERN = rf"(?P<dates>(?P<start>{_DATE_PART_PATTERN})\s+[–—-]\s+(?P<end>{_DATE_PART_PATTERN}))"
_DEGREE_WORD_RE = re.compile(
    r"\b(master|bachelor|b\.?tech|m\.?s\.?|b\.?s\.?|ph\.?d|degree|science|engineering|computer)\b",
    re.I,
)
_ROLE_WORD_RE = re.compile(
    r"\b(?:engineer|developer|analyst|scientist|intern|manager|consultant|architect|"
    r"researcher|assistant|associate|specialist|lead|director|designer|administrator)\b",
    re.I,
)
_COMPANY_WORD_RE = re.compile(
    r"\b(?:inc|corp|corporation|llc|ltd|limited|company|technologies|technology|"
    r"systems|solutions|labs|prism|accenture|samsung|microsoft|google|amazon|meta|"
    r"university|school|institute)\b",
    re.I,
)
_DATE_HEADER_RE = re.compile(
    rf"(?P<head>.+?)\s+(?P<start>{_DATE_PART_PATTERN})\s+[–—-]\s+(?P<end>{_DATE_PART_PATTERN})$",
    re.I,
)


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_profile_facts_from_text(text: str) -> dict[str, object]:
    email = ""
    phone = ""
    linkedin = ""
    github = ""
    website = ""
    name = _first_nonempty_line(text)

    email_match = re.search(r"[\w.+\-]+@[\w.\-]+\.\w+", text)
    if email_match:
        email = email_match.group(0)
    phone_match = re.search(r"(?<!\w)(\+?\d[\d\s().\-]{7,}\d)(?!\w)", text)
    if phone_match:
        phone = " ".join(phone_match.group(1).split())
    urls = re.findall(r"https?://[^\s)\]}>,]+", text)
    linkedin = next((url for url in urls if "linkedin.com" in url), "")
    github = next((url for url in urls if "github.com" in url), "")
    website = next(
        (
            url
            for url in urls
            if url not in {linkedin, github}
            and "mailto:" not in url.casefold()
            and not url.casefold().startswith("tel:")
        ),
        "",
    )
    linkedin = normalize_profile_url(linkedin, default_scheme="https")
    github = normalize_profile_url(github, default_scheme="https")
    website = normalize_profile_url(website, default_scheme="https")

    facts: dict[str, object] = {
        "full_name": name.title() if name.isupper() else name,
        "email": email,
        "phone": phone,
        "linkedin_url": normalize_profile_url(linkedin, default_scheme="https"),
        "github_url": normalize_profile_url(github, default_scheme="https"),
        "portfolio_url": normalize_profile_url(website, default_scheme="https"),
    }

    educations = extract_education_profiles_from_text(text)
    if educations:
        facts["educations"] = educations
        facts["education"] = educations[0]
    work_experiences = extract_work_profiles_from_text(text)
    if work_experiences:
        facts["work_experiences"] = work_experiences
    skills = extract_skills_from_text(text)
    if skills:
        facts["skills"] = skills
    return {key: value for key, value in facts.items() if value}


def extract_profile_facts_from_tex(tex: str) -> dict[str, object]:
    parse_result = parse(tex, resume_id="profile_prefill")
    data = extract_full_resume(parse_result)
    personal = data.get("personal_info", {}) or {}
    facts: dict[str, object] = {
        "full_name": personal.get("name", ""),
        "email": personal.get("email", ""),
        "phone": personal.get("phone", ""),
        "linkedin_url": normalize_profile_url(str(personal.get("linkedin", "") or ""), default_scheme="https"),
        "github_url": normalize_profile_url(str(personal.get("github", "") or ""), default_scheme="https"),
        "portfolio_url": normalize_profile_url(str(personal.get("website", "") or ""), default_scheme="https"),
    }

    educations: list[EducationProfile] = []
    for education in data.get("education", []) or []:
        if not isinstance(education, dict):
            continue
        educations.append(education_profile_from_extracted_item(education))
    if educations:
        facts["educations"] = educations
        facts["education"] = educations[0]

    work_experiences: list[WorkExperienceProfile] = []
    for item in data.get("work_experience", []) or []:
        if isinstance(item, dict):
            work_experiences.append(work_profile_from_extracted_item(item))
    if work_experiences:
        facts["work_experiences"] = work_experiences

    skills = skills_from_extracted_data(data.get("skills"))
    if skills:
        facts["skills"] = skills
    return {key: value for key, value in facts.items() if value}


def profile_with_resume_prefill(
    profile: CandidateProfile,
    *,
    filename: str,
    data: bytes,
    overwrite: bool,
) -> tuple[CandidateProfile, list[str]]:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".tex":
        facts = extract_profile_facts_from_tex(data.decode("utf-8"))
    elif suffix == ".pdf":
        facts = extract_profile_facts_from_text(extract_pdf_text(data))
    else:
        raise ValueError("Upload a `.tex` or `.pdf` resume for profile extraction.")

    updated = profile.model_copy(deep=True)
    applied: list[str] = []
    for key, value in facts.items():
        if _set_profile_path(updated, key, value, overwrite):
            applied.append(key)
    if updated.full_name and (not updated.first_name or not updated.last_name):
        first_name, last_name = split_resume_name(updated.full_name)
        if first_name and (overwrite or not updated.first_name):
            updated.first_name = first_name
            applied.append("first_name")
        if last_name and (overwrite or not updated.last_name):
            updated.last_name = last_name
            applied.append("last_name")
    if updated.educations and (overwrite or not updated.education.school):
        updated.education = updated.educations[0]
        if "education" not in applied:
            applied.append("education")
    return updated, applied


def extract_education_profiles_from_text(text: str) -> list[EducationProfile]:
    lines = _section_lines(
        text,
        ("Education",),
        ("Experience", "Work Experience", "Skills", "Projects", "Publications", "Certifications"),
    )
    if not lines:
        lines = _clean_pdf_lines(text)
    educations: list[EducationProfile] = []
    degree_re = re.compile(
        r"(?P<degree>.+?)\s+(?:CGPA|GPA):\s*"
        r"(?P<gpa>[0-9.]+\s*/\s*[0-9.]+|[0-9.]+)",
        re.I,
    )
    school_re = re.compile(
        rf"(?P<school>.+?)(?:\s+-\s+(?P<location>.+?))?\s+{_DATE_RANGE_PATTERN}$",
        re.I,
    )
    index = 0
    while index < len(lines):
        degree_match = degree_re.search(lines[index])
        if not degree_match or not _DEGREE_WORD_RE.search(degree_match.group("degree")):
            index += 1
            continue
        school = ""
        start_date = ""
        end_date = ""
        if index + 1 < len(lines):
            school_match = school_re.search(lines[index + 1])
            if school_match:
                school = school_match.group("school").strip()
                start_date, end_date = _split_date_range(school_match.group("dates"))
                index += 1
        degree = degree_match.group("degree").strip()
        end_date = normalize_profile_date(end_date)
        major = major_from_degree(degree)
        educations.append(
            EducationProfile(
                school=school,
                degree=degree,
                degree_level=degree_level_from_degree(degree),
                major=major,
                field_of_study_candidates=field_of_study_candidates(school, degree, major),
                start_date=normalize_profile_date(start_date),
                end_date=end_date,
                currently_studying=end_date.casefold() == "present",
                gpa=_clean_gpa(degree_match.group("gpa")),
            )
        )
        index += 1
    return educations


def extract_work_profiles_from_text(text: str) -> list[WorkExperienceProfile]:
    lines = _section_lines(
        text,
        ("Experience", "Work Experience"),
        ("Skills", "Projects", "Education", "Publications", "Certifications"),
    )
    if not lines:
        lines = _clean_pdf_lines(text)
    work_entries: list[WorkExperienceProfile] = []
    index = 0
    while index < len(lines):
        header_match = _DATE_HEADER_RE.search(lines[index])
        if not header_match:
            index += 1
            continue
        company = header_match.group("head").strip()
        start_date = normalize_profile_date(header_match.group("start"))
        end_date = normalize_profile_date(header_match.group("end"))
        role = ""
        location = ""
        index += 1
        if index < len(lines) and not lines[index].startswith("•") and not _DATE_HEADER_RE.search(lines[index]):
            role, location = _split_role_location(lines[index])
            index += 1
        bullets: list[str] = []
        current_bullet = ""
        while index < len(lines) and not _DATE_HEADER_RE.search(lines[index]):
            line = lines[index]
            if line.startswith("•"):
                if current_bullet:
                    bullets.append(_clean_bullet_spacing(current_bullet))
                current_bullet = line.lstrip("•").strip()
            elif current_bullet:
                current_bullet += " " + line
            index += 1
        if current_bullet:
            bullets.append(_clean_bullet_spacing(current_bullet))
        if company or role or bullets:
            work_entries.append(
                WorkExperienceProfile(
                    job_title=role,
                    company=company,
                    job_type=job_type_from_role(role),
                    location=location,
                    start_date=start_date,
                    end_date=end_date,
                    currently_working=end_date.casefold() == "present",
                    summary=_summarize_bullets(bullets),
                    bullets=bullets,
                )
            )
    return work_entries


def extract_skills_from_text(text: str) -> list[str]:
    lines = _section_lines(
        text,
        ("Skills",),
        ("Projects", "Experience", "Work Experience", "Education", "Publications", "Certifications"),
    )
    if not lines:
        return []
    skills: list[str] = []
    for line in lines:
        cleaned = re.sub(r"^[A-Za-z][A-Za-z /&]+:\s*", "", line).strip()
        skills.extend(split_skill_tokens(cleaned))
    return list(dict.fromkeys(skills))


def split_skill_tokens(text: str) -> list[str]:
    """Split comma/semicolon skill lists without breaking parenthetical groups."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
        elif char in ",;" and depth == 0:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def normalize_profile_url(value: str, *, default_scheme: str = "https") -> str:
    cleaned = value.strip()
    if not cleaned or cleaned.casefold().startswith("tel:") or cleaned.casefold().startswith("mailto:"):
        return ""
    if cleaned.startswith("//"):
        return f"{default_scheme}:{cleaned}"
    if re.match(r"^[\w.-]+\.[a-z]{2,}/", cleaned, flags=re.I):
        return f"{default_scheme}://{cleaned}"
    if re.match(r"^(?:www\.)?(linkedin|github)\.com/", cleaned, flags=re.I):
        return f"{default_scheme}://{cleaned}"
    return cleaned


def skills_from_extracted_data(raw_skills: object) -> list[str]:
    if isinstance(raw_skills, dict):
        values = raw_skills.values()
    elif isinstance(raw_skills, list):
        values = raw_skills
    else:
        return []
    skills: list[str] = []
    for value in values:
        chunks = value if isinstance(value, list) else split_skill_tokens(str(value))
        skills.extend(
            chunk.strip().lstrip(":").strip()
            for chunk in chunks
            if chunk and chunk.strip().lstrip(":").strip()
        )
    return list(dict.fromkeys(skills))


def major_from_degree(degree: str) -> str:
    if " in " in degree:
        return degree.rsplit(" in ", 1)[-1].strip()
    if "," in degree:
        return degree.rsplit(",", 1)[-1].strip()
    return ""


def degree_level_from_degree(degree: str) -> str:
    """Return a conservative US application degree code without rewriting the resume."""
    normalized = re.sub(r"[^a-z0-9]+", " ", degree.casefold()).strip()
    if re.search(r"\b(ph d|phd|doctor(?:ate|al)?)\b", normalized):
        return "PhD"
    if re.search(r"\b(mba|master of business administration)\b", normalized):
        return "MBA"
    if re.search(r"\b(m s|ms|master|masters)\b", normalized):
        return "MS"
    if re.search(r"\b(m a|ma)\b", normalized):
        return "MA"
    if re.search(r"\b(b tech|btech|b e|be|b s|bs|bachelor|bachelors)\b", normalized):
        return "BS"
    if re.search(r"\b(b a|ba)\b", normalized):
        return "BA"
    if re.search(r"\b(a s|as|associate of science)\b", normalized):
        return "AS"
    if re.search(r"\b(a a|aa|associate of arts)\b", normalized):
        return "AA"
    return ""


def field_of_study_candidates(
    school: str,
    degree: str,
    major: str,
) -> list[str]:
    """Build ordered, exact-only Workday search candidates from resume facts."""
    school_key = school.casefold()
    source = major or major_from_degree(degree)
    source_key = source.casefold()
    candidates: list[str] = []
    if "university of houston" in school_key and "data science" in source_key:
        candidates.extend(["Data Science", "Computer Engineering"])
    elif "amrita" in school_key and "computer" in source_key:
        candidates.extend(["Computer Science", "Computer Engineering"])
    else:
        if "data science" in source_key:
            candidates.append("Data Science")
        if "computer science" in source_key:
            candidates.extend(["Computer Science", "Computer and Information Science"])
        if "computer engineering" in source_key or "computer science and engineering" in source_key:
            candidates.append("Computer Engineering")
        if source and not candidates:
            candidates.append(source)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def job_type_from_role(role: str) -> str:
    if "intern" in role.casefold():
        return "Internship"
    if role:
        return "Full-time"
    return ""


def education_profile_from_extracted_item(item: dict[str, object]) -> EducationProfile:
    raw_degree = str(item.get("degree", "") or "")
    raw_school = str(item.get("institution", "") or item.get("school", "") or "")
    area = str(item.get("area", "") or "")
    school, degree = _normalize_school_degree(raw_school, raw_degree)
    end_date = normalize_profile_date(str(item.get("end_date", "") or ""))
    grad_month, grad_year = _graduation_parts(end_date)
    major = area or major_from_degree(degree)
    return EducationProfile(
        school=school,
        degree=degree,
        degree_level=degree_level_from_degree(degree),
        major=major,
        field_of_study_candidates=field_of_study_candidates(school, degree, major),
        start_date=normalize_profile_date(str(item.get("start_date", "") or "")),
        end_date=end_date,
        currently_studying=end_date.casefold() == "present",
        graduation_month=grad_month,
        graduation_year=grad_year,
        gpa=_clean_gpa(str(item.get("gpa", "") or "")),
    )


def work_profile_from_extracted_item(item: dict[str, object]) -> WorkExperienceProfile:
    role = str(item.get("role", "") or "")
    company = str(item.get("company", "") or "")
    location = str(item.get("location", "") or "")
    start_date = normalize_profile_date(str(item.get("start_date", "") or ""))
    end_date = normalize_profile_date(str(item.get("end_date", "") or ""))
    header = str(item.get("header", "") or "")
    if header and not (role and company):
        match = re.match(
            rf"(?P<role>.+?)\s+(?P<start>{_DATE_PART_PATTERN})\s+[–—-]\s+"
            rf"(?P<end>{_DATE_PART_PATTERN})\s+(?P<company>.+)",
            header,
            flags=re.I,
        )
        if match:
            role = role or match.group("role").strip()
            start_date = start_date or normalize_profile_date(match.group("start"))
            end_date = end_date or normalize_profile_date(match.group("end"))
            company_tail = match.group("company").strip()
            if ", " in company_tail:
                company, location = company_tail.split(", ", 1)
            else:
                company = company or company_tail
    company, role = _normalize_company_role(company, role)
    bullets = [_clean_bullet_spacing(str(bullet)) for bullet in item.get("bullets", []) or []]
    return WorkExperienceProfile(
        job_title=role,
        company=company,
        job_type=job_type_from_role(role),
        location=location,
        start_date=start_date,
        end_date=end_date,
        currently_working=end_date.casefold() == "present",
        summary=_summarize_bullets(bullets),
        bullets=bullets,
    )


def _normalize_school_degree(raw_school: str, raw_degree: str) -> tuple[str, str]:
    school = " ".join(raw_school.split()).strip(" ,;")
    degree = " ".join(raw_degree.split()).strip(" ,;")
    if _looks_like_degree(school) and not _looks_like_degree(degree):
        school, degree = degree, school
    return school, degree


def _looks_like_degree(value: str) -> bool:
    lowered = value.casefold()
    school_markers = (
        "university",
        "college",
        "institute",
        "school of",
        "academy",
    )
    if any(marker in lowered for marker in school_markers):
        return False
    degree_markers = (
        "bachelor",
        "master",
        "b.tech",
        "btech",
        "b.s.",
        "bs ",
        "m.s.",
        "ms ",
        "ph.d",
        "degree",
        "science",
        "engineering",
        "computer",
        "artificial intelligence",
        "data science",
    )
    return any(marker in lowered for marker in degree_markers)


def _normalize_company_role(raw_company: str, raw_role: str) -> tuple[str, str]:
    company = " ".join(raw_company.split()).strip(" ,;")
    role = " ".join(raw_role.split()).strip(" ,;")
    company_is_role = _looks_like_role(company)
    role_is_role = _looks_like_role(role)
    company_from_role = _company_from_compound_label(role)

    if company_is_role and company_from_role:
        return company_from_role, company
    if company_is_role and role and not role_is_role:
        return role, company
    if not company and company_from_role:
        return company_from_role, role
    return company, role


def _looks_like_role(value: str) -> bool:
    if not value:
        return False
    return bool(_ROLE_WORD_RE.search(value))


def _company_from_compound_label(value: str) -> str:
    if not value:
        return ""
    for separator in (" – ", " — ", " - ", " | "):
        if separator not in value:
            continue
        left, _right = value.split(separator, 1)
        candidate = left.strip(" ,;")
        if candidate and not _looks_like_role(candidate):
            return candidate
    if _COMPANY_WORD_RE.search(value) and not _looks_like_role(value):
        return value.strip(" ,;")
    return ""


def normalize_profile_date(value: str) -> str:
    cleaned = " ".join(value.replace(",", " ").split()).strip()
    if not cleaned:
        return ""
    if cleaned.casefold() == "present":
        return "Present"
    if re.fullmatch(r"\d{4}-\d{2}", cleaned):
        return cleaned
    match = re.match(r"(?P<month>[A-Za-z]+)\.?\s+(?P<year>\d{4})$", cleaned)
    if not match:
        return cleaned
    month = _MONTHS.get(match.group("month").casefold().rstrip("."))
    return f"{match.group('year')}-{month}" if month else cleaned


def split_resume_name(name: str) -> tuple[str, str]:
    parts = [part for part in name.split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0].title(), ""
    return parts[0].title(), parts[-1].title()


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = " ".join(line.split()).strip(" |")
        if cleaned and "@" not in cleaned and not cleaned.lower().startswith("http"):
            return cleaned
    return ""


def _clean_pdf_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _section_lines(text: str, start_headings: tuple[str, ...], stop_headings: tuple[str, ...]) -> list[str]:
    lines = _clean_pdf_lines(text)
    start = None
    start_names = {heading.casefold() for heading in start_headings}
    for index, line in enumerate(lines):
        if line.casefold() in start_names:
            start = index + 1
            break
    if start is None:
        return []
    stop = len(lines)
    stop_names = {heading.casefold() for heading in stop_headings}
    for index in range(start, len(lines)):
        if lines[index].casefold() in stop_names:
            stop = index
            break
    return lines[start:stop]


def _split_date_range(text: str) -> tuple[str, str]:
    normalized = text.replace("–", "-").replace("—", "-")
    parts = re.split(r"\s+-\s+", normalized, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


def _split_role_location(line: str) -> tuple[str, str]:
    match = re.match(r"(?P<role>.+?)\s+(?P<location>[A-Z][A-Za-z .-]+,\s*[A-Za-z .-]+)$", line)
    if match:
        return match.group("role").strip(), match.group("location").strip()
    return line.strip(), ""


def _clean_gpa(value: str) -> str:
    cleaned = " ".join(value.replace("CGPA:", "").replace("GPA:", "").split()).strip()
    return cleaned


def _clean_bullet_spacing(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", cleaned)
    cleaned = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", cleaned)
    cleaned = re.sub(r"(?<=[a-z])(?=\d)", " ", cleaned)
    return cleaned


def _summarize_bullets(bullets: list[str]) -> str:
    if not bullets:
        return ""
    first = bullets[0].rstrip(".")
    return first if len(first) <= 220 else first[:217].rstrip() + "..."


def _graduation_parts(end_date: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-\d{2}", end_date):
        return "", ""
    year, month = end_date.split("-", 1)
    return month, year


def _set_profile_path(profile: CandidateProfile, key: str, value: object, overwrite: bool) -> bool:
    if not value:
        return False
    target: object = profile
    parts = key.split(".")
    for part in parts[:-1]:
        target = getattr(target, part)
    current = getattr(target, parts[-1])
    if not overwrite and not _profile_value_is_empty(parts[-1], current):
        return False
    setattr(target, parts[-1], value)
    return True


def _profile_value_is_empty(key: str, value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        if not value:
            return True
        if key == "educations":
            return all(not edu.school.strip() and not edu.degree.strip() for edu in value)
        if key == "work_experiences":
            return all(
                not work.company.strip() and not work.job_title.strip() and not work.bullets
                for work in value
            )
        if key == "skills":
            return not value
    return False
