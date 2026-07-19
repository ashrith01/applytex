"""On-disk storage helpers for resume/application PDF bytes."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path


def artifacts_root(db_path: Path) -> Path:
    """Return `.applytex/artifacts` beside the SQLite database."""
    return db_path.parent / "artifacts"


def write_pdf_bytes(
    *,
    root: Path,
    profile_id: str,
    name: str,
    data: bytes,
) -> tuple[str, int, str]:
    """Write PDF bytes and return (relative_path from DB parent, size, sha256)."""
    safe_profile = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in profile_id) or "default"
    safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "resume.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"
    directory = root / safe_profile
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_name
    path.write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    relative = str(Path("artifacts") / safe_profile / safe_name)
    return relative, len(data), checksum


def resolve_artifact_path(db_path: Path, relative_or_absolute: str) -> Path:
    """Resolve a stored artifact path against the DB directory."""
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return (db_path.parent / path).resolve()


def load_pdf_b64(
    *,
    db_path: Path,
    pdf_path: str | None,
    pdf_b64: str | None,
) -> str:
    """Load PDF as base64, preferring on-disk path with b64 fallback."""
    if pdf_path:
        resolved = resolve_artifact_path(db_path, pdf_path)
        if resolved.is_file():
            return base64.b64encode(resolved.read_bytes()).decode()
    if pdf_b64:
        return pdf_b64
    return ""


def persist_b64_pdf(
    *,
    db_path: Path,
    profile_id: str,
    name: str,
    data_b64: str,
) -> tuple[str, int, str]:
    """Decode base64 PDF, write to disk, return path metadata."""
    raw = base64.b64decode(data_b64)
    return write_pdf_bytes(
        root=artifacts_root(db_path),
        profile_id=profile_id,
        name=name,
        data=raw,
    )
