import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

/** 仅汇总本次连续窗口，保留基线以便核对前置场景不污染覆盖计数。 */
export function summarizeContinuousCounts(diagnostics, baseline, processedCues) {
  const counts = Object.fromEntries(Object.entries(diagnostics.counts).map(([key, value]) => [key, value - (baseline[key] ?? 0)]));
  assert.equal(counts.requested, processedCues);
  assert.ok(Number.isInteger(counts.shown) && counts.shown >= 0 && counts.shown <= processedCues);
  return { baselineCounts: { ...baseline }, windowCounts: counts,
    syntheticHintCoverage: { numerator: counts.shown, denominator: processedCues } };
}

/** Real wall-clock soak of the actual extension + API, using only authored synthetic cues. */
export async function runContinuousAcceptance({ page, artifactRoot, setCaption, waitForState, overlayText, seconds = 375 }) {
  assert.ok(Number.isInteger(seconds) && seconds >= 10 && seconds <= 900, "bounded explicit soak duration");
  // The overlay intentionally has no persistent controls; aggregate diagnostics remain on its host.
  const initialDiagnostics = await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics");
  const baseline = initialDiagnostics ? JSON.parse(initialDiagnostics).counts : {};
  const start = performance.now();
  const cases = [];
  for (let index = 0; performance.now() - start < seconds * 1000; index++) {
    const matched = index % 4 !== 0;
    const caption = matched ? `A reliable result number ${index}.` : `Zxqv plmn ${index}.`;
    const expected = matched ? "ready" : "no-pending";
    const observed = performance.now();
    await setCaption(page, caption, index + 100);
    const immediate = await page.evaluate(async () => {
      await new Promise(requestAnimationFrame);
      return {
        english: document.querySelector(".ytp-caption-segment").textContent,
        overlay: document.querySelector("#lexiflow-caption-overlay").shadowRoot.querySelector(".line").textContent,
        masked: document.querySelector("#player").classList.contains("lexiflow-inline-active")
      };
    });
    assert.equal(immediate.english, caption);
    assert.ok([caption, caption.replace("reliable", "reliable(可靠的)")].includes(immediate.overlay), "current English must appear before next paint without stale text");
    assert.equal(immediate.masked, true);
    await waitForState(page, expected);
    assert.equal(await overlayText(page), matched ? caption.replace("reliable", "reliable(可靠的)") : caption);
    cases.push({ index, expected, terminalMs: Math.round((performance.now() - observed) * 1000) / 1000 });
    const remaining = start + (index + 1) * 1000 - performance.now();
    if (remaining > 0) await delay(remaining);
  }
  const elapsedMs = performance.now() - start;
  const diagnostics = JSON.parse(await page.locator("#lexiflow-caption-overlay").getAttribute("data-lexiflow-diagnostics"));
  assert.ok(elapsedMs >= seconds * 1000);
  assert.ok(cases.length >= Math.floor(seconds / 2), "soak must actually process captions, not merely wait");
  const delta = key => diagnostics.counts[key] - (baseline[key] ?? 0);
  assert.equal(delta("requested"), cases.length);
  assert.equal(delta("ready"), cases.filter(item => item.expected === "ready").length);
  assert.equal(delta("no-pending"), cases.filter(item => item.expected === "no-pending").length);
  assert.equal(delta("shown"), delta("ready"));
  assert.equal(delta("no_hint"), delta("no-pending"));
  for (const outcome of ["network", "timeout", "protocol_mismatch", "backend_unavailable", "rejected", "late_response", "cancelled_before_send", "cancelled_in_flight"]) {
    assert.equal(delta(outcome), 0, `unexpected soak outcome: ${outcome}`);
  }
  for (const [stage, series] of Object.entries(diagnostics.timings)) {
    assert.ok(series.count > 0 && series.p95Ms >= 0, `missing stage: ${stage}`);
    assert.ok(series.sampleCount <= diagnostics.sampleLimit);
    if (series.count > diagnostics.sampleLimit) assert.equal(series.sampleCount, diagnostics.sampleLimit);
  }
  const report = {
    status: "PASS", inputMode: "authored-synthetic-captions-real-extension-and-builtin-api",
    requestedDurationSeconds: seconds, elapsedMs, processedCues: cases.length,
    ...summarizeContinuousCounts(diagnostics, baseline, cases.length),
    diagnostics, cases,
    limitations: ["Not a real video or real published dictionary coverage estimate.", "No user browser profile, real captions, or viewing history used."]
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(resolve(artifactRoot, "continuous-report.json"), JSON.stringify(report, null, 2));
  await page.locator("#player").screenshot({ path: resolve(artifactRoot, "continuous-final.png") });
  return report;
}
