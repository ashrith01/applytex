"""Naturalness scoring and refinement for generated resume bullets."""

from __future__ import annotations

import re

from latex_resume.llm import complete_json
from latex_resume.prompts import REFINE_BULLET_PROMPT

NATURALNESS_THRESHOLD: float = 0.70

_AI_BUZZWORDS: tuple[str, ...] = (
    "leveraging", "utilizing", "spearheaded", "orchestrated",
    "synergize", "synergizes", "seamlessly", "cutting-edge",
    "state-of-the-art", "robust solution", "robust approach",
    "innovative solution", "innovative approach", "impactful",
    "thought leader", "best-in-class", "mission-critical",
    "next-generation", "transformative", "rapidly evolving",
    "fast-paced environment", "move the needle", "at the forefront",
    "go-to solution", "deep dive",
)

_FABRICATED_DOMAINS: tuple[str, ...] = (
    "healthcare industry", "health care industry", "in healthcare",
    "fintech industry", "financial services industry",
    "e-commerce industry", "ecommerce industry",
    "retail industry", "manufacturing industry",
    "insurance industry", "banking industry",
)


def _word_count(text: str) -> int:
    """Approximate word count after stripping LaTeX commands."""
    stripped = re.sub(r"\\[a-zA-Z]+\{[^}]*\}|\\[a-zA-Z]+|\{|\}", " ", text)
    return len(stripped.split())


def naturalness_check(
    value: str,
    original: str,
    stmt_id: str = "",
) -> tuple[float, list[str]]:
    """Score how human-natural a rewrite is. Returns ``(score, issues)``."""
    issues: list[str] = []
    lower_val = value.lower()
    lower_orig = original.lower()
    is_skills = stmt_id.startswith("skills_")

    added_buzz = [w for w in _AI_BUZZWORDS if w in lower_val and w not in lower_orig]
    if added_buzz:
        issues.append(f"AI buzzwords added: {', '.join(added_buzz)}")

    added_domains = [d for d in _FABRICATED_DOMAINS if d in lower_val and d not in lower_orig]
    if added_domains:
        issues.append(f"Fabricated domain(s) added (not in original): {', '.join(added_domains)}")

    orig_wc = _word_count(original)
    new_wc = _word_count(value)
    if not is_skills and orig_wc > 5 and new_wc > orig_wc * 1.35:
        issues.append(
            f"Length inflation: {new_wc} words vs {orig_wc} original "
            f"({new_wc / orig_wc:.0%} of original)"
        )

    score = 1.0
    score -= 0.15 * len(added_buzz)
    score -= 0.40 * len(added_domains)
    if not is_skills and orig_wc > 5 and new_wc > orig_wc * 1.35:
        score -= 0.20

    return max(0.0, score), issues


async def refine_bullet(
    original: str,
    ai_rewrite: str,
    problems: list[str],
    llm_backend: str | None = None,
    llm_model: str | None = None,
) -> str | None:
    """Ask the LLM to make a rewrite sound more natural while preserving truth."""
    if not problems:
        return None
    prompt = REFINE_BULLET_PROMPT.format(
        original=original,
        ai_rewrite=ai_rewrite,
        problems="\n".join(f"- {p}" for p in problems),
    )
    data = await complete_json(
        prompt,
        task="refine",
        backend_override=llm_backend,
        model_override=llm_model,
    )
    refined = data.get("refined")
    return refined if isinstance(refined, str) else None
