import assert from "node:assert/strict";
import test from "node:test";
import { localApiPort } from "../scripts/build-config.mjs";

test("local test port is explicit and cannot redirect captions to a remote endpoint", () => {
  assert.equal(localApiPort(undefined),18080);
  assert.equal(localApiPort("18081"),18081);
  for (const value of ["", "0", "01", "65536", "http://remote", "18081/foo", " 18081", "18081.0", "NaN"]) {
    assert.throws(() => localApiPort(value));
  }
});
