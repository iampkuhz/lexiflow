import assert from "node:assert/strict";
import test from "node:test";
import { localApiPort } from "../scripts/build/extension-build-settings.mjs";

test("only the fixed formal port or explicit development port can be selected", () => {
  assert.equal(localApiPort(undefined),18080);
  assert.equal(localApiPort("18080"),18080);
  assert.equal(localApiPort("18081"),18081);
  for (const value of ["", "0", "01", "65536", "18082", "http://remote", "18081/foo", " 18081", "18081.0", "NaN"]) {
    assert.throws(() => localApiPort(value));
  }
});
