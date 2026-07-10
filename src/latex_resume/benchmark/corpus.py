"""Safe benchmark-corpus generation and public job-board ingestion."""

from __future__ import annotations

import csv
import html
import io
import json
import random
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from latex_resume.benchmark.io import (
    JOB_MANIFEST,
    JOBS_DIR,
    LIVE_JOBS_DIR,
    MANIFEST_DIR,
    RESUME_MANIFEST,
    RESUMES_DIR,
    TAXONOMY_RAW_DIR,
    ensure_directories,
    relative_path,
    sha256_text,
    stable_id,
    write_jsonl,
)
from latex_resume.benchmark.models import (
    EvidenceLedger,
    FitTier,
    JobFixture,
    ResumeFixture,
    RoleFamily,
    Seniority,
    utc_now,
)
from latex_resume.job_keywords import extract_job_keywords_fast
from latex_resume.parser import parse
from latex_resume.renderer import check_one_page

SEED = 20260618
ONET_VERSION = "30.3"
ONET_TEXT_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_text.zip"
LIVE_SOURCE_REGISTRY = MANIFEST_DIR / "live_job_boards.json"

ROLE_PROFILES: dict[RoleFamily, dict[str, Any]] = {
    "ai_engineer": {
        "title": "AI Engineer",
        "skills": [
            "Python", "FastAPI", "Generative AI", "RAG", "LangChain",
            "OpenAI", "Vector Databases", "Git", "Docker", "Azure",
        ],
        "adjacent": ["API development", "LLM applications", "cloud-native"],
        "keywords": ["AI agents", "prompt engineering", "production", "evaluation"],
        "tasks": [
            "Built retrieval-augmented assistants grounded in curated knowledge bases",
            "Developed API services for integrating language-model workflows into applications",
            "Evaluated response quality, retrieval relevance, and production failure modes",
        ],
    },
    "ml_engineer": {
        "title": "Machine Learning Engineer",
        "skills": [
            "Python", "PyTorch", "Scikit-learn", "SQL", "FastAPI",
            "MLflow", "Docker", "AWS", "Feature Engineering", "Model Deployment",
        ],
        "adjacent": ["API development", "model monitoring", "ML lifecycle"],
        "keywords": ["machine learning pipelines", "inference", "production", "latency"],
        "tasks": [
            "Built training and inference pipelines for supervised machine learning models",
            "Served validated models through APIs with repeatable deployment workflows",
            "Monitored model quality and investigated performance regressions",
        ],
    },
    "data_scientist": {
        "title": "Data Scientist",
        "skills": [
            "Python", "SQL", "Pandas", "Scikit-learn", "Statistics",
            "A/B Testing", "Predictive Modeling", "Machine Learning", "Git", "Tableau",
        ],
        "adjacent": ["experimentation", "stakeholder communication", "model evaluation"],
        "keywords": ["business impact", "hypothesis testing", "forecasting", "insights"],
        "tasks": [
            "Developed predictive analyses that translated operational data into decisions",
            "Designed experiments and communicated statistically grounded recommendations",
            "Partnered with product stakeholders to define measurable model outcomes",
        ],
    },
    "mlops_engineer": {
        "title": "MLOps Engineer",
        "skills": [
            "Python", "MLflow", "Docker", "Kubernetes", "CI/CD",
            "AWS", "Terraform", "Airflow", "Model Monitoring", "GitHub",
        ],
        "adjacent": ["ML lifecycle", "cloud platform", "deployment automation"],
        "keywords": ["observability", "reliability", "model registry", "infrastructure"],
        "tasks": [
            "Automated model packaging, validation, deployment, and rollback workflows",
            "Built observability for data quality, service health, and model performance",
            "Standardized reproducible infrastructure for training and inference workloads",
        ],
    },
    "nlp_llm_engineer": {
        "title": "NLP and LLM Engineer",
        "skills": [
            "Python", "PyTorch", "NLP", "LLMs", "Transformers",
            "Hugging Face", "RAG", "Embeddings", "Vector Databases", "LangGraph",
        ],
        "adjacent": ["conversational AI", "summarization", "LLM applications"],
        "keywords": ["information retrieval", "fine-tuning", "evaluation", "Responsible AI"],
        "tasks": [
            "Built language-model applications for retrieval, extraction, and summarization",
            "Created evaluation datasets for response quality and retrieval relevance",
            "Applied interpretability and bias checks to language-model workflows",
        ],
    },
    "data_engineer": {
        "title": "Data Engineer",
        "skills": [
            "Python", "SQL", "Spark", "Airflow", "Databricks",
            "AWS", "Docker", "PostgreSQL", "ETL", "Data Modeling",
        ],
        "adjacent": ["data pipelines", "cloud platform", "production reliability"],
        "keywords": ["batch processing", "streaming", "data quality", "scale"],
        "tasks": [
            "Built reliable batch pipelines for analytics and machine learning datasets",
            "Designed tested data models and reusable transformations for downstream teams",
            "Improved pipeline observability, recovery, and data-quality validation",
        ],
    },
}

SENIORITY_YEARS: dict[Seniority, int] = {
    "junior": 1,
    "mid": 3,
    "senior": 6,
    "staff": 9,
}
SENIORITY_PREFIX: dict[Seniority, str] = {
    "junior": "Associate",
    "mid": "",
    "senior": "Senior",
    "staff": "Staff",
}
INDUSTRIES = ("healthcare", "finance", "saas", "retail", "security", "technology")
TEMPLATES = ("classic", "compact", "custom_commands", "project_first")

_SYNTHETIC_NAMES = (
    "Avery Quinn",
    "Jordan Vale",
    "Morgan Hale",
    "Casey Rowan",
    "Riley Arden",
    "Taylor Ellis",
    "Cameron Lane",
    "Drew Parker",
)

_DEFAULT_SOURCES: list[dict[str, str]] = [
    {"provider": "greenhouse", "site": "anthropic", "company": "Anthropic", "industry": "technology"},
    {"provider": "greenhouse", "site": "datadog", "company": "Datadog", "industry": "saas"},
    {"provider": "greenhouse", "site": "mongodb", "company": "MongoDB", "industry": "saas"},
    {"provider": "greenhouse", "site": "sumologic", "company": "Sumo Logic", "industry": "security"},
    {"provider": "greenhouse", "site": "twilio", "company": "Twilio", "industry": "saas"},
    {"provider": "greenhouse", "site": "faire", "company": "Faire", "industry": "retail"},
    {"provider": "lever", "site": "highspot", "company": "Highspot", "industry": "saas"},
    {"provider": "ashby", "site": "airops", "company": "AirOps", "industry": "saas"},
    {"provider": "ashby", "site": "modal", "company": "Modal", "industry": "technology"},
    {"provider": "ashby", "site": "perplexity", "company": "Perplexity", "industry": "technology"},
    {"provider": "ashby", "site": "openai", "company": "OpenAI", "industry": "technology"},
]


def build_synthetic_resumes(count: int = 40, seed: int = SEED) -> list[ResumeFixture]:
    """Generate deterministic one-page LaTeX resumes and evidence ledgers."""
    if count != 40:
        raise ValueError("The MVP corpus is fixed at 40 resumes for reproducibility.")
    ensure_directories()
    rng = random.Random(seed)
    fixtures: list[ResumeFixture] = []
    role_families = list(ROLE_PROFILES)
    seniorities: list[Seniority] = ["junior", "mid", "senior", "staff"]
    fit_tiers: list[FitTier] = ["strong", "medium", "weak", "incompatible"]

    for index in range(count):
        profile_index = index // 2 if index < 8 else index - 4
        role = role_families[profile_index % len(role_families)]
        seniority = seniorities[(profile_index // len(role_families)) % len(seniorities)]
        fit_tier = fit_tiers[(profile_index * 3 + profile_index // 4) % len(fit_tiers)]
        template_id = TEMPLATES[profile_index % len(TEMPLATES)]
        counterfactual_group = f"cf-{index // 2:02d}" if index < 8 else None
        counterfactual_attribute = None
        if counterfactual_group:
            counterfactual_attribute = "name_a" if index % 2 == 0 else "name_b"
            paired_name_index = (index // 2) * 2 + (index % 2)
            name = _SYNTHETIC_NAMES[paired_name_index]
        else:
            name = _SYNTHETIC_NAMES[index % len(_SYNTHETIC_NAMES)]

        profile = ROLE_PROFILES[role]
        resume_id = f"resume-{index + 1:03d}-{role}-{seniority}"
        ledger = _build_evidence_ledger(
            resume_id,
            role,
            seniority,
            fit_tier,
            profile_index,
            rng,
        )
        latex = _render_resume(
            resume_id=resume_id,
            name=name,
            role=role,
            seniority=seniority,
            template_id=template_id,
            ledger=ledger,
            profile=profile,
            index=profile_index,
        )
        parse_result = parse(latex, resume_id=resume_id)
        render = check_one_page(latex)
        latex_path = RESUMES_DIR / f"{resume_id}.tex"
        evidence_path = RESUMES_DIR / f"{resume_id}.evidence.json"
        latex_path.write_text(latex, encoding="utf-8")
        evidence_path.write_text(
            ledger.model_dump_json(indent=2),
            encoding="utf-8",
        )
        fixtures.append(
            ResumeFixture(
                resume_id=resume_id,
                role_family=role,
                seniority=seniority,
                profile_fit=fit_tier,
                template_id=template_id,
                latex_path=relative_path(latex_path),
                evidence_path=relative_path(evidence_path),
                content_sha256=sha256_text(latex),
                holdout=_resume_holdout(index),
                counterfactual_group=counterfactual_group,
                counterfactual_attribute=counterfactual_attribute,
                word_count=len(re.findall(r"\b[\w+#.-]+\b", latex)),
                parser_ok=bool(parse_result.doc.sections),
                render_ok=render.ok,
                page_count=render.page_count,
                overflow=render.overflow,
            )
        )

    write_jsonl(RESUME_MANIFEST, fixtures)
    return fixtures


def build_offline_jobs(
    taxonomy_count: int = 40,
    adversarial_count: int = 20,
    esco_dir: Path | None = None,
) -> list[JobFixture]:
    """Build the redistributable taxonomy and adversarial JD fixtures."""
    if taxonomy_count != 40 or adversarial_count != 20:
        raise ValueError("The MVP offline corpus is fixed at 40 taxonomy and 20 adversarial JDs.")
    ensure_directories()
    archive = TAXONOMY_RAW_DIR / f"onet-{ONET_VERSION}-text.zip"
    onet_profiles = _load_onet_profiles(archive) if archive.exists() else {}
    esco_aliases = _load_esco_aliases(esco_dir) if esco_dir else {}
    jobs = _build_taxonomy_jobs(
        taxonomy_count,
        onet_profiles,
        esco_aliases,
    ) + _build_adversarial_jobs(adversarial_count)
    live_jobs = [
        job for job in _load_existing_jobs()
        if job.source_kind == "live_public" and Path(job.text_path).exists()
    ]
    write_jsonl(JOB_MANIFEST, live_jobs + jobs)
    return jobs


def download_onet(url: str = ONET_TEXT_URL, timeout: float = 60.0) -> Path:
    """Download the CC BY 4.0 O*NET text archive for local taxonomy inspection."""
    ensure_directories()
    target = TAXONOMY_RAW_DIR / f"onet-{ONET_VERSION}-text.zip"
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target


def fetch_live_jobs(
    target_count: int = 60,
    registry_path: Path = LIVE_SOURCE_REGISTRY,
    timeout: float = 30.0,
) -> tuple[list[JobFixture], list[str]]:
    """Fetch current AI/ML postings from public ATS job-board APIs."""
    ensure_directories()
    if not registry_path.exists():
        registry_path.write_text(
            json.dumps({"sources": _DEFAULT_SOURCES}, indent=2),
            encoding="utf-8",
        )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    candidates: list[JobFixture] = []
    errors: list[str] = []

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for source in registry.get("sources", []):
            try:
                postings = _fetch_source(client, source)
            except Exception as exc:
                errors.append(
                    f"{source.get('provider')}:{source.get('site')}: {type(exc).__name__}: {exc}"
                )
                continue
            for posting in postings:
                fixture = _posting_to_fixture(posting, source)
                if fixture is not None:
                    candidates.append(fixture)

    selected = _balance_live_jobs(candidates, target_count)
    selected = [
        item.model_copy(update={"holdout": index % 5 == 0})
        for index, item in enumerate(selected)
    ]
    if len(selected) < target_count:
        errors.append(
            f"Only {len(selected)} qualifying current postings were available; "
            f"target is {target_count}. Add job boards to {relative_path(registry_path)}."
        )
    existing_non_live = [job for job in _load_existing_jobs() if job.source_kind != "live_public"]
    write_jsonl(JOB_MANIFEST, selected + existing_non_live)
    _prune_live_snapshots(selected)
    return selected, errors


def load_evidence(fixture: ResumeFixture) -> EvidenceLedger:
    """Load the evidence ledger for a resume fixture."""
    from latex_resume.benchmark.io import ROOT

    return EvidenceLedger.model_validate_json(
        (ROOT / fixture.evidence_path).read_text(encoding="utf-8")
    )


def _build_evidence_ledger(
    resume_id: str,
    role: RoleFamily,
    seniority: Seniority,
    fit_tier: FitTier,
    index: int,
    rng: random.Random,
) -> EvidenceLedger:
    profile = ROLE_PROFILES[role]
    skills = list(profile["skills"])
    if fit_tier == "medium":
        skills = skills[:7]
    elif fit_tier == "weak":
        skills = skills[:4] + ["Excel", "Data Visualization"]
    elif fit_tier == "incompatible":
        other_role = list(ROLE_PROFILES)[(list(ROLE_PROFILES).index(role) + 3) % 6]
        skills = list(ROLE_PROFILES[other_role]["skills"][:5])

    metrics = [
        f"{12 + index % 19}\\%",
        f"{18 + index % 23}\\%",
        f"{2 + index % 6} pipelines",
    ]
    domains = [INDUSTRIES[index % len(INDUSTRIES)]]
    equivalents = {
        "API development": [skill for skill in skills if skill in {"FastAPI", "Postman"}],
        "cloud-native": [skill for skill in skills if skill in {"AWS", "Azure", "GCP", "Docker"}],
        "model monitoring": [skill for skill in skills if skill in {"MLflow", "Model Monitoring"}],
        "LLM applications": [skill for skill in skills if skill in {"RAG", "LLMs", "LangChain"}],
    }
    equivalents = {key: value for key, value in equivalents.items() if value}
    return EvidenceLedger(
        resume_id=resume_id,
        skills=skills,
        employers=["Synthetic Analytics Studio", "Open Systems Laboratory"],
        domains=domains,
        education=["B.S. Computer Science"],
        metrics=metrics,
        certifications=[],
        supported_equivalents=equivalents,
        allowed_claims=list(profile["tasks"]) + list(profile["adjacent"]),
    )


def _render_resume(
    *,
    resume_id: str,
    name: str,
    role: RoleFamily,
    seniority: Seniority,
    template_id: str,
    ledger: EvidenceLedger,
    profile: dict[str, Any],
    index: int,
) -> str:
    years = SENIORITY_YEARS[seniority]
    title_prefix = SENIORITY_PREFIX[seniority]
    title = f"{title_prefix} {profile['title']}".strip()
    skills = ", ".join(ledger.skills)
    domain = ledger.domains[0]
    metric_a, metric_b, metric_c = ledger.metrics
    tasks = profile["tasks"]
    if ledger.skills != profile["skills"] and len(ledger.skills) <= 7:
        tasks = [
            "Analyzed operational datasets and documented repeatable technical workflows",
            "Built Python prototypes and validated outputs with unit and integration checks",
            "Presented findings to technical and non-technical project stakeholders",
        ]
    bullets = [
        f"{tasks[0]}, improving validated workflow quality by \\textbf{{{metric_a}}}.",
        f"{tasks[1]} and reduced repeatable processing time by \\textbf{{{metric_b}}}.",
        f"{tasks[2]} across \\textbf{{{metric_c}}} in a {domain} test environment.",
    ]
    project_bullets = [
        f"Created a reproducible {profile['title'].lower()} project using {', '.join(ledger.skills[:3])}.",
        "Added evaluation checks, documented limitations, and preserved source evidence for each reported result.",
    ]
    summary = (
        f"{title} with {years}+ years of synthetic benchmark experience in "
        f"{', '.join(ledger.skills[:4])}. Builds evidence-backed systems for "
        f"{domain} workflows while emphasizing reliability and clear evaluation."
    )
    section_order = (
        ("Summary", "Skills", "Projects", "Experience", "Education")
        if template_id == "project_first"
        else ("Summary", "Experience", "Projects", "Skills", "Education")
    )
    custom = template_id == "custom_commands"
    list_env = "enumerate" if template_id == "compact" else "itemize"
    bullet_lines = _latex_list(bullets, list_env, custom)
    project_lines = _latex_list(project_bullets, list_env, custom)
    skills_lines = _latex_list(
        [skills, "Evidence practices: testing, documentation, reproducibility"],
        list_env,
        custom,
    )
    sections = {
        "Summary": summary,
        "Experience": (
            f"\\textbf{{{title}}} \\hfill 2021 -- Present\\\\\n"
            "\\textit{Synthetic Analytics Studio}, Remote\n"
            f"{bullet_lines}"
        ),
        "Projects": (
            f"\\textbf{{Evidence-Grounded {profile['title']} Benchmark}} \\hfill 2025\n"
            f"{project_lines}"
        ),
        "Skills": skills_lines,
        "Education": "\\textbf{B.S. Computer Science}, Synthetic Technical University \\hfill 2021",
    }
    rendered_sections = "\n\n".join(
        f"\\section*{{{section}}}\n{sections[section]}" for section in section_order
    )
    custom_commands = (
        "\\newcommand{\\resumeItem}[1]{\\item #1}\n"
        "\\newcommand{\\resumeItemListStart}{\\begin{itemize}[leftmargin=*]}\n"
        "\\newcommand{\\resumeItemListEnd}{\\end{itemize}}\n"
        if custom
        else ""
    )
    margin = "13mm" if template_id in {"compact", "project_first"} else "16mm"
    return (
        "% Synthetic benchmark resume. No real person or employer is represented.\n"
        f"% Fixture: {resume_id}\n"
        "\\documentclass[10pt,letterpaper]{article}\n"
        f"\\usepackage[margin={margin}]{{geometry}}\n"
        "\\usepackage{enumitem}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\setlist{nosep,leftmargin=*}\n"
        "\\pagestyle{empty}\n"
        f"{custom_commands}"
        "\\begin{document}\n"
        "\\begin{center}\n"
        f"{{\\Large \\textbf{{{name}}}}}\\\\\n"
        "synthetic.candidate@example.test $\\cdot$ github.example/synthetic\n"
        "\\end{center}\n\n"
        f"{rendered_sections}\n"
        "\\end{document}\n"
    )


def _latex_list(items: Iterable[str], environment: str, custom: bool) -> str:
    if custom:
        return (
            "\\resumeItemListStart\n"
            + "\n".join(f"  \\resumeItem{{{item}}}" for item in items)
            + "\n\\resumeItemListEnd"
        )
    return (
        f"\\begin{{{environment}}}\n"
        + "\n".join(f"  \\item {item}" for item in items)
        + f"\n\\end{{{environment}}}"
    )


def _build_taxonomy_jobs(
    count: int,
    onet_profiles: dict[RoleFamily, dict[str, list[str]]] | None = None,
    esco_aliases: dict[str, list[str]] | None = None,
) -> list[JobFixture]:
    jobs: list[JobFixture] = []
    roles = list(ROLE_PROFILES)
    seniorities: list[Seniority] = ["junior", "mid", "senior", "staff"]
    onet_profiles = onet_profiles or {}
    esco_aliases = esco_aliases or {}
    for index in range(count):
        role = roles[index % len(roles)]
        seniority = seniorities[(index // len(roles)) % 4]
        profile = ROLE_PROFILES[role]
        onet_profile = onet_profiles.get(role, {})
        prefix = SENIORITY_PREFIX[seniority]
        title = f"{prefix} {profile['title']}".strip()
        industry = INDUSTRIES[index % len(INDUSTRIES)]
        required = list(profile["skills"][: 6 if seniority == "junior" else 8])
        technologies = [
            item
            for item in onet_profile.get("technologies", [])
            if item.lower() not in {skill.lower() for skill in required}
        ]
        aliases = [
            alias
            for skill in required
            for alias in esco_aliases.get(_normalise_label(skill), [])
        ]
        preferred = (
            list(profile["skills"][len(required):])
            + technologies[:3]
            + aliases[:3]
            + list(profile["adjacent"][:2])
        )
        preferred = list(dict.fromkeys(preferred))
        years = SENIORITY_YEARS[seniority]
        text = _render_taxonomy_jd(
            title,
            industry,
            years,
            required,
            preferred,
            onet_profile.get("tasks", profile["tasks"])[:4],
            profile["keywords"],
        )
        job_id = f"taxonomy-{index + 1:03d}-{role}-{seniority}"
        path = JOBS_DIR / f"{job_id}.md"
        path.write_text(text, encoding="utf-8")
        jobs.append(
            JobFixture(
                job_id=job_id,
                title=title,
                company="O*NET-derived Synthetic Employer",
                role_family=role,
                seniority=seniority,
                industry=industry,
                source_kind="taxonomy_derived",
                provider="onet_esco" if esco_aliases else "onet",
                source_url="https://www.onetcenter.org/database.html",
                captured_at=utc_now(),
                text_path=relative_path(path),
                content_sha256=sha256_text(text),
                required_skills=required,
                preferred_skills=preferred,
                keywords=list(profile["keywords"]),
                experience_years=float(years),
                education_requirements=["Bachelor's degree"],
                holdout=index % 5 == 0,
                license_or_usage=(
                    "Synthetic transformation derived from O*NET 30.3 task and "
                    "technology tables, CC BY 4.0; "
                    "not endorsed or tested by USDOL/ETA."
                    if onet_profile
                    else
                    "Synthetic bootstrap fixture using the O*NET-aligned role "
                    "profile fallback; regenerate with --download-onet for direct "
                    "O*NET 30.3 table provenance."
                ),
            )
        )
    return jobs


def _render_taxonomy_jd(
    title: str,
    industry: str,
    years: int,
    required: list[str],
    preferred: list[str],
    tasks: list[str],
    keywords: list[str],
) -> str:
    responsibilities = "\n".join(f"- {task.rstrip('.')}." for task in tasks)
    return (
        f"# {title}\n\n"
        "This is a synthetic benchmark job description derived from public "
        f"occupational task and skill concepts for the {industry} sector.\n\n"
        "## Responsibilities\n"
        f"{responsibilities}\n"
        f"- Improve {', '.join(keywords[:3])} across production workflows.\n\n"
        "## Required Qualifications\n"
        f"- {years}+ years of relevant experience.\n"
        f"- Experience with {', '.join(required)}.\n"
        "- Bachelor's degree or equivalent practical experience.\n\n"
        "## Preferred Qualifications\n"
        f"- Familiarity with {', '.join(preferred)}.\n"
    )


def _build_adversarial_jobs(count: int) -> list[JobFixture]:
    tags = (
        "excessive_tools",
        "conflicting_seniority",
        "vague_requirements",
        "domain_heavy",
        "keyword_repetition",
        "missing_sections",
        "unrealistic_experience",
        "mixed_role",
    )
    jobs: list[JobFixture] = []
    roles = list(ROLE_PROFILES)
    for index in range(count):
        role = roles[index % len(roles)]
        profile = ROLE_PROFILES[role]
        tag = tags[index % len(tags)]
        seniority: Seniority = ["junior", "mid", "senior", "staff"][index % 4]
        title = f"{SENIORITY_PREFIX[seniority]} {profile['title']}".strip()
        required = list(profile["skills"][:6])
        preferred = list(profile["skills"][6:])
        text, required, preferred = _adversarial_text(
            title,
            tag,
            required,
            preferred,
            list(profile["keywords"]),
        )
        job_id = f"adversarial-{index + 1:03d}-{tag}"
        path = JOBS_DIR / f"{job_id}.md"
        path.write_text(text, encoding="utf-8")
        jobs.append(
            JobFixture(
                job_id=job_id,
                title=title,
                company="Synthetic Stress-Test Employer",
                role_family=role,
                seniority=seniority,
                industry=INDUSTRIES[(index + 2) % len(INDUSTRIES)],
                source_kind="adversarial",
                provider="synthetic",
                source_url="benchmark://adversarial",
                captured_at=utc_now(),
                text_path=relative_path(path),
                content_sha256=sha256_text(text),
                required_skills=required,
                preferred_skills=preferred,
                keywords=list(profile["keywords"]),
                experience_years=15.0 if tag == "unrealistic_experience" else float(SENIORITY_YEARS[seniority]),
                education_requirements=["PhD"] if tag == "conflicting_seniority" else [],
                holdout=index % 5 == 0,
                license_or_usage="Generated synthetic stress-test fixture.",
                adversarial_tags=[tag],
            )
        )
    return jobs


def _adversarial_text(
    title: str,
    tag: str,
    required: list[str],
    preferred: list[str],
    keywords: list[str],
) -> tuple[str, list[str], list[str]]:
    if tag == "excessive_tools":
        extra = ["Kubernetes", "Golang", "Kotlin", "Vertex AI", "Amazon Bedrock", "Snowflake"]
        required += extra
        body = f"Must know every tool: {', '.join(required)}."
    elif tag == "conflicting_seniority":
        body = "Entry-level candidates welcome. Requires 8+ years, a PhD, and staff-level architecture leadership."
    elif tag == "vague_requirements":
        body = "Build amazing AI. Move fast. Own everything. Be world class."
    elif tag == "domain_heavy":
        body = "Requires deep healthcare claims, clinical operations, HIPAA, and payer-domain experience."
        required += ["Healthcare", "HIPAA"]
    elif tag == "keyword_repetition":
        repeated = " ".join(keywords * 5)
        body = f"Keywords: {repeated}"
    elif tag == "missing_sections":
        body = f"We need a {title} who can help our team."
    elif tag == "unrealistic_experience":
        body = f"Requires 15+ years with {', '.join(required)}, including recently introduced tools."
    else:
        required += ["Spark", "React", "Kubernetes"]
        body = f"Own data engineering, frontend delivery, and AI research using {', '.join(required)}."
    text = f"# {title}\n\n{body}\n\nPreferred: {', '.join(preferred)}.\n"
    return text, required, preferred


def _fetch_source(client: httpx.Client, source: dict[str, str]) -> list[dict[str, Any]]:
    provider = source["provider"]
    site = source["site"]
    if provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{site}/jobs?content=true"
        payload = client.get(url).raise_for_status().json()
        return [
            {
                "external_id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "description": _strip_html(item.get("content", "")),
                "url": item.get("absolute_url", ""),
                "location": (item.get("location") or {}).get("name", ""),
                "updated_at": item.get("updated_at"),
            }
            for item in payload.get("jobs", [])
        ]
    if provider == "lever":
        url = f"https://api.lever.co/v0/postings/{site}?mode=json"
        payload = client.get(url).raise_for_status().json()
        return [
            {
                "external_id": str(item.get("id", "")),
                "title": item.get("text", ""),
                "description": item.get("descriptionPlain") or _strip_html(item.get("description", "")),
                "url": item.get("hostedUrl", ""),
                "location": (item.get("categories") or {}).get("location", ""),
                "updated_at": None,
            }
            for item in payload
        ]
    if provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{site}"
        payload = client.get(url).raise_for_status().json()
        return [
            {
                "external_id": str(item.get("id") or stable_id(item.get("title", ""), item.get("jobUrl", ""))),
                "title": item.get("title", ""),
                "description": (
                    item.get("descriptionPlain")
                    or item.get("description")
                    or item.get("descriptionHtml")
                    or ""
                ),
                "url": item.get("jobUrl") or item.get("applyUrl") or "",
                "location": item.get("location", ""),
                "updated_at": item.get("publishedAt"),
            }
            for item in payload.get("jobs", [])
        ]
    raise ValueError(f"Unsupported job provider: {provider}")


def _posting_to_fixture(
    posting: dict[str, Any],
    source: dict[str, str],
) -> JobFixture | None:
    title = str(posting.get("title", "")).strip()
    description = _strip_html(str(posting.get("description", ""))).strip()
    role = _classify_role(title, description)
    if role is None or len(description) < 250:
        return None
    seniority = _classify_seniority(title, description)
    keywords = extract_job_keywords_fast(description)
    job_id = (
        f"live-{source['provider']}-{source['site']}-"
        f"{stable_id(str(posting.get('external_id')), title, length=12)}"
    )
    path = LIVE_JOBS_DIR / f"{job_id}.txt"
    path.write_text(description, encoding="utf-8")
    captured = datetime.now(timezone.utc)
    return JobFixture(
        job_id=job_id,
        title=title,
        company=source["company"],
        role_family=role,
        seniority=seniority,
        industry=source.get("industry", "technology"),
        source_kind="live_public",
        provider=source["provider"],
        source_url=str(posting.get("url") or ""),
        captured_at=captured.isoformat(),
        text_path=relative_path(path),
        content_sha256=sha256_text(description),
        required_skills=keywords.get("required_skills", []),
        preferred_skills=keywords.get("preferred_skills", []),
        keywords=keywords.get("keywords", []),
        experience_years=keywords.get("experience_years"),
        education_requirements=keywords.get("education_requirements", []),
        holdout=_is_holdout(job_id),
        license_or_usage=(
            "Publicly viewable employer posting retained locally for evaluation; "
            "not redistributed. Refresh or delete after 90 days."
        ),
        expires_at=(captured + timedelta(days=90)).isoformat(),
    )


def _balance_live_jobs(candidates: list[JobFixture], target_count: int) -> list[JobFixture]:
    unique: dict[str, JobFixture] = {}
    for job in candidates:
        unique.setdefault(job.content_sha256, job)
    pool = sorted(unique.values(), key=lambda item: (item.role_family, item.company, item.job_id))
    selected: list[JobFixture] = []
    counts: Counter[str] = Counter()
    per_role_target = max(1, target_count // len(ROLE_PROFILES))
    while pool and len(selected) < target_count:
        progressed = False
        for role in ROLE_PROFILES:
            if counts[role] >= per_role_target and len(selected) < per_role_target * len(ROLE_PROFILES):
                continue
            match_index = next(
                (idx for idx, item in enumerate(pool) if item.role_family == role),
                None,
            )
            if match_index is None:
                continue
            selected.append(pool.pop(match_index))
            counts[role] += 1
            progressed = True
            if len(selected) >= target_count:
                break
        if not progressed:
            break
    selected.extend(pool[: max(0, target_count - len(selected))])
    return selected[:target_count]


def _prune_live_snapshots(selected: list[JobFixture]) -> None:
    """Delete fetched snapshots that were not retained in the benchmark manifest."""
    retained = {Path(item.text_path).name for item in selected}
    for path in LIVE_JOBS_DIR.glob("*.txt"):
        if path.name not in retained:
            path.unlink()


def _classify_role(title: str, description: str) -> RoleFamily | None:
    text = f"{title} {description[:1500]}".lower()
    title_lower = title.lower()
    rules: list[tuple[RoleFamily, tuple[str, ...]]] = [
        ("mlops_engineer", ("mlops", "machine learning platform", "ml platform")),
        ("nlp_llm_engineer", ("nlp", "llm", "language model", "conversational ai")),
        ("data_engineer", ("data engineer", "analytics engineer")),
        ("data_scientist", ("data scientist", "applied scientist", "decision scientist")),
        ("ml_engineer", ("machine learning engineer", "ml engineer")),
        ("ai_engineer", ("ai engineer", "artificial intelligence engineer", "ai developer")),
    ]
    for role, phrases in rules:
        if any(phrase in title_lower for phrase in phrases):
            return role
    if "machine learning" in text and "engineer" in title_lower:
        return "ml_engineer"
    if "generative ai" in text and "engineer" in title_lower:
        return "ai_engineer"
    return None


def _classify_seniority(title: str, description: str) -> Seniority:
    text = f"{title} {description[:1000]}".lower()
    if any(token in text for token in ("staff", "principal", "distinguished")):
        return "staff"
    if any(token in text for token in ("senior", "sr.", "lead ")):
        return "senior"
    if any(token in text for token in ("junior", "associate", "entry level", "new grad")):
        return "junior"
    return "mid"


def _strip_html(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _load_existing_jobs() -> list[JobFixture]:
    if not JOB_MANIFEST.exists():
        return []
    jobs: list[JobFixture] = []
    for line in JOB_MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            jobs.append(JobFixture.model_validate_json(line))
    return jobs


def _is_holdout(identifier: str) -> bool:
    return int(sha256_text(identifier)[-2:], 16) % 5 == 0


def _resume_holdout(index: int) -> bool:
    """Return the fixed eight-resume holdout while keeping audit pairs together."""
    return index in {0, 1, 6, 7, 15, 23, 31, 39}


def inspect_onet_archive(path: Path) -> dict[str, list[str]]:
    """Return O*NET archive filenames and headers for provenance diagnostics."""
    output: dict[str, list[str]] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".txt"):
                continue
            with archive.open(name) as handle:
                wrapper = io.TextIOWrapper(handle, encoding="utf-8-sig")
                reader = csv.reader(wrapper, delimiter="\t")
                output[name] = next(reader, [])
    return output


def _load_onet_profiles(path: Path) -> dict[RoleFamily, dict[str, list[str]]]:
    """Load role tasks and technology examples from an O*NET text archive."""
    soc_by_role: dict[RoleFamily, tuple[str, ...]] = {
        "ai_engineer": ("15-2051.00", "15-1252.00"),
        "ml_engineer": ("15-2051.00", "15-1252.00"),
        "data_scientist": ("15-2051.00",),
        "mlops_engineer": ("15-1252.00", "15-1244.00"),
        "nlp_llm_engineer": ("15-1221.00", "15-2051.00"),
        "data_engineer": ("15-1243.01", "15-1252.00"),
    }
    tasks_by_soc: dict[str, list[str]] = defaultdict(list)
    technologies_by_soc: dict[str, list[str]] = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            lower = Path(name).name.lower()
            if lower == "task statements.txt":
                for row in _read_onet_table(archive, name):
                    soc = row.get("O*NET-SOC Code", "")
                    task = row.get("Task", "").strip()
                    if soc and task and task not in tasks_by_soc[soc]:
                        tasks_by_soc[soc].append(task)
            elif lower in {"technology skills.txt", "software skills.txt", "tools used.txt"}:
                for row in _read_onet_table(archive, name):
                    soc = row.get("O*NET-SOC Code", "")
                    technology = (
                        row.get("Example")
                        or row.get("Workplace Example")
                        or row.get("Commodity Title")
                        or row.get("Technology")
                        or ""
                    ).strip()
                    if soc and technology and technology not in technologies_by_soc[soc]:
                        technologies_by_soc[soc].append(technology)
    output: dict[RoleFamily, dict[str, list[str]]] = {}
    for role, soc_codes in soc_by_role.items():
        tasks = [
            task
            for soc in soc_codes
            for task in tasks_by_soc.get(soc, [])
        ]
        technologies = [
            technology
            for soc in soc_codes
            for technology in technologies_by_soc.get(soc, [])
        ]
        if tasks:
            output[role] = {
                "tasks": list(dict.fromkeys(tasks)),
                "technologies": list(dict.fromkeys(technologies)),
            }
    return output


def _read_onet_table(
    archive: zipfile.ZipFile,
    name: str,
) -> Iterable[dict[str, str]]:
    with archive.open(name) as handle:
        wrapper = io.TextIOWrapper(handle, encoding="utf-8-sig")
        yield from csv.DictReader(wrapper, delimiter="\t")


def _load_esco_aliases(directory: Path) -> dict[str, list[str]]:
    """Load optional ESCO preferred/alternative labels from an extracted export."""
    if not directory.exists():
        raise ValueError(f"ESCO directory does not exist: {directory}")
    candidates = sorted(directory.rglob("*.csv"))
    aliases: dict[str, list[str]] = defaultdict(list)
    for path in candidates:
        sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            continue
        with path.open(encoding="utf-8-sig", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle, dialect=dialect)
            fieldnames = {name.lower(): name for name in (reader.fieldnames or [])}
            preferred_key = fieldnames.get("preferredlabel")
            alternative_key = fieldnames.get("altlabels") or fieldnames.get("alternativelabel")
            if not preferred_key or not alternative_key:
                continue
            for row in reader:
                preferred = (row.get(preferred_key) or "").strip()
                alternatives = (row.get(alternative_key) or "").strip()
                if not preferred or not alternatives:
                    continue
                values = [
                    value.strip()
                    for value in re.split(r"\n|\||;", alternatives)
                    if value.strip()
                ]
                aliases[_normalise_label(preferred)].extend(values)
    return {
        key: list(dict.fromkeys(values))
        for key, values in aliases.items()
    }


def _normalise_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
