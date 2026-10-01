import assert from "node:assert/strict";
import test from "node:test";
import { summarizeContinuousCounts } from "./continuous-acceptance.mjs";

test("continuous coverage excludes preceding acceptance counters", () => {
  const baseline = { requested: 17, shown: 16, no_hint: 1, network: 0 };
  const summary = summarizeContinuousCounts({ counts: { requested: 392, shown: 297, no_hint: 95, network: 0 } }, baseline, 375);
  assert.deepEqual(summary.syntheticHintCoverage, { numerator: 281, denominator: 375 });
  assert.deepEqual(summary.windowCounts, { requested: 375, shown: 281, no_hint: 94, network: 0 });
  assert.deepEqual(summary.baselineCounts, baseline);
  assert.notEqual(summary.baselineCounts, baseline);
});

test("continuous coverage rejects mismatched or impossible window counts", () => {
  assert.throws(() => summarizeContinuousCounts({ counts: { requested: 9, shown: 5 } }, {}, 10));
  assert.throws(() => summarizeContinuousCounts({ counts: { requested: 10, shown: 11 } }, {}, 10));
  assert.throws(() => summarizeContinuousCounts({ counts: { requested: 10, shown: 1 } }, { shown: 2 }, 10));
});
