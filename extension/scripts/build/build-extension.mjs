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
const testModules = resolve(root, "build/test-modules");
const buildMode = process.argv[2] ?? "";
if (process.argv.length > 3 || !["", "test-modules"].includes(buildMode)) {
  throw new Error("usage: npm run build [-- test-modules]");
}
const includeTestModules = buildMode === "test-modules";
const apiPort = localApiPort(process.env.LEXIFLOW_API_PORT);
let expectedDataset = null;
if (process.env.LEXIFLOW_EXPECTED_DATASET !== undefined) {
  try { expectedDataset = validateExpectedDataset(JSON.parse(process.env.LEXIFLOW_EXPECTED_DATASET)); }
  catch (error) { throw new Error(`LEXIFLOW_EXPECTED_DATASET invalid: ${error.message}`); }
}
const identity = resolveBuildIdentity();
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await rm(testModules, { recursive: true, force: true });
await cp(resolve(root, "assets"), resolve(dist, "assets"), { recursive: true });
const require = createRequire(import.meta.url);
execFileSync(process.execPath, [require.resolve("typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.json"], { cwd: root, stdio: "inherit" });
const entries = [
  ["src/background.ts", "esm"],
  ["src/content.ts", "iife"],
  ["src/youtube-bridge.ts", "iife"],
  ["src/popup.ts", "iife"]
];
const testEntries = ["youtube-source", "caption-snapshot", "caption-source", "stream", "page-lifecycle", "caption-capture", "protocol", "caption-debug", "runtime-status", "diagnostics", "preferences"];
// Runtime stays as independent bundles. Test-only modules share chunks only in the ignored test output.
try {
  for (const format of ["esm", "iife"]) {
    await build({
      absWorkingDir: root,
      entryPoints: entries.filter(([, entryFormat]) => entryFormat === format).map(([entry]) => entry),
      outbase: "src",
      outdir: dist,
      bundle: true,
      format,
      minify: true,
      legalComments: "eof",
      target: "es2022",
      define: {
        __LEXIFLOW_API_PORT__: String(apiPort),
        __LEXIFLOW_EXPECTED_DATASET__: JSON.stringify(JSON.stringify(expectedDataset)),
      },
      logLevel: "info",
    });
  }
  if (includeTestModules) {
    await build({
      absWorkingDir: root,
      entryPoints: testEntries.map((name) => `src/${name}.ts`),
      outbase: "src",
      outdir: testModules,
      entryNames: "[name]",
      chunkNames: "chunks/[name]-[hash]",
      bundle: true,
      splitting: true,
      format: "esm",
      target: "es2022",
      minify: true,
      legalComments: "eof",
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
