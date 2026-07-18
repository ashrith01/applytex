"""Project evidence library for Tailor Studio.

The module keeps project handling deterministic and evidence-grounded:
resume projects can be selected for the tailored PDF, while public GitHub
projects are recommendations until a later template-insertion phase.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from latex_resume.extractor import extract_full_resume
from latex_resume.job_models import ProjectRecommendation, ProjectRecord, ProjectSource, utc_now
from latex_resume.models import ParseResult, SectionType
from latex_resume.parser import parse
from latex_resume.project_scoring import score_project_credibility


_TECH_TERMS: tuple[str, ...] = (
    "ai",
    "api",
    "aws",
    "azure",
    "docker",
    "fastapi",
    "flask",
    "gcp",
    "github",
    "langchain",
    "llm",
    "machine learning",
    "ml",
    "nlp",
    "postgres",
    "python",
    "pytorch",
    "rag",
    "react",
    "sql",
    "tensorflow",
    "typescript",
    "vector",
)


@dataclass
class ProjectFilterResult:
    """Result of removing unselected project entries from LaTeX."""

    latex_source: str
    warnings: list[str] = field(default_factory=list)
    removed_entry_ids: list[str] = field(default_factory=list)


class GitHubProjectClient:
    """Small unauthenticated GitHub public-repo client."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def fetch_public_projects(self, *, profile_id: str, github_url: str) -> list[ProjectRecord]:
        username = github_username_from_url(github_url)
        if not username:
            raise ValueError("Profile GitHub URL must point to github.com/<username>.")
        if self._client is not None:
            return await self._fetch_with_client(self._client, profile_id=profile_id, username=username)
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            timeout=12.0,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ApplyTeX-local"},
        ) as client:
            return await self._fetch_with_client(client, profile_id=profile_id, username=username)

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        *,
        profile_id: str,
        username: str,
    ) -> list[ProjectRecord]:
        repos_response = await client.get(
            f"/users/{username}/repos",
            params={"per_page": 100, "sort": "updated"},
        )
        repos_response.raise_for_status()
        projects: list[ProjectRecord] = []
        for repo in repos_response.json():
            if not isinstance(repo, Mapping):
                continue
            if repo.get("archived") or repo.get("fork"):
                continue
            owner = str(repo.get("owner", {}).get("login") or username)
            name = str(repo.get("name") or "").strip()
            if not name:
                continue
            languages = await self._fetch_languages(client, owner, name)
            readme_excerpt = await self._fetch_readme_excerpt(client, owner, name)
            topics = [
                str(topic)
                for topic in repo.get("topics", [])
                if str(topic).strip()
            ]
            full_name = str(repo.get("full_name") or f"{owner}/{name}")
            description = str(repo.get("description") or "").strip()
            projects.append(
                ProjectRecord(
                    project_id=f"{profile_id}:github:{full_name.casefold()}",
                    profile_id=profile_id,
                    source=ProjectSource.GITHUB,
                    title=name,
                    url=str(repo.get("html_url") or f"https://github.com/{full_name}"),
                    description=description,
                    languages=languages,
                    topics=topics,
                    readme_excerpt=readme_excerpt,
                    credibility_score=None,
                    updated_at=utc_now(),
                )
            )
        return projects

    async def _fetch_languages(self, client: httpx.AsyncClient, owner: str, name: str) -> list[str]:
        try:
            response = await client.get(f"/repos/{owner}/{name}/languages")
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        languages = response.json()
        if not isinstance(languages, Mapping):
            return []
        return [str(language) for language in languages.keys() if str(language).strip()]

    async def _fetch_readme_excerpt(self, client: httpx.AsyncClient, owner: str, name: str) -> str:
        try:
            response = await client.get(f"/repos/{owner}/{name}/readme")
            response.raise_for_status()
        except httpx.HTTPError:
            return ""
        payload = response.json()
        if not isinstance(payload, Mapping):
            return ""
        content = str(payload.get("content") or "")
        encoding = str(payload.get("encoding") or "")
        if encoding != "base64" or not content:
            return ""
        try:
            decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
        except (ValueError, TypeError):
            return ""
        return compact_text(strip_markdown(decoded), 500)


def github_username_from_url(url: str) -> str:
    """Extract a GitHub username from common profile URL forms."""
    cleaned = url.strip()
    if not cleaned:
        return ""
    if not re.match(r"^https?://", cleaned, flags=re.I):
        cleaned = f"https://{cleaned}"
    try:
        from urllib.parse import urlparse

        parsed = urlparse(cleaned)
    except ValueError:
        return ""
    if parsed.netloc.casefold().removeprefix("www.") != "github.com":
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    username = parts[0]
    if username in {"orgs", "organizations", "topics", "marketplace"}:
        return ""
    return username


def build_resume_project_records(profile_id: str, parse_result: ParseResult) -> list[ProjectRecord]:
    """Build stable project records from parsed LaTeX resume projects."""
    resume_data = extract_full_resume(parse_result)
    raw_projects = resume_data.get("projects", [])
    if not isinstance(raw_projects, list):
        return []
    project_section = next(
        (section for section in parse_result.doc.sections if section.section_type is SectionType.PROJECTS),
        None,
    )
    if project_section is None:
        return []
    credibility_by_name = {
        score.name.casefold(): score
        for score in score_project_credibility(resume_data).projects
    }
    records: list[ProjectRecord] = []
    for index, entry in enumerate(project_section.entries):
        raw = raw_projects[index] if index < len(raw_projects) and isinstance(raw_projects[index], Mapping) else {}
        title = str(raw.get("title") or raw.get("name") or raw.get("header") or f"Project {index + 1}").strip()
        bullets = [str(item).strip() for item in raw.get("bullets", []) if str(item).strip()]
        urls = [str(item).strip() for item in raw.get("urls", []) if str(item).strip()]
        text = " ".join([title, str(raw.get("venue") or ""), " ".join(bullets)])
        credibility = credibility_by_name.get(title.casefold())
        records.append(
            ProjectRecord(
                project_id=f"{profile_id}:resume:{entry.entry_id}",
                profile_id=profile_id,
                source=ProjectSource.RESUME,
                title=title,
                url=urls[0] if urls else "",
                description=" ".join(bullets[:2]),
                languages=term_hits(text, _TECH_TERMS),
                topics=[],
                readme_excerpt="",
                credibility_score=credibility.score if credibility else None,
                resume_entry_id=entry.entry_id,
                statement_ids=[statement.stmt_id for statement in entry.statements],
                updated_at=utc_now(),
            )
        )
    return records


def rank_project_records(
    projects: list[ProjectRecord],
    job_keywords: Mapping[str, Any],
    *,
    selected_project_ids: list[str] | None = None,
) -> list[ProjectRecommendation]:
    """Rank project records against JD keywords with resume projects preferred."""
    required = [str(item) for item in job_keywords.get("required_skills", []) if str(item).strip()]
    preferred = [str(item) for item in job_keywords.get("preferred_skills", []) if str(item).strip()]
    keywords = [str(item) for item in job_keywords.get("keywords", []) if str(item).strip()]
    ranked: list[ProjectRecommendation] = []
    selected = set(selected_project_ids or [])
    for project in projects:
        text = project_text(project)
        required_hits = matched_terms(required, text)
        preferred_hits = matched_terms(preferred, text)
        keyword_hits = matched_terms(keywords, text)
        required_score = (len(required_hits) / len(required) * 42.0) if required else 18.0
        preferred_score = (len(preferred_hits) / len(preferred) * 24.0) if preferred else 10.0
        keyword_score = (len(keyword_hits) / len(keywords) * 14.0) if keywords else 5.0
        evidence_score = min(12.0, len(project.languages) * 2.0 + len(project.topics) * 1.0)
        credibility_score = (project.credibility_score or 0.0) * 0.09
        resume_boost = 8.0 if project.source is ProjectSource.RESUME else 0.0
        link_boost = 3.0 if project.url else 0.0
        fit_score = round(
            min(
                100.0,
                required_score
                + preferred_score
                + keyword_score
                + evidence_score
                + credibility_score
                + resume_boost
                + link_boost,
            ),
            1,
        )
        hits = unique_preserve(required_hits + preferred_hits + keyword_hits)
        ranked.append(
            ProjectRecommendation(
                project=project,
                fit_score=fit_score,
                matched_terms=hits,
                summary_points=project_summary_points(project, hits),
                default_selected=project.project_id in selected,
                selectable=project.source is ProjectSource.RESUME,
                rationale=project_rationale(project, hits),
            )
        )
    ranked.sort(key=lambda item: (item.fit_score, item.project.source == ProjectSource.RESUME), reverse=True)
    return ranked


def default_selected_project_ids(recommendations: list[ProjectRecommendation], limit: int = 2) -> list[str]:
    """Return top resume-backed project IDs for default selection."""
    return [
        item.project.project_id
        for item in recommendations
        if item.project.source is ProjectSource.RESUME
    ][:limit]


def filter_latex_projects(
    latex_source: str,
    *,
    selected_resume_entry_ids: set[str],
) -> ProjectFilterResult:
    """Remove unselected list-backed project entries from LaTeX."""
    parse_result = parse(latex_source, resume_id="project_filter")
    project_section = next(
        (section for section in parse_result.doc.sections if section.section_type is SectionType.PROJECTS),
        None,
    )
    if project_section is None:
        return ProjectFilterResult(
            latex_source=latex_source,
            warnings=["No Projects section was found, so project filtering was skipped."],
        )
    replacements: list[tuple[int, int, str]] = []
    warnings: list[str] = []
    removed: list[str] = []
    available_entry_ids = {entry.entry_id for entry in project_section.entries}
    missing = sorted(selected_resume_entry_ids - available_entry_ids)
    if missing:
        warnings.append("Some selected projects were no longer present in the resume source: " + ", ".join(missing))
    for entry in project_section.entries:
        if entry.entry_id in selected_resume_entry_ids:
            continue
        if not entry.can_remove or entry.tex_start is None or entry.tex_end is None:
            warnings.append(f"Project {entry.entry_id} could not be removed safely; unsupported project layout.")
            continue
        replacements.append((entry.tex_start, entry.tex_end, ""))
        removed.append(entry.entry_id)
    if not replacements:
        return ProjectFilterResult(latex_source=latex_source, warnings=warnings, removed_entry_ids=removed)
    filtered = latex_source
    for start, end, value in sorted(replacements, key=lambda item: item[0], reverse=True):
        filtered = filtered[:start] + value + filtered[end:]
    return ProjectFilterResult(latex_source=filtered, warnings=warnings, removed_entry_ids=removed)


def allowed_statement_ids_after_project_filter(parse_result: ParseResult) -> list[str]:
    """Allow all statements in a filtered resume; removed project IDs are absent."""
    return list(parse_result.stmt_index.keys())


def project_text(project: ProjectRecord) -> str:
    return " ".join(
        [
            project.title,
            project.description,
            " ".join(project.languages),
            " ".join(project.topics),
            project.readme_excerpt,
        ]
    )


def matched_terms(terms: list[str], text: str) -> list[str]:
    normalized = normalize_text(text)
    return [term for term in terms if term_matches(term, normalized)]


def term_hits(text: str, terms: tuple[str, ...]) -> list[str]:
    normalized = normalize_text(text)
    return [term for term in terms if term_matches(term, normalized)]


def term_matches(term: str, normalized_text: str) -> bool:
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False
    if normalized_term in normalized_text:
        return True
    tokens = [token for token in normalized_term.split() if token]
    return bool(tokens) and all(token in normalized_text for token in tokens)


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", value.casefold()).strip()


def project_summary_points(project: ProjectRecord, hits: list[str]) -> list[str]:
    what = first_sentence(project.description) or first_sentence(project.readme_excerpt)
    if not what:
        what = f"{project.title} is available as {project.source.value} project evidence."
    evidence_parts = []
    if hits:
        evidence_parts.append("matches " + ", ".join(hits[:5]))
    if project.languages:
        evidence_parts.append("stack includes " + ", ".join(project.languages[:4]))
    if project.credibility_score is not None:
        evidence_parts.append(f"resume evidence score {project.credibility_score:.0f}/100")
    if project.url:
        evidence_parts.append("has a verification link")
    evidence = "; ".join(evidence_parts) or "needs more visible JD evidence"
    return [what, evidence]


def project_rationale(project: ProjectRecord, hits: list[str]) -> str:
    if project.source is ProjectSource.GITHUB:
        return "GitHub evidence only in v1; use it to decide whether to update the source resume later."
    if hits:
        return "Resume project overlaps with the job description."
    return "Resume project is available, but has limited keyword overlap with this JD."


def unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def strip_markdown(value: str) -> str:
    value = re.sub(r"```.*?```", " ", value, flags=re.DOTALL)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[#*_>`~|-]+", " ", value)
    return " ".join(value.split())


def compact_text(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def first_sentence(value: str) -> str:
    cleaned = compact_text(value, 220)
    if not cleaned:
        return ""
    match = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    return match.group(1).strip() if match else cleaned
