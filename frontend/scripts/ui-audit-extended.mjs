#!/usr/bin/env node
/** Extended UI audit — uses existing user with jobs if available */
import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.UI_BASE_URL || "http://localhost:3000";
const SAMPLE_TEX = resolve(__dirname, "../../samples/sample_resume.tex");

const results = [];
const log = (area, action, status, detail = "") => {
  results.push({ area, action, status, detail });
  const icon = status === "pass" ? "✓" : status === "fail" ? "✗" : status === "warn" ? "!" : "·";
  console.log(`${icon} [${area}] ${action}${detail ? ` — ${detail}` : ""}`);
};

async function signIn(page, username) {
  await page.goto(`${BASE}/login`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.getByPlaceholder("yourname").fill(username);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.waitForTimeout(2000);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });

  await signIn(page, "ashrith");
  log("login", "existing user ashrith", page.url().includes("/profile") ? "pass" : "fail", page.url());

  // Sidebar nav
  for (const label of ["Dashboard", "Profile", "Jobs", "Applications", "Resume Lab", "Settings"]) {
    await page.getByRole("link", { name: label, exact: true }).first().click();
    await page.waitForTimeout(600);
    const ok = await page.locator("h1").first().isVisible();
    log("sidebar", label, ok ? "pass" : "fail");
  }

  // Profile with data
  await page.goto(`${BASE}/profile`);
  await page.waitForTimeout(1500);
  const profileText = await page.locator("main").innerText();
  log("profile", "has name data", profileText.includes("Ashrith") || profileText.includes("ashrith") ? "pass" : "warn", "may be empty profile");
  log("profile", "Resume upload link", (await page.getByRole("link", { name: "Resume upload" }).count()) ? "pass" : "fail");

  // Jobs page
  await page.goto(`${BASE}/jobs`);
  await page.waitForTimeout(1500);
  const jobCards = await page.getByRole("link", { name: "Tailor resume" }).count();
  log("jobs", "saved job cards", jobCards > 0 ? "pass" : "warn", `${jobCards} tailor links`);
  const detailsLinks = await page.getByRole("link", { name: "Details" }).count();
  log("jobs", "Details links", detailsLinks > 0 ? "pass" : "warn", `${detailsLinks} found`);

  if (detailsLinks > 0) {
    await page.getByRole("link", { name: "Details" }).first().click();
    await page.waitForTimeout(1500);
    log("job-detail", "Tailor resume button", (await page.getByRole("link", { name: "Tailor resume" }).count()) ? "pass" : "fail");
    log("job-detail", "Track application button", (await page.getByRole("button", { name: "Track application" }).count()) ? "pass" : "fail");
    log("job-detail", "View posting link", (await page.getByRole("link", { name: "View posting" }).count()) ? "pass" : "fail");
    log("job-detail", "Apply page link", (await page.getByRole("link", { name: "Apply page" }).count()) ? "pass" : "fail");
  }

  // Track application flow
  if (detailsLinks > 0) {
    await page.getByRole("button", { name: "Track application" }).click();
    await page.waitForTimeout(1500);
    log("job-detail", "Track application click", "pass", "no error thrown");
  }

  await page.goto(`${BASE}/applications`);
  await page.waitForTimeout(1500);
  const appCount = await page.locator("main").innerText();
  const hasApps = !appCount.includes("No applications") && appCount.includes("application");
  log("applications", "list renders", "pass");
  const transition = page.getByRole("button", { name: /→/ }).first();
  if (await transition.count()) {
    await transition.click();
    await page.waitForTimeout(1000);
    log("applications", "status transition", "pass");
  } else {
    log("applications", "status transition", "warn", "no transition buttons visible");
  }

  // Full tailor wizard
  if (jobCards > 0) {
    await page.goto(`${BASE}/jobs`);
    await page.getByRole("link", { name: "Tailor resume" }).first().click();
    await page.waitForTimeout(2500);
    const step1 = page.getByRole("button", { name: "Improve My Resume for This Job" });
    if (await step1.count()) {
      await step1.click();
      await page.waitForTimeout(2000);
      log("tailor", "step 2 skill confirm UI", (await page.getByText(/Confirm skills|Run optimization/i).count()) ? "pass" : "fail");
      const runOpt = page.getByRole("button", { name: /Run optimization/i });
      log("tailor", "Run optimization button", (await runOpt.count()) ? "pass" : "warn", "present (not clicked — needs LLM)");
      const backBtn = page.getByRole("button", { name: /Back|Previous/i });
      if (await backBtn.count()) {
        await backBtn.first().click();
        log("tailor", "back to step 1", "pass");
      }
    } else {
      const err = await page.locator("main").innerText();
      log("tailor", "wizard blocked", "warn", err.slice(0, 100));
    }
  }

  // Lab — optimize tab button presence (don't run LLM)
  await page.goto(`${BASE}/lab`);
  await page.locator('input[type="file"]').first().setInputFiles(SAMPLE_TEX);
  await page.locator("textarea").first().fill("Python engineer FastAPI");
  await page.getByRole("button", { name: "Optimize", exact: true }).click();
  await page.waitForTimeout(500);
  const optBtn = page.getByRole("button", { name: /Run optimization|Optimize resume/i });
  log("lab", "Optimize tab action button", (await optBtn.count()) ? "pass" : "warn", "needs prior analyze");

  // Resume upload success feedback
  await page.goto(`${BASE}/profile/resume`);
  await page.locator('input[type="file"]').first().setInputFiles(SAMPLE_TEX);
  await page.waitForTimeout(5000);
  const resumeMain = await page.locator("main").innerText();
  const uploaded =
    resumeMain.includes("sample_resume") ||
    resumeMain.includes("LaTeX source") ||
    resumeMain.includes("Profile updated") ||
    resumeMain.includes("prefill");
  log("resume", "upload feedback", uploaded ? "pass" : "warn", resumeMain.split("\n").slice(0, 5).join(" | "));

  // Settings (read-only page)
  await page.goto(`${BASE}/settings`);
  log("settings", "API base URL card", (await page.getByText("API base URL").count()) ? "pass" : "fail");
  log("settings", "LLM backends card", (await page.getByText("LLM backends").count()) ? "pass" : "fail");

  // Sign out from sidebar
  await page.goto(`${BASE}/profile`);
  await page.getByRole("button", { name: "Change username" }).click();
  await page.waitForTimeout(800);
  log("auth", "sidebar sign out", page.url().includes("/login") ? "pass" : "fail");

  await browser.close();
  const fails = results.filter((r) => r.status === "fail");
  const warns = results.filter((r) => r.status === "warn");
  console.log(`\n--- Extended: ${results.length} checks, ${fails.length} failed, ${warns.length} warnings ---`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
