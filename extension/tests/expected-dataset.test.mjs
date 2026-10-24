import assert from "node:assert/strict";
import test from "node:test";
import { validateExpectedDataset } from "../scripts/build/expected-dataset.mjs";

const valid = { releaseId: "release-1", preparationId: "prep-1", ruleId: "rules-1", sqlVersion: "4", sha256: "a".repeat(64) };
test("expected dataset binding accepts exactly the runtime identity fields", () => {
  assert.deepEqual(validateExpectedDataset(valid), valid);
  for (const value of [
    { ...valid, bytes: 32 },
    { ...valid, sha256: "A".repeat(64) },
    { ...valid, releaseId: "" },
    { ...valid, sqlVersion: "x\ny" },
    Object.fromEntries(Object.entries(valid).filter(([key]) => key !== "ruleId")),
    null
  ]) assert.throws(() => validateExpectedDataset(value));
});
