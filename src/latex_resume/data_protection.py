"""Field-level encryption at rest for the most sensitive stored values.

Profiles hold voluntary EEO answers, compensation expectations and (from
Phase 5b) per-profile LLM API keys; submission receipts can hold EEO answers
that were autofilled with consent. Those sub-documents are encrypted with a
Fernet key from ``APPLYTEX_DATA_KEY`` before they reach SQLite, so a copied
database file does not expose them.

Local single-user mode may run without a key: values are stored in plain
JSON, exactly as before. Once a key is set, new writes are encrypted and old
plaintext rows still read (decryption is only attempted on the ``__enc__``
marker). Reading an encrypted row without the key fails loudly rather than
silently returning garbage.

Generate a key:  ``uv run python -m latex_resume.data_protection``
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

DATA_KEY_ENV = "APPLYTEX_DATA_KEY"
ENC_MARKER = "__enc__"

# Paths inside a CandidateProfile JSON payload that are encrypted as a unit.
PROFILE_PROTECTED_PATHS: tuple[tuple[str, ...], ...] = (
    ("equal_opportunity",),
    ("application_facts", "compensation_preferences"),
    ("llm_settings", "api_key"),
)


class DataKeyMissing(RuntimeError):
    """A stored value is encrypted but no APPLYTEX_DATA_KEY is configured."""


class DataProtection:
    """Encrypt/decrypt JSON-serializable values; a no-op when no key is set."""

    def __init__(self, key: str | None) -> None:
        cleaned = (key or "").strip()
        self._fernet = Fernet(cleaned.encode()) if cleaned else None

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: Any) -> Any:
        if self._fernet is None or value is None:
            return value
        token = self._fernet.encrypt(json.dumps(value, ensure_ascii=True).encode("utf-8"))
        return {ENC_MARKER: token.decode("ascii")}

    def decrypt(self, value: Any) -> Any:
        if not (isinstance(value, dict) and ENC_MARKER in value and len(value) == 1):
            return value
        if self._fernet is None:
            raise DataKeyMissing(
                f"A stored value is encrypted; set {DATA_KEY_ENV} to the key it was written with."
            )
        try:
            raw = self._fernet.decrypt(str(value[ENC_MARKER]).encode("ascii"))
        except InvalidToken as exc:
            raise DataKeyMissing(f"{DATA_KEY_ENV} does not match the key this value was written with.") from exc
        return json.loads(raw.decode("utf-8"))


def protection_from_env() -> DataProtection:
    return DataProtection(os.environ.get(DATA_KEY_ENV))


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _walk(data: dict[str, Any], path: tuple[str, ...]) -> tuple[dict[str, Any] | None, str]:
    node: Any = data
    for part in path[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None, path[-1]
        node = node[part]
    return (node if isinstance(node, dict) else None), path[-1]


def protect_profile_payload(data: dict[str, Any], protection: DataProtection) -> dict[str, Any]:
    if not protection.enabled:
        return data
    for path in PROFILE_PROTECTED_PATHS:
        parent, leaf = _walk(data, path)
        if parent is not None and leaf in parent and parent[leaf] not in (None, "", [], {}):
            parent[leaf] = protection.encrypt(parent[leaf])
    return data


def unprotect_profile_payload(data: dict[str, Any], protection: DataProtection) -> dict[str, Any]:
    for path in PROFILE_PROTECTED_PATHS:
        parent, leaf = _walk(data, path)
        if parent is not None and leaf in parent:
            parent[leaf] = protection.decrypt(parent[leaf])
    return data


def protect_submission_fields(fields: list[dict[str, Any]], protection: DataProtection) -> list[dict[str, Any]]:
    """EEO answers autofilled with consent are the only receipt values worth sealing."""
    if not protection.enabled:
        return fields
    for field in fields:
        if field.get("source") == "eeo_opt_in" and field.get("value") is not None:
            field["value"] = protection.encrypt(field["value"])
    return fields


def unprotect_submission_fields(fields: list[dict[str, Any]], protection: DataProtection) -> list[dict[str, Any]]:
    for field in fields:
        if "value" in field:
            field["value"] = protection.decrypt(field["value"])
    return fields


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI convenience
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] not in {"generate-key", "generate"}:
        print("usage: python -m latex_resume.data_protection [generate-key]", file=sys.stderr)
        return 2
    print(generate_key())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
