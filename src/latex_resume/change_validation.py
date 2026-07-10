"""Pure validation gates for LLM-generated LaTeX statement edits."""

from __future__ import annotations

import re
from typing import Any


_EDITABLE_ID_PREFIXES = frozenset({"summary", "work", "proj", "skills"})


def _is_locked_stmt_id(stmt_id: str) -> bool:
    """Return True if the stmt_id belongs to a locked section."""
    prefix = stmt_id.split("_")[0]
    return prefix not in _EDITABLE_ID_PREFIXES


_BARE_PERCENT_RE = re.compile(r"(?<!\\)%")
_BARE_AMPERSAND_RE = re.compile(r"(?<!\\)&")


def _escape_latex_specials(text: str) -> str:
    """Escape bare ``%`` and ``&`` left by an LLM."""
    text = _BARE_PERCENT_RE.sub(r"\\%", text)
    text = _BARE_AMPERSAND_RE.sub(r"\\&", text)
    return text


_METRIC_RE = re.compile(r"\d[\d.,]*\s*(?:\\?%|\+)")


def _metric_tokens(text: str) -> set[str]:
    """Return normalised quantitative-claim tokens in *text*."""
    cleaned = text.replace("\\", "")
    return {re.sub(r"\s+", "", m.group(0)) for m in _METRIC_RE.finditer(cleaned)}


def _fabricated_metrics(value: str, original: str) -> set[str]:
    """Return metric tokens present in *value* but absent from *original*."""
    return _metric_tokens(value) - _metric_tokens(original)


_UNSUPPORTED_CLAIM_PHRASES: tuple[str, ...] = (
    "m r sales",
    "sales digital platform",
    "sales digital platforms",
    "digital platform",
    "digital platforms",
    "digital application",
    "digital applications",
    "inference service",
    "inference services",
    "integrate ml models",
    "integrated ml models",
    "integrating ml models",
    "ml models into",
    "seamless integration",
)


def _normalize_claim_text(text: str) -> str:
    """Normalize LaTeX/plain text enough to compare introduced claims."""
    cleaned = text.replace(r"\&", " ")
    cleaned = re.sub(r"\\textbf\s*\{([^{}]*)\}", r" \1 ", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}", r" \1 ", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+", " ", cleaned)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", cleaned).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def _unsupported_claim_drifts(value: str, original: str, stmt_id: str) -> set[str]:
    """Return unsupported domain/platform claims added by a rewrite."""
    if stmt_id.startswith("skills_"):
        return set()

    value_norm = _normalize_claim_text(value)
    original_norm = _normalize_claim_text(original)
    return {
        phrase
        for phrase in _UNSUPPORTED_CLAIM_PHRASES
        if phrase in value_norm and phrase not in original_norm
    }


_ITEM_WRAPPER_RE = re.compile(r"^\s*\\(?:resumeItem|item)\s*\{(.*)\}\s*$", re.DOTALL)
_LEADING_ITEM_RE = re.compile(r"^\s*\\item\b\s*")


def _strip_item_wrapper(value: str) -> str:
    """Remove any ``\\item`` / ``\\resumeItem{...}`` wrapper around content."""
    m = _ITEM_WRAPPER_RE.match(value)
    if m:
        value = m.group(1)
    return _LEADING_ITEM_RE.sub("", value)


_TEXTBF_RE = re.compile(r"\\textbf\s*\{([^{}]+)\}")


def _preserve_existing_textbf(value: str, original: str) -> str:
    """Reapply simple original ``\\textbf{...}`` highlights when text remains."""
    if r"\textbf" in value:
        return value

    restored = value
    for match in _TEXTBF_RE.finditer(original):
        highlighted = match.group(1).strip()
        if not highlighted or highlighted not in restored:
            continue
        restored = restored.replace(highlighted, rf"\textbf{{{highlighted}}}", 1)
    return restored


def _normalize_change_stmt_id(stmt_id: str, stmt_index: dict) -> str:
    """Fix common LLM stmt_id shorthand before strict validation."""
    if stmt_id == "summary" and "summary_0" in stmt_index:
        return "summary_0"
    return stmt_id


def _prepare_raw_changes_for_validation(
    raw_changes: list[dict[str, Any]],
    stmt_index: dict,
) -> list[dict[str, Any]]:
    """Normalize harmless LLM drift and collapse duplicate stmt_ids."""
    ordered: list[str] = []
    by_stmt_id: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []

    for change in raw_changes:
        if not isinstance(change, dict):
            passthrough.append(change)
            continue

        stmt_id = change.get("stmt_id", "")
        if not isinstance(stmt_id, str) or not stmt_id:
            passthrough.append(change)
            continue

        normalized_id = _normalize_change_stmt_id(stmt_id, stmt_index)
        normalized_change = {**change, "stmt_id": normalized_id}
        if normalized_id not in by_stmt_id:
            ordered.append(normalized_id)
        by_stmt_id[normalized_id] = normalized_change

    return [by_stmt_id[stmt_id] for stmt_id in ordered] + passthrough


def validate_changes(
    raw_changes: list[dict[str, Any]],
    stmt_index: dict,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply validation gates to LLM-generated changes."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    seen_ids: set[str] = set()

    for change in raw_changes:
        if not isinstance(change, dict):
            rejected.append(
                {"change": change, "rejection_reason": "change is not a JSON object"}
            )
            continue

        stmt_id = change.get("stmt_id", "")
        value = change.get("value", "")
        reason = change.get("reason", "")

        def reject(msg: str) -> None:
            rejected.append({**change, "rejection_reason": msg})

        if not isinstance(stmt_id, str) or not stmt_id:
            reject("'stmt_id' is missing or not a string")
            continue
        if not isinstance(value, str):
            reject(f"'value' for '{stmt_id}' is not a string")
            continue

        if stmt_id in seen_ids:
            reject(f"duplicate stmt_id '{stmt_id}' in change list")
            continue
        seen_ids.add(stmt_id)

        if stmt_id not in stmt_index:
            reject(f"stmt_id '{stmt_id}' not found in stmt_index")
            continue

        if _is_locked_stmt_id(stmt_id):
            reject(f"stmt_id '{stmt_id}' belongs to a locked section")
            continue

        span = stmt_index[stmt_id]
        current_text = span.original_text if hasattr(span, "original_text") else span.text

        safe_value = _escape_latex_specials(
            _preserve_existing_textbf(_strip_item_wrapper(value), current_text)
        )

        if not safe_value or safe_value == current_text:
            reject(f"'value' is empty or identical to original for '{stmt_id}'")
            continue

        fabricated = _fabricated_metrics(safe_value, current_text)
        if fabricated:
            reject(
                f"introduces metric(s) not in the original "
                f"({', '.join(sorted(fabricated))}) — possible fabrication for '{stmt_id}'"
            )
            continue

        unsupported_claims = _unsupported_claim_drifts(
            safe_value,
            current_text,
            stmt_id,
        )
        if unsupported_claims:
            reject(
                f"introduces unsupported claim/domain/platform phrase(s) not in "
                f"the original ({', '.join(sorted(unsupported_claims))}) for '{stmt_id}'"
            )
            continue

        accepted.append(
            {
                "stmt_id": stmt_id,
                "original": current_text,
                "value": safe_value,
                "reason": reason,
            }
        )

    return accepted, rejected
