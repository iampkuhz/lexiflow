import assert from "node:assert/strict";
import { resolve } from "node:path";

const fixture = `<!doctype html>
  <div id="movie_player" class="html5-video-player" style="position:relative;width:700px;height:420px">
    <video></video>
    <div id="ytp-caption-window-container" style="position:absolute;bottom:40px;left:20px;font-size:24px">
      <span class="ytp-caption-segment">A synthetic cue.</span>
    </div>
  </div>`;

export async function runContentContextAcceptance(context, extensionPath) {
  for (const mode of ["missing-runtime", "sync-invalidation", "async-invalidation"]) {
    const page = await context.newPage();
    const uncaught = [];
    page.on("pageerror", error => uncaught.push(String(error)));
    try {
      await page.route("https://lexiflow-context.test/**", route => route.fulfill({ contentType: "text/html", body: fixture }));
      await page.goto("https://lexiflow-context.test/watch?v=LfxE2ETest1");
      await page.evaluate(() => {
        window.__captionRequests = 0;
        window.__setCue = text => {
          document.querySelector(".ytp-caption-segment").textContent = text;
          document.querySelector("video").dispatchEvent(new Event("timeupdate", { bubbles: true }));
        };
        globalThis.chrome.runtime = {
          id: "fixture-extension",
          onMessage: { addListener() {} },
          sendMessage(message) {
            if (message.type === "local-preferences") {
              return Promise.resolve({ ok: true, commonWordCutoff: 1500, hidden: [] });
            }
            if (message.type === "caption-hints") {
              window.__captionRequests++;
              if (window.__invalidate === "sync-invalidation") throw new Error("Extension context invalidated.");
              if (window.__invalidate === "async-invalidation") return Promise.reject(new Error("Extension context invalidated."));
              return Promise.reject(new Error("Temporary receiver unavailable."));
            }
            return Promise.resolve();
          }
        };
      });
      await page.addScriptTag({ path: resolve(extensionPath, "content.js") });
      await page.waitForFunction(() => window.__captionRequests === 1 &&
        document.querySelector("#lexiflow-caption-overlay")?.dataset.lexiflowState === "fallback");
      const clip = page.locator("#ytp-caption-window-container");
      assert.match(await clip.evaluate(node => getComputedStyle(node).clipPath), /inset/);
      await page.evaluate(mode => {
        window.__invalidate = mode;
        if (mode === "missing-runtime") delete globalThis.chrome.runtime;
        window.__setCue("Another synthetic cue.");
      }, mode);
      await page.waitForFunction(() => !document.querySelector("#lexiflow-caption-overlay") &&
        !document.documentElement.hasAttribute("data-lexiflow-caption-owner"));
      assert.equal(await clip.evaluate(node => getComputedStyle(node).clipPath), "none");
      assert.equal(await page.locator(".ytp-caption-segment").textContent(), "Another synthetic cue.");
      const calls = await page.evaluate(async () => {
        window.__setCue("A final synthetic cue.");
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return window.__captionRequests;
      });
      assert.equal(calls, mode === "missing-runtime" ? 1 : 2);
      assert.deepEqual(uncaught, []);
    } finally {
      await page.close();
    }
  }
}
