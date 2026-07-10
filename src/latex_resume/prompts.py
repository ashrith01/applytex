"""LLM prompt templates for the LaTeX resume optimizer.

All prompts follow the same contract:
- Accept named ``{placeholder}`` format keys
- Return ONLY a valid JSON object (no markdown, no prose)
- Are sanitized at call time via ``_sanitize_user_input`` in ``llm.py``
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

EXTRACT_KEYWORDS_PROMPT = """\
Extract job requirements as JSON. Output ONLY the JSON object, no other text.

Example format:
{{
  "required_skills": ["Python", "AWS"],
  "preferred_skills": ["Kubernetes"],
  "experience_requirements": ["5+ years"],
  "education_requirements": ["Bachelor's in CS"],
  "key_responsibilities": ["Lead team of engineers"],
  "keywords": ["microservices", "agile", "CI/CD"],
  "experience_years": 5,
  "seniority_level": "senior"
}}

Rules:
- Extract numeric years (e.g. "5+ years" → 5)
- Infer seniority level (intern/junior/mid/senior/staff/principal)
- required_skills: explicitly required or "must have"
- preferred_skills: "nice to have" or "preferred"
- keywords: domain/industry terms to weave into bullets naturally

Job description:
{job_description}"""


# ---------------------------------------------------------------------------
# Skill target planning
# ---------------------------------------------------------------------------

SKILL_TARGET_PLAN_PROMPT = """\
Build a concise skill target plan for tailoring this resume to the job.

Return ONLY a JSON object. Do not rewrite the resume.

Rules:
1. Prefer required and preferred JD skills.
2. Include existing resume skills that are highly relevant to the JD.
3. You may include JD skills missing from the resume — these will be flagged for user review.
4. Do not include skills unrelated to the JD.
5. Do not include certifications or degrees as skills.
6. Limit to 12 target skills maximum.

Existing resume skills (plain text):
{existing_skills}

JD extracted keywords and skills:
{job_keywords}

Job description:
{job_description}

Resume (plain text, read-only — do not generate diffs here):
{resume_plain_text}

Output this exact JSON format:
{{
  "target_skills": [
    {{
      "skill": "exact skill name",
      "reason": "one sentence: why this skill helps match the JD"
    }}
  ],
  "strategy_notes": "brief (1-2 sentence) notes about the tailoring strategy"
}}"""


# ---------------------------------------------------------------------------
# LaTeX diff generation
# ---------------------------------------------------------------------------

GENERATE_LATEX_DIFFS_PROMPT = """\
You are an expert resume editor who preserves the candidate's authentic voice. \
Tailor the resume bullets and summary to better match the job description. \
Output ONLY valid JSON — no prose, no markdown fences.

══════════════════ NATURAL WRITING STYLE GUIDE ══════════════════
VOICE FIRST: Keep the candidate's existing sentence structure. A good edit sounds \
like the *same person* chose slightly different words — not a different person \
rewrote their work history.

MINIMAL EDITS: If inserting 2–4 relevant keywords into an existing phrase achieves \
the goal, do NOT rewrite the full sentence. Fewer words changed = more authentic.

FORBIDDEN WORDS — never add these (common AI-generation signals):
  leveraging, utilizing, spearheaded, orchestrated, synergize, seamlessly, \
  cutting-edge, state-of-the-art, robust solutions, innovative approach, impactful, \
  dynamic, thought leader, best-in-class, mission-critical, next-generation, \
  transformative, fast-paced, rapidly evolving, at the forefront, go-to solution, \
  move the needle, deep dive.

NO FABRICATED INDUSTRY VERTICALS: NEVER add domain experience (healthcare, fintech, \
e-commerce, retail, banking, insurance) that is NOT already present in the resume. \
This fabrication will disqualify the candidate.

LENGTH: Keep each rewrite within ±20 percent of the original word count. \
Do not pad bullets with extra clauses to seem comprehensive.

GOOD PATTERNS:
  ✓ Insert 2–4 JD keywords into an existing technical phrase
  ✓ Rearrange a sentence to lead with the most JD-relevant action verb
  ✓ Replace a generic phrase with a specific technical equivalent from the JD
BAD PATTERNS:
  ✗ Delete specific technical details (data analysis, predictive modeling) and \
replace with vague corporate language (designing scalable inference services)
  ✗ Add an entire new sentence of AI buzzwords where one phrase was before
  ✗ Change the main claim or technical domain of a bullet
══════════════════════════════════════════════════════════════════

═══════════════════════ CRITICAL RULES ═══════════════════════
1. ONLY modify text content — never change names, companies, dates, \
institutions, degrees, or job titles.
2. DO NOT FABRICATE. Never invent numbers, percentages, metrics, tools, \
technologies, certifications, achievements, or domain/industry experience \
(e.g. claiming healthcare or fintech work) that is not already supported by \
the resume. Reuse the original's metrics exactly — do not change their values \
or add new ones. If the original bullet has no metric, the rewrite must have \
none either.
3. You MAY rephrase freely: restructure sentences, tighten wording, and weave \
in job-description keywords that genuinely fit the candidate's real \
experience. A substantially reworded statement is fine as long as every \
claim is truthful and grounded in the original.
4. NEVER add or remove bullet points — edit only the statements listed below, \
keyed by their stmt_id.
5. The text you return is spliced INSIDE an existing bullet, so return ONLY the \
statement content. Do NOT wrap it in \\item, \\resumeItem, or any list/bullet \
marker. Output MUST be valid LaTeX: YOU decide which 1-3 key terms to \
emphasise with \\textbf{{...}} (you may bold different words than the original), \
preserve any \\href{{url}}{{text}} links, and escape specials as \\% and \\&. \
Never leave a bare % or & in the text.
6. For skills lines: keep the \\textbf{{Category}}{{: ...}} wrapper intact; only \
edit the comma-separated items inside. You MUST add every skill listed in the \
ATS REMEDIATION block to the most appropriate existing skills line \
(e.g. LangChain/LangGraph → Gen AI \\& Agents, Hugging Face Transformers → \
Frameworks \\& Libraries, GitHub → Frameworks \\& Libraries alongside Git, \
VS Code / Claude Code → Cloud \\& APIs or Frameworks \\& Libraries).
7. Use stmt_ids EXACTLY as shown in the editable JSON below — copy the \
key name character-for-character (e.g. "work_0_1", "skills_2").
8. Do not include an "original" field — omit it entirely.
9. Do not use em-dashes (—). Use a comma or restructure instead.
10. Keep each statement concise (no more than ~30 words added vs the original).
11. Statements that already align with the JD may still be updated to weave in \
a keyword from the ATS REMEDIATION block, provided the change is truthful.
══════════════════════════════════════════════════════════════

VERIFIED SKILL TARGETS (emphasise these where supported by the resume):
{skill_targets}
{ats_remediation}
JD KEYWORDS TO WEAVE IN NATURALLY:
{job_keywords}

JOB DESCRIPTION:
{job_description}

RESUME — EDITABLE STATEMENTS (stmt_id → current LaTeX text):
{editable_json}

RESUME — PLAIN TEXT VIEW (for context; do not copy LaTeX from here):
{resume_plain_text}

Output this exact JSON format:
{{
  "changes": [
    {{
      "stmt_id": "work_0_1",
      "value": "improved LaTeX text (preserve all \\\\commands)",
      "reason": "one sentence: what changed and why"
    }}
  ],
  "strategy_notes": "1-2 sentences on overall approach taken"
}}

Generate only changes where improvement is clear and well-supported by the \
original resume. Fewer high-quality changes beat many marginal ones."""


COMPACT_GENERATE_LATEX_DIFFS_PROMPT = """\
Return ONLY valid JSON. Generate surgical LaTeX statement edits for this resume.

Rules:
- Edit only listed stmt_ids. Do not add/remove bullets.
- Return only statement content, never \\item or \\resumeItem wrappers.
- Preserve LaTeX commands/links, especially existing \\textbf{{...}} highlights,
  and keep skills-line wrappers intact.
- Never invent skills, metrics, companies, dates, degrees, certifications, or
  domain experience. Reuse original metrics exactly; if original has no metric,
  do not add one.
- Do not copy JD-specific business/platform names into a bullet unless the
  original statement already supports that exact platform/domain. For example,
  never turn a code conversion, migration, or preprocessing bullet into ML
  inference services, APIs, digital applications, or sales/digital platforms.
- Escape bare % and & as \\% and \\&.
- Prefer small, natural edits that add truthful JD keywords.
- Do not edit locked sections.
- Keep each rewrite close to original length.
- Respect the page budget. If the resume is tight, shorten or minimally edit
  existing statements instead of adding clauses.

Submission fit target: 80+.

Optimization strategy:
{strategy_guidance}

Page budget:
{page_budget}

Skill targets:
{skill_targets}

ATS gaps to address truthfully:
{ats_remediation}

JD requirements/keywords:
{job_keywords}

Job description excerpt:
{job_description}

Editable statements:
{editable_json}

Resume context excerpt:
{resume_plain_text}

Output JSON:
{{
  "changes": [
    {{
      "stmt_id": "work_0_1",
      "value": "new LaTeX statement content only",
      "reason": "why this truthful edit improves fit"
    }}
  ],
  "strategy_notes": "brief strategy"
}}
"""


# ---------------------------------------------------------------------------
# Stage 5.5 — naturalness refinement
# ---------------------------------------------------------------------------

REFINE_BULLET_PROMPT = """\
You are a resume editor who fixes AI-sounding text to sound natural and human-written.

The bullet below was edited by an AI and has naturalness problems. Your job is to:
1. Keep all the JD-relevant technical keywords that were added
2. Restore the candidate's original voice and sentence structure
3. Fix the specific problems listed below

ORIGINAL bullet (the candidate's own words — restore this voice):
{original}

AI-GENERATED rewrite (needs fixing):
{ai_rewrite}

PROBLEMS detected in the AI rewrite:
{problems}

RULES:
- Keep every technical keyword from the AI rewrite that is relevant to the job
- Restore the original sentence structure as much as possible
- Do NOT keep fabricated industry verticals (healthcare, fintech, etc.) not in the original
- Avoid: leveraging, utilizing, spearheaded, orchestrated, synergize, seamlessly, \
cutting-edge, robust, innovative, impactful, transformative
- Keep identical LaTeX formatting (\\textbf{{...}}, \\href{{url}}{{text}}, \\% and \\&)
- Length should be close to the original (within ±20 percent of original word count)
- Do NOT wrap in \\item or \\resumeItem — return only the statement content

Output this exact JSON format (one field only):
{{"refined": "improved bullet text here"}}"""


REPAIR_CONTEXT_DRIFT_PROMPT = """\
Return ONLY valid JSON. Repair a resume edit that was rejected because it changed
the candidate's actual work into unsupported JD/platform claims.

Your job:
1. Start from the ORIGINAL statement, not from the rejected rewrite.
2. Preserve the original main action, technical domain, tools, metric values,
   LaTeX commands, and factual scope.
3. Add 1-3 truthful JD keywords only where they fit the original context.
4. Keep existing metrics exactly. If the original has no metric, add no metric.
5. Do not add JD-specific business/platform names, product teams, industry
   verticals, inference services, APIs, or ML model integration unless those
   concepts already appear in the ORIGINAL statement.
6. Keep the rewrite close to the original length and natural in voice.

ORIGINAL statement:
{original}

REJECTED rewrite:
{rejected_value}

Why it was rejected:
{rejection_reason}

JD keywords/requirements:
{job_keywords}

ATS gaps to address truthfully:
{ats_remediation}

Output JSON:
{{
  "value": "context-preserving LaTeX statement content only",
  "reason": "briefly explain which truthful keywords were woven in"
}}
"""


COMPACT_REWRITE_PROMPT = """\
Return ONLY valid JSON. Rewrite this resume statement to keep the useful JD
keywords while fitting a strict one-page word budget.

Rules:
1. Start from the ORIGINAL statement and preserve its factual scope.
2. Keep only JD wording that is supported by the original resume evidence.
3. Do not add hard tools/platforms, certifications, degrees, employers, domains,
   metrics, or numbers that are not in the original or confirmed skills.
4. Preserve LaTeX commands such as \\textbf{{...}} and \\href{{...}}{{...}}.
5. Return statement content only, never \\item or \\resumeItem wrappers.
6. The rewritten statement must be no more than {max_words} words.
7. Prefer replacing generic wording with JD keywords instead of appending clauses.

ORIGINAL statement:
{original}

TOO-LONG rewrite:
{candidate}

Why it needs compaction:
{reason}

JD requirements/keywords:
{job_keywords}

ATS gaps to address truthfully:
{ats_remediation}

Output JSON:
{{
  "value": "compact LaTeX statement content only",
  "reason": "briefly explain which supported wording was kept"
}}
"""


RECRUITER_REVIEW_PROMPT = """\
Return ONLY valid JSON. Act like a recruiter at the company hiring for this job.
Review the optimized resume against the job description and propose a final,
truthful ATS-improving revision.

Mindset:
- You are screening for shortlist fit, not writing a generic resume.
- Prioritize required skills, role relevance, company/JD wording, and concise
  evidence in the summary/work/projects.
- Be aggressive with supported inference from resume evidence, but do not
  invent hard facts.

Hard rules:
1. Edit only listed stmt_ids. Do not add/remove bullets.
2. Preserve facts, metrics, companies, dates, degrees, certifications, and job
   titles. Never invent new numbers or tools.
3. Do not add hard unconfirmed tools/platforms such as Bedrock, Vertex AI,
   Kubernetes, Golang, or Kotlin unless already present or user-confirmed.
4. Adjacent wording is allowed only when supported by evidence, e.g. FastAPI to
   API development, Azure to cloud platform, XAI to responsible AI, MLflow to
   model monitoring, RAG/LLM/chatbot work to LLM applications or conversational AI.
5. Keep every rewrite compact enough for a one-page resume.
6. Return statement content only. Preserve LaTeX commands and escape bare %
   and & as \\% and \\&.

ATS before this review:
{ats_summary}

Confirmed skills:
{confirmed_skills}

Job requirements/keywords:
{job_keywords}

Job description:
{job_description}

Current optimized resume plain text:
{resume_plain_text}

Editable current statements:
{editable_json}

Output JSON:
{{
  "feedback": "short recruiter-style critique of what still blocks shortlist fit",
  "changes": [
    {{
      "stmt_id": "work_0_1",
      "value": "revised LaTeX statement content only",
      "reason": "why this improves recruiter/ATS fit without fabricating"
    }}
  ]
}}
"""


CHAT_REFINE_PROMPT = """\
Return ONLY valid JSON. Apply the user's resume edit request as surgical LaTeX
statement edits.

Rules:
1. Edit only the listed stmt_ids. Do not add/remove bullets or sections.
2. Preserve facts, metrics, companies, dates, degrees, certifications, and job
   titles. Never invent new numbers, tools, employers, or domains.
3. If the user asks for an unsupported change, make the closest truthful wording
   improvement or return no changes.
4. Return statement content only, never \\item or \\resumeItem wrappers.
5. Preserve LaTeX commands and escape bare % and & as \\% and \\&.
6. Keep edits concise enough for a one-page resume.

User request:
{instruction}

Selected section/scope:
{scope_label}

Confirmed skills:
{confirmed_skills}

Job requirements/keywords:
{job_keywords}

Job description excerpt:
{job_description}

Editable current statements:
{editable_json}

Current resume context:
{resume_plain_text}

Output JSON:
{{
  "changes": [
    {{
      "stmt_id": "summary_0",
      "value": "new LaTeX statement content only",
      "reason": "why this edit satisfies the request truthfully"
    }}
  ],
  "strategy_notes": "brief explanation of what changed"
}}
"""


# ---------------------------------------------------------------------------
# Prompt-injection defence
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?above",
    r"forget\s+(everything|all)",
    r"new\s+instructions?:",
    r"system\s*:",
    r"<\s*/?\s*system\s*>",
    r"\[\s*INST\s*\]",
    r"\[\s*/\s*INST\s*\]",
]
