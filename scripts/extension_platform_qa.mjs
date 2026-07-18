#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");
const EXTENSION_DIR = path.join(ROOT, "extension");
const DEFAULT_OUT = path.join(ROOT, ".applytex", "qa", "extension-platform-qa.json");
const QA_GENERATED_ANSWER = "I build practical AI products across model evaluation, retrieval, APIs, and user-facing workflows. My experience shipping monitored LLM systems and full-stack tools would help me contribute quickly while learning from Cohere's engineering team.";

const args = parseArgs(process.argv.slice(2));
const jobsPerProvider = Number.parseInt(args["jobs-per-provider"] || "50", 10);
const outputPath = path.resolve(args.out || DEFAULT_OUT);
const headed = Boolean(args.headed);

if (!Number.isFinite(jobsPerProvider) || jobsPerProvider < 1) {
  throw new Error("--jobs-per-provider must be a positive integer.");
}

const providersSource = fs.readFileSync(path.join(EXTENSION_DIR, "providers.js"), "utf8");
const panelPath = path.join(EXTENSION_DIR, "panel.js");
const providerRegistry = loadProviderRegistry(providersSource);
const availableProviders = Object.keys(providerRegistry.providers);
const requestedProviders = String(args.provider || "")
  .split(",")
  .map((provider) => provider.trim())
  .filter(Boolean);
const unknownProviders = requestedProviders.filter((provider) => !availableProviders.includes(provider));
if (unknownProviders.length) {
  throw new Error(`Unknown --provider value(s): ${unknownProviders.join(", ")}`);
}
const providers = requestedProviders.length ? requestedProviders : availableProviders;
const verbose = Boolean(args.verbose);
const knownCatalogLabels = loadKnownQuestionCatalog();
const knownCatalogKeys = new Set(knownCatalogLabels.map(normalizeQuestion));
const playwright = await loadPlaywright();

const report = await runQa();
writeReport(report, outputPath);
console.log(renderConsoleSummary(report, outputPath));

async function runQa() {
  if (verbose) console.log("QA: launching browser");
  const browser = await playwright.chromium.launch({
    headless: !headed,
    ...(explicitChromeExecutable() ? { executablePath: explicitChromeExecutable() } : {}),
  });
  if (verbose) console.log("QA: creating browser context");
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1366, height: 920 },
  });
  if (verbose) console.log("QA: installing runtime mock");
  const qaState = createQaState();
  const pageByUrl = new Map();
  for (const provider of providers) {
    for (let index = 1; index <= jobsPerProvider; index += 1) {
      const url = providerUrl(provider, index);
      pageByUrl.set(url, { provider, index });
    }
  }

  await context.addInitScript(() => {
    const store = { applicationByUrl: {}, applytexExtensionProfileId: "qa" };
    window.chrome = {
      runtime: {
        onMessage: { addListener() {} },
        async sendMessage(message) {
          if (message?.type !== "APPLYTEX_API_REQUEST") {
            return { ok: false, status: 400, error: "Unsupported QA runtime message." };
          }
          const proxyUrl = new URL("/__applytex_api_proxy__", window.location.origin);
          proxyUrl.searchParams.set("path", String(message.path || ""));
          const response = await fetch(proxyUrl, {
            method: message.options?.method || "GET",
            headers: { "Content-Type": "application/json" },
            body: message.options?.body,
          });
          const text = await response.text();
          const data = text ? JSON.parse(text) : null;
          return {
            ok: response.ok,
            status: response.status,
            data,
            error: response.ok ? "" : data?.detail || `Local API returned ${response.status}.`,
          };
        },
      },
      storage: {
        local: {
          async get(keys) {
            if (Array.isArray(keys)) {
              return Object.fromEntries(keys.map((key) => [key, store[key]]));
            }
            if (typeof keys === "string") {
              return { [keys]: store[keys] };
            }
            if (keys && typeof keys === "object") {
              return Object.fromEntries(
                Object.entries(keys).map(([key, fallback]) => [
                  key,
                  store[key] === undefined ? fallback : store[key],
                ]),
              );
            }
            return { ...store };
          },
          async set(values) {
            Object.assign(store, values || {});
          },
          async remove(keys) {
            for (const key of Array.isArray(keys) ? keys : [keys]) delete store[key];
          },
        },
      },
    };
  });

  await context.route("http://127.0.0.1:8000/**", (route, request) => handleApiRoute(route, request, qaState));
  await context.route("https://**/*", async (route, request) => {
    const requestUrl = new URL(request.url());
    if (requestUrl.pathname === "/__applytex_api_proxy__") {
      await handleApiRoute(route, request, qaState, requestUrl.searchParams.get("path") || "/");
      return;
    }
    const hit = pageByUrl.get(request.url());
    if (!hit) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: fixtureHtml(hit.provider, hit.index),
    });
  });

  if (verbose) console.log("QA: opening fixture page");
  const page = await context.newPage();
  if (verbose) console.log("QA: fixture page ready");
  const failures = [];
  const records = [];
  page.on("pageerror", (error) => qaState.pageErrors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      qaState.consoleMessages.push(`${message.type()}: ${message.text()}`);
    }
  });

  for (const provider of providers) {
    for (let index = 1; index <= jobsPerProvider; index += 1) {
      const url = providerUrl(provider, index);
      const beforeErrorCount = qaState.consoleMessages.length + qaState.pageErrors.length;
      const record = {
        provider,
        index,
        url,
        success: false,
        resume_uploaded: false,
        scanned_questions: [],
        initial_actions: [],
        initial_review_items: [],
        unresolved_required: [],
        filled_fields: 0,
        skipped_fields: 0,
        required_total: 0,
        required_filled_after_autofill: 0,
        phone_value: "",
        first_fill_panel_text: "",
        panel_text: "",
        errors: [],
      };
      qaState.current = record;
      try {
        if (verbose) console.log(`${provider} fixture ${index}: navigating`);
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: 8000 });
        if (verbose) console.log(`${provider} fixture ${index}: injecting provider registry`);
        await page.addScriptTag({ content: providersSource });
        for (const moduleName of [
          "panel-shared.js",
          "panel-scan.js",
          "panel-fill.js",
          "panel-workday.js",
        ]) {
          if (verbose) console.log(`${provider} fixture ${index}: injecting ${moduleName}`);
          await page.addScriptTag({ path: path.join(EXTENSION_DIR, moduleName) });
        }
        if (verbose) console.log(`${provider} fixture ${index}: injecting panel`);
        await page.addScriptTag({ path: panelPath });
        if (verbose) console.log(`${provider} fixture ${index}: waiting for panel`);
        await page.waitForSelector("#smartjobapply-panel", { timeout: 6000 });
        if (verbose) console.log(`${provider} fixture ${index}: waiting for autofill readiness`);
        await page.waitForFunction(() => {
          const button = document.querySelector("#smartjobapply-panel [data-action='autofill']");
          return Boolean(button && !button.disabled);
        }, { timeout: 6000 });

        if (provider === "ashby") {
          await page.waitForFunction(() => {
            const answer = document.querySelector("#ashby_good_fit")?.value || "";
            const panel = document.querySelector("#smartjobapply-panel")?.textContent || "";
            return answer.length > 0 && panel.includes("Edit draft");
          }, { timeout: 10000 });
        }

        if (provider === "workday") {
          for (const kind of ["education", "work_experience"]) {
            const selector = `#smartjobapply-panel [data-record-kind='${kind}']`;
            if (!await page.locator(selector).count()) continue;
            await page.locator(selector).selectOption("1", { timeout: 3000 });
            await page.waitForFunction(({ selector }) => {
              const panel = document.querySelector("#smartjobapply-panel");
              const select = document.querySelector(selector);
              return select?.value === "1" && !panel?.textContent?.includes("Updating profile record...");
            }, { selector }, { timeout: 6000 });
          }
        }

        if (verbose) console.log(`${provider} fixture ${index}: uploading fixture resume`);
        if (!await page.locator("#smartjobapply-panel [data-action='default-resume']").count()) {
          await page.locator("#smartjobapply-panel [data-tab='tailor']").first().click({ timeout: 3000 });
        }
        await page.locator("#smartjobapply-panel [data-action='default-resume']").click({ timeout: 3000 });
        await page.waitForFunction(() => {
          const panel = document.querySelector("#smartjobapply-panel");
          return panel?.textContent?.includes("Uploaded qa-resume.pdf.");
        }, { timeout: 6000 });
        await page.locator("#smartjobapply-panel [data-tab='autofill']").click({ timeout: 3000 });
        if (verbose) console.log(`${provider} fixture ${index}: filling reviewed fields`);
        await page.locator("#smartjobapply-panel [data-action='autofill']").click({ timeout: 3000 });
        if (provider === "workday") {
          await page.waitForFunction(() => document.querySelector("#smartjobapply-panel .sja-autofill-progress"), null, { timeout: 5000 });
        }
        await page.waitForFunction((provider) => {
          const panel = document.querySelector("#smartjobapply-panel");
          const text = panel?.textContent || "";
          if (provider === "workday") return /Added \d+ records and filled \d+ reviewed fields/.test(text) && !panel?.querySelector(".sja-autofill-progress");
          return /Filled \d+ reviewed fields/.test(text);
        }, provider, { timeout: provider === "workday" ? 60000 : provider === "ashby" ? 30000 : 10000 });
        if (provider === "ashby") await page.waitForTimeout(500);
        record.first_fill_panel_text = await page.locator("#smartjobapply-panel").textContent({ timeout: 3000 }) || "";
        if (provider === "workday") {
          await page.locator("#smartjobapply-panel [data-action='autofill']").click({ timeout: 3000 });
          await page.waitForFunction(() => document.querySelector("#smartjobapply-panel .sja-autofill-progress"), null, { timeout: 5000 });
          await page.waitForFunction(() => {
            const panel = document.querySelector("#smartjobapply-panel");
            const text = panel?.textContent || "";
            return /Added 0 records and filled \d+ reviewed fields/.test(text) && !panel?.querySelector(".sja-autofill-progress");
          }, null, { timeout: 60000 });
          if (args.screenshot) {
            await page.screenshot({ path: path.resolve(args.screenshot), fullPage: false });
          }
        }

        const pageState = await page.evaluate(() => {
          const controls = Array.from(document.querySelectorAll("input, select, textarea, [contenteditable='true']"))
            .filter((element) => !element.closest("#smartjobapply-panel"));
          const required = controls.filter((element) => {
            if (element instanceof HTMLInputElement && element.type === "radio") {
              return element.required || element.getAttribute("aria-required") === "true";
            }
            return element.required || element.getAttribute("aria-required") === "true";
          });
          const valuePresent = (element) => {
            if (element instanceof HTMLInputElement && element.type === "checkbox") return element.checked;
            if (element instanceof HTMLInputElement && element.type === "radio") {
              return Boolean(document.querySelector(`input[type='radio'][name='${CSS.escape(element.name)}']:checked`));
            }
            if (element instanceof HTMLInputElement && element.type === "file") {
              return Boolean(element.files && element.files.length);
            }
            if (element.getAttribute("contenteditable") === "true") return Boolean(element.textContent.trim());
            return Boolean(element.value);
          };
          const requiredFilled = required.filter(valuePresent).length;
          const files = Array.from(document.querySelectorAll("input[type='file']"))
            .filter((input) => input.files && input.files.length)
            .map((input) => input.files[0].name);
          const message = document.querySelector("#smartjobapply-panel")?.textContent || "";
          return {
            required_total: required.length,
            required_filled: requiredFilled,
            file_names: files,
            panel_text: message,
            phone_value: document.querySelector("#phone")?.value || "",
          workday_values: {
              education_institutions: Array.from(document.querySelectorAll("[data-record-kind='education'] input[data-field='school']")).map((input) => input.closest('[data-automation-id="multiSelectContainer"]')?.querySelector("[data-automation-id='selectedItem']")?.dataset.value || input.value),
              education_degrees: Array.from(document.querySelectorAll("[data-record-kind='education'] [id$='-degree']")).map((control) => control.textContent.trim()),
              education_fields: Array.from(document.querySelectorAll("[data-record-kind='education'] [id$='-fieldOfStudy']")).map((input) => Array.from(input.closest('[data-automation-id="multiSelectContainer"]').querySelectorAll("[data-automation-id='selectedItem']")).map((item) => item.dataset.value || item.textContent.trim())),
              education_gpas: Array.from(document.querySelectorAll("[data-record-kind='education'] [id$='-gpa']")).map((input) => input.value),
              education_from_years: Array.from(document.querySelectorAll("[data-record-kind='education'] [id*='firstYearAttended'][data-automation-id='dateSectionYear-input']")).map((input) => input.value),
              education_to_years: Array.from(document.querySelectorAll("[data-record-kind='education'] [id*='lastYearAttended'][data-automation-id='dateSectionYear-input']")).map((input) => input.value),
              experience_titles: Array.from(document.querySelectorAll("[data-record-kind='work_experience'] input[data-field='job-title']")).map((input) => input.value),
              experience_companies: Array.from(document.querySelectorAll("[data-record-kind='work_experience'] input[data-field='company']")).map((input) => input.value),
              experience_from_dates: Array.from(document.querySelectorAll("[data-record-kind='work_experience']")).map((record) => [
                record.querySelector("[id*='startDate'][data-automation-id='dateSectionMonth-input']")?.value || "",
                record.querySelector("[id*='startDate'][data-automation-id='dateSectionYear-input']")?.value || "",
              ].join("/")),
              experience_to_dates: Array.from(document.querySelectorAll("[data-record-kind='work_experience']")).map((record) => [
                record.querySelector("[id*='endDate'][data-automation-id='dateSectionMonth-input']")?.value || "",
                record.querySelector("[id*='endDate'][data-automation-id='dateSectionYear-input']")?.value || "",
              ].join("/")),
              role_descriptions: Array.from(document.querySelectorAll("[data-record-kind='work_experience'] textarea[id$='-roleDescription']")).map((input) => input.value),
              optional_values: Array.from(document.querySelectorAll("[data-optional-workday] input, [data-optional-workday] textarea")).map((input) => input.value),
              skills: Array.from(document.querySelectorAll("#workday-skills-control [data-automation-id='selectedItem']")).map((item) => item.dataset.value || item.textContent.trim()),
              education_count: document.querySelectorAll("[data-record-kind='education']").length,
              experience_count: document.querySelectorAll("[data-record-kind='work_experience']").length,
              save_clicks: Number(document.body.dataset.saveClicks || 0),
            },
            ashby_engineering_interests: Array.from(document.querySelectorAll("[data-fixture-question='engineering-interests'] input[type='checkbox']"))
              .filter((input) => input.checked)
              .map((input) => input.labels?.[0]?.textContent?.trim() || input.name),
            ashby_good_fit: document.querySelector("#ashby_good_fit")?.value || "",
          };
        });
        const scansForUrl = qaState.scansByUrl.get(url) || [];
        const latestScan = scansForUrl.at(-1);
        const initialScan = scansForUrl[0];
        const latestPlan = qaState.plansByUrl.get(url)?.[0];
        if (initialScan) {
          record.scanned_questions = initialScan.questions;
        }
        if (latestPlan) {
          record.initial_actions = latestPlan.actions;
          record.initial_review_items = latestPlan.review_items;
          record.unresolved_required = latestPlan.unresolved_required;
        }
        record.resume_uploaded = pageState.file_names.includes("qa-resume.pdf");
        record.required_total = latestScan
          ? latestScan.questions.filter((question) => question.required).length
          : pageState.required_total;
        record.required_filled_after_autofill = latestScan
          ? latestScan.questions.filter((question) => question.required && question.current_value_present).length
          : pageState.required_filled;
        record.filled_fields = filledCountFromPanelText(pageState.panel_text);
        record.skipped_fields = skippedCountFromActions(record.initial_actions);
        record.phone_value = pageState.phone_value;
        record.panel_text = pageState.panel_text;
        if (record.scanned_questions[0]?.field_id !== "first_name") {
          record.errors.push("Scanned questions were not returned in document order.");
        }
        if (provider === "ashby" && latestScan) {
          const grouped = latestScan.questions.filter((item) => item.label === "What area of software engineering interests you the most?");
          if (grouped.length !== 1 || grouped[0].control_kind !== "multi_select" || grouped[0].options.length !== 6) {
            record.errors.push(`Ashby checkbox group was scanned incorrectly: ${JSON.stringify(grouped)}.`);
          }
          if (JSON.stringify(pageState.ashby_engineering_interests) !== JSON.stringify(["Backend Development", "Full-stack Development", "Inference and Distributed Training"])) {
            record.errors.push(`Ashby checkbox group was filled incorrectly: ${JSON.stringify(pageState.ashby_engineering_interests)}.`);
          }
          if (pageState.ashby_good_fit !== QA_GENERATED_ANSWER) {
            record.errors.push(`Ashby AI draft was not filled automatically: ${JSON.stringify(pageState.ashby_good_fit)}.`);
          }
          if (!pageState.panel_text.includes("Edit draft")) {
            record.errors.push("Ashby generated answer did not expose an Edit draft action.");
          }
          const capturedJob = Array.from(qaState.jobs.values()).find((job) => job.source_url === url);
          if (capturedJob?.company !== companyFor("ashby")) {
            record.errors.push(`Ashby company was captured as ${JSON.stringify(capturedJob?.company)}.`);
          }
          for (const fieldId of ["authorized_to_work", "requires_sponsorship"]) {
            const question = latestScan.questions.find((item) => item.field_id === fieldId);
            if (!question?.current_value_present) {
              const state = await page.evaluate((name) => {
                const input = document.querySelector(`input[name='${name}']`);
                const buttons = Array.from(input?.parentElement?.querySelectorAll(":scope > button") || []);
                return buttons.map((button) => ({ text: button.textContent.trim(), className: button.className }));
              }, fieldId);
              record.errors.push(`Ashby Yes/No field ${fieldId} was not filled (${JSON.stringify(state)}).`);
            }
          }
        }
        if (record.phone_value.replace(/\D/g, "") !== "2025550142") {
          record.errors.push(`Split phone field contained ${JSON.stringify(record.phone_value)} instead of the national number.`);
        }
        if (provider === "workday") {
          record.workday_record_values = pageState.workday_values;
          const phantomHeaderQuestions = latestScan.questions.filter((question) =>
            /settings|ashrith\.vadde@gmail\.com|^english\b/i.test(question.label || ""),
          );
          if (phantomHeaderQuestions.length) {
            record.errors.push(`Workday header controls leaked into the form scan: ${JSON.stringify(phantomHeaderQuestions)}.`);
          }
          const skillsQuestion = latestScan.questions.find((question) => question.field_id === "skills--skills");
          if (!skillsQuestion || /education/i.test(skillsQuestion.label || "") || !/type to add skills/i.test(skillsQuestion.label || "")) {
            record.errors.push(`Workday Skills was labelled incorrectly: ${JSON.stringify(skillsQuestion)}.`);
          }
          const transientPromptQuestions = scansForUrl
            .flatMap((scan) => scan.questions || [])
            .filter((question) => /^(accounting|actuarial science|study option \d+)$/i.test(question.label || "") || /radiobtn/i.test(question.field_id || ""));
          if (transientPromptQuestions.length) {
            record.errors.push(`Workday prompt options leaked into the form scan: ${JSON.stringify(transientPromptQuestions.slice(0, 5))}.`);
          }
          if (JSON.stringify(pageState.workday_values.education_institutions) !== JSON.stringify(["UNIVERSITY OF HOUSTON", "Amrita School of Engineering"])) {
            record.errors.push(`Workday education records were ${JSON.stringify(pageState.workday_values.education_institutions)}.`);
          }
          if (JSON.stringify(pageState.workday_values.education_degrees) !== JSON.stringify(["MS", "BS"])) {
            record.errors.push(`Workday education degrees were ${JSON.stringify(pageState.workday_values.education_degrees)}.`);
          }
          if (JSON.stringify(pageState.workday_values.education_fields) !== JSON.stringify([["Computer and Information Science"], ["Computer Science"]])) {
            record.errors.push(`Workday education fields were ${JSON.stringify(pageState.workday_values.education_fields)}.`);
          }
          if (JSON.stringify(pageState.workday_values.education_gpas) !== JSON.stringify(["4/4", "8.43/10"]) ||
              JSON.stringify(pageState.workday_values.education_from_years) !== JSON.stringify(["2025", "2019"]) ||
              JSON.stringify(pageState.workday_values.education_to_years) !== JSON.stringify(["2027", "2023"])) {
            record.errors.push(`Workday education scalar values were ${JSON.stringify(pageState.workday_values)}.`);
          }
          if (JSON.stringify(pageState.workday_values.experience_titles) !== JSON.stringify(["AI/ML Engineer", "Project Intern – Explainable AI"])) {
            record.errors.push(`Workday experience records were ${JSON.stringify(pageState.workday_values.experience_titles)}.`);
          }
          if (JSON.stringify(pageState.workday_values.experience_companies) !== JSON.stringify(["Accenture", "Samsung PRISM"])) {
            record.errors.push(`Workday experience companies were ${JSON.stringify(pageState.workday_values.experience_companies)}.`);
          }
          if (JSON.stringify(pageState.workday_values.experience_from_dates) !== JSON.stringify(["11/2023", "09/2021"]) ||
              JSON.stringify(pageState.workday_values.experience_to_dates) !== JSON.stringify(["08/2025", "04/2022"])) {
            record.errors.push(`Workday experience dates were ${JSON.stringify(pageState.workday_values)}.`);
          }
          const expectedSkills = [
            "Reinforcement Learning",
            "Git",
            "GitHub",
            "Microsoft Azure",
            "Python (programming language)",
            "Structured Query Language(SQL)",
            "Java (programming language)",
          ];
          if (JSON.stringify(pageState.workday_values.skills) !== JSON.stringify(expectedSkills)) {
            record.errors.push(`Workday skills were ${JSON.stringify(pageState.workday_values.skills)}.`);
          }
          const firstFillPanelText = record.first_fill_panel_text || pageState.panel_text;
          if (!firstFillPanelText.includes("Claude") || !firstFillPanelText.includes("Gemini")) {
            record.errors.push("Workday unavailable skills Claude and Gemini were not reported.");
          }
          if (pageState.workday_values.education_count !== 2 || pageState.workday_values.experience_count !== 2) {
            record.errors.push(`Workday record counts were ${JSON.stringify(pageState.workday_values)}.`);
          }
          if (JSON.stringify(pageState.workday_values.role_descriptions) !== JSON.stringify(["Built production RAG systems.", "Developed explainable AI systems."])) {
            record.errors.push(`Workday role descriptions were ${JSON.stringify(pageState.workday_values.role_descriptions)}.`);
          }
          if (pageState.workday_values.optional_values.some(Boolean)) {
            record.errors.push(`Workday optional sections were modified: ${JSON.stringify(pageState.workday_values)}.`);
          }
          if (pageState.workday_values.save_clicks !== 0) {
            record.errors.push("Workday Save and Continue was clicked.");
          }
          const isolation = await verifyWorkdayRequiredDropdownIsolation(page);
          record.workday_failure_isolation = isolation;
          if (isolation.failed_record_school !== "UNIVERSITY OF HOUSTON" || isolation.next_record_school !== "Amrita School of Engineering") {
            record.errors.push(`Workday dropdown failure was not isolated to one record: ${JSON.stringify(isolation)}.`);
          }
          if (!isolation.panel_text.toLowerCase().includes("failed: option unavailable")) {
            record.errors.push(`Workday dropdown failure was not written to its review item: ${JSON.stringify(isolation)}.`);
          }
          if (isolation.education_count !== 2 || isolation.save_clicks !== 0) {
            record.errors.push(`Workday failure recovery changed record count or submitted: ${JSON.stringify(isolation)}.`);
          }
          const applicationQuestions = await verifyWorkdayApplicationQuestions(page, qaState, url);
          record.workday_application_questions = applicationQuestions;
          const expectedQuestionValues = [
            "Yes", "No", "Yes", "Yes", "Bachelor's Degree", "75000",
            "No", "No", "Yes", "Yes", "No",
          ];
          if (applicationQuestions.question_count !== 11 || applicationQuestions.required_count !== 11) {
            record.errors.push(`Workday Application Questions scan was ${JSON.stringify(applicationQuestions)}.`);
          }
          if (applicationQuestions.long_label_length <= 320) {
            record.errors.push("Workday long future-sponsorship question was truncated or omitted.");
          }
          if (JSON.stringify(applicationQuestions.values) !== JSON.stringify(expectedQuestionValues)) {
            record.errors.push(`Workday Application Questions values were ${JSON.stringify(applicationQuestions.values)}.`);
          }
          if (applicationQuestions.save_clicks !== 0) {
            record.errors.push("Workday Application Questions clicked Save and Continue.");
          }
          if (applicationQuestions.second_menu_opens !== applicationQuestions.first_menu_opens) {
            record.errors.push(`Workday second fill was not idempotent: ${JSON.stringify(applicationQuestions)}.`);
          }
          if (applicationQuestions.stale_failure_visible) {
            record.errors.push("My Experience failures leaked into the Application Questions step.");
          }
        }
        const newErrors = qaState.consoleMessages.length + qaState.pageErrors.length - beforeErrorCount;
        if (newErrors > 0) {
          record.errors.push(`Browser emitted ${newErrors} console/page errors.`);
        }
        record.success = record.scanned_questions.length > 0 &&
          record.required_filled_after_autofill > 0 &&
          record.errors.length === 0;
      } catch (error) {
        record.panel_text = await page.locator("#smartjobapply-panel").textContent().catch(() => "") || "";
        const failedScans = qaState.scansByUrl.get(url) || [];
        record.scanned_questions = failedScans.at(-1)?.questions || record.scanned_questions;
        const failedPlan = qaState.plansByUrl.get(url)?.at(-1);
        record.initial_actions = failedPlan?.actions || record.initial_actions;
        record.unresolved_required = failedPlan?.unresolved_required || record.unresolved_required;
        record.errors.push(error.message || String(error));
      } finally {
        if (!record.success) failures.push(record);
        records.push(record);
        if (verbose) {
          console.log(`${provider} fixture ${index}: ${record.success ? "pass" : "fail"} ${record.errors.join(" | ")}`);
        }
        qaState.current = null;
      }
    }
  }

  await browser.close();
  return summarize(records, failures, qaState);
}

async function verifyWorkdayRequiredDropdownIsolation(page) {
  await page.evaluate(() => {
    const failedSchool = document.querySelector("#education-1-school");
    const nextSchool = document.querySelector("#education-2-school");
    const degree = document.querySelector("#education-1-degree");
    for (const input of [failedSchool, nextSchool]) {
      const shell = input.closest('[data-automation-id="multiSelectContainer"]');
      shell?.querySelectorAll('[data-automation-id="selectedItem"]').forEach((item) => item.remove());
      const count = shell?.querySelector('.selected-count');
      if (count) count.textContent = '0 items selected';
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const replacement = degree.cloneNode(true);
    replacement.textContent = "Select One";
    replacement.addEventListener("click", () => openWorkdayOptions(replacement, ["Associate's Degree"]));
    degree.replaceWith(replacement);
  });
  await page.waitForTimeout(900);
  await page.locator("#smartjobapply-panel [data-action='autofill']").click({ timeout: 3000 });
  await page.waitForFunction(() => document.querySelector("#smartjobapply-panel .sja-autofill-progress"), null, { timeout: 5000 });
  await page.waitForFunction(() => {
    const panel = document.querySelector("#smartjobapply-panel");
    const text = panel?.textContent || "";
    return /Added 0 records and filled \d+ reviewed fields/.test(text) && !panel?.querySelector(".sja-autofill-progress");
  }, null, { timeout: 60000 });
  return page.evaluate(() => ({
    failed_record_school: document.querySelector("#education-1-school")?.closest('[data-automation-id="multiSelectContainer"]')?.querySelector('[data-automation-id="selectedItem"]')?.dataset.value || "",
    next_record_school: document.querySelector("#education-2-school")?.closest('[data-automation-id="multiSelectContainer"]')?.querySelector('[data-automation-id="selectedItem"]')?.dataset.value || "",
    education_count: document.querySelectorAll("[data-record-kind='education']").length,
    save_clicks: Number(document.body.dataset.saveClicks || 0),
    panel_text: document.querySelector("#smartjobapply-panel")?.textContent || "",
  }));
}

async function verifyWorkdayApplicationQuestions(page, qaState, url) {
  const labels = [
    "Are you legally eligible to work in the country to which you are applying?*",
    "Do you currently require sponsorship for work visa status (e.g. H-1B visa) to work in the country you are applying?*",
    "Will you in the future require sponsorship for work visa status (e.g. H-1B visa) in the country for which you are applying? Please note that if you currently have CPT or OPT work authorization and will not have any other basis for work authorization after the expiration of your OPT, you must answer yes to this question.*",
    "Are you at least 18 years of age?*",
    "What is your highest level of completed education?*",
    "What is your desired income? (Hourly, Monthly, or Annual)*",
    "Are you currently employed by Daikin Applied? Internal candidates must apply through the internal Workday Jobs Hub.*",
    "Are you currently employed by a Daikin Subsidiary, Daikin Majority Owned Representative, or Member of Daikin Group?*",
    "Are you willing to relocate if required by the position?*",
    "Are you willing to travel if required by the position?*",
    "Do you currently have an active non-compete and/or non-solicit?*",
  ];
  await page.evaluate((questionLabels) => {
    document.querySelector("[data-automation-id='applyFlowMyExpPage']")?.remove();
    document.querySelector("[data-automation-id='progressBarActiveStep']")?.remove();
    const step = document.createElement("div");
    step.dataset.automationId = "progressBarActiveStep";
    step.textContent = "Application Questions";
    document.body.prepend(step);
    const form = document.querySelector("#application-form");
    form.innerHTML = "";
    document.body.dataset.saveClicks = "0";
    document.body.dataset.workdayQuestionMenuOpens = "0";
    questionLabels.forEach((label, index) => {
      const group = document.createElement("section");
      group.setAttribute("role", "group");
      group.className = "workday-application-question";
      const prompt = document.createElement("p");
      prompt.textContent = label;
      group.append(prompt);
      if (index === 5) {
        const textarea = document.createElement("textarea");
        textarea.id = `primaryQuestionnaire--${index}`;
        textarea.required = true;
        group.append(textarea);
      } else {
        const button = document.createElement("button");
        button.type = "button";
        button.id = `primaryQuestionnaire--${index}`;
        button.className = "workday-question-select";
        button.setAttribute("aria-haspopup", "listbox");
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-label", "Select One Required");
        button.textContent = "Select One";
        if (index === 1) button.dataset.failFirstOpen = "true";
        if (index === 4) button.dataset.replaceOnCommit = "true";
        group.append(button);
      }
      form.append(group);
    });
    const save = document.createElement("button");
    save.type = "button";
    save.id = "save-and-continue";
    save.textContent = "Save and Continue";
    save.addEventListener("click", () => {
      document.body.dataset.saveClicks = String(Number(document.body.dataset.saveClicks || 0) + 1);
    });
    form.append(save);
    form.addEventListener("click", (event) => {
      const button = event.target.closest(".workday-question-select");
      if (!button) return;
      if (button.dataset.failFirstOpen === "true" && button.dataset.failedOnce !== "true") {
        button.dataset.failedOnce = "true";
        return;
      }
      document.querySelectorAll(".workday-question-listbox").forEach((item) => item.remove());
      button.setAttribute("aria-expanded", "true");
      window.setTimeout(() => {
        if (!button.isConnected || button.getAttribute("aria-expanded") !== "true") return;
        document.body.dataset.workdayQuestionMenuOpens = String(Number(document.body.dataset.workdayQuestionMenuOpens || 0) + 1);
        const list = document.createElement("div");
        list.setAttribute("role", "listbox");
        list.className = "workday-question-listbox";
        const options = button.id.endsWith("--4")
          ? ["Select One", "High School", "Bachelor's Degree", "Master's Degree"]
          : ["Select One", "Yes", "No"];
        options.forEach((value, optionIndex) => {
          const option = document.createElement("div");
          option.setAttribute("role", "option");
          option.textContent = value;
          if (optionIndex === 0) option.setAttribute("aria-disabled", "true");
          option.addEventListener("click", () => {
            const current = document.getElementById(button.id);
            if (!current) return;
            const target = current.dataset.replaceOnCommit === "true" ? current.cloneNode(true) : current;
            target.textContent = value;
            target.setAttribute("aria-expanded", "false");
            if (target !== current) current.replaceWith(target);
            list.remove();
          });
          list.append(option);
        });
        document.body.append(list);
      }, 120);
    });
    window.dispatchEvent(new CustomEvent("smartjobapply:open"));
  }, labels);

  await page.waitForFunction(() => {
    const panel = document.querySelector("#smartjobapply-panel")?.textContent || "";
    const button = document.querySelector("#smartjobapply-panel [data-action='autofill']");
    return panel.includes("Required 0/11") && button && !button.disabled;
  }, null, { timeout: 10000 });
  const beforeFillPanel = await page.locator("#smartjobapply-panel").textContent() || "";
  const stepScan = (qaState.scansByUrl.get(url) || []).at(-1);
  await page.locator("#smartjobapply-panel [data-action='autofill']").click({ timeout: 3000 });
  await page.waitForFunction(() => {
    const panel = document.querySelector("#smartjobapply-panel")?.textContent || "";
    return /Filled \d+ reviewed fields/.test(panel) && !panel.includes("Filling reviewed fields...");
  }, null, { timeout: 35000 });
  const firstMenuOpens = await page.evaluate(() => Number(document.body.dataset.workdayQuestionMenuOpens || 0));
  await page.locator("#smartjobapply-panel [data-action='autofill']").click({ timeout: 3000 });
  await page.waitForTimeout(900);
  return page.evaluate(({ questionCount, requiredCount, longLabelLength, signature, firstOpens, staleFailureVisible }) => ({
    question_count: questionCount,
    required_count: requiredCount,
    long_label_length: longLabelLength,
    form_signature: signature,
    values: Array.from(document.querySelectorAll(".workday-application-question")).map((group) => {
      const control = group.querySelector("button, textarea");
      return control instanceof HTMLTextAreaElement ? control.value : control.textContent.trim();
    }),
    first_menu_opens: firstOpens,
    second_menu_opens: Number(document.body.dataset.workdayQuestionMenuOpens || 0),
    save_clicks: Number(document.body.dataset.saveClicks || 0),
    stale_failure_visible: staleFailureVisible,
  }), {
    questionCount: stepScan?.questions?.length || 0,
    requiredCount: stepScan?.questions?.filter((question) => question.required).length || 0,
    longLabelLength: stepScan?.questions?.[2]?.label?.length || 0,
    signature: stepScan?.form_signature || "",
    firstOpens: firstMenuOpens,
    staleFailureVisible: /failed:\s*option unavailable/i.test(beforeFillPanel),
  });
}

function createQaState() {
  return {
    current: null,
    jobs: new Map(),
    applications: new Map(),
    scans: new Map(),
    scansByUrl: new Map(),
    plansByUrl: new Map(),
    consoleMessages: [],
    pageErrors: [],
  };
}

async function handleApiRoute(route, request, qaState, pathOverride = "") {
  const corsHeaders = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-allow-private-network": "true",
  };
  if (request.method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: corsHeaders, body: "" });
    return;
  }
  const url = pathOverride
    ? new URL(pathOverride, "http://127.0.0.1:8000")
    : new URL(request.url());
  const sendJson = async (payload, status = 200) => {
    await route.fulfill({
      status,
      headers: { ...corsHeaders, "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  };

  if (url.pathname === "/health") {
    await sendJson({ status: "ok" });
    return;
  }
  if (url.pathname === "/auth/status") {
    await sendJson({ auth_required: false, authenticated: true, profile_id: "qa", has_password: false });
    return;
  }
  if (url.pathname === "/profiles") {
    await sendJson({ profiles: [{ profile_id: "qa", full_name: "ApplyTeX QA Candidate", usable: true }] });
    return;
  }
  if (url.pathname === "/profile/active" && request.method() === "PUT") {
    await sendJson({ profile_id: "qa" });
    return;
  }
  if (url.pathname === "/profile/active") {
    await sendJson({
      profile_id: "qa",
      full_name: "ApplyTeX QA Candidate",
      email: "qa@example.test",
      resume_filename: "qa-resume.pdf",
      has_pdf: true,
      has_latex_source: false,
    });
    return;
  }
  if (url.pathname === "/profile/view") {
    await sendJson({
      profile_id: "qa",
      full_name: "ApplyTeX QA Candidate",
      first_name: "ApplyTeX",
      last_name: "Candidate",
      email: "qa@example.test",
      phone: "+1 202-555-0142",
      location: "Austin, TX",
      address: { city: "Austin", state: "Texas", postal_code: "78701", country: "United States" },
      linkedin_url: "https://www.linkedin.com/in/applytex-qa",
      portfolio_url: "https://applytex-qa.example.test",
      github_url: "https://github.com/applytex-qa",
      skills: ["Claude", "Gemini", "Reinforcement Learning", "Git/GitHub", "Azure", "Python", "SQL", "Java"],
      education: { school: "University of Houston", degree: "M.S. in Engineering Data Science & Artificial Intelligence", degree_level: "MS", major: "Data Science", field_of_study_candidates: ["Data Science", "Computer Engineering"], start_date: "2025-08", end_date: "2027-05", gpa: "4.0/4.0" },
      educations: [
        { school: "University of Houston", degree: "M.S. in Engineering Data Science & Artificial Intelligence", degree_level: "MS", major: "Data Science", field_of_study_candidates: ["Data Science", "Computer Engineering"], start_date: "2025-08", end_date: "2027-05", gpa: "4.0/4.0" },
        { school: "Amrita School of Engineering", degree: "B.Tech in Computer Science and Engineering (Artificial Intelligence)", degree_level: "BS", major: "Computer Science and Engineering", field_of_study_candidates: ["Computer Science", "Computer Engineering"], start_date: "2019-06", end_date: "2023-05", gpa: "8.43/10" },
      ],
      work_experiences: [
        { company: "Accenture", job_title: "AI/ML Engineer", location: "Hyderabad, India", start_date: "2023-11", end_date: "2025-08" },
        { company: "Samsung PRISM", job_title: "Project Intern – Explainable AI", location: "Bengaluru, India", start_date: "2021-09", end_date: "2022-04" },
      ],
      work_authorization: { authorized_to_work_in_us: true, requires_sponsorship: false },
      equal_opportunity: { allow_autofill: false, sexual_orientation: [] },
      search_preferences: {},
      custom_answers: {},
    });
    return;
  }
  if (url.pathname === "/profile/resume") {
    await sendJson({
      has_pdf: true,
      has_latex_source: false,
      resume_filename: "qa-resume.pdf",
      resume_pdf_filename: "qa-resume.pdf",
      updated_at: new Date().toISOString(),
    });
    return;
  }
  if (url.pathname === "/extension/resume/prepare") {
    await sendJson({
      filename: "qa-resume.pdf",
      mime_type: "application/pdf",
      data_b64: Buffer.from("%PDF-1.4\n% ApplyTeX QA synthetic resume\n").toString("base64"),
      customized: false,
      warnings: [],
      ats_score: null,
      overflow: false,
    });
    return;
  }
  if (url.pathname === "/extension/jobs/capture" && request.method() === "POST") {
    const body = await request.postDataJSON();
    const job = {
      job_id: stableJobId(body.provider, body.source_url),
      provider: body.provider,
      board_token: boardTokenFromUrl(body.source_url),
      external_id: body.external_id || stableJobId(body.provider, body.source_url),
      company: body.company,
      title: body.title,
      description: body.description,
      location: body.location || "",
      workplace_type: "unknown",
      source_url: body.source_url,
      apply_url: body.apply_url,
      published_at: body.published_at || null,
      retrieved_at: new Date().toISOString(),
      industry: "Synthetic QA",
      target_role: "ai_engineer",
      employment_track: "full_time",
      search_score: 0,
    };
    qaState.jobs.set(job.job_id, job);
    await sendJson(job);
    return;
  }
  if (url.pathname === "/applications" && request.method() === "POST") {
    const body = await request.postDataJSON();
    const application = {
      application_id: `qa-app-${qaState.applications.size + 1}`,
      job_id: body.job_id,
      status: "discovered",
      resume_session_id: null,
      notes: "",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      approved_at: null,
      submitted_at: null,
    };
    qaState.applications.set(application.application_id, application);
    await sendJson(application);
    return;
  }
  const applicationDetailMatch = url.pathname.match(/^\/applications\/([^/]+)$/);
  if (applicationDetailMatch && request.method() === "GET") {
    const application = qaState.applications.get(applicationDetailMatch[1]);
    if (!application) {
      await sendJson({ detail: "Application not found" }, 404);
      return;
    }
    await sendJson({
      application,
      job: qaState.jobs.get(application.job_id) || null,
      artifacts: [],
      events: [],
      tasks: [],
      latest_form_scan: null,
    });
    return;
  }
  if (/^\/applications\/[^/]+\/artifacts\/latest$/.test(url.pathname)) {
    await sendJson(null);
    return;
  }
  const scoreMatch = url.pathname.match(/^\/applications\/([^/]+)\/score$/);
  if (scoreMatch && request.method() === "POST") {
    const application = qaState.applications.get(scoreMatch[1]);
    await sendJson({ application, score: null, required_missing: [], preferred_missing: [], keyword_misses: [] });
    return;
  }
  if (url.pathname === "/extension/forms/scan" && request.method() === "POST") {
    const body = await request.postDataJSON();
    const scan = {
      scan_id: `qa-scan-${qaState.scans.size + 1}`,
      application_id: body.application_id || null,
      provider: body.provider,
      page_url: body.page_url,
      page_title: body.page_title || "",
      step_key: body.step_key || "",
      form_signature: body.form_signature || "",
      questions: body.questions || [],
      captured_at: new Date().toISOString(),
    };
    qaState.scans.set(scan.scan_id, scan);
    if (!qaState.scansByUrl.has(scan.page_url)) qaState.scansByUrl.set(scan.page_url, []);
    qaState.scansByUrl.get(scan.page_url).push(scan);
    if (qaState.current && qaState.current.url === scan.page_url) {
      qaState.current.scanned_questions = scan.questions;
    }
    await sendJson(scan);
    return;
  }
  const answerDraftMatch = url.pathname.match(/^\/extension\/forms\/([^/]+)\/answers\/draft$/);
  if (answerDraftMatch && request.method() === "POST") {
    const scan = qaState.scans.get(answerDraftMatch[1]);
    if (!scan) {
      await sendJson({ detail: `Unknown scan_id: ${answerDraftMatch[1]}` }, 404);
      return;
    }
    const body = await request.postDataJSON();
    await sendJson({
      field_id: body.field_id,
      answer: QA_GENERATED_ANSWER,
      evidence: [{ evidence_id: "qa-resume", label: "QA resume" }],
      sources: [{ title: "Official company engineering", url: "https://cohere.com/research" }],
      warnings: [],
      provider: "qa",
      model: "qa-draft-model",
      word_count: QA_GENERATED_ANSWER.split(/\s+/).length,
      generation_ms: 1,
    });
    return;
  }
  const planMatch = url.pathname.match(/^\/extension\/forms\/([^/]+)\/plan$/);
  if (planMatch) {
    const scan = qaState.scans.get(planMatch[1]);
    if (!scan) {
      await sendJson({ detail: `Unknown scan_id: ${planMatch[1]}` }, 404);
      return;
    }
    const requestBody = request.method() === "POST" ? await request.postDataJSON() : {};
    const plan = buildFillPlan(scan, requestBody);
    if (!qaState.plansByUrl.has(scan.page_url)) qaState.plansByUrl.set(scan.page_url, []);
    qaState.plansByUrl.get(scan.page_url).push(plan);
    await sendJson(plan);
    return;
  }
  await sendJson({ detail: `Unhandled QA API route: ${url.pathname}` }, 404);
}

function buildFillPlan(scan, requestBody = {}) {
  const overrides = requestBody.overrides || {};
  const answerSource = requestBody.answer_source || "user_input";
  const actions = scan.questions.map((question) => (
    Object.prototype.hasOwnProperty.call(overrides, question.field_id)
      ? typedAction(question, overrides[question.field_id], answerSource)
      : resolveQuestion(question)
  ));
  const unresolvedRequired = scan.questions
    .filter((question, index) => question.required && !question.current_value_present && actions[index].action === "skip")
    .map((question) => question.label);
  const reviewItems = scan.questions.map((question, index) => {
    const action = actions[index];
    const questionIntent = qaQuestionIntent(question);
    return {
      field_id: question.field_id,
      label: question.label,
      status: question.current_value_present || action.action !== "skip" ? "ready" : "skipped",
      required: Boolean(question.required),
      answer_source: question.current_value_present ? "already_on_page" : action.answer_source,
      value_preview: question.current_value_present ? "Already filled" : previewFillValue(action.value),
      change_kind: question.current_value_present && action.action === "skip" ? "keep" : question.current_value_present ? "replace" : action.action !== "skip" ? "fill" : "unresolved",
      current_value_preview: previewFillValue(question.current_value),
      planned_value_preview: previewFillValue(action.value),
      failure_status: null,
      question_intent: questionIntent,
      draft_eligible: questionIntent === "narrative",
      resolution_reason: action.action === "skip" ? "No explicit profile fact is saved." : "Resolved from an explicit profile fact.",
    };
  });
  return {
    scan_id: scan.scan_id,
    page_url: scan.page_url,
    actions,
    review_items: reviewItems,
    unresolved_required: unresolvedRequired,
    can_fill: unresolvedRequired.length === 0,
  };
}

function qaQuestionIntent(question) {
  const label = normalizeText(question.label);
  if (/sponsor|sponsorship/.test(label)) return label.includes("future") ? "future_sponsorship" : label.includes("current") ? "current_sponsorship" : "sponsorship";
  if (/eligible|authorized/.test(label) && label.includes("work")) return "authorization";
  if (/at least 18|18 years of age/.test(label)) return "age";
  if (label.includes("highest level of completed education")) return "completed_education";
  if (/desired income|desired salary|compensation expectation/.test(label)) return "compensation";
  if (label.includes("majority owned") || label.includes("subsidiary") || label.includes("affiliate")) return "affiliate_employment";
  if (label.includes("currently employed by")) return "company_employment";
  if (label.includes("relocat")) return "relocation";
  if (/willing to travel|travel if required/.test(label)) return "travel";
  if (/non compete|non solicit|restrictive agreement/.test(label)) return "restrictive_agreement";
  if (["textarea", "contenteditable"].includes(question.input_type) && /why |describe |tell us|what makes|cover letter|experience with/.test(label)) return "narrative";
  return question.profile_record_kind ? "record_field" : "unknown";
}

function resolveQuestion(question) {
  const label = normalizeText(question.label);
  const sensitive = question.sensitive || /race|ethnicity|gender|disability|veteran|sexual orientation|date of birth|social security|hispanic|latino/.test(label);
  if (sensitive) {
    return fillAction(question, "skip", null, "eeo_opt_in");
  }
  if (question.input_type === "file" && /\b(resume|cv)\b/.test(label)) {
    return fillAction(question, "upload", null, "resume");
  }
  if (question.input_type === "file") {
    return fillAction(question, "skip", null, question.required ? "user_input" : "none");
  }
  if (label === "what area of software engineering interests you the most") {
    return fillAction(
      question,
      "select_many",
      ["Backend Development", "Full-stack Development", "Inference and Distributed Training"],
      "custom_answer",
    );
  }
  if ((label.includes("authorized") || label.includes("eligible")) && (label.includes("work") || label.includes("country") || label.includes("united states") || label.includes("u s"))) {
    return booleanAction(question, true);
  }
  if (label.includes("sponsor") || label.includes("sponsorship")) {
    return booleanAction(question, label.includes("future"));
  }
  if (/at least 18|18 years of age/.test(label)) return booleanAction(question, true);
  if (label.includes("highest level of completed education")) return typedAction(question, "Bachelor's Degree", "profile");
  if (label.includes("desired income")) return typedAction(question, "75000", "profile");
  if (label.includes("currently employed by")) return booleanAction(question, false);
  if (label.includes("willing to relocate")) return booleanAction(question, true);
  if (label.includes("willing to travel")) return booleanAction(question, true);
  if (/bound by any agreement|non compete|non solicitation|confidentiality|non disclosure|contractual obligation|restrictive covenant|restrict your ability/.test(label)) {
    return booleanAction(question, false);
  }
  const customAnswers = {
    "Previously employed by company": "No",
    "Reliable commute": "Yes",
    "preferred name": "Asha",
    "pronouns": "She/her",
    "earliest start date": "Immediately",
    "desired salary": "Open to market-aligned compensation",
    "Compensation expectations": "Open to market-aligned compensation",
    "open to relocate": "No",
    "Security clearance": "No",
    "Available to work weekends": "No",
    "Relevant project link": "https://applytex-qa.example.test/ml-platform",
    "Why this role": "The role matches my applied AI and product engineering work.",
    "Production AI system summary": "Built production LLM evaluation and monitoring services.",
    "LLM evaluation years": "Yes",
    "LLM evaluation experience": "Built LLM evaluation workflows for retrieval quality, model monitoring, and release gates.",
    "RAG systems experience": "Built RAG services with retrieval evaluation, prompt testing, and production monitoring.",
    "MLOps experience": "Managed model deployment, observability, and CI checks for machine learning services.",
    "data pipelines experience": "Built batch and API data pipelines with validation, monitoring, and SQL transformations.",
    "model monitoring experience": "Implemented drift checks, quality dashboards, and alerting for deployed models.",
    "FastAPI services experience": "Built FastAPI services for model inference, evaluation workflows, and internal tools.",
    "vector databases experience": "Used vector databases for retrieval workflows, embedding search, and RAG evaluation.",
    "how did you hear about us": "Company careers site",
    "cover letter": "I am interested in this role because it aligns with my applied AI and production ML experience.",
  };
  for (const [prompt, answer] of Object.entries(customAnswers)) {
    const aliases = customAnswerAliases(prompt);
    if (aliases.some((alias) => customPromptMatchesLabel(alias, label))) {
      return typedAction(question, answer, "custom_answer");
    }
  }
  if (isPreviousEmployerQuestion(label)) {
    return fillAction(question, "skip", null, question.required ? "user_input" : "none");
  }
  if (/certification|language|website/.test(label)) {
    return fillAction(question, "skip", null, "none");
  }
  if (question.profile_record_kind === "education") {
    const records = [
      { school: ["University of Houston"], degree: ["MS", "Master of Science", "Master's Degree"], major: ["Computer and Information Science", "Data Science", "Data Processing", "Computer Engineering"], gpa: "4/4", start: "2025-08", end: "2027-05" },
      { school: ["Amrita School of Engineering", "Amrita Vishwa Vidyapeetham"], degree: ["BS", "Bachelor of Science", "Bachelor's Degree"], major: ["Computer Science", "Computer and Information Science", "Computer Engineering"], gpa: "8.43/10", start: "2019-06", end: "2023-05" },
    ];
    const record = records[question.profile_record_index ?? 0] || records[0];
    if (label.includes("degree")) return fillAction(question, "select", record.degree, "profile");
    if (label.includes("major") || label.includes("field of study")) return fillAction(question, "select", record.major, "profile");
    if (question.date_boundary) {
      const date = question.date_boundary === "start" ? record.start : record.end;
      return fillAction(question, "fill", question.date_component === "month" ? date.slice(5, 7) : date.slice(0, 4), "profile");
    }
    const educationDate = label.includes("education from") ? record.start : label.includes("education to") ? record.end : "";
    if (educationDate) return fillAction(question, "fill", educationDate.slice(0, 4), "profile");
    const fields = [
      [["institution", "school", "university", "college"], record.school],
      [["gpa", "overall result"], record.gpa],
    ];
    const match = fields.find(([aliases]) => aliases.some((alias) => label.includes(alias)));
    if (match) return fillAction(question, question.control_kind === "custom_select" ? "select" : "fill", match[1], "profile");
  }
  if (question.profile_record_kind === "work_experience") {
    const records = [
      { title: "AI/ML Engineer", company: "Accenture", location: "Hyderabad, India", start: "2023-11", end: "2025-08", summary: "Built production RAG systems." },
      { title: "Project Intern – Explainable AI", company: "Samsung PRISM", location: "Bengaluru, India", start: "2021-09", end: "2022-04", summary: "Developed explainable AI systems." },
    ];
    const record = records[question.profile_record_index ?? 0] || records[0];
    if (question.date_boundary) {
      const date = question.date_boundary === "start" ? record.start : record.end;
      return fillAction(question, "fill", question.date_component === "month" ? date.slice(5, 7) : date.slice(0, 4), "profile");
    }
    const workDate = label.includes("experience from") ? record.start : label.includes("experience to") ? record.end : "";
    if (workDate) return fillAction(question, "fill", `${workDate.slice(5, 7)}/${workDate.slice(0, 4)}`, "profile");
    const fields = [
      [["experience title", "job title", "position title"], record.title],
      [["company", "employer"], record.company],
      [["office location", "work location", "job location", "experience location"], record.location],
      [["experience description", "work description", "role description"], record.summary],
    ];
    const match = fields.find(([aliases]) => aliases.some((alias) => label.includes(alias)));
    if (match) return typedAction(question, match[1], "profile");
  }
  if (/^(?:type to add |search |add |select |choose )?(?:professional |relevant )?skills$/.test(label)) {
    return fillAction(question, "select_many", ["Claude", "Gemini", "Reinforcement Learning", "Git/GitHub", "Azure", "Python", "SQL", "Java"], "profile");
  }
  const directFields = [
    [["first name", "given name"], "Asha"],
    [["last name", "family name", "surname"], "Patel"],
    [["full name", "legal name", "candidate name", "applicant name"], "Asha Patel"],
    [["email", "email address"], "qa@example.test"],
    [["phone", "mobile"], "+1 202-555-0142"],
    [["current location"], "Austin, TX"],
    [["city"], "Austin"],
    [["state", "province"], "Texas"],
    [["zip", "postal code"], "78701"],
    [["country"], "United States"],
    [["linkedin"], "https://www.linkedin.com/in/applytex-qa"],
    [["github"], "https://github.com/applytex-qa"],
    [["portfolio", "website"], "https://applytex-qa.example.test"],
    [["school", "university", "college", "institution"], "University of Texas at Austin"],
    [["degree"], "Master of Science"],
    [["major", "field of study"], "Computer Science"],
    [["graduation month"], "May"],
    [["graduation year"], "2026"],
    [["gpa"], "3.8"],
    [["job title", "current title", "position title", "experience title"], "Machine Learning Engineer"],
    [["company", "employer"], "ApplyTeX Labs"],
    [["job type", "employment type"], "Full-time"],
    [["work location", "job location", "office location"], "Austin, TX"],
  ];
  for (const [aliases, value] of directFields) {
    if (aliases.some((alias) => label.includes(alias))) {
      return typedAction(question, value, "profile");
    }
  }
  if (label.includes("relocat")) {
    return booleanAction(question, false);
  }
  return fillAction(question, "skip", null, question.required ? "user_input" : "none");
}

function typedAction(question, value, source) {
  const action = question.input_type === "select" || question.input_type === "radio" ? "select" : "fill";
  return fillAction(question, action, matchOption(String(value), question.options || []), source);
}

function booleanAction(question, value) {
  if (question.input_type === "checkbox") {
    return fillAction(question, "check", value, "profile");
  }
  const action = question.input_type === "radio" || question.input_type === "select" ? "select" : "fill";
  return fillAction(question, action, matchOption(value ? "Yes" : "No", question.options || []), "profile");
}

function fillAction(question, action, value, source) {
  return {
    field_id: question.field_id,
    action,
    value,
    answer_source: source,
    requires_review: true,
  };
}

function fixtureHtml(provider, index) {
  const company = companyFor(provider);
  const title = `${roleFor(index)} ${index}`;
  const location = locationFor(index);
  const description = [
    `${company} is hiring a ${title} in ${location}.`,
    "The role focuses on production machine learning systems, evaluation pipelines, and reliable data workflows.",
    "Required: Python, SQL, model evaluation, APIs, and stakeholder communication.",
  ].join("\n\n");
  const body = `
    <header>
      <img class="main-header-logo" alt="${escapeHtml(company)}">
      <div class="company-name job-details-jobs-unified-top-card__company-name jobs-unified-top-card__company-name" data-ui="company-name" data-testid="company-name" data-cy="companyName" data-test="employer-name">${escapeHtml(company)}</div>
      ${provider === "workday" ? `
        <button type="button" aria-haspopup="listbox">English</button>
        <button type="button" aria-haspopup="listbox">Settings</button>
        <button type="button" aria-haspopup="listbox">qa.candidate@example.test</button>
      ` : ""}
    </header>
    <h1
      class="job-title app-title"
      data-automation-id="jobPostingHeader"
      data-testid="job-title"
      data-test="job-title"
      data-cy="jobTitle"
      data-ui="job-title">${escapeHtml(title)}</h1>
    <div
      class="location job-location"
      data-automation-id="locations"
      data-testid="job-location"
      data-test="location"
      data-cy="location"
      data-ui="job-location">${escapeHtml(location)}</div>
    <main id="content" class="job__description posting-description iCIMS_JobContent" data-testid="job-description" data-ui="job-description" data-cy="jobDescription">
      <section id="job-details" class="jobs-description__content" data-automation-id="jobPostingDescription" data-test="jobDescription">
        ${escapeHtml(description)}
      </section>
      ${applicationForm(provider, index, company, location)}
    </main>
  `;
  const tabbedBody = `
    <button type="button" role="tab" aria-selected="true" data-tab-target="overview">Overview</button>
    <button type="button" role="tab" aria-selected="false" data-tab-target="application">Application</button>
    <main data-tab-panel="overview">
      <h1 data-testid="job-title" class="job-title">${escapeHtml(title)}</h1>
      <div class="company">CC</div>
      <div class="location" data-testid="location">${escapeHtml(location)}</div>
      <section data-testid="job-description">${escapeHtml(description)}</section>
    </main>
    <section data-tab-panel="application" hidden>
      ${applicationForm(provider, index, company, location)}
    </section>
    <script>
      document.querySelectorAll("[data-tab-target]").forEach((button) => {
        button.addEventListener("click", () => {
          const target = button.getAttribute("data-tab-target");
          document.querySelectorAll("[data-tab-target]").forEach((item) => {
            item.setAttribute("aria-selected", String(item === button));
          });
          document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
            panel.hidden = panel.getAttribute("data-tab-panel") !== target;
          });
        });
      });
    </script>
  `;
  return `
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>${escapeHtml(title)} at ${escapeHtml(company)}</title>
        <meta property="og:site_name" content="${provider === "ashby" ? "CC" : escapeAttr(company)}">
        <style>
          body { margin: 0; padding: 32px; font-family: Arial, sans-serif; color: #17201b; }
          main, section { max-width: 780px; }
          form { display: grid; gap: 12px; max-width: 720px; margin-top: 28px; padding-bottom: 80px; }
          label, fieldset, .field { display: grid; gap: 6px; padding: 10px; border: 1px solid #dde3de; border-radius: 6px; }
          fieldset label { border: 0; padding: 0; display: inline-flex; align-items: center; gap: 6px; }
          input, select, textarea { min-height: 34px; padding: 6px 8px; font: inherit; }
          textarea { min-height: 74px; }
        </style>
      </head>
      <body>${provider === "ashby" ? tabbedBody : body}</body>
    </html>
  `;
}

function applicationForm(provider, index, company, location) {
  const extras = extraQuestions(provider, index, company, location);
  const authorizationQuestions = provider === "ashby"
    ? `
      ${ashbyYesNoQuestion("Are you legally authorized to work in the United States?", "authorized_to_work", true)}
      ${ashbyYesNoQuestion("Will you now or in the future require sponsorship for employment visa status?", "requires_sponsorship", true)}
    `
    : `
      <fieldset>
        <legend>Are you legally authorized to work in the United States?*</legend>
        <label><input type="radio" name="authorized_to_work" value="Yes" required> Yes</label>
        <label><input type="radio" name="authorized_to_work" value="No" required> No</label>
      </fieldset>
      <fieldset>
        <legend>Will you now or in the future require sponsorship for employment visa status?*</legend>
        <label><input type="radio" name="requires_sponsorship" value="Yes" required> Yes</label>
        <label><input type="radio" name="requires_sponsorship" value="No" required> No</label>
      </fieldset>
    `;
  return `
    <form id="application-form" class="${provider === "ashby" ? "ashby-application-form-container" : ""}" autocomplete="on">
      ${inputField("First name", "first_name", "text", true, "given-name")}
      ${inputField("Last name", "last_name", "text", true, "family-name")}
      ${inputField("Email address", "email", "email", true, "email")}
      ${phoneField()}
      ${inputField("City", "city", "text", true, "address-level2")}
      ${selectField("State", "state", true, ["Texas", "California", "New York"])}
      ${inputField("ZIP/postal code", "postal_code", "text", true)}
      ${selectField("Country", "country", true, ["United States", "Canada"])}
      ${inputField("LinkedIn URL", "linkedin_url", "text", true)}
      ${inputField("GitHub URL", "github_url", "text", false)}
      ${fileField("Resume/CV", "resume", true)}
      ${authorizationQuestions}
      ${provider === "ashby" ? ashbyCheckboxGroup() : ""}
      ${provider === "workday" ? workdayProfileSections() : ""}
      ${extras.join("\n")}
    </form>
  `;
}

function workdayProfileSections() {
  return `
    <div data-automation-id="applyFlowMyExpPage">
      <h2>My Experience</h2>
      <section role="group" aria-label="Work Experience">
        <h4>Work Experience</h4>
        <div id="workday-work-records"></div>
        <button type="button" data-automation-id="add-button" onclick="addWorkdayWorkRecord()">Add</button>
      </section>
      <section role="group" aria-label="Education">
        <h4>Education</h4>
        <div id="workday-education-records"></div>
        <button type="button" data-automation-id="add-button" onclick="addWorkdayEducationRecord()">Add</button>
      </section>
      <section role="group" aria-label="Skills" id="workday-skills-control">
        <h4>Skills</h4>
        <label>Type to Add Skills<span id="skills-fixture" data-automation-id="multiSelectContainer" data-uxi-widget-type="multiselect"><span class="selected-items" role="listbox" data-automation-id="selectedItemList" data-uxi-multiselect-id="skills-fixture"></span><input id="skills--skills" placeholder="Search" autocomplete="off" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="skills-fixture" onkeydown="commitWorkdaySkill(event, this)"><span data-automation-id="promptSearchButton" data-uxi-multiselect-id="skills-fixture" onclick="toggleWorkdayPrompt('skills-fixture', workdaySkillOptions)"></span><span class="selected-count" data-automation-id="promptAriaInstruction">0 items selected</span></span></label>
      </section>
      <section role="group" aria-label="Certifications" data-optional-workday="certifications">
        <h4>Certifications</h4>
        <label>Certification<input id="workday-certification"></label>
      </section>
      <section role="group" aria-label="Languages" data-optional-workday="languages">
        <h4>Languages</h4>
        <label>Language<input id="workday-language"></label>
      </section>
      <section role="group" aria-label="Websites" data-optional-workday="websites">
        <h4>Websites</h4>
        <label>Website URL<input id="workday-website"></label>
      </section>
      <button type="button" id="save-and-continue" onclick="document.body.dataset.saveClicks = String(Number(document.body.dataset.saveClicks || 0) + 1)">Save and Continue</button>
    </div>
    <script>
      function workdayField(label, id, attrs) {
        return '<label>' + label + '<input id="' + id + '" ' + (attrs || '') + '></label>';
      }
      function workdayDateGroup(recordType, index, boundary, required) {
        const dateName = boundary === 'From' ? 'startDate' : 'endDate';
        const prefix = recordType + '-' + index + '--' + dateName;
        return '<div role="group" aria-label="' + boundary + '">' + boundary + (required ? '*' : '') +
          '<input role="spinbutton" aria-label="Month" data-automation-id="dateSectionMonth-input" id="' + prefix + '-dateSectionMonth-input" onkeydown="corruptDateOnKeyboard(event, this)" oninput="requireYearBeforeMonth(this)">' +
          '<input role="spinbutton" aria-label="Year" data-automation-id="dateSectionYear-input" id="' + prefix + '-dateSectionYear-input"></div>';
      }
      function workdayEducationYear(index, boundary) {
        const dateName = boundary === 'From' ? 'firstYearAttended' : 'lastYearAttended';
        const label = boundary === 'From' ? 'From' : 'To (Actual or Expected)';
        return '<div role="group" aria-label="' + label + '">' + label +
          '<input role="spinbutton" aria-label="Year" data-automation-id="dateSectionYear-input" id="education-' + index + '--' + dateName + '-dateSectionYear-input"></div>';
      }
      function workdayStudyField(index) {
        const promptId = 'study-' + index;
        return '<label>Field of Study*<span id="' + promptId + '" data-automation-id="multiSelectContainer" data-uxi-widget-type="multiselect">' +
          '<span class="selected-items" role="listbox" data-automation-id="selectedItemList" data-uxi-multiselect-id="' + promptId + '"></span><input id="education-' + index + '--fieldOfStudy" placeholder="Search" autocomplete="off" aria-required="true" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="' + promptId + '">' +
          '<span data-automation-id="promptSearchButton" data-uxi-multiselect-id="' + promptId + '" onclick="toggleWorkdayPrompt(&quot;' + promptId + '&quot;, studyOptions)"></span>' +
          '<span class="selected-count" data-automation-id="promptAriaInstruction">0 items selected</span></span></label>';
      }
      function workdaySchoolField(index) {
        const promptId = 'school-' + index;
        return '<label>School or University*<span id="' + promptId + '" data-automation-id="multiSelectContainer" data-uxi-widget-type="multiselect">' +
          '<span class="selected-items" role="listbox" data-automation-id="selectedItemList" data-uxi-multiselect-id="' + promptId + '"></span><input id="education-' + index + '-school" data-field="school" placeholder="Search" autocomplete="off" aria-required="true" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="' + promptId + '">' +
          '<span data-automation-id="promptSearchButton" data-uxi-multiselect-id="' + promptId + '" onclick="toggleWorkdayPrompt(&quot;' + promptId + '&quot;, schoolOptions)"></span>' +
          '<span class="selected-count" data-automation-id="promptAriaInstruction">0 items selected</span></span></label>';
      }
      function addWorkdayWorkRecord() {
        const host = document.getElementById('workday-work-records');
        const index = host.querySelectorAll('[data-record-kind="work_experience"]').length + 1;
        const record = document.createElement('div');
        record.setAttribute('role', 'group');
        record.dataset.recordKind = 'work_experience';
        record.innerHTML = '<h5>Work Experience ' + index + '</h5>' +
          workdayField('Job Title*', 'work-' + index + '-jobTitle', 'data-field="job-title" required') +
          workdayField('Company*', 'work-' + index + '-company', 'data-field="company" required') +
          workdayField('Location', 'work-' + index + '-location') +
          workdayDateGroup('workExperience', index, 'From', true) +
          workdayDateGroup('workExperience', index, 'To', true) +
          '<label>Role Description<textarea id="work-' + index + '-roleDescription"></textarea></label>';
        host.appendChild(record);
        host.closest('section').querySelector('button[data-automation-id="add-button"]').textContent = 'Add Another';
      }
      function addWorkdayEducationRecord() {
        const host = document.getElementById('workday-education-records');
        const index = host.querySelectorAll('[data-record-kind="education"]').length + 1;
        const record = document.createElement('div');
        record.setAttribute('role', 'group');
        record.dataset.recordKind = 'education';
        record.innerHTML = '<h5>Education ' + index + '</h5>' +
          workdaySchoolField(index) +
          '<label>Degree*<button type="button" id="education-' + index + '-degree" aria-label="Degree Select One Required" aria-haspopup="listbox">Select One</button></label>' +
          workdayStudyField(index) +
          workdayField('Overall Result (GPA)', 'education-' + index + '-gpa') +
          workdayEducationYear(index, 'From') +
          workdayEducationYear(index, 'To');
        host.appendChild(record);
        host.closest('section').querySelector('button[data-automation-id="add-button"]').textContent = 'Add Another';
        const degree = record.querySelector('[id$="-degree"]');
        degree.addEventListener('click', () => openWorkdayOptions(degree, ['MS','BS']));
      }
      function openWorkdayOptions(control, values) {
        document.querySelectorAll('.workday-options').forEach((item) => item.remove());
        const list = document.createElement('div');
        list.className = 'workday-options';
        list.setAttribute('role', 'listbox');
        values.forEach((value) => {
          const option = document.createElement('button');
          option.type = 'button';
          option.setAttribute('role', 'option');
          option.dataset.automationId = 'promptOption';
          option.textContent = value;
          option.addEventListener('click', () => {
            control.textContent = value;
            control.dispatchEvent(new Event('change', { bubbles: true }));
            list.remove();
          });
          list.appendChild(option);
        });
        control.parentElement.appendChild(list);
      }
      function corruptDateOnKeyboard(event, input) {
        if (!/^\d$/.test(event.key)) return;
        input.dataset.keyboardBuffer = (input.dataset.keyboardBuffer || '') + event.key;
        if (input.dataset.keyboardBuffer.length < 2) return;
        const year = input.nextElementSibling;
        if (year?.dataset.automationId === 'dateSectionYear-input') {
          year.value = '20' + input.dataset.keyboardBuffer.slice(-2);
        }
        input.dataset.keyboardBuffer = '';
      }
      function requireYearBeforeMonth(input) {
        if (!input.value) return;
        const year = input.nextElementSibling;
        if (year?.dataset.automationId === 'dateSectionYear-input' && !year.value) {
          input.value = '';
        }
      }
      const schoolOptions = [
        'Bauer College of Business, University of Houston',
        'UNIVERSITY OF HOUSTON',
        'Amrita School of Engineering, Amrita Vishwa Vidyapeetham',
        'Amrita School of Engineering',
        'Amrita Vishwa Vidyapeetham',
      ];
      const studyOptions = Array.from({ length: 320 }, (_, index) => 'Study Option ' + String(index + 1).padStart(3, '0'));
      studyOptions[84] = 'Computer and Information Science';
      studyOptions[190] = 'Computer Science';
      studyOptions[260] = 'Computer Engineering';
      const workdaySkillOptions = [
        'Reinforcement Learning',
        'Git',
        'GitHub',
        'Microsoft Azure',
        'Python (programming language)',
        'Structured Query Language(SQL)',
        'Java (programming language)',
        'JavaScript',
      ];
      function normalizeSkillText(value) {
        return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
      }
      function compactSkillText(value) {
        return normalizeSkillText(value).replace(/\s+/g, '');
      }
      function baseSkillText(value) {
        return normalizeSkillText(String(value || '').replace(/\([^)]*\)/g, ' '));
      }
      function skillQueryMatchesOption(option, query) {
        const queryKeys = new Set([
          normalizeSkillText(query),
          compactSkillText(query),
          baseSkillText(query),
          compactSkillText(baseSkillText(query)),
        ].filter(Boolean));
        const optionKeys = new Set([
          normalizeSkillText(option),
          compactSkillText(option),
          baseSkillText(option),
          compactSkillText(baseSkillText(option)),
        ].filter(Boolean));
        const aliases = {
          azure: ['microsoftazure'],
          java: ['javaprogramminglanguage'],
          python: ['pythonprogramminglanguage'],
          sql: ['structuredquerylanguage', 'structuredquerylanguagesql'],
          rl: ['reinforcementlearning'],
          reinforcementlearning: ['reinforcementlearning'],
        };
        for (const key of Array.from(queryKeys)) {
          for (const alias of aliases[key] || []) queryKeys.add(alias);
        }
        return Array.from(queryKeys).some((key) => optionKeys.has(key));
      }
      function filteredWorkdaySkills(query) {
        const text = String(query || '').trim();
        if (!text) return workdaySkillOptions;
        return workdaySkillOptions.filter((option) => skillQueryMatchesOption(option, text));
      }
      function selectWorkdayToken(input, value) {
        const shell = input.closest('[data-automation-id="multiSelectContainer"]');
        let items = shell.querySelector('.selected-items');
        if (!items) {
          items = document.createElement('span');
          items.className = 'selected-items';
          shell.prepend(items);
        }
        if (!Array.from(items.children).some((item) => item.dataset.value === value)) {
          const chip = document.createElement('span');
          chip.dataset.automationId = 'selectedItem';
          chip.dataset.value = value;
          chip.textContent = value;
          items.appendChild(chip);
        }
        shell.querySelector('.selected-count').textContent = items.children.length + ' items selected';
        input.value = '';
        document.querySelectorAll('[data-automation-id="activeListContainer"]').forEach((item) => item.remove());
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
      document.addEventListener('input', (event) => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || !input.dataset.uxiMultiselectId) return;
        const shell = input.closest('[data-automation-id="multiSelectContainer"]');
        const button = shell?.querySelector('[data-uxi-multiselect-id="' + input.dataset.uxiMultiselectId + '"][data-automation-id="promptSearchButton"], [data-uxi-multiselect-id="' + input.dataset.uxiMultiselectId + '"][data-automation-id="clearIcon"]');
        if (button) button.dataset.automationId = input.value ? 'clearIcon' : 'promptSearchButton';
        if (input.dataset.uxiMultiselectId === 'skills-fixture') {
          window.clearTimeout(input._workdaySkillSearchTimer);
          input._workdaySkillSearchTimer = window.setTimeout(() => {
            const list = Array.from(document.querySelectorAll('[data-automation-id="activeListContainer"]'))
              .find((item) => item.dataset.promptId === 'skills-fixture');
            if (list) renderPromptWindow(list, input, 'skills-fixture', filteredWorkdaySkills(input.value));
          }, 120);
        } else if (input.dataset.uxiMultiselectId.startsWith('study-') || input.dataset.uxiMultiselectId.startsWith('school-')) {
          window.clearTimeout(input._workdayStudySearchTimer);
          input._workdayStudySearchTimer = window.setTimeout(() => {
            const promptId = input.dataset.uxiMultiselectId;
            const list = Array.from(document.querySelectorAll('[data-automation-id="activeListContainer"]'))
              .find((item) => item.dataset.promptId === promptId);
            const query = normalizeSkillText(input.value);
            const catalog = promptId.startsWith('school-') ? schoolOptions : studyOptions;
            const values = query
              ? catalog.filter((option) => normalizeSkillText(option).includes(query))
              : catalog;
            if (list) renderPromptWindow(list, input, promptId, values);
          }, 120);
        }
      });
      function promptOption(input, promptId, value, index, total) {
        const option = document.createElement('div');
        option.setAttribute('role', 'option');
        option.dataset.automationId = 'menuItem';
        option.setAttribute('aria-posinset', String(index + 1));
        option.setAttribute('aria-setsize', String(total));
        option.style.cssText = 'height:32px;left:0;position:absolute;top:' + (index * 32) + 'px;width:100%';
        const leaf = document.createElement('div');
        leaf.dataset.automationId = 'promptLeafNode';
        leaf.dataset.uxiMultiselectId = promptId;
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.dataset.automationId = 'radioBtn';
        const label = document.createElement('div');
        label.dataset.automationId = 'promptOption';
        label.dataset.automationLabel = value;
        label.textContent = value;
        leaf.append(radio, label);
        option.append(leaf);
        if (value !== 'No Items.') option.addEventListener('click', () => selectWorkdayToken(input, value));
        return option;
      }
      function renderPromptWindow(list, input, promptId, values) {
        const inner = list.querySelector('[role="presentation"]');
        inner.replaceChildren();
        const effective = values.length ? values : ['No Items.'];
        const start = Math.max(Math.floor(list.scrollTop / 32) - 2, 0);
        const end = Math.min(start + 22, effective.length);
        for (let index = start; index < end; index += 1) {
          inner.appendChild(promptOption(input, promptId, effective[index], index, effective.length));
        }
      }
      function toggleWorkdayPrompt(promptId, values) {
        const existing = Array.from(document.querySelectorAll('[data-automation-id="activeListContainer"]'))
          .find((item) => item.dataset.promptId === promptId);
        if (existing) {
          existing.remove();
          return;
        }
        document.querySelectorAll('[data-automation-id="activeListContainer"]').forEach((item) => item.remove());
        const input = document.querySelector('[data-uxi-multiselect-id="' + promptId + '"][data-uxi-widget-type="selectinput"]');
        const list = document.createElement('div');
        list.dataset.automationId = 'activeListContainer';
        list.dataset.promptId = promptId;
        list.setAttribute('role', 'listbox');
        list.style.cssText = 'height:302px;overflow:auto;position:absolute;width:340px;background:white;z-index:20';
        const inner = document.createElement('div');
        inner.setAttribute('role', 'presentation');
        inner.style.cssText = 'height:' + (Math.max(values.length, 1) * 32) + 'px;position:relative';
        list.appendChild(inner);
        list.addEventListener('scroll', () => renderPromptWindow(list, input, promptId, values));
        input.closest('[data-automation-id="multiSelectContainer"]').appendChild(list);
        renderPromptWindow(list, input, promptId, values);
      }
      function commitWorkdaySkill(event, input) {
        if (event.key === 'Enter' && input.value.trim()) {
          event.preventDefault();
          window.clearTimeout(input._workdaySkillSearchTimer);
          input._workdaySkillSearchTimer = window.setTimeout(() => {
            const list = Array.from(document.querySelectorAll('[data-automation-id="activeListContainer"]'))
              .find((item) => item.dataset.promptId === 'skills-fixture');
            if (!list) return;
            renderPromptWindow(list, input, 'skills-fixture', filteredWorkdaySkills(input.value));
          }, 450);
        }
      }
    </script>
  `;
}

function extraQuestions(provider, index, company, location) {
  const skill = skillFor(index);
  const items = [
    textQuestion("Have you ever worked for this company or any affiliate before?", "previous_employer", true),
    textQuestion("What is your earliest available start date?", "available_start_date", index % 3 === 0),
    textQuestion("What are your compensation expectations?", "compensation_expectations", index % 4 === 0),
    textareaQuestion(`Please summarize your experience with ${skill}.`, `skill_summary_${index}`, index % 5 === 0),
    radioQuestion("Can you reliably commute to this job's location?", "reliable_commute", ["Yes", "No"], index % 2 === 0),
    radioQuestion("Do you consent to receive SMS updates about your application?", "sms_consent", ["Yes", "No"], false),
  ];
  if (index % 7 === 0) {
    items.push(radioQuestion(`Do you have at least 2 years of ${skill} experience?`, `skill_years_${index}`, ["Yes", "No"], true));
  }
  if (provider === "ashby") {
    items.push(textareaQuestion("What makes you a good fit for Cohere?", "ashby_good_fit", true));
  }
  const providerSpecific = {
    linkedin: textQuestion("Current company website", `linkedin_company_site_${index}`, index % 6 === 0),
    greenhouse: radioQuestion("Are you bound by any agreement that restricts your ability to work here?", `greenhouse_restriction_${index}`, ["Yes", "No"], index % 4 === 0),
    lever: textareaQuestion("Why are you interested in this role?", `lever_interest_${index}`, index % 6 === 0),
    ashby: textQuestion("Link to the project most relevant to this role", `ashby_project_${index}`, index % 5 === 0),
    workday: radioQuestion(`Have you previously been employed by ${company}?`, `workday_previous_${index}`, ["Yes", "No"], index % 3 === 0),
    icims: radioQuestion("Are you subject to a restrictive covenant or non-compete?", `icims_covenant_${index}`, ["Yes", "No"], index % 4 === 0),
    smartrecruiters: radioQuestion("I consent to joining the talent community for future opportunities.", `smartrecruiters_talent_${index}`, ["Yes", "No"], false),
    workable: textareaQuestion("Briefly describe a production AI system you shipped.", `workable_ai_system_${index}`, index % 5 === 0),
    indeed: textQuestion(`Can you commute to ${location}?`, `indeed_commute_${index}`, index % 3 === 0),
    ziprecruiter: radioQuestion("Are you available to work weekends if needed?", `ziprecruiter_weekends_${index}`, ["Yes", "No"], index % 8 === 0),
    glassdoor: textQuestion("What is your desired pay range?", `glassdoor_pay_${index}`, index % 3 === 0),
    wellfound: textareaQuestion("Tell us why you are interested in this startup.", `wellfound_startup_${index}`, index % 4 === 0),
    dice: radioQuestion("Do you hold an active security clearance?", `dice_clearance_${index}`, ["Yes", "No"], index % 4 === 0),
  };
  items.push(providerSpecific[provider]);
  return items;
}

function textQuestion(label, id, required) {
  return inputField(label, id, "text", required);
}

function textareaQuestion(label, id, required) {
  return `
    <div class="field">
      <label for="${escapeAttr(id)}">${escapeHtml(label)}${required ? "*" : ""}</label>
      <textarea id="${escapeAttr(id)}" name="${escapeAttr(id)}" ${required ? "required" : ""}></textarea>
    </div>
  `;
}

function radioQuestion(label, name, options, required) {
  return `
    <fieldset>
      <legend>${escapeHtml(label)}${required ? "*" : ""}</legend>
      ${options.map((option) => `<label><input type="radio" name="${escapeAttr(name)}" value="${escapeAttr(option)}" ${required ? "required" : ""}> ${escapeHtml(option)}</label>`).join("")}
    </fieldset>
  `;
}

function ashbyYesNoQuestion(label, name, required) {
  const selectOption = `
    const container = this.parentElement;
    container.querySelectorAll('button').forEach((button) => button.classList.remove('selected'));
    this.classList.add('selected');
    const input = container.querySelector('input');
    input.checked = this.textContent.trim() === 'Yes';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  `.replace(/\s+/g, " ").trim();
  return `
    <div class="ashby-application-form-field-entry" data-field-path="${escapeAttr(name)}">
      <label class="ashby-application-form-question-title ${required ? "_required_fixture" : ""}" for="${escapeAttr(name)}">${escapeHtml(label)}</label>
      <div class="_yesno_fixture">
        <button type="button" onclick="${escapeAttr(selectOption)}">Yes</button>
        <button type="button" onclick="${escapeAttr(selectOption)}">No</button>
        <input type="checkbox" name="${escapeAttr(name)}" style="display:none" tabindex="-1">
      </div>
    </div>
  `;
}

function ashbyCheckboxGroup() {
  const options = [
    ["Backend Development", true],
    ["Frontend Development", true],
    ["Full-stack Development", true],
    ["DevOps/Cloud Engineering", false],
    ["Security Engineering", false],
    ["Inference and Distributed Training", false],
  ];
  return `
    <fieldset class="_container_1258i_28 _fieldEntry_1e3gg_28" data-fixture-question="engineering-interests">
      <label class="_heading_f7cvd_52 _required_f7cvd_91 _label_1e3gg_42 ashby-application-form-question-title">What area of software engineering interests you the most?:</label>
      ${options.map(([option, checked], index) => `
        <label for="engineering-interest-${index}">
          <input id="engineering-interest-${index}" type="checkbox" name="${escapeAttr(option)}" value="on" ${checked ? "checked" : ""}>
          ${escapeHtml(option)}
        </label>
      `).join("")}
    </fieldset>
  `;
}

function inputField(label, id, type, required, autocomplete = "") {
  return `
    <div class="field">
      <label for="${escapeAttr(id)}">${escapeHtml(label)}${required ? "*" : ""}</label>
      <input id="${escapeAttr(id)}" name="${escapeAttr(id)}" type="${escapeAttr(type)}" ${autocomplete ? `autocomplete="${escapeAttr(autocomplete)}"` : ""} ${required ? "required" : ""}>
    </div>
  `;
}

function phoneField() {
  return `
    <div class="phone-group">
      <div class="field">
        <label id="phone-country-label">Country*</label>
        <button type="button" aria-labelledby="phone-country-label">US +1</button>
      </div>
      <div class="field">
        <label for="phone">Phone*</label>
        <input id="phone" name="phone" type="tel" autocomplete="tel-national" required>
      </div>
    </div>
  `;
}

function selectField(label, id, required, options) {
  return `
    <div class="field">
      <label for="${escapeAttr(id)}">${escapeHtml(label)}${required ? "*" : ""}</label>
      <select id="${escapeAttr(id)}" name="${escapeAttr(id)}" ${required ? "required" : ""}>
        <option value="">Select...</option>
        ${options.map((option) => `<option>${escapeHtml(option)}</option>`).join("")}
      </select>
    </div>
  `;
}

function fileField(label, id, required) {
  return `
    <div class="field">
      <label for="${escapeAttr(id)}">${escapeHtml(label)}${required ? "*" : ""}</label>
      <input id="${escapeAttr(id)}" name="${escapeAttr(id)}" type="file" accept="application/pdf,.pdf" ${required ? "required" : ""}>
    </div>
  `;
}

function summarize(records, failures, qaState) {
  const providerStats = Object.fromEntries(providers.map((provider) => [
    provider,
    {
      jobs_tested: 0,
      successful_jobs: 0,
      resume_uploads: 0,
      fields_scanned: 0,
      required_fields: 0,
      required_filled_after_autofill: 0,
      unresolved_required: 0,
      failures: 0,
    },
  ]));
  const uncovered = new Map();
  const frequentNotInCatalog = new Map();
  for (const record of records) {
    const stats = providerStats[record.provider];
    stats.jobs_tested += 1;
    stats.successful_jobs += record.success ? 1 : 0;
    stats.resume_uploads += record.resume_uploaded ? 1 : 0;
    stats.fields_scanned += record.scanned_questions.length;
    stats.required_fields += record.required_total;
    stats.required_filled_after_autofill += record.required_filled_after_autofill;
    stats.unresolved_required += record.unresolved_required.length;
    stats.failures += record.errors.length ? 1 : 0;

    const reviewByField = new Map(record.initial_review_items.map((item) => [item.field_id, item]));
    for (const question of record.scanned_questions) {
      const item = reviewByField.get(question.field_id);
      const notInCatalog = !questionCoveredByCatalog(question.label);
      if (!notInCatalog) continue;
      bumpQuestion(frequentNotInCatalog, question, record, item);
      if (question.required && item?.status !== "ready") {
        bumpQuestion(uncovered, question, record, item);
      }
    }
  }
  return {
    generated_at: new Date().toISOString(),
    scope: "Local intercepted browser QA. No real job applications, accounts, employer sites, or personal data transmission.",
    jobs_per_provider: jobsPerProvider,
    providers,
    total_jobs_tested: records.length,
    provider_stats: providerStats,
    frequent_questions_not_in_catalog: sortedQuestionCounts(frequentNotInCatalog),
    unresolved_required_not_in_catalog: sortedQuestionCounts(uncovered),
    failures: failures.map((failure) => ({
      provider: failure.provider,
      index: failure.index,
      url: failure.url,
      errors: failure.errors,
    })),
    browser_messages: {
      console: qaState.consoleMessages.slice(0, 50),
      page_errors: qaState.pageErrors.slice(0, 50),
      total_console_or_page_errors: qaState.consoleMessages.length + qaState.pageErrors.length,
    },
    records,
  };
}

function bumpQuestion(bucket, question, record, reviewItem) {
  const key = normalizeQuestion(question.label);
  if (!bucket.has(key)) {
    bucket.set(key, {
      label: stripRequiredMarker(question.label),
      count: 0,
      required_count: 0,
      providers: new Set(),
      answer_sources: new Set(),
      examples: [],
    });
  }
  const item = bucket.get(key);
  item.count += 1;
  item.required_count += question.required ? 1 : 0;
  item.providers.add(record.provider);
  item.answer_sources.add(reviewItem?.answer_source || "unknown");
  if (item.examples.length < 5) {
    item.examples.push({ provider: record.provider, url: record.url });
  }
}

function sortedQuestionCounts(bucket) {
  return Array.from(bucket.values())
    .map((item) => ({
      ...item,
      providers: Array.from(item.providers).sort(),
      answer_sources: Array.from(item.answer_sources).sort(),
    }))
    .sort((left, right) => right.required_count - left.required_count || right.count - left.count || left.label.localeCompare(right.label));
}

function writeReport(report, jsonPath) {
  fs.mkdirSync(path.dirname(jsonPath), { recursive: true });
  fs.writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`);
  const mdPath = jsonPath.replace(/\.json$/i, ".md");
  fs.writeFileSync(mdPath, markdownReport(report, jsonPath));
}

function markdownReport(report, jsonPath) {
  const rows = Object.entries(report.provider_stats)
    .map(([provider, stats]) => `| ${provider} | ${stats.jobs_tested} | ${stats.successful_jobs} | ${stats.resume_uploads} | ${stats.fields_scanned} | ${stats.required_filled_after_autofill}/${stats.required_fields} | ${stats.unresolved_required} | ${stats.failures} |`)
    .join("\n");
  const missingRows = report.unresolved_required_not_in_catalog
    .slice(0, 25)
    .map((item) => `| ${escapeMarkdown(item.label)} | ${item.required_count} | ${item.count} | ${item.providers.join(", ")} | ${item.answer_sources.join(", ")} |`)
    .join("\n") || "| None | 0 | 0 | - | - |";
  const frequentRows = report.frequent_questions_not_in_catalog
    .slice(0, 25)
    .map((item) => `| ${escapeMarkdown(item.label)} | ${item.required_count} | ${item.count} | ${item.providers.join(", ")} | ${item.answer_sources.join(", ")} |`)
    .join("\n") || "| None | 0 | 0 | - | - |";
  return `# Extension Platform QA

Generated: ${report.generated_at}

Scope: ${report.scope}

JSON artifact: \`${path.relative(ROOT, jsonPath)}\`

## Provider Summary

| Provider | Jobs | Successful | Resume uploads | Fields scanned | Required filled | Unresolved required | Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
${rows}

## Required Questions Missing From Catalog

These required questions were not in the profile/setup catalog and were not fillable by the current fill plan.

| Question | Required count | Seen count | Providers | Answer source |
| --- | ---: | ---: | --- | --- |
${missingRows}

## Frequent Questions Not In Catalog

These labels are not in the known question catalog. Some may still be handled by heuristics, but adding them explicitly can improve profile setup and review quality.

| Question | Required count | Seen count | Providers | Answer source |
| --- | ---: | ---: | --- | --- |
${frequentRows}

## Browser Errors

Total console/page errors: ${report.browser_messages.total_console_or_page_errors}
`;
}

function renderConsoleSummary(report, jsonPath) {
  const mdPath = jsonPath.replace(/\.json$/i, ".md");
  const failures = report.failures.length;
  const unresolved = report.unresolved_required_not_in_catalog
    .slice(0, 8)
    .map((item) => `- ${item.label}: required ${item.required_count}, seen ${item.count}, providers ${item.providers.join(", ")}`)
    .join("\n") || "- None";
  return [
    `Extension platform QA complete: ${report.total_jobs_tested} jobs across ${report.providers.length} providers.`,
    `Failures: ${failures}. Browser console/page errors: ${report.browser_messages.total_console_or_page_errors}.`,
    `JSON: ${path.relative(ROOT, jsonPath)}`,
    `Markdown: ${path.relative(ROOT, mdPath)}`,
    "Top unresolved required questions not in catalog:",
    unresolved,
  ].join("\n");
}

function loadProviderRegistry(source) {
  const sandbox = { globalThis: {} };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(source, sandbox, { filename: "providers.js" });
  if (!sandbox.ApplyTexProviders?.providers) {
    throw new Error("Could not load ApplyTexProviders from extension/providers.js.");
  }
  return sandbox.ApplyTexProviders;
}

function loadKnownQuestionCatalog() {
  const source = fs.readFileSync(path.join(ROOT, "src", "latex_resume", "form_resolution.py"), "utf8");
  const labels = [];
  const pattern = /"label":\s*"([^"]+)"/g;
  for (const match of source.matchAll(pattern)) {
    labels.push(match[1]);
  }
  return labels;
}

async function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_MODULE,
    "playwright",
    path.join(ROOT, "frontend", "node_modules", "playwright", "index.mjs"),
    "/Applications/Codex.app/Contents/Resources/cua_node/lib/node_modules/playwright/index.mjs",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (candidate.startsWith("/") || candidate.startsWith(".")) {
        return await import(pathToFileURL(path.resolve(candidate)).href);
      }
      return await import(candidate);
    } catch {
      // Try the next candidate.
    }
  }
  throw new Error("Playwright is required. Install it or set PLAYWRIGHT_MODULE to a Playwright module path.");
}

function explicitChromeExecutable() {
  const candidate = process.env.CHROME_EXECUTABLE || "";
  return candidate && fs.existsSync(candidate) ? candidate : "";
}

function providerUrl(provider, index) {
  const padded = String(index).padStart(3, "0");
  const urls = {
    linkedin: `https://www.linkedin.com/jobs/view/qa-${padded}`,
    greenhouse: `https://boards.greenhouse.io/applytexai/jobs/${padded}`,
    lever: `https://jobs.lever.co/applytexai/qa-${padded}`,
    ashby: `https://jobs.ashbyhq.com/applytexai/qa-${padded}`,
    workday: `https://applytexai.myworkdayjobs.com/en-US/careers/job/R-${padded}`,
    icims: `https://careers-applytexai.icims.com/jobs/${padded}/job`,
    smartrecruiters: `https://jobs.smartrecruiters.com/ApplyTeXAI/${padded}-machine-learning-engineer`,
    workable: `https://apply.workable.com/applytexai/j/${padded}/`,
    indeed: `https://www.indeed.com/viewjob?jk=qa${padded}`,
    ziprecruiter: `https://www.ziprecruiter.com/c/ApplyTeX-AI/Job/Machine-Learning-Engineer/-in-Austin,TX?jid=qa${padded}`,
    glassdoor: `https://www.glassdoor.com/job-listing/qa-${padded}.htm`,
    wellfound: `https://wellfound.com/jobs/${padded}-machine-learning-engineer`,
    dice: `https://www.dice.com/job-detail/qa-${padded}`,
  };
  return urls[provider];
}

function companyFor(provider) {
  const names = {
    linkedin: "LinkedIn Fixture AI",
    greenhouse: "Greenhouse Fixture AI",
    lever: "Lever Fixture AI",
    ashby: "Ashby Fixture AI",
    workday: "Workday Fixture AI",
    icims: "iCIMS Fixture AI",
    smartrecruiters: "SmartRecruiters Fixture AI",
    workable: "Workable Fixture AI",
    indeed: "Indeed Fixture AI",
    ziprecruiter: "ZipRecruiter Fixture AI",
    glassdoor: "Glassdoor Fixture AI",
    wellfound: "Wellfound Fixture AI",
    dice: "Dice Fixture AI",
  };
  return names[provider] || "ApplyTeX Fixture AI";
}

function roleFor(index) {
  const roles = ["AI Engineer", "Machine Learning Engineer", "Data Scientist", "MLOps Engineer", "NLP Engineer"];
  return roles[index % roles.length];
}

function locationFor(index) {
  const locations = ["Austin, TX", "Houston, TX", "Dallas, TX", "Remote - US", "New York, NY"];
  return locations[index % locations.length];
}

function skillFor(index) {
  const skills = ["LLM evaluation", "RAG systems", "MLOps", "data pipelines", "model monitoring", "FastAPI services", "vector databases"];
  return skills[index % skills.length];
}

function stableJobId(provider, url) {
  return `${provider}-${Buffer.from(url).toString("base64url").slice(0, 24)}`;
}

function boardTokenFromUrl(url) {
  const parsed = new URL(url);
  return parsed.hostname.split(".")[0].replace(/[^a-z0-9_-]/gi, "") || "browser";
}

function skippedCountFromActions(actions) {
  return actions.filter((action) => action.action === "skip" || action.value === null).length;
}

function filledCountFromPanelText(text) {
  const value = String(text || "");
  const match = value.match(/Filled\s+(\d+)\s+reviewed fields/i) || value.match(/(\d+)\s+Filled/i);
  return match ? Number.parseInt(match[1], 10) : 0;
}

function previewFillValue(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const text = String(value);
  return text.length > 48 ? `${text.slice(0, 45)}...` : text;
}

function matchOption(value, options) {
  if (!options.length) return value;
  const wanted = normalizeText(value);
  for (const option of options) {
    if (normalizeText(option) === wanted) return option;
  }
  for (const option of options) {
    const normalized = normalizeText(option);
    if (wanted.includes(normalized) || normalized.includes(wanted)) return option;
  }
  return value;
}

function stripRequiredMarker(value) {
  return String(value || "").replace(/\*+$/g, "").trim();
}

function questionCoveredByCatalog(value) {
  const label = normalizeQuestion(value);
  if (knownCatalogKeys.has(label)) return true;
  if (/\b(first name|given name|last name|family name|surname|full name|legal name)\b/.test(label)) return true;
  if (/\b(email|email address|phone|mobile)\b/.test(label)) return true;
  if (/\b(city|state|province|zip|postal code|country)\b/.test(label)) return true;
  if (/\b(linkedin|github|portfolio|website)\b/.test(label)) return true;
  if (/\b(resume|cv|cover letter)\b/.test(label)) return true;
  if (/\b(school|university|college|degree|major|field of study|graduation month|graduation year|gpa)\b/.test(label)) return true;
  if (label.includes("authorized") && (label.includes("work") || label.includes("united states") || label.includes("u s"))) return true;
  if (label.includes("sponsor") || label.includes("sponsorship")) return true;
  if (/bound by any agreement|non compete|non solicitation|confidentiality|non disclosure|contractual obligation|restrictive covenant|restrict your ability/.test(label)) return true;
  if (customAnswerAliases("Earliest start date").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Desired salary").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Reliable commute").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Previously employed by company").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Security clearance").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Available to work weekends").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Relevant project link").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Why this role").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (customAnswerAliases("Production AI system summary").some((alias) => customPromptMatchesLabel(alias, label))) return true;
  if (isSkillSpecificQaQuestion(label)) return true;
  return false;
}

function isPreviousEmployerQuestion(label) {
  return /previously employed|previous employer|former employee|ever worked|worked for this company|worked at this company|worked for us|worked at our company/.test(label);
}

function isSkillSpecificQaQuestion(label) {
  return /(?:do you have )?(?:at least )?\d+\+? years? of .+ experience/.test(label)
    || /(?:please )?(?:summarize|describe) your experience with .+/.test(label);
}

function customAnswerAliases(prompt) {
  const aliasMap = {
    "earliest start date": ["earliest available start date", "available start date", "when can you start"],
    "desired salary": ["compensation expectations", "salary expectations", "desired pay range", "pay range"],
    "compensation expectations": ["desired salary", "salary expectations", "desired pay range", "pay range"],
    "open to relocate": ["willing to relocate", "relocation"],
    "reliable commute": ["reliably commute", "commute to this job", "commute to"],
    "previously employed by company": ["previously employed", "previous employer", "former employee", "ever worked", "worked for this company", "worked at this company", "worked for us", "worked at our company"],
    "security clearance": ["active security clearance", "security clearance", "clearance"],
    "available to work weekends": ["available to work weekends", "work weekends", "weekend availability"],
    "relevant project link": ["project most relevant", "relevant project", "project link"],
    "why this role": ["why are you interested", "why interested in this role", "interested in this role", "interested in this startup"],
    "production ai system summary": ["production ai system", "ai system you shipped"],
  };
  const normalized = normalizeText(prompt);
  return [normalized, ...(aliasMap[normalized] || []).map(normalizeText)];
}

function customPromptMatchesLabel(prompt, label) {
  if (prompt === label || prompt.includes(label) || label.includes(prompt)) return true;
  const promptTokens = meaningfulTokens(prompt);
  const labelTokens = new Set(meaningfulTokens(label));
  return promptTokens.length > 0 && promptTokens.every((token) => labelTokens.has(token));
}

function meaningfulTokens(value) {
  const stopWords = new Set(["are", "can", "did", "for", "have", "how", "the", "this", "what", "when", "with", "you", "your"]);
  return normalizeText(value).split(" ").filter((token) => token.length > 2 && !stopWords.has(token));
}

function normalizeText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\*/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeQuestion(value) {
  return normalizeText(stripRequiredMarker(value));
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--headed" || arg === "--verbose") {
      parsed[arg.slice(2)] = true;
      continue;
    }
    const match = arg.match(/^--([^=]+)=(.*)$/);
    if (match) {
      parsed[match[1]] = match[2];
      continue;
    }
    if (arg.startsWith("--")) {
      const next = argv[index + 1];
      if (next && !next.startsWith("--")) {
        parsed[arg.slice(2)] = next;
        index += 1;
      } else {
        parsed[arg.slice(2)] = true;
      }
    }
  }
  return parsed;
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

function escapeMarkdown(value) {
  return String(value || "").replace(/\|/g, "\\|").replace(/\n/g, " ");
}
