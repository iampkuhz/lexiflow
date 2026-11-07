import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { EXTENSION_PACKAGE_FILES } from "../scripts/packaging/extension-archive-contract.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtimeJs = ["background.js", "content.js", "popup.js", "youtube-bridge.js"];
async function files(directory, prefix = "") {
  const result = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${item.name}` : item.name;
    if (item.isDirectory()) result.push(...await files(path.join(directory, item.name), relative));
    else result.push(relative);
  }
  return result.sort();
}

test("production build contains only four runtime JavaScript bundles and package inputs", async () => {
  const dist = path.join(root, "dist");
  const names = await files(dist);
  assert.deepEqual(names, EXTENSION_PACKAGE_FILES.filter(name => name !== "expected-dataset.json").sort());
  assert.deepEqual(names.filter(name => name.endsWith(".js")), runtimeJs);
  assert.ok(names.includes("manifest.json"));
  assert.ok(names.includes("build-identity.json"));
  assert.ok(names.includes("popup.html"));
  assert.ok(names.includes("popup.css"));
  assert.ok(names.some(name => name.startsWith("assets/icon-")));
  assert.equal(names.some(name => name.includes("test")), false);
});

test("test-only modules are compiled separately and share ESM chunks", async () => {
  const output = path.join(root, "build/test-modules");
  const names = await files(output);
  for (const name of ["caption-capture.js", "caption-debug.js", "caption-snapshot.js", "caption-source.js", "diagnostics.js", "page-lifecycle.js", "preferences.js", "protocol.js", "runtime-status.js", "stream.js", "youtube-source.js"]) assert.ok(names.includes(name), name);
  assert.ok(names.some(name => name.startsWith("chunks/")));
  assert.equal((await stat(path.join(root, "dist"))).isDirectory(), true);
  const manifest = JSON.parse(await readFile(path.join(root, "dist/manifest.json"), "utf8"));
  assert.deepEqual(manifest.permissions, ["storage"]);
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1:18080/*"]);
});
