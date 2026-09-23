import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { mkdtemp, rm, readFile, mkdir, cp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright";

const extensionRoot = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(extensionRoot, "..");
const extensionPath = resolve(extensionRoot, "dist");
let apiBase;
let apiPort;

class ApiResourceUnavailable extends Error {}

function assertApiResources() {
  if (!existsSync(resolve(repositoryRoot, "backend/gradlew")) || !existsSync(resolve(repositoryRoot, "scripts/environment/java_exec.py"))) {
    throw new ApiResourceUnavailable("api-launcher-resource-unavailable");
  }
  const java = spawnSync("python3", ["-c", "from pathlib import Path; from scripts.environment.java_runtime import resolve_java_home; import os; resolve_java_home(Path('.'), os.environ)"], {
    cwd: repositoryRoot,
    stdio: "ignore"
  });
  if (java.error || java.status !== 0) throw new ApiResourceUnavailable("api-java-runtime-unavailable");
}

function startApi() {
  const child = spawn("python3", ["-m", "scripts.environment.java_exec", "backend/gradlew", "-p", "backend", "--no-daemon", ":apps:api:bootRun", `--args=--server.address=127.0.0.1 --server.port=${apiPort} --spring.datasource.url=false`], {
    cwd: repositoryRoot,
    env: { ...process.env, SPRING_DATASOURCE_URL: "false" },
    detached: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.once("error", (error) => { child.launchError = error; });
  return child;
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
      if ((await fetch(`${apiBase}/actuator/health`)).ok) return;
    } catch {
      // The process is still starting.
    }
    await delay(1000);
  }
  throw new Error(`API did not become ready:\n${logs}`);
}

function killProcessGroup(pid, signal) {
  try {
    globalThis.process.kill(-pid, signal);
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

async function stop(child) {
  if (child.exitCode !== null) return;
  killProcessGroup(child.pid, "SIGTERM");
  await Promise.race([once(child, "close"), delay(10_000)]);
  if (child.exitCode === null) {
    killProcessGroup(child.pid, "SIGKILL");
    await once(child, "close");
  }
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

async function waitForState(page, state) {
  await page.waitForFunction(
    (expected) => document.querySelector("#lexiflow-caption-overlay")?.getAttribute("data-lexiflow-state") === expected,
    state,
    { timeout: 10_000 }
  );
}

let api;
let userDataDir;
let context;
try {
  assertApiResources();
  apiPort = await freePort();
  apiBase = `http://127.0.0.1:${apiPort}`;
  api = startApi();
  await waitForApi(api);
  userDataDir = await mkdtemp(resolve(tmpdir(), "lexiflow-extension-e2e-"));
  const testExtensionPath = resolve(userDataDir, "extension");
  await cp(extensionPath, testExtensionPath, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve(testExtensionPath, "manifest.json"), "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(resolve(testExtensionPath, "manifest.json"), JSON.stringify(manifest));
  context = await chromium.launchPersistentContext(resolve(userDataDir, "profile"), {
    headless: false,
    args: [`--disable-extensions-except=${testExtensionPath}`, `--load-extension=${testExtensionPath}`]
  });
  const serviceWorker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  await serviceWorker.evaluate((base) => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (_input, options) => originalFetch(`${base}/api/v1/caption-hints`, options);
  }, apiBase);
  const page = await context.newPage();
  const logs = [];
  page.on("console", message => logs.push(message.text()));
  await page.route("https://www.youtube.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: youtubeFixture() })
  );
  await page.goto("https://www.youtube.com/watch?v=lexiflow-e2e", { waitUntil: "domcontentloaded" });

  await setCaption(page, "zxqv zxqv", 1);
  await waitForState(page, "no-pending");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "zxqv zxqv");
  assert.equal(await overlayText(page), "zxqv zxqv");

  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "ready");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "We need reliable captions.");
  assert.match(await overlayText(page), /可靠的/);
  assert.equal(await overlayText(page), "We need reliable(可靠的) captions.");
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
  await mkdir(resolve(repositoryRoot, "tmp/quality"), { recursive: true });
  await page.locator("#player").screenshot({ path: resolve(repositoryRoot, "tmp/quality/inline-caption-preview.png") });
  // Optional extended acceptance remains entirely on the local synthetic fixture.
  // It never visits the real YouTube page or operates a user-owned browser profile.
  if (process.env.LEXIFLOW_EXTENDED_ACCEPTANCE === "1") {
    const artifactRoot = resolve(repositoryRoot, "tmp/quality/extended-acceptance", String(Date.now()));
    const { runVisualAcceptance } = await import("./visual-acceptance.mjs");
    const { runContinuousAcceptance } = await import("./continuous-acceptance.mjs");
    const visual = await runVisualAcceptance({ page, serviceWorker, apiBase, artifactRoot, setCaption, waitForState, overlayText });
    await writeFile(resolve(artifactRoot, "visual-report.json"), JSON.stringify(visual, null, 2));
    const continuous = await runContinuousAcceptance({ page, artifactRoot, setCaption, waitForState, overlayText,
      seconds: Number(process.env.LEXIFLOW_SOAK_SECONDS ?? "375") });
    await writeFile(resolve(repositoryRoot, "tmp/quality/extended-acceptance/latest.json"),
      JSON.stringify({ artifactRoot, status: "PASS", cases: visual.cases.length, processedCues: continuous.processedCues }));
    await setCaption(page, "We need reliable captions.", 2);
    await waitForState(page, "ready");
  }
  const { runExperienceAcceptance } = await import("./experience-acceptance.mjs");
  await runExperienceAcceptance({ page, context, serviceWorker, apiBase, repositoryRoot, setCaption, waitForState, overlayText });
  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "ready");
  // A user choice is local, persists across reload, and can be explicitly reversed.
  await page.locator("#lexiflow-caption-overlay .gloss").click();
  await waitForState(page, "no-pending");
  assert.equal(await overlayText(page), "We need reliable captions.");
  assert.equal(await page.locator("#player").evaluate(el => el.classList.contains("lexiflow-inline-active")), true);
  await page.reload({waitUntil:"domcontentloaded"});
  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "no-pending");
  await page.getByLabel("LexiFlow 设置与诊断").click();
  await page.getByRole("button", {name:"恢复全部提示",exact:true}).click();
  await waitForState(page, "ready");
  assert.equal(await overlayText(page), "We need reliable(可靠的) captions.");
  await page.getByLabel("LexiFlow 设置与诊断").click();
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
      if (message.type === "local-preferences") return Promise.resolve({ok:true,entryKeys:[]});
      if (message.type === "cancel-caption-hint") return Promise.resolve({ ok: true });
      const caption = message.payload.caption;
      const startOffset = caption.indexOf("reliable");
      return Promise.resolve({ ok: true, body: { caption, state: startOffset < 0 ? "NO_PENDING" : "READY",
        hints: startOffset < 0 ? [] : [{ lexiconEntryId:"00000000-0000-0000-0000-000000000001",lexiconVersion:1,senseId:"00000000-0000-0000-0000-000000000002",startOffset, endOffset: startOffset + 8, chineseGloss: "可靠的" }] } });
    } };
  });
  await hashPage.evaluate(await readFile(resolve(extensionPath, "content.js"), "utf8"));
  await setCaption(hashPage, "A reliable result.", 1);
  await waitForState(hashPage, "ready");
  await hashPage.evaluate(() => { window.__holdDigest = true; window.__setCaption("A reliable method.", 2); });
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
  if (api !== undefined) await stop(api);
  if (userDataDir !== undefined) await rm(userDataDir, { recursive: true, force: true });
}
