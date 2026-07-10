"""Tests for the pure functions in optimizer.py.

No LLM calls are made — these exercise validate_changes and
verify_skill_target_plan deterministically.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from latex_resume.ats import ATSResult
from latex_resume.parser import parse
from latex_resume.models import PageBudget
from latex_resume.renderer import RenderResult
from latex_resume.optimizer import (
    apply_manual_statement_edits,
    _deterministic_compact_statement,
    _deterministic_skills_patch,
    _escape_latex_specials,
    _fabricated_metrics,
    _filter_confirmed_patch_skills,
    _build_ats_remediation,
    _prepare_raw_changes_for_validation,
    _strip_item_wrapper,
    build_skill_confirmation_candidates,
    extract_job_keywords_fast,
    generate_latex_diffs,
    generate_skill_target_plan_fast,
    LLMTaskRoute,
    run_optimization_pipeline,
    split_skill_confirmation_candidates,
    validate_changes,
    verify_skill_target_plan,
)


def test_deterministic_compaction_removes_trailing_clause_safely() -> None:
    original = (
        r"Built scalable \textbf{RAG pipelines} with Python and FastAPI, improving "
        r"retrieval quality through evaluation and production monitoring."
    )

    compacted = _deterministic_compact_statement(original, max_words=8)

    assert compacted == r"Built scalable \textbf{RAG pipelines} with Python and FastAPI."
    assert compacted.count("{") == compacted.count("}")
    assert "RAG pipelines" in compacted


def test_deterministic_compaction_refuses_to_cut_unbalanced_latex() -> None:
    original = (
        r"Built \textbf{RAG pipelines, with Python and FastAPI, improving "
        r"retrieval quality through evaluation and production monitoring."
    )

    assert _deterministic_compact_statement(original, max_words=8) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(original_text: str) -> object:
    """Create a minimal StmtSpan-like object for validate_changes."""
    return SimpleNamespace(original_text=original_text)


def _make_index(*items: tuple[str, str]) -> dict:
    """Build a fake stmt_index from (stmt_id, original_text) pairs."""
    return {sid: _make_span(text) for sid, text in items}


# ---------------------------------------------------------------------------
# verify_skill_target_plan
# ---------------------------------------------------------------------------


class TestVerifySkillTargetPlan:
    def test_valid_plan(self):
        plan = {
            "target_skills": [
                {"skill": "Python", "reason": "Core JD requirement"},
                {"skill": "AWS", "reason": "Cloud infra mentioned"},
            ],
            "strategy_notes": "Emphasise cloud and Python.",
        }
        ok, errors = verify_skill_target_plan(plan)
        assert ok
        assert errors == []

    def test_missing_target_skills_key(self):
        ok, errors = verify_skill_target_plan({"strategy_notes": "..."})
        assert not ok
        assert any("target_skills" in e for e in errors)

    def test_target_skills_not_a_list(self):
        ok, errors = verify_skill_target_plan(
            {"target_skills": "Python, AWS", "strategy_notes": "..."}
        )
        assert not ok

    def test_too_many_skills(self):
        skills = [{"skill": f"Skill{i}", "reason": "..."} for i in range(13)]
        ok, errors = verify_skill_target_plan(
            {"target_skills": skills, "strategy_notes": "..."}
        )
        assert not ok
        assert any("Too many" in e for e in errors)

    def test_exactly_12_skills_ok(self):
        skills = [{"skill": f"Skill{i}", "reason": "..."} for i in range(12)]
        ok, errors = verify_skill_target_plan(
            {"target_skills": skills, "strategy_notes": "..."}
        )
        assert ok

    def test_missing_skill_key(self):
        plan = {
            "target_skills": [{"reason": "Important"}],
            "strategy_notes": "...",
        }
        ok, errors = verify_skill_target_plan(plan)
        assert not ok
        assert any("'skill'" in e for e in errors)

    def test_missing_reason_key(self):
        plan = {
            "target_skills": [{"skill": "Python"}],
            "strategy_notes": "...",
        }
        ok, errors = verify_skill_target_plan(plan)
        assert not ok
        assert any("'reason'" in e for e in errors)

    def test_missing_strategy_notes(self):
        plan = {
            "target_skills": [{"skill": "Python", "reason": "..."}],
        }
        ok, errors = verify_skill_target_plan(plan)
        assert not ok
        assert any("strategy_notes" in e for e in errors)

    def test_non_dict_item_in_list(self):
        plan = {
            "target_skills": ["Python"],
            "strategy_notes": "...",
        }
        ok, errors = verify_skill_target_plan(plan)
        assert not ok


# ---------------------------------------------------------------------------
# validate_changes
# ---------------------------------------------------------------------------


class TestValidateChanges:
    def _base_index(self) -> dict:
        return _make_index(
            ("work_0_0", "Original bullet text."),
            ("proj_1_0", "A project bullet."),
            ("summary_0", "A short summary."),
            ("skills_0", "Python, Java"),
        )

    def test_valid_change_accepted(self):
        """Accepted change has original populated from stmt_index, not from input."""
        index = self._base_index()
        changes = [
            {
                "stmt_id": "work_0_0",
                "value": "Improved bullet with Python and AWS integration.",
                "reason": "Added relevant tech keywords.",
            }
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1
        assert len(rejected) == 0
        assert accepted[0]["stmt_id"] == "work_0_0"
        # original is always populated from stmt_index
        assert accepted[0]["original"] == "Original bullet text."

    def test_original_from_index_not_input(self):
        """Even if model sends a wrong 'original', accepted diff uses the real text."""
        index = self._base_index()
        changes = [
            {
                "stmt_id": "work_0_0",
                "original": "WRONG TEXT FROM MODEL",   # model got it wrong
                "value": "Improved bullet.",
                "reason": "test",
            }
        ]
        accepted, rejected = validate_changes(changes, index)
        # Change is accepted (original mismatch no longer a rejection gate)
        assert len(accepted) == 1
        # But original in the diff is the real text, not the model's wrong copy
        assert accepted[0]["original"] == "Original bullet text."

    def test_unknown_stmt_id_rejected(self):
        index = self._base_index()
        changes = [{"stmt_id": "work_99_0", "value": "something", "reason": "test"}]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert len(rejected) == 1
        assert "not found" in rejected[0]["rejection_reason"]

    def test_locked_section_rejected(self):
        """edu_ prefix → locked."""
        index = {"edu_0_0": _make_span("Bachelor of Science")}
        changes = [{"stmt_id": "edu_0_0", "value": "Master of Science", "reason": "bump"}]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert len(rejected) == 1
        assert "locked" in rejected[0]["rejection_reason"]

    def test_empty_value_rejected(self):
        index = self._base_index()
        changes = [{"stmt_id": "work_0_0", "value": "", "reason": "test"}]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert "empty or identical" in rejected[0]["rejection_reason"]

    def test_identical_value_rejected(self):
        """Value identical to the real current text → rejected."""
        index = self._base_index()
        changes = [
            {
                "stmt_id": "work_0_0",
                "value": "Original bullet text.",   # same as stmt_index text
                "reason": "no change",
            }
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert "identical" in rejected[0]["rejection_reason"]

    def test_duplicate_stmt_id_rejected(self):
        index = self._base_index()
        changes = [
            {"stmt_id": "work_0_0", "value": "First edit.", "reason": "first"},
            {"stmt_id": "work_0_0", "value": "Second edit.", "reason": "second duplicate"},
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert "duplicate" in rejected[0]["rejection_reason"]

    def test_multiple_valid_changes(self):
        index = self._base_index()
        changes = [
            {"stmt_id": "work_0_0", "value": "Improved work bullet.", "reason": "reason 1"},
            {"stmt_id": "proj_1_0", "value": "Enhanced project bullet with ML details.", "reason": "reason 2"},
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 2
        assert len(rejected) == 0

    def test_mixed_valid_and_invalid(self):
        index = self._base_index()
        changes = [
            {"stmt_id": "work_0_0", "value": "Improved.", "reason": "ok"},
            {"stmt_id": "work_99_0", "value": "something", "reason": "bad"},
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1
        assert len(rejected) == 1

    def test_pub_prefix_locked(self):
        """pub_ prefix is locked; publications are extracted for display, not edited."""
        index = _make_index(("pub_0_0", "A publication bullet."))
        changes = [{"stmt_id": "pub_0_0", "value": "Revised publication bullet.", "reason": "improve"}]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert len(rejected) == 1
        assert "locked" in rejected[0]["rejection_reason"]

    def test_empty_change_list(self):
        index = self._base_index()
        accepted, rejected = validate_changes([], index)
        assert accepted == []
        assert rejected == []

    def test_bare_specials_escaped_in_accepted_value(self):
        """Bare % and & in the model's value are escaped on the accepted change.

        The 50% metric is present in the original, so the fabrication gate
        (gate 5) does not fire — this test isolates escaping behaviour.
        """
        index = _make_index(("work_0_0", r"Cut costs by 50\% across R\&D."))
        changes = [
            {"stmt_id": "work_0_0", "value": "Slashed costs by 50% & accelerated R&D delivery.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1, rejected
        assert accepted[0]["value"] == r"Slashed costs by 50\% \& accelerated R\&D delivery."

    def test_escape_induced_noop_rejected(self):
        """A value that only differs from the original by an unescaped special,
        and so becomes identical once escaped, is rejected — not spliced back."""
        index = _make_index(("work_0_0", r"Improved uptime by 99\% reliability."))
        changes = [
            # model dropped the backslash before %; escaping restores the original
            {"stmt_id": "work_0_0", "value": "Improved uptime by 99% reliability.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert "identical" in rejected[0]["rejection_reason"]

    def test_non_dict_change_rejected(self):
        index = self._base_index()
        accepted, rejected = validate_changes(["not a dict", 42, None], index)
        assert len(accepted) == 0
        assert len(rejected) == 3
        assert all("not a JSON object" in r["rejection_reason"] for r in rejected)

    def test_missing_or_non_string_stmt_id_rejected(self):
        index = self._base_index()
        changes = [
            {"value": "no stmt_id", "reason": "x"},
            {"stmt_id": 123, "value": "numeric id", "reason": "x"},
            {"stmt_id": "", "value": "empty id", "reason": "x"},
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert len(rejected) == 3
        assert all("stmt_id" in r["rejection_reason"] for r in rejected)

    def test_non_string_value_rejected(self):
        """A non-string value (e.g. model returned a list/null) is rejected, not crashed on."""
        index = self._base_index()
        changes = [
            {"stmt_id": "work_0_0", "value": ["a", "list"], "reason": "x"},
            {"stmt_id": "proj_1_0", "value": None, "reason": "x"},
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert len(rejected) == 2
        assert "not a string" in rejected[0]["rejection_reason"]

    def test_fabricated_percentage_rejected(self):
        """A rewrite that invents a percentage absent from the original is rejected."""
        index = _make_index(("work_0_0", "Reduced manual migration effort substantially."))
        changes = [
            {
                "stmt_id": "work_0_0",
                "value": r"Reduced manual migration effort by over \textbf{70\%}.",
                "reason": "fabricated metric",
            }
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert "fabrication" in rejected[0]["rejection_reason"]
        assert "70%" in rejected[0]["rejection_reason"]

    def test_fabricated_count_rejected(self):
        """Inventing an 'N+' count not in the original is rejected."""
        index = _make_index(("proj_0_0", "Built RAG pipelines for enterprise clients."))
        changes = [
            {"stmt_id": "proj_0_0", "value": "Built 3+ RAG pipelines for enterprise clients.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 0
        assert "3+" in rejected[0]["rejection_reason"]

    def test_existing_metric_preserved_accepted(self):
        """Reusing the original's own metric (even reworded around it) is fine."""
        index = _make_index(("work_0_0", r"Cut migration effort by \textbf{70\%} overall."))
        changes = [
            {"stmt_id": "work_0_0", "value": r"Reduced cross-language migration effort by 70\% at enterprise scale.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1
        assert len(rejected) == 0

    def test_keyword_addition_without_metric_accepted(self):
        """Adding non-numeric JD keywords (no new metric) passes the fabrication gate."""
        index = _make_index(("work_0_0", "Built data pipelines for the platform."))
        changes = [
            {"stmt_id": "work_0_0", "value": "Built cloud-native data pipelines with FastAPI for the platform.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1

    def test_unsupported_platform_rewrite_rejected(self):
        """JD platform language cannot replace the original technical claim."""
        original = (
            r"Designed and implemented a code conversion module with integrated "
            r"preprocessing pipelines, reducing manual code migration "
            r"\textbf{effort by over 70\%} and supporting enterprise-scale "
            r"performance and reliability."
        )
        index = _make_index(("work_0_0", original))
        changes = [
            {
                "stmt_id": "work_0_0",
                "value": (
                    r"Developed scalable inference services and APIs to integrate "
                    r"ML models into M\&R Sales digital platforms and applications, "
                    r"enabling seamless integration with enterprise-scale systems."
                ),
                "reason": "JD rewrite",
            }
        ]

        accepted, rejected = validate_changes(changes, index)

        assert accepted == []
        assert len(rejected) == 1
        reason = rejected[0]["rejection_reason"]
        assert "unsupported claim/domain/platform" in reason
        assert "m r sales" in reason
        assert "digital platforms" in reason

    def test_existing_claim_phrase_can_be_kept(self):
        """A phrase is only rejected when newly introduced."""
        index = _make_index(
            (
                "work_0_0",
                "Built inference services to integrate ML models into digital platforms.",
            )
        )
        changes = [
            {
                "stmt_id": "work_0_0",
                "value": "Built production inference services to integrate ML models into digital platforms.",
                "reason": "tightened wording",
            }
        ]

        accepted, rejected = validate_changes(changes, index)

        assert len(accepted) == 1, rejected
        assert rejected == []

    def test_version_number_skill_not_flagged_as_metric(self):
        """Version-like skill tokens (GPT-4, S3) are not percentage/count metrics → accepted."""
        index = _make_index(
            ("skills_0", r"\textbf{Cloud}{: Azure, Pinecone}")
        )
        changes = [
            {"stmt_id": "skills_0", "value": r"\textbf{Cloud}{: Azure, Pinecone, AWS S3, GPT-4}", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1, rejected

    def test_leading_item_stripped_from_value(self):
        """A model that prepends \\item has it stripped so the splice stays clean."""
        index = _make_index(("work_0_0", "Original bullet text."))
        changes = [
            {"stmt_id": "work_0_0", "value": r"\item Rewritten bullet with cloud focus.", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1, rejected
        assert accepted[0]["value"] == "Rewritten bullet with cloud focus."

    def test_resumeitem_wrapper_unwrapped_from_value(self):
        """A full \\resumeItem{...} wrapper is unwrapped to its inner content."""
        index = _make_index(("work_0_0", "Original bullet text."))
        changes = [
            {"stmt_id": "work_0_0", "value": r"\resumeItem{Rewritten with \textbf{NLP} focus.}", "reason": "x"}
        ]
        accepted, rejected = validate_changes(changes, index)
        assert len(accepted) == 1, rejected
        assert accepted[0]["value"] == r"Rewritten with \textbf{NLP} focus."

    def test_existing_textbf_highlight_reapplied_when_model_drops_it(self):
        index = _make_index(("work_0_0", r"Built \textbf{Azure} data pipelines."))
        changes = [
            {"stmt_id": "work_0_0", "value": "Built Azure data pipelines for ML workloads.", "reason": "x"}
        ]

        accepted, rejected = validate_changes(changes, index)

        assert len(accepted) == 1, rejected
        assert accepted[0]["value"] == r"Built \textbf{Azure} data pipelines for ML workloads."


def test_prepare_raw_changes_normalizes_summary_and_collapses_duplicates():
    index = _make_index(
        ("summary_0", "A short summary."),
        ("skills_1", "Python"),
    )
    changes = [
        {"stmt_id": "summary", "value": "A stronger summary.", "reason": "x"},
        {"stmt_id": "skills_1", "value": "Python, Azure", "reason": "first"},
        {"stmt_id": "skills_1", "value": "Python, Azure, Databricks", "reason": "second"},
    ]

    prepared = _prepare_raw_changes_for_validation(changes, index)

    assert prepared == [
        {"stmt_id": "summary_0", "value": "A stronger summary.", "reason": "x"},
        {"stmt_id": "skills_1", "value": "Python, Azure, Databricks", "reason": "second"},
    ]


# ---------------------------------------------------------------------------
# _fabricated_metrics
# ---------------------------------------------------------------------------


class TestFabricatedMetrics:
    def test_new_percentage_detected(self):
        assert _fabricated_metrics("up 70%", "no metric here") == {"70%"}

    def test_escaped_and_unescaped_percent_equal(self):
        # original has \%, value has bare % → same token, not fabrication
        assert _fabricated_metrics("up 70%", r"up 70\%") == set()

    def test_new_plus_count_detected(self):
        assert _fabricated_metrics("6+ languages", "several languages") == {"6+"}

    def test_plain_numbers_not_metrics(self):
        # version-like numbers are not flagged
        assert _fabricated_metrics("GPT-4 and S3", "no numbers") == set()

    def test_reused_metric_not_flagged(self):
        assert _fabricated_metrics("by 70% now", "was 70% before") == set()


# ---------------------------------------------------------------------------
# _strip_item_wrapper
# ---------------------------------------------------------------------------


class TestStripItemWrapper:
    def test_strips_bare_leading_item(self):
        assert _strip_item_wrapper(r"\item Designed a module.") == "Designed a module."

    def test_unwraps_resumeitem(self):
        assert _strip_item_wrapper(r"\resumeItem{Designed a module.}") == "Designed a module."

    def test_unwraps_nested_item_inside_resumeitem(self):
        assert _strip_item_wrapper(r"\resumeItem{\item Designed a module.}") == "Designed a module."

    def test_plain_content_unchanged(self):
        assert _strip_item_wrapper(r"Designed a \textbf{module}.") == r"Designed a \textbf{module}."

    def test_inner_textbf_preserved(self):
        assert _strip_item_wrapper(r"\item Built \textbf{6+} pipelines.") == r"Built \textbf{6+} pipelines."


# ---------------------------------------------------------------------------
# _escape_latex_specials
# ---------------------------------------------------------------------------


class TestEscapeLatexSpecials:
    def test_escapes_bare_percent(self):
        assert _escape_latex_specials("up 50% gain") == r"up 50\% gain"

    def test_escapes_bare_ampersand(self):
        assert _escape_latex_specials("R&D team") == r"R\&D team"

    def test_leaves_already_escaped_untouched(self):
        already = r"up 50\% and R\&D"
        assert _escape_latex_specials(already) == already

    def test_no_specials_unchanged(self):
        assert _escape_latex_specials("plain text") == "plain text"


def test_pipeline_stage4_failure_returns_original_artifact(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python and ML experience.}
\section{Skills}
Python, ML
\end{document}
"""
    pr = parse(tex)

    async def fake_keywords(_jd):
        return {"required_skills": ["Python"], "preferred_skills": [], "keywords": ["ML"]}

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        raise RuntimeError("diff prompt too large")

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "extract_job_keywords", fake_keywords)
    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(run_optimization_pipeline(pr, "Need Python ML"))

    assert result.modified_latex == tex
    assert result.validated_changes == {}
    assert any("Stage 4" in warning for warning in result.warnings)


def test_unconfirmed_skills_are_not_auto_patched(monkeypatch):
    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.delenv("SMARTJOBAPPLY_CONFIRMED_SKILLS", raising=False)

    allowed, skipped = _filter_confirmed_patch_skills(["LangChain", "Azure"])

    assert allowed == []
    assert skipped == ["LangChain", "Azure"]


def test_confirmed_skills_can_be_patched(monkeypatch):
    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.delenv("SMARTJOBAPPLY_CONFIRMED_SKILLS", raising=False)

    allowed, skipped = _filter_confirmed_patch_skills(
        ["LangChain", "Azure"],
        confirmed_skills=["LangChain"],
    )

    assert allowed == ["LangChain"]
    assert skipped == ["Azure"]


def test_confirmed_skills_can_also_come_from_env(monkeypatch):
    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.setenv("SMARTJOBAPPLY_CONFIRMED_SKILLS", "Azure")

    allowed, skipped = _filter_confirmed_patch_skills(["LangChain", "Azure"])

    assert allowed == ["Azure"]
    assert skipped == ["LangChain"]


def test_skill_confirmation_candidates_filter_jd_themes():
    candidates = build_skill_confirmation_candidates(
        [
            "VS Code with GitHub Copilot or Codex or Claude Code",
            "Digital experience use cases",
            "Conversational AI for Digital and eCommerce platforms",
            "LangChain",
            "Hugging Face Transformers",
        ]
    )

    assert candidates == [
        "VS Code",
        "Codex",
        "LangChain",
        "Hugging Face Transformers",
    ]


def test_skill_confirmation_candidates_extract_cloud_vendor_from_theme():
    candidates, themes = split_skill_confirmation_candidates(
        [
            "Cloud Platform - Azure",
            "Communication skills",
            "Healthcare industry knowledge",
        ]
    )

    assert candidates == ["Azure"]
    assert themes == ["Communication skills", "Healthcare industry knowledge"]


def test_fast_job_keyword_extractor_finds_common_ai_cloud_skills():
    jd = """
    Required qualifications: experience with Python, Azure, RAG, and LangChain.
    Preferred: GitHub Copilot or Codex, plus healthcare industry knowledge.
    Need 3+ years building production ML systems.
    """

    keywords = extract_job_keywords_fast(jd)

    assert keywords["extraction_method"] == "fast_local"
    assert "Python" in keywords["required_skills"]
    assert "Azure" in keywords["required_skills"]
    assert "RAG" in keywords["required_skills"]
    assert "LangChain" in keywords["required_skills"]
    assert "Codex" in keywords["preferred_skills"]
    assert keywords["experience_years"] == 3
    assert "healthcare" in keywords["keywords"]


def test_ats_aliases_match_cloud_and_ml_platform_terms() -> None:
    from latex_resume.ats import check_ats

    resume = (
        "Built preprocessing pipelines and FastAPI model serving on AWS Bedrock "
        "and Google Vertex AI with MLflow production model reviews. Skills: ML / DL."
    )
    keywords = {
        "required_skills": [
            "Bedrock",
            "Vertex AI",
            "ML pipelines",
            "Inference services",
            "API development",
            "Model monitoring",
            "Machine Learning",
        ],
        "preferred_skills": [],
        "keywords": [],
    }

    result = check_ats(resume, keywords)

    assert result.required_score == 100
    assert result.required_missing == []


def test_ats_excludes_unconfirmed_hard_tools_from_submission_score() -> None:
    from latex_resume.ats import check_ats

    resume = "AI engineer with Python, FastAPI API development, and Azure cloud systems."
    keywords = {
        "required_skills": ["Python", "Amazon Bedrock", "Vertex AI"],
        "preferred_skills": ["API development"],
        "keywords": ["cloud"],
    }

    result = check_ats(resume, keywords)

    assert result.score == result.submission_score
    assert result.score >= 80
    assert result.raw_score < result.submission_score
    assert result.excluded_unconfirmed_skills == ["Amazon Bedrock", "Vertex AI"]
    assert result.required_missing == []


def test_ats_confirmed_hard_tools_are_included_in_submission_score() -> None:
    from latex_resume.ats import check_ats

    resume = "AI engineer with Python and FastAPI API development."
    keywords = {
        "required_skills": ["Python", "Amazon Bedrock"],
        "preferred_skills": [],
        "keywords": [],
    }

    result = check_ats(resume, keywords, confirmed_skills=["Amazon Bedrock"])

    assert result.excluded_unconfirmed_skills == []
    assert result.required_missing == ["Amazon Bedrock"]
    assert result.score < 80
    assert result.raw_score == result.submission_score


def test_ats_aliases_match_supported_responsible_ai_keywords() -> None:
    from latex_resume.ats import check_ats

    resume = (
        "Architected an XAI framework for model interpretability and automated "
        "black-box model auditing."
    )
    keywords = {
        "required_skills": [],
        "preferred_skills": ["Communication skills"],
        "keywords": ["bias assessment", "auditability", "governance", "transparency"],
    }

    result = check_ats(resume + " Translating business data for stakeholders.", keywords)

    assert result.preferred_missing == []
    assert result.keyword_misses == []


def test_deep_keyword_extractor_falls_back_to_fast_local(monkeypatch):
    jd = "Required qualifications: experience with Python and LangChain."
    calls = []

    async def fail_complete_json(*_args, **_kwargs):
        calls.append(_kwargs)
        raise ValueError("Ollama did not return valid JSON")

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "complete_json", fail_complete_json)

    keywords = asyncio.run(
        opt.extract_job_keywords_with_fallback(
            jd,
            llm_backend="codex",
            llm_model="gpt-5.4",
        )
    )

    assert keywords["extraction_method"] == "fast_local_fallback"
    assert keywords["llm_error"] == "Ollama did not return valid JSON"
    assert keywords["llm_backend"] == "codex"
    assert keywords["llm_model"] == "gpt-5.4"
    assert calls[0]["backend_override"] == "codex"
    assert calls[0]["model_override"] == "gpt-5.4"
    assert "Python" in keywords["required_skills"]
    assert "LangChain" in keywords["required_skills"]


def test_pipeline_records_confirmation_required_skills(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def fake_keywords(_jd):
        return {
            "required_skills": ["Python", "LangChain"],
            "preferred_skills": [],
            "keywords": [],
        }

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "LangChain", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.delenv("SMARTJOBAPPLY_CONFIRMED_SKILLS", raising=False)
    monkeypatch.setattr(opt, "extract_job_keywords", fake_keywords)
    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(run_optimization_pipeline(pr, "Need Python and LangChain"))

    assert "LangChain" in result.confirmation_required_skills
    assert result.validated_changes == {}
    assert "LangChain" not in result.modified_latex


def test_pipeline_uses_preextracted_job_keywords(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def should_not_call(_jd):
        raise AssertionError("extract_job_keywords should be skipped")

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "extract_job_keywords", should_not_call)
    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={
                "required_skills": ["Python"],
                "preferred_skills": [],
                "keywords": [],
            },
        )
    )

    assert result.job_keywords["required_skills"] == ["Python"]


def test_fast_skill_target_plan_prioritizes_required_skills():
    plan = generate_skill_target_plan_fast(
        existing_skills="Languages: Python; Cloud: Azure",
        job_keywords={
            "required_skills": ["Python", "LangChain"],
            "preferred_skills": ["Azure", "RAG"],
        },
    )

    assert plan["planning_method"] == "fast_local_fallback"
    assert [item["skill"] for item in plan["target_skills"]] == [
        "Python",
        "LangChain",
        "Azure",
        "RAG",
    ]
    assert "already present" in plan["target_skills"][0]["reason"]


def test_fast_skill_target_plan_marks_confirmed_skills_truthful():
    plan = generate_skill_target_plan_fast(
        existing_skills="Languages: Python",
        job_keywords={
            "required_skills": ["Python", "LangChain"],
            "preferred_skills": ["GitHub Copilot"],
        },
        confirmed_skills=["LangChain", "GitHub Copilot"],
    )

    reasons = {item["skill"]: item["reason"] for item in plan["target_skills"]}
    assert "user-confirmed" in reasons["LangChain"]
    assert "user-confirmed" in reasons["GitHub Copilot"]


def test_ats_remediation_adds_only_confirmed_skill_gaps():
    ats = SimpleNamespace(
        score=50.0,
        required_missing=["LangChain", "R"],
        preferred_missing=["GitHub Copilot"],
        keyword_misses=[],
    )
    plan = {
        "target_skills": [
            {"skill": "LangChain", "reason": "required"},
            {"skill": "R", "reason": "required"},
            {"skill": "GitHub Copilot", "reason": "preferred"},
        ]
    }

    remediation = _build_ats_remediation(
        ats,
        plan,
        confirmed_skills=["LangChain", "GitHub Copilot"],
    )

    confirmed_block = remediation.split("UNCONFIRMED SKILL GAPS")[0]
    unconfirmed_block = remediation.split("UNCONFIRMED SKILL GAPS")[1]
    assert "LangChain" in confirmed_block
    assert "GitHub Copilot" in confirmed_block
    assert "  • R" not in confirmed_block
    assert "  • R" in unconfirmed_block


def test_pipeline_continues_when_skill_plan_llm_fails(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def failing_plan(**_kwargs):
        raise RuntimeError("planner offline")

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", failing_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={
                "required_skills": ["Python"],
                "preferred_skills": [],
                "keywords": [],
            },
        )
    )

    assert result.skill_target_plan["planning_method"] == "fast_local_fallback"
    assert any("Stage 2 LLM skill plan unavailable" in warning for warning in result.warnings)
    assert not any("Stage 2 (skill target plan) failed" in warning for warning in result.warnings)
    assert result.modified_latex


def test_pipeline_uses_per_task_llm_routes(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)
    seen: dict[str, tuple[str | None, str | None]] = {}

    async def fake_plan(**kwargs):
        seen["plan"] = (kwargs.get("llm_backend"), kwargs.get("llm_model"))
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**kwargs):
        seen["diff"] = (kwargs.get("llm_backend"), kwargs.get("llm_model"))
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={"required_skills": ["Python"], "preferred_skills": [], "keywords": []},
            llm_routes={
                "plan": LLMTaskRoute("groq", "llama-3.3-70b-versatile"),
                "diff": LLMTaskRoute("ollama", "qwen2.5-coder:7b"),
            },
        )
    )

    assert seen["plan"] == ("groq", "llama-3.3-70b-versatile")
    assert seen["diff"] == ("ollama", "qwen2.5-coder:7b")


def test_generate_latex_diffs_includes_strategy_guidance(monkeypatch):
    import latex_resume.optimizer as opt

    seen: dict[str, str] = {}

    async def fake_complete_json(prompt: str, **_kwargs):
        seen["prompt"] = prompt
        return {"changes": [], "strategy_notes": "x"}

    monkeypatch.setattr(opt, "complete_json", fake_complete_json)

    asyncio.run(
        generate_latex_diffs(
            editable_json={"summary_0": "AI engineer."},
            resume_plain_text="AI engineer with Python.",
            skill_target_plan={
                "target_skills": [{"skill": "Python", "reason": "required"}]
            },
            job_keywords={"required_skills": ["Python"], "preferred_skills": [], "keywords": []},
            job_description="Need Python",
            optimization_strategy="ats_aggressive",
        )
    )

    assert "ATS aggressive" in seen["prompt"]
    assert "close every supported required" in seen["prompt"]


def test_pipeline_records_selected_optimization_strategy(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)
    seen: dict[str, str] = {}

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**kwargs):
        seen["strategy"] = kwargs["optimization_strategy"]
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={"required_skills": ["Python"], "preferred_skills": [], "keywords": []},
            optimization_strategy="recruiter_readable",
        )
    )

    assert result.optimization_strategy == "recruiter_readable"
    assert seen["strategy"] == "recruiter_readable"


def test_pipeline_repairs_unsupported_platform_rewrite(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Experience}
\textbf{Company}\\
\begin{itemize}
\item Designed and implemented a code conversion module with integrated preprocessing pipelines, reducing manual code migration \textbf{effort by over 70\%} and supporting enterprise-scale performance and reliability.
\end{itemize}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {
            "changes": [
                {
                    "stmt_id": "work_0_0",
                    "value": (
                        r"Developed scalable inference services and APIs to integrate "
                        r"ML models into M\&R Sales digital platforms and applications, "
                        r"enabling seamless integration with enterprise-scale systems."
                    ),
                    "reason": "bad platform rewrite",
                }
            ],
            "strategy_notes": "x",
        }

    async def fake_complete_json(*_args, **_kwargs):
        return {
            "value": (
                r"Designed and implemented a code conversion module with integrated "
                r"preprocessing pipelines for enterprise AI workflows, reducing manual "
                r"code migration \textbf{effort by over 70\%} while improving "
                r"performance and reliability."
            ),
            "reason": "Preserved migration context while adding enterprise AI wording.",
        }

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)
    monkeypatch.setattr(opt, "complete_json", fake_complete_json)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python for enterprise AI platforms",
            job_keywords={
                "required_skills": ["Python"],
                "preferred_skills": [],
                "keywords": ["enterprise AI"],
            },
        )
    )

    repaired = next(change for change in result.diff if change["stmt_id"] == "work_0_0")
    assert "code conversion module" in repaired["value"]
    assert "enterprise AI workflows" in repaired["value"]
    assert "M\\&R Sales" not in repaired["value"]
    assert "inference services" not in repaired["value"]
    assert not any(
        "unsupported claim/domain/platform" in change.get("rejection_reason", "")
        for change in result.rejected_changes
    )
    assert any("Repaired 1 unsafe rewrite" in warning for warning in result.warnings)


def test_pipeline_prunes_low_roi_edit_to_fix_overflow(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {
            "changes": [
                {
                    "stmt_id": "summary_0",
                    "value": "AI engineer with Python experience. EXTRA OVERFLOW PHRASE.",
                    "reason": "low ROI keyword addition",
                }
            ],
            "strategy_notes": "x",
        }

    async def fake_complete_json(*_args, **_kwargs):
        return {"value": "Python AI engineer.", "reason": "compact"}

    def fake_check_one_page(tex_source: str) -> RenderResult:
        overflow = "EXTRA OVERFLOW PHRASE" in tex_source
        return RenderResult(
            ok=True,
            page_count=2 if overflow else 1,
            overflow=overflow,
            pdf_bytes=b"%PDF",
        )

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)
    monkeypatch.setattr(opt, "complete_json", fake_complete_json)
    monkeypatch.setattr(opt, "check_one_page", fake_check_one_page)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={
                "required_skills": ["Python"],
                "preferred_skills": [],
                "keywords": [],
            },
        )
    )

    assert not result.overflow
    assert result.page_count == 1
    assert result.ats_target_met
    assert result.validated_changes == {"summary_0": "Python AI engineer."}
    assert result.compacted_changes
    assert "EXTRA OVERFLOW PHRASE" not in result.modified_latex
    assert any("Compacted applied edit" in warning for warning in result.warnings)


def test_one_page_strict_prunes_overflow_even_when_ats_below_target(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Bedrock", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {
            "changes": [
                {
                    "stmt_id": "summary_0",
                    "value": "AI engineer with Python experience. EXTRA OVERFLOW PHRASE.",
                    "reason": "keyword addition",
                }
            ],
            "strategy_notes": "x",
        }

    async def fake_complete_json(*_args, **_kwargs):
        return {"value": "Python engineer.", "reason": "compact"}

    def fake_check_one_page(tex_source: str) -> RenderResult:
        overflow = "EXTRA OVERFLOW PHRASE" in tex_source
        return RenderResult(
            ok=True,
            page_count=2 if overflow else 1,
            overflow=overflow,
            pdf_bytes=b"%PDF",
        )

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)
    monkeypatch.setattr(opt, "complete_json", fake_complete_json)
    monkeypatch.setattr(opt, "check_one_page", fake_check_one_page)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python and Bedrock",
            job_keywords={
                "required_skills": ["Python", "Bedrock"],
                "preferred_skills": [],
                "keywords": [],
            },
            optimization_strategy="one_page_strict",
        )
    )

    assert not result.overflow
    assert result.page_count == 1
    assert result.ats_target_met
    assert result.ats_after.raw_score < result.ats_after.submission_score
    assert result.ats_after.excluded_unconfirmed_skills == ["Bedrock"]
    assert result.validated_changes == {"summary_0": "Python engineer."}
    assert "EXTRA OVERFLOW PHRASE" not in result.modified_latex
    assert any("Compacted applied edit" in warning for warning in result.warnings)


def test_pipeline_rejects_wordy_rewrite_when_page_budget_is_tight(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)
    pr.doc.page_budget = PageBudget(
        estimated_word_count=415,
        max_word_budget=420,
        estimated_bullet_count=18,
        max_bullet_budget=18,
    )

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {
            "changes": [
                {
                    "stmt_id": "summary_0",
                    "value": (
                        "AI engineer with Python experience across cloud-native "
                        "machine learning platforms, responsible AI delivery, "
                        "enterprise stakeholder alignment, model optimization, "
                        "and scalable production automation workflows."
                    ),
                    "reason": "too wordy",
                }
            ],
            "strategy_notes": "x",
        }

    async def fake_complete_json(*_args, **_kwargs):
        return {"value": "Python AI engineer.", "reason": "compact"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)
    monkeypatch.setattr(opt, "complete_json", fake_complete_json)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python",
            job_keywords={"required_skills": ["Python"], "preferred_skills": [], "keywords": []},
        )
    )

    assert result.validated_changes == {"summary_0": "Python AI engineer."}
    assert result.compacted_changes
    assert not result.rejected_changes


def test_pipeline_patches_confirmed_skill(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Frameworks \& Libraries}{: Python}
\end{document}
"""
    pr = parse(tex)

    async def fake_keywords(_jd):
        return {
            "required_skills": ["Python", "LangChain"],
            "preferred_skills": [],
            "keywords": [],
        }

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "LangChain", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "AUTO_PATCH_UNCONFIRMED_SKILLS", False)
    monkeypatch.delenv("SMARTJOBAPPLY_CONFIRMED_SKILLS", raising=False)
    monkeypatch.setattr(opt, "extract_job_keywords", fake_keywords)
    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python and LangChain",
            confirmed_skills=["LangChain"],
        )
    )

    assert result.confirmation_required_skills == []
    assert result.confirmed_skills == ["LangChain"]
    assert "LangChain" in result.modified_latex
    assert any(change["stmt_id"] == "skills_0" for change in result.diff)


def test_score_aware_skill_patch_stops_after_ats_target(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Skills}
\textbf{Frameworks \& Libraries}{: Python} \\
\textbf{Cloud \& APIs}{: AWS SageMaker} \\
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {
            "target_skills": [
                {"skill": "LangChain", "reason": "required"},
                {"skill": "Bedrock", "reason": "preferred"},
                {"skill": "Vertex AI", "reason": "preferred"},
            ],
            "strategy_notes": "x",
        }

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python and LangChain. Preferred: Bedrock, Vertex AI.",
            confirmed_skills=["LangChain", "Bedrock", "Vertex AI"],
            job_keywords={
                "required_skills": ["Python", "LangChain"],
                "preferred_skills": ["Bedrock", "Vertex AI"],
                "keywords": [],
            },
        )
    )

    assert result.ats_target_met
    assert "LangChain" in result.modified_latex
    assert "Bedrock" in result.modified_latex
    assert "Vertex AI" not in result.modified_latex


def test_pipeline_automatically_weaves_supported_keyword_gaps(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer focused on translating business data through scalable pipelines.}
\section{Experience}
\begin{itemize}
\item Built AI chatbot prototypes for enterprise clients.
\item Architected a modular \textbf{XAI framework} supporting automated model auditing.
\end{itemize}
\section{Projects}
\begin{itemize}
\item Built a RAG system with LLM-generated answers and citations.
\end{itemize}
\section{Skills}
\textbf{Frameworks \& Libraries}{: Python, FastAPI, Postman}
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {"target_skills": [{"skill": "Python", "reason": "required"}], "strategy_notes": "x"}

    async def fake_diffs(**_kwargs):
        return {"changes": [], "strategy_notes": "no LLM edits"}

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need API development, communication skills, cloud-native, summarization, conversational workflows, bias assessment, transparency.",
            job_keywords={
                "required_skills": ["Python", "API development"],
                "preferred_skills": ["Communication skills"],
                "keywords": [
                    "cloud-native",
                    "summarization",
                    "conversational workflows",
                    "bias assessment",
                    "transparency",
                ],
            },
        )
    )

    assert result.ats_after.score > result.ats_before.score
    assert "communicating AI/ML concepts" in result.modified_latex
    assert "cloud-native scalable pipelines" in result.modified_latex
    assert "LLM-generated summarization and answers" in result.modified_latex
    assert "conversational AI workflow and AI chatbot prototypes" in result.modified_latex
    assert "transparency and bias assessment" in result.modified_latex


def test_confirmed_ai_platform_skills_patch_to_matching_skill_lines() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Skills}
\textbf{Frameworks \& Libraries}{: PyTorch, FastAPI, MLflow} \\
\textbf{Cloud \& APIs}{: AWS SageMaker, Pinecone} \\
\end{document}
"""
    parsed = parse(tex, resume_id="test")
    editable = {
        "editable": {
            "skills": {
                "skills_0": r"\textbf{Frameworks \& Libraries}{: PyTorch, FastAPI, MLflow}",
                "skills_1": r"\textbf{Cloud \& APIs}{: AWS SageMaker, Pinecone}",
            }
        }
    }

    patch = _deterministic_skills_patch(
        ["Bedrock", "Vertex AI", "ML pipelines", "Inference services"],
        parsed,
        editable,
    )

    assert "Bedrock" in patch["skills_1"]
    assert "Vertex AI" in patch["skills_1"]
    assert "ML pipelines" in patch["skills_0"]
    assert "Inference services" in patch["skills_0"]


def test_recruiter_review_loop_applies_supported_score_improving_edit(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python and FastAPI experience.}
\section{Skills}
\textbf{Languages}{: Python}
\end{document}
"""
    pr = parse(tex)

    import latex_resume.optimizer as opt

    result = opt.OptimizationResult()
    result.modified_latex = tex
    result.job_keywords = {
        "required_skills": ["Python"],
        "preferred_skills": ["API development"],
        "keywords": [],
    }
    result.skill_target_plan = {
        "target_skills": [{"skill": "Python", "reason": "required"}],
        "strategy_notes": "x",
    }
    result.ats_after = ATSResult(
        score=60.0,
        required_score=100.0,
        preferred_score=0.0,
        keyword_score=0.0,
        required_found=["Python"],
        required_missing=[],
        preferred_found=[],
        preferred_missing=["API development"],
        keyword_hits=[],
        keyword_misses=[],
    )

    async def fake_complete_json(*_args, **_kwargs):
        return {
            "feedback": "Recruiter wants clearer API development evidence.",
            "changes": [
                {
                    "stmt_id": "summary_0",
                    "value": "AI engineer with Python and FastAPI API development experience.",
                    "reason": "Maps FastAPI evidence to API development wording.",
                }
            ],
        }

    def fake_evaluate(_parse_result, changes, _job_keywords, _confirmed_skills=None):
        latex = changes["summary_0"]
        return {
            "latex": latex,
            "render": RenderResult(ok=True, page_count=1, overflow=False, pdf_bytes=b"%PDF"),
            "ats": ATSResult(
                score=85.0,
                required_score=100.0,
                preferred_score=100.0,
                keyword_score=0.0,
                required_found=["Python"],
                required_missing=[],
                preferred_found=["API development"],
                preferred_missing=[],
                keyword_hits=[],
                keyword_misses=[],
            ),
        }

    monkeypatch.setattr(opt, "complete_json", fake_complete_json)
    monkeypatch.setattr(opt, "_evaluate_change_set", fake_evaluate)

    iterations = asyncio.run(
        opt._run_recruiter_review_loop(
            result,
            pr,
            "Need Python and API development",
            {},
            max_iterations=2,
        )
    )

    assert iterations == 1
    assert result.ats_after.score == 85.0
    assert result.recruiter_iteration_count == 1
    assert result.recruiter_feedback == [
        "Recruiter wants clearer API development evidence."
    ]
    assert result.validated_changes["summary_0"] == (
        "AI engineer with Python and FastAPI API development experience."
    )


def test_pipeline_rejects_changes_outside_selected_scope(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Experience}
\begin{itemize}
\item Built data tools with Python.
\end{itemize}
\section{Skills}
Python
\end{document}
"""
    pr = parse(tex)

    async def fake_plan(**_kwargs):
        return {
            "target_skills": [{"skill": "Python", "reason": "required"}],
            "strategy_notes": "x",
        }

    async def fake_diffs(**_kwargs):
        return {
            "changes": [
                {
                    "stmt_id": "summary_0",
                    "value": "AI engineer with Python automation experience.",
                    "reason": "Improve summary.",
                },
                {
                    "stmt_id": "work_0_0",
                    "value": "Built scoped-out data automation tools with Python.",
                    "reason": "Should be rejected by selected scope.",
                },
            ],
            "strategy_notes": "x",
        }

    async def fake_review(*_args, **_kwargs):
        return 0

    import latex_resume.optimizer as opt

    monkeypatch.setattr(opt, "generate_skill_target_plan", fake_plan)
    monkeypatch.setattr(opt, "generate_latex_diffs", fake_diffs)
    monkeypatch.setattr(opt, "_run_recruiter_review_loop", fake_review)
    monkeypatch.setattr(
        opt,
        "check_one_page",
        lambda _latex: RenderResult(ok=True, page_count=1, overflow=False, pdf_bytes=b"%PDF"),
    )

    result = asyncio.run(
        run_optimization_pipeline(
            pr,
            "Need Python automation.",
            job_keywords={
                "required_skills": ["Python"],
                "preferred_skills": [],
                "keywords": ["automation"],
            },
            allowed_stmt_ids=["summary_0"],
        )
    )

    assert result.validated_changes == {
        "summary_0": "AI engineer with Python automation experience."
    }
    assert "scoped-out" not in result.modified_latex
    assert any("outside the selected edit scope" in warning for warning in result.warnings)


def test_manual_statement_edits_respect_selected_scope(monkeypatch):
    tex = r"""
\documentclass{article}
\begin{document}
\section{Summary}
\small{AI engineer with Python experience.}
\section{Experience}
\begin{itemize}
\item Built data tools with Python.
\end{itemize}
\end{document}
"""

    import latex_resume.optimizer as opt

    monkeypatch.setattr(
        opt,
        "check_one_page",
        lambda _latex: RenderResult(ok=True, page_count=1, overflow=False, pdf_bytes=b"%PDF"),
    )

    result = apply_manual_statement_edits(
        latex_source=tex,
        changes={
            "summary_0": "AI engineer with Python and automation experience.",
            "work_0_0": "Built edited work tools with Python.",
        },
        job_keywords={
            "required_skills": ["Python"],
            "preferred_skills": [],
            "keywords": ["automation"],
        },
        allowed_stmt_ids=["summary_0"],
    )

    assert result.validated_changes == {
        "summary_0": "AI engineer with Python and automation experience."
    }
    assert "Built edited work tools" not in result.modified_latex
    assert any(
        "outside the selected edit scope" in item["rejection_reason"]
        for item in result.rejected_changes
    )
