#!/usr/bin/env node
/** Zip the extension for distribution: dist/applytex-extension-<version>.zip (Web Store upload format). */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "extension");
const manifest = JSON.parse(fs.readFileSync(path.join(source, "manifest.json"), "utf8"));
const include = [
  "manifest.json",
  "background.js",
  "providers.js",
  "panel-shared.js",
  "panel-scan.js",
  "panel-fill.js",
  "panel-workday.js",
  "panel-profile.js",
  "panel.js",
  "options.html",
  "options.js",
];
for (const name of include) {
  if (!fs.existsSync(path.join(source, name))) throw new Error(`missing ${name}`);
  if (name.endsWith(".js")) execFileSync(process.execPath, ["--check", path.join(source, name)], { stdio: "inherit" });
}
for (const name of ["panel.js", "background.js"]) {
  const text = fs.readFileSync(path.join(source, name), "utf8");
  if (/APPLYTEX_DEBUG|:7402/.test(text)) throw new Error(`${name} contains debug hooks; refusing to package`);
}
const dist = path.join(root, "dist");
fs.mkdirSync(dist, { recursive: true });
const out = path.join(dist, `applytex-extension-${manifest.version}.zip`);
fs.rmSync(out, { force: true });
execFileSync("zip", ["-q", "-X", out, ...include], { cwd: source, stdio: "inherit" });
const size = fs.statSync(out).size;
console.log(`${path.relative(root, out)} (${(size / 1024).toFixed(1)} KB, ${include.length} files, manifest v${manifest.version})`);
