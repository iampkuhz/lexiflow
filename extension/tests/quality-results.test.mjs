import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { cp, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { evaluateBrowserResult, evaluateUnitResult } from "../scripts/quality-results.mjs";

const normalTap = `TAP version 13
# Subtest: bounded protocol
ok 1 - bounded protocol
  ---
  duration_ms: 1
  ...
# Subtest: stream update
ok 2 - stream update
  ---
  duration_ms: 1
  ...
1..2
# tests 2
# suites 0
# pass 2
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 3
`;

test("quality adapter accepts a complete, real-style Node TAP report", () => {
  const result = evaluateUnitResult({ exitCode: 0, stdout: normalTap, stderr: "" });
  assert.equal(result.status, "PASS");
  assert.equal(result.unit_tests, 2);
});

test("quality adapter rejects skipped or todo Node tests", () => {
  const skipped = normalTap.replace("# pass 2", "# pass 1").replace("# skipped 0", "# skipped 1");
  assert.equal(evaluateUnitResult({ exitCode: 0, stdout: skipped, stderr: "" }).reason, "extension-unit-tests-incomplete");
  const todo = normalTap.replace("# pass 2", "# pass 1").replace("# todo 0", "# todo 1");
  assert.equal(evaluateUnitResult({ exitCode: 0, stdout: todo, stderr: "" }).reason, "extension-unit-tests-incomplete");
});

test("quality adapter rejects empty Node test suites", () => {
  const empty = normalTap.replace("ok 1 - bounded protocol\n  ---\n  duration_ms: 1\n  ...\n# Subtest: stream update\nok 2 - stream update\n  ---\n  duration_ms: 1\n  ...\n1..2\n# tests 2\n# suites 0\n# pass 2", "1..0\n# tests 0\n# suites 0\n# pass 0");
  assert.equal(evaluateUnitResult({ exitCode: 0, stdout: empty, stderr: "" }).reason, "extension-unit-tests-incomplete");
});

test("quality adapter rejects exit or stderr mismatches", () => {
  assert.equal(evaluateUnitResult({ exitCode: 1, stdout: normalTap, stderr: "" }).reason, "extension-unit-command-failed");
  assert.equal(evaluateUnitResult({ exitCode: 0, stdout: normalTap, stderr: "unreported failure\n" }).reason, "extension-unit-result-inconsistent");
});

test("browser adapter requires an E2E-owned successful execution report", () => {
  const result = evaluateBrowserResult({ exitCode: 0, stdout: JSON.stringify({ status: "PASS", browser_smoke: 1, api_smoke: 1, reason: "" }), stderr: "" });
  assert.deepEqual(result, { status: "PASS", reason: "", browser_smoke: 1 });
});

test("quality command preserves browser assertion diagnostics without corrupting stdout", async () => {
  const root = await mkdtemp(join(tmpdir(), "lexiflow-quality-diagnostics-"));
  try {
    for (const folder of ["scripts", "tests", "node_modules/playwright"]) {
      await mkdir(join(root, folder), { recursive: true });
    }
    for (const file of ["quality-check.mjs", "quality-results.mjs"]) {
      await cp(new URL(`../scripts/${file}`, import.meta.url), join(root, "scripts", file));
    }
    await writeFile(join(root, "package.json"), JSON.stringify({
      type: "module", scripts: { build: "node -e \"process.exit(0)\"" }
    }));
    // Only the adapter is exercised here; the synthetic child deliberately fails.
    await writeFile(join(root, "tests/fixture.test.mjs"), 'import test from "node:test"; test("fixture", () => {});');
    await writeFile(join(root, "node_modules/playwright/package.json"), JSON.stringify({ type: "module", exports: "./index.js" }));
    await writeFile(join(root, "node_modules/playwright/index.js"), 'export const chromium = { executablePath: () => process.execPath };');
    await writeFile(join(root, "tests/translation-e2e.mjs"), `
      console.log(JSON.stringify({ status: "FAIL", browser_smoke: 0, api_smoke: 0, reason: "browser-api-smoke-assertion-failed" }));
      console.error("AssertionError: synthetic-caption-mismatch\\n    at fixture:42");
      process.exitCode = 1;
    `);
    const env = { ...process.env };
    // This is a separate CLI invocation, not a recursive node:test worker.
    delete env.NODE_TEST_CONTEXT;
    const result = spawnSync(process.execPath, [join(root, "scripts/quality-check.mjs")], { env, encoding: "utf8", timeout: 20_000 });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0);
    assert.equal(JSON.parse(result.stdout).status, "FAIL");
    assert.match(result.stderr, /AssertionError: synthetic-caption-mismatch\n    at fixture:42/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
