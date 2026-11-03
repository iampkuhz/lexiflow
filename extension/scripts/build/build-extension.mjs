import { localApiPort } from "./extension-build-settings.mjs";
import { resolveBuildIdentity } from "../../../ops/release/integrity/source-build-identity.mjs";
import { readFile, writeFile, mkdir, rm, cp } from "node:fs/promises";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { build, stop } from "esbuild";
import { validateExpectedDataset } from "./expected-dataset.mjs";

const root = resolve(import.meta.dirname, "../..");
const dist = resolve(root, "dist");
const apiPort = localApiPort(process.env.LEXIFLOW_API_PORT);
let expectedDataset = null;
if (process.env.LEXIFLOW_EXPECTED_DATASET !== undefined) {
  try { expectedDataset = validateExpectedDataset(JSON.parse(process.env.LEXIFLOW_EXPECTED_DATASET)); }
  catch (error) { throw new Error(`LEXIFLOW_EXPECTED_DATASET invalid: ${error.message}`); }
}
const identity = resolveBuildIdentity();
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await cp(resolve(root, "assets"), resolve(dist, "assets"), { recursive: true });
const require = createRequire(import.meta.url);
execFileSync(process.execPath, [require.resolve("typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.json"], { cwd: root, stdio: "inherit" });
const entries = [
  ["src/background.ts", "esm"],
  ["src/content.ts", "iife"],
  ["src/youtube-bridge.ts", "iife"],
  ["src/youtube-source.ts", "esm"],
  ["src/caption-snapshot.ts", "esm"],
  ["src/popup.ts", "iife"],
  ["src/caption-source.ts", "esm"],
  ["src/stream.ts", "esm"],
  ["src/page-lifecycle.ts", "esm"],
  ["src/caption-capture.ts", "esm"],
  ["src/protocol.ts", "esm"],
  ["src/caption-debug.ts", "esm"],
  ["src/runtime-status.ts", "esm"],
  ["src/diagnostics.ts", "esm"],
  ["src/preferences.ts", "esm"]
];
// 保留每个独立 bundle；只合并 npm/Node 启动，不启用共享 chunk 或增量结果复用。
try {
  for (const format of ["esm", "iife"]) {
    await build({
      absWorkingDir: root,
      entryPoints: entries.filter(([, entryFormat]) => entryFormat === format).map(([entry]) => entry),
      outbase: "src",
      outdir: dist,
      bundle: true,
      format,
      target: "es2022",
      define: {
        __LEXIFLOW_API_PORT__: String(apiPort),
        __LEXIFLOW_EXPECTED_DATASET__: JSON.stringify(JSON.stringify(expectedDataset)),
      },
      logLevel: "info",
    });
  }
} finally { await stop(); }

for (const name of ["popup.html", "popup.css"]) {
  await writeFile(resolve(dist, name), await readFile(resolve(root, "src", name)));
}
const manifest = JSON.parse(await readFile(resolve(root, "manifest.json"), "utf8"));
manifest.version = identity.chromeVersion;
manifest.version_name = identity.softwareVersion;
await writeFile(resolve(dist, "build-identity.json"), JSON.stringify(identity, null, 2) + "\n");
if (expectedDataset) await writeFile(resolve(dist, "expected-dataset.json"), JSON.stringify(expectedDataset) + "\n");
manifest.host_permissions = [`http://127.0.0.1:${apiPort}/*`];
await writeFile(resolve(dist, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
