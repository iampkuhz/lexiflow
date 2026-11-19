import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm, readFile, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright";
import { waitForE2EState } from "./e2e-state.mjs";
import { openActionPopup } from "./action-popup.mjs";
import { createApiLaunchSpec, stopApi } from "./e2e-runtime.mjs";
import { resolveBuildIdentity } from "../../ops/release/integrity/source-build-identity.mjs";
import { createExtensionZip } from "../scripts/packaging/package-extension-release.mjs";

const extensionRoot = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(extensionRoot, "..");
let extensionPath;
let apiBase;
let apiPort;

class ApiResourceUnavailable extends Error {}

function assertApiResources() {
  if (!existsSync(resolve(repositoryRoot, "backend/gradlew"))) {
    throw new ApiResourceUnavailable("api-launcher-resource-unavailable");
  }
  const java = spawnSync("python3", ["-c", "from pathlib import Path; from scripts.environment.toolchains.java_toolchain_selection import resolve_java_home; import os; print(resolve_java_home(Path('.'), os.environ))"], {
    cwd: repositoryRoot,
    encoding: "utf8"
  });
  if (java.error || java.status !== 0) throw new ApiResourceUnavailable("api-java-runtime-unavailable");
  return java.stdout.trim();
}

function startApi(javaHome, preferencesDatabase) {
  const spec = createApiLaunchSpec({ repositoryRoot, apiPort, javaHome, preferencesDatabase });
  const child = spawn(spec.command, spec.args, {
    cwd: repositoryRoot,
    env: spec.env,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.once("error", (error) => { child.launchError = error; });
  return child;
}

function runDailyCountFixture(database, operation, input) {
  // Use the Python already required by this test, without depending on experimental Node SQLite.
  const result = spawnSync("python3", ["-c", `
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys

database, operation = sys.argv[1:]
data = json.load(sys.stdin)
identity = int(data["identity"])
if operation == "seed":
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(data["schema"])
        db.executemany("INSERT INTO recent_hint_daily_count(day, entry_id, count) VALUES (?, ?, ?)",
                       [(day, identity, count) for day, count in data["rows"]])
else:
    with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(recent_hint_daily_count)")]
        rows = list(db.execute("SELECT day, count FROM recent_hint_daily_count WHERE entry_id = ? ORDER BY day",
                               (identity,)))
        print(json.dumps({"columns": columns, "rows": rows}))
`, database, operation], {
    input: JSON.stringify(input),
    encoding: "utf8"
  });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  return result.stdout;
}

async function prepareDailyCountFixture(preferencesDatabase) {
  const schema = await readFile(resolve(repositoryRoot, "infra/sqlite/preferences.sql"), "utf8");
  // This fixture uses the built-in demo identity for "deliver", which no test caption displays.
  const identity = (createHash("sha256").update("entry:en:deliver").digest().readBigUInt64BE() & ((1n << 63n) - 1n)).toString();
  const now = Date.now();
  const day = (daysAgo) => new Date(now - daysAgo * 86_400_000).toISOString().slice(0, 10);
  runDailyCountFixture(preferencesDatabase, "seed", {
    identity, schema, rows: [[day(0), 11], [day(6), 13], [day(7), 17], [day(8), 29]]
  });
  return () => {
    const retained = JSON.parse(runDailyCountFixture(preferencesDatabase, "read", { identity }));
    assert.deepEqual(retained, {
      columns: ["day", "entry_id", "count"],
      rows: [[day(7), 17], [day(6), 13], [day(0), 11]]
    });
  };
}

async function freePort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitForApi(process) {
  let logs = "";
  process.stdout.on("data", (chunk) => (logs += chunk));
  process.stderr.on("data", (chunk) => (logs += chunk));
  for (let attempt = 0; attempt < 90; attempt += 1) {
    if (process.launchError) throw new ApiResourceUnavailable("api-launcher-resource-unavailable");
    if (process.exitCode !== null) throw new Error(`API stopped before readiness:\n${logs}`);
    try {
      if ((await fetch(`${apiBase}/actuator/health/liveness`)).ok) {
        const readiness = await fetch(`${apiBase}/actuator/health/readiness`);
        assert.equal(readiness.status, 503, "demo must not advertise formal readiness");
        assert.match(await readiness.text(), /DEMO_MODE/u);
        return;
      }
    } catch {
      // The process is still starting.
    }
    await delay(1000);
  }
  throw new Error(`API did not become ready:\n${logs}`);
}

function youtubeFixture() {
  return `<!doctype html>
<html><head><style>
#player { position: relative; width: 960px; height: 540px; background: #222; color: white; }
#ytp-caption-window-container { position: absolute; left: 10%; right: 10%; bottom: 20%; text-align: center; font: 24px Arial; }
</style></head><body>
<div id="player" class="html5-video-player"><video></video><div id="ytp-caption-window-container"></div></div>
<script>
  const video = document.querySelector('video');
  let time = 0;
  Object.defineProperty(video, 'currentTime', { configurable: true, get: () => time });
  window.__setCaption = (caption, nextTime) => {
    time = nextTime;
    document.querySelector('#ytp-caption-window-container').replaceChildren(
      Object.assign(document.createElement('span'), { className: 'ytp-caption-segment', textContent: caption })
    );
    video.dispatchEvent(new Event('timeupdate', { bubbles: true }));
  };
</script></body></html>`;
}

async function setCaption(page, caption, time) {
  await page.evaluate(([nextCaption, nextTime]) => window.__setCaption(nextCaption, nextTime), [caption, time]);
}

async function overlayState(page) {
  return page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-state");
}

async function overlayText(page) {
  return page.locator("#lexiflow-caption-overlay").evaluate((host) => host.shadowRoot?.querySelector("span")?.textContent?.trim() ?? "");
}
async function overlayServiceStatus(page) {
  return page.locator("#lexiflow-caption-overlay").evaluate((host) => host.shadowRoot?.querySelector(".service-status")?.textContent?.trim() ?? "");
}

const waitForState = waitForE2EState;

let api;
let userDataDir;
let context;
let evidenceRoot;
try {
  if (process.env.LEXIFLOW_LIVE_YOUTUBE_URL) throw new ApiResourceUnavailable("synthetic-e2e-refuses-live-url");
  await mkdir(resolve(repositoryRoot, "tmp/quality/e2e-runs"), { recursive: true });
  evidenceRoot = await mkdtemp(resolve(repositoryRoot, "tmp/quality/e2e-runs/run-"));
  // Build a fresh, explicit development package after unit fixtures have finished.
  // Never attach a synthetic published-dataset identity to the demo backend.
  const developmentEnvironment = { ...process.env, LEXIFLOW_API_PORT: "18081" };
  delete developmentEnvironment.LEXIFLOW_EXPECTED_DATASET;
  const developmentBuild = spawnSync("npm", ["run", "build"], {
    cwd: extensionRoot, env: developmentEnvironment, encoding: "utf8"
  });
  if (developmentBuild.error || developmentBuild.status !== 0) {
    throw new Error(`Development extension build failed:\n${developmentBuild.stderr ?? ""}`);
  }
  const javaHome = assertApiResources();
  apiPort = await freePort();
  apiBase = `http://127.0.0.1:${apiPort}`;
  userDataDir = await mkdtemp(resolve(tmpdir(), "lexiflow-extension-e2e-"));
  const preferencesDatabase = resolve(userDataDir, "preferences.db");
  const assertDailyRetention = await prepareDailyCountFixture(preferencesDatabase);
  api = startApi(javaHome, preferencesDatabase);
  await waitForApi(api);
  assertDailyRetention();
  const testExtensionPath = resolve(userDataDir, "extension");
  const packaged = await createExtensionZip({ root: extensionRoot, buildIdentity: resolveBuildIdentity(repositoryRoot), apiPort: 18081 });
  await mkdir(testExtensionPath, { recursive: true });
  for (const [name, bytes] of Object.entries(packaged.unzip())) {
    const target = resolve(testExtensionPath, name);
    await mkdir(resolve(target, ".."), { recursive: true });
    await writeFile(target, bytes);
  }
  extensionPath = testExtensionPath;
  const manifest = JSON.parse(await readFile(resolve(testExtensionPath, "manifest.json"), "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(resolve(testExtensionPath, "manifest.json"), JSON.stringify(manifest));
  context = await chromium.launchPersistentContext(resolve(userDataDir, "profile"), {
    headless: false,
    channel: "chromium",
    args: [`--disable-extensions-except=${testExtensionPath}`, `--load-extension=${testExtensionPath}`]
  });
  const serviceWorker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  await serviceWorker.evaluate((base) => {
    const originalFetch = globalThis.fetch;
    // In-memory synthetic service contract for preferences: no durable profile fixture or private preference DB.
    const localPreferences = { commonWordCutoff: 1500, hidden: [] };
    globalThis.fetch = (input, options) => {
      const pathname = new URL(typeof input === "string" ? input : input.url).pathname;
      if (pathname === "/api/v1/preferences") {
        let action;
        try { action = JSON.parse(options?.body ?? "{}"); } catch { return Promise.resolve(new Response("{}", { status: 400 })); }
        if (action.action === "setCutoff") localPreferences.commonWordCutoff = action.commonWordCutoff;
        else if (action.action === "hide" && !localPreferences.hidden.some(item => item.entryId === action.entryId))
          localPreferences.hidden.push({ entryId: action.entryId, lemma: action.lemma, gloss: action.gloss });
        else if (action.action === "restore") localPreferences.hidden.splice(0, localPreferences.hidden.length,
          ...localPreferences.hidden.filter(item => item.entryId !== action.entryId));
        else if (action.action !== "read") return Promise.resolve(new Response("{}", { status: 400 }));
        return Promise.resolve(new Response(JSON.stringify(localPreferences), { status: 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } }));
      }
      return originalFetch(`${base}${pathname}`, options);
    };
  }, apiBase);
  const page = await context.newPage();
  const logs = [];
  page.on("console", message => logs.push(message.text()));
  await page.route("https://www.youtube.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: youtubeFixture() })
  );
  await page.goto("https://www.youtube.com/watch?v=LfxE2ETest1", { waitUntil: "domcontentloaded" });

  await setCaption(page, "zxqv zxqv", 1);
  await waitForState(page, "no-pending");
  assert.match(await overlayServiceStatus(page), /演示模式/u, "content must report the actual non-formal runtime mode");
  const initialCounts = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics"));
  assert.equal(initialCounts.counts.no_hint, 1);
  assert.equal(initialCounts.timings.endToEnd.count, 1);
  await page.evaluate(() => window.dispatchEvent(new Event("resize")));
  const redrawn = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics"));
  assert.equal(redrawn.timings.endToEnd.count, 1, "layout redraw is not another terminal");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "zxqv zxqv");
  assert.equal(await overlayText(page), "zxqv zxqv");

  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "ready");
  const observedBeforeVisibility = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics")).counts.observed;
  const observedAfterVisibility = await page.evaluate(() => {
    document.querySelector(".ytp-caption-segment").textContent = "We need reliable captions again.";
    document.dispatchEvent(new Event("visibilitychange"));
    return JSON.parse(document.querySelector("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics")).counts.observed;
  });
  assert.ok(observedAfterVisibility > observedBeforeVisibility, "content bundle's visibilitychange listener must synchronously recapture the live caption");
  await waitForState(page, "ready");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "We need reliable captions again.");
  assert.match(await overlayText(page), /可靠的/);
  assert.equal(await overlayText(page), "We need reliable(可靠的) captions again.");
  const diagnostics = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics"));
  assert.ok(diagnostics.counts.ready >= 1);
  for (const stage of ["acquisition", "coalesce", "transport", "query", "rules", "api", "render", "endToEnd"]) {
    assert.ok(diagnostics.timings[stage].count > 0, `missing stage: ${stage}`);
    assert.ok(diagnostics.timings[stage].p95Ms >= 0);
  }
  assert.equal(JSON.stringify(diagnostics).includes("reliable"), false);
  assert.equal(await page.locator("#player").evaluate(el => el.classList.contains("lexiflow-inline-active")), true);
  assert.match(await page.locator("#ytp-caption-window-container").evaluate(el => getComputedStyle(el).clipPath), /inset/);
  const inlineLayout = await page.evaluate(() => {
    const line = document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".line");
    const gloss = line.querySelector(".gloss");
    return { lineBottom: line.getBoundingClientRect().bottom, glossBottom: gloss.getBoundingClientRect().bottom };
  });
  assert.ok(Math.abs(inlineLayout.lineBottom - inlineLayout.glossBottom) < 15);
  await page.locator("#player").screenshot({ path: resolve(evidenceRoot, "inline-caption-preview.png") });
  const { runUnderlineAcceptance } = await import("./visual-acceptance.mjs");
  await runUnderlineAcceptance({page,serviceWorker,apiBase,repositoryRoot: evidenceRoot,setCaption,waitForState});
  // Optional extended acceptance remains entirely on the local synthetic fixture.
  // It never visits the real YouTube page or operates a user-owned browser profile.
  if (process.env.LEXIFLOW_EXTENDED_ACCEPTANCE === "1") {
    const artifactRoot = resolve(evidenceRoot, "tmp/quality/extended-acceptance", String(Date.now()));
    const { runVisualAcceptance } = await import("./visual-acceptance.mjs");
    const { runContinuousAcceptance } = await import("./continuous-acceptance.mjs");
    const visual = await runVisualAcceptance({ page, serviceWorker, apiBase, artifactRoot, setCaption, waitForState, overlayText });
    await writeFile(resolve(artifactRoot, "visual-report.json"), JSON.stringify(visual, null, 2));
    const continuous = await runContinuousAcceptance({ page, artifactRoot, setCaption, waitForState, overlayText,
      seconds: Number(process.env.LEXIFLOW_SOAK_SECONDS ?? "375") });
    await writeFile(resolve(evidenceRoot, "tmp/quality/extended-acceptance/latest.json"),
      JSON.stringify({ artifactRoot, status: "PASS", cases: visual.cases.length, processedCues: continuous.processedCues }));
    await setCaption(page, "We need reliable captions.", 2);
    await waitForState(page, "ready");
  }
  const { runIncrementalAcceptance } = await import("./incremental-acceptance.mjs");
  await runIncrementalAcceptance({page,serviceWorker,setCaption,waitForState,overlayText});
  const { runMultilineAcceptance } = await import("./multiline-acceptance.mjs");
  await runMultilineAcceptance({page,serviceWorker,repositoryRoot: evidenceRoot,setCaption,waitForState});
  const { runExperienceAcceptance } = await import("./experience-acceptance.mjs");
  await runExperienceAcceptance({ page, context, serviceWorker, apiBase, repositoryRoot: evidenceRoot, setCaption, waitForState, overlayText });
  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "ready");
  // A user choice is local, persists across reload, and can be explicitly reversed.
  await page.locator("#lexiflow-caption-overlay .gloss").click();
  await waitForState(page, "no-pending");
  assert.equal(await overlayText(page), "We need reliable captions.");
  const suppressedCounts = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics")).counts;
  assert.equal(suppressedCounts.suppressed, 1);
  await page.evaluate(() => window.dispatchEvent(new Event("resize")));
  assert.equal(JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics")).counts.suppressed, 1);
  assert.equal(await page.locator("#player").evaluate(el => el.classList.contains("lexiflow-inline-active")), true);
  await page.reload({waitUntil:"domcontentloaded"});
  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "no-pending");
  await page.bringToFront();
  const popup = await openActionPopup({context,page,serviceWorker});
  let managePage;
  try {
    const opened = context.waitForEvent("page");
    await popup.click("#open-manage");
    managePage = await opened;
    await managePage.waitForLoadState("domcontentloaded");
    await managePage.waitForFunction(() => document.querySelector("#hidden-count")?.textContent === "1");
    // Read actual overlay receipts through the management page opened by the extension.
    await managePage.setViewportSize({ width: 320, height: 640 });
    const reliableRow = managePage.locator("#recent-list .row:not(.head)").filter({
      has: managePage.getByText("reliable", { exact: true })
    });
    await reliableRow.waitFor({ timeout: 10_000 });
    assert.ok(Number(await reliableRow.locator(".number").textContent()) > 0);
    assert.equal(await managePage.locator("#recent-heading").textContent(), "近 7 天提示");
    assert.equal(await managePage.locator("#recent-panel select").count(), 0);
    const deliverRow = managePage.locator("#recent-list .row:not(.head)").filter({
      has: managePage.getByText("deliver", { exact: true })
    });
    assert.equal(await deliverRow.locator(".number").textContent(), "24", "the browser shows only D through D-6");
    const readCounts = (origin) => fetch(`${apiBase}/api/v1/recent-hints`, {
      method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: '{"action":"read"}'
    });
    const extensionOrigin = `chrome-extension://${new URL(serviceWorker.url()).host}`;
    const summary = await readCounts(extensionOrigin);
    assert.equal(summary.status, 200);
    assert.equal(summary.headers.get("cache-control"), "no-store");
    const payload = await summary.json();
    assert.deepEqual(Object.keys(payload), ["items"]);
    const deliverSummary = payload.items.find(item => item.lemma === "deliver");
    assert.deepEqual(Object.keys(deliverSummary).sort(), ["count", "entryId", "gloss", "lemma"]);
    assert.equal(deliverSummary.count, 24);
    assert.equal((await readCounts("https://untrusted.invalid")).status, 403);
    assertDailyRetention();
    assert.equal(await managePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await managePage.click("#hidden-tab");
    await managePage.getByRole("button", { name: "恢复提示" }).first().click();
    await managePage.waitForFunction(() => /已恢复/.test(document.querySelector("#feedback")?.textContent ?? ""));
  } finally { await popup.close(); }
  await managePage?.close();
  await page.bringToFront();
  await waitForState(page, "ready");
  assert.equal(await overlayText(page), "We need reliable(可靠的) captions.");
  // Clear before the next paint, even though the new request has not started.
  const cleared = await page.evaluate(async () => {
    window.__setCaption("A new unmatched line.", 2.1);
    await new Promise(requestAnimationFrame);
    return { text: document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".line").textContent,
      masked: document.querySelector("#player").classList.contains("lexiflow-inline-active") };
  });
  assert.deepEqual(cleared, { text: "A new unmatched line.", masked: true });
  await waitForState(page, "no-pending");

  // A busy real watch page must not indefinitely postpone caption capture.
  await page.evaluate(() => {
    const noise = document.body.appendChild(document.createElement("div"));
    window.__noiseTimer = setInterval(() => { noise.textContent = String(Date.now()); }, 10);
  });
  await setCaption(page, "The context is reliable.", 2.5);
  await waitForState(page, "ready");
  await page.locator("#ytp-caption-window-container").evaluate((element) => { element.style.display = "none"; });
  await waitForState(page, "idle");
  assert.equal(await overlayText(page), "");
  const emptyBox = await page.locator("#lexiflow-caption-overlay").evaluate((host) => {
    const box = host.shadowRoot.querySelector("span").getBoundingClientRect();
    return { width: box.width, height: box.height };
  });
  assert.deepEqual(emptyBox, { width: 0, height: 0 });
  await page.evaluate(() => clearInterval(window.__noiseTimer));
  await page.locator("#ytp-caption-window-container").evaluate((element) => { element.style.display = ""; });
  await waitForState(page, "ready");
  await page.locator("#player").evaluate((element) => element.classList.add("ad-showing"));
  await waitForState(page, "idle");
  await page.locator("#player").evaluate((element) => element.classList.remove("ad-showing"));
  await waitForState(page, "ready");

  await setCaption(page, "We need reliable captions.", 3);
  await setCaption(page, "zxqv zxqv", 3.1);
  await waitForState(page, "no-pending");
  await delay(2_000);
  assert.equal(await overlayState(page), "no-pending");
  assert.equal(await overlayText(page), "zxqv zxqv");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "zxqv zxqv");

  await setCaption(page, "x".repeat(501), 4);
  await waitForState(page, "idle");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "x".repeat(501));
  assert.equal(await overlayText(page), "");

  await setCaption(page, "", 4.5);
  await waitForState(page, "idle");
  assert.equal(await overlayText(page), "");

  // Seeking the same text must invalidate the previous cue, not just compare strings.
  await setCaption(page, "We need reliable captions.", 4.8);
  await waitForState(page, "ready");
  await page.evaluate(() => document.querySelector("video").dispatchEvent(new Event("seeking", { bubbles: true })));
  await waitForState(page, "idle");
  assert.equal(await overlayText(page), "");
  await page.evaluate(() => document.querySelector("video").dispatchEvent(new Event("seeked", { bubbles: true })));
  await waitForState(page, "ready");

  for (const type of ["ended", "emptied"]) {
    await page.evaluate(type => document.querySelector("video").dispatchEvent(new Event(type, {bubbles:true})), type);
    await waitForState(page, "idle");
    assert.equal(await overlayText(page), "");
    assert.equal(await page.locator("#player").evaluate(el => el.classList.contains("lexiflow-inline-active")), false);
    await page.evaluate(() => document.querySelector("video").dispatchEvent(new Event("play", {bubbles:true})));
    await waitForState(page, "ready");
  }

  await serviceWorker.evaluate(() => {
    globalThis.fetch = (_input, options) =>
      new Promise((_resolve, reject) =>
        options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))
      );
  });
  await setCaption(page, "A reliable result.", 5);
  await waitForState(page, "fallback");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "A reliable result.");
  assert.equal(await overlayText(page), "A reliable result.");

  // Deterministically delay WebCrypto in a separate synthetic browser page. This exercises
  // the real content bundle, including the gap before coordinator.submit, not just stream.ts.
  const hashPage = await context.newPage();
  await hashPage.route("https://fixture.invalid/**", route => route.fulfill({ contentType: "text/html", body: youtubeFixture() }));
  await hashPage.goto("https://fixture.invalid/watch?v=hash-test");
  await hashPage.evaluate(() => {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    crypto.subtle.digest = async (...args) => {
      if (window.__holdDigest) await new Promise(resolve => { window.__releaseDigest = resolve; });
      return digest(...args);
    };
    (window.chrome ??= {}).runtime = { sendMessage: message => {
      if (message.type === "local-preferences") return Promise.resolve({ok:true,commonWordCutoff:1500,hidden:[]});
      if (message.type === "local-presented") return Promise.resolve({ok:true});
      if (message.type === "cancel-caption-hint") return Promise.resolve({ ok: true });
      const segments = message.payload.currentSnapshot.captions.flatMap(group => group.segments);
      const target = segments.find(segment => segment.append && segment.text.includes("reliable"));
      const startOffset = target?.text.indexOf("reliable") ?? -1;
      return Promise.resolve({ ok: true, body: { processedKeys: segments.filter(segment => segment.append).map(segment => segment.key),
        hints: !target ? [] : [{ startKey:target.key,endKey:target.key,lexiconEntryId:"9007199254740993",lemma:"reliable",lexiconVersion:1,senseId:"00000000-0000-0000-0000-000000000002",startOffset, endOffset: startOffset + 8, chineseGloss: "可靠的" }] } });
    } };
  });
  await hashPage.evaluate(await readFile(resolve(extensionPath, "content.js"), "utf8"));
  await setCaption(hashPage, "A reliable result.", 1);
  await waitForState(hashPage, "ready");
  await hashPage.evaluate(() => {
    window.__holdDigest = true;
    document.dispatchEvent(new Event("yt-navigate-start"));
    history.replaceState({}, "", "/watch?v=hash-next");
    window.__setCaption("A reliable method.", 2);
    document.dispatchEvent(new Event("yt-navigate-finish"));
  });
  await hashPage.evaluate(() => new Promise(requestAnimationFrame));
  assert.equal(await overlayText(hashPage), "A reliable method.");
  assert.equal(await hashPage.locator("#player").evaluate(el => el.classList.contains("lexiflow-inline-active")), true);
  // Clear the source while hashing is suspended, then release the obsolete work.
  await hashPage.evaluate(() => { window.__setCaption("", 3); window.__holdDigest = false; window.__releaseDigest(); });
  await delay(250);
  assert.equal(await overlayState(hashPage), "idle");
  assert.equal(await overlayText(hashPage), "");
  await setCaption(hashPage, "A reliable result.", 4);
  await waitForState(hashPage, "ready");
  await hashPage.evaluate(() => document.dispatchEvent(new Event("yt-navigate-start")));
  await waitForState(hashPage, "idle");
  await delay(200);
  assert.equal(await overlayText(hashPage), "");
  await hashPage.evaluate(() => document.dispatchEvent(new Event("yt-navigate-finish")));
  await waitForState(hashPage, "ready");
  // Replacing the player with identical caption text must recreate the detached overlay.
  await hashPage.evaluate(() => {
    const old = document.querySelector("#player");
    const replacement = old.cloneNode(true);
    replacement.querySelector("#lexiflow-caption-overlay").remove();
    replacement.classList.remove("lexiflow-inline-active");
    old.replaceWith(replacement);
  });
  await waitForState(hashPage, "ready");
  assert.equal(await overlayText(hashPage), "A reliable(可靠的) result.");
  await hashPage.close();

  assert.equal(logs.some(line => line.includes("We need reliable") || line.includes("可靠的")), false);
  // Aggregate DOM diagnostics replace per-caption content-script logs.
  assert.ok(diagnostics.counts.requested >= diagnostics.counts.ready);
  process.stdout.write(`${JSON.stringify({ status: "PASS", browser_smoke: 1, api_smoke: 1, reason: "" })}\n`);
} catch (error) {
  const blocked = error instanceof ApiResourceUnavailable;
  process.stdout.write(`${JSON.stringify({ status: blocked ? "BLOCKED" : "FAIL", browser_smoke: 0, api_smoke: 0, reason: blocked ? error.message : "browser-api-smoke-assertion-failed" })}\n`);
  if (!blocked) {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
    process.exitCode = 1;
  }
} finally {
  await context?.close();
  if (api !== undefined) await stopApi(api);
  if (userDataDir !== undefined) await rm(userDataDir, { recursive: true, force: true });
}
