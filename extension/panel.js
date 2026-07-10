(() => {
  if (window.__smartJobApplyPanelInstalled) {
    window.dispatchEvent(new CustomEvent("smartjobapply:open"));
    return;
  }
  window.__smartJobApplyPanelInstalled = true;

  const API_BASE = "http://127.0.0.1:8000";
  const state = {
    provider: providerForUrl(location.href),
    profileId: "default",
    activeProfile: null,
    backendReady: false,
    job: null,
    applicationId: null,
    scan: null,
    plan: null,
    resumeInfo: null,
    preview: null,
    pendingResume: null,
    busy: "",
    message: "",
    error: "",
    customizing: false,
    minimized: false,
  };

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "SMARTJOBAPPLY_OPEN") {
      openPanel();
    }
  });
  window.addEventListener("smartjobapply:open", openPanel);
  openPanel();

  async function openPanel() {
    ensureStyles();
    ensurePanel();
    render();
    await initialize();
  }

  async function initialize() {
    const saved = await chrome.storage.local.get(["applicationByUrl"]);
    try {
      await apiRequest("/health");
      state.backendReady = true;
    } catch {
      state.backendReady = false;
      state.error = "Start the local ApplyTeX ATS API on port 8000.";
      render();
      return;
    }
    await loadActiveProfile();
    await loadProfileResume();
    await autoCaptureAndScan(saved.applicationByUrl || {});
  }

  async function autoCaptureAndScan(applicationByUrl) {
    await withBusy("Reading job and scanning form", async () => {
      state.job = await extractJobFromPage(state.provider);
      const savedJob = await apiRequest("/extension/jobs/capture", {
        method: "POST",
        body: JSON.stringify(state.job),
      });
      state.job = savedJob;
      const pageKey = canonicalPageKey(location.href);
      state.applicationId = applicationByUrl[pageKey] || null;
      if (!state.applicationId) {
        const application = await apiRequest("/applications", {
          method: "POST",
          body: JSON.stringify({ job_id: savedJob.job_id }),
        });
        state.applicationId = application.application_id;
        applicationByUrl[pageKey] = state.applicationId;
        await chrome.storage.local.set({ applicationByUrl });
      }
      await rescanAndPlan();
      state.message = "Job details and application fields are ready.";
    });
  }

  async function rescanAndPlan() {
    state.scan = scanApplicationForm(state.provider);
    state.scan.application_id = state.applicationId;
    const savedScan = await apiRequest("/extension/forms/scan", {
      method: "POST",
      body: JSON.stringify(state.scan),
    });
    state.plan = await apiRequest(
      `/extension/forms/${savedScan.scan_id}/plan`,
    );
  }

  async function loadActiveProfile() {
    state.activeProfile = await apiRequest("/profile/active");
    state.profileId = state.activeProfile.profile_id || "default";
  }

  async function loadProfileResume() {
    state.resumeInfo = await apiRequest("/profile/resume");
  }

  async function runAutofill() {
    if (!state.plan) return;
    await withBusy("Filling reviewed fields", async () => {
      const result = await fillReviewedFields(state.plan.actions);
      await rescanAndPlan();
      state.message = `Filled ${result.filled} reviewed fields. Review everything before submitting.`;
    });
  }

  async function uploadDefaultResume() {
    await withBusy("Uploading saved resume", async () => {
      const prepared = await apiRequest(
        "/extension/resume/prepare",
        {
          method: "POST",
          body: JSON.stringify({
            job_description: state.job?.description || "",
            customize: false,
          }),
        },
      );
      const upload = uploadResumeFileToApplication(prepared);
      if (upload.error) throw new Error(upload.error);
      await rescanAndPlan();
      state.message = `Uploaded ${prepared.filename}.`;
    });
  }

  async function showCustomizationPreview() {
    await withBusy("Analyzing JD against resume", async () => {
      state.preview = await apiRequest(
        "/extension/resume/customization-preview",
        {
          method: "POST",
          body: JSON.stringify({ job_description: state.job?.description || "" }),
        },
      );
      state.customizing = true;
      state.pendingResume = null;
      state.message = state.preview.available
        ? "Select only skills you can defend, then generate the customized resume."
        : "Customization is not available for this profile resume.";
    });
  }

  function openWebCustomization() {
    const params = new URLSearchParams();
    if (state.applicationId) params.set("application_id", state.applicationId);
    const jobId = state.job?.job_id;
    const url = jobId
      ? `http://localhost:3000/tailor/${encodeURIComponent(jobId)}?${params.toString()}`
      : `http://localhost:3000/jobs?${params.toString()}`;
    window.open(url, "_blank", "noopener,noreferrer");
    state.message = "Opened the guided resume customization flow in the web UI.";
    render();
  }

  async function generateCustomizedResume() {
    const selected = selectedSkillCandidates();
    await withBusy("Generating customized resume", async () => {
      const prepared = await apiRequest(
        "/extension/resume/prepare",
        {
          method: "POST",
          body: JSON.stringify({
            job_description: state.job?.description || "",
            customize: true,
            confirmed_skills: selected,
          }),
        },
      );
      state.pendingResume = prepared;
      state.message = prepared.customized
        ? `Customized resume is ready: ${prepared.filename}. Approve it to upload.`
        : `Prepared saved resume: ${prepared.filename}. ${firstWarning(prepared)}`;
    });
  }

  async function approvePendingResume() {
    if (!state.pendingResume) return;
    await withBusy("Uploading approved resume", async () => {
      const upload = uploadResumeFileToApplication(state.pendingResume);
      if (upload.error) throw new Error(upload.error);
      await rescanAndPlan();
      state.message = `Uploaded ${state.pendingResume.filename}.`;
      state.pendingResume = null;
      state.customizing = false;
    });
  }

  async function withBusy(label, task) {
    state.busy = label;
    state.error = "";
    render();
    try {
      await task();
    } catch (error) {
      state.error = error.message || String(error);
    } finally {
      state.busy = "";
      render();
    }
  }

  function render() {
    const root = document.getElementById("smartjobapply-panel");
    if (!root) return;
    root.classList.toggle("sja-minimized", state.minimized);
    if (state.minimized) {
      root.innerHTML = `
        <button class="sja-expand-button" data-action="expand" type="button" aria-label="Expand ApplyTeX ATS">
          <span>S</span>
        </button>
      `;
      bindEvents(root);
      return;
    }
    const progress = requiredProgress();
    const resumeLabel = state.resumeInfo?.has_pdf
      ? state.resumeInfo.resume_pdf_filename || "PDF resume ready"
      : state.resumeInfo?.has_latex_source
        ? "LaTeX saved, PDF not rendered"
        : "No profile resume saved";
    const profileLabel = state.activeProfile?.full_name || state.activeProfile?.profile_id || "Active profile";
    root.innerHTML = `
      <div class="sja-head">
        <div>
          <div class="sja-brand">ApplyTeX ATS</div>
          <div class="sja-muted">${state.backendReady ? "LaTeX gate ready" : "Local API unavailable"}</div>
        </div>
        <div class="sja-head-actions">
          <button class="sja-icon-button" data-action="minimize" type="button" aria-label="Minimize ApplyTeX ATS">›</button>
          <button class="sja-icon-button" data-action="close" type="button" aria-label="Close ApplyTeX ATS">x</button>
        </div>
      </div>

      <div class="sja-profile-pill">
        <span>Profile</span>
        <strong>${escapeHtml(profileLabel)}</strong>
      </div>

      <section class="sja-section">
        <div class="sja-row-between">
          <h2>Captured job</h2>
          <span class="sja-chip">${escapeHtml(state.provider)}</span>
        </div>
        <div class="sja-card">
          <strong>${escapeHtml(state.job?.title || document.title || "Current job")}</strong>
          <span>${escapeHtml(state.job?.company || "Captured from this page")}</span>
        </div>
        <details open>
          <summary>Job description</summary>
          <div class="sja-jd">${escapeHtml(compactText(state.job?.description || "Reading job description..."))}</div>
        </details>
      </section>

      <section class="sja-section">
        <div class="sja-row-between">
          <h2>Resume source</h2>
          <span class="${state.resumeInfo?.has_pdf ? "sja-ok" : "sja-warn"}">${escapeHtml(resumeLabel)}</span>
        </div>
        <div class="sja-actions">
          <button data-action="default-resume" type="button" ${state.resumeInfo?.has_pdf ? "" : "disabled"}>Upload saved PDF</button>
          <button data-action="customize-start" type="button" ${state.resumeInfo?.has_latex_source ? "" : "disabled"}>Open guided tailoring</button>
        </div>
        ${renderCustomization()}
      </section>

      <section class="sja-section">
        <div class="sja-row-between">
          <h2>Form review</h2>
          <strong>${progress.filled}/${progress.total} required ready</strong>
        </div>
        <div class="sja-track"><div class="sja-bar" style="width:${progress.percent}%"></div></div>
        <button data-action="autofill" type="button" ${state.plan ? "" : "disabled"}>Autofill reviewed fields</button>
        <div class="sja-field-list">${renderReviewItems()}</div>
      </section>

      ${state.busy ? `<div class="sja-status">${escapeHtml(state.busy)}...</div>` : ""}
      ${state.message ? `<div class="sja-status sja-success">${escapeHtml(state.message)}</div>` : ""}
      ${state.error ? `<div class="sja-status sja-error">${escapeHtml(state.error)}</div>` : ""}
    `;
    bindEvents(root);
  }

  function renderCustomization() {
    if (!state.customizing && !state.pendingResume) return "";
    if (state.pendingResume) {
      return `
        <div class="sja-subpanel">
          <strong>${state.pendingResume.customized ? "Customized resume ready" : "Resume ready"}</strong>
          <span>${escapeHtml(state.pendingResume.filename)}</span>
          ${firstWarning(state.pendingResume) ? `<span class="sja-warn">${escapeHtml(firstWarning(state.pendingResume))}</span>` : ""}
          <button data-action="approve-resume" type="button">Approve and upload resume</button>
        </div>
      `;
    }
    if (!state.preview) return "";
    if (!state.preview.available) {
      return `<div class="sja-subpanel sja-warn">${escapeHtml((state.preview.warnings || [])[0] || "Customization unavailable.")}</div>`;
    }
    const candidates = state.preview.skill_candidates || [];
    return `
      <div class="sja-subpanel">
        <strong>Fit before customization: ${Math.round(state.preview.baseline_score || 0)}/100</strong>
        <span>Select only skills you can defend in an interview.</span>
        <div class="sja-skill-list">
          ${candidates.length ? candidates.map((skill, index) => `
            <label class="sja-check">
              <input type="checkbox" data-skill-index="${index}">
              <span>${escapeHtml(skill)}</span>
            </label>
          `).join("") : `<span class="sja-muted">No missing skill confirmations found.</span>`}
        </div>
        <button data-action="customize-generate" type="button">Generate customized resume</button>
      </div>
    `;
  }

  function renderReviewItems() {
    const items = state.plan?.review_items || [];
    if (!items.length) return `<div class="sja-muted">Scanning application fields...</div>`;
    return items.map((item) => {
      const ready = item.status === "ready";
      return `
        <div class="sja-field">
          <span class="sja-dot ${ready ? "ready" : ""}">${ready ? "✓" : "−"}</span>
          <div>
            <strong>${escapeHtml(item.label || "Unlabelled field")}${item.required ? "*" : ""}</strong>
            <span>${escapeHtml(
              ready
                ? item.value_preview || item.answer_source || "Ready"
                : item.answer_source === "eeo_opt_in"
                  ? "Enable EEO autofill in profile"
                  : item.required
                    ? "Needs review"
                    : "Skipped",
            )}</span>
          </div>
        </div>
      `;
    }).join("");
  }

  function bindEvents(root) {
    root.querySelector("[data-action='close']")?.addEventListener("pointerup", () => root.remove());
    root.querySelector("[data-action='minimize']")?.addEventListener("pointerup", () => {
      state.minimized = true;
      render();
    });
    root.querySelector("[data-action='expand']")?.addEventListener("pointerup", () => {
      state.minimized = false;
      render();
    });
    root.querySelector("[data-action='autofill']")?.addEventListener("pointerup", runAutofill);
    root.querySelector("[data-action='default-resume']")?.addEventListener("pointerup", uploadDefaultResume);
    root.querySelector("[data-action='customize-start']")?.addEventListener("pointerup", openWebCustomization);
    root.querySelector("[data-action='customize-generate']")?.addEventListener("pointerup", generateCustomizedResume);
    root.querySelector("[data-action='approve-resume']")?.addEventListener("pointerup", approvePendingResume);
  }

  function selectedSkillCandidates() {
    const candidates = state.preview?.skill_candidates || [];
    return Array.from(document.querySelectorAll("#smartjobapply-panel [data-skill-index]:checked"))
      .map((item) => candidates[Number(item.dataset.skillIndex)])
      .filter(Boolean);
  }

  function requiredProgress() {
    const items = state.plan?.review_items || [];
    const required = items.filter((item) => item.required);
    const ready = required.filter((item) => item.status === "ready");
    const total = required.length;
    return {
      filled: ready.length,
      total,
      percent: total ? Math.round((ready.length / total) * 100) : 0,
    };
  }

  function ensurePanel() {
    let root = document.getElementById("smartjobapply-panel");
    if (!root) {
      root = document.createElement("aside");
      root.id = "smartjobapply-panel";
      document.body.append(root);
    }
  }

  function ensureStyles() {
    if (document.getElementById("smartjobapply-panel-style")) return;
    const style = document.createElement("style");
    style.id = "smartjobapply-panel-style";
    style.textContent = `
      #smartjobapply-panel {
        position: fixed;
        top: 0;
        right: 0;
        z-index: 2147483647;
        width: min(390px, 34vw);
        min-width: 340px;
        height: 100vh;
        overflow-x: hidden;
        overflow-y: auto;
        box-sizing: border-box;
        padding: 14px;
        border-left: 1px solid #dedad0;
        background: #fbfaf7;
        color: #17201b;
        box-shadow: -14px 0 34px rgba(0,0,0,0.16);
        font-family: "IBM Plex Sans", "Avenir Next", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 13px;
      }
      #smartjobapply-panel.sja-minimized {
        top: 132px;
        right: 14px;
        width: 46px;
        min-width: 46px;
        height: 46px;
        padding: 0;
        overflow: visible;
        border: 0;
        background: transparent;
        box-shadow: none;
      }
      #smartjobapply-panel * { box-sizing: border-box; letter-spacing: 0; }
      #smartjobapply-panel,
      #smartjobapply-panel * {
        max-width: 100%;
      }
      #smartjobapply-panel button {
        min-height: 34px;
        border: 1px solid #177a55;
        border-radius: 6px;
        color: #ffffff;
        background: #177a55;
        font-weight: 700;
        cursor: pointer;
      }
      #smartjobapply-panel button:disabled { cursor: default; opacity: 0.48; }
      #smartjobapply-panel h2 { margin: 0; font-size: 15px; }
      #smartjobapply-panel summary { cursor: pointer; font-weight: 700; margin: 8px 0; }
      #smartjobapply-panel .sja-head,
      #smartjobapply-panel .sja-row-between {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      #smartjobapply-panel .sja-head { margin-bottom: 12px; }
      #smartjobapply-panel .sja-head-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      #smartjobapply-panel .sja-brand { font-size: 20px; font-weight: 850; font-family: Georgia, "Times New Roman", serif; }
      #smartjobapply-panel .sja-icon-button {
        width: 30px;
        min-height: 30px;
        border-radius: 50%;
        color: #203028;
        background: #f0efe8;
        border-color: #dedad0;
      }
      #smartjobapply-panel .sja-expand-button {
        display: grid;
        place-items: center;
        width: 46px;
        min-height: 46px;
        border-radius: 50%;
        border: 1px solid #dedad0;
        color: #ffffff;
        background: #177a55;
        box-shadow: 0 8px 22px rgba(0,0,0,0.2);
      }
      #smartjobapply-panel .sja-expand-button span {
        display: grid;
        place-items: center;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        background: #edf5ef;
        color: #177a55;
        font-size: 14px;
        font-weight: 900;
      }
      #smartjobapply-panel .sja-section {
        display: grid;
        gap: 10px;
        padding: 12px 0;
        border-top: 1px solid #dedad0;
      }
      #smartjobapply-panel .sja-label,
      #smartjobapply-panel .sja-muted {
        color: #5d645f;
        font-size: 12px;
      }
      #smartjobapply-panel .sja-profile-pill {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        width: 100%;
        margin: 4px 0 12px;
        padding: 8px 9px;
        border: 1px solid #dedad0;
        border-radius: 8px;
        background: #ffffff;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-profile-pill span {
        color: #5d645f;
        font-size: 12px;
      }
      #smartjobapply-panel .sja-profile-pill strong {
        min-width: 0;
        text-align: right;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-input {
        width: 100%;
        min-height: 34px;
        padding: 8px 9px;
        margin: 5px 0 12px;
        border: 1px solid #dedad0;
        border-radius: 6px;
      }
      #smartjobapply-panel .sja-chip,
      #smartjobapply-panel .sja-ok,
      #smartjobapply-panel .sja-warn {
        padding: 3px 7px;
        border-radius: 6px;
        background: #edf5ef;
        color: #177a55;
        font-size: 11px;
        font-weight: 800;
      }
      #smartjobapply-panel .sja-warn { background: #fff3dd; color: #b56a14; }
      #smartjobapply-panel .sja-card,
      #smartjobapply-panel .sja-subpanel {
        display: grid;
        gap: 5px;
        padding: 9px;
        border: 1px solid #dedad0;
        border-radius: 8px;
        background: #ffffff;
        min-width: 0;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-jd {
        max-height: 170px;
        overflow-x: hidden;
        overflow-y: auto;
        padding: 9px;
        border: 1px solid #dedad0;
        border-radius: 7px;
        color: #303b35;
        background: #ffffff;
        font-size: 12px;
        line-height: 1.42;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-actions {
        display: grid;
        grid-template-columns: 1fr;
        gap: 8px;
      }
      #smartjobapply-panel .sja-track {
        height: 7px;
        overflow: hidden;
        border-radius: 999px;
        background: #f0efe8;
      }
      #smartjobapply-panel .sja-bar {
        height: 100%;
        background: #177a55;
      }
      #smartjobapply-panel .sja-field-list,
      #smartjobapply-panel .sja-skill-list {
        display: grid;
        gap: 8px;
        max-height: 310px;
        overflow-x: hidden;
        overflow-y: auto;
      }
      #smartjobapply-panel .sja-field {
        display: grid;
        grid-template-columns: 26px minmax(0, 1fr);
        gap: 7px;
        align-items: center;
      }
      #smartjobapply-panel .sja-field strong,
      #smartjobapply-panel .sja-field span {
        display: block;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-field span { color: #5d645f; font-size: 12px; }
      #smartjobapply-panel .sja-dot {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        min-width: 18px;
        height: 18px;
        border-radius: 50%;
        color: #7a817a;
        background: #f0efe8;
        font-size: 12px;
        line-height: 1;
        font-weight: 800;
        text-align: center;
        align-self: center;
      }
      #smartjobapply-panel .sja-dot.ready { color: #ffffff; background: #177a55; }
      #smartjobapply-panel .sja-check {
        display: grid;
        grid-template-columns: 22px minmax(0, 1fr);
        gap: 8px;
        align-items: center;
      }
      #smartjobapply-panel .sja-check input[type="checkbox"] {
        appearance: none;
        display: inline-grid;
        place-items: center;
        width: 18px;
        height: 18px;
        margin: 0;
        border: 2px solid #dedad0;
        border-radius: 5px;
        background: #ffffff;
      }
      #smartjobapply-panel .sja-check input[type="checkbox"]:checked {
        border-color: #177a55;
        background: #177a55;
      }
      #smartjobapply-panel .sja-check input[type="checkbox"]:checked::after {
        content: "✓";
        color: #ffffff;
        font-size: 14px;
        line-height: 1;
        font-weight: 900;
      }
      #smartjobapply-panel .sja-status {
        margin-top: 10px;
        padding: 8px;
        border-radius: 7px;
        background: #f0efe8;
        color: #38443e;
      }
      #smartjobapply-panel .sja-success { background: #edf5ef; color: #177a55; }
      #smartjobapply-panel .sja-error { background: #fff1f1; color: #9f2f2f; }
    `;
    document.head.append(style);
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Local API returned ${response.status}.`);
    }
    return response.json();
  }

  function providerForUrl(url) {
    const hostname = new URL(url || "https://invalid.local").hostname;
    if (hostname === "www.linkedin.com" || hostname === "linkedin.com") return "linkedin";
    if (hostname.endsWith("greenhouse.io")) return "greenhouse";
    if (hostname.endsWith("lever.co")) return "lever";
    if (hostname.endsWith("ashbyhq.com")) return "ashby";
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

  function normalizeProfileId(value) {
    const cleaned = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
    return cleaned || "default";
  }

  async function extractJobFromPage(detectedProvider) {
    const text = (element) => element?.textContent?.trim() || "";
    const firstText = (selectors) => {
      for (const selector of selectors) {
        const element = document.querySelector(selector);
        const value = element?.getAttribute?.("content") || text(element);
        if (value) return value;
      }
      return "";
    };
    const selectors = {
      linkedin: {
        title: [".job-details-jobs-unified-top-card__job-title", ".jobs-unified-top-card__job-title", "h1"],
        company: [".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name"],
        location: [".job-details-jobs-unified-top-card__primary-description-container", ".jobs-unified-top-card__bullet"],
        description: [".jobs-description__content", "#job-details"],
      },
      greenhouse: {
        title: [".app-title", "h1"],
        company: ["meta[property='og:site_name']", ".company-name", "[data-mapped='true'] .company"],
        location: [".location", "[class*='location']"],
        description: ["#content", ".job__description", "main", "body"],
      },
      lever: {
        title: [".posting-headline h2", "h1"],
        company: [".main-header-logo img", "meta[property='og:site_name']"],
        location: [".posting-categories .location", ".location"],
        description: [".posting-page .content", ".posting-description", "main"],
      },
      ashby: {
        title: ["h1", "[data-testid='job-title']", "[class*='job-title']"],
        company: ["meta[property='og:site_name']", "[class*='company']", "header"],
        location: ["[class*='location']", "[data-testid='location']"],
        description: ["main", "[data-testid='job-description']", "[class*='description']"],
      },
    };
    if (detectedProvider === "ashby") {
      await activateTab("Overview", 650);
    }
    const config = selectors[detectedProvider] || {};
    let company = firstText(config.company || []);
    if (!company && detectedProvider === "lever") {
      company = document.querySelector(".main-header-logo img")?.alt || "";
    }
    if (!company && detectedProvider === "greenhouse") {
      company = companyFromGreenhousePage();
    }
    if (!company && detectedProvider === "ashby") {
      company = document.querySelector("meta[property='og:site_name']")?.content || location.pathname.split("/").filter(Boolean)[0] || "";
    }
    const title = firstText(config.title || []);
    const description = cleanDescription(firstText(config.description || []), detectedProvider);
    if (detectedProvider === "ashby") {
      await activateTab("Application", 250);
    }
    if (!title || !description) {
      throw new Error("The job title or description could not be identified on this page.");
    }
    return {
      provider: detectedProvider,
      external_id: location.pathname.split("/").filter(Boolean).pop() || "",
      company: company || document.title.split("|").pop()?.trim() || "Unknown company",
      title,
      description,
      location: firstText(config.location || []),
      source_url: location.href,
      apply_url: location.href,
    };
  }

  function companyFromGreenhousePage() {
    const title = document.title || "";
    const fromApplicationTitle = title.match(/\bat\s+(.+?)(?:\s*$|\s+-\s+|\s+\|)/i)?.[1]?.trim();
    if (fromApplicationTitle) return fromApplicationTitle;
    const fromMeta = document.querySelector("meta[property='og:site_name']")?.content?.trim();
    if (fromMeta && !/greenhouse/i.test(fromMeta)) return fromMeta;
    const parsed = new URL(location.href);
    const boardToken = parsed.searchParams.get("for") || parsed.pathname.split("/").filter(Boolean)[0] || "";
    return humanizeBoardToken(boardToken);
  }

  function humanizeBoardToken(value) {
    return String(value || "")
      .replace(/[-_]+/g, " ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, (letter) => letter.toUpperCase())
      .trim();
  }

  async function activateTab(label, delayMs) {
    const wanted = label.toLowerCase();
    const tab = Array.from(document.querySelectorAll("button, [role='tab']"))
      .find((element) => (element.textContent || "").trim().toLowerCase() === wanted);
    if (tab) {
      tab.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  function cleanDescription(value, detectedProvider = "") {
    let cleaned = String(value || "").replace(/\n{3,}/g, "\n\n").trim();
    if (detectedProvider === "greenhouse") {
      const stopMarkers = [
        "PLEASE NOTE: We collect, retain and use personal data",
        "We collect, retain and use personal data",
        "Create a Job Alert",
        "Apply for this job",
        "Voluntary Self-Identification",
      ];
      for (const marker of stopMarkers) {
        const index = cleaned.toLowerCase().indexOf(marker.toLowerCase());
        if (index > 0) cleaned = cleaned.slice(0, index).trim();
      }
    }
    return cleaned;
  }

  function scanApplicationForm(detectedProvider) {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    const fields = [];
    const usedFieldIds = new Set();
    const radioNames = new Set();
    Array.from(document.querySelectorAll("input[type='radio']")).forEach((element, index) => {
      if (!visible(element)) return;
      const name = element.name || element.id || `radio-${index}`;
      if (radioNames.has(name)) return;
      radioNames.add(name);
      const group = Array.from(document.querySelectorAll(`input[type='radio'][name='${cssEscape(name)}']`))
        .filter((candidate) => visible(candidate));
      const groupLabels = group.map((candidate) => labelFor(candidate)).filter(Boolean);
      fields.push({
        field_id: name,
        label: radioGroupLabel(element, groupLabels),
        input_type: "radio",
        required: group.some((candidate) => candidate.required || candidate.getAttribute("aria-required") === "true"),
        options: group.map((candidate, groupIndex) => (groupLabels[groupIndex] || candidate.value || "").trim()).filter(Boolean),
        sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(groupLabels.join(" ")),
        autocomplete: null,
        current_value_present: group.some((candidate) => candidate.checked),
      });
      usedFieldIds.add(name);
    });

    Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
      .filter((element) => {
        const fileInput = element instanceof HTMLInputElement && element.type === "file";
        const ignoredInput = element instanceof HTMLInputElement && ["hidden", "button", "submit", "reset", "radio"].includes(element.type);
        return !ignoredInput && !isDecorativeSelectInput(element) && (visible(element) || fileInput);
      })
      .forEach((element, index) => {
        const generatedId = uniqueFieldId(element, index, usedFieldIds);
        element.dataset.smartjobapplyFieldId = generatedId;
        const label = labelFor(element);
        const inputType =
          element.tagName === "SELECT"
            ? "select"
            : element.tagName === "TEXTAREA"
              ? "textarea"
              : element.getAttribute("contenteditable") === "true"
                ? "contenteditable"
                : isCustomSelectInput(element)
                  ? "select"
                  : element.type || "text";
        const options =
          element.tagName === "SELECT"
            ? Array.from(element.options).map((option) => option.textContent.trim()).filter(Boolean)
            : [];
        const checkbox = element instanceof HTMLInputElement && element.type === "checkbox";
        const fileInput = element instanceof HTMLInputElement && element.type === "file";
        fields.push({
          field_id: generatedId,
          label,
          input_type: inputType,
          required: element.required || element.getAttribute("aria-required") === "true" || requiredFromContext(element, label),
          options,
          sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
          autocomplete: element.getAttribute("autocomplete"),
          current_value_present: checkbox
            ? element.checked
            : fileInput
            ? Boolean(element.files?.length || element.value)
            : Boolean(element.value || element.textContent?.trim()),
        });
      });
    return {
      provider: detectedProvider,
      page_url: location.href,
      page_title: document.title,
      questions: fields,
    };
  }

  function uniqueFieldId(element, index, usedFieldIds) {
    const raw =
      element.id ||
      element.name ||
      element.getAttribute("aria-labelledby") ||
      element.getAttribute("aria-label") ||
      `sja-field-${index}`;
    let fieldId = String(raw).trim() || `sja-field-${index}`;
    if (element instanceof HTMLInputElement && element.type === "checkbox" && !element.id) {
      const checkboxValue = String(element.value || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      fieldId = `${fieldId}-${checkboxValue || index}`;
    }
    let uniqueId = fieldId;
    let suffix = 2;
    while (usedFieldIds.has(uniqueId)) {
      uniqueId = `${fieldId}-${suffix}`;
      suffix += 1;
    }
    usedFieldIds.add(uniqueId);
    return uniqueId;
  }

  function isDecorativeSelectInput(element) {
    if (!(element instanceof HTMLInputElement)) return false;
    const hasStableIdentifier = Boolean(element.id || element.name || element.getAttribute("aria-label") || element.getAttribute("aria-labelledby"));
    if (hasStableIdentifier) return false;
    if (!element.closest("[class*='select'], [role='combobox']")) return false;
    const label = weakText(labelFor(element));
    return !label || isGenericControlText(label) || label === "Unlabelled field" || !element.value;
  }

  function isCustomSelectInput(element) {
    if (!(element instanceof HTMLInputElement)) return false;
    if (element.getAttribute("role") === "combobox") return true;
    if (element.getAttribute("aria-autocomplete") || element.getAttribute("aria-controls")) return true;
    return Boolean(element.closest(".select__input-container, .select-shell, [class*='select']")) && element.autocomplete === "off";
  }

  function requiredFromContext(element, label) {
    if (/\*/.test(label)) return true;
    const container = element.closest("label, [data-field], .field, .field-wrapper, div, section, li");
    return /\*/.test(container?.textContent || "");
  }

  function labelFor(element) {
    if (element instanceof HTMLInputElement && element.type === "file") {
      const fileLabel = fileUploadLabelFor(element);
      if (fileLabel) return fileLabel;
    }
    if (element.labels?.length) {
      return Array.from(element.labels).map((label) => label.textContent.trim()).join(" ");
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const value = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      if (value) return value;
    }
    const aria = weakText(element.getAttribute("aria-label"));
    if (aria && !isGenericControlText(aria)) return aria;
    const nearby = nearbyQuestionLabel(element);
    if (nearby) return nearby;
    const placeholder = weakText(element.getAttribute("placeholder"));
    if (placeholder && !isGenericControlText(placeholder)) return placeholder;
    const containerText = cleanControlText(element.closest("label, [data-field], .field, .field-wrapper, div, section, li")?.textContent || "");
    return containerText || aria || placeholder || element.name || element.id || "Unlabelled field";
  }

  function fileUploadLabelFor(element) {
    const fieldId = weakText(element.id || element.name).toLowerCase();
    const containerText = weakText(element.closest("label, [data-field], .field, .field-wrapper, div, section, li")?.textContent || "");
    if (fieldId.includes("resume") || fieldId.includes("cv")) return /\*/.test(containerText) ? "Resume/CV*" : "Resume/CV";
    if (fieldId.includes("cover")) return /\*/.test(containerText) ? "Cover Letter*" : "Cover Letter";
    if (/resume|cv/i.test(containerText)) return /\*/.test(containerText) ? "Resume/CV*" : "Resume/CV";
    if (/cover letter/i.test(containerText)) return /\*/.test(containerText) ? "Cover Letter*" : "Cover Letter";
    return "";
  }

  function radioGroupLabel(element, optionLabels) {
    const fieldset = element.closest("fieldset");
    const legend = fieldset?.querySelector("legend")?.textContent?.trim();
    if (legend) return legend;
    return nearbyQuestionLabel(element, optionLabels) || optionLabels[0] || "Unlabelled field";
  }

  function nearbyQuestionLabel(element, ignoredPhrases = []) {
    const ignored = [...ignoredPhrases, element.getAttribute("placeholder") || "", element.value || ""].filter(Boolean);
    let current = element.closest("label, [data-field], .field, .field-wrapper, [class*='question'], [class*='application'], div, section, li");
    for (let depth = 0; current && depth < 7; depth += 1) {
      const text = cleanControlText(current.textContent || "", ignored);
      if (isUsefulQuestionText(text)) return text;
      current = current.parentElement;
    }
    let sibling = element.previousElementSibling;
    for (let depth = 0; sibling && depth < 4; depth += 1) {
      const text = cleanControlText(sibling.textContent || "", ignored);
      if (isUsefulQuestionText(text)) return text;
      sibling = sibling.previousElementSibling;
    }
    return "";
  }

  function cleanControlText(value, ignoredPhrases = []) {
    let text = weakText(value);
    for (const phrase of ignoredPhrases) {
      const cleaned = weakText(phrase);
      if (cleaned) text = text.replaceAll(cleaned, " ");
    }
    text = text
      .replace(/\bSelect\.\.\./gi, " ")
      .replace(/\bType your response\b/gi, " ")
      .replace(/\bAttach resume\/cv\b/gi, " ")
      .replace(/\bAttach\b/gi, " ")
      .replace(/\bNo file chosen\b/gi, " ")
      .replace(/\bAnalyzing resume\.\.\.\b/gi, " ")
      .replace(/\bSuccess!\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim();
    return text;
  }

  function weakText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function isGenericControlText(value) {
    return /^(select\.\.\.|type your response|attach|search|choose|no file chosen)$/i.test(weakText(value));
  }

  function isUsefulQuestionText(value) {
    const text = weakText(value);
    if (!text || isGenericControlText(text)) return false;
    if (text.length > 320) return false;
    return text.length >= 3 && /[a-z0-9]/i.test(text);
  }

  async function fillReviewedFields(actions) {
    const setNativeValue = (element, value) => {
      const prototype =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : element instanceof HTMLSelectElement
            ? HTMLSelectElement.prototype
            : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      if (descriptor?.set) descriptor.set.call(element, value);
      else element.value = value;
    };
    const dispatch = (element) => {
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      element.dispatchEvent(new Event("blur", { bubbles: true }));
    };
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    const optionMatches = (optionText, wantedText) => {
      const option = weakText(optionText).toLowerCase();
      const wanted = weakText(wantedText).toLowerCase();
      return option === wanted || option.includes(wanted) || wanted.includes(option);
    };
    const selectNativeOption = (element, value) => {
      const option = Array.from(element.options).find((candidate) => optionMatches(candidate.textContent, value));
      if (!option) return false;
      setNativeValue(element, option.value);
      dispatch(element);
      return true;
    };
    const selectRadioOption = (fieldId, value) => {
      const radios = Array.from(document.querySelectorAll(`input[type='radio'][name='${cssEscape(fieldId)}']`))
        .filter((candidate) => visible(candidate));
      const wanted = weakText(value);
      const radio = radios.find((candidate) => {
        const label = candidate.labels?.length
          ? Array.from(candidate.labels).map((item) => item.textContent.trim()).join(" ")
          : candidate.value || "";
        return optionMatches(label, wanted) || optionMatches(candidate.value, wanted);
      });
      if (!radio) return false;
      radio.checked = true;
      dispatch(radio);
      return true;
    };
    const firePointerSequence = (element) => {
      for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
        element.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
      }
    };
    const waitForOption = async (value) => {
      const deadline = Date.now() + 900;
      while (Date.now() < deadline) {
        const options = Array.from(document.querySelectorAll("[role='option'], [id*='option'], [class*='option']"))
          .filter((candidate) => visible(candidate));
        const matched = options.find((candidate) => optionMatches(candidate.textContent, value));
        if (matched) return matched;
        await new Promise((resolve) => setTimeout(resolve, 60));
      }
      return null;
    };
    const selectCustomOption = async (element, value) => {
      const target = element.closest("[role='combobox'], .select-shell, [class*='select']") || element;
      element.scrollIntoView({ block: "center", inline: "nearest" });
      firePointerSequence(target);
      element.focus();
      setNativeValue(element, String(value));
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
      const option = await waitForOption(value);
      if (!option) return false;
      firePointerSequence(option);
      dispatch(element);
      return true;
    };
    let filled = 0;
    let skipped = 0;
    for (const action of actions) {
      if (action.action === "skip" || action.action === "upload" || action.value === null) {
        skipped += 1;
        continue;
      }
      const element = findField(action.field_id);
      if (!element) {
        skipped += 1;
        continue;
      }
      if (action.action === "select" && element instanceof HTMLSelectElement) {
        if (!selectNativeOption(element, action.value)) {
          skipped += 1;
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select" && element instanceof HTMLInputElement && element.type === "radio") {
        if (!selectRadioOption(action.field_id, action.value)) {
          skipped += 1;
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select" && isCustomSelectInput(element)) {
        if (!(await selectCustomOption(element, action.value))) {
          skipped += 1;
          continue;
        }
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
      setNativeValue(element, String(action.value));
      dispatch(element);
      filled += 1;
    }
    return { filled, skipped };
  }

  function findField(fieldId) {
    const byId = document.getElementById(fieldId);
    if (byId) return byId;
    return Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
      .find((element) => element.name === fieldId || element.dataset.smartjobapplyFieldId === fieldId) || null;
  }

  function uploadResumeFileToApplication(preparedResume) {
    const candidates = Array.from(document.querySelectorAll("input[type='file']"));
    if (!candidates.length) {
      return { error: "No file upload field was found on this application." };
    }
    candidates.sort((left, right) => scoreFileInput(right) - scoreFileInput(left));
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
    return { uploaded: true, filename: preparedResume.filename };
  }

  function scoreFileInput(element) {
    const label = labelFor(element).toLowerCase();
    let score = 0;
    if (label.includes("resume") || label.includes("cv")) score += 10;
    if (label.includes("cover letter")) score -= 8;
    if (element.accept?.toLowerCase().includes("pdf")) score += 2;
    return score;
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return value.replace(/['\\]/g, "\\$&");
  }

  function firstWarning(prepared) {
    return prepared?.warnings?.length ? prepared.warnings[0] : "";
  }

  function compactText(value) {
    return String(value || "").replace(/\n{3,}/g, "\n\n").trim();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }
})();
