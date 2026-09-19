import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
execFileSync("npx", ["tsc", "--noEmit", "-p", "tsconfig.json"], { cwd: root, stdio: "inherit" });
for (const [entry, format] of [
  ["src/background.ts", "esm"],
  ["src/content.ts", "iife"],
  ["src/stream.ts", "esm"],
  ["src/protocol.ts", "esm"]
]) {
  const outfile = resolve(dist, entry.replace("src/", "").replace(".ts", ".js"));
  execFileSync(
    "npx",
    ["esbuild", entry, "--bundle", `--format=${format}`, "--target=es2022", `--outfile=${outfile}`],
    { cwd: root, stdio: "inherit" }
  );
}
await cp(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));
