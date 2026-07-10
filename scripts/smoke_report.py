#!/usr/bin/env python3
"""Generate deterministic smoke-test metrics for SmartJobApply.

This script avoids LLM calls. It exercises parser, extractor, renderer, and ATS
scoring against the sample resume and saved JD fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latex_resume.ats import check_ats
from latex_resume.engine import extract_editable, parse_file
from latex_resume.extractor import extract_full_resume
from latex_resume.optimizer import _build_plain_text
from latex_resume.renderer import check_one_page

ROOT = Path(__file__).parent.parent
RESUME = ROOT / "samples" / "sample_resume.tex"
OUT = ROOT / "samples" / "out" / "smartjobapply_smoke_report.json"


def jd_keywords() -> dict[str, dict[str, Any]]:
    """Curated keyword fixtures for deterministic ATS smoke testing."""
    return {
        "jd_optum_existing": {
            "source_file": "samples/job_descriptions/optum_ai_ml_engineer.md",
            "required_skills": [
                "Python",
                "GitHub",
                "Langchain",
                "OpenAI API",
                "Hugging Face Transformers",
                "LangGraph",
                "AutoGen",
                "Bedrock",
                "Vertex AI",
                "VS Code with GitHub Copilot",
                "Codex",
                "Claude Code",
            ],
            "preferred_skills": [
                "Communication skills",
                "Digital/eCommerce experience",
                "Azure",
            ],
            "keywords": [
                "machine learning",
                "generative AI",
                "NLP",
                "LLMs",
                "cloud-native",
                "Responsible AI",
                "ethical AI",
                "agentic frameworks",
            ],
        },
        "darkwolf_ml_engineer": {
            "source_file": "samples/job_descriptions/darkwolf_ml_engineer.md",
            "required_skills": [
                "Python",
                "TensorFlow",
                "Keras",
                "PyTorch",
                "scikit-learn",
                "SQL",
                "Spark",
                "Hadoop",
                "AWS",
                "Azure",
                "GCP",
                "Git",
                "CI/CD",
            ],
            "preferred_skills": [
                "NLP",
                "Computer Vision",
                "MLOps",
                "Docker",
                "Kubernetes",
                "RESTful APIs",
                "statistical modeling",
            ],
            "keywords": [
                "machine learning pipelines",
                "feature engineering",
                "model deployment",
                "model monitoring",
                "data visualization",
                "production environment",
            ],
        },
        "airops_data_scientist": {
            "source_file": "samples/job_descriptions/airops_data_scientist.md",
            "required_skills": [
                "Data Science",
                "Machine Learning",
                "NLP",
                "Search algorithms",
                "Recommendation algorithms",
                "LLM-based applications",
                "Python",
                "production ML systems",
            ],
            "preferred_skills": [
                "technical leadership",
                "content optimization",
                "product collaboration",
            ],
            "keywords": [
                "AI-driven search",
                "LLMs",
                "search visibility",
                "measurable business results",
            ],
        },
        "dropbox_senior_mle_conversational_ai": {
            "source_file": "samples/job_descriptions/dropbox_senior_mle_conversational_ai.md",
            "required_skills": [
                "LLMs",
                "RAG",
                "prompt engineering",
                "fine-tuning",
                "LoRA",
                "information retrieval",
                "knowledge extraction",
                "deep learning",
                "APIs",
                "vector stores",
                "ETL pipelines",
            ],
            "preferred_skills": [
                "instruction tuning",
                "model compression",
                "distillation",
                "on-device optimization",
                "privacy-first product development",
            ],
            "keywords": [
                "conversational AI",
                "document understanding",
                "AI assistants",
                "evaluation",
                "latency",
                "cost",
                "quality",
            ],
        },
        "sumologic_senior_mle_agentic_ai": {
            "source_file": "samples/job_descriptions/sumologic_senior_mle_agentic_ai.md",
            "required_skills": [
                "Agentic AI",
                "tools",
                "memory management",
                "prompting strategies",
                "reasoning chains",
                "evaluation datasets",
                "reliability",
                "observability",
                "production testing",
            ],
            "preferred_skills": [
                "log analytics",
                "large-scale data",
                "model interpretability",
                "tool-using AI agents",
            ],
            "keywords": [
                "golden datasets",
                "AI-powered insights",
                "customer experience",
                "continuous improvement",
            ],
        },
    }


def main() -> int:
    pr = parse_file(RESUME, resume_id=RESUME.stem)
    editable = extract_editable(pr)
    full = extract_full_resume(pr)
    plain = _build_plain_text(full)
    render = check_one_page(pr.latex_source)

    ats_results: dict[str, Any] = {}
    for name, kw in jd_keywords().items():
        result = check_ats(plain, kw)
        ats_results[name] = {
            "source_file": kw["source_file"],
            "score": result.score,
            "required_score": result.required_score,
            "preferred_score": result.preferred_score,
            "keyword_score": result.keyword_score,
            "required_found": result.required_found,
            "required_missing": result.required_missing,
            "preferred_found": result.preferred_found,
            "preferred_missing": result.preferred_missing,
            "keyword_hits": result.keyword_hits,
            "keyword_misses": result.keyword_misses,
        }

    report = {
        "resume": str(RESUME.relative_to(ROOT)),
        "parser": {
            "sections": [
                {
                    "section_id": s.section_id,
                    "section_type": s.section_type.value,
                    "locked": s.is_locked,
                    "statements": len(s.statements),
                    "entries": len(s.entries),
                    "skill_lines": len(s.skill_lines),
                }
                for s in pr.doc.sections
            ],
            "stmt_index_count": len(pr.stmt_index),
            "editable_sections": list(editable["editable"].keys()),
            "page_budget": editable["page_budget"],
        },
        "render": {
            "ok": render.ok,
            "overflow": render.overflow,
            "page_count": render.page_count,
            "estimated": render.estimated,
            "error": render.error,
            "pdf_bytes": len(render.pdf_bytes or b""),
        },
        "ats": ats_results,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
