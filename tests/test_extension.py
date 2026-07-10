"""Static safety checks for the read-only Chrome extension."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXTENSION = ROOT / "extension"


def test_manifest_is_valid_and_scoped_to_supported_targets() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage"}
    assert "tabs" not in manifest["permissions"]
    assert "<all_urls>" not in manifest["host_permissions"]
    assert "https://www.linkedin.com/*" in manifest["host_permissions"]
    assert "https://*.greenhouse.io/*" in manifest["host_permissions"]
    assert "https://*.lever.co/*" in manifest["host_permissions"]
    assert "https://*.ashbyhq.com/*" in manifest["host_permissions"]
    assert "default_popup" not in manifest["action"]
    assert manifest["background"]["service_worker"] == "background.js"


def test_extension_fills_only_after_review_and_never_submits() -> None:
    script = (EXTENSION / "panel.js").read_text(encoding="utf-8")
    forbidden = (
        ".click()",
        "requestSubmit",
        ".submit()",
        "chrome.debugger",
    )
    assert all(token not in script for token in forbidden)
    assert "/extension/forms/scan" in script
    assert "/extension/jobs/capture" in script
    assert "/extension/resume/customization-preview" in script
    assert "openWebCustomization" in script
    assert "http://localhost:3000/" in script
    assert "window.open" in script
    assert "fillReviewedFields" in script
    assert "Autofill reviewed fields" in script
    assert "Review everything before submitting" in script


def test_extension_checkbox_checkmark_is_centered() -> None:
    script = (EXTENSION / "panel.js").read_text(encoding="utf-8")
    assert "#smartjobapply-panel .sja-check input[type=\"checkbox\"]" in script
    assert "place-items: center" in script
    assert 'content: "✓"' in script


def test_extension_recognizes_ashby_boards() -> None:
    script = (EXTENSION / "panel.js").read_text(encoding="utf-8")
    assert 'hostname.endsWith("ashbyhq.com")' in script
    assert 'return "ashby"' in script


def test_extension_scanner_handles_ats_placeholder_controls() -> None:
    script = (EXTENSION / "panel.js").read_text(encoding="utf-8")
    assert "isDecorativeSelectInput" in script
    assert "isCustomSelectInput" in script
    assert "nearbyQuestionLabel" in script
    assert "checkbox" in script
    assert "element.checked" in script
    assert "type your response" in script.lower()
    assert "select\\.\\.\\." in script.lower()


def test_extension_fill_supports_reviewed_radio_and_custom_selects() -> None:
    script = (EXTENSION / "panel.js").read_text(encoding="utf-8")
    assert "async function fillReviewedFields" in script
    assert "selectRadioOption" in script
    assert "selectCustomOption" in script
    assert "waitForOption" in script
    assert 'action.action === "upload"' in script
