#!/usr/bin/env node
/** End-to-end executor check against the synthetic lab: enqueue -> fill -> pause -> approve -> submit -> receipt. */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const base = process.env.AUTOFILL_LAB_URL || "http://127.0.0.1:8765";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Executor lab QA only runs on localhost.");
const LAB_ORIGIN = "https://applytex-lab.example.test";
const PROFILE = "autofill-lab";
const scenarios = (process.env.EXECUTOR_LAB_SCENARIOS || "complete,multi-step").split(",");
const headers = { "Content-Type": "application/json", "X-Profile-Id": PROFILE };
const report = { started_at: new Date().toISOString(), kind: "executor-lab", cases: [], failures: [] };
// A candidate-written letter with no numbers or links, so the grounding validator accepts it.
const LAB_LETTER = [
  "I am writing to apply for the Machine Learning Engineer role at Example Research Labs. Your focus on reliable data pipelines and careful model evaluation matches the work I enjoy most, and I would like to bring that experience to your team and grow with it.",
  "At Example Analytics I built tested SQL pipelines and Python tooling that other teams depended on every day. I care about data quality, clear monitoring, and deployments that do not surprise anyone, and I have learned to write down the tradeoffs so the next engineer can follow them without guessing.",
  "I am comfortable owning a system from ingestion through evaluation, and I like working with people who review each other's work closely and share what they learn. Reliability is a habit rather than a milestone, and I try to build it into the tooling instead of relying on heroics.",
  "I would welcome the chance to talk about how my background fits the problems your team is solving now, and I am happy to walk through the pipelines I have built in as much detail as is useful.",
].join("\n\n");
const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "applytex-executor-qa-"));

const executor = spawn(process.execPath, [
  path.join(root, "scripts/executor.mjs"),
  "--api", base, "--profile", PROFILE, "--headless", "--poll", "1",
  "--user-data-dir", userDataDir,
  "--url-rewrite", `${LAB_ORIGIN}=${base}`,
  ...(process.env.EXECUTOR_LAB_DEBUG ? ["--debug"] : []),
], { stdio: ["ignore", "pipe", "pipe"] });
let executorLog = "";
executor.stdout.on("data", (chunk) => { executorLog += chunk; });
executor.stderr.on("data", (chunk) => { executorLog += chunk; });

try {
  for (const scenario of scenarios) {
    const record = { scenario, success: false, errors: [], assertions: 0 };
    const check = (condition, message) => { record.assertions += 1; if (!condition) record.errors.push(message); };
    try {
      const pageUrl = `${LAB_ORIGIN}/lab/greenhouse/${scenario}`;
      const job = await api("POST", "/extension/jobs/capture", {
        provider: "greenhouse", external_id: `executor-${scenario}-${Date.now()}`,
        company: "Example Research Labs", title: "Machine Learning Engineer", location: "Austin, TX",
        description: "Example Research Labs is hiring a Machine Learning Engineer. Work on Python and SQL pipelines, model evaluation, data quality, APIs, monitoring and reliable deployments. This fictional role supports a synthetic executor test. No employer account is created and no external application is submitted.",
        source_url: pageUrl, apply_url: pageUrl,
      });
      const application = await api("POST", "/applications", { job_id: job.job_id });
      const run = await api("POST", `/applications/${application.application_id}/apply-runs`, {});
      record.run_id = run.run_id;
      check(run.status === "queued", "run did not start queued");
      // Once an earlier scenario remembered the Rust answer, the bank resolves it
      // on later applications and the executor never has to ask again.
      const bankHadRust = (await api("GET", "/profile/answers")).answers.some((answer) => /rust/i.test(answer.prompt_text));

      // The lab form has a required cover-letter upload and a deliberate unknown
      // ("years of Rust", on the last step): the executor must pause per step
      // rather than guess. The "human" answers whatever each pause asks for.
      let current = await waitForStatus(run.run_id, ["paused_for_review", "awaiting_input", "failed"], 120000);
      check(current.status === "awaiting_input", `expected awaiting_input first, got ${current.status}: ${current.error || current.step_log?.at(-1)?.message}`);
      check(current.screenshot_path, "no screenshot recorded when pausing for input");
      let rustAnswered = false;
      let letterApproved = false;
      const asked = new Set();
      for (let round = 0; round < 3 && current.status === "awaiting_input"; round += 1) {
        (current.unresolved_required || []).forEach((label) => asked.add(label));
        const detail = await api("GET", `/applications/${application.application_id}`);
        const scan = detail.latest_form_scan;
        const rust = scan?.questions.find((question) => /rust/i.test(question.label));
        if (rust && !rustAnswered) {
          await api("POST", `/extension/forms/${scan.scan_id}/plan`, { overrides: { [rust.field_id]: "0" }, remember: true });
          rustAnswered = true;
        }
        if (!letterApproved && (current.unresolved_required || []).some((label) => /cover letter/i.test(label))) {
          const letter = await api("POST", `/applications/${application.application_id}/cover-letter`, { text: LAB_LETTER });
          const approvedLetter = await api("POST", `/applications/${application.application_id}/cover-letter/${letter.artifact.artifact_id}/approve`, {});
          check(approvedLetter.artifact.status === "approved", "cover letter was not approved");
          letterApproved = true;
        }
        await api("POST", `/apply-runs/${run.run_id}/resume`, { notes: `round ${round + 1}` });
        current = await waitForStatus(run.run_id, ["paused_for_review", "awaiting_input", "failed"], 120000);
      }
      check(bankHadRust || [...asked].some((label) => /rust/i.test(label)), "unknown Rust question was neither remembered nor surfaced");
      check([...asked].some((label) => /cover letter/i.test(label)), "required cover letter was never surfaced");
      check((bankHadRust || rustAnswered) && letterApproved, "harness did not get to answer the blockers");
      record.rust_resolved_from_bank = bankHadRust;
      const paused = current;
      check(paused.status === "paused_for_review", `expected paused_for_review, got ${paused.status}: ${paused.error || paused.step_log?.at(-1)?.message}`);
      check((paused.unresolved_required || []).length === 0, `still unresolved after resume: ${(paused.unresolved_required || []).join("; ")}`);
      check(paused.step_log.some((entry) => /attached the approved cover letter/i.test(entry.message)), "executor did not attach the approved cover letter");
      const bank = await api("GET", "/profile/answers");
      check(bank.answers.some((answer) => /rust/i.test(answer.prompt_text)), "Rust answer was not remembered in the answers bank");
      check((paused.review_summary?.ready || 0) + (paused.review_summary?.already_filled || 0) >= 8, `too few filled fields: ${JSON.stringify(paused.review_summary && { ready: paused.review_summary.ready, already_filled: paused.review_summary.already_filled })}`);
      check(paused.screenshot_path, "no screenshot recorded at review");
      const shot = await fetch(`${base}/apply-runs/${run.run_id}/screenshot`, { headers });
      check(shot.ok && shot.headers.get("content-type")?.includes("image/png"), "screenshot endpoint did not serve a PNG");
      const beforeApproval = await api("GET", `/applications/${application.application_id}`);
      check(beforeApproval.application.status !== "submitted", "application was submitted before approval");
      check(!(await api("GET", `/apply-runs/${run.run_id}`)).approval_token, "approval token existed before approval");

      // The executor must refuse to record a submission without the token.
      const forged = await fetch(`${base}/apply-runs/${run.run_id}/submitted`, { method: "POST", headers, body: JSON.stringify({ approval_token: "forged" }) });
      check(forged.status === 409 || forged.status === 403, `forged submission returned ${forged.status}`);

      const approved = await api("POST", `/apply-runs/${run.run_id}/approve`, { notes: "lab QA approval" });
      check(approved.status === "approved" && approved.approval_token, "approve did not mint a token");

      const finished = await waitForStatus(run.run_id, ["submitted", "needs_verification", "failed"], 90000);
      check(finished.status === "submitted", `expected submitted, got ${finished.status}: ${finished.error || finished.step_log?.at(-1)?.message}`);
      check(!finished.approval_token, "approval token was not cleared after submission");
      const after = await api("GET", `/applications/${application.application_id}`);
      check(after.application.status === "submitted", `application status is ${after.application.status}`);
      const receipt = await api("GET", `/applications/${application.application_id}/submission`);
      check(receipt.fields.length >= 8, `receipt has only ${receipt.fields.length} fields`);
      check(receipt.confirmed_by === "user" && /executor|thank you/i.test(receipt.detection_evidence), "receipt provenance is wrong");
      check(after.tasks.some((task) => task.category === "follow_up"), "no follow-up task scheduled");
      record.run_id = run.run_id;
    } catch (error) {
      record.errors.push(error.message);
    }
    record.success = record.errors.length === 0;
    if (record.run_id) {
      const finalRun = await api("GET", `/apply-runs/${record.run_id}`).catch(() => null);
      record.step_log = finalRun?.step_log || [];
      record.final_status = finalRun?.status;
    }
    if (!record.success) report.failures.push({ scenario, errors: record.errors });
    report.cases.push(record);
    console.log(`${record.success ? "PASS" : "FAIL"} executor/greenhouse/${scenario}: ${record.assertions} assertions${record.errors.length ? " — " + record.errors.join("; ") : ""}`);
    if (!record.success) {
      for (const entry of (record.step_log || []).slice(-14)) console.log(`    ${entry.at.slice(11, 19)} [${entry.level}] ${entry.message.slice(0, 160)}`);
    }
  }
} finally {
  executor.kill("SIGTERM");
  await new Promise((resolve) => executor.once("exit", resolve));
  fs.rmSync(userDataDir, { recursive: true, force: true });
}
report.completed_at = new Date().toISOString();
report.executor_log_tail = executorLog.split("\n").slice(-40).join("\n");
const out = path.join(root, ".applytex/qa/executor-lab.json");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(report, null, 2) + "\n");
console.log(`${report.cases.filter((c) => c.success).length}/${report.cases.length} passed. ${out}`);
if (report.failures.length) { console.log(report.executor_log_tail); process.exitCode = 1; }

async function api(method, apiPath, body) {
  const response = await fetch(`${base}${apiPath}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(`${method} ${apiPath} -> ${response.status}: ${data?.detail || text}`);
  return data;
}

async function waitForStatus(runId, statuses, timeout) {
  const deadline = Date.now() + timeout;
  let run = null;
  while (Date.now() < deadline) {
    run = await api("GET", `/apply-runs/${runId}`);
    if (statuses.includes(run.status)) return run;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return run;
}
