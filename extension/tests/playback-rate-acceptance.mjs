import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { createIncrementalFetch } from "./e2e-runtime.mjs";

export async function runPlaybackRateAcceptance({ context, fixture, serviceWorker, waitForState, reportPath }) {
  const page = await context.newPage();
  const readings = [];
  try {
    await page.route("https://www.youtube.com/**", route => route.fulfill({ contentType: "text/html", body: fixture }));
    await page.goto("https://www.youtube.com/watch?v=LfxE2ESpeed", { waitUntil: "domcontentloaded" });
    await page.bringToFront();
    await page.evaluate(async () => {
      // Use Chrome's real media clock, not the default fixture's synthetic currentTime getter.
      const video = document.querySelector("video");
      delete video.currentTime;
      const sampleRate = 8000, dataSize = sampleRate * 2 * 24;
      const buffer = new ArrayBuffer(44 + dataSize), data = new DataView(buffer);
      const tag = (pos, code) => data.setUint32(pos, code);
      tag(0, 0x52494646); data.setUint32(4, 36 + dataSize, true);
      tag(8, 0x57415645); tag(12, 0x666d7420);
      data.setUint32(16, 16, true); data.setUint16(20, 1, true); data.setUint16(22, 1, true);
      data.setUint32(24, sampleRate, true); data.setUint32(28, sampleRate * 2, true);
      data.setUint16(32, 2, true); data.setUint16(34, 16, true);
      tag(36, 0x64617461); data.setUint32(40, dataSize, true);
      video.src = URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
      video.muted = true; video.loop = true;
      await video.play();
    });
    await serviceWorker.evaluate(factory => {
      globalThis.playbackProofFetch = globalThis.fetch;
      globalThis.fetch = (0, eval)(`(${factory})`)(globalThis.playbackProofFetch, [], 550);
    }, createIncrementalFetch.toString());
    for (const [index, spec] of [{ rate: 1 }, { rate: 1.5 }, { rate: 2 }, { rate: 1, switchTo: 2 }].entries()) {
      await page.evaluate(({ rate, index }) => {
        const player = document.querySelector("#player"), video = player.querySelector("video");
        video.playbackRate = rate;
        video.dispatchEvent(new Event("seeking", { bubbles: true }));
        const window = document.createElement("div");
        window.className = "caption-window";
        window.style.cssText = "height:48px;overflow:hidden";
        for (const text of ["Most of us are here", "We can see it from here", "A reliable result is here " + index]) {
          const row = document.createElement("div");
          row.className = "caption-visual-line"; row.style.cssText = "height:24px;line-height:24px";
          const segment = document.createElement("span");
          segment.className = "ytp-caption-segment"; segment.textContent = text;
          row.append(segment); window.append(row);
        }
        player.querySelector("#ytp-caption-window-container").replaceChildren(window);
        video.dispatchEvent(new Event("seeked", { bubbles: true }));
      }, { ...spec, index });
      await waitForState(page, "no-pending");
      const result = await page.evaluate(async ({ switchTo }) => {
        const video = document.querySelector("video"), native = document.querySelector(".caption-window");
        const root = document.querySelector("#lexiflow-caption-overlay").shadowRoot;
        const viewport = root.querySelector(".viewport");
        const rows = () => [...root.querySelectorAll(".line > .caption-row")];
        const shared = rows()[1];
        const before = video.currentTime;
        // Trigger the next caption at a real media position, then expose it fully in the native window.
        while (video.currentTime - before < .125) await new Promise(requestAnimationFrame);
        native.firstElementChild.remove();
        const start = performance.now(), sourceTime = video.currentTime;
        let frame = 0, firstUpdatedFrame = null, visibleMs = null, hintedAtVisibility = false, preserved = true, switched = false;
        let mediaAdvanceMs = 0, jumpAfterSwitch = 0, lastTop;
        do {
          await new Promise(requestAnimationFrame);
          frame++;
          const elapsed = performance.now() - start;
          const [upper, lower] = rows();
          if (upper === shared) {
            if (firstUpdatedFrame === null) firstUpdatedFrame = frame;
            const top = upper.getBoundingClientRect().top;
            if (switched && lastTop !== undefined) jumpAfterSwitch = Math.max(jumpAfterSwitch, Math.abs(top - lastTop));
            lastTop = top;
            const clip = viewport.getBoundingClientRect();
            // A real English glyph box, not merely the presence of an offscreen DOM node.
            const glyph = lower.querySelector(".row-content > span")?.getBoundingClientRect();
            if (glyph && glyph.height > 0 && glyph.top >= clip.top - 1 && glyph.bottom <= clip.bottom + 1) {
              visibleMs = Math.round(elapsed);
              mediaAdvanceMs = Math.round((video.currentTime - sourceTime) * 1000);
              hintedAtVisibility = !!lower.querySelector(".gloss");
              break;
            }
            if (switchTo && !switched && elapsed >= 65) { video.playbackRate = switchTo; switched = true; }
          } else if (firstUpdatedFrame !== null) preserved = false;
        } while (performance.now() - start < 900);
        return { firstUpdatedFrame, visibleMs, hintedAtVisibility, mediaAdvanceMs, preserved, switched, jumpAfterSwitch };
      }, spec);
      readings.push({ ...spec, ...result });
      await waitForState(page, "ready"); // Chinese may arrive later, provided its cue is still current.
    }
    await writeFile(reportPath, JSON.stringify(readings, null, 2));
    const [normal, faster, double, switched] = readings;
    for (const reading of readings) {
      assert.ok(reading.firstUpdatedFrame !== null && reading.firstUpdatedFrame <= 2, JSON.stringify(reading));
      assert.ok(reading.visibleMs > 0 && reading.mediaAdvanceMs > 0, JSON.stringify(reading));
      assert.equal(reading.hintedAtVisibility, false, "English reveal must not wait for the delayed lookup");
      assert.equal(reading.preserved, true, "the shared row retains its node");
    }
    assert.ok(faster.visibleMs < normal.visibleMs * .82, JSON.stringify(readings));
    assert.ok(double.visibleMs <= 185 && double.visibleMs < normal.visibleMs * .65, JSON.stringify(readings));
    assert.ok(switched.switched && switched.visibleMs < normal.visibleMs * .84 && switched.jumpAfterSwitch < 22, JSON.stringify(readings));
  } finally {
    await page.close();
    await serviceWorker.evaluate(() => {
      if (globalThis.playbackProofFetch) globalThis.fetch = globalThis.playbackProofFetch;
      delete globalThis.playbackProofFetch;
    });
  }
}
