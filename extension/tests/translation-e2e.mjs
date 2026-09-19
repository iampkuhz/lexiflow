import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright";

const extensionRoot = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(extensionRoot, "..");
const extensionPath = resolve(extensionRoot, "dist");
const apiBase = "http://127.0.0.1:18080";

function startApi() {
  return spawn("python3", ["scripts/toolchain/java_gradle.py", "--no-daemon", ":apps:api:bootRun", "--args=--server.port=18080"], {
    cwd: repositoryRoot,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
}

async function isApiHealthy() {
  try {
    return (await fetch(`${apiBase}/actuator/health`)).ok;
  } catch {
    return false;
  }
}

async function waitForApi(process) {
  let logs = "";
  process.stdout.on("data", (chunk) => (logs += chunk));
  process.stderr.on("data", (chunk) => (logs += chunk));
  for (let attempt = 0; attempt < 90; attempt += 1) {
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
if (!(await isApiHealthy())) {
  api = startApi();
  await waitForApi(api);
}
const userDataDir = await mkdtemp(resolve(tmpdir(), "lexiflow-extension-e2e-"));
let context;
try {
  context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]
  });
  const page = await context.newPage();
  await page.route("https://www.youtube.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: youtubeFixture() })
  );
  await page.goto("https://www.youtube.com/watch?v=lexiflow-e2e", { waitUntil: "domcontentloaded" });

  await setCaption(page, "hello world", 1);
  await waitForState(page, "no-pending");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "hello world");
  assert.equal(await overlayText(page), "");

  await setCaption(page, "We need reliable captions.", 2);
  await waitForState(page, "ready");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "We need reliable captions.");
  assert.match(await overlayText(page), /可靠的/);

  await setCaption(page, "We need reliable captions.", 3);
  await setCaption(page, "hello world", 3.1);
  await waitForState(page, "no-pending");
  await delay(2_000);
  assert.equal(await overlayState(page), "no-pending");
  assert.equal(await overlayText(page), "");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "hello world");

  await setCaption(page, "x".repeat(501), 4);
  await waitForState(page, "idle");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "x".repeat(501));
  assert.equal(await overlayText(page), "");

  await setCaption(page, "", 4.5);
  await waitForState(page, "idle");
  assert.equal(await overlayText(page), "");

  const serviceWorker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
  await serviceWorker.evaluate(() => {
    globalThis.fetch = (_input, options) =>
      new Promise((_resolve, reject) =>
        options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))
      );
  });
  await setCaption(page, "We need reliable captions.", 5);
  await waitForState(page, "fallback");
  assert.equal(await page.locator(".ytp-caption-segment").textContent(), "We need reliable captions.");
  assert.equal(await overlayText(page), "");

  console.log("PASS extension E2E: simulated subtitle stream, stale-result rejection, bounded input, and API-failure English-only fallback");
} finally {
  await context?.close();
  if (api !== undefined) await stop(api);
  await rm(userDataDir, { recursive: true, force: true });
}
