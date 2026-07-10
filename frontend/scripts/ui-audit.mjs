#!/usr/bin/env node
/**
 * Manual UI audit script. Requires dev servers:
 *   uv run applytex-api
 *   cd frontend && npm run dev
 */
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.UI_BASE_URL || "http://localhost:3000";
const API = process.env.API_BASE_URL || "http://127.0.0.1:8000";
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
  await page.waitForTimeout(1500);
}

async function main() {
  try {
    const health = await fetch(`${API}/health`);
    log("api", "health", health.ok ? "pass" : "fail", await health.text());
  } catch (e) {
    log("api", "health", "fail", String(e));
    console.error("\nStart API: uv run applytex-api");
    process.exit(1);
  }

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  await signIn(page, `audit-${Date.now()}`);
  log("login", "Continue → profile", page.url().includes("/profile") ? "pass" : "fail", page.url());

  for (const route of ["/", "/profile", "/profile/resume", "/jobs", "/applications", "/lab", "/settings"]) {
    const resp = await page.goto(`${BASE}${route}`);
    log("route", route, resp && resp.status() < 500 ? "pass" : "fail", `status=${resp?.status()}`);
  }

  await page.goto(`${BASE}/profile`);
  await page.getByRole("heading", { name: "Profile" }).waitFor({ timeout: 10000 });
  await page.waitForTimeout(800);
  for (const title of [
    "Personal",
    "Education",
    "Work Experience",
    "Skills",
    "Job Application Questions",
    "Search Preferences",
  ]) {
    const btn = page.getByTitle(`Edit ${title}`);
    if (!(await btn.count())) {
      log("profile", `Edit ${title}`, "fail", "button missing");
      continue;
    }
    await btn.scrollIntoViewIfNeeded();
    await btn.click();
    await page.waitForTimeout(400);
    const ok = (await page.getByRole("button", { name: "Save" }).count()) > 0;
    log("profile", `Edit ${title}`, ok ? "pass" : "fail", ok ? "editor opens" : "no save");
    if (ok) await page.getByRole("button", { name: "Cancel" }).click();
  }

  await page.goto(`${BASE}/profile/resume`);
  await page.getByRole("heading", { name: "Profile resume" }).waitFor();
  await page.waitForTimeout(500);
  log("resume", "Choose .tex file", (await page.getByRole("button", { name: "Choose .tex file" }).count()) ? "pass" : "fail");
  log("resume", "Choose PDF file", (await page.getByRole("button", { name: "Choose PDF file" }).count()) ? "pass" : "fail");

  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: "Choose .tex file" }).click(),
  ]);
  await chooser.setFiles(SAMPLE_TEX);
  await page.waitForTimeout(4000);
  const resumeText = await page.locator("main").innerText();
  log(
    "resume",
    "upload + extract",
    /Resume saved|Profile updated|Uploading/i.test(resumeText) ? "pass" : "fail",
    resumeText.split("\n").slice(0, 3).join(" | "),
  );

  await page.goto(`${BASE}/lab`);
  await page.locator('input[type="file"]').first().setInputFiles(SAMPLE_TEX);
  await page.locator("textarea").first().fill("Python FastAPI machine learning backend engineer");
  await page.getByRole("button", { name: "Fit Score", exact: true }).click();
  const analyze = page.getByRole("button", { name: /Analyze resume against JD/i });
  log("lab", "Analyze button on Fit tab", (await analyze.count()) ? "pass" : "fail");
  if (await analyze.count()) {
    await analyze.click();
    await page.waitForTimeout(2500);
    const labText = await page.locator("main").innerText();
    log("lab", "Analyze result", /match for this role|Confirm skills|Submission fit/i.test(labText) ? "pass" : "warn", labText.slice(0, 80));
  }

  for (const tab of ["Parse", "Fit Score", "Optimize", "Analysis", "Optimized PDF"]) {
    const btn =
      tab === "Optimize"
        ? page.locator("button").filter({ hasText: /^Optimize$/ })
        : page.getByRole("button", { name: tab, exact: true });
    await btn.first().click().catch(() => {});
    log("lab", `tab ${tab}`, (await btn.count()) ? "pass" : "fail");
  }

  await page.goto(`${BASE}/jobs`);
  const searchBtn = page.getByRole("button", { name: "Search jobs" });
  log("jobs", "Search disabled when empty", (await searchBtn.isDisabled()) ? "pass" : "warn");
  const tailorLink = page.getByRole("link", { name: "Tailor resume" }).first();
  if (await tailorLink.count()) {
    await tailorLink.click();
    await page.waitForTimeout(2500);
    const tailorText = await page.locator("main").innerText();
    log("tailor", "open from jobs", /Tailor resume|Could not start|Improve My Resume/i.test(tailorText) ? "pass" : "fail");
    const step1 = page.getByRole("button", { name: "Improve My Resume for This Job" });
    if (await step1.count()) {
      await step1.click();
      log("tailor", "step 1 → 2", (await page.getByText(/Confirm skills|Run optimization/i).count()) ? "pass" : "fail");
    } else {
      log("tailor", "step 1 CTA", "warn", "blocked — likely missing .tex on profile");
    }
  } else {
    log("jobs", "Tailor links", "info", "no saved jobs");
  }

  await page.goto(`${BASE}/applications`);
  const transition = page.getByRole("button", { name: /→/ }).first();
  if (await transition.count()) {
    await transition.click();
    await page.waitForTimeout(1000);
    log("applications", "status transition", "pass");
  } else {
    log("applications", "status transition", "info", "no applications or no transitions");
  }

  await page.goto(`${BASE}/`);
  await page.waitForTimeout(1500);
  log("shell", "API connected badge", (await page.getByText("API connected").count()) ? "pass" : "fail");
  log("dashboard", "Open profile link", (await page.getByRole("link", { name: "Open profile" }).count()) ? "pass" : "fail");
  log("dashboard", "Manage resume link", (await page.getByRole("link", { name: "Manage resume" }).count()) ? "pass" : "fail");
  log("dashboard", "Open Resume Lab link", (await page.getByRole("link", { name: "Open Resume Lab" }).count()) ? "pass" : "fail");

  await page.goto(`${BASE}/profile`);
  await page.getByRole("button", { name: "Change username" }).click();
  await page.waitForTimeout(500);
  log("auth", "Change username", page.url().includes("/login") ? "pass" : "fail");

  await browser.close();

  const fails = results.filter((r) => r.status === "fail");
  const warns = results.filter((r) => r.status === "warn");
  console.log(`\n--- Summary: ${results.length} checks, ${fails.length} failed, ${warns.length} warnings ---`);
  process.exit(fails.length ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
