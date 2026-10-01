import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { lstat, mkdir, readFile, rm, writeFile, link } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { zipSync, unzipSync } from "fflate";
import { readVersion, checkReleaseSource } from "../../ops/release/version.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const extensionRoot = path.resolve(HERE, "..");
export const repositoryRoot = path.resolve(extensionRoot, "..");
export const PACKAGE_FILES = ["manifest.json", "background.js", "content.js", "youtube-bridge.js", "popup.js", "popup.html", "popup.css", "assets/icon-16.png", "assets/icon-32.png", "assets/icon-48.png", "assets/icon-128.png", "assets/logo.svg"];
const ZIP_OPTIONS = { level: 9, mtime: new Date(2020, 0, 1, 0, 0, 0), os: 0 };

function fail(message) { throw new Error(message); }
async function regularNoSymlink(root, relative) {
  const components = relative.split("/");
  let cursor = root;
  for (let i = 0; i < components.length; i += 1) {
    cursor = path.join(cursor, components[i]);
    const stat = await lstat(cursor).catch(() => null);
    if (!stat) fail(`package file missing: ${relative}`);
    if (stat.isSymbolicLink()) fail(`package path contains symlink: ${relative}`);
    if (i < components.length - 1 ? !stat.isDirectory() : !stat.isFile()) fail(`invalid package path: ${relative}`);
  }
  return cursor;
}

export async function createExtensionZip(options = {}) {
  const { root = extensionRoot, sourceCommit = "test-source" } = options;
  const softwareVersion = options.softwareVersion ?? await readVersion();
  const dist = path.join(root, "dist");
  await assertNoSymlinkPath(root);
  await assertNoSymlinkPath(dist);
  for (const directory of [root, dist]) {
    const info = await lstat(directory).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) fail("package root contains symlink or is missing");
  }
  const entries = [];
  for (const name of PACKAGE_FILES) {
    const file = await regularNoSymlink(dist, name);
    const bytes = await readFile(file);
    if (name === "manifest.json") {
      const manifest = JSON.parse(bytes.toString("utf8"));
      if (manifest.version !== softwareVersion) fail("manifest software version mismatch");
      if (JSON.stringify(manifest.permissions) !== JSON.stringify(["storage"])) fail("manifest permissions mismatch");
      if (JSON.stringify(manifest.host_permissions) !== JSON.stringify(["http://127.0.0.1:18080/*"])) fail("manifest host permissions mismatch");
    }
    entries.push([name, bytes]);
  }
  const binaryArchive = zipSync(Object.fromEntries(entries), ZIP_OPTIONS);
  const digest = createHash("sha256").update(binaryArchive).digest("hex");
  const filename = `lexiflow-extension-${softwareVersion}.zip`;
  const descriptor = { softwareVersion, sourceCommit, filename, bytes: binaryArchive.byteLength, sha256: digest };
  return { archive: binaryArchive, descriptor, files: entries.map(([name]) => name), unzip: () => unzipSync(binaryArchive) };
}

async function assertNoSymlinkPath(target) {
  let current = path.parse(target).root;
  for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const info = await lstat(current).catch(() => null);
    if (info?.isSymbolicLink()) fail("release output path contains symlink");
  }
}
export async function publishExtensionArchive(zipBytes, checksum, directory, filename) {
  await assertNoSymlinkPath(directory);
  await mkdir(directory, { recursive: true });
  const zipPath = path.join(directory, filename);
  const sumPath = `${zipPath}.sha256`;
  await assertNoSymlinkPath(zipPath); await assertNoSymlinkPath(sumPath);
  const existingZip = await readExistingRegularFile(zipPath);
  const existingSum = await readExistingRegularFile(sumPath);
  if (existingZip || existingSum) {
    if (existingZip && existingSum && existingZip.equals(zipBytes) && existingSum.toString() === checksum) return;
    fail("different release artifact already exists");
  }
  const nonce = `${process.pid}-${Math.random().toString(16).slice(2)}`;
  const tmpZip = `${zipPath}.${nonce}.tmp`, tmpSum = `${sumPath}.${nonce}.tmp`;
  try {
    await writeFile(tmpZip, zipBytes, { flag: "wx" });
    await writeFile(tmpSum, checksum, { flag: "wx" });
    // Hard-link publication is exclusive: never overwrite a concurrent or existing artifact.
    await link(tmpZip, zipPath);
    try { await link(tmpSum, sumPath); } catch (error) { await rm(zipPath, { force: true }); throw error; }
  } finally { await rm(tmpZip, { force: true }); await rm(tmpSum, { force: true }); }
}

async function readExistingRegularFile(target) {
  let info;
  try { info = await lstat(target); }
  catch (error) { if (error.code === "ENOENT") return null; throw error; }
  if (info.isSymbolicLink() || !info.isFile()) fail("release artifact path is not a regular file");
  return readFile(target);
}

export async function packageRelease({ repoRoot = repositoryRoot } = {}) {
  const source = checkReleaseSource(repoRoot);
  const extension = path.join(repoRoot, "extension");
  execFileSync("npm", ["--prefix", extension, "run", "build"], { cwd: repoRoot, stdio: ["ignore", "ignore", "inherit"], env: { ...process.env, LEXIFLOW_API_PORT: "18080" } });
  const after = checkReleaseSource(repoRoot);
  if (after.sourceCommit !== source.sourceCommit || after.softwareVersion !== source.softwareVersion) fail("release source changed during build");
  const result = await createExtensionZip({ root: extension, ...source });
  await publishExtensionArchive(result.archive, `${result.descriptor.sha256}  ${result.descriptor.filename}\n`, path.join(repoRoot, "tmp/releases", source.softwareVersion, source.sourceCommit), result.descriptor.filename);
  process.stdout.write(`${JSON.stringify(result.descriptor)}\n`);
  return result.descriptor;
}

function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) {
  const args = process.argv.slice(2);
  if (args.length) { process.stderr.write("extension package rejected: unexpected arguments\n"); process.exitCode = 1; }
  else packageRelease().catch(error => { process.stderr.write(`extension package rejected: ${error.message}\n`); process.exitCode = 1; });
}
