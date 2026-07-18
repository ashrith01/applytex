"""Static safety checks for the read-only Chrome extension."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXTENSION = ROOT / "extension"
LEGACY = EXTENSION / "legacy"
PANEL_MODULE_FILES = (
    "panel-shared.js",
    "panel-scan.js",
    "panel-fill.js",
    "panel-workday.js",
    "panel.js",
)
INJECT_FILES = ["providers.js", *PANEL_MODULE_FILES]


def panel_source() -> str:
    """Concatenate injected panel modules for static contract checks."""
    return "\n".join(
        (EXTENSION / name).read_text(encoding="utf-8") for name in PANEL_MODULE_FILES
    )


def extension_scripts_source() -> str:
    """Panel modules plus legacy popup for shared behavior checks."""
    return "\n".join(
        [
            panel_source(),
            (LEGACY / "popup.js").read_text(encoding="utf-8"),
        ]
    )

SUPPORTED_HOST_PERMISSIONS = {
    "https://www.linkedin.com/*",
    "https://*.greenhouse.io/*",
    "https://*.lever.co/*",
    "https://*.ashbyhq.com/*",
    "https://*.myworkdayjobs.com/*",
    "https://*.myworkdaysite.com/*",
    "https://*.icims.com/*",
    "https://*.smartrecruiters.com/*",
    "https://apply.workable.com/*",
    "https://*.workable.com/*",
    "https://www.indeed.com/*",
    "https://www.ziprecruiter.com/*",
    "https://www.glassdoor.com/*",
    "https://wellfound.com/*",
    "https://www.dice.com/*",
}
SUPPORTED_PROVIDERS = {
    "linkedin",
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "icims",
    "smartrecruiters",
    "workable",
    "indeed",
    "ziprecruiter",
    "glassdoor",
    "wellfound",
    "dice",
}


def test_manifest_is_valid_and_scoped_to_supported_targets() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage", "webNavigation"}
    assert "tabs" not in manifest["permissions"]
    assert "<all_urls>" not in manifest["host_permissions"]
    assert SUPPORTED_HOST_PERMISSIONS.issubset(set(manifest["host_permissions"]))
    assert "default_popup" not in manifest["action"]
    assert manifest["background"]["service_worker"] == "background.js"
    assert "void restoreOpenPanels();" in background
    assert "Promise.all(tabIds.map((tabId) => injectPanel(tabId)))" in background


def test_extension_fills_only_after_review_and_never_submits() -> None:
    script = panel_source()
    forbidden = (
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


def test_extension_loads_shared_provider_registry() -> None:
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    popup = (LEGACY / "popup.html").read_text(encoding="utf-8")
    providers = (EXTENSION / "providers.js").read_text(encoding="utf-8")

    assert 'importScripts("providers.js")' in background
    for name in INJECT_FILES:
        assert f'"{name}"' in background
    assert '<script src="../providers.js"></script>' in popup
    assert popup.index("../providers.js") < popup.index("popup.js")
    for provider in SUPPORTED_PROVIDERS:
        assert f"{provider}:" in providers


def test_extension_persists_one_flow_across_supported_navigation() -> None:
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    panel = panel_source()

    assert "chrome.webNavigation.onCompleted" in background
    assert "chrome.webNavigation.onHistoryStateUpdated" in background
    assert "chrome.storage.session" in background
    assert "chrome.tabs.sendMessage" not in background
    assert 'FLOW_STORAGE_KEY' in panel
    assert '"applicationFlows"' in panel
    assert "syncPageContext" in panel
    assert "workflowKeyForUrl" in panel
    assert "flowStorageKeysForContext" in panel
    assert "canonical URL" in panel
    assert "same ATS host" in panel
    assert "LOW_CONFIDENCE_CONTEXT_MESSAGE" in panel
    assert "Open the original job page once, then return here." in panel
    assert 'parsed.searchParams.get("jk")' in panel
    assert 'pathname.match(/\\/jobs\\/(\\d+)' in panel
    assert "previousJobId !== savedJob.job_id" in panel
    assert "Job context saved. Sign in to continue" in panel
    assert "Job context restored and application fields are ready" in panel
    assert "flowContextForCurrentPage" in panel
    assert "hostnameForUrl(location.href)" in panel
    assert "isApplicationLikePage" in panel
    assert "applicationByJob" in panel
    assert "applicationStorageKey" in panel
    assert "capture_confidence" in panel
    assert "description_source" in panel
    assert "submit your application|application form|attach resume|resume\\/cv|cover letter" in panel


def test_extension_has_separate_autofill_and_tailor_tabs() -> None:
    panel = panel_source()
    assert 'panelTab: "autofill"' in panel
    assert 'data-tab="autofill"' in panel
    assert 'data-tab="tailor"' in panel
    assert "renderAutofillTab" in panel
    assert "renderTailorTab" in panel
    assert "current_resume_score" in panel
    assert "tailored_resume_score" in panel
    assert "/score" in panel
    assert "Refresh score" in panel


def test_extension_surfaces_job_summary_score_and_question_checklist() -> None:
    panel = panel_source()
    assert "sja-job-summary" in panel
    assert "sja-score-ring" in panel
    assert "currentResumeScore" in panel
    assert "Required ${progress.filled}/${progress.total}" in panel
    assert "renderReviewChecklist" in panel
    assert "renderReviewGroup(\"Required\"" in panel
    assert "renderReviewGroup(\"Optional\"" in panel
    assert "sja-question-row" in panel
    assert "sja-question-mark" in panel
    assert "sja-inline-action" in panel
    assert "Add answer to Profile" in panel
    assert "createMissingAnswerTask" in panel
    assert "/tasks" in panel
    assert "sja-diagnostics" in panel
    assert "Capture</b>" in panel


def test_extension_recaptures_combined_provider_pages_and_can_restart() -> None:
    panel = panel_source()

    assert "hasCapturableJobContextOnApplicationPage" in panel
    assert "semanticIdentity" in panel
    assert "company.length >= 3" in panel
    assert "cleanCapturedJobTitle" in panel
    assert "jobTitleFromDocumentTitle" in panel
    assert 'data-action="restart-page"' in panel
    assert 'aria-label="Restart page analysis"' in panel
    assert "restartPageAnalysis" in panel
    assert "forceCapture: true" in panel
    assert "return canonicalIdentityUrl(parsed)" in panel


def test_extension_recognizes_greenhouse_selected_values_and_clears_stale_failures() -> None:
    panel = panel_source()

    assert "selectedSingleValue" in panel
    assert ".select__single-value, [class*='singleValue']" in panel
    assert "button.iti__selected-country[title]" in panel
    assert 'item.answer_source === "already_on_page"' in panel
    assert "resolvedIds.has(item.field_id)" in panel
    assert "field_id: action.field_id" in panel


def test_extension_uses_compact_panel_typography() -> None:
    panel = panel_source()

    assert "width: min(390px, 100vw);" in panel
    assert "min-width: 0;" in panel
    assert "font-size: 11px;" in panel
    assert ".sja-brand { font-size: 14px;" in panel
    assert "font-size: 11.5px;" in panel


def test_extension_uses_proofing_ledger_hierarchy() -> None:
    panel = panel_source()

    assert "sja-account-bar" in panel
    assert "sja-account-menu" in panel
    assert 'aria-label="Open profile actions"' in panel
    assert "sja-profile-pill" not in panel
    assert "sja-bridge" not in panel
    assert "sja-diagnostic-bridge" in panel
    assert '<details class="sja-diagnostics">' in panel
    assert "max-height: min(48vh, 430px);" in panel
    assert "border-bottom: 1px solid #e5eae7;" in panel
    assert "width: 3px;" in panel
    assert "letter-spacing: 0;" in panel


def test_extension_distinguishes_filled_fields_from_ready_actions() -> None:
    panel = panel_source()

    assert 'item.status === "ready") return { className: "ready", symbol: "→" }' in panel
    assert 'item.answer_source === "already_on_page"' in panel
    assert 'item.planned_value_preview ? `Ready · ${item.planned_value_preview}` : "Ready to fill"' in panel
    assert 'background: #2457a6;' in panel


def test_extension_matches_expanded_binary_options_and_quiets_generic_metadata() -> None:
    panel = panel_source()

    assert "binaryMeaning" in panel
    assert 'normalized.startsWith("no ")' in panel
    assert "optionBinary === wantedBinary" in panel
    assert '/^(application form|job description)$/i.test(pageContext)' in panel
    assert 'title="${escapeAttr(scoreUpdatedLabel)}"' in panel
    assert '<span><b>Score</b>' in panel


def test_extension_rescans_spa_application_steps_after_async_rendering() -> None:
    panel = panel_source()

    assert "new MutationObserver" in panel
    assert "startPageMonitoring" in panel
    assert "refreshObservedApplicationStep" in panel
    assert "formFingerprint" in panel
    assert "setInterval(() => scheduleObservedRescan" in panel
    assert "Application page detected. Waiting for the current step fields to load." in panel
    assert "Review before autofilling." in panel
    assert "stopPageMonitoring" in panel


def test_extension_proxies_local_api_through_service_worker() -> None:
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    panel = panel_source()

    assert 'message?.type === "APPLYTEX_API_REQUEST"' in background
    assert "proxyApiRequest" in background
    assert 'type: "APPLYTEX_API_REQUEST"' in panel
    assert "await fetch(`${API_BASE}${path}`" not in panel
    assert 'headers["X-Profile-Id"]' in background
    assert 'headers["X-Profile-Id"]' in panel
    assert "profileId" in background


def test_extension_panel_supports_username_sign_in_switch_and_logout() -> None:
    panel = panel_source()

    assert 'PROFILE_STORAGE_KEY = "applytexExtensionProfileId"' in panel
    assert "chrome.storage.local" in panel
    assert "signInWithProfileId" in panel
    assert "signOutExtension" in panel
    assert "switchAccount" in panel
    assert 'data-action="sign-in"' in panel
    assert 'data-action="switch-account"' in panel
    assert 'data-action="sign-out"' in panel
    assert "needsSignIn" in panel
    assert "/profile/active" in panel
    assert 'apiRequest("/profiles")' in panel
    assert "usableProfiles" in panel
    assert panel.count("function normalizeProfileId") == 1
    assert 'cleaned || "default"' not in panel
    assert "Drop files here" in panel
    assert "sanitizeFieldLabel" in panel


def test_extension_workday_geo_select_uses_fuzzy_match_and_aliases() -> None:
    panel = panel_source()

    assert "countriesEquivalent" in panel
    assert "countrySelectCandidates" in panel
    assert "stateSelectCandidates" in panel
    assert "geoSelectCandidates" in panel
    assert "isGeoField" in panel
    assert "controlAlreadyMatches" in panel
    assert "const exactOnly = state.provider === \"workday\" && !geoField" in panel
    assert "United States of America" in panel
    assert "Texas (TX)" in panel or "`${name} (${code})`" in panel


def test_extension_uses_registry_for_provider_detection() -> None:
    panel = panel_source()
    popup = (LEGACY / "popup.js").read_text(encoding="utf-8")
    providers = (EXTENSION / "providers.js").read_text(encoding="utf-8")

    assert "ApplyTexProviders?.providerForUrl" in panel
    assert "ApplyTexProviders?.configFor" in panel
    assert "ApplyTexProviders?.providerForUrl" in popup
    assert "files: [\"providers.js\"]" in popup
    assert "myworkdayjobs.com" in providers
    assert "smartrecruiters.com" in providers
    assert "wellfound.com" in providers


def test_workday_company_capture_uses_branding_not_navigation_header() -> None:
    panel = panel_source()
    providers = (EXTENSION / "providers.js").read_text(encoding="utf-8")
    workday_config = providers.split("workday:", 1)[1].split("icims:", 1)[0]

    assert '"header"' not in workday_config
    assert "workdayCompanyFromPage" in panel
    assert "/assets\\/" in panel
    assert "humanizeBoardToken(assetToken)" in panel


def test_extension_checkbox_checkmark_is_centered() -> None:
    script = panel_source()
    assert "#smartjobapply-panel .sja-check input[type=\"checkbox\"]" in script
    assert "place-items: center" in script
    assert 'content: "✓"' in script


def test_extension_review_badge_checkmark_is_centered() -> None:
    script = panel_source()
    assert "#smartjobapply-panel .sja-field .sja-dot" in script
    assert "font-size: 0" in script
    assert "#smartjobapply-panel .sja-field .sja-dot.ready::before" in script
    assert "transform: translateY(-1px)" in script


def test_extension_explains_current_step_and_review_state() -> None:
    script = panel_source()

    assert "applicationStepLabel" in script
    assert "filled" in script
    assert "ready" in script
    assert "blocked" in script
    assert "Final submission stays manual." in script


def test_extension_has_continue_to_next_page_button() -> None:
    panel = panel_source()

    assert 'data-action="continue-next-page"' in panel
    assert "continueToNextPage" in panel
    assert "findContinuePageButton" in panel
    assert "Continue to the next page" in panel
    assert "isFinalSubmitButton" in panel
    assert "sja-continue-button" in panel
    assert "bottom-navigation-next-button" in panel


def test_extension_typography_is_isolated_from_ashby_line_height() -> None:
    script = panel_source()
    assert "#smartjobapply-panel * {" in script
    assert "line-height: 1.35" in script
    assert "#smartjobapply-panel .sja-field > div" in script
    assert "min-height: 34px" in script


def test_extension_strips_a_matching_separate_phone_country_code() -> None:
    script = panel_source()
    assert "valueForField(element, action.value)" in script
    assert "selectedDialingCodeNear(element)" in script
    assert 'autocomplete !== "tel-country-code"' in script
    assert "dialingCodeFromControl(candidate)" in script


def test_extension_lets_portal_masks_format_phone_numbers() -> None:
    panel = panel_source()
    popup = (LEGACY / "popup.js").read_text(encoding="utf-8")

    for script in (panel, popup):
        assert "fillPhoneField" in script
        assert 'inputType: "insertText"' in script
        assert 'replace(/\\D/g, "")' in script
        assert "`${element.value}${digit}`" in script


def test_extension_recognizes_ashby_boards() -> None:
    script = panel_source()
    assert 'hostname.endsWith("ashbyhq.com")' in script
    assert 'return "ashby"' in script


def test_extension_scans_same_origin_application_iframes() -> None:
    panel = panel_source()

    assert "function pageRoots()" in panel
    assert "frame.contentDocument" in panel
    assert 'queryAllFromPage("input, select, textarea' in panel
    assert 'queryAllFromPage("input[type=\'file\']")' in panel
    assert "pageRoots().flatMap" in panel
    assert 'state.provider === "icims"' in panel
    assert 'params.get("mode") === "apply"' in panel


def test_extension_scans_open_application_shadow_roots_but_not_other_extensions() -> None:
    panel = panel_source()

    assert "element.shadowRoot" in panel
    assert 'tag === "plasmo-csui"' in panel
    assert 'tag.startsWith("grammarly-")' in panel
    assert "element.getRootNode().getElementById" in panel
    assert "function isTag(element, tagName)" in panel
    assert "element.ownerDocument.defaultView" in panel


def test_extension_supports_explicit_repeated_profile_record_selection() -> None:
    panel = panel_source()

    assert "renderProfileRecordSelectors" in panel
    assert 'data-record-kind="${escapeAttr(group.kind)}"' in panel
    assert "profile_record_kind" in panel
    assert "profile_record_index" in panel
    assert "Workday: open Add or Edit" in panel


def test_extension_orchestrates_workday_my_experience_without_submitting() -> None:
    panel = panel_source()

    assert "runWorkdayMyExperienceAutofill" in panel
    assert "ensureWorkdayProfileRecords" in panel
    assert '["Add", "Add Another"]' in panel
    assert "isWorkdayPortalChromeControl" in panel
    assert "header, [role='banner'], nav, [role='navigation']" in panel
    assert "assignWorkdayRecordIndexes" in panel
    assert "workdayIdentityMatches" in panel
    assert "normalizedEducationSchool" in panel
    assert "amrita vishwa vidyapeetham" in panel
    assert "workdayIdentityIsBlank" in panel
    assert "smartjobapplyPendingRecord" in panel
    assert "isUnmatchedWorkdayRecordField" in panel
    assert "Approve and fill My Experience" in panel
    assert "const workdayExperience = isWorkdayMyExperiencePage()" in panel
    assert "isWorkdayMyExperiencePage() && state.profile" in panel
    assert "? Boolean(state.profile)" in panel
    assert "state.contextWarning && !workdayExperience" in panel
    assert "ApplyTeX did not click Save and Continue" in panel
    assert "selectMultiValue" in panel
    assert "failed_values" in panel
    assert "field_outcomes" in panel
    assert "record_aborted" in panel
    assert "applyRuntimeFailureStatuses" in panel
    assert "failure_status" in panel
    assert "selectedCountForControl" in panel
    assert "selectedMultiValues(control)[0]" in panel
    assert "const retryRequests = []" in panel
    assert "const promptContainer = element.closest" in panel
    assert "[...ignoredPhrases].sort" in panel
    assert 'Failed: ${weakText(item.status).replaceAll("_", " ")}' in panel
    assert "const committedSearchDeadline = committedSearchStartedAt + 5000" in panel
    assert "(!sawRealOptions || sawNoItems)" in panel
    assert "const workdayCatalogSearchTerms" in panel
    assert "foreignPromptId" in panel
    assert "const dismissTarget = element.closest" in panel
    assert "workdayDateMetadata" in panel
    assert "date_boundary" in panel
    assert "date_component" in panel
    assert "isTransientPromptControl" in panel
    assert "selectWorkdayPromptCandidate" in panel
    assert "activeWorkdayListFor" in panel
    assert "commitWorkdayFreeformValue" in panel
    assert "selectWorkdaySkillValue" in panel
    assert "workdaySkillOptionMatches" in panel
    assert "workdaySkillRequestsForValue" in panel
    assert "Microsoft Azure" in panel
    assert "Structured Query Language(SQL)" in panel
    assert "Java (programming language)" in panel
    assert "Date.now() + 5000" in panel
    assert "PointerEventConstructor" in panel
    assert "element.click()" in panel
    assert "setTimeout(resolve, 700)" in panel
    assert "observedNumber === expectedNumber" in panel
    assert 'key: "Enter"' in panel
    assert 'question?.date_component === "year"' in panel
    assert 'question?.date_component === "month"' in panel
    assert "!question.date_component" in panel
    assert "reviewItemDisplayLabel" in panel
    assert "Work Experience ${recordNumber}" in panel
    assert "Education ${recordNumber}" in panel
    assert "setWorkdaySpinbuttonValue" in panel
    assert "[data-automation-id='activeListContainer'][role='listbox']" in panel
    assert "const list = await openWorkdayPrompt(element, query)" in panel
    assert 'const list = await openWorkdayPrompt(element);' in panel
    assert "if (element && isUnmatchedWorkdayRecordField(element))" not in panel
    assert 'action.action === "select_many"' in panel
    assert "strictWorkdayCatalogQuestion" in panel
    assert "workdayCatalogOptionMatches" in panel
    assert "return exactOptionMatch(optionText, wantedText)" in panel


def test_extension_shows_cancellable_animated_autofill_progress() -> None:
    panel = panel_source()

    assert "beginAutofillRun" in panel
    assert "updateAutofillRun" in panel
    assert "cancelAutofillRun" in panel
    assert 'data-action="cancel-autofill"' in panel
    assert "sja-wave-dots" in panel
    assert "@keyframes sja-dot-wave" in panel
    assert "prefers-reduced-motion: reduce" in panel
    assert "if (run?.cancelled) break" in panel
    assert "Stopping after the current field" in panel
    assert "Save and Continue" not in panel_source().split("function cancelAutofillRun", 1)[1].split("function finishAutofillRun", 1)[0]


def test_extension_scans_and_fills_workday_application_question_groups() -> None:
    panel = panel_source()

    assert "scanWorkdayQuestionGroups" in panel
    assert "isWorkdayApplicationQuestionsPage" in panel
    assert 'queryAllFromPage("[role=\'group\']")' in panel
    assert ".slice(0, 2000)" in panel
    assert "groupedControls.add(control)" in panel
    assert "formStructureSignature" in panel
    assert "step_key: applicationStepLabel()" in panel
    assert "state.scan && previousSignature !== scan.form_signature" in panel
    assert "selectWorkdayStandardOption" in panel
    assert "visibleStandardWorkdayLists" in panel
    assert 'lastStatus = "menu_not_opened"' in panel
    assert 'lastStatus = "option_unavailable"' in panel
    assert 'lastStatus = "selection_not_committed"' in panel
    assert "findField(fieldId)" in panel
    assert "item.draft_eligible === true" in panel


def test_platform_qa_harness_uses_the_service_worker_message_contract() -> None:
    harness = (ROOT / "scripts" / "extension_platform_qa.mjs").read_text(encoding="utf-8")

    assert 'message?.type !== "APPLYTEX_API_REQUEST"' in harness
    assert "async sendMessage(message)" in harness
    assert 'arg === "--headed" || arg === "--verbose"' in harness
    assert "!next.startsWith" in harness
    assert 'url.pathname === "/profile/view"' in harness
    assert 'provider === "workday"' in harness
    assert 'selectOption("1"' in harness
    assert 'data-automation-id="applyFlowMyExpPage"' in harness
    assert "addWorkdayWorkRecord" in harness
    assert "addWorkdayEducationRecord" in harness
    assert "commitWorkdaySkill" in harness
    assert "corruptDateOnKeyboard" in harness
    assert "requireYearBeforeMonth" in harness
    assert "_workdaySkillSearchTimer" in harness
    assert 'data-automation-id="selectedItemList"' in harness
    assert "clearIcon" in harness
    assert "Added 0 records" in harness
    assert "save_clicks" in harness
    assert "Accenture" in harness
    assert "Samsung PRISM" in harness
    assert "University of Houston" in harness
    assert "Amrita School of Engineering" in harness
    assert 'data-automation-id="dateSectionMonth-input"' in harness
    assert 'data-automation-id="dateSectionYear-input"' in harness
    assert "activeListContainer" in harness
    assert "verifyWorkdayApplicationQuestions" in harness
    assert "primaryQuestionnaire--" in harness
    assert "workday-question-listbox" in harness
    assert "second_menu_opens" in harness
    assert "long_label_length" in harness
    assert "Computer Engineering" in harness
    assert "Computer and Information Science" in harness
    assert "Bauer College of Business, University of Houston" in harness
    assert "No Items." in harness
    assert "Reinforcement Learning" in harness
    assert "Git/GitHub" in harness
    assert "Microsoft Azure" in harness
    assert "Python (programming language)" in harness
    assert "Structured Query Language(SQL)" in harness
    assert "Java (programming language)" in harness
    assert "JavaScript" in harness
    assert "first_fill_panel_text" in harness
    assert 'panel_text = pageState.panel_text' in harness
    assert 'firstFillPanelText.includes("Claude")' in harness
    assert 'firstFillPanelText.includes("Gemini")' in harness
    assert "Accenture" in harness
    assert "Samsung" in harness
    assert "University of Houston" in harness
    assert "Amrita School of Engineering" in harness
    assert "optional_values" in harness


def test_extension_scanner_handles_ats_placeholder_controls() -> None:
    script = panel_source()
    assert "isDecorativeSelectInput" in script
    assert "isCustomSelectInput" in script
    assert "isCustomSelectButton" in script
    assert "controlHasCurrentValue" in script
    assert "nearbyQuestionLabel" in script
    assert "checkbox" in script
    assert "element.checked" in script
    assert "type your response" in script.lower()
    assert "select\\.\\.\\." in script.lower()
    assert 'label !== "Unlabelled field"' in script
    assert 'element.getAttribute("aria-haspopup")' in script
    assert 'element.getAttribute("data-automation-id")' in script


def test_extension_captures_semantic_job_context_on_combined_application_pages() -> None:
    script = panel_source()

    assert "hasCapturableJobContextOnApplicationPage" in script
    assert "isAuthenticationPage()" in script
    assert "semanticIdentity" in script
    assert "description|job-details|posting" in script
    assert "title.length >= 5" in script
    assert "company.length >= 3" in script


def test_extension_groups_and_fills_native_ashby_checkbox_questions() -> None:
    script = panel_source()

    assert "ashbyFieldEntries" in script
    assert '".ashby-application-form-container fieldset"' in script
    assert "scanAshbyCheckboxGroups" in script
    assert "nativeCheckboxOptionLabel" in script
    assert 'control_kind: "multi_select"' in script
    assert "groupedCheckboxes.has(element)" in script
    assert "selectNativeCheckboxGroup" in script
    assert "checkbox.checked !== shouldCheck" in script


def test_extension_automatically_generates_and_fills_reviewable_answers() -> None:
    script = panel_source()

    assert "/answers/draft" in script
    assert "scheduleAutomaticAnswerGeneration" in script
    assert "runAutomaticAnswerQueue" in script
    assert "generateAndFillAutomaticAnswer" in script
    assert "fillReviewedFields([action])" in script
    assert 'state.scan = savedScan' in script
    assert "Edit draft" in script
    assert "Generate draft" not in script
    assert "Use answer" not in script
    assert "Regenerate" not in script
    assert "Discard" not in script
    assert 'if (currentAnswerValue(element)) {' in script
    assert "answerWordCount(answer) > 100" in script
    assert 'answer_source: "generated"' in script
    assert "requestSubmit" not in script


def test_extension_fill_supports_reviewed_radio_and_custom_selects() -> None:
    script = panel_source()
    assert "async function fillReviewedFields" in script
    assert "selectRadioOption" in script
    assert "radio.click()" in script
    assert "exactOptionMatch(label, wanted)" in script
    assert "exactOptionMatch(candidate.value, wanted)" not in script
    assert "selectCustomYesNoOption" in script
    assert "customYesNoButtons" in script
    assert "selectCustomOption" in script
    assert "waitForOption" in script
    assert 'action.action === "upload"' in script


def test_extension_matches_state_names_and_postal_codes() -> None:
    script = panel_source()

    assert "US_STATE_CODE_BY_NAME" in script
    assert "canonicalUsState" in script
    assert "optionState === wantedState" in script
    assert 'texas: "TX"' in script


def test_extension_uses_ashby_question_titles_and_isolates_review_layout() -> None:
    script = panel_source()

    assert 'querySelector(".ashby-application-form-question-title")' in script
    assert "flex-direction: column !important" in script
    assert "flex: 0 0 auto !important" in script
    assert "#smartjobapply-panel .sja-field > div strong" in script
    assert "height: auto !important" in script


def test_extension_returns_scanned_fields_in_document_order() -> None:
    panel = panel_source()
    popup = (LEGACY / "popup.js").read_text(encoding="utf-8")

    assert "sortFieldsInDocumentOrder(fields)" in panel
    assert "compareDocumentPosition" in panel
    assert "compareDocumentPosition" in popup


def test_extension_heading_omits_latex_gate_copy() -> None:
    panel = panel_source()
    popup = (LEGACY / "popup.html").read_text(encoding="utf-8")

    assert "LaTeX gate ready" not in panel
    assert "LaTeX resume gate" not in popup


def test_extension_ignores_ashby_resume_parser_and_prefers_required_resume() -> None:
    script = panel_source()
    assert "isAuxiliaryResumeInput" in script
    assert "autofill key application fields" in script.lower()
    assert "if (element.required) score += 20" in script
    assert "score -= 50" in script


def test_extension_recognizes_ashby_required_label_classes() -> None:
    script = panel_source()
    assert ".ashby-application-form-field-entry" in script
    assert ".ashby-application-form-container fieldset" in script
    assert "[data-field-path]" in script
    assert r"(?:^|[^a-z0-9])required(?:[^a-z0-9]|$)" in script
    assert "selectedMultiValues(element).length > 0 || Boolean(weakText(element.value))" in script


def test_extension_has_no_debug_telemetry_ingest() -> None:
    panel = panel_source()
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    for source in (panel, background):
        assert ":7402" not in source
        assert "APPLYTEX_DEBUG" not in source
        assert "127.0.0.1:7402" not in source
        assert "#region agent log" not in source


def test_extension_continue_button_refuses_final_submit() -> None:
    panel = panel_source()
    assert "isFinalSubmitButton" in panel
    assert "continueToNextPage" in panel
    assert "requestSubmit" not in panel
    assert "findContinuePageButton" in panel
    # Continue path must consult final-submit guard before clicking.
    continue_fn = panel.split("async function continueToNextPage", 1)[1].split(
        "\n  async function ",
        1,
    )[0]
    assert "isFinalSubmitButton" in continue_fn


def test_extension_optional_bearer_auth_contracts() -> None:
    panel = panel_source()
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert 'TOKEN_STORAGE_KEY = "applytexExtensionAccessToken"' in panel
    assert "/auth/status" in panel
    assert "/auth/login" in panel
    assert "data-signin-password" in panel
    assert "headers.Authorization" in panel or 'headers["Authorization"]' in panel
    assert "Authorization" in background
    assert "usableProfiles" in panel
    assert "Bearer " in background
