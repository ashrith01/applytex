#!/usr/bin/env python3
"""Smoke-test FastAPI routes that do not require an LLM call."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

from latex_resume.api import create_app

ROOT = Path(__file__).parent.parent
RESUME = ROOT / "samples" / "sample_resume.tex"
OUT = ROOT / "samples" / "out" / "api_smoke_report.json"


def main() -> int:
    client = TestClient(create_app())
    report: dict = {}

    with RESUME.open("rb") as f:
        upload = client.post(
            "/latex/upload",
            files={"file": ("resume.tex", f, "application/x-tex")},
        )
    report["upload_status"] = upload.status_code
    upload.raise_for_status()
    upload_json = upload.json()
    session_id = upload_json["session_id"]
    report["session_id"] = session_id
    report["editable_sections"] = list(upload_json["editable"].keys())
    report["page_budget"] = upload_json["page_budget"]

    status = client.get(f"/latex/{session_id}/status")
    report["status_status"] = status.status_code
    status.raise_for_status()
    report["initial_status"] = status.json()
    assert "confirmation_required_skills" in report["initial_status"]
    assert report["initial_status"]["confirmation_required_skills"] == []

    rerender = client.post(
        f"/latex/{session_id}/rerender",
        json={
            "changes": {
                "summary_0": (
                    "AI/ML Engineer with hands-on experience building LLM, RAG, "
                    "and production-oriented machine learning systems."
                )
            }
        },
    )
    report["rerender_status"] = rerender.status_code
    rerender.raise_for_status()
    rerender_json = rerender.json()
    report["rerender"] = {
        "applied": rerender_json["applied"],
        "rejected": rerender_json["rejected"],
        "overflow": rerender_json["overflow"],
        "page_count": rerender_json["page_count"],
        "has_pdf": rerender_json["modified_pdf_b64"] is not None,
        "latex_bytes": len(rerender_json["modified_latex"]),
    }

    deleted = client.delete(f"/latex/{session_id}")
    report["delete_status"] = deleted.status_code
    deleted.raise_for_status()

    after_delete = client.get(f"/latex/{session_id}/status")
    report["status_after_delete"] = after_delete.status_code

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
