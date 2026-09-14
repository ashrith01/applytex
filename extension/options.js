// Extension settings: which API and web app the panel talks to.
// Stored in chrome.storage.local so the service worker and panel read the same values.

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_WEB_BASE = "http://localhost:3000";
const API_KEY = "applytexApiBase";
const WEB_KEY = "applytexWebAppBase";
const PROFILE_KEY = "applytexExtensionProfileId";
const TOKEN_KEY = "applytexExtensionAccessToken";

const apiInput = document.getElementById("api-base");
const webInput = document.getElementById("web-base");
const status = document.getElementById("status");
const sessionInfo = document.getElementById("session-info");

function show(kind, message) {
  status.className = kind;
  status.textContent = message;
}

function isLocal(hostname) {
  return ["127.0.0.1", "localhost", "[::1]"].includes(hostname);
}

// Returns a normalized origin or throws with a user-facing message.
function normalizeOrigin(value, { requireHttpsRemote }) {
  const trimmed = String(value || "").trim();
  if (!trimmed) throw new Error("Enter an origin such as http://127.0.0.1:8000.");
  let url;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error("That is not a valid URL.");
  }
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Use http:// or https://.");
  if (url.pathname !== "/" || url.search || url.hash) throw new Error("Origin only: no path, query or fragment.");
  if (requireHttpsRemote && url.protocol === "http:" && !isLocal(url.hostname)) {
    throw new Error("A non-local API must use https:// so your resume and answers are not sent in the clear.");
  }
  return url.origin;
}

async function load() {
  const stored = await chrome.storage.local.get([API_KEY, WEB_KEY, PROFILE_KEY, TOKEN_KEY]);
  apiInput.value = stored[API_KEY] || DEFAULT_API_BASE;
  webInput.value = stored[WEB_KEY] || DEFAULT_WEB_BASE;
  const profile = stored[PROFILE_KEY];
  sessionInfo.textContent = profile
    ? `Signed in as ${profile}${stored[TOKEN_KEY] ? " with a bearer token" : " (no password required)"}.`
    : "Not signed in. Open the panel on a job page to sign in.";
}

async function save() {
  try {
    const apiOrigin = normalizeOrigin(apiInput.value, { requireHttpsRemote: true });
    const webOrigin = normalizeOrigin(webInput.value, { requireHttpsRemote: false });
    if (!isLocal(new URL(apiOrigin).hostname)) {
      // Non-local origins are optional host permissions: ask, in the user's click.
      const granted = await chrome.permissions.request({ origins: [`${apiOrigin}/*`] });
      if (!granted) throw new Error("Permission for that origin was not granted; settings were not saved.");
    }
    await chrome.storage.local.set({ [API_KEY]: apiOrigin, [WEB_KEY]: webOrigin });
    apiInput.value = apiOrigin;
    webInput.value = webOrigin;
    show("ok", `Saved. API: ${apiOrigin} · Web app: ${webOrigin}`);
  } catch (error) {
    show("error", error.message);
  }
}

async function testConnection() {
  try {
    const apiOrigin = normalizeOrigin(apiInput.value, { requireHttpsRemote: true });
    const response = await chrome.runtime.sendMessage({
      type: "APPLYTEX_API_REQUEST",
      path: "/auth/status",
      options: { method: "GET" },
      apiBaseOverride: apiOrigin,
    });
    if (!response?.ok) throw new Error(response?.error || `The API answered ${response?.status || "nothing"}.`);
    const data = response.data || {};
    show(
      data.auth_required ? "warn" : "ok",
      data.auth_required
        ? "Reachable. This API requires a password; sign in from the panel."
        : "Reachable. Local profile sign-in works without a password.",
    );
  } catch (error) {
    show("error", `Could not reach the API: ${error.message}`);
  }
}

async function reset() {
  await chrome.storage.local.set({ [API_KEY]: DEFAULT_API_BASE, [WEB_KEY]: DEFAULT_WEB_BASE });
  await load();
  show("ok", "Reset to the local defaults.");
}

async function clearSession() {
  await chrome.storage.local.remove([PROFILE_KEY, TOKEN_KEY]);
  await load();
  show("ok", "Signed out. The panel will ask you to sign in again.");
}

document.getElementById("save").addEventListener("click", () => { void save(); });
document.getElementById("test").addEventListener("click", () => { void testConnection(); });
document.getElementById("reset").addEventListener("click", () => { void reset(); });
document.getElementById("clear-session").addEventListener("click", () => { void clearSession(); });
void load();
