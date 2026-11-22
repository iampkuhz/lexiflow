import assert from "node:assert/strict";
import { createIncrementalFetch } from "./e2e-runtime.mjs";

export async function runCaptionHistoryAcceptance({ page, serviceWorker, waitForState }) {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const setLimit = limit => serviceWorker.evaluate(value => chrome.storage.local.set({ captionLineLimit: value }), limit);
  await serviceWorker.evaluate(factory => {
    globalThis.historyProofFetch = globalThis.fetch; globalThis.historyProofRequests = [];
    globalThis.fetch = (0, eval)(`(${factory})`)(globalThis.historyProofFetch, globalThis.historyProofRequests, 150);
  }, createIncrementalFetch.toString());
  try {
    await setLimit(3);
    await page.evaluate(() => {
      // Speaker-marked pairs exercise history without short same-speaker compaction.
      const video = document.querySelector("video");
      window.__historyCaption = (texts, reset = false) => {
        if (reset) video.dispatchEvent(new Event("seeking", { bubbles: true }));
        const win = document.createElement("div"); win.className = "caption-window";
        win.style.cssText = "overflow:hidden;line-height:37px";
        for (const text of texts) {
          const row = document.createElement("div"); row.className = "caption-visual-line";
          const segment = document.createElement("span"); segment.className = "ytp-caption-segment"; segment.textContent = ">> " + text;
          row.append(segment); win.append(row);
        }
        document.querySelector("#ytp-caption-window-container").replaceChildren(win);
        if (reset) video.dispatchEvent(new Event("seeked", { bubbles: true }));
      };
      window.__historyCaption(["FIRST reliable results", "SECOND we can see it"], true);
    });
    await waitForState(page, "ready");
    const host = page.locator("#lexiflow-caption-overlay"), line = host.locator(".line"), rows = line.locator(":scope > .caption-row");
    const names = () => rows.evaluateAll(items => items.map(row => row.textContent.trim().replace(/^>>\s*/u, "").split(" ")[0]));
    const cue = (...labels) => page.evaluate(lines => window.__historyCaption(lines), labels);
    const waitRows = count => page.waitForFunction(n =>
      document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelectorAll(".line > .caption-row").length === n, count);
    await cue("SECOND we can see it", "THIRD it is here");
    await waitRows(3);
    assert.deepEqual(await names(), ["FIRST", "SECOND", "THIRD"]);
    assert.equal(await rows.first().evaluate(row => row.inert && row.hasAttribute("data-history") && !!row.querySelector(".gloss")), true);
    await waitForState(page, "no-pending");
    const sent = await serviceWorker.evaluate(() => globalThis.historyProofRequests.at(-1));
    const current = sent.currentSnapshot.captions.flatMap(group => group.segments).map(segment => segment.text).join("");
    assert.equal(current, ">> SECOND we can see it >> THIRD it is here");
    await page.waitForFunction(() => !document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".line > :last-child").getAnimations().length);
    const trace = await page.evaluate(async () => {
      const root = document.querySelector("#lexiflow-caption-overlay").shadowRoot;
      const list = () => [...root.querySelectorAll(".line > .caption-row")];
      const [departing, shared] = list(), initialTop = shared.getBoundingClientRect().top, frames = [];
      window.__historyCaption(["THIRD it is here", "FOURTH there it is"]);
      const t = performance.now();
      while (performance.now() - t < 460) {
        await new Promise(requestAnimationFrame);
        frames.push({ retained: list().includes(shared), y: shared.getBoundingClientRect().top,
          ghost: root.querySelector(".outgoing")?.contains(departing) ?? false });
      }
      return { intermediate: frames.filter(f => f.retained && f.ghost && f.y < initialTop-2 && f.y > initialTop-30).length,
        moved: initialTop - frames.at(-1).y, gone: !departing.isConnected };
    });
    assert.ok(trace.intermediate >= 2 && trace.moved > 25 && trace.gone, JSON.stringify(trace));
    assert.deepEqual(await names(), ["SECOND", "THIRD", "FOURTH"]);
    const countBeforeChange = await serviceWorker.evaluate(() => globalThis.historyProofRequests.length);
    await setLimit(4);
    await page.waitForFunction(() => parseFloat(document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".viewport").style.maxHeight) > 135);
    assert.deepEqual(await names(), ["SECOND", "THIRD", "FOURTH"]);
    assert.equal(await serviceWorker.evaluate(() => globalThis.historyProofRequests.length), countBeforeChange);
    await cue("FIFTH we can go", "SIXTH we can see");
    await waitRows(4);
    assert.deepEqual(await names(), ["THIRD", "FOURTH", "FIFTH", "SIXTH"]);
    await setLimit(2);
    await waitRows(2);
    assert.deepEqual(await names(), ["FIFTH", "SIXTH"]);
    assert.equal(await host.locator(".outgoing").count(), 0);
    await setLimit(3);
    await page.waitForFunction(() => parseFloat(document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".viewport").style.maxHeight) > 105);
    await cue("SIXTH we can see", "SEVENTH that is all");
    await waitRows(3);
    await page.evaluate(() => window.__historyCaption(["EIGHTH we are here", "NINTH there we go"], true));
    await waitRows(2);
    assert.deepEqual(await names(), ["EIGHTH", "NINTH"]);
    assert.equal(await line.locator("[data-history]").count(), 0);
  } finally {
    await serviceWorker.evaluate(async () => {
      await chrome.storage.local.remove("captionLineLimit");
      globalThis.fetch = globalThis.historyProofFetch;
      delete globalThis.historyProofFetch; delete globalThis.historyProofRequests;
    });
  }
}
