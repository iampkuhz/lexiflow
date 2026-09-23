import assert from "node:assert/strict";
import test from "node:test";
import { isCaptionHintRequest, parseHintResponse, inlineParts } from "../dist/protocol.js";

const source = { lexiconEntryId: "00000000-0000-0000-0000-000000000001", lexiconVersion: 1, senseId: "00000000-0000-0000-0000-000000000002" };
const caption = "They reviewed the complex assignments.";
const validRequest = {
  contentId: "00000000-0000-5000-8000-000000000001", contentRevision: 1,
  segmentId: "a".repeat(64), caption, startOffset: 0, endOffset: caption.length
};
const hint = { ...source, startOffset: 26, endOffset: 37, chineseGloss: "任务" };
const response = { caption, state: "READY", hints: [hint] };

test("accepts only the bounded API request contract", () => {
  assert.equal(isCaptionHintRequest(validRequest), true);
  for (const fields of [{ caption: "x".repeat(501) }, { segmentId: "invalid" }, { endOffset: 1000 }]) {
    assert.equal(isCaptionHintRequest({ ...validRequest, ...fields }), false);
  }
});

test("keeps range and caption binding and renders after the target before punctuation", () => {
  assert.deepEqual(parseHintResponse(response, caption), response);
  assert.equal(inlineParts(caption, response.hints).map(part => part.text).join(""),
    "They reviewed the complex assignments(任务).");
  assert.equal(parseHintResponse(response, "another caption"), undefined);
});

test("rejects dictionary lists instead of truncating them into an arbitrary sense", () => {
  for (const chineseGloss of ["", " 任务", "任务，归因", "任务;作业", "任务、作业", "(任务)", "任务\n解释", "释".repeat(25), "assignment", "<任务>"]) {
    assert.equal(parseHintResponse({ ...response, hints: [{ ...hint, chineseGloss }] }), undefined);
  }
});

test("rejects malformed, overlapping, out-of-bounds and state-inconsistent results", () => {
  for (const body of [
    { ...response, caption: undefined }, { ...response, state: "PENDING" },
    { ...response, hints: [] }, { ...response, state: "NO_PENDING" },
    { ...response, hints: [hint, hint] }, { ...response, hints: [{ ...hint, endOffset: 99 }] },
    { ...response, hints: [{ ...hint, startOffset: -1 }] },
    { ...response, hints: [{ ...hint, startOffset: 1.5 }] },
    { ...response, hints: [{ chineseGloss: "任务" }] },
    { caption: "😀a", state: "READY", hints: [{ ...hint, startOffset: 1, endOffset: 2 }] }
  ]) assert.equal(parseHintResponse(body), undefined);
  assert.deepEqual(parseHintResponse({ caption, state: "NO_PENDING", hints: [] }), { caption, state: "NO_PENDING", hints: [] });
});

test("supports ordered multiple spans and escapes by leaving DOM creation to text nodes", () => {
  const body = parseHintResponse({ caption: "Complex tasks!", state: "READY", hints: [
    { ...source, startOffset: 8, endOffset: 13, chineseGloss: "任务" },
    { ...source, startOffset: 0, endOffset: 7, chineseGloss: "复杂的" }
  ] });
  assert.equal(inlineParts(body.caption, body.hints).map(part => part.text).join(""), "Complex(复杂的) tasks(任务)!");
});

test("requires exact published entry/sense references and one version per response", () => {
  for (const fields of [{lexiconEntryId:undefined},{senseId:"guessed"},{lexiconVersion:0},{lexiconVersion:1.5}]) {
    assert.equal(parseHintResponse({...response,hints:[{...hint,...fields}]}),undefined);
  }
  assert.equal(parseHintResponse({caption:"a b",state:"READY",hints:[
    {...source,startOffset:0,endOffset:1,chineseGloss:"甲"},
    {...source,lexiconVersion:2,startOffset:2,endOffset:3,chineseGloss:"乙"}
  ]}),undefined);
  assert.equal(parseHintResponse(response).hints[0].senseId,source.senseId);
});
