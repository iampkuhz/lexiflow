import assert from "node:assert/strict";
import test from "node:test";
import { Diagnostics } from "../dist/diagnostics.js";

test("bounded timing window reports lifetime count and window percentiles separately", () => {
  const stats = new Diagnostics();
  for (let i = 1; i <= 300; i++) stats.record({ stage: "query", elapsedMs: i, outcome: "requested" });
  const value = stats.snapshot();
  assert.equal(value.counts.requested, 300);
  assert.deepEqual(value.timings.query, { count: 300, sampleCount: 256, meanMs: 150.5, p50Ms: 172, p95Ms: 288 });
  assert.equal(value.timings.render.p95Ms, null);
  stats.reset();
  assert.equal(stats.snapshot().timings.query.count, 0);
});

test("unknown dimensions and invalid timings cannot introduce raw user data", () => {
  const stats = new Diagnostics();
  for (const elapsedMs of [-1, Infinity, NaN, 60001, "12"]) stats.record({ stage: "query", elapsedMs });
  stats.record({ stage: "private caption", elapsedMs: 1, outcome: "private URL" });
  assert.equal(stats.snapshot().timings.query.count, 0);
  assert.equal(JSON.stringify(stats.snapshot()).includes("private"), false);
  stats.record({ outcome: "timeout" });
  stats.record({ outcome: "cancelled" });
  assert.equal(stats.snapshot().counts.timeout, 1);
  assert.equal(stats.snapshot().counts.cancelled, 1);
});
