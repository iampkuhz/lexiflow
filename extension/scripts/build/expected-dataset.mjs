const keys = ["releaseId", "preparationId", "ruleId", "sqlVersion", "sha256"];
export function validateExpectedDataset(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) {
    throw new Error("EXPECTED_DATASET_INVALID_FIELDS");
  }
  for (const key of keys.slice(0, 4)) {
    if (typeof value[key] !== "string" || value[key].length < 1 || value[key].length > 128 || /[\u0000-\u001f\u007f]/u.test(value[key])) {
      throw new Error("EXPECTED_DATASET_INVALID_VALUE");
    }
  }
  if (typeof value.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.sha256)) throw new Error("EXPECTED_DATASET_INVALID_SHA256");
  return Object.fromEntries(keys.map(key => [key, value[key]]));
}
