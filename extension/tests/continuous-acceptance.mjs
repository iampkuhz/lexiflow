import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

/** Real wall-clock soak of the actual extension + API, using only authored synthetic cues. */
export async function runContinuousAcceptance({ page, artifactRoot, setCaption, waitForState, overlayText, seconds = 375 }) {
  assert.ok(Number.isInteger(seconds) && seconds >= 10 && seconds <= 900, "bounded explicit soak duration");
  await page.getByLabel("LexiFlow 设置与诊断").click();
  await page.getByRole("button", { name: "清空本机统计", exact: true }).click();
  await page.getByLabel("LexiFlow 设置与诊断").click();
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
  assert.equal(diagnostics.counts.requested, cases.length);
  assert.equal(diagnostics.counts.ready, cases.filter(item => item.expected === "ready").length);
  assert.equal(diagnostics.counts["no-pending"], cases.filter(item => item.expected === "no-pending").length);
  assert.equal(diagnostics.counts.shown, diagnostics.counts.ready);
  for (const outcome of ["network", "timeout", "invalid-response", "rejected", "late"]) {
    assert.equal(diagnostics.counts[outcome], 0, `unexpected soak outcome: ${outcome}`);
  }
  for (const [stage, series] of Object.entries(diagnostics.timings)) {
    assert.ok(series.count > 0 && series.p95Ms >= 0, `missing stage: ${stage}`);
    assert.ok(series.sampleCount <= diagnostics.sampleLimit);
    if (series.count > diagnostics.sampleLimit) assert.equal(series.sampleCount, diagnostics.sampleLimit);
  }
  const report = {
    status: "PASS", inputMode: "authored-synthetic-captions-real-extension-and-builtin-api",
    requestedDurationSeconds: seconds, elapsedMs, processedCues: cases.length,
    syntheticHintCoverage: { numerator: diagnostics.counts.shown, denominator: cases.length },
    diagnostics, cases,
    limitations: ["Not a real video or real published dictionary coverage estimate.", "No user browser profile, real captions, or viewing history used."]
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(resolve(artifactRoot, "continuous-report.json"), JSON.stringify(report, null, 2));
  await page.locator("#player").screenshot({ path: resolve(artifactRoot, "continuous-final.png") });
  return report;
}
