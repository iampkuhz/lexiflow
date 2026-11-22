import { readFileSync } from "node:fs";
import { DEVELOPMENT_PORT, LOCAL_TEST_PORT, RELEASE_PORT } from "../../../ops/podman/loopback-ports.mjs";

export const isExtensionPort = port => [RELEASE_PORT, DEVELOPMENT_PORT, LOCAL_TEST_PORT].includes(port);

/** Builds bind only to a named loopback slot; candidates cannot allocate fallback ports. */
export function localApiPort(value) {
  if (value === undefined) return RELEASE_PORT;
  if (!isExtensionPort(Number(value)) || String(Number(value)) !== value) {
    throw new Error("LEXIFLOW_API_PORT must select the release, development or local test slot");
  }
  return Number(value);
}

const formalKey = JSON.parse(readFileSync(new URL("../../manifest.json", import.meta.url))).key;
// Chrome uses the public SPKI, not the package version or the unpacked directory.
const previewKey = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA7HKUVFHJr1cV324EOH8VsJNqOn5hA/nAafXtbrjfxPhRsV284VDCTdQhGvW9CSoPVf25Y1AcKQOpWMfBhU/o2EWA0fOBn7NdaK0VGwYfozAvG6PrqO3RNLkeUPYoL0gyIAexVkOfB/YDTT957IVYdDmHhvtZWGOL6dPNQhgDqn0IZ8yXR2iTu/pCKOeg5cIf74hA5sYc13GeasVWg7xSL2uUWQVlizHVVDDxB4Kvj04kKeOi/pt2L9s+O2t5b6mvPkg8iQhJVLKJQKdwB+XkrsbQawtz4DEcl0CypdQe9C/qephlwivDgb3LGh6MfCm+KPU2Tohkfit+IACFdxKCrQIDAQAB";
export function channelManifest(port) {
  const preview = localApiPort(String(port)) !== RELEASE_PORT;
  return { key: preview ? previewKey : formalKey, name: preview ? "LexiFlow 测试版" : "LexiFlow",
    permissions: ["storage"], host_permissions: ["http://127.0.0.1/*", "http://localhost/*"] };
}
export function matchesChannelManifest(manifest, port) {
  if (!isExtensionPort(port)) return false;
  return Object.entries(channelManifest(port)).every(([key, value]) => JSON.stringify(manifest[key]) === JSON.stringify(value)) &&
    !["optional_permissions", "optional_host_permissions", "externally_connectable"].some(key => Object.hasOwn(manifest, key));
}
