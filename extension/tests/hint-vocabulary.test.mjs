import assert from "node:assert/strict";
import test from "node:test";
import { HintVocabulary, isVocabularyRow } from "../build/test-modules/hint-vocabulary.js";

const observation = (overrides = {}) => ({
  entryId: "10", version: 1, segmentKey: "segment-a", occurrence: "a:5",
  displayed: false, english: "reliable", chinese: "可靠的", frequencyRank: 127, ...overrides
});

test("tracks zero and counts actual occurrences, not render calls or preference changes", () => {
  const list = new HintVocabulary();
  const active = new Set(["segment-a", "segment-b"]);
  list.observe(active, [observation()]);
  assert.deepEqual(list.snapshot(), [{ english:"reliable", chinese:"可靠的", frequencyRank:127, hintCount:0 }]);
  list.observe(active, [observation({ displayed:true })]);
  list.observe(active, [observation({ displayed:true }), observation({ displayed:true, segmentKey:"segment-b", occurrence:"b:5" })]);
  list.observe(active, [observation()]);
  list.observe(active, [observation({ displayed:true })]);
  assert.equal(list.snapshot()[0].hintCount, 2);
  list.observe(new Set(["segment-c"]), [observation({ displayed:true, segmentKey:"segment-c", occurrence:"c:5", english:"reliably" })]);
  assert.equal(list.snapshot()[0].hintCount, 3);
});

test("orders by actual count rather than rank, isolates versions, and clears at the session boundary", () => {
  const list = new HintVocabulary();
  const active = new Set(["segment-a"]);
  list.observe(active, [observation(), observation({ entryId:"11", english:"essential", frequencyRank:1 }),
    observation({ entryId:"12", english:"some phrase", frequencyRank:null, occurrence:"a:14", displayed:true }),
    observation({ entryId:"12", english:"some phrase", frequencyRank:null, occurrence:"a:25", displayed:true }),
    observation({ occurrence:"a:5", displayed:true })]);
  assert.deepEqual(list.snapshot().map(({ frequencyRank, hintCount }) => [frequencyRank, hintCount]), [[null,2],[127,1],[1,0]]);
  list.observe(active, [observation({ version:2, frequencyRank:4 })]);
  assert.deepEqual(list.snapshot().map(item=>item.frequencyRank), [4]);
  assert.equal(isVocabularyRow(list.snapshot()[0]), true);
  assert.equal(isVocabularyRow({ ...list.snapshot()[0], hintCount:-1 }), false);
  list.reset();
  assert.deepEqual(list.snapshot(), []);
});
