import assert from "node:assert/strict";
import test from "node:test";
import { captionLineLimit, fitCaptionHistory } from "../build/test-modules/caption-display.js";

test("display settings accept only the numeric 2–4 range and default to three", () => {
  for (const v of [undefined, null, 1, 5, "3", NaN, {}]) assert.equal(captionLineLimit(v), 3);
  for (const v of [2, 3, 4]) assert.equal(captionLineLimit(v), v);
});
test("retain only contiguous newest rows fitting the leftover visual line budget", () => {
  const old = [1, 2, 1];
  assert.deepEqual(fitCaptionHistory(old, 0, x => x), []);
  assert.deepEqual(fitCaptionHistory(old, 2, x => x), [1]);
  assert.deepEqual(fitCaptionHistory(old, 3, x => x), [2, 1]);
  assert.deepEqual(old, [1, 2, 1]);
});
