#!/usr/bin/env node
/** End-to-end synthetic applications: actual panel scripts + actual local API. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(pathToFileURL(path.join(root, "frontend/node_modules/playwright/index.mjs")));
const base = process.env.AUTOFILL_LAB_URL || "http://127.0.0.1:8765";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Lab QA only runs on localhost.");
const providers = (process.env.AUTOFILL_LAB_PROVIDERS || "greenhouse,ashby,workday,lever,icims,smartrecruiters,workable,linkedin,indeed,ziprecruiter,glassdoor,wellfound,dice").split(",");
const scenarios = (process.env.AUTOFILL_LAB_SCENARIOS || "complete,edge-cases,multi-step").split(",");
const out = path.join(root, ".applytex/qa/autofill-lab.json");
const report = { started_at: new Date().toISOString(), kind: "synthetic-real-api", runtime: "real panel scripts with lab-only Chrome message transport and reserved test URLs", cases: [], assertions: 0, failures: [] };
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
// No test can accidentally navigate/fetch an external site.
await context.route("**/*", (route) => {
  const host = new URL(route.request().url()).hostname;
  return ["127.0.0.1", "localhost"].includes(host) ? route.continue() : route.abort();
});
try {
  for (const provider of providers) for (const scenario of scenarios) {
    const page = await context.newPage();
    const record = { provider, scenario, success: false, assertions: 0, errors: [], initial_questions: [], final_values: {} };
    const started = Date.now();
    const pending = [];
    page.on("pageerror", (error) => record.errors.push(error.message));
    page.on("response", (response) => {
      if (new URL(response.url()).pathname === "/extension/forms/scan" && response.ok()) {
        pending.push(response.json().then((scan) => { if (!record.initial_questions.length) record.initial_questions = scan.questions; }).catch(() => {}));
      }
    });
    const check = (condition, message) => { record.assertions++; if (!condition) record.errors.push(message); };
    try {
      await page.goto(`${base}/lab/${provider}/${scenario}`, { waitUntil: "domcontentloaded" });
      await ready(page);
      await Promise.all(pending);
      check(record.initial_questions.length > 0, "No form questions scanned by the real API.");
      for (const question of record.initial_questions.filter((q) => q.input_type === "select")) {
        check(!question.current_value_present, `${question.field_id}: placeholder was counted as filled.`);
        check(!question.label.includes("Select one"), `${question.field_id}: options leaked into prompt.`);
      }
      const chooseResume = page.locator("#smartjobapply-panel [data-action='open-resume-workspace']").first();
      await chooseResume.click();
      await page.locator("#smartjobapply-panel [data-action='use-profile-resume']").click();
      await page.waitForFunction(() => document.querySelector("#resume")?.files.length === 1);
      await page.locator("#smartjobapply-panel [data-tab='autofill']").click();
      check(await page.locator("#cover_file").evaluate((el) => el.files.length) === 0, "Resume was incorrectly attached as cover letter.");
      const chooser = page.waitForEvent("filechooser");
      await page.locator("#smartjobapply-panel [data-action='attach-document'][data-field-id='cover_file']").click();
      await (await chooser).setFiles({ name: "synthetic-cover-letter.txt", mimeType: "text/plain", buffer: Buffer.from("Fictional test document, not a real application.") });
      await page.waitForFunction(() => !document.querySelector("#smartjobapply-panel [data-action='attach-document'][data-field-id='cover_file']"));
      check(await page.locator("#resume").evaluate((el) => el.files[0]?.name) === "synthetic-resume.pdf", "Cover letter replaced the resume.");
      await fill(page);
      const contacts = { first_name: scenario === "edge-cases" ? "Already reviewed" : "Avery", last_name: "Morgan", email: "avery@example.test", phone: "+1 202-555-0142", city: "Austin", state: "Texas", country: "United States", postal_code: "78701", linkedin_url: "https://www.linkedin.com/in/synthetic-avery" };
      for (const [id, value] of Object.entries(contacts)) {
        const actual = await page.locator(`#${id}`).inputValue();
        check(id === "phone" ? actual.replace(/\D/g, "") === value.replace(/\D/g, "") : actual === value, `${id}: wrong contact answer (${actual}).`);
      }
      if (scenario === "multi-step") {
        // User initiates navigation; the extension must discover fresh controls.
        await page.locator("#next-step").click();
        await page.waitForFunction(() => document.querySelector("#smartjobapply-panel")?.textContent.includes("Rust experience"), null, { timeout: 15000 });
        await fill(page);
      }
      const expected = { authorized: "Yes", current_sponsorship: "No", future_sponsorship: "Yes", relocation: "No", travel: "Yes", language: scenario === "edge-cases" ? "" : "Java", start_date: "2026-10-01", gender: "", unknown_years: "" };
      for (const [id, value] of Object.entries(expected)) {
        const actual = await page.locator(`#${id}`).inputValue(); record.final_values[id] = actual;
        check(actual === value, `${id}: expected ${JSON.stringify(value)}, got ${JSON.stringify(actual)}.`);
      }
      if (scenario === "edge-cases") check(await page.locator("#ambiguous_authorization").inputValue() === "", "Ambiguous authorization should be left for review.");
      if (provider === "workday") check(await page.locator("#age").textContent() === "Yes", "Lazy Workday age dropdown was not resolved.");
      if (provider === "ashby") check(await page.locator('input[name="lab_skill"][value="Rust"]').isChecked() === false, "Unknown skill selected.");
      // A second fill must preserve a user correction and keep unknowns blank.
      await page.locator("#unknown_years").fill("2");
      await page.locator("#current_sponsorship").selectOption("Yes");
      await page.locator("#start_date").fill("");
      await page.waitForTimeout(800);
      await fill(page);
      check(await page.locator("#unknown_years").inputValue() === "2", "A repeated fill changed the user's answer.");
      check(await page.locator("#current_sponsorship").inputValue() === "Yes", "A repeated fill overwrote a user correction with the profile fact.");
      check(await page.locator("#start_date").inputValue() === "2026-10-01", "A repeated fill failed to populate a newly empty field.");
      if (scenario === "edge-cases") {
        await page.locator("#smartjobapply-panel [data-action='replace-existing']").check();
        await page.waitForTimeout(500);
        await fill(page);
        check(await page.locator("#first_name").inputValue() === "Avery", "Explicit replacement did not use the profile name.");
        check(await page.locator("#current_sponsorship").inputValue() === "No", "Explicit replacement did not use the saved sponsorship fact.");
        check(await page.locator("#smartjobapply-panel [data-action='replace-existing']").isChecked() === false, "Replacement approval did not reset after filling.");
      }
      check(await page.locator("#submit-count").textContent() === "Test submissions: 0", "Extension triggered final submission.");
      if (provider === "greenhouse" && scenario === "complete") {
        await page.screenshot({ path: path.join(root, ".applytex/qa/autofill-lab.png"), fullPage: true });
      }
    } catch (error) { record.errors.push(error.message); }
    await Promise.all(pending);
    record.duration_ms = Date.now() - started;
    record.success = record.errors.length === 0;
    report.assertions += record.assertions;
    report.cases.push(record);
    if (!record.success) report.failures.push({ provider, scenario, errors: record.errors });
    console.log(`${record.success ? "PASS" : "FAIL"} ${provider}/${scenario}: ${record.assertions} assertions${record.errors.length ? " — " + record.errors.join("; ") : ""}`);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, JSON.stringify(report, null, 2) + "\n");
    await page.close();
  }
} finally { await browser.close(); }
report.completed_at = new Date().toISOString();
fs.writeFileSync(out, JSON.stringify(report, null, 2) + "\n");
console.log(`${report.cases.filter((c) => c.success).length}/${report.cases.length} passed; ${report.assertions} assertions. ${out}`);
if (report.failures.length) process.exitCode = 1;

async function ready(page) {
  await page.waitForFunction(() => { const b=document.querySelector("#smartjobapply-panel [data-action='autofill']"); return b && !b.disabled; }, null, { timeout: 15000 });
}

async function fill(page) {
  await ready(page);
  await page.locator("#smartjobapply-panel [data-action='autofill']").click();
  await page.waitForFunction(() => {
    const panel=document.querySelector("#smartjobapply-panel");
    return /filled \d+ reviewed fields/i.test(panel?.textContent || "") && !panel.querySelector(".sja-autofill-progress");
  }, null, { timeout: 60000 });
}
