import assert from "node:assert/strict";
import test from "node:test";
import { isCutoff, isHiddenPreference, parsePreferences } from "../build/test-modules/preferences.js";

test("preferences accept only bounded explicit cutoff and stable hidden-entry details", () => {
  assert.equal(isCutoff(1000), true); assert.equal(isCutoff(1500), true); assert.equal(isCutoff(2000), true);
  for (const value of [0, 1001, "1500", null, NaN]) assert.equal(isCutoff(value), false);
  const valid = { commonWordCutoff: 2000, hidden: [{ entryId: "9007199254740993", lemma: "reliable", gloss: "可靠的", frequencyRank: 42 }] };
  assert.deepEqual(parsePreferences(valid), valid);
  assert.equal(isHiddenPreference({ entryId: "not-an-id", lemma: "private caption", gloss: "可靠的" }), false);
});

test("invalid or duplicate preference identities fail closed", () => {
  assert.equal(parsePreferences({ commonWordCutoff: 1200, hidden: [] }), undefined);
  assert.equal(parsePreferences({ commonWordCutoff: 1500, hidden: [{ entryId: "1", lemma: "x", gloss: "g", frequencyRank: null }, { entryId: "1", lemma: "x", gloss: "g", frequencyRank: null }] }), undefined);
  assert.equal(parsePreferences({ commonWordCutoff: 1500, hidden: Array.from({ length: 1001 }, (_, i) => ({ entryId: String(i + 1), lemma: "x", gloss: "g", frequencyRank: null })) }), undefined);
});
