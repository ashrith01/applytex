#!/usr/bin/env node
/**
 * ApplyTeX local executor: fills queued applications in a dedicated Chrome
 * profile on this machine and submits ONLY after the user approves the paused
 * review in the API.
 *
 *   node scripts/executor.mjs --profile <profile_id> [--api http://127.0.0.1:8000]
 *        [--user-data-dir .applytex/browser-profile] [--headless] [--poll 5]
 *        [--allow-provider workday] [--url-rewrite https://a.example=http://127.0.0.1:8765]
 *        [--token <bearer>] [--once]
 *
 * How it works: for each run it opens the apply URL, injects the real
 * extension panel scripts with a chrome.* shim (API calls are bridged to Node),
 * drives the panel's own reviewed-fill flow, and pauses with a screenshot.
 * When the API says the run is approved it clicks the employer's Submit
 * button while holding the approval token, waits for a confirmation page, and
 * records the receipt. It never bypasses sign-in, CAPTCHA, or MFA: those pause
 * the run and hand the open browser window back to you.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(pathToFileURL(path.join(root, "frontend/node_modules/playwright/index.mjs")));

const args = parseArgs(process.argv.slice(2));
const API = (args.api || process.env.APPLYTEX_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
const PROFILE_ID = args.profile || process.env.APPLYTEX_EXECUTOR_PROFILE || "";
const TOKEN = args.token || process.env.APPLYTEX_EXECUTOR_TOKEN || "";
const USER_DATA_DIR = path.resolve(root, args["user-data-dir"] || ".applytex/browser-profile");
const HEADLESS = Boolean(args.headless);
const POLL_SECONDS = Number(args.poll || 5);
const ONCE = Boolean(args.once);
const ALLOWED_PROVIDERS = new Set(["greenhouse", "lever", "ashby", ...(args["allow-provider"] || [])]);
const URL_REWRITES = (args["url-rewrite"] || []).map((pair) => pair.split("="));
const DEBUG = Boolean(args.debug) || process.env.APPLYTEX_EXECUTOR_DEBUG === "1";
const EXECUTOR_ID = `${os.hostname()}:${process.pid}`;
const MAX_STEPS = 8;

if (!PROFILE_ID) {
  console.error("--profile <profile_id> is required (the ApplyTeX profile whose applications this executor may fill).");
  process.exit(2);
}
if (!["127.0.0.1", "localhost"].includes(new URL(API).hostname)) {
  console.error("The executor only talks to a local ApplyTeX API.");
  process.exit(2);
}

const PANEL_FILES = ["providers.js", "panel-shared.js", "panel-scan.js", "panel-fill.js", "panel-workday.js", "panel-profile.js", "panel.js"];
const panelSources = PANEL_FILES.map((name) => fs.readFileSync(path.join(root, "extension", name), "utf8"));

// run_id -> { page, run }
const openRuns = new Map();

const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
  headless: HEADLESS,
  channel: process.env.APPLYTEX_BROWSER_CHANNEL || undefined,
  viewport: { width: 1440, height: 1000 },
  args: ["--disable-blink-features=AutomationControlled"],
});
await context.exposeBinding("__applytexApiRequest", async (_source, message) => apiBridge(message));
await context.addInitScript(installChromeShim, { profileId: PROFILE_ID });

log(`executor ${EXECUTOR_ID} ready: api=${API} profile=${PROFILE_ID} providers=${[...ALLOWED_PROVIDERS].join(",")} headless=${HEADLESS}`);

let stopping = false;
process.on("SIGINT", () => { stopping = true; });
process.on("SIGTERM", () => { stopping = true; });

while (!stopping) {
  try {
    const runs = await api("GET", `/apply-runs?status=queued&status=approved`);
    for (const run of runs) {
      if (stopping) break;
      if (!ALLOWED_PROVIDERS.has(run.provider)) {
        await progress(run.run_id, { status: "failed", step: "provider", level: "error", error: `Provider ${run.provider} is not enabled for this executor (use --allow-provider ${run.provider}).`, message: `Skipped: provider ${run.provider} not enabled.` }).catch(() => {});
        continue;
      }
      try {
        if (run.status === "queued") await handleQueued(run);
        else if (run.status === "approved") await handleApproved(run);
      } catch (error) {
        log(`run ${run.run_id} failed: ${error.message}`);
        await progress(run.run_id, { status: "failed", step: "error", level: "error", error: String(error.message || error), message: `Executor error: ${error.message}` }).catch(() => {});
      }
    }
  } catch (error) {
    log(`poll error: ${error.message}`);
  }
  if (ONCE) break;
  await sleep(POLL_SECONDS * 1000);
}
await context.close().catch(() => {});
process.exit(0);

// ---------------------------------------------------------------------------

async function handleQueued(queued) {
  const claim = await api("POST", `/apply-runs/${queued.run_id}/claim`, { executor_id: EXECUTOR_ID });
  const { run, application, job } = claim;
  const existing = openRuns.get(run.run_id);
  const page = existing?.page && !existing.page.isClosed() ? existing.page : await context.newPage();
  openRuns.set(run.run_id, { page, run });
  const executorContext = { application_id: application.application_id, job };
  if (!existing) {
    if (DEBUG) {
      page.on("console", (msg) => log(`[page ${msg.type()}] ${msg.text()}`));
      page.on("pageerror", (error) => log(`[page error] ${error.message}`));
    }
    await page.addInitScript((ctx) => { window.__applytexExecutor = ctx; }, executorContext);
    await progress(run.run_id, { step: "opening", message: `Opening ${run.apply_url}` });
    await page.goto(rewriteUrl(run.apply_url), { waitUntil: "domcontentloaded", timeout: 60000 });
  } else {
    await page.evaluate((ctx) => { window.__applytexExecutor = ctx; }, executorContext);
  }
  await installPanel(page);

  // On a resumed run the re-opened panel rescans first; wait for that to settle.
  const ready = await waitForPanelReady(page, 40000, { minimumMs: existing ? 1500 : 0 });
  if (DEBUG) log(`[debug] run ${run.run_id} ${existing ? "resumed" : "opened"} ready=${ready} canFill=${await panelCanFill(page)}`);
  if (ready !== "ready") {
    const blocker = await detectBlocker(page);
    if (DEBUG) {
      const dump = await page.evaluate(() => ({
        executor: Boolean(window.__applytexExecutor),
        installed: Boolean(window.__smartJobApplyPanelInstalled),
        chrome: window.chrome?.runtime?.id || null,
        panel: (document.querySelector("#smartjobapply-panel")?.innerText || "").replace(/\s+/g, " ").slice(0, 1500),
      })).catch((error) => ({ error: error.message }));
      log(`[debug] ready=${ready} blocker=${blocker.step} ${JSON.stringify(dump)}`);
    }
    await progress(run.run_id, {
      status: "awaiting_input",
      step: blocker.step,
      level: "warn",
      message: blocker.message,
      screenshot_b64: await screenshot(page),
    });
    return;
  }

  let steps = 0;
  let plan = null;
  while (steps < MAX_STEPS) {
    steps += 1;
    if (await panelCanFill(page)) {
      await progress(run.run_id, { step: `fill-${steps}`, message: `Filling reviewed fields on step ${steps}.` });
      await clickPanel(page, "[data-action='autofill']");
      await waitForFillComplete(page, 90000);
    } else {
      await progress(run.run_id, { step: `fill-${steps}`, message: `Nothing left to fill on step ${steps}.` });
    }
    // The panel keeps document uploads as separate reviewed clicks. The resume
    // is always in scope (approved tailored PDF first, else the profile
    // resume); an approved cover letter is attached where the panel offers to.
    if (await attachResume(page)) {
      await progress(run.run_id, { step: `resume-${steps}`, message: "Uploaded the resume (approved tailored PDF when available)." });
    }
    if (await attachApprovedDocuments(page)) {
      await progress(run.run_id, { step: `attach-${steps}`, message: "Attached the approved cover letter." });
    }
    plan = await latestPlan(application.application_id);
    if (DEBUG) log(`[debug] run ${run.run_id} step ${steps} plan scan=${plan?.scan_id} unresolved=${JSON.stringify(plan?.unresolved_required)} ready=${plan?.ready_action_count}`);
    if (plan?.unresolved_required?.length) {
      await progress(run.run_id, {
        status: "awaiting_input",
        step: `fill-${steps}`,
        level: "warn",
        unresolved_required: plan.unresolved_required,
        review_summary: summarize(plan),
        screenshot_b64: await screenshot(page),
        message: `${plan.unresolved_required.length} required field(s) need an answer: ${plan.unresolved_required.slice(0, 5).join("; ")}. Answer them (extension panel or answers bank), then Resume.`,
      });
      return;
    }
    const canContinue = await page.evaluate(() => {
      const button = document.querySelector("#smartjobapply-panel [data-action='continue-next-page']");
      return Boolean(button && !button.disabled);
    });
    if (!canContinue) break;
    await progress(run.run_id, { step: `continue-${steps}`, message: "Continuing to the next application step." });
    await clickPanel(page, "[data-action='continue-next-page']");
    await sleep(2000);
    await page.waitForLoadState("domcontentloaded").catch(() => {});
    await installPanel(page);
    if ((await waitForPanelReady(page, 20000)) !== "ready") break;
  }

  const invalid = await invalidFormFields(page);
  await progress(run.run_id, {
    status: "paused_for_review",
    step: "review",
    unresolved_required: [],
    review_summary: { ...summarize(plan), employer_invalid_fields: invalid },
    screenshot_b64: await screenshot(page),
    message: invalid.length
      ? `Form filled, but the employer form still reports invalid fields: ${invalid.join("; ")}. Fix them in the browser window before approving.`
      : "Form filled. Review the screenshot and the field list, then approve to submit or cancel.",
  });
}

// Employer-side HTML validation: a required/invalid control blocks the submit
// event silently, so surface the labels rather than waiting on a confirmation.
async function invalidFormFields(page) {
  return page.evaluate(() => {
    const labelFor = (el) => {
      const id = el.id;
      const byFor = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
      const text = (byFor || el.closest("label") || el.closest("fieldset")?.querySelector("legend"))?.textContent
        || el.getAttribute("aria-label") || el.name || el.id || el.tagName;
      return text.replace(/\s+/g, " ").trim().slice(0, 80);
    };
    return Array.from(document.querySelectorAll("form :invalid"))
      .filter((el) => !el.closest("#smartjobapply-panel") && ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName))
      .map((el) => `${labelFor(el)} (${el.validationMessage || "invalid"})`)
      .slice(0, 12);
  }).catch(() => []);
}

async function handleApproved(approved) {
  const entry = openRuns.get(approved.run_id);
  if (!entry || entry.page.isClosed()) {
    await progress(approved.run_id, {
      status: "failed", step: "submit", level: "error",
      error: "The browser page for this run is no longer open (executor restarted?). Cancel and enqueue again.",
      message: "Lost the filled page before submission; nothing was submitted.",
    });
    return;
  }
  const { page } = entry;
  const claim = await api("POST", `/apply-runs/${approved.run_id}/claim`, { executor_id: EXECUTOR_ID });
  const token = claim.approval_token;
  if (!token) throw new Error("API did not return an approval token; refusing to submit.");

  const clicked = await clickFinalSubmit(page);
  if (clicked) await progress(approved.run_id, { step: "submit", message: `Clicked "${clicked}".` });
  if (!clicked) {
    await progress(approved.run_id, {
      status: "needs_verification", step: "submit", level: "warn",
      screenshot_b64: await screenshot(page),
      message: "Could not find the employer's Submit button. Submit manually in the ApplyTeX browser window, then Mark as submitted.",
    });
    openRuns.delete(approved.run_id);
    return;
  }
  await progress(approved.run_id, { step: "submit", message: "Clicked the employer's Submit button; waiting for confirmation." });
  let evidence = await waitForConfirmation(page, 4000);
  if (!evidence) {
    const invalid = await invalidFormFields(page);
    if (invalid.length) {
      await progress(approved.run_id, {
        status: "needs_verification", step: "confirm", level: "warn", screenshot_b64: await screenshot(page),
        message: `The employer form blocked submission on invalid fields: ${invalid.join("; ")}. Fix them in the ApplyTeX browser window, submit, then Mark as submitted.`,
      });
      openRuns.delete(approved.run_id);
      return;
    }
    evidence = await waitForConfirmation(page, 26000);
  }
  const shot = await screenshot(page);
  if (!evidence) {
    const excerpt = await page.evaluate(() => (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 240)).catch(() => "");
    await progress(approved.run_id, {
      status: "needs_verification", step: "confirm", level: "warn", screenshot_b64: shot,
      message: `No confirmation page was detected within 30s. Check the browser window; if it went through, Mark as submitted. Page: ${excerpt}`,
    });
    openRuns.delete(approved.run_id);
    return;
  }
  const result = await api("POST", `/apply-runs/${approved.run_id}/submitted`, {
    approval_token: token,
    detection_evidence: evidence,
    screenshot_b64: shot,
  });
  log(`run ${approved.run_id} submitted; receipt ${result.bundle_id}`);
  openRuns.delete(approved.run_id);
  await sleep(1500);
  await page.close().catch(() => {});
}

// --- page helpers ------------------------------------------------------------

async function installPanel(page) {
  const installed = await page.evaluate(() => Boolean(window.__smartJobApplyPanelInstalled)).catch(() => false);
  if (installed) {
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("smartjobapply:open"))).catch(() => {});
    return;
  }
  for (const source of panelSources) {
    await page.addScriptTag({ content: source });
  }
}

// Panel state as seen from outside its closure: "busy" while a withBusy()
// status ("...") or the autofill progress bar is showing.
// "ready" means the main panel view is rendered and idle. Whether the Autofill
// button is enabled is a separate question: the panel disables it once every
// field is already filled, which is success, not a blocker.
function panelSnapshot() {
  const panel = document.querySelector("#smartjobapply-panel");
  if (!panel) return { state: "missing", canFill: false };
  const busy = Array.from(panel.querySelectorAll(".sja-status")).some((el) => /\.\.\.\s*$/.test(el.textContent || ""))
    || Boolean(panel.querySelector(".sja-autofill-progress"));
  if (busy) return { state: "busy", canFill: false };
  const autofill = panel.querySelector("[data-action='autofill']");
  if (autofill) return { state: "ready", canFill: !autofill.disabled };
  if (panel.querySelector("[data-action='sign-in']")) return { state: "signin", canFill: false };
  return { state: "waiting", canFill: false };
}

async function panelCanFill(page) {
  const { canFill } = await page.evaluate(panelSnapshot).catch(() => ({ canFill: false }));
  return canFill;
}

// The panel renders a sign-in form for a moment while it initializes, so a
// sign-in state only counts as a blocker once it has persisted for a few seconds.
// "ready" additionally means idle: no scan/fill/attach is in flight.
async function waitForPanelReady(page, timeout, { minimumMs = 0 } = {}) {
  const started = Date.now();
  const deadline = started + timeout;
  let signInSince = 0;
  while (Date.now() < deadline) {
    const { state } = await page.evaluate(panelSnapshot).catch(() => ({ state: "missing" }));
    if (state === "ready" && Date.now() - started >= minimumMs) return "ready";
    if (state === "signin") {
      signInSince ||= Date.now();
      if (Date.now() - signInSince > 6000) return "signin";
    } else {
      signInSince = 0;
    }
    await sleep(400);
  }
  return "timeout";
}

async function detectBlocker(page) {
  const info = await page.evaluate(() => {
    const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const password = Array.from(document.querySelectorAll("input[type='password']")).some(visible);
    const captcha = Boolean(document.querySelector("iframe[src*='captcha' i], iframe[src*='recaptcha' i], iframe[src*='hcaptcha' i], [class*='captcha' i], [id*='captcha' i]"));
    const panelText = document.querySelector("#smartjobapply-panel")?.textContent || "";
    return { password, captcha, panelText: panelText.slice(0, 400) };
  }).catch(() => ({ password: false, captcha: false, panelText: "" }));
  if (info.captcha) return { step: "captcha", message: "A CAPTCHA is on the page. Solve it in the ApplyTeX browser window, then Resume the run." };
  if (info.password) return { step: "sign-in", message: "The employer portal wants you to sign in. Sign in in the ApplyTeX browser window, then Resume the run." };
  if (/sign in/i.test(info.panelText)) return { step: "profile", message: "The panel could not sign in to the ApplyTeX profile. Check the profile id and that the API is running." };
  return { step: "form", message: `The application form was not ready to fill. Panel said: ${info.panelText.replace(/\s+/g, " ").trim().slice(0, 200)}` };
}

async function clickPanel(page, selector) {
  await page.locator(`#smartjobapply-panel ${selector}`).first().dispatchEvent("pointerup");
}

// After clicking Autofill: let the run start (progress bar / busy status),
// then wait until the panel is idle again with its rescan finished.
async function waitForFillComplete(page, timeout) {
  const started = Date.now();
  while (Date.now() - started < 4000) {
    const { state } = await page.evaluate(panelSnapshot).catch(() => ({ state: "missing" }));
    if (state === "busy") break;
    await sleep(200);
  }
  const result = await waitForPanelReady(page, timeout, { minimumMs: 1500 });
  if (result !== "ready") throw new Error(`The panel did not finish filling (${result}).`);
  await sleep(500);
}

async function attachResume(page) {
  const needsResume = await page.evaluate(() => Array.from(document.querySelectorAll("input[type='file']")).some((el) => {
    const label = ((el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent) || el.closest("label")?.textContent || el.getAttribute("aria-label") || el.name || el.id || "").toLowerCase();
    return /resume|\bcv\b/.test(label) && !/cover/.test(label) && !(el.files && el.files.length);
  })).catch(() => false);
  if (!needsResume) return false;
  const opener = page.locator("#smartjobapply-panel [data-action='open-resume-workspace']").first();
  if (!(await opener.count())) return false;
  await opener.dispatchEvent("pointerup");
  await sleep(600);
  const tailored = page.locator("#smartjobapply-panel [data-action='use-tailored-resume']");
  const profile = page.locator("#smartjobapply-panel [data-action='use-profile-resume']");
  const button = (await tailored.count()) ? tailored.first() : profile.first();
  if (!(await button.count())) throw new Error("The resume chooser offered no resume to upload; save a profile resume first.");
  await button.dispatchEvent("pointerup");
  const settled = await waitForPanelReady(page, 40000, { minimumMs: 1500 });
  if (settled !== "ready") throw new Error(`The panel did not settle after uploading the resume (${settled}).`);
  await sleep(500);
  return true;
}

async function attachApprovedDocuments(page) {
  const buttons = page.locator("#smartjobapply-panel [data-action='attach-cover-letter']");
  const count = await buttons.count();
  if (!count) return false;
  await buttons.first().dispatchEvent("pointerup");
  // attachCoverLetter() fetches the file, injects it, then rescans the form.
  const settled = await waitForPanelReady(page, 30000, { minimumMs: 1500 });
  if (settled !== "ready") throw new Error(`The panel did not settle after attaching the cover letter (${settled}).`);
  await sleep(500);
  return true;
}

async function latestPlan(applicationId) {
  const detail = await api("GET", `/applications/${applicationId}`);
  const scanId = detail?.latest_form_scan?.scan_id;
  if (!scanId) return null;
  return api("GET", `/extension/forms/${scanId}/plan`);
}

function summarize(plan) {
  if (!plan) return {};
  const items = plan.review_items || [];
  return {
    ready: items.filter((item) => item.status === "ready" && item.answer_source !== "already_on_page").length,
    already_filled: items.filter((item) => item.answer_source === "already_on_page").length,
    blocked: items.filter((item) => item.status !== "ready" && item.required).length,
    unresolved_required: plan.unresolved_required || [],
    items: items.slice(0, 120).map((item) => ({
      label: item.label, required: item.required, status: item.status, source: item.answer_source,
      value: item.planned_value_preview || item.current_value_preview || item.value_preview || null,
    })),
  };
}

async function clickFinalSubmit(page) {
  return page.evaluate(() => {
    const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    const text = (el) => `${el.getAttribute("aria-label") || ""} ${el.value || ""} ${el.textContent || ""}`.replace(/\s+/g, " ").trim();
    const candidates = Array.from(document.querySelectorAll("button, input[type='submit'], a[role='button']"))
      .filter((el) => !el.closest("#smartjobapply-panel") && visible(el) && !el.disabled && el.getAttribute("aria-disabled") !== "true");
    const isFinal = (el) => /\b(submit application|submit my application|submit|send application|apply now|complete application)\b/i.test(text(el))
      || /submit|apply$/i.test(el.getAttribute("data-automation-id") || "");
    const match = candidates.find(isFinal);
    if (!match) return false;
    match.scrollIntoView({ block: "center" });
    match.click();
    return text(match).slice(0, 80) || "submit";
  });
}

async function waitForConfirmation(page, timeout) {
  const markers = [/thank you for applying/i, /thanks for applying/i, /your application (?:has been|was) (?:successfully )?(?:submitted|received)/i, /application (?:successfully )?submitted/i, /we(?:'ve| have) received your application/i, /you have successfully applied/i];
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const info = await page.evaluate(() => {
      const text = Array.from(document.body?.children || [])
        .filter((el) => el.id !== "smartjobapply-panel" && el.id !== "smartjobapply-panel-style")
        .map((el) => el.innerText || "").join(" ").replace(/\s+/g, " ").slice(0, 20000);
      return { text, path: location.pathname };
    }).catch(() => ({ text: "", path: "" }));
    const marker = markers.find((pattern) => pattern.test(info.text));
    if (marker) return info.text.match(marker)[0];
    if (/\/(?:confirmation|thanks|thank-you|applied)(?:\/|$)/.test(info.path)) return `confirmation URL ${info.path}`;
    await sleep(1000);
  }
  return "";
}

async function screenshot(page) {
  try {
    return (await page.screenshot({ fullPage: true, type: "png" })).toString("base64");
  } catch {
    return undefined;
  }
}

// --- API -----------------------------------------------------------------------

function apiHeaders() {
  const headers = { "Content-Type": "application/json", "X-Profile-Id": PROFILE_ID };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  return headers;
}

async function api(method, apiPath, body) {
  const response = await fetch(`${API}${apiPath}`, { method, headers: apiHeaders(), body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(`${method} ${apiPath} -> ${response.status}: ${data?.detail || text}`);
  return data;
}

async function progress(runId, body) {
  return api("PATCH", `/apply-runs/${runId}`, body);
}

// Bridge for the injected panel's chrome.runtime.sendMessage({type: "APPLYTEX_API_REQUEST"}).
async function apiBridge(message) {
  if (message?.type !== "APPLYTEX_API_REQUEST") return { ok: false, status: 400, error: "Unsupported message." };
  const url = new URL(String(message.path || ""), API);
  if (url.origin !== API) return { ok: false, status: 400, error: "Invalid local API path." };
  const options = message.options || {};
  try {
    const response = await fetch(url.href, {
      method: options.method || "GET",
      headers: { ...apiHeaders(), ...(options.headers || {}) },
      body: options.body,
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    return { ok: response.ok, status: response.status, data, error: response.ok ? "" : data?.detail || `Local API returned ${response.status}.` };
  } catch (error) {
    return { ok: false, status: 0, data: null, error: error.message };
  }
}

// Runs in every page before its scripts: a minimal chrome.* so the real panel
// modules work outside the extension. Pages that already have one keep it.
function installChromeShim({ profileId }) {
  if (window.chrome?.runtime?.id) return;
  const storage = { applytexExtensionProfileId: profileId };
  window.chrome = {
    runtime: {
      id: "applytex-local-executor",
      onMessage: { addListener() {} },
      sendMessage(message) { return window.__applytexApiRequest(message); },
    },
    storage: {
      local: {
        async get(keys) {
          if (Array.isArray(keys)) return Object.fromEntries(keys.map((key) => [key, storage[key]]));
          if (typeof keys === "string") return { [keys]: storage[keys] };
          return { ...keys, ...storage };
        },
        async set(values) { Object.assign(storage, values); },
        async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key]; },
      },
    },
  };
}

// --- util --------------------------------------------------------------------

function rewriteUrl(url) {
  for (const [from, to] of URL_REWRITES) {
    if (from && url.startsWith(from)) return to + url.slice(from.length);
  }
  return url;
}

function parseArgs(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith("--")) continue;
    const key = arg.slice(2);
    const next = argv[index + 1];
    const value = next === undefined || next.startsWith("--") ? true : (index += 1, next);
    if (["allow-provider", "url-rewrite"].includes(key)) (out[key] ||= []).push(value);
    else out[key] = value;
  }
  return out;
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function log(message) { console.log(`[executor ${new Date().toISOString()}] ${message}`); }
