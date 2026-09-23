import { localApiPort } from "./build-config.mjs";
import { readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const apiPort = localApiPort(process.env.LEXIFLOW_API_PORT);
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
execFileSync("npx", ["tsc", "--noEmit", "-p", "tsconfig.json"], { cwd: root, stdio: "inherit" });
for (const [entry, format] of [
  ["src/background.ts", "esm"],
  ["src/content.ts", "iife"],
  ["src/popup.ts", "iife"],
  ["src/caption-source.ts", "esm"],
  ["src/stream.ts", "esm"],
  ["src/protocol.ts", "esm"],
  ["src/diagnostics.ts", "esm"],
  ["src/preferences.ts", "esm"]
]) {
  const outfile = resolve(dist, entry.replace("src/", "").replace(".ts", ".js"));
  execFileSync(
    "npx",
    ["esbuild", entry, "--bundle", `--format=${format}`, "--target=es2022", `--define:__LEXIFLOW_API_PORT__=${apiPort}`, `--outfile=${outfile}`],
    { cwd: root, stdio: "inherit" }
  );
}
for (const name of ["popup.html", "popup.css"]) {
  await writeFile(resolve(dist, name), await readFile(resolve(root, "src", name)));
}
const manifest = JSON.parse(await readFile(resolve(root, "manifest.json"), "utf8"));
manifest.host_permissions = [`http://127.0.0.1:${apiPort}/*`];
await writeFile(resolve(dist, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
