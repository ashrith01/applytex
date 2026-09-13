"""Conservative matching of known answers to an application's offered labels."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel


class OptionMatch(BaseModel):
    """A rule result, not a calibrated model confidence score."""

    value: str | None = None
    method: Literal["exact", "alias", "boolean_label", "ambiguous", "unavailable"]
    reason: str


def normalize_option(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().rstrip(".*")


_ALIASES: tuple[frozenset[str], ...] = (
    frozenset({"non-binary", "non binary", "nonbinary"}),
    frozenset({"decline to state", "prefer not to say", "prefer not to answer", "i don't wish to answer", "i do not wish to answer", "decline to self-identify"}),
    frozenset({"ms", "m.s", "msc", "master's degree", "masters degree", "master of science"}),
    frozenset({"bs", "b.s", "bsc", "bachelor's degree", "bachelors degree", "bachelor of science"}),
)


def match_available_option(value: str, options: list[str]) -> OptionMatch:
    """Match exact labels or explicit aliases; never select by substring alone.

    Duplicate labels and competing qualified yes/no choices are ambiguous. An
    unknown option is not evidence that a candidate possesses the qualification.
    """
    wanted = normalize_option(value)
    if not wanted:
        return OptionMatch(method="unavailable", reason="No saved answer is available.")
    exact = [option for option in options if normalize_option(option) == wanted]
    if exact:
        return _choose(exact, "exact")
    for aliases in _ALIASES:
        if wanted in aliases:
            matches = [option for option in options if normalize_option(option) in aliases]
            if matches:
                return _choose(matches, "alias")
    if wanted in {"yes", "no"}:
        matches = [option for option in options if re.match(rf"^{wanted}(?:\s|[,;:!]|$)", normalize_option(option))]
        if matches:
            return _choose(matches, "boolean_label")
    return OptionMatch(method="unavailable", reason="No offered option matches the saved answer; review is required.")


def _choose(candidates: list[str], method: Literal["exact", "alias", "boolean_label"]) -> OptionMatch:
    if len(candidates) != 1:
        return OptionMatch(method="ambiguous", reason="Several offered options match; choose the intended option explicitly.")
    return OptionMatch(value=candidates[0], method=method, reason=f"Matched an offered option using {method.replace('_', ' ')} rules.")
