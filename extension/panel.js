(() => {
  const PANEL_INSTANCE_KEY = "__applytexPanelInstanceId";
  const runtimeId = chrome.runtime?.id || "unknown";
  if (window.__smartJobApplyPanelInstalled && window[PANEL_INSTANCE_KEY] === runtimeId) {
    window.dispatchEvent(new CustomEvent("smartjobapply:open"));
    return;
  }
  if (window.__smartJobApplyPanelInstalled && window[PANEL_INSTANCE_KEY] !== runtimeId) {
    document.getElementById("smartjobapply-panel")?.remove();
  }
  window.__smartJobApplyPanelInstalled = true;
  window[PANEL_INSTANCE_KEY] = runtimeId;

  const shared = globalThis.ApplyTexPanelShared || {};
  const scanParts = globalThis.ApplyTexPanelScan || {};
  const fillParts = globalThis.ApplyTexPanelFill || {};
  const workdayParts = globalThis.ApplyTexPanelWorkday || {};
  const FLOW_STORAGE_KEY = shared.FLOW_STORAGE_KEY || "applicationFlows";
  const LOW_CONFIDENCE_CONTEXT_MESSAGE = shared.LOW_CONFIDENCE_CONTEXT_MESSAGE
    || "Open the original job page once, then return here.";
  const US_STATE_CODE_BY_NAME = shared.US_STATE_CODE_BY_NAME || Object.freeze({});
  const US_STATE_CODES = shared.US_STATE_CODES || new Set(Object.values(US_STATE_CODE_BY_NAME));
  const PROFILE_STORAGE_KEY = "applytexExtensionProfileId";
  const TOKEN_STORAGE_KEY = "applytexExtensionAccessToken";
  const WEB_APP_BASE = "http://localhost:3000";
  const state = {
    provider: providerForUrl(location.href),
    flowKey: workflowKeyForUrl(location.href, providerForUrl(location.href)),
    profileId: "",
    accessToken: "",
    authRequired: false,
    signedIn: false,
    needsSignIn: false,
    signInUsername: "",
    signInPassword: "",
    availableProfiles: [],
    activeProfile: null,
    profile: null,
    recordSelections: {},
    backendReady: false,
    job: null,
    applicationId: null,
    applicationDetail: null,
    scan: null,
    plan: null,
    answerDrafts: {},
    automaticAnswerStatus: {},
    generatedAnswerFields: {},
    lastFillResult: null,
    resumeInfo: null,
    preview: null,
    pendingResume: null,
    approvedArtifact: null,
    panelTab: "autofill",
    scoreError: "",
    contextRestoreSource: "",
    contextWarning: "",
    busy: "",
    autofillProgress: null,
    message: "",
    error: "",
    customizing: false,
    minimized: false,
    initialized: false,
    initializePromise: null,
  };
  let pageObserver = null;
  let pageMonitorInterval = null;
  let observedRescanTimer = null;
  let observedRescanRunning = false;
  let lastFormFingerprint = "";
  let automaticAnswerQueueRunning = false;
  let automaticAnswerEpoch = 0;
  let activeAutofillRun = null;

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "SMARTJOBAPPLY_OPEN") {
      openPanel();
    }
  });
  window.addEventListener("smartjobapply:open", openPanel);
  window.addEventListener("focus", () => {
    if (!state.initialized || state.needsSignIn || !state.applicationId || state.busy) return;
    loadApplicationDetail()
      .then(loadApprovedArtifact)
      .then(render)
      .catch(() => {});
  });
  openPanel();

  async function openPanel() {
    ensureStyles();
    ensurePanel();
    if (state.signedIn) startPageMonitoring();
    render();
    if (state.initializePromise) return state.initializePromise;
    state.initializePromise = (state.initialized && state.signedIn ? refreshCurrentPage() : initialize())
      .finally(() => {
        state.initializePromise = null;
      });
    return state.initializePromise;
  }

  async function initialize() {
    const saved = await chrome.storage.local.get([
      FLOW_STORAGE_KEY,
      "applicationByUrl",
      "applicationByJob",
      PROFILE_STORAGE_KEY,
      TOKEN_STORAGE_KEY,
    ]);
    try {
      await apiRequest("/health");
      state.backendReady = true;
    } catch {
      state.backendReady = false;
      state.error = "Start the local ApplyTeX ATS API on port 8000.";
      render();
      return;
    }
    await refreshAuthStatus();
    state.accessToken = String(saved[TOKEN_STORAGE_KEY] || "").trim();
    if (!state.authRequired) {
      await loadAvailableProfiles();
    }
    const storedProfileId = normalizeProfileId(saved[PROFILE_STORAGE_KEY] || "");
    if (!storedProfileId || storedProfileId === "default") {
      if (storedProfileId === "default") await clearSignedInProfileId();
      state.needsSignIn = true;
      state.signedIn = false;
      state.initialized = true;
      stopPageMonitoring();
      render();
      return;
    }
    if (state.authRequired && !state.accessToken) {
      state.needsSignIn = true;
      state.signedIn = false;
      state.signInUsername = storedProfileId;
      state.initialized = true;
      stopPageMonitoring();
      render();
      return;
    }
    try {
      await signInWithProfileId(storedProfileId, {
        persist: false,
        password: "",
        reuseToken: true,
      });
    } catch (error) {
      state.needsSignIn = true;
      state.signedIn = false;
      state.error = error.message || String(error);
      state.initialized = true;
      stopPageMonitoring();
      render();
      return;
    }
    restoreFlowContext(saved[FLOW_STORAGE_KEY] || {});
    state.initialized = true;
    await refreshCurrentPage(saved);
  }

  function normalizeProfileId(value) {
    // Match web app username rules; never silently fall back to "default".
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_.-]+/g, "_")
      .replace(/^[._-]+|[._-]+$/g, "");
  }

  async function loadAvailableProfiles() {
    try {
      const body = await apiRequest("/profiles");
      state.availableProfiles = Array.isArray(body?.profiles) ? body.profiles : [];
    } catch {
      state.availableProfiles = [];
    }
  }

  function usableProfiles() {
    return (state.availableProfiles || []).filter((profile) => profile.usable);
  }

  function profileHintText() {
    const usable = usableProfiles();
    if (!usable.length) {
      return "No filled profiles yet. Create one in the web app first (e.g. sign in there and save your resume).";
    }
    return `Existing accounts: ${usable.map((profile) => {
      const name = weakText(profile.full_name);
      return name ? `${profile.profile_id} (${name})` : profile.profile_id;
    }).join(", ")}`;
  }

  async function persistSignedInProfileId(profileId) {
    await chrome.storage.local.set({ [PROFILE_STORAGE_KEY]: profileId });
  }

  async function persistAccessToken(token) {
    state.accessToken = String(token || "").trim();
    if (state.accessToken) {
      await chrome.storage.local.set({ [TOKEN_STORAGE_KEY]: state.accessToken });
    } else {
      await chrome.storage.local.remove([TOKEN_STORAGE_KEY]);
    }
  }

  async function clearSignedInProfileId() {
    await chrome.storage.local.remove([PROFILE_STORAGE_KEY, TOKEN_STORAGE_KEY]);
    state.accessToken = "";
  }

  async function refreshAuthStatus(profileId = "") {
    const query = profileId
      ? `/auth/status?profile_id=${encodeURIComponent(profileId)}`
      : "/auth/status";
    const status = await apiRequest(query, { skipAuth: true });
    state.authRequired = Boolean(status?.auth_required);
    return status;
  }

  function profileLooksUsable(profile) {
    if (!profile) return false;
    return Boolean(
      weakText(profile.full_name)
      || weakText(profile.email)
      || profile.has_pdf
      || profile.has_latex_source,
    );
  }

  async function signInWithProfileId(
    profileId,
    { persist = true, password = "", reuseToken = false } = {},
  ) {
    const cleaned = normalizeProfileId(profileId);
    if (!cleaned) throw new Error("Enter a username to sign in.");
    if (cleaned === "default") {
      throw new Error(`"default" is an empty system profile. ${profileHintText()}`);
    }
    const authStatus = await refreshAuthStatus(cleaned);
    if (authStatus.auth_required) {
      if (!reuseToken || !state.accessToken) {
        const pwd = String(password || state.signInPassword || "");
        if (pwd.length < 8) {
          throw new Error("Password must be at least 8 characters when auth is enabled.");
        }
        const login = await apiRequest("/auth/login", {
          method: "POST",
          body: JSON.stringify({
            profile_id: cleaned,
            password: pwd,
            set_password: !authStatus.has_password,
          }),
          skipAuth: true,
        });
        await persistAccessToken(login.access_token);
      }
    } else {
      await persistAccessToken("");
      await loadAvailableProfiles();
      const listed = (state.availableProfiles || []).find((profile) => profile.profile_id === cleaned);
      if (state.availableProfiles.length && (!listed || !listed.usable)) {
        const hint = profileHintText();
        throw new Error(
          listed
            ? `Profile "${cleaned}" is empty (no name/email/resume). ${hint}`
            : `Unknown username "${cleaned}". ${hint}`,
        );
      }
    }
    await apiRequest("/profile/active", {
      method: "PUT",
      body: JSON.stringify({ profile_id: cleaned }),
      profileId: cleaned,
    });
    state.profileId = cleaned;
    state.signedIn = true;
    state.needsSignIn = false;
    state.signInUsername = cleaned;
    state.signInPassword = "";
    if (persist) await persistSignedInProfileId(cleaned);
    await loadActiveProfile();
    if (!profileLooksUsable(state.activeProfile)) {
      await clearSignedInProfileId();
      state.signedIn = false;
      state.needsSignIn = true;
      state.profileId = "";
      throw new Error(`Profile "${cleaned}" is empty (no name/email/resume). ${profileHintText()}`);
    }
    await loadProfileResume();
    startPageMonitoring();
  }

  async function signOutExtension() {
    await clearSignedInProfileId();
    stopPageMonitoring();
    state.signedIn = false;
    state.needsSignIn = true;
    state.profileId = "";
    state.activeProfile = null;
    state.profile = null;
    state.resumeInfo = null;
    state.job = null;
    state.applicationId = null;
    state.applicationDetail = null;
    state.scan = null;
    state.plan = null;
    resetAutomaticAnswerState();
    state.lastFillResult = null;
    state.approvedArtifact = null;
    state.message = "Signed out. Sign in with a username to continue.";
    state.error = "";
    render();
  }

  async function switchAccount() {
    await clearSignedInProfileId();
    stopPageMonitoring();
    state.signedIn = false;
    state.needsSignIn = true;
    state.signInUsername = state.profileId || "";
    state.signInPassword = "";
    state.message = state.authRequired
      ? "Switch account — enter username and password."
      : "Switch account — enter another username.";
    state.error = "";
    render();
  }

  async function refreshCurrentPage(savedStorage = null, { forceCapture = false } = {}) {
    await withBusy("Reading job and scanning form", async () => {
      const saved = savedStorage || await chrome.storage.local.get([FLOW_STORAGE_KEY, "applicationByUrl", "applicationByJob"]);
      syncPageContext(saved[FLOW_STORAGE_KEY] || {});
      if (!state.job) restoreFlowContext(saved[FLOW_STORAGE_KEY] || {});
      state.contextWarning = "";

      let captureError = null;
      if (forceCapture || shouldCaptureJobFromPage()) {
        try {
          const previousJobId = state.job?.job_id || null;
          const capturedJob = await extractJobFromPage(state.provider);
          const savedJob = await apiRequest("/extension/jobs/capture", {
            method: "POST",
            body: JSON.stringify(capturedJob),
          });
          if (previousJobId && previousJobId !== savedJob.job_id) {
            state.applicationId = null;
            resetAutomaticAnswerState();
          }
          state.job = savedJob;
        } catch (error) {
          captureError = error;
        }
      }

      if (state.job && !state.applicationId) {
        const legacyApplicationId = saved.applicationByUrl?.[canonicalPageKey(location.href)] || null;
        const jobApplicationId = state.job?.job_id
          ? saved.applicationByJob?.[applicationStorageKey(state.job.job_id)] || null
          : null;
        state.applicationId = legacyApplicationId || jobApplicationId;
        if (!state.applicationId) {
          const application = await apiRequest("/applications", {
            method: "POST",
            body: JSON.stringify({ job_id: state.job.job_id, profile_id: state.profileId }),
          });
          state.applicationId = application.application_id;
        }
      }

      if (state.job) {
        await persistFlowContext();
        await loadApplicationDetail();
        await loadApprovedArtifact();
        await refreshApplicationScore({ quiet: true });
      }

      const scan = scanApplicationForm(state.provider, state.recordSelections);
      lastFormFingerprint = formFingerprint(scan);
      if (state.job && shouldScanApplicationForm(scan)) {
        if (isLowConfidenceApplicationContext()) {
          state.scan = scan;
          state.plan = null;
          state.contextWarning = LOW_CONFIDENCE_CONTEXT_MESSAGE;
          state.error = LOW_CONFIDENCE_CONTEXT_MESSAGE;
          return;
        }
        await saveScanAndPlan(scan);
        state.message = "Job context restored and application fields are ready.";
        return;
      }

      state.scan = scan;
      state.plan = null;
      if (state.job) {
        state.message = isAuthenticationPage()
          ? "Job context saved. Sign in to continue; the application form will be scanned after login."
          : "Job context saved. Open the application form to continue.";
        return;
      }
      throw captureError || new Error("Open the extension on the job description before continuing to the application form.");
    });
  }

  async function restartPageAnalysis() {
    state.busy = "Restarting page analysis";
    state.error = "";
    state.message = "";
    stopPageMonitoring();
    render();
    try {
      const saved = await chrome.storage.local.get([FLOW_STORAGE_KEY, "applicationByUrl", "applicationByJob"]);
      const currentCanonical = canonicalPageKey(location.href);
      const currentExternalId = externalIdForUrl(location.href, state.provider).toLowerCase();
      const flows = Object.fromEntries(
        Object.entries(saved[FLOW_STORAGE_KEY] || {}).filter(([, context]) => {
          if (!context || context.provider !== state.provider) return true;
          const urls = [
            context.page_url,
            context.job?.canonical_url,
            context.job?.source_url,
            context.job?.apply_url,
          ].filter(Boolean).map(canonicalPageKey);
          const externalId = weakText(context.job?.external_id || "").toLowerCase();
          return !urls.includes(currentCanonical) && !(currentExternalId && externalId === currentExternalId);
        }),
      );
      const applicationByUrl = { ...(saved.applicationByUrl || {}) };
      delete applicationByUrl[currentCanonical];
      const refreshedStorage = {
        ...saved,
        [FLOW_STORAGE_KEY]: flows,
        applicationByUrl,
      };
      await chrome.storage.local.set({ [FLOW_STORAGE_KEY]: flows, applicationByUrl });

      state.job = null;
      state.applicationId = null;
      state.applicationDetail = null;
      state.approvedArtifact = null;
      state.scan = null;
      state.plan = null;
      resetAutomaticAnswerState();
      state.preview = null;
      state.pendingResume = null;
      state.lastFillResult = null;
      state.contextWarning = "";
      state.scoreError = "";
      await refreshCurrentPage(refreshedStorage, { forceCapture: true });
      state.message = "Page analysis restarted from the current page.";
    } catch (error) {
      state.error = error.message || String(error);
    } finally {
      state.busy = "";
      if (state.signedIn) startPageMonitoring();
      render();
    }
  }

  async function rescanAndPlan() {
    const scan = scanApplicationForm(state.provider, state.recordSelections);
    lastFormFingerprint = formFingerprint(scan);
    if (!shouldScanApplicationForm(scan)) {
      state.scan = scan;
      state.plan = null;
      return;
    }
    await saveScanAndPlan(scan);
  }

  async function saveScanAndPlan(scan) {
    lastFormFingerprint = formFingerprint(scan);
    const previousSignature = state.scan?.form_signature || "";
    if (state.scan && previousSignature !== scan.form_signature) {
      state.lastFillResult = null;
      resetAutomaticAnswerState();
    }
    scan.application_id = state.applicationId;
    const savedScan = await apiRequest("/extension/forms/scan", {
      method: "POST",
      body: JSON.stringify(scan),
    });
    state.scan = savedScan;
    state.plan = await apiRequest(
      `/extension/forms/${savedScan.scan_id}/plan`,
    );
    if (state.lastFillResult) applyRuntimeFailureStatuses(state.lastFillResult);
    scheduleAutomaticAnswerGeneration();
  }

  function restoreFlowContext(flows) {
    const context = flowContextForCurrentPage(flows);
    if (!context) return false;
    state.job = context.job || null;
    state.applicationId = context.application_id || null;
    state.minimized = Boolean(context.minimized);
    state.panelTab = context.panel_tab === "tailor" ? "tailor" : "autofill";
    state.contextRestoreSource = context.restore_source || "saved context";
    state.contextWarning = "";
    return true;
  }

  function flowContextForCurrentPage(flows) {
    const exact = flows?.[state.flowKey];
    if (exact?.job) return annotateContext(exact, "workflow key");
    const canonical = canonicalPageKey(location.href);
    const urlExact = flows?.[`url:${canonical}`];
    if (urlExact?.job) return annotateContext(urlExact, "canonical URL");
    if (!isApplicationLikePage()) return null;
    const host = hostnameForUrl(location.href);
    const pageText = weakText(document.body?.innerText || "").toLowerCase();
    const candidates = Object.values(flows || {})
      .filter((context) => context?.job)
      .filter((context) => context.provider === state.provider)
      .filter((context) => {
        const contextHost = context.hostname ||
          hostnameForUrl(context.page_url) ||
          hostnameForUrl(context.job?.apply_url) ||
          hostnameForUrl(context.job?.source_url);
        if (contextHost !== host) return false;
        const company = weakText(context.job?.company || "").toLowerCase();
        const title = weakText(context.job?.title || "").toLowerCase();
        if (!company && !title) return true;
        return !pageText || pageText.includes(company) || pageText.includes(title);
      })
      .sort((left, right) => contextRank(right) - contextRank(left));
    return candidates[0] ? annotateContext(candidates[0], "same ATS host") : null;
  }

  function annotateContext(context, source) {
    return { ...context, restore_source: source };
  }

  function contextRank(context) {
    const updated = Date.parse(context.updated_at || "") || 0;
    const confidence = Number(context.job?.capture_confidence);
    const confidenceRank = Number.isFinite(confidence) ? confidence * 100000 : 50000;
    const descriptionRank = Math.min(5000, weakText(context.job?.description || "").length);
    return updated + confidenceRank + descriptionRank;
  }

  function syncPageContext(flows) {
    const provider = providerForUrl(location.href);
    const flowKey = workflowKeyForUrl(location.href, provider);
    if (provider === state.provider && flowKey === state.flowKey) return;
    state.provider = provider;
    state.flowKey = flowKey;
    state.job = null;
    state.applicationId = null;
    state.applicationDetail = null;
    state.scan = null;
    state.plan = null;
    resetAutomaticAnswerState();
    restoreFlowContext(flows);
  }

  async function persistFlowContext() {
    if (!state.flowKey || !state.job) return;
    const saved = await chrome.storage.local.get([FLOW_STORAGE_KEY, "applicationByUrl", "applicationByJob"]);
    const flows = { ...(saved[FLOW_STORAGE_KEY] || {}) };
    const context = {
      provider: state.provider,
      hostname: hostnameForUrl(location.href),
      page_url: location.href,
      job: state.job,
      application_id: state.applicationId,
      minimized: state.minimized,
      panel_tab: state.panelTab,
      updated_at: new Date().toISOString(),
    };
    flowStorageKeysForContext(context).forEach((key) => {
      flows[key] = context;
    });
    const applicationByUrl = { ...(saved.applicationByUrl || {}) };
    const applicationByJob = { ...(saved.applicationByJob || {}) };
    if (state.applicationId) applicationByUrl[canonicalPageKey(location.href)] = state.applicationId;
    if (state.applicationId && state.job?.source_url) {
      applicationByUrl[canonicalPageKey(state.job.source_url)] = state.applicationId;
    }
    if (state.applicationId && state.job?.apply_url) {
      applicationByUrl[canonicalPageKey(state.job.apply_url)] = state.applicationId;
    }
    if (state.applicationId && state.job?.job_id) {
      applicationByJob[applicationStorageKey(state.job.job_id)] = state.applicationId;
    }
    await chrome.storage.local.set({ [FLOW_STORAGE_KEY]: flows, applicationByUrl, applicationByJob });
  }

  function flowStorageKeysForContext(context) {
    const keys = new Set([state.flowKey]);
    const job = context.job || {};
    [
      context.page_url,
      job.canonical_url,
      job.source_url,
      job.apply_url,
    ].filter(Boolean).forEach((url) => {
      keys.add(`url:${canonicalPageKey(url)}`);
    });
    if (job.workflow_key) keys.add(job.workflow_key);
    if (job.job_id) keys.add(`job:${applicationStorageKey(job.job_id)}`);
    return Array.from(keys).filter(Boolean);
  }

  async function loadActiveProfile() {
    state.activeProfile = await apiRequest("/profile/active");
    state.profileId = state.activeProfile.profile_id || state.profileId || "default";
    state.profile = await apiRequest(`/profile/view?profile_id=${encodeURIComponent(state.profileId)}`);
  }

  async function loadProfileResume() {
    state.resumeInfo = await apiRequest("/profile/resume");
  }

  async function loadApprovedArtifact() {
    if (!state.applicationId) {
      state.approvedArtifact = null;
      return;
    }
    try {
      state.approvedArtifact = await apiRequest(
        `/applications/${encodeURIComponent(state.applicationId)}/artifacts/latest?type=tailored_resume&status=approved`,
      );
    } catch {
      state.approvedArtifact = null;
    }
  }

  async function loadApplicationDetail() {
    if (!state.applicationId) {
      state.applicationDetail = null;
      return;
    }
    try {
      state.applicationDetail = await apiRequest(
        `/applications/${encodeURIComponent(state.applicationId)}`,
      );
      if (state.applicationDetail?.job) {
        state.job = state.applicationDetail.job;
      }
    } catch {
      state.applicationDetail = null;
    }
  }

  async function refreshApplicationScore({ quiet = false } = {}) {
    if (!state.applicationId) return;
    state.scoreError = "";
    try {
      const score = await apiRequest(
        `/applications/${encodeURIComponent(state.applicationId)}/score`,
        {
          method: "POST",
          body: JSON.stringify({ profile_id: state.profileId }),
        },
      );
      state.applicationDetail = {
        ...(state.applicationDetail || {}),
        application: score.application,
        job: state.applicationDetail?.job || state.job,
      };
      if (!quiet) state.message = "Current resume score refreshed.";
    } catch (error) {
      state.scoreError = error.message || String(error);
      if (!quiet) state.error = state.scoreError;
    }
  }

  async function runAutofill() {
    if (isWorkdayMyExperiencePage() && state.profile) {
      await runWorkdayMyExperienceAutofill();
      return;
    }
    if (!state.plan) return;
    const run = beginAutofillRun(state.plan.actions || []);
    await withBusy("Filling reviewed fields", async () => {
      const result = await fillReviewedFields(state.plan.actions, run);
      state.lastFillResult = result;
      await rescanAndPlan();
      finishAutofillRun(run);
      state.message = run.cancelled
        ? `Autofill cancelled after ${run.completed} of ${run.total} fields. Review the completed fields before submitting.`
        : `Filled ${result.filled} reviewed fields. Review everything before submitting.`;
    });
    finishAutofillRun(run);
  }

  async function runWorkdayMyExperienceAutofill() {
    const run = beginAutofillRun(orderWorkdayFillActions(state.plan?.actions || []), "Preparing Workday records");
    await withBusy("Filling approved Workday experience", async () => {
      const records = await ensureWorkdayProfileRecords(run);
      if (run.cancelled) {
        state.lastFillResult = { filled: 0, skipped: 0, failed_values: [], field_outcomes: {}, added_records: records.added, record_failures: records.failures };
        finishAutofillRun(run);
        state.message = `Autofill cancelled while preparing Workday records. ApplyTeX did not click Save and Continue.`;
        return;
      }
      await rescanAndPlan();
      resetAutofillRunActions(run, orderWorkdayFillActions(state.plan?.actions || []));
      const result = await fillReviewedFields(orderWorkdayFillActions(state.plan?.actions || []), run);
      state.lastFillResult = { ...result, added_records: records.added, record_failures: records.failures };
      await rescanAndPlan();
      applyRuntimeFailureStatuses(result);
      const unavailableCount = (result.failed_values || [])
        .reduce((total, item) => total + (item.unavailable_count || 1), 0);
      const skillNote = unavailableCount
        ? ` ${unavailableCount} saved values were unavailable and are listed below.`
        : "";
      const recordNote = records.failures.length
        ? ` ${records.failures.length} record sections could not be created.`
        : "";
      finishAutofillRun(run);
      state.message = run.cancelled
        ? `Autofill cancelled after ${run.completed} of ${run.total} fields. Completed values remain on the page; ApplyTeX did not click Save and Continue.`
        : `Added ${records.added} records and filled ${result.filled} reviewed fields.${skillNote}${recordNote} Review the page; ApplyTeX did not click Save and Continue.`;
    });
    finishAutofillRun(run);
  }

  function actionableAutofillActions(actions) {
    return (actions || []).filter((action) => !["skip", "upload"].includes(action.action) && action.value !== null);
  }

  function beginAutofillRun(actions, currentLabel = "Starting autofill") {
    const total = actionableAutofillActions(actions).length;
    const run = { cancelled: false, completed: 0, total };
    activeAutofillRun = run;
    state.autofillProgress = {
      active: true,
      stopping: false,
      completed: 0,
      total,
      percent: 0,
      currentLabel,
    };
    render();
    return run;
  }

  function resetAutofillRunActions(run, actions) {
    if (!run || run.cancelled) return;
    run.completed = 0;
    run.total = actionableAutofillActions(actions).length;
    state.autofillProgress = {
      active: true,
      stopping: false,
      completed: 0,
      total: run.total,
      percent: 0,
      currentLabel: "Filling reviewed fields",
    };
    render();
  }

  function updateAutofillRun(run, action, completed = false) {
    if (!run || run !== activeAutofillRun) return;
    if (completed) run.completed = Math.min(run.completed + 1, run.total);
    const question = workdayQuestionForField(action?.field_id) ||
      state.scan?.questions?.find((candidate) => candidate.field_id === action?.field_id);
    state.autofillProgress = {
      active: true,
      stopping: run.cancelled,
      completed: run.completed,
      total: run.total,
      percent: run.total ? Math.round((run.completed / run.total) * 100) : 100,
      currentLabel: question?.label || action?.field_id || "Filling reviewed fields",
    };
    render();
  }

  function cancelAutofillRun() {
    if (!activeAutofillRun) return;
    activeAutofillRun.cancelled = true;
    state.autofillProgress = {
      ...(state.autofillProgress || {}),
      active: true,
      stopping: true,
    };
    render();
  }

  function finishAutofillRun(run) {
    if (activeAutofillRun === run) activeAutofillRun = null;
    state.autofillProgress = null;
    render();
  }

  function orderWorkdayFillActions(actions) {
    const phase = (action) => {
      const question = workdayQuestionForField(action.field_id);
      if (["fill", "check"].includes(action.action) && !question?.date_component) return 1;
      if (question?.date_component === "year") return 2;
      if (question?.date_component === "month") return 3;
      if (action.action === "select") return 4;
      if (action.action === "select_many") return 5;
      return 6;
    };
    const recordGroups = new Map();
    const unscoped = [];
    actions.forEach((action, index) => {
      const recordKey = workdayRecordKeyForField(action.field_id);
      const item = { action, index };
      if (!recordKey) {
        unscoped.push(item);
        return;
      }
      if (!recordGroups.has(recordKey)) recordGroups.set(recordKey, []);
      recordGroups.get(recordKey).push(item);
    });
    const ordered = [];
    for (const group of recordGroups.values()) {
      ordered.push(...group.sort((left, right) => phase(left.action) - phase(right.action) || left.index - right.index));
    }
    ordered.push(...unscoped.sort((left, right) => phase(left.action) - phase(right.action) || left.index - right.index));
    return ordered.map((item) => item.action);
  }

  function workdayQuestionForField(fieldId) {
    return (state.scan?.questions || []).find((question) => question.field_id === fieldId) || null;
  }

  function workdayRecordKeyForField(fieldId) {
    const question = workdayQuestionForField(fieldId);
    return question?.profile_record_kind && Number.isInteger(question.profile_record_index)
      ? `${question.profile_record_kind}:${question.profile_record_index}`
      : "";
  }

  function applyRuntimeFailureStatuses(result) {
    const normalizeLabel = (value) => weakText(value).replace(/\*+$/g, "").trim().toLowerCase();
    const rawFailedValues = result?.failed_values || [];
    const fieldsWithFailedValues = new Set(rawFailedValues.map((item) => item.field_id).filter(Boolean));
    const resolvedItems = (state.plan?.review_items || [])
      .filter((item) => item.answer_source === "already_on_page" && !fieldsWithFailedValues.has(item.field_id));
    const resolvedIds = new Set(resolvedItems.map((item) => item.field_id));
    const resolvedLabels = new Set(resolvedItems.map((item) => normalizeLabel(item.label)));
    const failures = Object.fromEntries(
      Object.entries(result?.field_outcomes || {})
        .filter(([fieldId]) => !resolvedIds.has(fieldId)),
    );
    const failedValues = rawFailedValues.filter((item) => (
      !resolvedIds.has(item.field_id) && !resolvedLabels.has(normalizeLabel(item.field))
    ));
    if (result === state.lastFillResult) {
      state.lastFillResult = { ...result, field_outcomes: failures, failed_values: failedValues };
    }
    if (!state.plan?.review_items) return;
    state.plan.review_items = state.plan.review_items.map((item) => ({
      ...item,
      failure_status: failures[item.field_id] || null,
    }));
  }

  async function uploadDefaultResume() {
    await withBusy(state.approvedArtifact ? "Uploading approved resume" : "Uploading saved resume", async () => {
      const prepared = await apiRequest(
        "/extension/resume/prepare",
        {
          method: "POST",
          body: JSON.stringify({
            job_description: state.job?.description || "",
            customize: false,
            application_id: state.applicationId,
            prefer_approved_artifact: true,
          }),
        },
      );
      const upload = uploadResumeFileToApplication(prepared);
      if (upload.error) throw new Error(upload.error);
      if (prepared.artifact_id) {
        await markArtifactStatus(prepared.artifact_id, "uploaded");
      }
      await loadApprovedArtifact();
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
    params.set("return", "extension");
    const jobId = state.job?.job_id;
    const url = jobId
      ? `http://localhost:3000/tailor/${encodeURIComponent(jobId)}?${params.toString()}`
      : `http://localhost:3000/jobs?${params.toString()}`;
    window.open(url, "_blank", "noopener,noreferrer");
    state.message = "Opened the guided resume customization flow in the web UI.";
    render();
  }

  async function createMissingAnswerTask(questionLabel) {
    if (!state.applicationId) return;
    try {
      await apiRequest(`/applications/${encodeURIComponent(state.applicationId)}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          title: `Add profile answer: ${questionLabel}`.slice(0, 240),
          category: "missing_answer",
          notes: "Captured from the ApplyTeX extension form review.",
        }),
      });
    } catch {
      // Profile still opens; task creation is a convenience, not a blocker.
    }
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
            application_id: state.applicationId,
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
      if (state.pendingResume.artifact_id) {
        await markArtifactStatus(state.pendingResume.artifact_id, "approved");
      }
      const upload = uploadResumeFileToApplication(state.pendingResume);
      if (upload.error) throw new Error(upload.error);
      if (state.pendingResume.artifact_id) {
        await markArtifactStatus(state.pendingResume.artifact_id, "uploaded");
      }
      await loadApprovedArtifact();
      await rescanAndPlan();
      state.message = `Uploaded ${state.pendingResume.filename}.`;
      state.pendingResume = null;
      state.customizing = false;
    });
  }

  async function markArtifactStatus(artifactId, status) {
    if (!state.applicationId || !artifactId) return null;
    return apiRequest(
      `/applications/${encodeURIComponent(state.applicationId)}/artifacts/${encodeURIComponent(artifactId)}/status`,
      {
        method: "POST",
        body: JSON.stringify({ status }),
      },
    );
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

  function startPageMonitoring() {
    if (!pageObserver && document.documentElement) {
      pageObserver = new MutationObserver((records) => {
        if (records.some(mutationAffectsApplicationPage)) {
          scheduleObservedRescan(500);
        }
      });
      pageObserver.observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
      document.addEventListener("input", handleObservedFieldChange, true);
      document.addEventListener("change", handleObservedFieldChange, true);
    }
    if (!pageMonitorInterval) {
      pageMonitorInterval = window.setInterval(() => scheduleObservedRescan(0), 1800);
    }
  }

  function stopPageMonitoring() {
    pageObserver?.disconnect();
    pageObserver = null;
    if (pageMonitorInterval) window.clearInterval(pageMonitorInterval);
    pageMonitorInterval = null;
    if (observedRescanTimer) window.clearTimeout(observedRescanTimer);
    observedRescanTimer = null;
    document.removeEventListener("input", handleObservedFieldChange, true);
    document.removeEventListener("change", handleObservedFieldChange, true);
  }

  function mutationAffectsApplicationPage(record) {
    const target = record.target?.nodeType === Node.ELEMENT_NODE
      ? record.target
      : record.target?.parentElement;
    if (target?.closest?.("#smartjobapply-panel") || target?.id === "smartjobapply-panel-style") {
      return false;
    }
    return Array.from(record.addedNodes || []).some((node) => {
      if (node.nodeType !== Node.ELEMENT_NODE) return true;
      return node.id !== "smartjobapply-panel" &&
        node.id !== "smartjobapply-panel-style" &&
        !node.closest?.("#smartjobapply-panel");
    }) || Array.from(record.removedNodes || []).some((node) => {
      return node.nodeType !== Node.ELEMENT_NODE || node.id !== "smartjobapply-panel";
    });
  }

  function handleObservedFieldChange(event) {
    if (event.target?.closest?.("#smartjobapply-panel")) return;
    scheduleObservedRescan(350);
  }

  function scheduleObservedRescan(delayMs) {
    if (!state.initialized || !state.backendReady || !state.job) return;
    if (observedRescanTimer) window.clearTimeout(observedRescanTimer);
    observedRescanTimer = window.setTimeout(() => {
      observedRescanTimer = null;
      void refreshObservedApplicationStep();
    }, delayMs);
  }

  async function refreshObservedApplicationStep() {
    if (observedRescanRunning) {
      scheduleObservedRescan(500);
      return;
    }
    if (state.busy || state.initializePromise) {
      scheduleObservedRescan(700);
      return;
    }
    const scan = scanApplicationForm(state.provider, state.recordSelections);
    const fingerprint = formFingerprint(scan);
    if (fingerprint === lastFormFingerprint) return;
    lastFormFingerprint = fingerprint;
    observedRescanRunning = true;
    try {
      if (!shouldScanApplicationForm(scan)) {
        state.scan = scan;
        state.plan = null;
        if (!isAuthenticationPage()) {
          state.message = "Application page detected. Waiting for the current step fields to load.";
        }
        render();
        return;
      }
      if (isLowConfidenceApplicationContext()) {
        state.scan = scan;
        state.plan = null;
        state.contextWarning = LOW_CONFIDENCE_CONTEXT_MESSAGE;
        state.error = LOW_CONFIDENCE_CONTEXT_MESSAGE;
        render();
        return;
      }
      await saveScanAndPlan(scan);
      state.message = `Detected ${scan.questions.length} fields on ${applicationStepLabel()}. Review before autofilling.`;
      state.error = "";
      render();
    } catch (error) {
      state.error = error.message || String(error);
      render();
    } finally {
      observedRescanRunning = false;
    }
  }

  function formFingerprint(scan) {
    return JSON.stringify({
      url: location.href,
      step: applicationStepLabel(),
      fields: (scan.questions || []).map((question) => [
        question.field_id,
        question.label,
        question.input_type,
        question.required,
        question.current_value_present,
        question.current_value,
        question.profile_record_index,
        question.options,
      ]),
    });
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
    if (state.needsSignIn || !state.signedIn) {
      root.innerHTML = `
        <div class="sja-head">
          <div>
            <div class="sja-brand">ApplyTeX ATS</div>
            ${state.backendReady ? "" : '<div class="sja-muted">Local API unavailable</div>'}
          </div>
          <div class="sja-head-actions">
            <button class="sja-icon-button" data-action="minimize" type="button" aria-label="Minimize ApplyTeX ATS">›</button>
            <button class="sja-icon-button" data-action="close" type="button" aria-label="Close ApplyTeX ATS">x</button>
          </div>
        </div>
        <section class="sja-section sja-signin">
          <h2>Sign in</h2>
          <p class="sja-note">${state.authRequired
            ? "Local API auth is enabled. Use the same username and password as the web app."
            : "Use the same username as the web app. No password required for local profiles."}</p>
          ${state.authRequired ? "" : `<p class="sja-note">${escapeHtml(profileHintText())}</p>`}
          <label class="sja-label" for="sja-signin-username">Username</label>
          <input id="sja-signin-username" data-signin-username type="text" autocomplete="username" value="${escapeHtml(state.signInUsername || "")}" placeholder="ashrith" />
          ${state.authRequired ? `
            <label class="sja-label" for="sja-signin-password">Password</label>
            <input id="sja-signin-password" data-signin-password type="password" autocomplete="current-password" value="${escapeHtml(state.signInPassword || "")}" placeholder="at least 8 characters" />
          ` : ""}
          ${!state.authRequired && usableProfiles().length ? `
            <div class="sja-profile-pick">
              ${usableProfiles().map((profile) => `
                <button class="sja-secondary-button" data-action="sign-in-profile" data-profile-id="${escapeHtml(profile.profile_id)}" type="button">
                  ${escapeHtml(profile.full_name || profile.profile_id)}
                  <span>@${escapeHtml(profile.profile_id)}</span>
                </button>
              `).join("")}
            </div>
          ` : ""}
          <div class="sja-actions">
            <button data-action="sign-in" type="button" ${state.backendReady ? "" : "disabled"}>Continue</button>
            <button class="sja-secondary-button" data-action="open-web-login" type="button">Open web app</button>
          </div>
        </section>
        ${state.busy ? `<div class="sja-status">${escapeHtml(state.busy)}...</div>` : ""}
        ${state.message ? `<div class="sja-status sja-success">${escapeHtml(state.message)}</div>` : ""}
        ${state.error ? `<div class="sja-status sja-error">${escapeHtml(state.error)}</div>` : ""}
      `;
      bindEvents(root);
      root.querySelector("[data-signin-username]")?.focus();
      return;
    }
    const progress = requiredProgress();
    const review = reviewSummary();
    const pageContext = applicationStepLabel();
    const resumeLabel = state.resumeInfo?.has_pdf
      ? state.resumeInfo.resume_pdf_filename || "PDF resume ready"
      : state.resumeInfo?.has_latex_source
        ? "LaTeX saved, PDF not rendered"
        : "No profile resume saved";
    const profileName = state.activeProfile?.full_name || state.profile?.full_name || state.profileId || "Profile";
    const profileHandle = state.profileId ? `@${state.profileId}` : "";
    root.innerHTML = `
      <div class="sja-head">
        <div>
          <div class="sja-brand">ApplyTeX ATS</div>
          ${state.backendReady ? "" : '<div class="sja-muted">Local API unavailable</div>'}
        </div>
        <div class="sja-head-actions">
          <button class="sja-icon-button" data-action="restart-page" type="button" aria-label="Restart page analysis" title="Restart page analysis">&#8635;</button>
          <button class="sja-icon-button" data-action="minimize" type="button" aria-label="Minimize ApplyTeX ATS">›</button>
          <button class="sja-icon-button" data-action="close" type="button" aria-label="Close ApplyTeX ATS">x</button>
        </div>
      </div>

      <div class="sja-account-bar">
        <span>Signed in</span>
        <details class="sja-account-menu">
          <summary aria-label="Open profile actions">
            <strong>${escapeHtml(profileName)}</strong>
            ${profileHandle ? `<span>${escapeHtml(profileHandle)}</span>` : ""}
            <span aria-hidden="true">⌄</span>
          </summary>
          <div class="sja-account-actions">
            <button data-action="switch-account" type="button">Switch profile</button>
            <button data-action="open-web-profile" type="button">Open web profile</button>
            <button data-action="sign-out" type="button">Log out</button>
          </div>
        </details>
      </div>

      ${renderJobSummary(pageContext)}

      <div class="sja-tabs" role="tablist" aria-label="ApplyTeX panel sections">
        <button class="${state.panelTab === "autofill" ? "active" : ""}" data-tab="autofill" type="button">Autofill</button>
        <button class="${state.panelTab === "tailor" ? "active" : ""}" data-tab="tailor" type="button">Tailor</button>
      </div>

      ${state.panelTab === "tailor"
        ? renderTailorTab(resumeLabel)
        : renderAutofillTab(progress, review)}

      ${state.busy && !state.autofillProgress?.active ? `<div class="sja-status">${escapeHtml(state.busy)}...</div>` : ""}
      ${state.message ? `<div class="sja-status sja-success">${escapeHtml(state.message)}</div>` : ""}
      ${state.error ? `<div class="sja-status sja-error">${escapeHtml(state.error)}</div>` : ""}
      ${renderDiagnostics()}
    `;
    bindEvents(root);
  }

  function renderJobSummary(pageContext) {
    const application = state.applicationDetail?.application || null;
    const score = currentResumeScore(application);
    const scoreLabel = formatWholeScore(score);
    const company = state.job?.company || "Captured company";
    const title = state.job?.title || jobTitleFromDocumentTitle(document.title) || "Current job";
    const logo = companyLogoText(company);
    const scoreUpdatedLabel = application?.score_updated_at
      ? `Score updated ${relativeTime(application.score_updated_at)}`
      : "Score details";
    const meaningfulStep = /^(application form|job description)$/i.test(pageContext)
      ? ""
      : pageContext;
    return `
      <section class="sja-job-summary" aria-label="Captured job">
        <div class="sja-job-main">
          <div class="sja-company-mark" aria-hidden="true">${escapeHtml(logo)}</div>
          <div class="sja-job-copy">
            <span class="sja-company-name">${escapeHtml(company)}</span>
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml([state.job?.location, state.provider].filter(Boolean).join(" / ") || "Captured from this page")}</span>
          </div>
          <button class="sja-score-ring" data-tab="tailor" type="button" aria-label="Open resume match details. ${escapeAttr(scoreUpdatedLabel)}" title="${escapeAttr(scoreUpdatedLabel)}">
            <strong>${escapeHtml(scoreLabel)}</strong>
            <span>match</span>
          </button>
        </div>
        ${meaningfulStep ? `<div class="sja-job-meta"><span class="sja-page-context">Step: ${escapeHtml(meaningfulStep)}</span></div>` : ""}
      </section>
    `;
  }

  function renderAutofillTab(progress, review) {
    const continueButton = findContinuePageButton();
    const continueReady = Boolean(continueButton && !continueButton.disabled && !state.autofillProgress?.active);
    const continueLabel = continueButtonLabel(continueButton);
    const workdayExperience = isWorkdayMyExperiencePage();
    const canAutofill = workdayExperience
      ? Boolean(state.profile)
      : Boolean(state.plan?.can_fill);
    const contextBlocksAutofill = Boolean(state.contextWarning && !workdayExperience);
    return `
      <section class="sja-section">
        <div class="sja-progress-head">
          <strong>Required ${progress.filled}/${progress.total}</strong>
          <span>${progress.percent}% filled</span>
        </div>
        <div class="sja-track"><div class="sja-bar" style="width:${progress.percent}%"></div></div>
        <div class="sja-review-summary">
          <span><strong>${review.completed}</strong> Filled</span>
          <span><strong>${review.planned}</strong> Ready</span>
          <span><strong>${review.needsReview}</strong> Blocked</span>
        </div>
        ${renderProfileRecordSelectors()}
        ${state.contextWarning ? `<div class="sja-status sja-warn">${escapeHtml(state.contextWarning)}</div>` : ""}
        ${state.autofillProgress?.active
          ? renderAutofillProgress()
          : `<button data-action="autofill" type="button" ${canAutofill && !contextBlocksAutofill ? "" : "disabled"}>${workdayExperience ? "Approve and fill My Experience" : "Autofill reviewed fields"}</button>`}
        ${state.plan?.unresolved_required?.length && !workdayExperience ? `<p class="sja-note">Unresolved required answers stay skipped (${state.plan.unresolved_required.length}). Ready actions: ${state.plan.ready_action_count || 0}.</p>` : ""}
        <p class="sja-safety-note">Final submission stays manual.</p>
        <div class="sja-field-list">${renderReviewChecklist()}</div>
        ${renderLastFillResult()}
        <div class="sja-continue-footer">
          <button class="sja-continue-button" data-action="continue-next-page" type="button" ${continueReady ? "" : "disabled"}>
            <span>${escapeHtml(continueLabel)}</span>
            <span aria-hidden="true">▶</span>
          </button>
        </div>
      </section>
    `;
  }

  function renderAutofillProgress() {
    const progress = state.autofillProgress || {};
    const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
    const status = progress.stopping ? "Stopping after the current field" : "Autofilling";
    return `
      <div class="sja-autofill-progress" role="status" aria-live="polite" aria-label="${escapeAttr(`${status}, ${percent}%`)}">
        <div class="sja-autofill-progress-head">
          <strong>${escapeHtml(status)}${progress.stopping ? "" : `<span class="sja-wave-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`}</strong>
          <span>${percent}%</span>
          <button class="sja-cancel-autofill" data-action="cancel-autofill" type="button" ${progress.stopping ? "disabled" : ""}>${progress.stopping ? "Stopping" : "Cancel"}</button>
        </div>
        <div class="sja-track" aria-hidden="true"><div class="sja-bar" style="width:${percent}%"></div></div>
        <div class="sja-autofill-current" title="${escapeAttr(progress.currentLabel || "")}">${escapeHtml(progress.currentLabel || "Preparing fields")}</div>
      </div>
    `;
  }

  function renderTailorTab(resumeLabel) {
    const application = state.applicationDetail?.application || null;
    const currentScore = application?.current_resume_score;
    const tailoredScore = application?.tailored_resume_score;
    const requiredMissing = application?.required_missing || [];
    const preferredMissing = application?.preferred_missing || [];
    const keywordMisses = application?.keyword_misses || [];
    return `
      <section class="sja-section">
        <div class="sja-row-between">
          <h2>Resume fit</h2>
          <button class="sja-secondary-button" data-action="score-refresh" type="button" ${state.applicationId ? "" : "disabled"}>Refresh score</button>
        </div>
        <div class="sja-score-grid">
          <span><strong>${formatScore(currentScore)}</strong> current</span>
          <span><strong>${formatScore(tailoredScore)}</strong> tailored</span>
        </div>
        ${state.scoreError ? `<div class="sja-status sja-warn">${escapeHtml(state.scoreError)}</div>` : ""}
        <div class="sja-subpanel">
          <strong>Missing evidence</strong>
          ${renderMissingGroup("Required", requiredMissing)}
          ${renderMissingGroup("Preferred", preferredMissing)}
          ${renderMissingGroup("Keywords", keywordMisses)}
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
          <button data-action="customize-start" type="button" ${state.job && state.resumeInfo?.has_latex_source ? "" : "disabled"}>Open guided tailoring</button>
          <button data-action="default-resume" type="button" ${state.resumeInfo?.has_pdf || state.approvedArtifact ? "" : "disabled"}>${state.approvedArtifact ? "Upload approved tailored PDF" : "Upload saved PDF"}</button>
        </div>
        ${state.approvedArtifact ? `
          <div class="sja-subpanel">
            <strong>Approved tailored resume</strong>
            <span>${escapeHtml(state.approvedArtifact.filename || "Tailored resume PDF")}</span>
            <span>Ready to upload from this application page.</span>
          </div>
        ` : ""}
        ${renderCustomization()}
      </section>
    `;
  }

  function renderMissingGroup(label, values) {
    const clean = (values || []).filter(Boolean);
    if (!clean.length) return `<span><b>${escapeHtml(label)}</b>: none</span>`;
    const preview = clean.slice(0, 5).join(", ");
    const extra = clean.length > 5 ? ` +${clean.length - 5} more` : "";
    return `<span><b>${escapeHtml(label)}</b>: ${escapeHtml(preview + extra)}</span>`;
  }

  function formatScore(score) {
    return Number.isFinite(score) ? Number(score).toFixed(1) : "-";
  }

  function formatWholeScore(score) {
    return Number.isFinite(score) ? `${Math.round(Number(score))}%` : "-";
  }

  function currentResumeScore(application) {
    if (Number.isFinite(application?.current_resume_score)) return Number(application.current_resume_score);
    if (Number.isFinite(application?.fit_score)) return Number(application.fit_score);
    if (Number.isFinite(state.preview?.baseline_score)) return Number(state.preview.baseline_score);
    return null;
  }

  function companyLogoText(company) {
    const cleaned = weakText(company || "").replace(/[^a-z0-9 ]/gi, "").trim();
    if (!cleaned) return "A";
    const words = cleaned.split(/\s+/).filter(Boolean);
    if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase();
    return cleaned.slice(0, 2).toUpperCase();
  }

  function relativeTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "recently";
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (Math.abs(seconds) < 60) return "just now";
    const minutes = Math.round(seconds / 60);
    if (Math.abs(minutes) < 60) return `${Math.abs(minutes)}m ago`;
    const hours = Math.round(minutes / 60);
    if (Math.abs(hours) < 24) return `${Math.abs(hours)}h ago`;
    const days = Math.round(hours / 24);
    return `${Math.abs(days)}d ago`;
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
    return renderReviewChecklist();
  }

  function renderReviewChecklist() {
    if (isWorkdayMyExperiencePage()) return renderWorkdayExperienceChecklist();
    const items = state.plan?.review_items || [];
    if (!items.length) return `<div class="sja-muted">Scanning application fields...</div>`;
    return `<div class="sja-question-group sja-question-ledger">${items.map(renderReviewQuestion).join("")}</div>`;
  }

  function renderWorkdayExperienceChecklist() {
    const items = state.plan?.review_items || [];
    const workFilled = workdayRecordGroups("work_experience").some((group) => {
      const identity = workdayRecordIdentity(group, "work_experience");
      return Boolean(weakText(identity.company) || weakText(identity.job_title));
    });
    const educationFilled = workdayRecordGroups("education").some((group) => {
      const identity = workdayRecordIdentity(group, "education");
      return Boolean(weakText(identity.school));
    });
    const resumeItem = items.find((item) => /resume|cv/i.test(item.label || ""));
    const resumeUploaded = Boolean(
      (resumeItem && (resumeItem.answer_source === "already_on_page" || resumeItem.change_kind === "keep" || resumeItem.status === "ready"))
      || queryFirstFromPage("[data-automation-id='file-upload-item'], [data-automation-id*='uploadedFile']")
      || queryAllFromPage("div, span, p").some((node) => {
        const text = weakText(node.textContent);
        return text.length < 80 && /successfully uploaded/i.test(text);
      }),
    );
    const requiredSummary = [
      {
        field_id: "workday-employment",
        label: "Employment",
        required: true,
        status: workFilled ? "ready" : "skipped",
        answer_source: workFilled ? "already_on_page" : "none",
        change_kind: workFilled ? "keep" : "unresolved",
      },
      {
        field_id: "workday-education",
        label: "Education",
        required: true,
        status: educationFilled ? "ready" : "skipped",
        answer_source: educationFilled ? "already_on_page" : "none",
        change_kind: educationFilled ? "keep" : "unresolved",
      },
      {
        field_id: "workday-resume",
        label: "Resume/CV",
        required: true,
        status: resumeUploaded ? "ready" : "skipped",
        answer_source: resumeUploaded ? "already_on_page" : "none",
        change_kind: resumeUploaded ? "keep" : "unresolved",
      },
    ];
    const optional = items.filter((item) => {
      if (item.required) return false;
      const label = weakText(item.label);
      if (/drop files|select files|unlabelled field/i.test(label)) return false;
      if (/resume|cv/i.test(label)) return false;
      return true;
    });
    return [
      renderReviewGroup("Required", requiredSummary),
      renderReviewGroup("Optional", optional),
    ].filter(Boolean).join("");
  }

  function renderReviewGroup(label, items) {
    if (!items.length) return "";
    return `
      <div class="sja-question-group">
        <h3>${escapeHtml(label)}</h3>
        ${items.map(renderReviewQuestion).join("")}
      </div>
    `;
  }

  function renderReviewQuestion(item) {
    const stateInfo = reviewItemState(item);
    const label = reviewItemDisplayLabel(item);
    const detail = reviewItemDetail(item, stateInfo);
    const generatable = isGeneratableQuestion(item);
    const statusRecord = state.automaticAnswerStatus[item.field_id];
    const automaticStatus = statusRecord?.label === item.label ? statusRecord.state : "";
    const generated = state.generatedAnswerFields[item.field_id] === item.label;
    const showManualFallback = stateInfo.className === "blocked" && (!generatable || automaticStatus === "failed");
    return `
      <div class="sja-question-row ${stateInfo.className}">
        <span class="sja-question-mark" aria-hidden="true">${escapeHtml(stateInfo.symbol)}</span>
        <div>
          <strong>${escapeHtml(label)} <small class="sja-question-requirement">${item.required ? "required" : "optional"}</small></strong>
          ${detail ? `<span>${escapeHtml(detail)}</span>` : ""}
          ${showManualFallback && item.required ? `<button class="sja-inline-action" data-action="open-profile" data-question="${escapeAttr(label)}" type="button">Add answer to Profile</button>` : ""}
          ${showManualFallback ? `<button class="sja-inline-action" data-action="plan-override" data-field-id="${escapeAttr(item.field_id)}" data-question="${escapeAttr(label)}" type="button">Answer once for this form</button>` : ""}
          ${generated ? `<button class="sja-inline-action" data-action="edit-generated-answer" data-field-id="${escapeAttr(item.field_id)}" type="button">Edit draft</button>` : ""}
        </div>
      </div>
    `;
  }

  function isGeneratableQuestion(item) {
    const question = (state.scan?.questions || []).find((candidate) => candidate.field_id === item.field_id);
    return Boolean(item.draft_eligible === true && question && ["textarea", "contenteditable"].includes(question.input_type));
  }

  function answerWordCount(value) {
    return (String(value || "").match(/\b[\w'-]+\b/g) || []).length;
  }

  function reviewItemState(item) {
    if (item.failure_status) return { className: "failed", symbol: "!" };
    if (item.answer_source === "already_on_page") return { className: "filled", symbol: "✓" };
    if (item.status === "ready") return { className: "ready", symbol: "→" };
    if (item.required) return { className: "blocked", symbol: "−" };
    return { className: "skipped", symbol: "−" };
  }

  function reviewItemDetail(item, stateInfo) {
    if (item.failure_status) return `Review: ${item.failure_status.replaceAll("_", " ")}`;
    const statusRecord = state.automaticAnswerStatus[item.field_id];
    const automaticStatus = statusRecord?.label === item.label ? statusRecord : null;
    if (item.answer_source === "already_on_page") {
      const prefix = state.generatedAnswerFields[item.field_id] === item.label ? "AI draft filled" : "Filled";
      return item.current_value_preview && item.current_value_preview !== "True"
        ? `${prefix} · ${item.current_value_preview}`
        : prefix;
    }
    if (item.change_kind === "replace") {
      return `Will replace ${item.current_value_preview || "current value"} with ${item.planned_value_preview || "reviewed value"}`;
    }
    if (stateInfo.className === "ready") {
      return item.planned_value_preview ? `Ready · ${item.planned_value_preview}` : "Ready to fill";
    }
    if (item.answer_source === "eeo_opt_in") return "Enable EEO autofill in Profile";
    if (item.required && isGeneratableQuestion(item)) {
      if (automaticStatus?.state === "generating") return "Generating AI draft...";
      if (automaticStatus?.state === "failed") return `Automatic draft failed: ${automaticStatus.error || "retry page analysis"}`;
      return "Preparing AI draft...";
    }
    if (item.required) return item.resolution_reason || "Missing answer";
    return "Not filled";
  }

  function reviewItemDisplayLabel(item) {
    const rawLabel = weakText(item.label || "Unlabelled field").replace(/\*+$/g, "").trim();
    if (!isWorkdayMyExperiencePage()) return rawLabel;
    const question = workdayQuestionForField(item.field_id);
    if (!question?.profile_record_kind || !Number.isInteger(question.profile_record_index)) return rawLabel;
    const recordNumber = question.profile_record_index + 1;
    if (question.profile_record_kind === "education") {
      return `Education ${recordNumber} — ${rawLabel.replace(/^Education\s+/i, "")}`;
    }
    if (question.profile_record_kind === "work_experience") {
      return `Work Experience ${recordNumber} — ${rawLabel.replace(/^Experience\s+/i, "")}`;
    }
    return rawLabel;
  }

  function renderLastFillResult() {
    const result = state.lastFillResult;
    if (!result) return "";
    const failures = [
      ...(result.record_failures || []),
      ...(result.failed_values || []).map((item) => (
        `${item.field}: ${item.value} · Failed: ${weakText(item.status).replaceAll("_", " ")}`
      )),
    ];
    if (!failures.length) return `<div class="sja-status sja-success">The approved fields were filled. Save and Continue was not clicked.</div>`;
    return `
      <div class="sja-status sja-warn">
        <strong>Some values need review</strong>
        ${failures.map((failure) => `<span>${escapeHtml(failure)}</span>`).join("")}
      </div>
    `;
  }

  function isWorkdayMyExperiencePage() {
    return state.provider === "workday" && Boolean(
      queryFirstFromPage("[data-automation-id='applyFlowMyExpPage']") ||
      queryAllFromPage("h1, h2, h3").some((heading) => weakText(heading.textContent) === "My Experience"),
    );
  }

  function workdayRecordGroups(kind) {
    const pattern = kind === "education"
      ? /^education\s+\d+$/i
      : /^(?:work\s+)?experience\s+\d+$/i;
    return queryAllFromPage("[role='group']")
      .filter((group) => !group.closest("#smartjobapply-panel"))
      .filter((group) => Array.from(group.querySelectorAll("h1, h2, h3, h4, h5, h6, legend"))
        .some((heading) => heading.closest("[role='group']") === group && pattern.test(weakText(heading.textContent))))
      .sort((left, right) => {
        if (left.ownerDocument !== right.ownerDocument) return 0;
        const position = left.compareDocumentPosition(right);
        return position & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : position & Node.DOCUMENT_POSITION_PRECEDING ? 1 : 0;
      });
  }

  function workdayRecordIdentity(group, kind) {
    const inputs = Array.from(group.querySelectorAll("input, textarea"));
    const valueFor = (patterns) => {
      const control = inputs.find((input) => {
        const key = `${input.id || ""} ${input.name || ""} ${input.getAttribute("data-automation-id") || ""} ${explicitControlLabel(input)}`.toLowerCase();
        return patterns.some((pattern) => key.includes(pattern));
      });
      return weakText(
        control && isCustomSelectInput(control)
          ? selectedMultiValues(control)[0] || selectedSingleValue(control) || control.value
          : control?.value,
      );
    };
    return kind === "education"
      ? { school: valueFor(["school", "university", "institution"]) }
      : { company: valueFor(["company", "employer"]), title: valueFor(["jobtitle", "job title", "position title"]) };
  }

  function normalizedIdentity(value) {
    if (workdayParts.normalizedIdentity) return workdayParts.normalizedIdentity(value);
    return weakText(value).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  function normalizedEducationSchool(value) {
    if (workdayParts.normalizedEducationSchool) return workdayParts.normalizedEducationSchool(value);
    const normalized = normalizedIdentity(value);
    if (/\bamrita\b/.test(normalized) && /\b(?:vishwa vidyapeetham|school of engineering)\b/.test(normalized)) {
      return "amrita vishwa vidyapeetham";
    }
    return normalized;
  }

  function workdayIdentityMatches(identity, record, kind) {
    if (workdayParts.workdayIdentityMatches) {
      return workdayParts.workdayIdentityMatches(identity, record, kind);
    }
    if (kind === "education") {
      return Boolean(identity.school) && normalizedEducationSchool(identity.school) === normalizedEducationSchool(record.school);
    }
    const companyMatches = identity.company && normalizedIdentity(identity.company) === normalizedIdentity(record.company);
    const titleMatches = identity.title && normalizedIdentity(identity.title) === normalizedIdentity(record.job_title);
    return Boolean(companyMatches && (!identity.title || titleMatches));
  }

  function workdayIdentityIsBlank(identity, kind) {
    if (workdayParts.workdayIdentityIsBlank) {
      return workdayParts.workdayIdentityIsBlank(identity, kind);
    }
    return kind === "education"
      ? !identity.school
      : !identity.company && !identity.title;
  }

  function workdayIdentityCompatible(identity, record, kind) {
    if (workdayParts.workdayIdentityCompatible) {
      return workdayParts.workdayIdentityCompatible(identity, record, kind);
    }
    if (kind === "education") {
      return !identity.school || normalizedEducationSchool(identity.school) === normalizedEducationSchool(record.school);
    }
    const companyCompatible = !identity.company || normalizedIdentity(identity.company) === normalizedIdentity(record.company);
    const titleCompatible = !identity.title || normalizedIdentity(identity.title) === normalizedIdentity(record.job_title);
    return companyCompatible && titleCompatible;
  }

  function workdayProfileRecords(kind) {
    if (kind === "education") {
      return state.profile?.educations?.length
        ? state.profile.educations
        : state.profile?.education?.school ? [state.profile.education] : [];
    }
    return state.profile?.work_experiences || [];
  }

  function assignWorkdayRecordIndexes() {
    if (!isWorkdayMyExperiencePage() || !state.profile) return;
    const groupsByKind = {
      education: workdayRecordGroups("education"),
      work_experience: workdayRecordGroups("work_experience"),
    };
    for (const kind of ["work_experience", "education"]) {
      const groups = groupsByKind[kind];
      const records = workdayProfileRecords(kind);
      const used = new Set();
      const pendingIndexes = new Map(groups.map((group) => [
        group,
        group.dataset.smartjobapplyPendingRecord === "true"
          ? Number.parseInt(group.dataset.smartjobapplyProfileIndex || "", 10)
          : null,
      ]));
      groups.forEach((group) => delete group.dataset.smartjobapplyProfileIndex);
      groups.forEach((group) => {
        const identity = workdayRecordIdentity(group, kind);
        const matched = records.findIndex((record, index) => !used.has(index) && workdayIdentityMatches(identity, record, kind));
        if (matched >= 0) {
          group.dataset.smartjobapplyProfileIndex = String(matched);
          used.add(matched);
        }
      });
      groups.forEach((group) => {
        if (group.dataset.smartjobapplyProfileIndex !== undefined) return;
        const pendingIndex = pendingIndexes.get(group);
        if (!Number.isInteger(pendingIndex) || pendingIndex < 0 || pendingIndex >= records.length || used.has(pendingIndex)) return;
        if (!workdayIdentityCompatible(workdayRecordIdentity(group, kind), records[pendingIndex], kind)) return;
        group.dataset.smartjobapplyProfileIndex = String(pendingIndex);
        used.add(pendingIndex);
      });
    }
  }

  function workdayAddButton(kind) {
    const wanted = kind === "education" ? "Education" : "Experience";
    return queryAllFromPage("button[data-automation-id='add-button'], button")
      .filter((button) => ["Add", "Add Another"].includes(weakText(button.textContent)))
      .find((button) => profileSectionContext(button) === wanted) || null;
  }

  async function ensureWorkdayProfileRecords(run = null) {
    const failures = [];
    let added = 0;
    assignWorkdayRecordIndexes();
    for (const kind of ["work_experience", "education"]) {
      if (run?.cancelled) break;
      const records = workdayProfileRecords(kind);
      const assigned = new Set(
        workdayRecordGroups(kind)
          .map((group) => Number.parseInt(group.dataset.smartjobapplyProfileIndex || "", 10))
          .filter((index) => Number.isInteger(index) && index >= 0),
      );
      const availableBlankGroups = workdayRecordGroups(kind)
        .filter((group) => group.dataset.smartjobapplyProfileIndex === undefined)
        .filter((group) => workdayIdentityIsBlank(workdayRecordIdentity(group, kind), kind));
      for (const profileIndex of records.map((_, index) => index).filter((index) => !assigned.has(index))) {
        if (run?.cancelled) break;
        let group = availableBlankGroups.shift() || null;
        if (!group) {
          const beforeGroups = workdayRecordGroups(kind);
          const addButton = workdayAddButton(kind);
          if (!addButton) {
            failures.push(`${kind} ${profileIndex + 1}: Add button was not found`);
            continue;
          }
          addButton.click();
          const deadline = Date.now() + 5000;
          while (Date.now() < deadline && workdayRecordGroups(kind).length <= beforeGroups.length) {
            if (run?.cancelled) break;
            await new Promise((resolve) => setTimeout(resolve, 80));
          }
          if (run?.cancelled) break;
          group = workdayRecordGroups(kind).find((candidate) => !beforeGroups.includes(candidate)) || null;
          if (!group) {
            failures.push(`${kind} ${profileIndex + 1}: Workday did not create another record`);
            continue;
          }
          added += 1;
        }
        group.dataset.smartjobapplyPendingRecord = "true";
        group.dataset.smartjobapplyProfileIndex = String(profileIndex);
        assigned.add(profileIndex);
      }
    }
    assignWorkdayRecordIndexes();
    return { added, failures };
  }

  function renderWorkdayMyExperiencePlan() {
    if (!isWorkdayMyExperiencePage() || !state.profile) return "";
    assignWorkdayRecordIndexes();
    const workRecords = state.profile.work_experiences || [];
    const educationRecords = state.profile.educations?.length
      ? state.profile.educations
      : state.profile.education?.school ? [state.profile.education] : [];
    const assignedIndexes = (kind) => new Set(
      workdayRecordGroups(kind)
        .map((group) => Number.parseInt(group.dataset.smartjobapplyProfileIndex || "", 10))
        .filter((index) => Number.isInteger(index) && index >= 0),
    );
    const assignedWork = assignedIndexes("work_experience");
    const assignedEducation = assignedIndexes("education");
    const recordRows = [
      ...workRecords.map((record, index) => ({
        kind: assignedWork.has(index) ? "keep" : "add record",
        label: [record.company, record.job_title].filter(Boolean).join(" — "),
      })),
      ...educationRecords.map((record, index) => ({
        kind: assignedEducation.has(index) ? "keep" : "add record",
        label: record.school || record.degree,
      })),
    ];
    return `
      <div class="sja-subpanel sja-workday-plan">
        <strong>Workday My Experience plan</strong>
        <span>One approved run will create missing records, fill reviewed values, and stop before Save and Continue.</span>
        ${recordRows.map((row) => `<span><b>${escapeHtml(row.kind)}</b> · ${escapeHtml(row.label)}</span>`).join("")}
        <span><b>skills</b> · ${(state.profile.skills || []).length} saved skills, added one by one</span>
        <span><b>skip</b> · Certifications, Languages, and Websites</span>
      </div>
    `;
  }

  function renderProfileRecordSelectors() {
    if (isWorkdayMyExperiencePage()) return renderWorkdayMyExperiencePlan();
    const questions = state.scan?.questions || [];
    const kinds = new Set(questions.map((question) => question.profile_record_kind).filter(Boolean));
    const groups = [
      {
        kind: "education",
        label: "Education record",
        records: state.profile?.educations?.length
          ? state.profile.educations
          : state.profile?.education?.school
            ? [state.profile.education]
            : [],
        describe: (record, index) => record.school || record.degree || `Education ${index + 1}`,
      },
      {
        kind: "work_experience",
        label: "Work record",
        records: state.profile?.work_experiences || [],
        describe: (record, index) => [record.company, record.job_title].filter(Boolean).join(" — ") || `Work ${index + 1}`,
      },
    ].filter((group) => kinds.has(group.kind) && group.records.length > 1);
    if (!groups.length) return "";
    return `
      <div class="sja-subpanel">
        <strong>Repeated profile section</strong>
        <span>Choose the saved record for the editor currently open on the employer page.</span>
        ${groups.map((group) => `
          <label class="sja-record-picker">
            <span>${escapeHtml(group.label)}</span>
            <select data-record-kind="${escapeAttr(group.kind)}">
              <option value="">Automatic page order</option>
              ${group.records.map((record, index) => `
                <option value="${index}" ${state.recordSelections[group.kind] === index ? "selected" : ""}>${escapeHtml(group.describe(record, index))}</option>
              `).join("")}
            </select>
          </label>
        `).join("")}
        ${state.provider === "workday" ? "<span>Workday: open Add or Edit, select the matching record here, autofill, review, then save it in Workday.</span>" : ""}
      </div>
    `;
  }

  function bindEvents(root) {
    root.querySelector("[data-action='close']")?.addEventListener("pointerup", () => {
      notifyBackground({ type: "SMARTJOBAPPLY_PANEL_STATE", open: false });
      stopPageMonitoring();
      root.remove();
    });
    root.querySelector("[data-action='minimize']")?.addEventListener("pointerup", () => {
      state.minimized = true;
      void persistFlowContext();
      render();
    });
    root.querySelector("[data-action='expand']")?.addEventListener("pointerup", () => {
      state.minimized = false;
      void persistFlowContext();
      render();
    });
    root.querySelector("[data-action='sign-in']")?.addEventListener("pointerup", () => {
      void withBusy("Signing in", async () => {
        const input = root.querySelector("[data-signin-username]");
        const passwordInput = root.querySelector("[data-signin-password]");
        const rawValue = input?.value || "";
        const username = normalizeProfileId(rawValue || state.signInUsername || "");
        state.signInUsername = username;
        state.signInPassword = passwordInput?.value || "";
        await signInWithProfileId(username, {
          persist: true,
          password: state.signInPassword,
        });
        state.message = `Signed in as ${state.profileId}.`;
        state.error = "";
        await refreshCurrentPage();
      });
    });
    root.querySelectorAll("[data-action='sign-in-profile']").forEach((button) => {
      button.addEventListener("pointerup", () => {
        void withBusy("Signing in", async () => {
          await signInWithProfileId(button.dataset.profileId || "", { persist: true });
          state.message = `Signed in as ${state.profileId}.`;
          state.error = "";
          await refreshCurrentPage();
        });
      });
    });
    root.querySelector("[data-signin-username]")?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (state.authRequired) {
        root.querySelector("[data-signin-password]")?.focus();
        return;
      }
      root.querySelector("[data-action='sign-in']")?.dispatchEvent(new PointerEvent("pointerup"));
    });
    root.querySelector("[data-signin-password]")?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      root.querySelector("[data-action='sign-in']")?.dispatchEvent(new PointerEvent("pointerup"));
    });
    root.querySelector("[data-signin-username]")?.addEventListener("input", (event) => {
      state.signInUsername = event.target.value || "";
    });
    root.querySelector("[data-signin-password]")?.addEventListener("input", (event) => {
      state.signInPassword = event.target.value || "";
    });
    root.querySelector("[data-action='switch-account']")?.addEventListener("pointerup", () => {
      void switchAccount();
    });
    root.querySelector("[data-action='sign-out']")?.addEventListener("pointerup", () => {
      void signOutExtension();
    });
    root.querySelector("[data-action='open-web-login']")?.addEventListener("pointerup", () => {
      window.open(`${WEB_APP_BASE}/login`, "_blank", "noopener,noreferrer");
    });
    root.querySelector("[data-action='open-web-profile']")?.addEventListener("pointerup", () => {
      window.open(`${WEB_APP_BASE}/profile`, "_blank", "noopener,noreferrer");
    });
    root.querySelector("[data-action='restart-page']")?.addEventListener("pointerup", () => {
      void restartPageAnalysis();
    });
    root.querySelector("[data-action='autofill']")?.addEventListener("pointerup", runAutofill);
    root.querySelector("[data-action='cancel-autofill']")?.addEventListener("pointerup", cancelAutofillRun);
    root.querySelector("[data-action='continue-next-page']")?.addEventListener("pointerup", () => {
      void withBusy("Continuing to next page", continueToNextPage);
    });
    root.querySelector("[data-action='default-resume']")?.addEventListener("pointerup", uploadDefaultResume);
    root.querySelector("[data-action='customize-start']")?.addEventListener("pointerup", openWebCustomization);
    root.querySelectorAll("[data-action='open-profile']").forEach((button) => {
      button.addEventListener("pointerup", () => {
        void createMissingAnswerTask(button.dataset.question || "Application question");
        window.open("http://localhost:3000/profile#questions", "_blank", "noopener,noreferrer");
      });
    });
    root.querySelectorAll("[data-action='plan-override']").forEach((button) => {
      button.addEventListener("pointerup", () => {
        void withBusy("Saving one-off answer", async () => {
          const fieldId = button.dataset.fieldId;
          if (!fieldId || !state.scan?.scan_id) return;
          const value = window.prompt(`Answer once for: ${button.dataset.question || fieldId}`, "");
          if (!value || !value.trim()) return;
          const plan = await apiRequest(`/extension/forms/${state.scan.scan_id}/plan`, {
            method: "POST",
            body: JSON.stringify({
              overrides: { [fieldId]: value.trim() },
              profile_id: state.profileId,
            }),
          });
          state.plan = plan;
          state.message = "One-off answer saved for this form plan.";
          render();
        });
      });
    });
    root.querySelectorAll("[data-action='edit-generated-answer']").forEach((button) => {
      button.addEventListener("click", () => {
        focusGeneratedAnswerField(button.dataset.fieldId || "");
      });
    });
    root.querySelector("[data-action='customize-generate']")?.addEventListener("pointerup", generateCustomizedResume);
    root.querySelector("[data-action='approve-resume']")?.addEventListener("pointerup", approvePendingResume);
    root.querySelector("[data-action='score-refresh']")?.addEventListener("pointerup", () => {
      void withBusy("Scoring current resume", async () => {
        await refreshApplicationScore();
      });
    });
    root.querySelectorAll("[data-tab]").forEach((button) => {
      button.addEventListener("pointerup", () => {
        state.panelTab = button.dataset.tab === "tailor" ? "tailor" : "autofill";
        void persistFlowContext();
        render();
      });
    });
    root.querySelectorAll("[data-record-kind]").forEach((select) => {
      select.addEventListener("change", () => {
        const kind = select.dataset.recordKind;
        if (!kind) return;
        if (select.value === "") delete state.recordSelections[kind];
        else state.recordSelections[kind] = Number.parseInt(select.value, 10);
        void withBusy("Updating profile record", rescanAndPlan);
      });
    });
  }

  function notifyBackground(message) {
    try {
      const pending = chrome.runtime.sendMessage?.(message);
      pending?.catch?.(() => {});
    } catch {
      // The extension can still operate when the service worker is restarting.
    }
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
    const filled = required.filter((item) => item.answer_source === "already_on_page");
    const total = required.length;
    return {
      filled: filled.length,
      total,
      percent: total ? Math.round((filled.length / total) * 100) : 0,
    };
  }

  function reviewSummary() {
    const items = state.plan?.review_items || [];
    return {
      completed: items.filter((item) => item.answer_source === "already_on_page").length,
      planned: items.filter((item) => item.status === "ready" && item.answer_source !== "already_on_page").length,
      needsReview: items.filter((item) => item.required && item.status !== "ready").length,
    };
  }

  function bridgeStatuses() {
    return [
      { label: state.backendReady ? "connected" : "localhost unavailable", ready: state.backendReady },
      { label: state.job ? "job captured" : "job needed", ready: Boolean(state.job) },
      { label: state.applicationId ? "application linked" : "application pending", ready: Boolean(state.applicationId) },
      { label: state.approvedArtifact ? "tailored resume approved" : "tailor in web", ready: Boolean(state.approvedArtifact) },
    ];
  }

  function renderDiagnostics() {
    const jobWarnings = state.job?.warnings || [];
    const fields = state.scan?.questions?.length || 0;
    const confidence = captureConfidenceLabel(state.job?.capture_confidence);
    const bridge = bridgeStatuses();
    const scoreUpdatedAt = state.applicationDetail?.application?.score_updated_at;
    return `
      <details class="sja-diagnostics">
        <summary>Diagnostics</summary>
        <div>
          <span class="sja-diagnostic-bridge"><b>Connections</b><em>${bridge.map((item) => `<i class="${item.ready ? "ready" : "pending"}">${escapeHtml(item.label)}</i>`).join("")}</em></span>
          <span><b>Provider</b>${escapeHtml(state.provider || "unknown")}</span>
          <span><b>Depth</b>${escapeHtml(globalThis.ApplyTexProviders?.depthFor?.(state.provider) || "experimental")}</span>
          <span><b>Restore</b>${escapeHtml(state.contextRestoreSource || "current page")}</span>
          <span><b>Workflow</b>${escapeHtml(state.job?.workflow_key || state.flowKey || "none")}</span>
          <span><b>Capture</b>${escapeHtml(confidence)}</span>
          <span><b>Step</b>${escapeHtml(applicationStepLabel())}</span>
          <span><b>Score</b>${scoreUpdatedAt ? `Updated ${escapeHtml(relativeTime(scoreUpdatedAt))}` : "Not scored"}</span>
          <span><b>Fields</b>${fields}</span>
          ${jobWarnings.length ? `<span><b>Warnings</b>${escapeHtml(jobWarnings.slice(0, 2).join(" | "))}</span>` : ""}
        </div>
      </details>
    `;
  }

  function captureConfidenceLabel(value) {
    const confidence = Number(value);
    if (!Number.isFinite(confidence)) return "unscored";
    if (confidence >= 0.8) return "high confidence";
    if (confidence >= 0.55) return "medium confidence";
    return "low confidence";
  }

  function applicationStepLabel() {
    if (isAuthenticationPage()) return "Sign in or create account";
    const activeStep = document.querySelector(
      "[data-automation-id='progressBarActiveStep'], [aria-current='step']",
    );
    const activeText = weakText(activeStep?.textContent);
    if (activeText) {
      return activeText.replace(/^current step\s*\d+\s*of\s*\d+/i, "").trim() || activeText;
    }
    const path = location.pathname.toLowerCase().replace(/[^a-z_]+/g, " ");
    if (/\b(apply|application|job_app)\b/.test(path)) return "Application form";
    return "Job description";
  }

  function continueButtonLabel(button = findContinuePageButton()) {
    const text = weakText(button?.textContent || button?.getAttribute?.("aria-label"));
    if (/save and continue/i.test(text)) return "Continue to the next page";
    if (/continue/i.test(text) || /next/i.test(text)) return "Continue to the next page";
    return "Continue to the next page";
  }

  function isFinalSubmitButton(element) {
    const text = weakText(`${element?.getAttribute?.("aria-label") || ""} ${element?.textContent || ""}`).toLowerCase();
    const automationId = weakText(element?.getAttribute?.("data-automation-id")).toLowerCase();
    if (/save and continue|continue to|next page|next step/.test(text)) return false;
    if (/bottom-navigation-next|pagefooternext|nextbutton|continuebutton/.test(automationId)) return false;
    return /\b(submit application|submit|send application|apply now|complete application)\b/.test(text)
      || /submit|apply$/.test(automationId);
  }

  function findContinuePageButton() {
    const preferredSelectors = [
      "[data-automation-id='bottom-navigation-next-button']",
      "[data-automation-id='pageFooterNextButton']",
      "[data-automation-id='bottom-navigation-nextButton']",
      "button[type='submit']",
    ];
    for (const selector of preferredSelectors) {
      const match = queryAllFromPage(selector)
        .filter((element) => !element.closest("#smartjobapply-panel"))
        .find((element) => {
          if (isFinalSubmitButton(element)) return false;
          const text = weakText(`${element.getAttribute("aria-label") || ""} ${element.textContent || ""}`);
          const automationId = weakText(element.getAttribute("data-automation-id"));
          return /save and continue|\bcontinue\b|\bnext\b/i.test(text)
            || /next|continue/i.test(automationId);
        });
      if (match) return match;
    }
    const byAutomation = queryAllFromPage("button[data-automation-id], a[data-automation-id]")
      .filter((element) => !element.closest("#smartjobapply-panel"))
      .find((element) => {
        if (isFinalSubmitButton(element)) return false;
        return /next|continue/i.test(element.getAttribute("data-automation-id") || "");
      });
    if (byAutomation) return byAutomation;
    return queryAllFromPage("button, a[role='button'], input[type='button'], input[type='submit']")
      .filter((element) => !element.closest("#smartjobapply-panel"))
      .find((element) => {
        if (isFinalSubmitButton(element)) return false;
        const text = weakText(`${element.getAttribute("aria-label") || ""} ${element.value || ""} ${element.textContent || ""}`);
        return /^(save and continue|continue|next|next step)$/i.test(text)
          || /\bsave and continue\b/i.test(text);
      }) || null;
  }

  async function continueToNextPage() {
    const button = findContinuePageButton();
    if (!button) {
      throw new Error("Could not find a Continue / Save and Continue button on this page.");
    }
    if (button.disabled || button.getAttribute("aria-disabled") === "true") {
      throw new Error("The page Continue button is disabled. Finish required fields first.");
    }
    if (isFinalSubmitButton(button)) {
      throw new Error("Blocked: ApplyTeX will not click the final Submit/Apply button.");
    }
    button.scrollIntoView({ block: "center", inline: "nearest" });
    button.focus?.();
    button.click();
    state.message = "Continued to the next application step. Review the new page before filling again.";
    state.error = "";
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
        width: min(390px, 100vw);
        min-width: 0;
        height: 100vh;
        overflow-x: hidden;
        overflow-y: auto;
        box-sizing: border-box;
        padding: 12px;
        border-left: 1px solid #d9e0dc;
        background: #f6f8f7;
        color: #121814;
        box-shadow: none;
        font-family: "Avenir Next", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 11px;
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
      #smartjobapply-panel * {
        box-sizing: border-box;
        letter-spacing: 0;
        line-height: 1.3 !important;
      }
      #smartjobapply-panel,
      #smartjobapply-panel * {
        max-width: 100%;
      }
      #smartjobapply-panel button {
        min-height: 32px;
        border: 1px solid #147a52;
        border-radius: 4px;
        color: #ffffff;
        background: #147a52;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
      }
      #smartjobapply-panel button:disabled { cursor: default; opacity: 0.48; }
      #smartjobapply-panel h2 { margin: 0; font-size: 12px; line-height: 1.25 !important; }
      #smartjobapply-panel summary { cursor: pointer; font-weight: 700; margin: 8px 0; line-height: 1.3 !important; }
      #smartjobapply-panel .sja-head,
      #smartjobapply-panel .sja-row-between {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      #smartjobapply-panel .sja-head {
        margin-bottom: 6px;
        padding-bottom: 6px;
        border-bottom: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-head-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      #smartjobapply-panel .sja-brand { font-size: 14px; line-height: 1.1 !important; font-weight: 800; font-family: "Avenir Next Condensed", "Arial Narrow", "Avenir Next", sans-serif; letter-spacing: 0; }
      #smartjobapply-panel .sja-icon-button {
        width: 32px;
        min-height: 32px;
        padding: 0;
        border-radius: 4px;
        color: #344039;
        background: transparent;
        border-color: transparent;
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
        gap: 8px;
        padding: 8px 0 0;
        border-top: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-label,
      #smartjobapply-panel .sja-muted {
        color: #5d645f;
        font-size: 10.5px;
      }
      #smartjobapply-panel .sja-account-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        width: 100%;
        min-height: 32px;
        margin-bottom: 6px;
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
      }
      #smartjobapply-panel .sja-account-menu {
        position: relative;
        min-width: 0;
      }
      #smartjobapply-panel .sja-account-menu > summary {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 4px;
        min-height: 32px;
        max-width: 250px;
        margin: 0;
        padding: 0 4px;
        color: #121814;
        cursor: pointer;
        list-style: none;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-account-menu > summary::-webkit-details-marker {
        display: none;
      }
      #smartjobapply-panel .sja-account-menu > summary strong {
        font-family: "Avenir Next", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 11px;
        font-weight: 650;
      }
      #smartjobapply-panel .sja-account-menu > summary span {
        color: #667169;
        font-size: 9px;
      }
      #smartjobapply-panel .sja-account-actions {
        position: absolute;
        top: calc(100% + 2px);
        right: 0;
        z-index: 5;
        display: grid;
        width: 160px;
        padding: 4px;
        border: 1px solid #d9e0dc;
        border-radius: 4px;
        background: #ffffff;
      }
      #smartjobapply-panel .sja-account-actions button {
        width: 100%;
        min-height: 32px;
        padding: 0 8px;
        border: 0;
        color: #121814;
        background: transparent;
        font-size: 10.5px;
        font-weight: 600;
        text-align: left;
      }
      #smartjobapply-panel .sja-account-actions button:hover,
      #smartjobapply-panel .sja-account-actions button:focus-visible {
        background: #eef2f0;
      }
      #smartjobapply-panel .sja-signin input[type="text"] {
        width: 100%;
        margin: 6px 0 10px;
        padding: 8px 10px;
        border: 1px solid #dedad0;
        border-radius: 6px;
        background: #ffffff;
        color: #17201b;
      }
      #smartjobapply-panel .sja-profile-pick {
        display: grid;
        gap: 6px;
        margin: 0 0 10px;
      }
      #smartjobapply-panel .sja-profile-pick .sja-secondary-button {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        width: 100%;
        text-align: left;
      }
      #smartjobapply-panel .sja-profile-pick span {
        color: #5d645f;
        font-size: 11px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      }
      #smartjobapply-panel .sja-job-summary {
        display: grid;
        gap: 6px;
        margin: 0 0 8px;
        padding: 8px 0;
        border-top: 1px solid #d9e0dc;
        border-bottom: 1px solid #d9e0dc;
        overflow: hidden;
      }
      #smartjobapply-panel .sja-job-main {
        display: grid;
        grid-template-columns: 32px minmax(0, 1fr) 52px;
        gap: 8px;
        align-items: center;
      }
      #smartjobapply-panel .sja-company-mark {
        display: grid;
        place-items: center;
        width: 32px;
        height: 32px;
        border-radius: 4px;
        background: #121814;
        color: #ffffff;
        font-family: "Avenir Next Condensed", "Arial Narrow", sans-serif;
        font-size: 12px;
        font-weight: 800;
      }
      #smartjobapply-panel .sja-job-copy {
        display: grid;
        gap: 2px;
        min-width: 0;
      }
      #smartjobapply-panel .sja-job-copy .sja-company-name {
        color: #667169;
        font-size: 10px;
        font-weight: 650;
      }
      #smartjobapply-panel .sja-job-copy strong {
        display: block;
        color: #121814;
        font-size: 14px;
        font-weight: 750;
        line-height: 1.2 !important;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-job-copy span:not(.sja-company-name) {
        color: #667169;
        font-size: 9.5px;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-score-ring {
        display: grid;
        place-items: center;
        align-content: center;
        width: 52px;
        height: 36px;
        min-height: 36px;
        padding: 0;
        border: 1px solid #d9e0dc;
        border-radius: 4px;
        color: #121814;
        background: #ffffff;
      }
      #smartjobapply-panel .sja-score-ring strong {
        font-size: 14px;
        font-weight: 800;
        line-height: 1 !important;
      }
      #smartjobapply-panel .sja-score-ring span {
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 8px;
        font-weight: 650;
        text-transform: none;
      }
      #smartjobapply-panel .sja-job-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 4px 8px;
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
        font-weight: 600;
      }
      #smartjobapply-panel .sja-job-meta span {
        min-width: 0;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-tabs {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 2px;
        padding: 2px;
        border: 0;
        border-radius: 4px;
        background: #e9eeeb;
      }
      #smartjobapply-panel .sja-tabs button {
        min-height: 30px;
        border-color: transparent;
        background: transparent;
        color: #5d645f;
        font-size: 11px;
      }
      #smartjobapply-panel .sja-tabs button.active {
        border-color: #d9e0dc;
        background: #ffffff;
        color: #177a55;
      }
      #smartjobapply-panel .sja-secondary-button {
        min-height: 30px;
        padding: 0 10px;
        border-color: #dedad0;
        background: #ffffff;
        color: #203028;
        font-size: 11px;
      }
      #smartjobapply-panel .sja-score-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
      }
      #smartjobapply-panel .sja-score-grid span {
        padding: 8px;
        border: 1px solid #dedad0;
        border-radius: 7px;
        background: #ffffff;
        color: #5d645f;
        font-size: 11px;
      }
      #smartjobapply-panel .sja-score-grid strong {
        display: block;
        color: #17201b;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 16px;
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
      #smartjobapply-panel .sja-card > *,
      #smartjobapply-panel .sja-subpanel > * {
        min-height: 1.35em;
        line-height: 1.35 !important;
      }
      #smartjobapply-panel .sja-page-context {
        color: #5d645f;
        font-size: 11px;
        font-weight: 750;
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
        font-size: 11px;
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
        height: 3px;
        overflow: hidden;
        border-radius: 2px;
        background: #dfe5e2;
      }
      #smartjobapply-panel .sja-bar {
        height: 100%;
        background: #147a52;
      }
      #smartjobapply-panel .sja-review-summary {
        display: flex;
        flex-wrap: wrap;
        gap: 4px 12px;
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
      }
      #smartjobapply-panel .sja-progress-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
        color: #121814;
      }
      #smartjobapply-panel .sja-progress-head strong {
        font-size: 12px;
        font-weight: 750;
      }
      #smartjobapply-panel .sja-progress-head span {
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
      }
      #smartjobapply-panel .sja-review-summary span {
        padding: 0;
        color: inherit;
        font-size: inherit;
        line-height: 1.2 !important;
        text-align: left;
      }
      #smartjobapply-panel .sja-review-summary strong {
        display: inline;
        color: #121814;
        font-size: 10px;
      }
      #smartjobapply-panel .sja-autofill-progress {
        display: grid;
        gap: 5px;
        padding: 6px 0;
        border-top: 1px solid #d9e0dc;
        border-bottom: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-autofill-progress-head {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto auto;
        align-items: center;
        gap: 8px;
        min-height: 32px;
      }
      #smartjobapply-panel .sja-autofill-progress-head strong {
        min-width: 0;
        color: #121814;
        font-size: 11px;
        font-weight: 700;
      }
      #smartjobapply-panel .sja-autofill-progress-head > span {
        color: #121814;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 10px;
      }
      #smartjobapply-panel .sja-wave-dots {
        display: inline-flex;
        align-items: baseline;
        width: 12px;
        margin-left: 1px;
      }
      #smartjobapply-panel .sja-wave-dots span {
        display: inline-block;
        animation: sja-dot-wave 900ms ease-in-out infinite;
      }
      #smartjobapply-panel .sja-wave-dots span:nth-child(2) { animation-delay: 120ms; }
      #smartjobapply-panel .sja-wave-dots span:nth-child(3) { animation-delay: 240ms; }
      #smartjobapply-panel .sja-cancel-autofill {
        width: auto;
        min-width: 54px;
        min-height: 32px;
        padding: 0 6px;
        border: 0;
        background: transparent;
        color: #121814;
        font-size: 10px;
        font-weight: 650;
        text-decoration: underline;
      }
      #smartjobapply-panel .sja-autofill-current {
        overflow: hidden;
        color: #667169;
        font-size: 9.5px;
        line-height: 1.3 !important;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      @keyframes sja-dot-wave {
        0%, 60%, 100% { opacity: 0.4; transform: translateY(0); }
        30% { opacity: 1; transform: translateY(-3px); }
      }
      @media (prefers-reduced-motion: reduce) {
        #smartjobapply-panel .sja-wave-dots span { animation: none; }
      }
      #smartjobapply-panel .sja-safety-note {
        margin: 0;
        color: #667169;
        font-size: 9.5px;
        line-height: 1.3 !important;
      }
      #smartjobapply-panel .sja-continue-footer {
        position: sticky;
        bottom: 0;
        z-index: 2;
        margin: 0;
        padding: 8px 0 0;
        background: #f6f8f7;
        border-top: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-continue-button {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        width: 100%;
        min-height: 36px;
        border: 0;
        border-radius: 4px;
        background: #147a52;
        color: #ffffff;
        font-size: 11px;
        font-weight: 700;
        box-shadow: none;
      }
      #smartjobapply-panel .sja-continue-button:disabled {
        background: #d9e0dc;
        color: #667169;
        box-shadow: none;
      }
      #smartjobapply-panel .sja-continue-button span:last-child {
        font-size: 11px;
        line-height: 1;
      }
      #smartjobapply-panel .sja-field-list {
        display: flex !important;
        flex-direction: column !important;
        gap: 0;
        max-height: min(48vh, 430px);
        overflow-x: hidden;
        overflow-y: auto;
        border-top: 1px solid #d9e0dc;
        border-bottom: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-question-group {
        display: grid;
        gap: 0;
      }
      #smartjobapply-panel .sja-question-group h3 {
        margin: 0;
        padding: 6px 4px 4px;
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
        font-weight: 700;
        text-transform: none;
        letter-spacing: 0;
      }
      #smartjobapply-panel .sja-question-group + .sja-question-group {
        padding-top: 4px;
        border-top: 1px solid #d9e0dc;
      }
      #smartjobapply-panel .sja-question-row {
        position: relative;
        display: grid;
        grid-template-columns: 18px minmax(0, 1fr);
        gap: 6px;
        align-items: start;
        min-height: 32px;
        padding: 6px 4px 6px 10px;
        border: 0;
        border-bottom: 1px solid #e5eae7;
        border-radius: 0;
        background: transparent;
        overflow: hidden;
      }
      #smartjobapply-panel .sja-question-row:last-child { border-bottom: 0; }
      #smartjobapply-panel .sja-question-row::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 3px;
        background: #9aa49e;
      }
      #smartjobapply-panel .sja-question-row.filled::before { background: #147a52; }
      #smartjobapply-panel .sja-question-row.ready::before { background: #2457a6; }
      #smartjobapply-panel .sja-question-row.failed::before { background: #a33c32; }
      #smartjobapply-panel .sja-question-row.blocked::before { background: #a96519; }
      #smartjobapply-panel .sja-question-row > div {
        min-width: 0;
        padding-top: 1px;
      }
      #smartjobapply-panel .sja-question-row strong {
        display: block;
        color: #121814;
        font-size: 11.5px;
        font-weight: 650;
        line-height: 1.25 !important;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-question-requirement {
        display: inline;
        color: #7a847e;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 8.5px;
        font-weight: 500;
      }
      #smartjobapply-panel .sja-question-row span:not(.sja-question-mark) {
        display: block;
        margin-top: 1px;
        color: #667169;
        font-size: 9.5px;
      }
      #smartjobapply-panel .sja-question-mark {
        display: grid;
        place-items: center;
        width: 16px;
        height: 16px;
        margin-top: 0;
        border-radius: 2px;
        background: transparent;
        color: #667169;
        font-size: 11px;
        font-weight: 800;
        line-height: 1 !important;
      }
      #smartjobapply-panel .sja-question-row.filled .sja-question-mark {
        background: transparent;
        color: #147a52;
      }
      #smartjobapply-panel .sja-question-row.ready .sja-question-mark {
        background: transparent;
        color: #2457a6;
      }
      #smartjobapply-panel .sja-question-row.failed .sja-question-mark {
        background: transparent;
        color: #a33c32;
      }
      #smartjobapply-panel .sja-question-row.blocked .sja-question-mark {
        background: transparent;
        color: #a96519;
      }
      #smartjobapply-panel .sja-inline-action {
        width: auto;
        min-height: 32px;
        margin-top: 4px;
        padding: 0 6px;
        border-color: #d9e0dc;
        background: #ffffff;
        color: #2457a6;
        font-size: 10px;
        font-weight: 650;
      }
      #smartjobapply-panel .sja-skill-list {
        display: grid;
        gap: 8px;
        max-height: 310px;
        overflow-x: hidden;
        overflow-y: auto;
      }
      #smartjobapply-panel .sja-field {
        display: grid !important;
        flex: 0 0 auto !important;
        grid-template-columns: 26px minmax(0, 1fr);
        grid-template-rows: auto;
        gap: 7px;
        align-items: start;
        min-height: 0;
        height: auto !important;
        padding: 3px 0;
        font-size: 12px !important;
      }
      #smartjobapply-panel .sja-field > div {
        display: block !important;
        position: static !important;
        height: auto !important;
        min-height: 0 !important;
        min-width: 0;
      }
      #smartjobapply-panel .sja-field > div strong,
      #smartjobapply-panel .sja-field > div span {
        display: block;
        position: static !important;
        width: auto !important;
        height: auto !important;
        min-height: 0 !important;
        margin: 0 !important;
        line-height: 1.25 !important;
        white-space: normal !important;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-field > div strong {
        margin-bottom: 2px !important;
        font-size: 12px !important;
        font-weight: 750 !important;
      }
      #smartjobapply-panel .sja-field > div span {
        color: #5d645f;
        font-size: 10.5px !important;
        font-weight: 400 !important;
      }
      #smartjobapply-panel .sja-field .sja-dot {
        display: inline-grid;
        place-items: center;
        width: 18px;
        min-width: 18px;
        height: 18px;
        border-radius: 50%;
        color: #7a817a;
        background: #f0efe8;
        font-size: 0;
        line-height: 0 !important;
        font-weight: 800;
        text-align: center;
        align-self: start;
        margin-top: 1px;
      }
      #smartjobapply-panel .sja-field .sja-dot::before {
        content: "−";
        display: block;
        color: currentColor;
        font-size: 12px;
        line-height: 1 !important;
      }
      #smartjobapply-panel .sja-field .sja-dot.ready { color: #ffffff; background: #177a55; }
      #smartjobapply-panel .sja-field .sja-dot.ready::before {
        content: "✓";
        transform: translateY(-1px);
      }
      #smartjobapply-panel .sja-field .sja-dot.planned {
        color: #177a55;
        background: #edf5ef;
        border: 1px solid #9bcbb5;
      }
      #smartjobapply-panel .sja-field .sja-dot.planned::before {
        content: "→";
      }
      #smartjobapply-panel .sja-field .sja-dot.failed {
        color: #9f2d20;
        background: #fff0ed;
        border: 1px solid #e1a096;
      }
      #smartjobapply-panel .sja-field .sja-dot.failed::before {
        content: "!";
      }
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
      #smartjobapply-panel .sja-record-picker {
        display: grid;
        gap: 5px;
      }
      #smartjobapply-panel .sja-record-picker select {
        width: 100%;
        min-height: 34px;
        border: 1px solid #c8cec9;
        border-radius: 6px;
        background: #ffffff;
        color: #17201b;
      }
      #smartjobapply-panel .sja-status {
        margin-top: 6px;
        padding: 6px;
        border-radius: 4px;
        background: #e9eeeb;
        color: #38443e;
        font-size: 10px;
      }
      #smartjobapply-panel .sja-success { background: #edf5ef; color: #177a55; }
      #smartjobapply-panel .sja-error { background: #fff1f1; color: #9f2f2f; }
      #smartjobapply-panel .sja-diagnostics {
        margin-top: 8px;
        border-top: 1px solid #d9e0dc;
        padding-top: 6px;
        color: #667169;
        font-size: 9px;
      }
      #smartjobapply-panel .sja-diagnostics summary {
        margin: 0;
        color: #667169;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 9px;
        font-weight: 700;
        text-transform: none;
        letter-spacing: 0;
      }
      #smartjobapply-panel .sja-diagnostics div {
        display: grid;
        gap: 4px;
        margin-top: 7px;
      }
      #smartjobapply-panel .sja-diagnostics span {
        display: grid;
        grid-template-columns: 78px minmax(0, 1fr);
        gap: 6px;
        overflow-wrap: anywhere;
      }
      #smartjobapply-panel .sja-diagnostics b {
        color: #121814;
      }
      #smartjobapply-panel .sja-diagnostic-bridge {
        align-items: start;
      }
      #smartjobapply-panel .sja-diagnostic-bridge em {
        display: grid;
        gap: 2px;
        font-style: normal;
      }
      #smartjobapply-panel .sja-diagnostic-bridge i {
        display: block;
        color: #667169;
        font-style: normal;
      }
      #smartjobapply-panel .sja-diagnostic-bridge i::before {
        content: "− ";
      }
      #smartjobapply-panel .sja-diagnostic-bridge i.ready {
        color: #147a52;
      }
      #smartjobapply-panel .sja-diagnostic-bridge i.ready::before {
        content: "✓ ";
      }
    `;
    document.head.append(style);
  }

  async function apiRequest(path, options = {}) {
    const profileId = String(options.profileId || state.profileId || "").trim();
    const headers = {
      ...(options.headers && typeof options.headers === "object" ? options.headers : {}),
    };
    if (profileId) headers["X-Profile-Id"] = profileId;
    const token = String(options.accessToken || state.accessToken || "").trim();
    if (token && !options.skipAuth) {
      headers.Authorization = `Bearer ${token}`;
    }
    let response;
    try {
      response = await chrome.runtime.sendMessage({
        type: "APPLYTEX_API_REQUEST",
        path,
        profileId,
        authorization: headers.Authorization || "",
        options: {
          method: options.method || "GET",
          body: options.body,
          headers,
          profileId,
          authorization: headers.Authorization || "",
        },
      });
    } catch (sendError) {
      throw new Error(sendError instanceof Error ? sendError.message : "Extension messaging failed.");
    }
    if (!response?.ok) {
      throw new Error(response?.error || `Local API returned ${response?.status || 0}.`);
    }
    return response.data;
  }

  function automaticAnswerCandidates() {
    const questions = new Map((state.scan?.questions || []).map((question) => [question.field_id, question]));
    return (state.plan?.review_items || []).filter((item) => {
      const question = questions.get(item.field_id);
      const statusRecord = state.automaticAnswerStatus[item.field_id];
      const automaticStatus = statusRecord?.label === question?.label ? statusRecord.state : "";
      return Boolean(
        item.required
        && item.status !== "ready"
        && item.answer_source !== "already_on_page"
        && item.answer_source !== "eeo_opt_in"
        && question
        && !question.sensitive
        && !question.current_value_present
        && item.draft_eligible === true
        && ["textarea", "contenteditable"].includes(question.input_type)
        && !["generating", "filled", "preserved", "failed"].includes(automaticStatus),
      );
    });
  }

  function resetAutomaticAnswerState() {
    automaticAnswerEpoch += 1;
    state.answerDrafts = {};
    state.automaticAnswerStatus = {};
    state.generatedAnswerFields = {};
  }

  function scheduleAutomaticAnswerGeneration() {
    if (automaticAnswerQueueRunning || !state.scan?.scan_id || !automaticAnswerCandidates().length) return;
    void runAutomaticAnswerQueue(automaticAnswerEpoch);
  }

  async function runAutomaticAnswerQueue(epoch) {
    if (automaticAnswerQueueRunning) return;
    automaticAnswerQueueRunning = true;
    try {
      while (epoch === automaticAnswerEpoch) {
        const item = automaticAnswerCandidates()[0];
        if (!item) break;
        const fieldId = item.field_id;
        state.automaticAnswerStatus[fieldId] = { state: "generating", error: "", label: item.label };
        state.error = "";
        render();
        try {
          await generateAndFillAutomaticAnswer(fieldId, epoch);
        } catch (error) {
          if (epoch !== automaticAnswerEpoch) break;
          const message = error instanceof Error ? error.message : String(error);
          state.automaticAnswerStatus[fieldId] = { state: "failed", error: message, label: item.label };
          state.error = `Could not fill the AI draft for ${reviewItemDisplayLabel(item)}: ${message}`;
        }
        render();
      }
    } finally {
      automaticAnswerQueueRunning = false;
      render();
      scheduleAutomaticAnswerGeneration();
    }
  }

  async function generateAndFillAutomaticAnswer(fieldId, epoch) {
    const scanId = state.scan?.scan_id;
    const question = (state.scan?.questions || []).find((candidate) => candidate.field_id === fieldId);
    if (!scanId || !question) throw new Error("The form scan is no longer available.");
    const draft = await apiRequest(`/extension/forms/${scanId}/answers/draft`, {
      method: "POST",
      body: JSON.stringify({ field_id: fieldId, profile_id: state.profileId }),
    });
    if (epoch !== automaticAnswerEpoch || state.scan?.scan_id !== scanId) {
      throw new Error("The application form changed while the draft was being generated.");
    }
    const answer = weakText(draft.answer);
    const maxLength = Number(question.max_length) || 0;
    if (!answer || answerWordCount(answer) > 100 || (maxLength > 0 && answer.length > maxLength)) {
      throw new Error("The generated answer exceeded the field limit.");
    }

    const element = findField(fieldId);
    if (!element) throw new Error("The application text box is no longer on the page.");
    if (currentAnswerValue(element)) {
      state.automaticAnswerStatus[fieldId] = { state: "preserved", error: "", label: question.label };
      await rescanAndPlan();
      return;
    }

    const plan = await apiRequest(`/extension/forms/${scanId}/plan`, {
      method: "POST",
      body: JSON.stringify({
        overrides: { [fieldId]: answer },
        answer_source: "generated",
        research_sources: (draft.sources || []).map((source) => source.url).filter(Boolean),
        profile_id: state.profileId,
      }),
    });
    const action = (plan.actions || []).find((candidate) => candidate.field_id === fieldId);
    if (!action || action.action === "skip") throw new Error("The generated answer was not approved by the fill plan.");
    const result = await fillReviewedFields([action]);
    if (result.filled !== 1 || result.failed_values?.length) {
      throw new Error(result.failed_values?.[0]?.status?.replaceAll("_", " ") || "The page rejected the generated answer.");
    }

    state.plan = plan;
    state.answerDrafts[fieldId] = draft;
    state.generatedAnswerFields[fieldId] = question.label;
    state.automaticAnswerStatus[fieldId] = { state: "filled", error: "", label: question.label };
    state.message = "AI draft filled into the application. Review it before submitting.";
    state.error = "";
    await rescanAndPlan();
  }

  function currentAnswerValue(element) {
    if (element.getAttribute("contenteditable") === "true") return weakText(element.textContent);
    return weakText(element.value);
  }

  function focusGeneratedAnswerField(fieldId) {
    const element = findField(fieldId);
    if (!element) {
      state.error = "The application text box is no longer available. Restart page analysis to find it again.";
      render();
      return;
    }
    element.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    element.focus({ preventScroll: true });
    if (typeof element.setSelectionRange === "function") {
      const end = String(element.value || "").length;
      element.setSelectionRange(end, end);
    }
    state.message = "Edit the draft directly in the application text box.";
    state.error = "";
    render();
  }

  function providerForUrl(url) {
    const detected = globalThis.ApplyTexProviders?.providerForUrl?.(url);
    if (detected) return detected;
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
      return canonicalIdentityUrl(parsed);
    } catch {
      return url;
    }
  }

  function applicationStorageKey(jobId) {
    return `${state.profileId || "default"}:${jobId}`;
  }

  function hostnameForUrl(url) {
    try {
      return new URL(url || "https://invalid.local").hostname;
    } catch {
      return "";
    }
  }

  function isApplicationLikePage() {
    if (isAuthenticationPage()) return true;
    const path = location.pathname.toLowerCase().replace(/[^a-z_]+/g, " ");
    if (/\b(apply|application|job_app|candidate_home)\b/.test(path)) return true;
    if (queryAllFromPage("input[type='file']").some(isPageElementVisible)) return true;
    const bodyText = weakText(document.body?.innerText || "").toLowerCase();
    const applicationCopy = /\b(submit your application|application form|attach resume|resume\/cv|cover letter)\b/.test(bodyText);
    if (!applicationCopy) return false;
    const visibleControls = queryAllFromPage("input, select, textarea")
      .filter((element) => !element.closest("#smartjobapply-panel"))
      .filter((element) => {
        const style = element.ownerDocument.defaultView?.getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return style?.display !== "none" && style?.visibility !== "hidden" && box.width > 0 && box.height > 0;
      });
    return visibleControls.length >= 3;
  }

  function isPageElementVisible(element) {
    if (!element) return false;
    const style = element.ownerDocument.defaultView?.getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return style?.display !== "none" && style?.visibility !== "hidden" && box.width > 0 && box.height > 0;
  }

  function workflowKeyForUrl(url, detectedProvider) {
    try {
      const parsed = new URL(url);
      const externalId = externalIdForUrl(url, detectedProvider);
      if (externalId) return `${detectedProvider}:${parsed.hostname}:${externalId.toLowerCase()}`;
      return `${detectedProvider}:${canonicalIdentityUrl(parsed)}`;
    } catch {
      return `${detectedProvider}:${url}`;
    }
  }

  function externalIdForUrl(url, detectedProvider) {
    try {
      const parsed = new URL(url);
      const pathname = decodeURIComponent(parsed.pathname);
      const queryIds = {
        greenhouse: parsed.searchParams.get("gh_jid") || parsed.searchParams.get("token"),
        indeed: parsed.searchParams.get("jk") || parsed.searchParams.get("vjk"),
        linkedin: parsed.searchParams.get("currentJobId"),
        ziprecruiter: parsed.searchParams.get("jid"),
      };
      if (queryIds[detectedProvider]) return queryIds[detectedProvider];
      if (detectedProvider === "workday") {
        return pathname.match(/\/job\/.*?((?:JR|R)-?\d+)(?:\/|$)/i)?.[1] || "";
      }
      if (detectedProvider === "ashby") {
        return pathname.match(/^\/[^/]+\/([0-9a-f-]{20,})(?:\/|$)/i)?.[1] || "";
      }
      if (detectedProvider === "icims") {
        return pathname.match(/\/jobs\/(\d+)(?:\/|$)/i)?.[1] || "";
      }
      if (detectedProvider === "linkedin") {
        return pathname.match(/\/jobs\/view\/(\d+)(?:\/|$)/i)?.[1] || "";
      }
      if (detectedProvider === "workable") {
        return pathname.match(/\/j\/([^/]+)(?:\/|$)/i)?.[1] || "";
      }
      return pathname.split("/").filter(Boolean).pop() || "";
    } catch {
      return "";
    }
  }

  function canonicalIdentityUrl(parsed) {
    const identityParams = ["gh_jid", "jid", "jk", "token", "vjk"]
      .map((key) => [key, parsed.searchParams.get(key)])
      .filter(([, value]) => value)
      .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
      .join("&");
    const base = `${parsed.origin}${parsed.pathname}`.replace(/\/$/, "");
    return identityParams ? `${base}?${identityParams}` : base;
  }

  function isAuthenticationPage() {
    const password = queryAllFromPage("input[type='password']")
      .find((element) => {
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      });
    return Boolean(password);
  }

  function shouldCaptureJobFromPage() {
    if (isApplicationLikePage()) {
      return hasCapturableJobContextOnApplicationPage();
    }
    if (!state.job) return true;
    if (isAuthenticationPage()) return false;
    const path = location.pathname.toLowerCase().replace(/[^a-z_]+/g, " ");
    return !/\b(apply|application|job_app|candidate_home)\b/.test(path);
  }

  function hasCapturableJobContextOnApplicationPage() {
    if (isAuthenticationPage()) return false;
    const config = globalThis.ApplyTexProviders?.configFor?.(state.provider) || {};
    const elementValue = (element) => weakText(
      element?.getAttribute?.("content") ||
      element?.getAttribute?.("alt") ||
      element?.getAttribute?.("aria-label") ||
      element?.textContent,
    );
    const firstValue = (selectors) => {
      for (const selector of selectors || []) {
        const value = elementValue(queryFirstFromPage(selector));
        if (value) return value;
      }
      return "";
    };
    const title = firstValue(config.selectors?.title || ["h1"]);
    const company = firstValue(config.selectors?.company || []);
    const description = (config.selectors?.description || [])
      .map((selector) => queryFirstFromPage(selector))
      .filter(Boolean)
      .find((element) => {
        const semanticIdentity = [
          element.id,
          element.className,
          element.getAttribute?.("data-testid"),
          element.getAttribute?.("data-test"),
          element.getAttribute?.("data-cy"),
          element.getAttribute?.("data-ui"),
          element.getAttribute?.("data-automation-id"),
        ].map(weakText).join(" ");
        return /\b(?:description|job-details|posting)\b/i.test(semanticIdentity) && elementValue(element).length >= 180;
      });
    return title.length >= 5 && company.length >= 3 && Boolean(description);
  }

  function shouldScanApplicationForm(scan) {
    if (isAuthenticationPage() || !scan.questions.length) return false;
    const path = location.pathname.toLowerCase();
    const params = new URLSearchParams(location.search);
    if (state.provider === "icims" && (params.get("mode") === "apply" || params.get("apply") === "yes")) return true;
    if (/\b(apply|application|job_app)\b/.test(path.replace(/[^a-z_]+/g, " "))) return true;
    if (queryAllFromPage("input[type='file']").some(isPageElementVisible)) return true;
    return scan.questions.length >= 3;
  }

  function isLowConfidenceApplicationContext() {
    if (!isApplicationLikePage()) return false;
    const confidence = Number(state.job?.capture_confidence);
    if (Number.isFinite(confidence) && confidence < 0.55) return true;
    return weakText(state.job?.description || "").length < 180;
  }

  async function extractJobFromPage(detectedProvider) {
    const text = (element) => element?.textContent?.trim() || "";
    const firstText = (selectors) => {
      for (const selector of selectors) {
        const element = queryFirstFromPage(selector);
        const value =
          element?.getAttribute?.("content") ||
          element?.getAttribute?.("alt") ||
          element?.getAttribute?.("aria-label") ||
          text(element);
        if (value) return value;
      }
      return "";
    };
    const providerConfig = globalThis.ApplyTexProviders?.configFor?.(detectedProvider) || {};
    if (providerConfig.beforeCaptureTab) {
      await activateTab(providerConfig.beforeCaptureTab, 1400);
    }
    const selectors = providerConfig.selectors || {};
    const titleCompany = companyFromDocumentTitle(document.title);
    let company = detectedProvider === "ashby" && titleCompany
      ? titleCompany
      : firstText(selectors.company || []);
    if (!company) {
      company = companyFromPage(detectedProvider);
    }
    const title = cleanCapturedJobTitle(firstText(selectors.title || ["h1"]), company);
    const tabPanelDescription = detectedProvider === "ashby" ? activeTabPanelText() : "";
    const descriptionSource = tabPanelDescription
      ? "active tab panel"
      : (selectors.description?.length ? "provider selector" : "page text");
    const description = cleanDescription(tabPanelDescription || firstText(selectors.description || ["main", "body"]), detectedProvider);
    const locationText = firstText(selectors.location || []);
    if (providerConfig.afterCaptureTab) {
      await activateTab(providerConfig.afterCaptureTab, 250);
    }
    if (!title || !description) {
      throw new Error("The job title or description could not be identified on this page.");
    }
    const confidence = captureConfidence({ company, title, description, detectedProvider });
    const warnings = captureWarnings({ company, title, description, confidence });
    return {
      provider: detectedProvider,
      external_id: externalIdForUrl(location.href, detectedProvider),
      company: company || document.title.split("|").pop()?.trim() || "Unknown company",
      title,
      description,
      location: locationText,
      source_url: location.href,
      apply_url: location.href,
      workflow_key: state.flowKey,
      canonical_url: canonicalPageKey(location.href),
      description_source: descriptionSource,
      capture_confidence: confidence,
      warnings,
    };
  }

  function captureConfidence({ company, title, description, detectedProvider }) {
    let score = 0.25;
    if (detectedProvider && detectedProvider !== "unknown") score += 0.15;
    if (weakText(company).length > 2) score += 0.15;
    if (weakText(title).length > 4) score += 0.2;
    const descriptionLength = weakText(description).length;
    if (descriptionLength > 1200) score += 0.25;
    else if (descriptionLength > 500) score += 0.18;
    else if (descriptionLength > 180) score += 0.1;
    return Math.max(0, Math.min(1, Number(score.toFixed(2))));
  }

  function captureWarnings({ company, title, description, confidence }) {
    const warnings = [];
    if (!weakText(company)) warnings.push("Company was inferred from the page title.");
    if (weakText(title).length < 5) warnings.push("Job title was weakly identified.");
    if (weakText(description).length < 500) warnings.push("Job description was shorter than expected.");
    if (confidence < 0.55) warnings.push(LOW_CONFIDENCE_CONTEXT_MESSAGE);
    return warnings;
  }

  function companyFromPage(detectedProvider) {
    const title = document.title || "";
    const fromApplicationTitle = companyFromDocumentTitle(title);
    if (fromApplicationTitle) return fromApplicationTitle;
    const fromMeta =
      queryFirstFromPage("meta[property='og:site_name']")?.content?.trim() ||
      queryFirstFromPage("meta[name='application-name']")?.content?.trim();
    const providerLabel = globalThis.ApplyTexProviders?.configFor?.(detectedProvider)?.label || detectedProvider;
    if (fromMeta && !fromMeta.toLowerCase().includes(String(providerLabel || "").toLowerCase())) return fromMeta;
    if (detectedProvider === "workday") {
      const brandedCompany = workdayCompanyFromPage();
      if (brandedCompany) return brandedCompany;
    }
    const parsed = new URL(location.href);
    const boardToken = parsed.searchParams.get("for") || parsed.pathname.split("/").filter(Boolean)[0] || "";
    return humanizeBoardToken(boardToken || parsed.hostname.split(".")[0]);
  }

  function cleanCapturedJobTitle(value, company = "") {
    let title = weakText(value).replace(/^job application for\s+/i, "");
    const companyText = weakText(company);
    if (companyText) {
      const escapedCompany = companyText.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      title = title.replace(new RegExp(`\\s+at\\s+${escapedCompany}\\s*$`, "i"), "");
    }
    return title.trim();
  }

  function jobTitleFromDocumentTitle(value) {
    const company = companyFromDocumentTitle(value);
    return cleanCapturedJobTitle(value, company);
  }

  function companyFromDocumentTitle(value) {
    return weakText(value).match(/(?:\bat\s+|\s@\s*)(.+?)(?:\s*$|\s+-\s+|\s+\|)/i)?.[1]?.trim() || "";
  }

  function workdayCompanyFromPage() {
    const logoSource = queryFirstFromPage("[data-automation-id='logo'][src]")?.getAttribute("src") || "";
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

  async function activateTab(label, delayMs) {
    const wanted = label.toLowerCase();
    const tab = queryAllFromPage("button, [role='tab']")
      .find((element) => (element.textContent || "").trim().toLowerCase() === wanted);
    if (tab) {
      tab.click();
      const deadline = Date.now() + delayMs;
      while (Date.now() < deadline) {
        const selected = tab.getAttribute("aria-selected") === "true" || tab.getAttribute("data-state") === "active";
        if (selected) return true;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return false;
    }
    return false;
  }

  function activeTabPanelText() {
    const panel = queryAllFromPage("[role='tabpanel']")
      .find((element) => {
        const style = element.ownerDocument.defaultView?.getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return !element.hidden && element.getAttribute("aria-hidden") !== "true" && style?.display !== "none" && box.width > 0 && box.height > 0;
      });
    return panel?.innerText?.trim() || panel?.textContent?.trim() || "";
  }

  function cleanDescription(value, detectedProvider = "") {
    let cleaned = String(value || "").replace(/\n{3,}/g, "\n\n").trim();
    const stopMarkers = globalThis.ApplyTexProviders?.configFor?.(detectedProvider)?.stopMarkers || [];
    for (const marker of stopMarkers) {
      const index = cleaned.toLowerCase().indexOf(marker.toLowerCase());
      if (index > 0) cleaned = cleaned.slice(0, index).trim();
    }
    return cleaned;
  }

  function scanApplicationForm(detectedProvider, recordSelections = {}) {
    if (detectedProvider === "workday") assignWorkdayRecordIndexes();
    const visible = (element) => {
      const style = element.ownerDocument.defaultView?.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style?.display !== "none" && style?.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    const fields = [];
    const usedFieldIds = new Set();
    const radioNames = new Set();
    const groupedCheckboxes = new Set();
    const groupedControls = new Set();
    if (detectedProvider === "workday" && isWorkdayApplicationQuestionsPage()) {
      scanWorkdayQuestionGroups({
        fields,
        usedFieldIds,
        groupedControls,
        visible,
        recordSelections,
      });
    }
    if (detectedProvider === "ashby") {
      ashbyFieldEntries()
        .filter((entry) => !entry.closest("#smartjobapply-panel"))
        .forEach((entry, index) => {
          const input = entry.querySelector("input[type='checkbox']");
          const optionButtons = customYesNoButtons(input);
          if (!input || optionButtons.length !== 2) return;
          const generatedId = uniqueFieldId(input, `ashby-yes-no-${index}`, usedFieldIds, false);
          input.dataset.smartjobapplyFieldId = generatedId;
          groupedCheckboxes.add(input);
          const labelElement = entry.querySelector(".ashby-application-form-question-title, label");
          const label = weakText(labelElement?.textContent) || labelWithProfileContext(input);
          fields.push({
            field_id: generatedId,
            label,
            input_type: "radio",
            required: Boolean(
              labelElement?.className?.toString().includes("required") ||
              requiredFromContext(input, label),
            ),
            options: optionButtons.map((button) => weakText(button.textContent)),
            sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
            autocomplete: null,
            current_value_present: optionButtons.some(isSelectedOptionButton),
            current_value: optionButtons.find(isSelectedOptionButton)?.textContent?.trim() || null,
            control_kind: "scalar",
            max_length: null,
            ...profileRecordMetadata(input, recordSelections),
          });
        });
      scanAshbyCheckboxGroups({ fields, usedFieldIds, groupedCheckboxes, visible, recordSelections });
    }
    queryAllFromPage("input[type='radio']").forEach((element, index) => {
      if (!visible(element) || groupedControls.has(element) || element.closest("#smartjobapply-panel") || isTransientPromptControl(element) || isWorkdayPortalChromeControl(element, detectedProvider)) return;
      const name = element.name || element.id || `radio-${index}`;
      if (radioNames.has(name)) return;
      radioNames.add(name);
      const group = Array.from(element.getRootNode().querySelectorAll(`input[type='radio'][name='${cssEscape(name)}']`))
        .filter((candidate) => visible(candidate));
      const groupLabels = group.map((candidate) => labelFor(candidate)).filter(Boolean);
      fields.push({
        field_id: name,
        label: radioGroupLabel(element, groupLabels),
        input_type: "radio",
        required: group.some((candidate) => candidate.required || candidate.getAttribute("aria-required") === "true")
          || requiredFromContext(element, radioGroupLabel(element, groupLabels)),
        options: group.map((candidate, groupIndex) => (groupLabels[groupIndex] || candidate.value || "").trim()).filter(Boolean),
        sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(groupLabels.join(" ")),
        autocomplete: null,
        current_value_present: group.some((candidate) => candidate.checked),
        current_value: groupLabels[group.findIndex((candidate) => candidate.checked)] || null,
        control_kind: "scalar",
        max_length: null,
        ...profileRecordMetadata(element, recordSelections),
      });
      usedFieldIds.add(name);
    });

    queryAllFromPage("button")
      .filter((element) => isCustomSelectButton(element) && visible(element) && !groupedControls.has(element) && !element.closest("#smartjobapply-panel") && !isTransientPromptControl(element) && !isWorkdayPortalChromeControl(element, detectedProvider))
      .forEach((element, index) => {
        const generatedId = uniqueFieldId(element, `button-${index}`, usedFieldIds);
        element.dataset.smartjobapplyFieldId = generatedId;
        const label = customSelectButtonLabel(element);
        const currentText = weakText(element.textContent);
        fields.push({
          field_id: generatedId,
          label,
          input_type: "select",
          required: /\brequired\b/i.test(element.getAttribute("aria-label") || "") || requiredFromContext(element, label),
          options: [],
          sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
          autocomplete: null,
          current_value_present: Boolean(currentText && !/^(select one|select|choose)$/i.test(currentText)),
          current_value: currentText && !/^(select one|select|choose)$/i.test(currentText) ? currentText : null,
          control_kind: "custom_select",
          max_length: null,
          ...profileRecordMetadata(element, recordSelections),
        });
      });

    queryAllFromPage("input, select, textarea, [contenteditable='true']")
      .filter((element) => {
        const fileInput = isTag(element, "input") && element.type === "file";
        const ignoredInput = isTag(element, "input") && ["hidden", "button", "submit", "reset", "radio"].includes(element.type);
        return !element.closest("#smartjobapply-panel") &&
          !isTransientPromptControl(element) &&
          !isWorkdayPortalChromeControl(element, detectedProvider) &&
          !groupedControls.has(element) &&
          !groupedCheckboxes.has(element) &&
          !ignoredInput &&
          !isDecorativeSelectInput(element) &&
          !isAuxiliaryResumeInput(element, detectedProvider) &&
          (visible(element) || fileInput);
      })
      .forEach((element, index) => {
        const generatedId = uniqueFieldId(element, index, usedFieldIds);
        element.dataset.smartjobapplyFieldId = generatedId;
        const dateMetadata = workdayDateMetadata(element, detectedProvider);
        const recordMetadata = profileRecordMetadata(element, recordSelections);
        const label = dateMetadata.date_boundary
          ? workdayDateLabel(dateMetadata, recordMetadata)
          : labelWithProfileContext(element);
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
        const checkbox = isTag(element, "input") && element.type === "checkbox";
        const fileInput = isTag(element, "input") && element.type === "file";
        fields.push({
          field_id: generatedId,
          label,
          input_type: inputType,
          required: element.required || element.getAttribute("aria-required") === "true" || workdayDateRequired(element, dateMetadata) || requiredFromContext(element, label),
          options,
          sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
          autocomplete: element.getAttribute("autocomplete"),
          current_value_present: controlHasCurrentValue(element, { checkbox, fileInput }),
          current_value: controlCurrentValue(element, { checkbox, fileInput }),
          control_kind: controlKindFor(element, label),
          max_length: Number.isInteger(element.maxLength) && element.maxLength > 0 ? element.maxLength : null,
          ...recordMetadata,
          ...dateMetadata,
        });
      });
    sortFieldsInDocumentOrder(fields);
    const scan = {
      provider: detectedProvider,
      page_url: location.href,
      page_title: document.title,
      step_key: applicationStepLabel(),
      questions: fields,
    };
    scan.form_signature = formStructureSignature(scan);
    return scan;
  }

  function isWorkdayApplicationQuestionsPage() {
    return /application questions/i.test(applicationStepLabel()) || queryAllFromPage("h1, h2, h3")
      .some((heading) => /^application questions$/i.test(weakText(heading.textContent)));
  }

  function scanWorkdayQuestionGroups({ fields, usedFieldIds, groupedControls, visible, recordSelections }) {
    queryAllFromPage("[role='group']")
      .filter((group) => (
        !group.closest("#smartjobapply-panel")
        && !group.querySelector("[role='group']")
      ))
      .forEach((group, index) => {
        const controls = Array.from(group.querySelectorAll(
          "button[aria-haspopup='listbox'], button[role='combobox'], textarea, select, input:not([type='hidden']):not([type='button']):not([type='submit'])",
        )).filter((control) => visible(control) && !isTransientPromptControl(control));
        if (controls.length !== 1) return;
        const control = controls[0];
        const currentText = isTag(control, "button")
          ? weakText(control.textContent)
          : weakText(control.value || control.textContent);
        const ignored = [
          currentText,
          control.getAttribute("aria-label") || "",
          "Select One",
          "Required",
        ];
        const labelNode = Array.from(group.children).find((child) => (
          /^(P|LABEL|LEGEND|H[1-6])$/.test(child.tagName)
          && weakText(child.textContent).length >= 3
        ));
        const label = cleanControlText(labelNode?.textContent || group.getAttribute("aria-label") || "", ignored)
          .slice(0, 2000)
          .trim();
        if (!label) return;
        const generatedId = uniqueFieldId(control, `workday-question-${index}`, usedFieldIds);
        control.dataset.smartjobapplyFieldId = generatedId;
        groupedControls.add(control);
        const buttonSelect = isTag(control, "button");
        const placeholder = /^(select one|select|choose)$/i.test(currentText);
        fields.push({
          field_id: generatedId,
          label,
          input_type: buttonSelect || isTag(control, "select") ? "select" : isTag(control, "textarea") ? "textarea" : control.type || "text",
          required: /\*/.test(labelNode?.textContent || "") || /\brequired\b/i.test(control.getAttribute("aria-label") || "") || requiredFromContext(control, label),
          options: isTag(control, "select") ? Array.from(control.options).map((option) => weakText(option.textContent)).filter(Boolean) : [],
          sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(label),
          autocomplete: control.getAttribute("autocomplete"),
          current_value_present: Boolean(currentText && !placeholder),
          current_value: currentText && !placeholder ? currentText : null,
          control_kind: buttonSelect ? "custom_select" : controlKindFor(control, label),
          max_length: Number.isInteger(control.maxLength) && control.maxLength > 0 ? control.maxLength : null,
          ...profileRecordMetadata(control, recordSelections),
        });
      });
  }

  function formStructureSignature(scan) {
    const source = JSON.stringify({
      provider: scan.provider,
      url: canonicalPageKey(scan.page_url),
      step: scan.step_key,
      field_ids: (scan.questions || []).map((question) => question.field_id),
    });
    let hash = 2166136261;
    for (let index = 0; index < source.length; index += 1) {
      hash ^= source.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `${scan.provider}:${scan.step_key}:${(hash >>> 0).toString(16)}`;
  }

  function ashbyFieldEntries() {
    return Array.from(new Set(queryAllFromPage([
      ".ashby-application-form-field-entry",
      ".ashby-application-form-container fieldset",
      "[data-field-path]",
    ].join(", "))));
  }

  function scanAshbyCheckboxGroups({ fields, usedFieldIds, groupedCheckboxes, visible, recordSelections }) {
    ashbyFieldEntries()
      .filter((entry) => !entry.closest("#smartjobapply-panel"))
      .forEach((entry, index) => {
        const checkboxes = Array.from(entry.querySelectorAll("input[type='checkbox']"))
          .filter((input) => visible(input) && !groupedCheckboxes.has(input) && customYesNoButtons(input).length !== 2);
        if (checkboxes.length < 2) return;
        const optionLabels = checkboxes.map(nativeCheckboxOptionLabel);
        const questionElement = entry.querySelector(".ashby-application-form-question-title, legend, [class*='question-title' i]");
        const questionLabel = (weakText(questionElement?.textContent)
          || nearbyQuestionLabel(checkboxes[0], optionLabels)
          || "Checkbox options").replace(/[:*]+\s*$/g, "").trim();
        const generatedId = uniqueFieldId(checkboxes[0], `ashby-checkbox-group-${index}`, usedFieldIds, false);
        checkboxes.forEach((input) => {
          input.dataset.smartjobapplyFieldId = generatedId;
          groupedCheckboxes.add(input);
        });
        const selected = checkboxes
          .map((input, optionIndex) => input.checked ? optionLabels[optionIndex] : "")
          .filter(Boolean);
        fields.push({
          field_id: generatedId,
          label: questionLabel,
          input_type: "checkbox",
          required: checkboxes.some((input) => input.required || input.getAttribute("aria-required") === "true")
            || requiredFromContext(checkboxes[0], questionLabel),
          options: optionLabels,
          sensitive: /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/i.test(questionLabel),
          autocomplete: null,
          current_value_present: selected.length > 0,
          current_value: selected,
          control_kind: "multi_select",
          max_length: null,
          ...profileRecordMetadata(checkboxes[0], recordSelections),
        });
      });
  }

  function nativeCheckboxOptionLabel(element) {
    const labelled = element.labels?.length
      ? Array.from(element.labels).map((label) => weakText(label.textContent)).find(Boolean)
      : "";
    return labelled || weakText(element.closest("label")?.textContent) || weakText(element.value) || "Option";
  }

  function uniqueFieldId(element, index, usedFieldIds, separateCheckboxOption = true) {
    const raw =
      element.id ||
      element.name ||
      element.getAttribute("aria-labelledby") ||
      element.getAttribute("aria-label") ||
      `sja-field-${index}`;
    let fieldId = String(raw).trim() || `sja-field-${index}`;
    if (separateCheckboxOption && isTag(element, "input") && element.type === "checkbox" && !element.id) {
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

  function sortFieldsInDocumentOrder(fields) {
    fields.sort((left, right) => {
      const leftElement = findField(left.field_id);
      const rightElement = findField(right.field_id);
      if (!leftElement || !rightElement || leftElement === rightElement) return 0;
      if (leftElement.ownerDocument !== rightElement.ownerDocument) return 0;
      const position = leftElement.compareDocumentPosition(rightElement);
      if (position & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
      if (position & Node.DOCUMENT_POSITION_PRECEDING) return 1;
      return 0;
    });
  }

  function customYesNoButtons(element) {
    if (!isTag(element, "input") || element.type !== "checkbox") return [];
    const buttons = Array.from(element.parentElement?.querySelectorAll(":scope > button") || [])
      .filter((button) => ["yes", "no"].includes(weakText(button.textContent).toLowerCase()));
    const labels = buttons.map((button) => weakText(button.textContent).toLowerCase());
    return buttons.length === 2 && labels.includes("yes") && labels.includes("no") ? buttons : [];
  }

  function isSelectedOptionButton(button) {
    const stateValue = weakText(
      button.getAttribute("aria-pressed") ||
      button.getAttribute("aria-selected") ||
      button.getAttribute("data-state"),
    ).toLowerCase();
    return stateValue === "true" ||
      stateValue === "selected" ||
      /(^|[_\s-])(active|checked|selected)([_\s-]|$)/i.test(button.className?.toString() || "");
  }

  function isDecorativeSelectInput(element) {
    if (!isTag(element, "input")) return false;
    const hasStableIdentifier = Boolean(element.id || element.name || element.getAttribute("aria-label") || element.getAttribute("aria-labelledby"));
    if (hasStableIdentifier) return false;
    if (!element.closest("[class*='select'], [role='combobox']")) return false;
    const label = weakText(labelFor(element));
    if (label && !isGenericControlText(label) && label !== "Unlabelled field") return false;
    return !element.value;
  }

  function isCustomSelectInput(element) {
    if (!isTag(element, "input")) return false;
    if (element.getAttribute("role") === "combobox") return true;
    if (element.getAttribute("aria-autocomplete") || element.getAttribute("aria-controls")) return true;
    if (element.autocomplete === "off" && /search/i.test(element.placeholder || "")) return true;
    return Boolean(element.closest(".select__input-container, .select-shell, [class*='select']")) && element.autocomplete === "off";
  }

  function isCustomSelectButton(element) {
    if (!isTag(element, "button")) return false;
    const aria = weakText(element.getAttribute("aria-label"));
    const controlText = `${aria} ${element.textContent || ""}`;
    if (/submit|apply|save and continue|next|back|sign in|create account/i.test(controlText)) return false;
    const popup = weakText(element.getAttribute("aria-haspopup")).toLowerCase();
    const automationId = weakText(element.getAttribute("data-automation-id")).toLowerCase();
    const looksLikeSelect =
      ["listbox", "menu"].includes(popup) ||
      element.getAttribute("role") === "combobox" ||
      /(?:multi)?select|dropdown|promptoption/.test(automationId);
    if (looksLikeSelect) {
      const label = customSelectButtonLabel(element);
      return Boolean(label && label !== "Unlabelled field" && !isGenericControlText(label));
    }
    return /\brequired\b/i.test(aria);
  }

  function customSelectButtonLabel(element) {
    const aria = weakText(element.getAttribute("aria-label"));
    const currentText = weakText(element.textContent);
    const nearby = nearbyQuestionLabel(element, [aria, currentText]);
    if (nearby) return nearby;
    return aria
      .replace(new RegExp(currentText.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), " ")
      .replace(/\brequired\b/gi, " ")
      .replace(/\bselect one\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim() || "Unlabelled field";
  }

  function controlHasCurrentValue(element, { checkbox = false, fileInput = false } = {}) {
    if (checkbox) return element.checked;
    if (fileInput) return Boolean(element.files?.length || element.value);
    if (isCustomSelectInput(element)) {
      const label = weakText(labelFor(element)).toLowerCase();
      let container = element.parentElement;
      for (let depth = 0; container && depth < 5; depth += 1) {
        const selected = weakText(container.textContent).match(/\b(\d+) items? selected\b/i);
        if (selected) return Number.parseInt(selected[1], 10) > 0;
        if (/country phone code|phone country code|dial(?:ing)? code/.test(label) && container.querySelector("[role='option']")) return true;
        if (isTag(container, "form")) break;
        container = container.parentElement;
      }
      return Boolean(selectedSingleValue(element)) || selectedMultiValues(element).length > 0 || Boolean(weakText(element.value));
    }
    return Boolean(element.value || element.textContent?.trim());
  }

  function controlCurrentValue(element, { checkbox = false, fileInput = false } = {}) {
    if (checkbox) return Boolean(element.checked);
    if (fileInput) return element.files?.[0]?.name || null;
    if (isCustomSelectInput(element)) {
      const selected = selectedMultiValues(element);
      if (selected.length) return selected;
      const singleValue = selectedSingleValue(element);
      if (singleValue) return singleValue;
      return weakText(element.value) || null;
    }
    if (element.getAttribute("contenteditable") === "true") {
      return weakText(element.textContent) || null;
    }
    return element.value || null;
  }

  function controlKindFor(element, label) {
    if (isCustomSelectInput(element)) {
      if (/\bskills?\b/i.test(label)) return "multi_select";
      return "custom_select";
    }
    if (isTag(element, "input")) {
      const placeholder = weakText(element.getAttribute("placeholder")).toLowerCase();
      const automationId = weakText(element.getAttribute("data-automation-id")).toLowerCase();
      if (/mm\s*\/\s*yyyy/.test(placeholder) || /month.*year|monthyear/.test(automationId)) return "month_year";
      if (/^y{4}$/.test(placeholder) || (element.maxLength === 4 && /\b(from|to|year)\b/i.test(label))) return "year";
    }
    return "scalar";
  }

  function isTransientPromptControl(element) {
    if (scanParts.isTransientPromptControl) {
      return scanParts.isTransientPromptControl(element);
    }
    return Boolean(element.closest(
      "[role='listbox'], [data-automation-id='menuItem'], [data-automation-id='promptLeafNode'], [data-automation-id='activeListContainer']",
    ));
  }

  function isWorkdayPortalChromeControl(element, detectedProvider) {
    if (detectedProvider !== "workday") return false;
    return Boolean(element.closest("header, [role='banner'], nav, [role='navigation']"));
  }

  function workdayDateMetadata(element, detectedProvider) {
    if (detectedProvider !== "workday" || !isTag(element, "input")) return {};
    const id = weakText(element.id).toLowerCase();
    const automationId = weakText(element.getAttribute("data-automation-id")).toLowerCase();
    const dateBoundary = id.includes("startdate") || id.includes("firstyearattended")
      ? "start"
      : id.includes("enddate") || id.includes("lastyearattended")
        ? "end"
        : null;
    const dateComponent = automationId.includes("datesectionmonth") || id.includes("datesectionmonth")
      ? "month"
      : automationId.includes("datesectionyear") || id.includes("datesectionyear")
        ? "year"
        : null;
    return dateBoundary && dateComponent
      ? { date_boundary: dateBoundary, date_component: dateComponent }
      : {};
  }

  function workdayDateLabel(dateMetadata, recordMetadata) {
    const section = recordMetadata.profile_record_kind === "education" ? "Education" : "Experience";
    const boundary = dateMetadata.date_boundary === "start" ? "From" : "To";
    const component = dateMetadata.date_component === "month" ? "Month" : "Year";
    return `${section} ${boundary} ${component}`;
  }

  function workdayDateRequired(element, dateMetadata) {
    if (!dateMetadata.date_boundary) return false;
    if (/^workexperience-/i.test(weakText(element.id))) return true;
    let container = element.parentElement;
    for (let depth = 0; container && depth < 6; depth += 1) {
      const label = weakText(container.getAttribute?.("aria-label"));
      if (/^(from|to|to \(actual or expected\))$/i.test(label)) {
        return /\*/.test(container.textContent || "");
      }
      container = container.parentElement;
    }
    return false;
  }

  function selectedMultiValues(element) {
    const promptContainer = element.closest(
      "[data-automation-id='multiSelectContainer'], [data-uxi-widget-type='multiselect']",
    );
    if (promptContainer) {
      return listUnique(Array.from(promptContainer.querySelectorAll(
        "[data-automation-id='selectedItem'], [data-automation-id='selectedListItem'], [class*='multi-select'] [role='listitem'], [class*='multiselect'] [role='listitem']",
      ))
        .map((item) => weakText(item.textContent).replace(/\bremove\b/gi, "").trim())
        .filter(Boolean));
    }
    if (state.provider === "workday") return [];
    let container = element.parentElement;
    for (let depth = 0; container && depth < 6; depth += 1) {
      const values = Array.from(container.querySelectorAll(
        "[data-automation-id='selectedItem'], [data-automation-id='selectedListItem'], [class*='multi-select'] [role='listitem'], [class*='multiselect'] [role='listitem']",
      ))
        .map((item) => weakText(item.textContent).replace(/\bremove\b/gi, "").trim())
        .filter(Boolean);
      if (values.length) return listUnique(values);
      if (isTag(container, "form")) break;
      container = container.parentElement;
    }
    return [];
  }

  function selectedSingleValue(element) {
    let control = element.closest(".select__control, [class*='select__control']");
    if (!control) {
      let container = element.parentElement;
      for (let depth = 0; container && depth < 4; depth += 1) {
        if (container.querySelector(".select__single-value, [class*='singleValue']")) {
          control = container;
          break;
        }
        container = container.parentElement;
      }
    }
    const selected = weakText(
      control?.querySelector(".select__single-value, [class*='singleValue']")?.textContent,
    );
    if (!selected || /^(select one|select|choose|select\.\.\.)$/i.test(selected)) return "";
    if (/^\+\s*\d/.test(selected) && /\bcountry\b/i.test(labelFor(element))) {
      const group = element.closest("[role='group'], fieldset") || control?.parentElement?.parentElement;
      const countryButton = group?.querySelector(
        "button.iti__selected-country[title], button[aria-label*='selected'][title]",
      );
      const country = weakText(countryButton?.getAttribute("title"));
      if (country) return country;
    }
    return selected;
  }

  function selectedCountForControl(element) {
    const values = selectedMultiValues(element);
    if (values.length) return values.length;
    const promptContainer = element.closest(
      "[data-automation-id='multiSelectContainer'], [data-uxi-widget-type='multiselect']",
    );
    if (promptContainer) {
      const match = weakText(promptContainer.textContent).match(/\b(\d+) items? selected\b/i);
      return match ? Number.parseInt(match[1], 10) : 0;
    }
    let container = element.parentElement;
    for (let depth = 0; container && depth < 6; depth += 1) {
      const match = weakText(container.textContent).match(/\b(\d+) items? selected\b/i);
      if (match) return Number.parseInt(match[1], 10);
      if (isTag(container, "form")) break;
      container = container.parentElement;
    }
    return 0;
  }

  function listUnique(values) {
    return Array.from(new Set(values));
  }

  function isAuxiliaryResumeInput(element, detectedProvider) {
    if (detectedProvider !== "ashby" || !isTag(element, "input") || element.type !== "file") return false;
    const containerText = weakText(element.closest("section, form, [role='tabpanel'], div")?.textContent || "");
    return /autofill from resume/i.test(containerText) && /autofill key application fields/i.test(containerText) && !element.required;
  }

  function requiredFromContext(element, label) {
    if (/\*/.test(label)) return true;
    const fieldContainer = element.closest(".ashby-application-form-field-entry, .ashby-application-form-container fieldset, [data-field-path], fieldset")
      || element.closest("label, [data-field], .field, .field-wrapper, li");
    if (fieldContainer) {
      const labelNodes = Array.from(fieldContainer.querySelectorAll("label, [class*='question-title'], [class*='label']"));
      if (labelNodes.some((node) => /(?:^|[^a-z0-9])required(?:[^a-z0-9]|$)/i.test(node.className?.toString() || "") || /\*/.test(node.textContent || ""))) {
        return true;
      }
    }
    const container = element.closest("label, [data-field], .field, .field-wrapper, div, section, li");
    return /\*/.test(container?.textContent || "");
  }

  function labelFor(element) {
    if (isTag(element, "input") && element.type === "file") {
      const fileLabel = fileUploadLabelFor(element);
      if (fileLabel) return fileLabel;
    }
    if (element.labels?.length) {
      const labelText = Array.from(element.labels).map((label) => label.textContent.trim()).join(" ");
      return sanitizeFieldLabel(
        cleanControlText(labelText, isCustomSelectInput(element) ? selectedMultiValues(element) : []),
        element,
      );
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const value = labelledBy
        .split(/\s+/)
        .map((id) => element.getRootNode().getElementById?.(id)?.textContent?.trim() || element.ownerDocument.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      if (value) return sanitizeFieldLabel(value, element);
    }
    const aria = weakText(element.getAttribute("aria-label"));
    if (aria && !isGenericControlText(aria)) return sanitizeFieldLabel(aria, element);
    const nearby = nearbyQuestionLabel(element);
    if (nearby) return sanitizeFieldLabel(nearby, element);
    const placeholder = weakText(element.getAttribute("placeholder"));
    if (placeholder && !isGenericControlText(placeholder)) return sanitizeFieldLabel(placeholder, element);
    const containerText = cleanControlText(element.closest("label, [data-field], .field, .field-wrapper, div, section, li")?.textContent || "");
    return sanitizeFieldLabel(
      containerText || aria || placeholder || element.name || element.id || "Unlabelled field",
      element,
    );
  }

  function sanitizeFieldLabel(label, element) {
    let text = cleanControlText(label);
    if (/drop files|select files/i.test(text)) {
      if (isTag(element, "input") && element.type === "file") {
        return fileUploadLabelFor(element) || "Resume/CV";
      }
      text = cleanControlText(text);
    }
    if (!text || /^(drop files|select files|or)$/i.test(text)) {
      if (isTag(element, "input") && element.type === "file") return "Resume/CV";
      return "Unlabelled field";
    }
    return text;
  }

  function labelWithProfileContext(element) {
    const label = labelFor(element);
    if (/^(resume\/cv|cover letter)/i.test(label)) return label;
    const section = profileSectionContext(element);
    if (!section) return label;
    if (label.toLowerCase().startsWith(section.toLowerCase())) return label;
    if (/unlabelled field/i.test(label)) return section;
    return `${section} ${label}`;
  }

  function profileSectionContext(element) {
    let current = element;
    for (let depth = 0; current && depth < 14; depth += 1) {
      const candidates = [];
      if (current?.querySelectorAll && current?.getAttribute) {
        const ownCandidates = [current.getAttribute("aria-label") || ""];
        ownCandidates.push(...Array.from(current.querySelectorAll(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, :scope > legend"))
          .map((heading) => weakText(heading.textContent)));
        candidates.push(...ownCandidates);
        const ownSection = ownCandidates
          .map((candidate) => weakText(candidate).toLowerCase())
          .find((candidate) => /^(?:work )?experience(?:\s+\d+)?$|^employment history$|^education(?:\s+\d+)?$/.test(candidate));
        if (ownSection) return ownSection.includes("education") ? "Education" : "Experience";
        const structuralBoundary = isTag(current, "section") ||
          isTag(current, "fieldset") ||
          current.getAttribute("role") === "group";
        if (structuralBoundary && ownCandidates.some((candidate) => weakText(candidate))) return "";
        let sibling = current.previousElementSibling;
        for (let index = 0; sibling && index < 5; index += 1) {
          const heading = sibling.matches("h1, h2, h3, h4, legend")
            ? sibling
            : sibling.querySelector("h1, h2, h3, h4, legend");
          if (heading) candidates.push(weakText(heading.textContent));
          sibling = sibling.previousElementSibling;
        }
      }
      const matched = candidates
        .map((candidate) => weakText(candidate).toLowerCase())
        .find((candidate) => /^(?:work )?experience(?:\s+\d+)?$|^employment history$|^education(?:\s+\d+)?$/.test(candidate));
      if (matched) return matched.includes("education") ? "Education" : "Experience";
      const root = current.getRootNode?.();
      current = current.parentElement || root?.host || null;
    }
    return "";
  }

  function profileRecordMetadata(element, recordSelections) {
    if (isWorkdayMyExperiencePage()) {
      for (const kind of ["work_experience", "education"]) {
        const group = workdayRecordGroups(kind).find((candidate) => candidate.contains(element));
        if (!group) continue;
        const assignedIndex = Number.parseInt(group.dataset.smartjobapplyProfileIndex || "", 10);
        const pageIndex = workdayRecordGroups(kind).indexOf(group);
        return {
          profile_record_kind: kind,
          profile_record_index: Number.isInteger(assignedIndex) && assignedIndex >= 0
            ? assignedIndex
            : pageIndex >= 0
              ? pageIndex
              : null,
        };
      }
    }
    const section = profileSectionContext(element);
    if (!section) return {};
    const profileRecordKind = section === "Education" ? "education" : "work_experience";
    const selectedIndex = recordSelections?.[profileRecordKind];
    const detectedIndex = profileRecordIndex(element, section);
    return {
      profile_record_kind: profileRecordKind,
      profile_record_index: Number.isInteger(selectedIndex) && selectedIndex >= 0
        ? selectedIndex
        : detectedIndex,
    };
  }

  function isUnmatchedWorkdayRecordField(element) {
    if (!isWorkdayMyExperiencePage()) return false;
    const group = [...workdayRecordGroups("work_experience"), ...workdayRecordGroups("education")]
      .find((candidate) => candidate.contains(element));
    return Boolean(group && group.dataset.smartjobapplyProfileIndex === undefined);
  }

  function profileRecordIndex(element, section) {
    const pattern = section === "Education"
      ? /\beducation\s+(\d+)\b/i
      : /\b(?:work\s+)?experience\s+(\d+)\b/i;
    let current = element;
    for (let depth = 0; current && depth < 12; depth += 1) {
      const assigned = Number.parseInt(current.dataset?.smartjobapplyProfileIndex || "", 10);
      if (Number.isInteger(assigned) && assigned >= 0) return assigned;
      if (isWorkdayMyExperiencePage() && current.getAttribute?.("role") === "group") {
        const groups = section === "Education"
          ? workdayRecordGroups("education")
          : workdayRecordGroups("work_experience");
        if (groups.includes(current)) return null;
      }
      const headingText = Array.from(current.querySelectorAll?.(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > legend") || [])
        .map((heading) => weakText(heading.textContent))
        .find((text) => pattern.test(text));
      const match = headingText?.match(pattern);
      if (match) return Math.max(Number.parseInt(match[1], 10) - 1, 0);
      const ariaMatch = weakText(current.getAttribute?.("aria-label")).match(pattern);
      if (ariaMatch) return Math.max(Number.parseInt(ariaMatch[1], 10) - 1, 0);
      const root = current.getRootNode?.();
      current = current.parentElement || root?.host || null;
    }
    return null;
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
    const questionTitle = fieldset?.querySelector(".ashby-application-form-question-title")?.textContent?.trim();
    if (questionTitle) return questionTitle;
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
    for (const phrase of [...ignoredPhrases].sort((left, right) => weakText(right).length - weakText(left).length)) {
      const cleaned = weakText(phrase);
      if (cleaned) text = text.replaceAll(cleaned, " ");
    }
    text = text
      .replace(/\bSelect\.\.\./gi, " ")
      .replace(/\bType your response\b/gi, " ")
      .replace(/\bDrop files here(?:\s+or)?\b/gi, " ")
      .replace(/\bSelect files\b/gi, " ")
      .replace(/\bSuccessfully Uploaded!?\b/gi, " ")
      .replace(/\bAttach resume\/cv\b/gi, " ")
      .replace(/\bAttach\b/gi, " ")
      .replace(/\bNo file chosen\b/gi, " ")
      .replace(/\bAnalyzing resume\.\.\.\b/gi, " ")
      .replace(/\bSuccess!\b/gi, " ")
      .replace(/\d+\s*items? selected\b/gi, " ")
      .replace(/\b(?:Minimized|Expanded)\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim();
    return text;
  }

  function canonicalUsState(value) {
    const raw = weakText(value);
    const normalizedName = raw.toLowerCase().replace(/[^a-z ]+/g, " ").replace(/\s+/g, " ").trim();
    if (US_STATE_CODE_BY_NAME[normalizedName]) return US_STATE_CODE_BY_NAME[normalizedName];
    const normalizedCode = raw.toUpperCase().replace(/\./g, "").trim();
    if (US_STATE_CODES.has(normalizedCode)) return normalizedCode;
    const decoratedCode = normalizedCode.match(/(?:^|[-–(]\s*)([A-Z]{2})(?:\s*[-–)]|$)/)?.[1] || "";
    if (US_STATE_CODES.has(decoratedCode)) return decoratedCode;
    return "";
  }

  function usStateNameForCode(code) {
    const wanted = weakText(code).toUpperCase();
    if (!wanted) return "";
    return Object.entries(US_STATE_CODE_BY_NAME).find(([, value]) => value === wanted)?.[0] || "";
  }

  function normalizeCountryText(value) {
    return weakText(value).toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  }

  function countriesEquivalent(left, right) {
    const aliases = new Set([
      "united states",
      "united states of america",
      "usa",
      "us",
      "u s",
      "u s a",
    ]);
    const leftNorm = normalizeCountryText(left);
    const rightNorm = normalizeCountryText(right);
    if (!leftNorm || !rightNorm) return false;
    if (leftNorm === rightNorm) return true;
    return aliases.has(leftNorm) && aliases.has(rightNorm);
  }

  function isGeoCountryField(label) {
    const text = weakText(label).toLowerCase();
    if (!/\bcountry\b/.test(text)) return false;
    return !/\b(phone|dial|calling)\b/.test(text);
  }

  function isGeoStateField(label) {
    return /\b(state|province)\b/i.test(weakText(label));
  }

  function isGeoField(label) {
    return isGeoCountryField(label) || isGeoStateField(label);
  }

  function countrySelectCandidates(value) {
    const wanted = weakText(value);
    if (!wanted) return [];
    if (!countriesEquivalent(wanted, "United States")) return [wanted];
    return uniqueTextValues([
      "United States of America",
      "United States",
      "USA",
      "US",
      "U.S.",
      "U.S.A.",
      wanted,
    ]);
  }

  function stateSelectCandidates(value) {
    const wanted = weakText(value);
    if (!wanted) return [];
    const code = canonicalUsState(wanted);
    const name = code
      ? usStateNameForCode(code).replace(/\b\w/g, (char) => char.toUpperCase())
      : "";
    return uniqueTextValues([
      wanted,
      name,
      code,
      name && code ? `${name} (${code})` : "",
    ]);
  }

  function geoSelectCandidates(label, values) {
    const source = Array.isArray(values) ? values : [values];
    if (isGeoCountryField(label)) {
      return uniqueTextValues(source.flatMap((value) => countrySelectCandidates(value)));
    }
    if (isGeoStateField(label)) {
      return uniqueTextValues(source.flatMap((value) => stateSelectCandidates(value)));
    }
    return uniqueTextValues(source);
  }

  function uniqueTextValues(values) {
    return Array.from(new Set((values || []).map((value) => weakText(value)).filter(Boolean)));
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

  async function fillReviewedFields(actions, run = null) {
    const setNativeValue = (element, value) => {
      const elementWindow = element.ownerDocument.defaultView || window;
      const prototype =
        isTag(element, "textarea")
          ? elementWindow.HTMLTextAreaElement.prototype
          : isTag(element, "select")
            ? elementWindow.HTMLSelectElement.prototype
            : elementWindow.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      if (descriptor?.set) descriptor.set.call(element, value);
      else element.value = value;
    };
    const dispatch = (element) => {
      const EventConstructor = element.ownerDocument.defaultView?.Event || Event;
      element.dispatchEvent(new EventConstructor("input", { bubbles: true }));
      element.dispatchEvent(new EventConstructor("change", { bubbles: true }));
      element.dispatchEvent(new EventConstructor("blur", { bubbles: true }));
    };
    const fillPhoneField = async (element, value) => {
      const digits = valueForField(element, value).replace(/\D/g, "");
      if (!digits) return false;
      const elementWindow = element.ownerDocument.defaultView || window;
      const InputEventConstructor = elementWindow.InputEvent || elementWindow.Event;
      element.focus();
      setNativeValue(element, "");
      element.dispatchEvent(new InputEventConstructor("input", { bubbles: true, inputType: "deleteContentBackward" }));
      for (const digit of digits) {
        setNativeValue(element, `${element.value}${digit}`);
        element.dispatchEvent(new InputEventConstructor("input", {
          bubbles: true,
          data: digit,
          inputType: "insertText",
        }));
        await new Promise((resolve) => setTimeout(resolve, 8));
      }
      element.dispatchEvent(new elementWindow.Event("change", { bubbles: true }));
      element.dispatchEvent(new elementWindow.Event("blur", { bubbles: true }));
      return true;
    };
    const visible = (element) => {
      const style = element.ownerDocument.defaultView?.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style?.display !== "none" && style?.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    const normalizeOptionText = (value) => weakText(value).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    const compactOptionText = (value) => normalizeOptionText(value).replace(/\s+/g, "");
    const baseOptionText = (value) => normalizeOptionText(weakText(value).replace(/\([^)]*\)/g, " "));
    const workdaySkillAliasLabels = (value) => {
      const normalized = normalizeOptionText(value);
      const compact = compactOptionText(value);
      const aliasesByKey = {
        azure: ["Microsoft Azure"],
        microsoftazure: ["Microsoft Azure"],
        java: ["Java (programming language)"],
        javaprogramminglanguage: ["Java (programming language)", "Java"],
        python: ["Python (programming language)"],
        pythonprogramminglanguage: ["Python (programming language)", "Python"],
        sql: ["Structured Query Language(SQL)", "Structured Query Language"],
        structuredquerylanguage: ["Structured Query Language(SQL)", "SQL"],
        structuredquerylanguagesql: ["Structured Query Language(SQL)", "SQL"],
        rl: ["Reinforcement Learning"],
        reinforcementlearning: ["Reinforcement Learning"],
      };
      return uniqueTextValues([
        value,
        baseOptionText(value),
        ...(aliasesByKey[normalized] || []),
        ...(aliasesByKey[compact] || []),
      ]);
    };
    const workdaySkillKeysFor = (value) => {
      const keys = [];
      for (const candidate of workdaySkillAliasLabels(value)) {
        const base = baseOptionText(candidate);
        keys.push(normalizeOptionText(candidate), compactOptionText(candidate), base, compactOptionText(base));
      }
      return new Set(keys.filter(Boolean));
    };
    const workdaySkillOptionMatches = (optionText, wantedText) => {
      const optionKeys = workdaySkillKeysFor(optionText);
      const wantedKeys = workdaySkillKeysFor(wantedText);
      return Array.from(wantedKeys).some((key) => optionKeys.has(key));
    };
    const workdaySkillSearchTerms = (value) => uniqueTextValues(workdaySkillAliasLabels(value));
    const workdaySkillRequestsForValue = (value) => {
      const original = weakText(value);
      if (/^git\s*(?:\/|&|\+|\band\b|\bor\b)\s*github$/i.test(original)) {
        return [
          { value: "Git", original },
          { value: "GitHub", original },
        ];
      }
      return [{ value: original, original }];
    };
    const binaryMeaning = (value) => {
      const normalized = weakText(value).toLowerCase();
      if (["no", "false"].includes(normalized) ||
          normalized.startsWith("no ") ||
          /\b(?:do not|don't|am not)\b/.test(normalized)) return false;
      if (["yes", "true"].includes(normalized) || normalized.startsWith("yes ")) return true;
      return null;
    };
    const optionMatches = (optionText, wantedText) => {
      const option = weakText(optionText).toLowerCase();
      const wanted = weakText(wantedText).toLowerCase();
      const optionState = canonicalUsState(optionText);
      const wantedState = canonicalUsState(wantedText);
      const optionBinary = binaryMeaning(optionText);
      const wantedBinary = binaryMeaning(wantedText);
      return option === wanted ||
        (optionBinary !== null && wantedBinary !== null && optionBinary === wantedBinary) ||
        Boolean(optionState && wantedState && optionState === wantedState) ||
        countriesEquivalent(optionText, wantedText) ||
        option.includes(wanted);
    };
    const exactOptionMatch = (optionText, wantedText) => {
      return normalizeOptionText(optionText) === normalizeOptionText(wantedText);
    };
    const strictWorkdayCatalogQuestion = (element) => {
      if (state.provider !== "workday" || !element) return false;
      const fieldId = element.dataset.smartjobapplyFieldId || element.id || "";
      const question = workdayQuestionForField(fieldId);
      if (question?.profile_record_kind !== "education") return false;
      const label = weakText(question.label || labelFor(element)).toLowerCase();
      return /\b(?:school|university|institution|field of study|major)\b/.test(label);
    };
    const workdayCatalogOptionMatches = (optionText, wantedText, element) => {
      if (strictWorkdayCatalogQuestion(element)) return exactOptionMatch(optionText, wantedText);
      return optionMatches(optionText, wantedText);
    };
    const currentControlValue = (element) => {
      const selected = selectedMultiValues(element);
      if (selected.length) return selected[0];
      const singleValue = selectedSingleValue(element);
      if (singleValue) return singleValue;
      return weakText(
        element.getAttribute("aria-valuetext") ||
        element.value ||
        element.textContent,
      );
    };
    const controlAlreadyMatches = (element, values) => {
      const current = currentControlValue(element);
      if (!current || /^(select one|select|choose|select\.\.\.)$/i.test(current)) return false;
      return values.some((value) => workdayCatalogOptionMatches(current, value, element) || countriesEquivalent(current, value));
    };
    const selectNativeOption = (element, value) => {
      const option = Array.from(element.options).find((candidate) => optionMatches(candidate.textContent, value));
      if (!option) return false;
      setNativeValue(element, option.value);
      dispatch(element);
      return true;
    };
    const selectRadioOption = async (fieldId, value) => {
      const radios = queryAllFromPage(`input[type='radio'][name='${cssEscape(fieldId)}']`)
        .filter((candidate) => visible(candidate));
      const wanted = weakText(value);
      const radio = radios.find((candidate) => {
        const label = candidate.labels?.length
          ? Array.from(candidate.labels).map((item) => item.textContent.trim()).join(" ")
          : candidate.value || "";
        return exactOptionMatch(label, wanted);
      });
      if (!radio) return false;
      radio.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      if (radio.checked) return true;
      const label = radio.labels?.[0];
      if (!label) return false;
      label.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      return radio.checked;
    };
    const selectCustomYesNoOption = async (element, value) => {
      const wanted = weakText(value);
      const button = customYesNoButtons(element)
        .find((candidate) => optionMatches(candidate.textContent, wanted));
      if (!button) return false;
      button.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      return isSelectedOptionButton(button);
    };
    const firePointerSequence = (element) => {
      const elementWindow = element.ownerDocument.defaultView || window;
      const PointerEventConstructor = elementWindow.PointerEvent || elementWindow.MouseEvent;
      element.dispatchEvent(new PointerEventConstructor("pointerdown", {
        bubbles: true,
        cancelable: true,
        view: elementWindow,
        pointerType: "mouse",
        button: 0,
      }));
      for (const eventName of ["mousedown", "mouseup"]) {
        element.dispatchEvent(new elementWindow.MouseEvent(eventName, {
          bubbles: true,
          cancelable: true,
          view: elementWindow,
          button: 0,
        }));
      }
      element.click();
    };
    const workdayPromptId = (element) => weakText(
      element.getAttribute("data-uxi-multiselect-id") ||
      element.closest("[data-uxi-widget-type='multiselect']")?.getAttribute("data-uxi-element-id") ||
      element.closest("[data-automation-id='multiSelectContainer']")?.id,
    );
    const workdayPromptButtonFor = (element) => {
      const promptId = workdayPromptId(element);
      if (!promptId) return null;
      return queryAllFromPage("[data-automation-id='promptSearchButton'], [data-automation-id='promptIcon']")
        .find((candidate) => candidate.getAttribute("data-uxi-multiselect-id") === promptId) || null;
    };
    const activeWorkdayListFor = (element) => {
      const promptId = workdayPromptId(element);
      if (!promptId) return null;
      return queryAllFromPage("[data-automation-id='activeListContainer'][role='listbox']")
        .filter((candidate) => visible(candidate))
        .find((candidate) => (
          candidate.closest("[data-associated-widget]")?.getAttribute("data-associated-widget") === promptId ||
          Array.from(candidate.querySelectorAll("[data-uxi-multiselect-id]"))
            .some((child) => child.getAttribute("data-uxi-multiselect-id") === promptId)
        )) || null;
    };
    const setWorkdaySearchValue = async (element, value) => {
      const elementWindow = element.ownerDocument.defaultView || window;
      const InputEventConstructor = elementWindow.InputEvent || elementWindow.Event;
      element.focus();
      setNativeValue(element, "");
      element.dispatchEvent(new InputEventConstructor("input", {
        bubbles: true,
        inputType: "deleteContentBackward",
      }));
      let current = "";
      for (const character of String(value)) {
        element.dispatchEvent(new elementWindow.KeyboardEvent("keydown", {
          key: character,
          bubbles: true,
          cancelable: true,
        }));
        current += character;
        setNativeValue(element, current);
        element.dispatchEvent(new InputEventConstructor("input", {
          bubbles: true,
          data: character,
          inputType: "insertText",
        }));
        element.dispatchEvent(new elementWindow.KeyboardEvent("keyup", {
          key: character,
          bubbles: true,
          cancelable: true,
        }));
      }
      await new Promise((resolve) => setTimeout(resolve, 40));
    };
    const closeWorkdayPrompt = async (element) => {
      if (activeWorkdayListFor(element)) {
        await setWorkdaySearchValue(element, "");
        const dismissTarget = element.closest("[role='group'], section")
          ?.querySelector("h1, h2, h3, h4, h5, h6, legend");
        if (dismissTarget) firePointerSequence(dismissTarget);
        else element.blur();
        await new Promise((resolve) => setTimeout(resolve, 180));
        if (activeWorkdayListFor(element)) {
          workdayPromptButtonFor(element)?.click();
          await new Promise((resolve) => setTimeout(resolve, 120));
        }
      } else {
        await setWorkdaySearchValue(element, "");
      }
    };
    const openWorkdayPrompt = async (element, query = "") => {
      if (activeWorkdayListFor(element)) await closeWorkdayPrompt(element);
      const foreignLists = queryAllFromPage("[data-automation-id='activeListContainer'][role='listbox']")
        .filter((candidate) => visible(candidate));
      for (const foreignList of foreignLists) {
        const foreignPromptId = weakText(
          foreignList.closest("[data-associated-widget]")?.getAttribute("data-associated-widget"),
        );
        const foreignInput = foreignPromptId
          ? queryAllFromPage("[data-uxi-widget-type='selectinput'][data-uxi-multiselect-id]")
            .find((candidate) => candidate.getAttribute("data-uxi-multiselect-id") === foreignPromptId)
          : null;
        if (foreignInput && foreignInput !== element) await closeWorkdayPrompt(foreignInput);
      }
      await setWorkdaySearchValue(element, "");
      const button = workdayPromptButtonFor(element);
      if (!button) return null;
      button.click();
      const deadline = Date.now() + 1600;
      while (Date.now() < deadline) {
        const list = activeWorkdayListFor(element);
        if (list) {
          if (query) {
            await setWorkdaySearchValue(element, query);
            await new Promise((resolve) => setTimeout(resolve, 120));
          }
          return activeWorkdayListFor(element) || list;
        }
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return null;
    };
    const workdayOptionLabel = (option) => weakText(
      option.querySelector("[data-automation-id='promptOption']")?.getAttribute("data-automation-label") ||
      option.querySelector("[data-automation-id='promptOption']")?.textContent ||
      option.textContent,
    );
    const workdayOptionsForPrompt = (list, promptId) => Array.from(list.querySelectorAll("[role='option'], [data-automation-id='menuItem']"))
      .filter((option) => {
        const owner = option.querySelector("[data-uxi-multiselect-id]");
        return !owner || owner.getAttribute("data-uxi-multiselect-id") === promptId;
      });
    const visibleWorkdayOptionLabels = (list, promptId) => workdayOptionsForPrompt(list, promptId)
      .map((option) => workdayOptionLabel(option))
      .filter((label) => label && !/^no items?\.?$/i.test(label));
    const workdayPromptShowsNoItems = (list, promptId) => {
      const labels = list ? workdayOptionsForPrompt(list, promptId).map((option) => workdayOptionLabel(option)).filter(Boolean) : [];
      return labels.length > 0 && labels.every((label) => /^no items?\.?$/i.test(label));
    };
    const matchingWorkdaySkillOption = (list, promptId, value) => workdayOptionsForPrompt(list, promptId)
      .find((option) => {
        const label = workdayOptionLabel(option);
        return label && !/^no items?\.?$/i.test(label) && workdaySkillOptionMatches(label, value);
      });
    const workdayCatalogSearchTerms = (value) => {
      const ignored = new Set(["university", "college", "school", "institute", "institution", "engineering"]);
      const tokens = weakText(value)
        .split(/[^a-z0-9]+/i)
        .filter((token) => token.length >= 5 && !ignored.has(token.toLowerCase()));
      return uniqueTextValues([value, ...tokens]);
    };
    const selectWorkdayPromptCandidate = async (element, values) => {
      const promptId = workdayPromptId(element);
      if (!promptId) return false;
      const beforeCount = selectedCountForControl(element);
      const filteredSearches = values.flatMap((value) => (
        workdayCatalogSearchTerms(value).map((query) => ({ value, query }))
      ));
      for (const { value, query } of filteredSearches) {
        if (run?.cancelled) return false;
        const filteredList = await openWorkdayPrompt(element, query);
        if (!filteredList) continue;
        let filteredOption = null;
        const filteredDeadline = Date.now() + 700;
        while (Date.now() < filteredDeadline) {
          if (run?.cancelled) {
            await closeWorkdayPrompt(element);
            return false;
          }
          const currentList = activeWorkdayListFor(element);
          filteredOption = currentList
            ? workdayOptionsForPrompt(currentList, promptId)
              .find((option) => workdayCatalogOptionMatches(workdayOptionLabel(option), value, element))
            : null;
          if (filteredOption || (currentList && workdayPromptShowsNoItems(currentList, promptId))) break;
          await new Promise((resolve) => setTimeout(resolve, 80));
        }
        if (!filteredOption) {
          const elementWindow = element.ownerDocument.defaultView || window;
          for (const eventName of ["keydown", "keypress", "keyup"]) {
            element.dispatchEvent(new elementWindow.KeyboardEvent(eventName, {
              key: "Enter",
              code: "Enter",
              bubbles: true,
              cancelable: true,
            }));
          }
          const committedSearchStartedAt = Date.now();
          const committedSearchDeadline = committedSearchStartedAt + 5000;
          while (Date.now() < committedSearchDeadline) {
            if (run?.cancelled) {
              await closeWorkdayPrompt(element);
              return false;
            }
            const currentList = activeWorkdayListFor(element);
            filteredOption = currentList
              ? workdayOptionsForPrompt(currentList, promptId)
                .find((option) => workdayCatalogOptionMatches(workdayOptionLabel(option), value, element))
              : null;
            if (filteredOption) break;
            if (currentList && workdayPromptShowsNoItems(currentList, promptId) && Date.now() - committedSearchStartedAt > 900) break;
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        }
        if (filteredOption) {
          const optionTarget = filteredOption.querySelector("[data-automation-id='promptOption']") ||
            filteredOption.querySelector("[data-automation-id='promptLeafNode']") ||
            filteredOption;
          firePointerSequence(optionTarget);
          await new Promise((resolve) => setTimeout(resolve, 700));
          const currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
          const committed = controlAlreadyMatches(currentElement, [value, workdayOptionLabel(filteredOption)]) ||
            selectedCountForControl(currentElement) > beforeCount;
          await closeWorkdayPrompt(currentElement);
          if (committed) return true;
        } else {
          await closeWorkdayPrompt(element);
        }
      }
      const list = await openWorkdayPrompt(element);
      if (!list) return false;
      const catalog = [];
      const seen = new Set();
      list.scrollTop = 0;
      list.dispatchEvent(new (element.ownerDocument.defaultView?.Event || Event)("scroll", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 60));
      for (let pass = 0; pass < 48; pass += 1) {
        if (run?.cancelled) {
          await closeWorkdayPrompt(element);
          return false;
        }
        for (const option of workdayOptionsForPrompt(list, promptId)) {
          const label = workdayOptionLabel(option);
          const key = comparableValue(label);
          if (label && !/^no items?\.?$/i.test(label) && !seen.has(key)) {
            seen.add(key);
            catalog.push({ label, scrollTop: list.scrollTop });
          }
        }
        const maximum = Math.max(list.scrollHeight - list.clientHeight, 0);
        if (list.scrollTop >= maximum) break;
        const next = Math.min(list.scrollTop + Math.max(list.clientHeight - 64, 160), maximum);
        if (next === list.scrollTop) break;
        list.scrollTop = next;
        list.dispatchEvent(new (element.ownerDocument.defaultView?.Event || Event)("scroll", { bubbles: true }));
        await new Promise((resolve) => setTimeout(resolve, 60));
      }
      const selectedCandidate = values
        .map((value) => {
          const match = catalog.find((entry) => workdayCatalogOptionMatches(entry.label, value, element));
          return match ? { value, match } : null;
        })
        .find(Boolean);
      if (!selectedCandidate) {
        await closeWorkdayPrompt(element);
        return false;
      }
      list.scrollTop = selectedCandidate.match.scrollTop;
      list.dispatchEvent(new (element.ownerDocument.defaultView?.Event || Event)("scroll", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 80));
      const option = workdayOptionsForPrompt(list, promptId)
        .find((candidate) => workdayCatalogOptionMatches(workdayOptionLabel(candidate), selectedCandidate.match.label, element));
      if (!option) {
        await closeWorkdayPrompt(element);
        return false;
      }
      const optionTarget = option.querySelector("[data-automation-id='promptOption']") ||
        option.querySelector("[data-automation-id='promptLeafNode']") ||
        option;
      firePointerSequence(optionTarget);
      await new Promise((resolve) => setTimeout(resolve, 700));
      let currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
      let committed = controlAlreadyMatches(currentElement, [selectedCandidate.value, selectedCandidate.match.label]) ||
        selectedCountForControl(currentElement) > beforeCount;
      if (!committed && optionTarget !== option) {
        const optionLeaf = option.querySelector("[data-automation-id='promptLeafNode']");
        if (optionLeaf && optionLeaf !== optionTarget) {
          firePointerSequence(optionLeaf);
          await new Promise((resolve) => setTimeout(resolve, 400));
          currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
          committed = controlAlreadyMatches(currentElement, [selectedCandidate.value, selectedCandidate.match.label]) ||
            selectedCountForControl(currentElement) > beforeCount;
        }
      }
      await closeWorkdayPrompt(currentElement);
      return committed;
    };
    const commitWorkdayFreeformValue = async (element, value, promptAlreadyOpen = false) => {
      const beforeCount = selectedCountForControl(element);
      const list = promptAlreadyOpen
        ? activeWorkdayListFor(element)
        : await openWorkdayPrompt(element, value);
      if (!list) return false;
      const elementWindow = element.ownerDocument.defaultView || window;
      for (const eventName of ["keydown", "keypress", "keyup"]) {
        element.dispatchEvent(new elementWindow.KeyboardEvent(eventName, {
          key: "Enter",
          code: "Enter",
          bubbles: true,
          cancelable: true,
        }));
      }
      await new Promise((resolve) => setTimeout(resolve, 260));
      const currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
      const committed = selectedMultiValues(currentElement)
        .some((selected) => exactOptionMatch(selected, value)) ||
        selectedCountForControl(currentElement) > beforeCount;
      await closeWorkdayPrompt(currentElement);
      return committed;
    };
    const selectWorkdaySkillValue = async (element, value) => {
      const promptId = workdayPromptId(element);
      if (!promptId) return false;
      const beforeCount = selectedCountForControl(element);
      const elementWindow = element.ownerDocument.defaultView || window;
      const pressEnterForWorkdaySearch = () => {
        for (const eventName of ["keydown", "keypress", "keyup"]) {
          element.dispatchEvent(new elementWindow.KeyboardEvent(eventName, {
            key: "Enter",
            code: "Enter",
            bubbles: true,
            cancelable: true,
          }));
        }
      };
      for (const query of workdaySkillSearchTerms(value)) {
        const list = await openWorkdayPrompt(element, query);
        if (!list) continue;
        let exactOption = null;
        let sawRealOptions = false;
        let sawNoItems = false;
        const queryStartedAt = Date.now();
        let deadline = Date.now() + 1800;
        while (Date.now() < deadline) {
          const currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
          if (selectedMultiValues(currentElement).some((selected) => workdaySkillOptionMatches(selected, value))) {
            await closeWorkdayPrompt(currentElement);
            return true;
          }
          const currentList = activeWorkdayListFor(element);
          if (currentList) {
            sawRealOptions = visibleWorkdayOptionLabels(currentList, promptId).length > 0 || sawRealOptions;
            sawNoItems = workdayPromptShowsNoItems(currentList, promptId) || sawNoItems;
            exactOption = matchingWorkdaySkillOption(currentList, promptId, value);
          }
          if (exactOption) break;
          if (sawNoItems && Date.now() - queryStartedAt > 350) break;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        if (!exactOption && (!sawRealOptions || sawNoItems)) {
          pressEnterForWorkdaySearch();
          deadline = Date.now() + 5000;
          const enterStartedAt = Date.now();
          while (Date.now() < deadline) {
            const currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
            if (selectedMultiValues(currentElement).some((selected) => workdaySkillOptionMatches(selected, value))) {
              await closeWorkdayPrompt(currentElement);
              return true;
            }
            const currentList = activeWorkdayListFor(element);
            exactOption = currentList ? matchingWorkdaySkillOption(currentList, promptId, value) : null;
            if (exactOption) break;
            if (currentList && workdayPromptShowsNoItems(currentList, promptId) && Date.now() - enterStartedAt > 900) break;
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        }
        if (!exactOption) {
          await closeWorkdayPrompt(element);
          continue;
        }
        const optionTarget = exactOption.querySelector("[data-automation-id='promptOption']") ||
          exactOption.querySelector("[data-automation-id='promptLeafNode']") ||
          exactOption;
        firePointerSequence(optionTarget);
        await new Promise((resolve) => setTimeout(resolve, 700));
        let currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
        let committed = selectedMultiValues(currentElement)
          .some((selected) => workdaySkillOptionMatches(selected, value)) ||
          selectedCountForControl(currentElement) > beforeCount;
        if (!committed) {
          const optionLeaf = exactOption.querySelector("[data-automation-id='promptLeafNode']");
          if (optionLeaf && optionLeaf !== optionTarget) {
            firePointerSequence(optionLeaf);
            await new Promise((resolve) => setTimeout(resolve, 700));
            currentElement = findField(element.dataset.smartjobapplyFieldId || element.id) || element;
            committed = selectedMultiValues(currentElement)
              .some((selected) => workdaySkillOptionMatches(selected, value)) ||
              selectedCountForControl(currentElement) > beforeCount;
          }
        }
        await closeWorkdayPrompt(currentElement);
        if (committed) return true;
      }
      await closeWorkdayPrompt(element);
      return false;
    };
    const waitForOption = async (value, exactOnly = false) => {
      const deadline = Date.now() + 900;
      while (Date.now() < deadline) {
        const roleOptions = queryAllFromPage("[role='option'], [data-automation-id='promptOption']")
          .filter((candidate) => visible(candidate));
        const options = roleOptions.length
          ? roleOptions
          : queryAllFromPage("[id*='option'], [class*='option']")
            .filter((candidate) => visible(candidate) && candidate.getAttribute("role") !== "listbox");
        const matched = options.find((candidate) => (
          exactOnly ? exactOptionMatch(candidate.textContent, value) : optionMatches(candidate.textContent, value)
        ));
        if (matched) return matched;
        await new Promise((resolve) => setTimeout(resolve, 60));
      }
      return null;
    };
    const workdayStandardOptionLabel = (option) => weakText(
      option.getAttribute("aria-label") ||
      option.getAttribute("data-automation-label") ||
      option.querySelector("[data-automation-label]")?.getAttribute("data-automation-label") ||
      option.textContent,
    );
    const workdayStandardOptionMatches = (optionText, wantedText) => {
      const optionBinary = binaryMeaning(optionText);
      const wantedBinary = binaryMeaning(wantedText);
      return exactOptionMatch(optionText, wantedText) ||
        (optionBinary !== null && wantedBinary !== null && optionBinary === wantedBinary) ||
        countriesEquivalent(optionText, wantedText) ||
        Boolean(canonicalUsState(optionText) && canonicalUsState(optionText) === canonicalUsState(wantedText));
    };
    const visibleStandardWorkdayLists = () => queryAllFromPage("[role='listbox']")
      .filter((candidate) => visible(candidate) && !candidate.closest("#smartjobapply-panel"));
    const closeStandardWorkdayList = async (button) => {
      const currentButton = findField(button.dataset.smartjobapplyFieldId || button.id) || button;
      if (currentButton.getAttribute("aria-expanded") === "true") {
        currentButton.click();
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    };
    const selectWorkdayStandardOption = async (fieldId, rawValues) => {
      const values = uniqueTextValues(rawValues);
      let lastStatus = "option_unavailable";
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const button = findField(fieldId);
        if (!button || !isTag(button, "button")) return { ok: false, status: "field_not_found" };
        if (controlAlreadyMatches(button, values)) return { ok: true, status: "already_selected" };
        const beforeLists = new Set(visibleStandardWorkdayLists());
        button.scrollIntoView({ block: "center", inline: "nearest" });
        button.focus();
        button.click();
        const openDeadline = Date.now() + 2500;
        let list = null;
        while (Date.now() < openDeadline) {
          const visibleLists = visibleStandardWorkdayLists();
          list = visibleLists.find((candidate) => !beforeLists.has(candidate)) ||
            (button.getAttribute("aria-expanded") === "true" && visibleLists.length === 1 ? visibleLists[0] : null);
          if (list) break;
          await new Promise((resolve) => setTimeout(resolve, 60));
        }
        if (!list) {
          lastStatus = "menu_not_opened";
          await closeStandardWorkdayList(button);
          continue;
        }
        const options = Array.from(list.querySelectorAll("[role='option']"))
          .filter((option) => visible(option) && option.getAttribute("aria-disabled") !== "true" && !option.hasAttribute("disabled"));
        const selected = values
          .map((value) => ({
            value,
            option: options.find((candidate) => workdayStandardOptionMatches(workdayStandardOptionLabel(candidate), value)),
          }))
          .find((candidate) => candidate.option);
        if (!selected) {
          lastStatus = "option_unavailable";
          await closeStandardWorkdayList(button);
          continue;
        }
        selected.option.scrollIntoView({ block: "nearest", inline: "nearest" });
        selected.option.click();
        const commitDeadline = Date.now() + 1800;
        while (Date.now() < commitDeadline) {
          const currentButton = findField(fieldId);
          if (currentButton && controlAlreadyMatches(currentButton, [selected.value, workdayStandardOptionLabel(selected.option)])) {
            return { ok: true, status: "selected" };
          }
          await new Promise((resolve) => setTimeout(resolve, 60));
        }
        lastStatus = "selection_not_committed";
        await closeStandardWorkdayList(button);
      }
      return { ok: false, status: lastStatus };
    };
    const selectCustomOption = async (element, value, exactOnly = false) => {
      const target = element.closest("[role='combobox'], .select-shell, [class*='select']") || element;
      element.scrollIntoView({ block: "center", inline: "nearest" });
      firePointerSequence(target);
      const matchesObserved = (observed) => (
        exactOnly ? exactOptionMatch(observed, value) : optionMatches(observed, value)
      );
      if (isTag(element, "button")) {
        const option = await waitForOption(value, exactOnly);
        if (!option) return false;
        firePointerSequence(option);
        dispatch(element);
        await new Promise((resolve) => setTimeout(resolve, 120));
        return matchesObserved(element.textContent) || matchesObserved(element.getAttribute("aria-valuetext"));
      }
      element.focus();
      setNativeValue(element, String(value));
      const elementWindow = element.ownerDocument.defaultView || window;
      element.dispatchEvent(new elementWindow.Event("input", { bubbles: true }));
      element.dispatchEvent(new elementWindow.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
      const option = await waitForOption(value, exactOnly);
      if (!option) {
        setNativeValue(element, "");
        element.dispatchEvent(new elementWindow.Event("input", { bubbles: true }));
        return false;
      }
      firePointerSequence(option);
      dispatch(element);
      await new Promise((resolve) => setTimeout(resolve, 120));
      return selectedMultiValues(element).some((selected) => matchesObserved(selected)) ||
        matchesObserved(selectedSingleValue(element)) ||
        matchesObserved(element.value) ||
        matchesObserved(element.getAttribute("aria-valuetext")) ||
        selectedCountForControl(element) > 0;
    };
    const selectFirstCustomCandidate = async (fieldId, values) => {
      const workdayElement = findField(fieldId);
      const label = workdayElement ? labelFor(workdayElement) : "";
      const geoField = isGeoField(label);
      const candidates = geoSelectCandidates(label, values);
      if (workdayElement && controlAlreadyMatches(workdayElement, candidates)) {
        return true;
      }
      if (state.provider === "workday" && workdayElement && workdayPromptId(workdayElement)) {
        return selectWorkdayPromptCandidate(workdayElement, candidates);
      }
      const exactOnly = state.provider === "workday" && !geoField;
      for (const value of candidates) {
        const currentElement = findField(fieldId);
        if (currentElement && await selectCustomOption(currentElement, value, exactOnly)) {
          return true;
        }
      }
      return false;
    };
    const selectMultiValue = async (fieldId, value) => {
      const element = findField(fieldId);
      if (!element || !isCustomSelectInput(element)) return false;
      const alreadySelected = selectedMultiValues(element)
        .some((selected) => state.provider === "workday" ? workdaySkillOptionMatches(selected, value) : exactOptionMatch(selected, value));
      if (alreadySelected) return true;
      const beforeCount = selectedCountForControl(element);
      if (state.provider === "workday" && workdayPromptId(element)) {
        return selectWorkdaySkillValue(element, value);
      }
      element.focus();
      setNativeValue(element, "");
      element.dispatchEvent(new (element.ownerDocument.defaultView?.Event || Event)("input", { bubbles: true }));
      setNativeValue(element, String(value));
      const elementWindow = element.ownerDocument.defaultView || window;
      element.dispatchEvent(new elementWindow.Event("input", { bubbles: true }));
      const option = await waitForOption(value, true);
      if (option) {
        firePointerSequence(option);
      } else {
        element.dispatchEvent(new elementWindow.KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          bubbles: true,
          cancelable: true,
        }));
      }
      await new Promise((resolve) => setTimeout(resolve, 160));
      const currentElement = findField(fieldId) || element;
      const selected = selectedMultiValues(currentElement)
        .some((item) => exactOptionMatch(item, value));
      const countIncreased = selectedCountForControl(currentElement) > beforeCount;
      if (!selected && !countIncreased) {
        setNativeValue(currentElement, "");
        currentElement.dispatchEvent(new elementWindow.Event("input", { bubbles: true }));
        return false;
      }
      dispatch(currentElement);
      return true;
    };
    let filled = 0;
    let skipped = 0;
    const failedValues = [];
    const fieldOutcomes = {};
    const blockedRecords = new Set();
    const failAction = (action, status, element, value) => {
      fieldOutcomes[action.field_id] = status;
      failedValues.push({
        field_id: action.field_id,
        field: weakText(element ? labelFor(element) : "") || action.field_id,
        value: Array.isArray(value) ? value.join(" → ") : weakText(value),
        unavailable_count: Array.isArray(value) ? value.length : 1,
        status,
      });
      const question = workdayQuestionForField(action.field_id);
      const recordKey = workdayRecordKeyForField(action.field_id);
      if (recordKey && question?.required && !question.date_component) {
        blockedRecords.add(recordKey);
      }
    };
    const comparableValue = (value) => weakText(value).toLowerCase().replace(/[^a-z0-9]+/g, "");
    const setWorkdaySpinbuttonValue = async (element, value) => {
      const elementWindow = element.ownerDocument.defaultView || window;
      const InputEventConstructor = elementWindow.InputEvent || elementWindow.Event;
      element.focus();
      setNativeValue(element, "");
      element.dispatchEvent(new InputEventConstructor("input", {
        bubbles: true,
        inputType: "deleteContentBackward",
      }));
      setNativeValue(element, String(value));
      element.dispatchEvent(new InputEventConstructor("input", {
        bubbles: true,
        data: String(value),
        inputType: "insertText",
      }));
      element.dispatchEvent(new elementWindow.Event("change", { bubbles: true }));
      element.dispatchEvent(new elementWindow.Event("blur", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 180));
    };
    const fillScalarValue = async (element, value, action) => {
      const expected = valueForField(element, value);
      const question = workdayQuestionForField(action.field_id);
      if (state.provider === "workday" && question?.date_component && element.getAttribute("role") === "spinbutton") {
        await setWorkdaySpinbuttonValue(element, expected);
      } else {
        element.focus();
        setNativeValue(element, expected);
      }
      dispatch(element);
      await new Promise((resolve) => setTimeout(resolve, state.provider === "workday" ? 140 : 20));
      if (state.provider !== "workday" || !isTag(element, "input")) return true;
      if (question?.date_component) {
        const observed = element.value || element.getAttribute("aria-valuetext") || "";
        const observedNumber = Number.parseInt(observed, 10);
        const expectedNumber = Number.parseInt(expected, 10);
        return Number.isInteger(observedNumber) && Number.isInteger(expectedNumber) && observedNumber === expectedNumber;
      }
      return comparableValue(element.value) === comparableValue(expected) ||
        comparableValue(element.getAttribute("aria-valuetext")) === comparableValue(expected);
    };
    for (const action of actions) {
      if (action.action === "skip" || action.action === "upload" || action.value === null) {
        skipped += 1;
        continue;
      }
      if (run?.cancelled) break;
      updateAutofillRun(run, action);
      try {
        const recordKey = workdayRecordKeyForField(action.field_id);
        if (recordKey && blockedRecords.has(recordKey)) {
          skipped += 1;
          fieldOutcomes[action.field_id] = "record_aborted";
          continue;
        }
        const element = findField(action.field_id);
        if (!element) {
          skipped += 1;
          failAction(action, "field_not_found", null, action.value);
          continue;
        }
      if (action.action === "select" && customYesNoButtons(element).length) {
        if (!(await selectCustomYesNoOption(element, action.value))) {
          skipped += 1;
          failAction(action, "option_unavailable", element, action.value);
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select" && isTag(element, "select")) {
        const values = Array.isArray(action.value) ? action.value : multiValuesForField(element, action.value);
        if (!values.some((value) => selectNativeOption(element, value))) {
          skipped += 1;
          failAction(action, "option_unavailable", element, values);
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select" && isTag(element, "input") && element.type === "radio") {
        if (!(await selectRadioOption(action.field_id, action.value))) {
          skipped += 1;
          failAction(action, "option_unavailable", element, action.value);
          continue;
        }
        filled += 1;
        continue;
      }
      if (
        action.action === "select"
        && state.provider === "workday"
        && isTag(element, "button")
        && workdayQuestionForField(action.field_id)?.control_kind === "custom_select"
        && !workdayPromptId(element)
      ) {
        const values = Array.isArray(action.value) ? action.value : multiValuesForField(element, action.value);
        let result;
        try {
          result = await selectWorkdayStandardOption(action.field_id, values);
        } catch {
          result = { ok: false, status: "selection_not_committed" };
        }
        if (!result.ok) {
          skipped += 1;
          failAction(action, result.status, element, values);
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select" && (isCustomSelectInput(element) || isCustomSelectButton(element))) {
        const values = Array.isArray(action.value) ? action.value : multiValuesForField(element, action.value);
        if (!(await selectFirstCustomCandidate(action.field_id, values))) {
          skipped += 1;
          failAction(action, "option_unavailable", element, values);
          continue;
        }
        filled += 1;
        continue;
      }
      if (action.action === "select_many") {
        const values = Array.isArray(action.value) ? action.value : multiValuesForField(element, action.value);
        if (isTag(element, "input") && element.type === "checkbox") {
          const result = selectNativeCheckboxGroup(action.field_id, values);
          if (!result.ok) {
            skipped += 1;
            failAction(action, "option_unavailable", element, result.unavailable);
          } else {
            filled += 1;
          }
          continue;
        }
        const requests = state.provider === "workday"
          ? values.flatMap((value) => workdaySkillRequestsForValue(value))
          : values.map((value) => ({ value, original: value }));
        let selectedCount = 0;
        const unavailable = new Set();
        const retryRequests = [];
        for (const request of requests) {
          if (run?.cancelled) break;
          if (await selectMultiValue(action.field_id, request.value)) selectedCount += 1;
          else retryRequests.push(request);
        }
        if (!run?.cancelled && state.provider === "workday" && retryRequests.length) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          for (const request of retryRequests) {
            if (run?.cancelled) break;
            if (await selectMultiValue(action.field_id, request.value)) selectedCount += 1;
            else unavailable.add(request.original);
          }
        } else if (!run?.cancelled) {
          retryRequests.forEach((request) => unavailable.add(request.original));
        }
        if (run?.cancelled) {
          if (selectedCount) filled += 1;
          continue;
        }
        if (selectedCount) filled += 1;
        if (selectedCount < requests.length) {
          skipped += 1;
          failAction(action, selectedCount ? "some_values_unavailable" : "option_unavailable", element, Array.from(unavailable));
        }
        continue;
      }
      if (action.action === "check" && isTag(element, "input")) {
        element.checked = Boolean(action.value);
        dispatch(element);
        filled += 1;
        continue;
      }
      if (element.getAttribute("contenteditable") === "true") {
        element.textContent = valueForField(element, action.value);
        dispatch(element);
        filled += 1;
        continue;
      }
      if (isPhoneField(element)) {
        if (!(await fillPhoneField(element, action.value))) {
          skipped += 1;
          failAction(action, "invalid_phone_value", element, action.value);
          continue;
        }
        filled += 1;
        continue;
      }
      if (!(await fillScalarValue(element, action.value, action))) {
        skipped += 1;
        failAction(action, "value_not_committed", element, action.value);
        continue;
      }
        filled += 1;
      } finally {
        updateAutofillRun(run, action, true);
      }
    }
    return { filled, skipped, failed_values: failedValues, field_outcomes: fieldOutcomes };
  }

  function valueForField(element, value) {
    const textValue = String(value);
    if (!isPhoneField(element)) return textValue;
    const dialingCode = selectedDialingCodeNear(element);
    if (!dialingCode) return textValue;
    const digits = dialingCode.replace(/\D/g, "");
    if (!digits) return textValue;
    return textValue.replace(new RegExp(`^\\s*\\+\\s*${digits}(?:[\\s().-]*)`), "").trim();
  }

  function multiValuesForField(element, value) {
    const textValue = String(value);
    const label = weakText(labelFor(element)).toLowerCase();
    if (!/\bskills?\b/.test(label) || !textValue.includes(";")) return [textValue];
    return textValue.split(";").map((item) => item.trim()).filter(Boolean);
  }

  function isPhoneField(element) {
    if (!isTag(element, "input")) return false;
    const autocomplete = weakText(element.getAttribute("autocomplete")).toLowerCase();
    const label = weakText(labelFor(element)).toLowerCase();
    if (/country phone code|phone country code|dial(?:ing)? code|phone extension|phone device type/.test(label)) return false;
    return element.type === "tel" || autocomplete.split(" ").some((token) => token.startsWith("tel")) || /\b(phone|mobile|telephone)\b/.test(label);
  }

  function selectedDialingCodeNear(element) {
    let container = element.parentElement;
    for (let depth = 0; container && depth < 6; depth += 1) {
      const candidates = Array.from(container.querySelectorAll("input, select, button, [role='combobox']"));
      for (const candidate of candidates) {
        if (candidate === element || candidate.closest("#smartjobapply-panel")) continue;
        const autocomplete = weakText(candidate.getAttribute("autocomplete")).toLowerCase();
        const label = explicitControlLabel(candidate);
        if (autocomplete !== "tel-country-code" && !isDialingCodeLabel(label)) continue;
        const dialingCode = dialingCodeFromControl(candidate);
        if (dialingCode) return dialingCode;
      }
      if (isTag(container, "form")) break;
      container = container.parentElement;
    }
    return "";
  }

  function explicitControlLabel(element) {
    if (element.labels?.length) {
      return Array.from(element.labels).map((label) => label.textContent.trim()).join(" ");
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => element.getRootNode().getElementById?.(id)?.textContent?.trim() || element.ownerDocument.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }
    const aria = weakText(element.getAttribute("aria-label"));
    if (aria) return aria;
    return weakText(element.closest(".field, [data-field], .field-wrapper, label")?.querySelector("label")?.textContent || "");
  }

  function isDialingCodeLabel(value) {
    const label = weakText(value).replace(/\*+$/g, "").trim();
    return /^country$/i.test(label) || /\b(country|dial(?:ing)?|phone)\b.*\b(code|prefix)\b/i.test(label);
  }

  function dialingCodeFromControl(element) {
    let selectedText = isTag(element, "select")
      ? element.selectedOptions?.[0]?.textContent || element.value
      : element.value || element.getAttribute("aria-valuetext") || element.textContent;
    if (!/\+\s*\d/.test(weakText(selectedText))) {
      let container = element.parentElement;
      for (let depth = 0; container && depth < 5; depth += 1) {
        const optionText = container.querySelector("[role='option']")?.textContent || container.textContent || "";
        if (/\+\s*\d/.test(optionText)) {
          selectedText = optionText;
          break;
        }
        container = container.parentElement;
      }
    }
    const match = weakText(selectedText).match(/\+\s*(\d{1,4})\b/);
    return match ? `+${match[1]}` : "";
  }

  function findField(fieldId) {
    const byId = pageRoots()
      .map((root) => root.getElementById?.(fieldId))
      .find(Boolean);
    if (byId) return byId;
    return queryAllFromPage("input, select, textarea, button, [contenteditable='true']")
      .find((element) => element.name === fieldId || element.dataset.smartjobapplyFieldId === fieldId) || null;
  }

  function fieldsForId(fieldId) {
    return queryAllFromPage("input, select, textarea, button, [contenteditable='true']")
      .filter((element) => element.name === fieldId || element.dataset.smartjobapplyFieldId === fieldId);
  }

  function selectNativeCheckboxGroup(fieldId, values) {
    const checkboxes = fieldsForId(fieldId)
      .filter((element) => isTag(element, "input") && element.type === "checkbox");
    if (!checkboxes.length) return { ok: false, unavailable: values };
    const desired = Array.isArray(values) ? values.map(weakText).filter(Boolean) : [];
    const matches = new Map();
    const unavailable = [];
    desired.forEach((value) => {
      const match = checkboxes.find((checkbox) => nativeCheckboxOptionMatches(nativeCheckboxOptionLabel(checkbox), value));
      if (match) matches.set(match, true);
      else unavailable.push(value);
    });
    if (unavailable.length) return { ok: false, unavailable };
    const blocked = checkboxes.some((checkbox) => checkbox.checked !== matches.has(checkbox) && checkbox.disabled);
    if (blocked) return { ok: false, unavailable: ["Disabled checkbox option"] };
    checkboxes.forEach((checkbox) => {
      const shouldCheck = matches.has(checkbox);
      if (checkbox.checked !== shouldCheck) checkbox.click();
    });
    return { ok: true, unavailable: [] };
  }

  function nativeCheckboxOptionMatches(left, right) {
    const normalize = (value) => weakText(value).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    return Boolean(normalize(left)) && normalize(left) === normalize(right);
  }

  function uploadResumeFileToApplication(preparedResume) {
    const candidates = queryAllFromPage("input[type='file']");
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
    const inputWindow = input.ownerDocument.defaultView || window;
    const file = new inputWindow.File([bytes], preparedResume.filename, { type: preparedResume.mime_type });
    const transfer = new inputWindow.DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new inputWindow.Event("input", { bubbles: true }));
    input.dispatchEvent(new inputWindow.Event("change", { bubbles: true }));
    return { uploaded: true, filename: preparedResume.filename };
  }

  function scoreFileInput(element) {
    const label = labelFor(element).toLowerCase();
    const containerText = weakText(element.closest("section, form, [role='tabpanel'], div")?.textContent || "").toLowerCase();
    let score = 0;
    if (label.includes("resume") || label.includes("cv")) score += 10;
    if (label.includes("cover letter")) score -= 8;
    if (element.accept?.toLowerCase().includes("pdf")) score += 2;
    if (element.required) score += 20;
    if (/resume|cv/.test(element.id || element.name || "")) score += 8;
    if (containerText.includes("autofill from resume") && containerText.includes("autofill key application fields") && !element.required) score -= 50;
    return score;
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return value.replace(/['\\]/g, "\\$&");
  }

  function pageRoots() {
    const roots = [];
    const visited = new Set();
    const visit = (root) => {
      if (!root || visited.has(root)) return;
      visited.add(root);
      roots.push(root);
      Array.from(root.querySelectorAll("iframe")).forEach((frame) => {
        try {
          visit(frame.contentDocument);
        } catch {
          // Cross-origin application frames cannot be inspected by a content script.
        }
      });
      Array.from(root.querySelectorAll("*")).forEach((element) => {
        const tag = element.tagName?.toLowerCase() || "";
        const isThirdPartyExtensionUi = tag === "plasmo-csui" || tag.startsWith("grammarly-");
        if (element.shadowRoot && !isThirdPartyExtensionUi) visit(element.shadowRoot);
      });
    };
    visit(document);
    return roots;
  }

  function queryAllFromPage(selector) {
    return pageRoots().flatMap((root) => Array.from(root.querySelectorAll(selector)));
  }

  function queryFirstFromPage(selector) {
    for (const root of pageRoots()) {
      const match = root.querySelector(selector);
      if (match) return match;
    }
    return null;
  }

  function isTag(element, tagName) {
    return element?.tagName?.toLowerCase() === tagName;
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
