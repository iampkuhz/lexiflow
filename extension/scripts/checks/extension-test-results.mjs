/** Parse only the extension-owned Node TAP and E2E result contracts. */

const SUMMARY_FIELDS = ["tests", "pass", "fail", "cancelled", "skipped", "todo"];

function numericSummary(tap, field) {
  const matches = [...tap.matchAll(new RegExp(`^# ${field} (\\d+)\\s*$`, "gm"))];
  if (matches.length !== 1) return null;
  return Number(matches[0][1]);
}

export function parseTapSummary(tap) {
  const summary = Object.fromEntries(SUMMARY_FIELDS.map((field) => [field, numericSummary(tap, field)]));
  const plan = [...tap.matchAll(/^(\d+)\.\.(\d+)\s*$/gm)];
  if (plan.length !== 1 || Object.values(summary).some((value) => value === null)) return { complete: false, ...summary };
  const planned = Number(plan[0][2]) - Number(plan[0][1]) + 1;
  const complete = planned === summary.tests && summary.pass + summary.fail + summary.cancelled + summary.skipped + summary.todo === summary.tests;
  return { complete, ...summary };
}

export function evaluateUnitResult({ exitCode, stdout, stderr }) {
  const summary = parseTapSummary(stdout);
  if (exitCode !== 0) return { status: "FAIL", reason: "extension-unit-command-failed", unit_tests: 0, summary };
  if (stderr.trim()) return { status: "FAIL", reason: "extension-unit-result-inconsistent", unit_tests: 0, summary };
  if (!summary.complete || summary.tests === 0 || summary.pass !== summary.tests || summary.fail !== 0 || summary.cancelled !== 0 || summary.skipped !== 0 || summary.todo !== 0) {
    return { status: "FAIL", reason: "extension-unit-tests-incomplete", unit_tests: 0, summary };
  }
  return { status: "PASS", reason: "", unit_tests: summary.pass, summary };
}

export function evaluateBrowserResult({ exitCode, stdout, stderr }) {
  let report;
  try {
    report = JSON.parse(stdout.trim());
  } catch {
    return { status: "FAIL", reason: "browser-api-smoke-result-invalid", browser_smoke: 0 };
  }
  if (!report || typeof report !== "object" || typeof report.status !== "string" || !Number.isInteger(report.browser_smoke) || !Number.isInteger(report.api_smoke)) {
    return { status: "FAIL", reason: "browser-api-smoke-result-invalid", browser_smoke: 0 };
  }
  if (report.status === "BLOCKED" && exitCode === 0) return { status: "BLOCKED", reason: report.reason || "browser-api-resource-unavailable", browser_smoke: 0 };
  if (report.status === "PASS" && exitCode === 0 && !stderr.trim() && report.browser_smoke > 0 && report.api_smoke > 0) {
    return { status: "PASS", reason: "", browser_smoke: report.browser_smoke };
  }
  return { status: "FAIL", reason: "browser-api-smoke-assertion-failed", browser_smoke: 0 };
}
