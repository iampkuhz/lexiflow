import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateBrowserResult, evaluateUnitResult } from "./quality-results.mjs";

const extensionRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function execute(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: extensionRoot, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (error) => resolve({ exitCode: null, stdout, stderr: `${stderr}${error.message}\n` }));
    child.on("close", (exitCode) => resolve({ exitCode, stdout, stderr }));
  });
}

function report(status, phase, unitTests, browserSmoke, reason = "") {
  process.stdout.write(`${JSON.stringify({ status, phase, unit_tests: unitTests, browser_smoke: browserSmoke, reason })}\n`);
}

async function main() {
  const build = await execute("npm", ["run", "build"]);
  if (build.exitCode !== 0) {
    if (build.stdout || build.stderr) process.stderr.write(`${build.stdout}${build.stderr}`);
    report("FAIL", "build", 0, 0, "extension-build-failed");
    return;
  }
  if (build.stdout || build.stderr) process.stderr.write(`${build.stdout}${build.stderr}`);

  const units = await execute("node", ["--test", "--test-reporter=tap", "tests/*.test.mjs"]);
  if (units.stdout || units.stderr) process.stderr.write(`${units.stdout}${units.stderr}`);
  const unit = evaluateUnitResult(units);
  if (unit.status !== "PASS") {
    report("FAIL", "unit", unit.unit_tests, 0, unit.reason);
    return;
  }

  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    report("BLOCKED", "browser-resource", unit.unit_tests, 0, "playwright-package-unavailable");
    return;
  }
  if (!existsSync(chromium.executablePath())) {
    report("BLOCKED", "browser-resource", unit.unit_tests, 0, "playwright-browser-unavailable");
    return;
  }

  const browserProcess = await execute("node", ["tests/translation-e2e.mjs"]);
  // Preserve the original assertion and stack in the check log, not just its category.
  if (browserProcess.stdout || browserProcess.stderr) process.stderr.write(`${browserProcess.stdout}${browserProcess.stderr}`);
  const browser = evaluateBrowserResult(browserProcess);
  if (browser.status === "BLOCKED") {
    report("BLOCKED", "browser-api-resource", unit.unit_tests, 0, browser.reason);
  } else if (browser.status === "FAIL") {
    report("FAIL", "browser-api-smoke", unit.unit_tests, browser.browser_smoke, browser.reason);
  } else {
    report("PASS", "complete", unit.unit_tests, browser.browser_smoke);
  }
}

try {
  await main();
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  report("FAIL", "runner", 0, 0, "quality-runner-failed");
}
