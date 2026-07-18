importScripts("providers.js");

const API_BASE = "http://127.0.0.1:8000";
const PANEL_TABS_KEY = "applytexPanelTabs";

void restoreOpenPanels();

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url || !providerAllowed(tab.url)) {
    return;
  }
  await setPanelTabOpen(tab.id, true);
  await injectPanel(tab.id);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "APPLYTEX_API_REQUEST") {
    void proxyApiRequest(message)
      .then(sendResponse)
      .catch((error) => {
        sendResponse({
          ok: false,
          status: 0,
          error: error instanceof Error ? error.message : "Local API request failed.",
        });
      });
    return true;
  }
  if (message?.type !== "SMARTJOBAPPLY_PANEL_STATE" || !sender.tab?.id) return false;
  void setPanelTabOpen(sender.tab.id, message.open !== false);
  return false;
});

chrome.webNavigation.onCompleted.addListener((details) => {
  void restorePanelAfterNavigation(details);
});

chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
  void restorePanelAfterNavigation(details);
});

async function restorePanelAfterNavigation(details) {
  if (details.frameId !== 0 || !providerAllowed(details.url)) return;
  const stored = await chrome.storage.session.get([PANEL_TABS_KEY]);
  if (!stored[PANEL_TABS_KEY]?.[String(details.tabId)]) return;
  await injectPanel(details.tabId);
}

async function restoreOpenPanels() {
  const stored = await chrome.storage.session.get([PANEL_TABS_KEY]);
  const tabIds = Object.keys(stored[PANEL_TABS_KEY] || {})
    .map((value) => Number.parseInt(value, 10))
    .filter(Number.isInteger);
  await Promise.all(tabIds.map((tabId) => injectPanel(tabId)));
}

async function injectPanel(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: [
      "providers.js",
      "panel-shared.js",
      "panel-scan.js",
      "panel-fill.js",
      "panel-workday.js",
      "panel.js",
    ],
  }).catch(() => {});
}

async function setPanelTabOpen(tabId, open) {
  const stored = await chrome.storage.session.get([PANEL_TABS_KEY]);
  const panelTabs = { ...(stored[PANEL_TABS_KEY] || {}) };
  if (open) panelTabs[String(tabId)] = true;
  else delete panelTabs[String(tabId)];
  await chrome.storage.session.set({ [PANEL_TABS_KEY]: panelTabs });
}

async function proxyApiRequest(message) {
  const url = new URL(String(message.path || ""), API_BASE);
  if (url.origin !== API_BASE || !url.pathname.startsWith("/")) {
    return { ok: false, status: 400, error: "Invalid local API path." };
  }
  const options = message.options || {};
  const method = String(options.method || "GET").toUpperCase();
  if (!["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    return { ok: false, status: 405, error: "Unsupported local API method." };
  }
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers && typeof options.headers === "object" ? options.headers : {}),
  };
  const profileId = String(message.profileId || options.profileId || "").trim();
  if (profileId) headers["X-Profile-Id"] = profileId;
  const authorization = String(
    options.authorization
    || options.headers?.Authorization
    || options.headers?.authorization
    || message.authorization
    || "",
  ).trim();
  if (authorization && !headers.Authorization && !headers.authorization) {
    headers.Authorization = authorization.startsWith("Bearer ")
      ? authorization
      : `Bearer ${authorization}`;
  }
  let response;
  try {
    response = await fetch(url.href, {
      method,
      headers,
      body: options.body,
    });
  } catch (fetchError) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: fetchError instanceof Error ? fetchError.message : "Local API request failed.",
    };
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  return {
    ok: response.ok,
    status: response.status,
    data,
    error: response.ok ? "" : data?.detail || `Local API returned ${response.status}.`,
  };
}

function providerAllowed(url) {
  return Boolean(globalThis.ApplyTexProviders?.allowed?.(url));
}
