const API_BASE = "http://127.0.0.1:8000";

const backendStatus = document.querySelector("#backend-status");
const providerLabel = document.querySelector("#provider-label");
const pageTitle = document.querySelector("#page-title");
const fieldCount = document.querySelector("#field-count");
const message = document.querySelector("#message");
const captureButton = document.querySelector("#capture-job");
const scanButton = document.querySelector("#scan-form");
const fillButton = document.querySelector("#fill-form");
const profileSetup = document.querySelector("#profile-setup");
const fillReview = document.querySelector("#fill-review");
const jobCard = document.querySelector("#job-card");
const showPanelButton = document.querySelector("#show-panel");
const profileIdInput = document.querySelector("#profile-id");
const profileResumeFile = document.querySelector("#profile-resume-file");
const uploadProfileResumeButton = document.querySelector("#upload-profile-resume");
const resumeStatus = document.querySelector("#resume-status");
const customizeResume = document.querySelector("#customize-resume");
const resumeModeLabel = document.querySelector("#resume-mode-label");
const prepareResumeButton = document.querySelector("#prepare-resume");
const progressLabel = document.querySelector("#progress-label");
const progressBar = document.querySelector("#progress-bar");

let activeTab = null;
let provider = "unknown";
let currentPlan = null;
let currentApplicationId = null;
let currentJob = null;
let currentProfileId = "default";
let latestPanelState = null;

document.addEventListener("DOMContentLoaded", async () => {
  [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  provider = providerForUrl(activeTab?.url || "");
  providerLabel.textContent = provider === "unknown" ? "Unsupported board" : provider;
  pageTitle.textContent = activeTab?.title || "No active page";
  captureButton.disabled = provider === "unknown";
  scanButton.disabled = provider === "unknown";
  const saved = await chrome.storage.local.get(["applicationByUrl", "profileId"]);
  currentProfileId = saved.profileId || "default";
  profileIdInput.value = currentProfileId === "default" ? "" : currentProfileId;
  currentApplicationId = saved.applicationByUrl?.[canonicalPageKey(activeTab?.url || "")] || null;
  await checkBackend();
  await loadProfileResume();
  await loadProfileSetup();
  updateResumeModeLabel();
});

profileIdInput.addEventListener("change", async () => {
  currentProfileId = normalizeProfileId(profileIdInput.value);
  profileIdInput.value = currentProfileId === "default" ? "" : currentProfileId;
  await chrome.storage.local.set({ profileId: currentProfileId });
  await loadProfileResume();
  await loadProfileSetup();
});

customizeResume.addEventListener("change", updateResumeModeLabel);

captureButton.addEventListener("click", async () => {
  await runAction(captureButton, "Capturing", async () => {
    const job = await executeInPage(extractJobFromPage, [provider]);
    currentJob = job;
    const saved = await apiRequest("/extension/jobs/capture", {
      method: "POST",
      body: JSON.stringify(job),
    });
    const application = await apiRequest("/applications", {
      method: "POST",
      body: JSON.stringify({ job_id: saved.job_id }),
    });
    currentApplicationId = application.application_id;
    const storage = await chrome.storage.local.get(["applicationByUrl"]);
    const applicationByUrl = storage.applicationByUrl || {};
    applicationByUrl[canonicalPageKey(activeTab.url)] = currentApplicationId;
    await chrome.storage.local.set({ applicationByUrl });
    renderJobCard(saved);
    await loadProfileResume();
    await updatePagePanel();
    showMessage(`Captured ${saved.title} at ${saved.company}.`, "success");
  });
});

showPanelButton.addEventListener("click", async () => {
  await updatePagePanel();
});

uploadProfileResumeButton.addEventListener("click", async () => {
  const [file] = profileResumeFile.files || [];
  if (!file) {
    showMessage("Choose a .tex or .pdf resume first.", "error");
    return;
  }
  await runAction(uploadProfileResumeButton, "Saving", async () => {
    const body = new FormData();
    body.append("file", file);
    await apiRequest(`/profile/resume?profile_id=${encodeURIComponent(currentProfileId)}`, {
      method: "POST",
      body,
    });
    await loadProfileResume();
    await loadProfileSetup();
    showMessage("Profile resume saved.", "success");
  });
});

prepareResumeButton.addEventListener("click", async () => {
  if (!currentJob) {
    showMessage("Capture the job first so the JD is available.", "error");
    return;
  }
  const shouldCustomize = customizeResume.checked
    ? window.confirm("Customize your saved LaTeX resume for this job? Choose Cancel to upload the saved profile resume as-is.")
    : false;
  await runAction(prepareResumeButton, "Preparing", async () => {
    const prepared = await apiRequest(`/extension/resume/prepare?profile_id=${encodeURIComponent(currentProfileId)}`, {
      method: "POST",
      body: JSON.stringify({
        job_description: currentJob.description || "",
        customize: shouldCustomize,
        application_id: currentApplicationId,
        prefer_approved_artifact: true,
      }),
    });
    const upload = await executeInPage(uploadResumeFileToApplication, [prepared]);
    await updatePagePanel({
      message: prepared.customized
        ? `Uploaded customized resume: ${prepared.filename}`
        : `Uploaded saved resume: ${prepared.filename}`,
    });
    const warning = prepared.warnings?.length ? ` ${prepared.warnings[0]}` : "";
    showMessage(
      `Resume uploaded to ${upload.label || "the application"}.${warning}`,
      prepared.warnings?.length ? "error" : "success",
    );
  });
});

scanButton.addEventListener("click", async () => {
  await runAction(scanButton, "Scanning", async () => {
    const scan = await executeInPage(scanApplicationForm, [provider]);
    scan.application_id = currentApplicationId;
    fieldCount.textContent = `${scan.questions.length} fields`;
    const saved = await apiRequest("/extension/forms/scan", {
      method: "POST",
      body: JSON.stringify(scan),
    });
    currentPlan = await apiRequest(`/extension/forms/${saved.scan_id}/plan?profile_id=${encodeURIComponent(currentProfileId)}`);
    renderFillReview(currentPlan.review_items || []);
    renderProgress(currentPlan.review_items || []);
    const unresolved = currentPlan.unresolved_required.length;
    const fillable = currentPlan.actions.filter((action) => action.action !== "skip").length;
    fillButton.disabled = fillable === 0;
    await updatePagePanel();
    showMessage(
      unresolved
        ? `${fillable} fields ready; ${unresolved} required answers need review.`
        : `${fillable} reviewed fields are ready to fill.`,
      unresolved ? "error" : "success",
    );
  });
});

fillButton.addEventListener("click", async () => {
  if (!currentPlan) {
    showMessage("Scan the form before filling.", "error");
    return;
  }
  if (canonicalPageKey(activeTab.url) !== canonicalPageKey(currentPlan.page_url)) {
    showMessage("The page changed after scanning. Scan it again.", "error");
    return;
  }
  await runAction(fillButton, "Filling", async () => {
    const result = await executeInPage(fillReviewedFields, [currentPlan.actions]);
    const scan = await executeInPage(scanApplicationForm, [provider]);
    const required = scan.questions.filter((question) => question.required);
    const filled = required.filter((question) => question.current_value_present).length;
    await updatePagePanel({
      message: `Filled ${result.filled} reviewed fields. Review before submitting.`,
      progressOverride: { filled, total: required.length },
    });
    showMessage(
      `Filled ${result.filled} fields. ${result.skipped} fields remain unchanged. Review before submitting.`,
      "success",
    );
  });
});

async function checkBackend() {
  try {
    await apiRequest("/health");
    backendStatus.textContent = "Local API ready";
  } catch {
    backendStatus.textContent = "Local API unavailable";
    captureButton.disabled = true;
    scanButton.disabled = true;
    fillButton.disabled = true;
    showMessage("Start ApplyTeX ATS on port 8000.", "error");
  }
}

async function loadProfileSetup() {
  try {
    const setup = await apiRequest(`/profile/setup-questions?profile_id=${encodeURIComponent(currentProfileId)}`);
    if (!setup.ready_for_basic_autofill) {
      const missing = setup.missing_required.slice(0, 5);
      renderProfileSetup(missing);
      showMessage("Set up your profile before filling. Missing common answers are shown below.", "error");
    } else {
      profileSetup.hidden = true;
    }
  } catch {
    profileSetup.hidden = true;
  }
}

async function runAction(button, busyLabel, action) {
  const original = button.textContent;
  const wasDisabled = button.disabled;
  button.disabled = true;
  button.textContent = `${busyLabel}...`;
  showMessage("");
  try {
    await action();
  } catch (error) {
    showMessage(error.message || String(error), "error");
  } finally {
    button.disabled = wasDisabled;
    button.textContent = original;
  }
}

async function executeInPage(func, args) {
  if (!activeTab?.id) {
    throw new Error("No active browser tab.");
  }
  await chrome.scripting.executeScript({
    target: { tabId: activeTab.id },
    files: ["providers.js"],
  });
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: activeTab.id },
    func,
    args,
  });
  if (!result || result.result?.error) {
    throw new Error(result?.result?.error || "Could not inspect this page.");
  }
  return result.result;
}

async function apiRequest(path, options = {}) {
  const isFormData = options.body instanceof FormData;
  const response = await fetch(`${API_BASE}${path}`, {
    headers: isFormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Local API returned ${response.status}.`);
  }
  return response.json();
}

function providerForUrl(url) {
  const detected = globalThis.ApplyTexProviders?.providerForUrl?.(url);
  if (detected) return detected;
  const hostname = new URL(url || "https://invalid.local").hostname;
  if (hostname === "www.linkedin.com" || hostname === "linkedin.com") {
    return "linkedin";
  }
  if (hostname.endsWith("greenhouse.io")) {
    return "greenhouse";
  }
  if (hostname.endsWith("lever.co")) {
    return "lever";
  }
  if (hostname.endsWith("ashbyhq.com")) {
    return "ashby";
  }
  return "unknown";
}

function canonicalPageKey(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}

function workflowKeyForPopup(url, detectedProvider) {
  try {
    const parsed = new URL(url);
    const externalId = parsed.searchParams.get("gh_jid") ||
      parsed.searchParams.get("jid") ||
      parsed.searchParams.get("jk") ||
      parsed.pathname.split("/").filter(Boolean).pop() ||
      "";
    return externalId
      ? `${detectedProvider}:${parsed.hostname}:${externalId.toLowerCase()}`
      : `${detectedProvider}:${canonicalPageKey(url)}`;
  } catch {
    return `${detectedProvider}:${url}`;
  }
}

function showMessage(text, type = "") {
  message.textContent = text;
  message.className = type;
}

function normalizeProfileId(value) {
  const cleaned = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  return cleaned || "default";
}

async function loadProfileResume() {
  try {
    const info = await apiRequest(`/profile/resume?profile_id=${encodeURIComponent(currentProfileId)}`);
    resumeStatus.textContent = info.has_pdf
      ? info.resume_pdf_filename || "Resume ready"
      : info.has_latex_source
        ? "LaTeX saved"
        : "No resume";
    prepareResumeButton.disabled = (!info.has_pdf && !info.has_latex_source) || !currentJob;
  } catch {
    resumeStatus.textContent = "No resume";
    prepareResumeButton.disabled = true;
  }
}

function updateResumeModeLabel() {
  resumeModeLabel.textContent = customizeResume.checked ? "Ask first" : "Use saved";
}

function renderJobCard(job) {
  jobCard.replaceChildren();
  jobCard.hidden = false;
  const title = document.createElement("div");
  title.className = "job-title";
  title.textContent = job.title || "Captured job";
  const meta = document.createElement("div");
  meta.className = "job-meta";
  meta.textContent = [job.company, job.location].filter(Boolean).join(" | ");
  jobCard.append(title, meta);
  showPanelButton.disabled = false;
}

function renderProgress(items) {
  const required = items.filter((item) => item.required);
  const ready = required.filter((item) => item.status === "ready");
  const total = required.length;
  const percent = total ? Math.round((ready.length / total) * 100) : 0;
  progressLabel.textContent = `${ready.length}/${total} required fields filled`;
  progressBar.style.width = `${percent}%`;
}

async function updatePagePanel(overrides = {}) {
  const state = {
    title: currentJob?.title || activeTab?.title || "Current job",
    company: currentJob?.company || "",
    provider,
    items: currentPlan?.review_items || [],
    message: overrides.message || "",
    progressOverride: overrides.progressOverride || null,
  };
  latestPanelState = state;
  await executeInPage(renderApplyTexAtsPanel, [state]);
}

function renderProfileSetup(missingLabels) {
  profileSetup.replaceChildren();
  profileSetup.hidden = missingLabels.length === 0;
  for (const label of missingLabels) {
    profileSetup.appendChild(reviewRow("skipped", label, "Missing from user profile"));
  }
}

function renderFillReview(items) {
  fillReview.replaceChildren();
  fillReview.hidden = items.length === 0;
  renderProgress(items);
  for (const item of items) {
    const detail = item.status === "ready"
      ? `${item.answer_source}${item.value_preview ? `: ${item.value_preview}` : ""}`
      : item.answer_source === "eeo_opt_in"
        ? "Enable EEO autofill in Profile → Job Application Questions"
        : item.required
          ? "Required but not in profile"
          : "Skipped";
    fillReview.appendChild(reviewRow(item.status, item.label, detail));
  }
}

function reviewRow(status, label, value) {
  const row = document.createElement("div");
  row.className = "review-item";

  const icon = document.createElement("span");
  icon.className = `review-icon ${status}`;
  icon.textContent = status === "ready" ? "✓" : "✕";

  const body = document.createElement("div");
  const title = document.createElement("div");
  title.className = "review-title";
  title.textContent = label;
  const subtitle = document.createElement("div");
  subtitle.className = "review-value";
  subtitle.textContent = value;

  body.append(title, subtitle);
  row.append(icon, body);
  return row;
}

async function extractJobFromPage(detectedProvider) {
  const text = (element) => element?.textContent?.trim() || "";
  const firstText = (selectors) => {
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const value =
        element?.getAttribute?.("content") ||
        element?.getAttribute?.("alt") ||
        element?.getAttribute?.("aria-label") ||
        text(element);
      if (value) return value;
    }
    return "";
  };
  const cleanDescription = (value, providerName = "") => {
    let cleaned = String(value || "").replace(/\n{3,}/g, "\n\n").trim();
    const stopMarkers = globalThis.ApplyTexProviders?.configFor?.(providerName)?.stopMarkers || [];
    for (const marker of stopMarkers) {
      const index = cleaned.toLowerCase().indexOf(marker.toLowerCase());
      if (index > 0) cleaned = cleaned.slice(0, index).trim();
    }
    return cleaned;
  };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const tabWithText = (label) => {
    const wanted = label.toLowerCase();
    return Array.from(document.querySelectorAll("button, [role='tab']"))
      .find((element) => text(element).toLowerCase() === wanted) || null;
  };
  const activate = (element) => {
    if (!element) return;
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  };
  const providerConfig = globalThis.ApplyTexProviders?.configFor?.(detectedProvider) || {};
  const applicationTab = providerConfig.afterCaptureTab ? tabWithText(providerConfig.afterCaptureTab) : null;
  const startedOnApplication =
    applicationTab?.getAttribute("aria-selected") === "true" ||
    applicationTab?.className?.toString().toLowerCase().includes("active");
  if (providerConfig.beforeCaptureTab) {
    const beforeTab = tabWithText(providerConfig.beforeCaptureTab);
    if (beforeTab) {
      activate(beforeTab);
      await wait(650);
    }
  }
  const config = providerConfig.selectors || {};
  let company = firstText(config.company || []);
  if (!company) {
    company = companyFromPage(detectedProvider);
  }
  const title = firstText(config.title || ["h1"]);
  const description = cleanDescription(firstText(config.description || ["main", "body"]), detectedProvider);
  const locationText = firstText(config.location || []);
  if (startedOnApplication && applicationTab) {
    activate(applicationTab);
    await wait(250);
  }
  if (!title || !description) {
    return { error: "The job title or description could not be identified on this page." };
  }
  const pathId = location.pathname.split("/").filter(Boolean).pop() || "";
  return {
    provider: detectedProvider,
    external_id: pathId,
    company: company || document.title.split("|").pop()?.trim() || "Unknown company",
    title,
    description,
    location: locationText,
    source_url: location.href,
    apply_url: location.href,
    workflow_key: workflowKeyForPopup(location.href, detectedProvider),
    canonical_url: canonicalPageKey(location.href),
    description_source: config.description?.length ? "provider selector" : "page text",
    capture_confidence: description.length > 500 ? 0.85 : 0.62,
    warnings: description.length > 500 ? [] : ["Job description was shorter than expected."],
  };
}

function companyFromPage(detectedProvider) {
  const title = document.title || "";
  const fromApplicationTitle = title.match(/\bat\s+(.+?)(?:\s*$|\s+-\s+|\s+\|)/i)?.[1]?.trim();
  if (fromApplicationTitle) return fromApplicationTitle;
  const fromMeta =
    document.querySelector("meta[property='og:site_name']")?.content?.trim() ||
    document.querySelector("meta[name='application-name']")?.content?.trim();
  const providerLabel = globalThis.ApplyTexProviders?.configFor?.(detectedProvider)?.label || detectedProvider;
  if (fromMeta && !fromMeta.toLowerCase().includes(String(providerLabel || "").toLowerCase())) return fromMeta;
  if (detectedProvider === "workday") {
    const brandedCompany = workdayCompanyFromPage();
    if (brandedCompany) return brandedCompany;
  }
  const parsed = new URL(location.href);
  const boardToken = parsed.searchParams.get("for") || parsed.pathname.split("/").filter(Boolean)[0] || parsed.hostname.split(".")[0] || "";
  return humanizeBoardToken(boardToken);
}

function workdayCompanyFromPage() {
  const logoSource = document.querySelector("[data-automation-id='logo'][src]")?.getAttribute("src") || "";
  try {
    const logoPath = new URL(logoSource, location.href).pathname;
    const assetToken = logoPath.match(/^\/([^/]+)\/assets\//i)?.[1] || "";
    if (assetToken && !/^(assets?|images?|logos?|workday)$/i.test(assetToken)) {
      return humanizeBoardToken(assetToken);
    }
  } catch {
    // Continue to the board-name fallback.
  }
  const segments = location.pathname.split("/").filter(Boolean);
  const localeIndex = segments.findIndex((segment) => /^[a-z]{2}-[a-z]{2}$/i.test(segment));
  const boardToken = localeIndex >= 0 ? segments[localeIndex + 1] || "" : "";
  return /^(careers?|external|jobs?)$/i.test(boardToken) ? "" : humanizeBoardToken(boardToken);
}

function humanizeBoardToken(value) {
  return String(value || "")
    .replace(/[-_]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .trim();
}

function scanApplicationForm(detectedProvider) {
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
  };
  const labelFor = (element) => {
    if (element.labels?.length) {
      return Array.from(element.labels).map((label) => label.textContent.trim()).join(" ");
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      return labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
    }
    return (
      element.getAttribute("aria-label") ||
      element.getAttribute("placeholder") ||
      element.name ||
      element.id ||
      "Unlabelled field"
    ).trim();
  };
  const radioGroupLabel = (element, optionLabels) => {
    const fieldset = element.closest("fieldset");
    const legend = fieldset?.querySelector("legend")?.textContent?.trim();
    if (legend) return legend;
    const questionTitle = fieldset?.querySelector(".ashby-application-form-question-title")?.textContent?.trim();
    if (questionTitle) return questionTitle;
    const group = element.closest("[role='radiogroup'], [aria-labelledby]");
    const labelledBy = group?.getAttribute("aria-labelledby");
    if (labelledBy) {
      const value = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      if (value) return value;
    }
    const container = element.closest("div, section, li");
    const containerText = container?.textContent?.replace(/\s+/g, " ").trim() || "";
    const withoutOptions = optionLabels.reduce(
      (value, option) => value.replace(option, ""),
      containerText,
    ).trim();
    return withoutOptions || optionLabels[0] || element.name || element.id || "Unlabelled field";
  };
  const fields = [];
  const radioNames = new Set();
  Array.from(document.querySelectorAll("input[type='radio']")).forEach((element, index) => {
    if (!visible(element)) return;
    const name = element.name || element.id || `radio-${index}`;
    if (radioNames.has(name)) return;
    radioNames.add(name);
    const group = Array.from(document.querySelectorAll(`input[type='radio'][name='${cssEscape(name)}']`))
      .filter((candidate) => visible(candidate));
    const groupLabels = group.map((candidate) => labelFor(candidate)).filter(Boolean);
    const label = radioGroupLabel(element, groupLabels);
    fields.push({
      field_id: name,
      label,
      input_type: "radio",
      required: group.some((candidate) => candidate.required || candidate.getAttribute("aria-required") === "true"),
      options: group.map((candidate, groupIndex) => {
        const optionLabel = groupLabels[groupIndex] || candidate.value || "";
        return optionLabel.trim();
      }).filter(Boolean),
      sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(`${label} ${groupLabels.join(" ")}`),
      autocomplete: null,
      current_value_present: group.some((candidate) => candidate.checked),
    });
  });

  Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
    .filter((element) => {
      const fileInput = element instanceof HTMLInputElement && element.type === "file";
      return (visible(element) || fileInput) && element.type !== "hidden" && element.type !== "radio";
    })
    .forEach((element, index) => {
      const label = labelFor(element);
      const inputType =
        element.tagName === "SELECT"
          ? "select"
          : element.tagName === "TEXTAREA"
            ? "textarea"
            : element.getAttribute("contenteditable") === "true"
              ? "contenteditable"
              : element.type || "text";
      const options =
        element.tagName === "SELECT"
          ? Array.from(element.options).map((option) => option.textContent.trim()).filter(Boolean)
          : [];
      fields.push({
        field_id: element.id || element.name || `field-${index}`,
        label,
        input_type: inputType,
        required: element.required || element.getAttribute("aria-required") === "true",
        options,
        sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
        autocomplete: element.getAttribute("autocomplete"),
        current_value_present: element instanceof HTMLInputElement && element.type === "file"
          ? Boolean(element.files?.length || element.value)
          : Boolean(element.value),
      });
    });
  fields.sort((left, right) => {
    const leftElement = document.getElementById(left.field_id) ||
      Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
        .find((element) => element.name === left.field_id);
    const rightElement = document.getElementById(right.field_id) ||
      Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
        .find((element) => element.name === right.field_id);
    if (!leftElement || !rightElement || leftElement === rightElement) return 0;
    const position = leftElement.compareDocumentPosition(rightElement);
    if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  return {
    provider: detectedProvider,
    page_url: location.href,
    page_title: document.title,
    questions: fields,
  };
}

function cssEscape(value) {
  if (window.CSS?.escape) {
    return window.CSS.escape(value);
  }
  return value.replace(/['\\]/g, "\\$&");
}

async function fillReviewedFields(actions) {
  const findField = (fieldId) => {
    const byId = document.getElementById(fieldId);
    if (byId) return byId;
    return Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
      .find((element) => element.name === fieldId) || null;
  };
  const findRadioOption = (fieldId, value) => {
    const wanted = String(value).trim().toLowerCase();
    return Array.from(document.querySelectorAll(`input[type='radio'][name='${cssEscape(fieldId)}']`))
      .find((candidate) => {
        const label = candidate.labels?.length
          ? Array.from(candidate.labels).map((item) => item.textContent.trim()).join(" ")
          : candidate.value || "";
        const normalized = label.trim().toLowerCase();
        return normalized === wanted;
      }) || null;
  };
  const setNativeValue = (element, value) => {
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : element instanceof HTMLSelectElement
          ? HTMLSelectElement.prototype
          : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor?.set) {
      descriptor.set.call(element, value);
    } else {
      element.value = value;
    }
  };
  const dispatch = (element) => {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const isPhoneField = (element) => {
    if (!(element instanceof HTMLInputElement)) return false;
    const autocomplete = String(element.getAttribute("autocomplete") || "").toLowerCase();
    const label = [
      ...(element.labels ? Array.from(element.labels).map((item) => item.textContent || "") : []),
      element.getAttribute("aria-label") || "",
    ].join(" ").toLowerCase();
    return element.type === "tel" ||
      autocomplete.split(/\s+/).some((token) => token.startsWith("tel")) ||
      /\b(phone|mobile|telephone)\b/.test(label);
  };
  const fillPhoneField = async (element, value) => {
    const digits = String(value).replace(/\D/g, "");
    if (!digits) return false;
    element.focus();
    setNativeValue(element, "");
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward" }));
    for (const digit of digits) {
      setNativeValue(element, `${element.value}${digit}`);
      element.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        data: digit,
        inputType: "insertText",
      }));
      await new Promise((resolve) => setTimeout(resolve, 8));
    }
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  };
  let filled = 0;
  let skipped = 0;

  for (const action of actions) {
    if (action.action === "skip" || action.value === null) {
      skipped += 1;
      continue;
    }
    const element = findField(action.field_id);
    if (!element) {
      skipped += 1;
      continue;
    }
    if (action.action === "select" && element instanceof HTMLInputElement && element.type === "radio") {
      const radio = findRadioOption(action.field_id, action.value);
      if (!radio) {
        skipped += 1;
        continue;
      }
      radio.click();
      filled += 1;
      continue;
    }
    if (action.action === "select" && element instanceof HTMLSelectElement) {
      const wanted = String(action.value).trim().toLowerCase();
      const option = Array.from(element.options).find((candidate) => {
        const text = candidate.textContent.trim().toLowerCase();
        return text === wanted || text.includes(wanted) || wanted.includes(text);
      });
      if (!option) {
        skipped += 1;
        continue;
      }
      setNativeValue(element, option.value);
      dispatch(element);
      filled += 1;
      continue;
    }
    if (action.action === "check" && element instanceof HTMLInputElement) {
      element.checked = Boolean(action.value);
      dispatch(element);
      filled += 1;
      continue;
    }
    if (element.getAttribute("contenteditable") === "true") {
      element.textContent = String(action.value);
      dispatch(element);
      filled += 1;
      continue;
    }
    if (isPhoneField(element)) {
      if (!(await fillPhoneField(element, action.value))) {
        skipped += 1;
        continue;
      }
      filled += 1;
      continue;
    }
    setNativeValue(element, String(action.value));
    dispatch(element);
    filled += 1;
  }
  return { filled, skipped };
}

function uploadResumeFileToApplication(preparedResume) {
  const labelFor = (element) => {
    if (element.labels?.length) {
      return Array.from(element.labels).map((label) => label.textContent.trim()).join(" ");
    }
    const container = element.closest("label, div, section, form");
    return (
      element.getAttribute("aria-label") ||
      element.getAttribute("name") ||
      element.getAttribute("id") ||
      container?.textContent ||
      "Resume upload"
    ).replace(/\s+/g, " ").trim();
  };
  const scoreInput = (element) => {
    const label = labelFor(element).toLowerCase();
    let score = 0;
    if (label.includes("resume") || label.includes("cv")) score += 10;
    if (label.includes("cover letter")) score -= 8;
    if (element.accept?.toLowerCase().includes("pdf")) score += 2;
    return score;
  };
  const candidates = Array.from(document.querySelectorAll("input[type='file']"));
  if (!candidates.length) {
    return { error: "No file upload field was found on this application." };
  }
  candidates.sort((left, right) => scoreInput(right) - scoreInput(left));
  const input = candidates[0];
  const binary = atob(preparedResume.data_b64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const file = new File([bytes], preparedResume.filename, { type: preparedResume.mime_type });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return { uploaded: true, label: labelFor(input), filename: preparedResume.filename };
}

function renderApplyTexAtsPanel(state) {
  const existing = document.getElementById("smartjobapply-panel");
  if (existing) {
    existing.remove();
  }
  const styleId = "smartjobapply-panel-style";
  if (!document.getElementById(styleId)) {
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      #smartjobapply-panel {
        position: fixed;
        top: 96px;
        right: 16px;
        z-index: 2147483647;
        width: 310px;
        max-height: calc(100vh - 120px);
        overflow: auto;
        box-sizing: border-box;
        padding: 14px;
        border: 1px solid #e4e8e5;
        border-radius: 8px;
        background: #ffffff;
        color: #101511;
        box-shadow: 0 12px 32px rgba(0,0,0,0.18);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 13px;
      }
      #smartjobapply-panel * { box-sizing: border-box; }
      #smartjobapply-panel .sja-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 12px; }
      #smartjobapply-panel .sja-brand { font-size: 18px; font-weight: 800; letter-spacing: 0; }
      #smartjobapply-panel .sja-chip { padding: 3px 7px; border-radius: 999px; background: #eafff5; color: #0a7d54; font-weight: 700; font-size: 12px; }
      #smartjobapply-panel .sja-card { border: 1px solid #edf0ee; border-radius: 8px; padding: 10px; margin-bottom: 12px; background: #fbfcfb; }
      #smartjobapply-panel .sja-title { font-weight: 800; margin-bottom: 4px; }
      #smartjobapply-panel .sja-muted { color: #67736d; font-size: 12px; line-height: 1.35; }
      #smartjobapply-panel .sja-progress-label { display: flex; justify-content: space-between; margin: 10px 0 6px; font-weight: 700; }
      #smartjobapply-panel .sja-track { height: 7px; border-radius: 999px; overflow: hidden; background: #e8ece9; }
      #smartjobapply-panel .sja-bar { height: 100%; background: #08d98d; }
      #smartjobapply-panel .sja-list { display: grid; gap: 8px; margin-top: 12px; }
      #smartjobapply-panel .sja-row { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 7px; align-items: center; }
      #smartjobapply-panel .sja-icon { width: 15px; height: 15px; border-radius: 50%; display: inline-grid; place-items: center; align-self: center; color: #fff; font-size: 10px; font-weight: 800; }
      #smartjobapply-panel .sja-ready { background: #08c984; }
      #smartjobapply-panel .sja-skip { background: #aab3ad; }
      #smartjobapply-panel .sja-label { font-weight: 650; overflow-wrap: anywhere; }
      #smartjobapply-panel .sja-value { color: #67736d; font-size: 12px; overflow-wrap: anywhere; }
    `;
    document.head.append(style);
  }

  const root = document.createElement("aside");
  root.id = "smartjobapply-panel";
  const head = document.createElement("div");
  head.className = "sja-head";
  const brand = document.createElement("div");
  brand.className = "sja-brand";
  brand.textContent = "ApplyTeX ATS";
  const chip = document.createElement("div");
  chip.className = "sja-chip";
  chip.textContent = state.provider || "job";
  head.append(brand, chip);

  const card = document.createElement("div");
  card.className = "sja-card";
  const title = document.createElement("div");
  title.className = "sja-title";
  title.textContent = state.title || "Current job";
  const company = document.createElement("div");
  company.className = "sja-muted";
  company.textContent = state.company || "Captured from this page";
  card.append(title, company);

  const items = Array.isArray(state.items) ? state.items : [];
  const required = items.filter((item) => item.required);
  const ready = required.filter((item) => item.status === "ready");
  const filled = state.progressOverride?.filled ?? ready.length;
  const total = state.progressOverride?.total ?? required.length;
  const percent = total ? Math.round((filled / total) * 100) : 0;

  const progress = document.createElement("div");
  const progressLabel = document.createElement("div");
  progressLabel.className = "sja-progress-label";
  const left = document.createElement("span");
  left.textContent = `${filled}/${total} required fields filled`;
  const right = document.createElement("span");
  right.textContent = `${percent}%`;
  progressLabel.append(left, right);
  const track = document.createElement("div");
  track.className = "sja-track";
  const bar = document.createElement("div");
  bar.className = "sja-bar";
  bar.style.width = `${percent}%`;
  track.append(bar);
  progress.append(progressLabel, track);

  const list = document.createElement("div");
  list.className = "sja-list";
  const shownItems = required.length ? required : items.slice(0, 8);
  for (const item of shownItems) {
    const row = document.createElement("div");
    row.className = "sja-row";
    const icon = document.createElement("span");
    icon.className = `sja-icon ${item.status === "ready" ? "sja-ready" : "sja-skip"}`;
    icon.textContent = item.status === "ready" ? "✓" : "-";
    const body = document.createElement("div");
    const label = document.createElement("div");
    label.className = "sja-label";
    label.textContent = item.label || "Field";
    const value = document.createElement("div");
    value.className = "sja-value";
    value.textContent = item.status === "ready"
      ? item.value_preview || item.answer_source || "Ready"
      : item.required
        ? "Needs review"
        : "Skipped";
    body.append(label, value);
    row.append(icon, body);
    list.append(row);
  }

  root.append(head, card, progress, list);
  if (state.message) {
    const note = document.createElement("div");
    note.className = "sja-muted";
    note.style.marginTop = "12px";
    note.textContent = state.message;
    root.append(note);
  }
  document.body.append(root);
  return { rendered: true };
}
