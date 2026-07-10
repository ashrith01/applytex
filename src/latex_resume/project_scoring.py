"""Deterministic project credibility scoring from parsed resume content.

This module intentionally does not fetch external profile data. It scores only
what the candidate already wrote in the resume, so the result can be used as
evidence for tailoring without adding unsupported claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

ProjectBand = Literal["strong", "good", "basic", "weak"]


_TECH_TERMS: frozenset[str] = frozenset(
    {
        "api",
        "aws",
        "azure",
        "docker",
        "fastapi",
        "flask",
        "gcp",
        "javascript",
        "kafka",
        "kubernetes",
        "langchain",
        "llm",
        "mlflow",
        "mongodb",
        "nlp",
        "postgres",
        "python",
        "pytorch",
        "rag",
        "react",
        "redis",
        "sql",
        "tensorflow",
        "typescript",
        "vector",
    }
)

_COMPLEXITY_SIGNALS: tuple[str, ...] = (
    "auth",
    "authentication",
    "database",
    "deployment",
    "distributed",
    "evaluation",
    "inference",
    "pipeline",
    "real-time",
    "realtime",
    "retrieval",
    "scalable",
    "streaming",
    "testing",
)

_IMPACT_SIGNALS: tuple[str, ...] = (
    "improved",
    "increased",
    "reduced",
    "optimized",
    "decreased",
    "accelerated",
    "users",
    "latency",
    "accuracy",
    "cost",
)

_PRODUCTION_SIGNALS: tuple[str, ...] = (
    "deployed",
    "production",
    "monitoring",
    "ci/cd",
    "cicd",
    "docker",
    "cloud",
    "api",
    "live",
)

_TUTORIAL_HINTS: tuple[str, ...] = (
    "todo",
    "to-do",
    "calculator",
    "weather app",
    "recipe app",
    "portfolio website",
    "hello world",
)

_URL_RE = re.compile(r"https?://[^\s)>,]+", re.IGNORECASE)
_QUANTIFIED_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|x|ms|s|sec|seconds|users?|requests?|records?|rows?)\b)"
    r"|(?:\b\d{2,}\+?\b)"
)


@dataclass
class ProjectScore:
    """Credibility signals for one project already present in the resume."""

    name: str
    score: float
    band: ProjectBand
    signals: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "band": self.band,
            "signals": list(self.signals),
            "risks": list(self.risks),
            "urls": list(self.urls),
        }


@dataclass
class ProjectCredibilityReport:
    """Aggregate project credibility for recruiter/ATS-style reporting."""

    score: float
    band: ProjectBand
    projects: list[ProjectScore] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "projects": [project.to_dict() for project in self.projects],
            "strengths": list(self.strengths),
            "risks": list(self.risks),
        }


def score_project_credibility(resume_data: Mapping[str, Any]) -> ProjectCredibilityReport:
    """Score project substance using only structured resume extraction output."""
    raw_projects = resume_data.get("projects", [])
    if not isinstance(raw_projects, list) or not raw_projects:
        return ProjectCredibilityReport(
            score=50.0,
            band="basic",
            risks=["No project section was found in the parsed resume."],
        )

    project_scores = [_score_project(project) for project in raw_projects if isinstance(project, Mapping)]
    if not project_scores:
        return ProjectCredibilityReport(
            score=50.0,
            band="basic",
            risks=["Project section was present but no readable project entries were found."],
        )

    ranked = sorted(project_scores, key=lambda project: project.score, reverse=True)
    top = ranked[:3]
    aggregate = round(sum(project.score for project in top) / len(top), 1)
    strengths = _aggregate_strengths(ranked)
    risks = _aggregate_risks(ranked)
    return ProjectCredibilityReport(
        score=aggregate,
        band=_band(aggregate),
        projects=ranked,
        strengths=strengths,
        risks=risks,
    )


def _score_project(project: Mapping[str, Any]) -> ProjectScore:
    name = _project_name(project)
    text = _project_text(project)
    norm = text.lower()
    urls = _project_urls(project, text)

    signals: list[str] = []
    risks: list[str] = []
    score = 25.0

    tech_hits = _term_hits(norm, _TECH_TERMS)
    if tech_hits:
        score += min(20.0, len(tech_hits) * 4.0)
        signals.append("Technical stack visible: " + ", ".join(tech_hits[:5]))
    else:
        risks.append("No clear technical stack is visible.")

    complexity_hits = _phrase_hits(norm, _COMPLEXITY_SIGNALS)
    if complexity_hits:
        score += min(18.0, len(complexity_hits) * 4.5)
        signals.append("Complexity signals: " + ", ".join(complexity_hits[:4]))

    impact_hits = _phrase_hits(norm, _IMPACT_SIGNALS)
    quantified = bool(_QUANTIFIED_RE.search(text))
    if impact_hits:
        score += min(12.0, len(impact_hits) * 3.0)
        signals.append("Impact language present: " + ", ".join(impact_hits[:4]))
    if quantified:
        score += 12.0
        signals.append("Quantified outcome or scale is present.")
    else:
        risks.append("No quantified outcome or scale is visible.")

    production_hits = _phrase_hits(norm, _PRODUCTION_SIGNALS)
    if production_hits:
        score += min(13.0, len(production_hits) * 3.25)
        signals.append("Deployment/production signals: " + ", ".join(production_hits[:4]))

    if urls:
        score += 10.0
        signals.append("Project link is present in the resume.")
    else:
        risks.append("No project URL or demo/source link is listed.")

    if _looks_tutorial_or_generic(name, norm):
        score -= 18.0
        risks.append("Project name/content looks generic or tutorial-like.")

    final_score = round(max(0.0, min(100.0, score)), 1)
    return ProjectScore(
        name=name,
        score=final_score,
        band=_band(final_score),
        signals=signals[:6],
        risks=risks[:5],
        urls=urls,
    )


def _project_name(project: Mapping[str, Any]) -> str:
    for key in ("title", "name", "project", "header"):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Untitled project"


def _project_text(project: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "name", "description", "venue", "date"):
        value = project.get(key)
        if isinstance(value, str):
            parts.append(value)
    bullets = project.get("bullets")
    if isinstance(bullets, list):
        parts.extend(str(item) for item in bullets if str(item).strip())
    technologies = project.get("technologies")
    if isinstance(technologies, list):
        parts.extend(str(item) for item in technologies if str(item).strip())
    elif isinstance(technologies, str):
        parts.append(technologies)
    return " ".join(parts)


def _project_urls(project: Mapping[str, Any], text: str) -> list[str]:
    urls: list[str] = []
    raw_urls = project.get("urls")
    if isinstance(raw_urls, list):
        urls.extend(str(url).strip() for url in raw_urls if str(url).strip())
    for key in ("url", "demo", "source"):
        value = project.get(key)
        if isinstance(value, str) and value.strip():
            urls.append(value.strip())
    urls.extend(match.group(0).rstrip(".") for match in _URL_RE.finditer(text))
    return list(dict.fromkeys(urls))


def _term_hits(norm: str, terms: frozenset[str]) -> list[str]:
    hits = [
        term
        for term in sorted(terms)
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", norm)
    ]
    return hits


def _phrase_hits(norm: str, phrases: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in phrases if phrase.lower() in norm]


def _looks_tutorial_or_generic(name: str, norm: str) -> bool:
    combined = f"{name} {norm}".lower()
    return any(hint in combined for hint in _TUTORIAL_HINTS)


def _aggregate_strengths(projects: list[ProjectScore]) -> list[str]:
    strengths: list[str] = []
    if any(project.urls for project in projects):
        strengths.append("At least one project includes a link for reviewer verification.")
    if any("Quantified outcome" in signal for project in projects for signal in project.signals):
        strengths.append("At least one project includes quantified impact or scale.")
    strong_projects = [project.name for project in projects if project.band in {"strong", "good"}]
    if strong_projects:
        strengths.append("Credible project examples: " + ", ".join(strong_projects[:3]))
    return strengths[:5]


def _aggregate_risks(projects: list[ProjectScore]) -> list[str]:
    risks: list[str] = []
    if all(not project.urls for project in projects):
        risks.append("Projects lack verification links in the resume.")
    if all("No quantified outcome or scale is visible." in project.risks for project in projects):
        risks.append("Projects do not show quantified impact or scale.")
    weak_projects = [project.name for project in projects if project.band == "weak"]
    if weak_projects:
        risks.append("Weak project entries: " + ", ".join(weak_projects[:3]))
    return risks[:5]


def _band(score: float) -> ProjectBand:
    if score >= 80:
        return "strong"
    if score >= 65:
        return "good"
    if score >= 45:
        return "basic"
    return "weak"
