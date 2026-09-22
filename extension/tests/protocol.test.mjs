import assert from "node:assert/strict";
import test from "node:test";
import { isCaptionHintRequest, parseHintResponse } from "../dist/protocol.js";

const validRequest = {
  contentId: "00000000-0000-5000-8000-000000000001",
  contentRevision: 1,
  segmentId: "a".repeat(64),
  caption: "We need reliable captions.",
  startOffset: 0,
  endOffset: 26
};

test("accepts only the bounded API request contract", () => {
  assert.equal(isCaptionHintRequest(validRequest), true);
  assert.equal(isCaptionHintRequest({ ...validRequest, caption: "x".repeat(501) }), false);
  assert.equal(isCaptionHintRequest({ ...validRequest, segmentId: "not-a-digest" }), false);
  assert.equal(isCaptionHintRequest({ ...validRequest, endOffset: 27 }), false);
});

test("rejects malformed API output before it reaches the overlay", () => {
  assert.deepEqual(parseHintResponse({ state: "READY", hints: [{ chineseGloss: "可靠的" }] }), {
    state: "READY",
    hints: [{ chineseGloss: "可靠的" }]
  });
  assert.equal(parseHintResponse({ state: "READY", hints: [{ chineseGloss: "" }] }), undefined);
  assert.equal(parseHintResponse({ state: "PENDING", hints: [] }), undefined);
  assert.equal(parseHintResponse({ state: "NO_PENDING", hints: "not-an-array" }), undefined);
});

test("compacts valid dictionary articles without discarding the entire response", () => {
  assert.deepEqual(parseHintResponse({state: "READY", hints: [{chineseGloss: "可靠的, 可信赖的；[法] 可靠的, 确实的"}]}), {
    state: "READY", hints: [{chineseGloss: "可靠的, 可信赖的"}]
  });
  const body = parseHintResponse({state: "READY", hints: [{chineseGloss: "释".repeat(1000)}]});
  assert.equal(body.hints[0].chineseGloss, "释".repeat(60) + "…");
  assert.equal(parseHintResponse({state: "READY", hints: [{chineseGloss: ";"}]}), undefined);
});
