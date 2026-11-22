import assert from "node:assert/strict";
import { createIncrementalFetch } from "./e2e-runtime.mjs";

export async function runRollupAcceptance({ page, serviceWorker, waitForState }) {
  const cases = [
    { name: "plain-to-plain", hints: [false, false, false] },
    { name: "plain-to-hint", hints: [false, false, true], growth: true },
    { name: "hint-to-hint", hints: [true, true, true], growth: true },
    { name: "hint-to-plain", hints: [true, true, false] },
    { name: "hint-arrives-after-roll", hints: [false, false, true], growth: true, delay: 580 },
  ];
  const findings = [];
  await page.emulateMedia({ reducedMotion: "no-preference" });
  try {
    // Keep early replies well inside the moving window even on a busy headless host.
    // The explicit 580 ms case still proves the independent post-roll layout path.
    for (const [index, { name, hints, growth, delay = 75 }] of cases.entries()) {
      await serviceWorker.evaluate(({ factory, delay }) => {
        globalThis.rollProofFetch ??= globalThis.fetch;
        globalThis.fetch = (0, eval)(`(${factory})`)(globalThis.rollProofFetch, [], delay);
      }, { factory: createIncrementalFetch.toString(), delay });
      await page.evaluate(({ hints, index }) => {
        const player = document.getElementById("player");
        const container = document.getElementById("ytp-caption-window-container");
        const video = player.querySelector("video");
        video.dispatchEvent(new Event("seeking", { bubbles: true }));
        const window = document.createElement("div");
        window.className = "caption-window";
        window.style.cssText = "height:48px;overflow:hidden";
        const rows = document.createElement("div");
        rows.style.cssText = "transition:transform 390ms linear;transform:translateY(0)";
        const sample = [
          ["Most of us can see it from here.", "A reliable test is what we need."],
          ["Can all of us take it from here?", "A reliable test is all that we need."],
          ["But we can all take it from here.", "A reliable test is the one we need."]
        ];
        const measure = document.createElement("canvas").getContext("2d");
        measure.font = "500 24px Arial";
        const incoming = sample[2][1] + " " + (index + 1);
        const plainWidth = measure.measureText(incoming).width;
        const hintWidth = measure.measureText(incoming.replace("reliable", "reliable(可靠的)")).width;
        // 场景宽度位于英文与中英混排的测量宽度之间，确保真实换行而不是靠固定像素碰巧触发。
        player.style.width = `${Math.ceil(((plainWidth + hintWidth) / 2 + 12) / .9)}px`;
        hints.forEach((hint, line) => {
          const row = document.createElement("span");
          row.className = "caption-visual-line";
          row.style.cssText = "display:block;height:24px;line-height:24px;white-space:nowrap";
          const segment = document.createElement("span");
          segment.className = "ytp-caption-segment";
          segment.textContent = sample[line][+hint] + " " + (index + 1);
          row.append(segment); rows.append(row);
        });
        window.append(rows); container.replaceChildren(window);
        video.dispatchEvent(new Event("seeked", { bubbles: true }));
      }, { hints, index });
      await waitForState(page, hints[0] || hints[1] ? "ready" : "no-pending");
      // Ready refers to the response, not the layout settling after its gloss wraps.
      await page.waitForFunction(() => {
        const overlay = document.querySelector("#lexiflow-caption-overlay")?.shadowRoot;
        return overlay && !overlay.querySelector(".outgoing") &&
          [...overlay.querySelectorAll(".caption-row")].every(row => !row.getAnimations().length);
      });
      const trace = await page.evaluate(async duration => {
        const host = document.getElementById("lexiflow-caption-overlay"), root = host.shadowRoot;
        const rowNodes = () => Array.from(root.querySelector(".line").querySelectorAll(":scope > .caption-row"));
        const box = node => node?.getBoundingClientRect();
        const source = document.querySelector(".caption-window > div");
        const shared = rowNodes()[1];
        const originalGloss = shared.querySelector(".gloss");
        const container = root.querySelector(".viewport");
        const frames = [];
        const start = performance.now();
        source.style.transform = "translateY(-24px)";
        do {
          const rows = rowNodes(), clip = box(container), ghost = box(root.querySelector(".outgoing .caption-row"));
          const last = rows.at(-1);
          frames.push({
            time: Math.round(performance.now() - start),
            sharedTop: box(shared).top, retained: rows.includes(shared), promoted: rows[0] === shared,
            retainedGloss: !originalGloss || shared.querySelector(".gloss") === originalGloss,
            clipBottom: clip.bottom,
            ghostVisible: ghost ? Math.max(0, Math.min(ghost.bottom, clip.bottom) - Math.max(ghost.top, clip.top)) : 0,
            incomingHeight: box(last).height, incomingHint: rows[0] === shared && !!last.querySelector(".gloss")
          });
          await new Promise(requestAnimationFrame);
        } while (performance.now() - start < duration);
        return frames;
      }, delay + 620);
      const first = trace[0], end = trace.at(-1);
      assert.ok(trace.every(frame => frame.retained && frame.retainedGloss), name + ": shared line and hint retain their nodes");
      assert.ok(end.promoted && end.sharedTop < first.sharedTop - 20, name + ": old second row finishes at the top");
      const seenGhost = trace.filter(frame => frame.ghostVisible > 2);
      assert.ok(seenGhost.length >= 3 && seenGhost.at(-1).ghostVisible < seenGhost[0].ghostVisible - 3,
        name + ": old first row exits progressively");
      assert.equal(end.ghostVisible, 0, name + ": retired row is gone");
      assert.equal(end.incomingHint, hints[2], name + ": rendered incoming glossary");
      assert.ok(trace.every(frame => Math.abs(frame.clipBottom - first.clipBottom) <= 1),
        name + ": player anchor must not move between lookup states");
      const hintFrame = trace.findIndex(frame => frame.incomingHint);
      if (growth) {
        assert.ok(hintFrame > 0 && trace[hintFrame].incomingHeight - trace[hintFrame-1].incomingHeight >= 25,
          name + `: inline gloss must exercise real line wrapping (${trace[hintFrame-1]?.incomingHeight} → ${trace[hintFrame]?.incomingHeight})`);
        assert.ok(Math.abs(trace[hintFrame].sharedTop - trace[hintFrame-1].sharedTop) < 22,
          name + ": wrapping on lookup completion cannot snap the shared row");
        assert.equal(trace[hintFrame].time > seenGhost.at(-1).time, delay > 400,
          name + `: expected lookup timing relative to the moving rows (hint ${trace[hintFrame].time} ms, retiring line until ${seenGhost.at(-1).time} ms)`);
      }
      const nearbySteps = trace.slice(1).flatMap((frame, index) => frame.time - trace[index].time <= 40
        ? [frame.sharedTop - trace[index].sharedTop] : []);
      assert.ok(nearbySteps.every(step => step < 3 && step > -22),
        name + `: shared line should advance without downward bounce or a full-line jump (${JSON.stringify(trace.slice(1).flatMap((frame, i) => {
          const prior = trace[i], delta = frame.sharedTop - prior.sharedTop;
          return frame.time - prior.time <= 40 && (delta >= 3 || delta <= -22)
            ? [{ from: prior.time, to: frame.time, delta, priorHeight: prior.incomingHeight, newHeight: frame.incomingHeight }] : [];
        }))})`);
      findings.push({ name, frames: trace.length, ghostFrames: seenGhost.length,
        glossaryFrame: hintFrame, largestSharedStepPx: Math.round(Math.max(...nearbySteps.map(Math.abs))) });
    }
    return findings;
  } finally {
    await page.evaluate(() => { document.getElementById("player").style.width = ""; });
    await serviceWorker.evaluate(() => {
      if (globalThis.rollProofFetch) globalThis.fetch = globalThis.rollProofFetch;
      delete globalThis.rollProofFetch;
    });
  }
}

/** Player DOM can replace both caption rows together (including an English soft-wrapped block). */
export async function runReplacedRowsAcceptance({ page, serviceWorker, waitForState }) {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const modes = [
    { name: "two fresh rows", hints: [false, false, false, false] },
    { name: "DOM rebuild with a brief gap", hints: [true, false, true, false], gap: true },
    { name: "mixed hints at double speed", hints: [false, true, false, true], rate: 2, delay: 35, preempt: true },
    { name: "one soft-wrapped segment", hints: [false, false, false, false], wrap: true, rate: 2 },
    { name: "reduced motion", hints: [false, false, false, false], reduce: true },
  ];
  for (const spec of modes) {
    if (spec.reduce) await page.emulateMedia({ reducedMotion: "reduce" });
    await serviceWorker.evaluate(({ factory, delay }) => {
      globalThis.blockProofFetch ??= globalThis.fetch;
      globalThis.fetch = (0, eval)(`(${factory})`)(globalThis.blockProofFetch, [], delay);
    }, { factory: createIncrementalFetch.toString(), delay: spec.delay ?? 155 });
    try {
      await page.evaluate(spec => {
        const player = document.getElementById("player"), video = player.querySelector("video");
        const container = player.querySelector("#ytp-caption-window-container");
        video.playbackRate = spec.rate ?? 1;
        video.dispatchEvent(new Event("seeking", { bubbles: true }));
        player.style.width = "";
        const samples = [
          ["FIRST We can all see the work from here.", "FIRST A reliable result makes this test."],
          ["SECOND We can all do it this very day.", "SECOND We can see a reliable test today."],
          ["THIRD We can see it all in a new light.", "THIRD A reliable result is what we need."],
          ["FOURTH We can do it all here this time.", "FOURTH This is one more reliable test."],
        ];
        if (spec.wrap) {
          const context = document.createElement("canvas").getContext("2d"); context.font = "500 24px Arial";
          player.style.width = `${Math.ceil((Math.max(context.measureText(samples[0][0]).width,
            context.measureText(samples[2][0]).width) + 20) / .9)}px`;
        }
        const cue = incoming => {
          const win = document.createElement("div");
          win.className = "caption-window"; win.style.cssText = "overflow:hidden;line-height:37px";
          const add = text => {
            const row = document.createElement("div"); row.className = "caption-visual-line";
            const segment = document.createElement("span"); segment.className = "ytp-caption-segment";
            segment.textContent = text; row.append(segment); win.append(row);
          };
          const group = [incoming ? 2 : 0, incoming ? 3 : 1];
          const captions = incoming === "latest" ?
            ["FIFTH The new line has arrived now.", "SIXTH We have a new line here."] :
            group.map(i => samples[i][Number(spec.hints[i])]);
          if (spec.wrap) add(captions.join(" ")); else captions.forEach(add);
          container.replaceChildren(win);
        };
        window.__replaceRollCaption = cue;
        cue(false);
        video.dispatchEvent(new Event("seeked", { bubbles: true }));
      }, spec);
      await waitForState(page, spec.hints.slice(0, 2).some(Boolean) ? "ready" : "no-pending");
      const metrics = await page.evaluate(async ({ wrap, gap, preempt }) => {
        const root = document.querySelector("#lexiflow-caption-overlay").shadowRoot;
        const line = root.querySelector(".line"), viewport = root.querySelector(".viewport");
        const box = element => element.getBoundingClientRect();
        const rows = () => [...line.querySelectorAll(":scope > .caption-row")];
        const old = rows(), height = box(line).height;
        const glyph = (row, token) => {
          if (!row) return;
          const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            const node = walker.currentNode, index = node.textContent.indexOf(token);
            if (index < 0) continue;
            const range = document.createRange();
            range.setStart(node, index); range.setEnd(node, index + token.length);
            return box(range);
          }
        };
        if (gap) {
          document.querySelector("#ytp-caption-window-container").replaceChildren();
          await new Promise(requestAnimationFrame);
        }
        const t = performance.now();
        window.__replaceRollCaption(true);
        const frames = [];
        while (performance.now() - t < 520) {
          await new Promise(requestAnimationFrame);
          const clip = box(viewport), current = rows(), outgoing = [...root.querySelectorAll(".outgoing .caption-row")];
          const visible = bounds => bounds ? Math.max(0, Math.min(clip.bottom, bounds.bottom) - Math.max(clip.top, bounds.top)) : 0;
          const first = glyph(current[0], "THIRD"), second = glyph(current[wrap ? 0 : 1], "FOURTH");
          const prevSecond = glyph(outgoing[wrap ? 0 : 1], "SECOND");
          frames.push({ ms: performance.now()-t, outgoing: outgoing.length, top: first?.top ?? null,
            oldFirst: visible(glyph(outgoing[0], "FIRST")), oldSecond: visible(prevSecond),
            newFirst: visible(first), newSecond: visible(second),
            noOldNodes: current.every(row => !old.includes(row)), activeRows: current.length,
            hinted: current.some(row => !!row.querySelector(".gloss")) });
        }
        const firstIn = frames.find(x => x.newFirst > 3), secondIn = frames.find(x => x.newSecond > 3);
        const held = frames.filter(x => x.oldFirst < 2 && x.oldSecond > 10 && x.newFirst > 10 && x.newSecond < 2);
        const bothReadable = frames.find(x => x.newFirst > 16 && x.newSecond > 16);
        let interrupt;
        if (preempt) {
          window.__replaceRollCaption(false);
          const interruptedAt = performance.now();
          let interrupted = false;
          while (performance.now() - interruptedAt < 110) {
            await new Promise(requestAnimationFrame);
            if (rows()[0]?.textContent.startsWith("FIRST") && root.querySelector(".outgoing") &&
                rows()[0].getAnimations().length) { interrupted = true; break; }
          }
          const newestAt = performance.now();
          window.__replaceRollCaption("latest");
          let upper, lower, ghosts = 0;
          while (performance.now() - newestAt < 220) {
            await new Promise(requestAnimationFrame);
            const clip = box(viewport);
            const readable = bounds => bounds && Math.min(clip.bottom, bounds.bottom) - Math.max(clip.top, bounds.top) > 16;
            if (upper === undefined && readable(glyph(rows()[0], "FIFTH"))) upper = performance.now() - newestAt;
            if (lower === undefined && readable(glyph(rows()[1], "SIXTH"))) lower = performance.now() - newestAt;
            ghosts = Math.max(ghosts, root.querySelectorAll(".outgoing").length);
          }
          interrupt = { interrupted, upper, lower, ghosts, retired: !root.querySelector(".outgoing") };
        }
        return { initialRows: old.length, height, frames: frames.length, firstIn: firstIn?.ms, secondIn: secondIn?.ms, interrupt,
          heldFrames: held.length, heldMs: (held.at(-1)?.ms ?? 0) - (held[0]?.ms ?? 0),
          bothReadableMs: bothReadable?.ms,
          oldFirstExits: frames.some(x => x.oldFirst > 3 && x.oldFirst < 20),
          mixed: frames.some(x => x.oldSecond > 3 && x.newFirst > 3 && x.newSecond < 2),
          moving: new Set(frames.filter(x => x.newFirst > 3).map(x => Math.round(x.top))).size,
          outgoing: Math.max(...frames.map(x => x.outgoing)), end: frames.at(-1) };
      }, spec);
      const context = `${spec.name}: ${JSON.stringify(metrics)}`;
      assert.equal(metrics.initialRows, spec.wrap ? 1 : 2, context);
      assert.ok(metrics.height > 68, context);
      assert.equal(metrics.end.activeRows, spec.wrap ? 1 : 2, context);
      assert.ok(metrics.end.newFirst > 10 && metrics.end.newSecond > 10 && metrics.end.outgoing === 0, context);
      assert.equal(metrics.end.hinted, spec.hints.slice(2).some(Boolean), context);
      assert.equal(metrics.end.noOldNodes, true, context);
      if (spec.reduce) assert.equal(metrics.outgoing, 0, context);
      else {
        assert.ok(metrics.secondIn > metrics.firstIn + 15, context);
        assert.ok(metrics.mixed && metrics.oldFirstExits && metrics.moving >= 3, context);
        if (spec.rate) {
          assert.ok(metrics.heldFrames >= 3 && metrics.heldMs >= 40, context);
          assert.ok(metrics.firstIn <= 85 && metrics.bothReadableMs <= 185, context);
          if (spec.preempt) {
            assert.equal(metrics.interrupt?.interrupted, true, context);
            assert.ok(metrics.interrupt.upper <= 105 && metrics.interrupt.lower <= 185, context);
            assert.ok(metrics.interrupt.ghosts <= 1 && metrics.interrupt.retired, context);
          }
        }
      }
    } finally {
      await page.evaluate(() => { document.getElementById("player").style.width = ""; document.querySelector("video").playbackRate = 1; });
      await serviceWorker.evaluate(() => {
        globalThis.fetch = globalThis.blockProofFetch;
        delete globalThis.blockProofFetch;
      });
    }
  }
  await page.emulateMedia({ reducedMotion: "no-preference" });
}
