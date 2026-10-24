import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { lstat, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { zipSync, unzipSync } from "fflate";
import { resolveBuildIdentity, chromeVersion, checkReleaseSource, assertBuildIdentityMatches } from "../../../ops/release/integrity/source-build-identity.mjs";
import { EXTENSION_PACKAGE_FILES, validateExtensionArchiveEntries, publishExtensionArchive, readStableRegularFile } from './extension-archive-contract.mjs';
import { validateExpectedDataset } from '../build/expected-dataset.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const extensionRoot = path.resolve(HERE, "../..");
export const repositoryRoot = path.resolve(extensionRoot, "..");
export const PACKAGE_FILES = EXTENSION_PACKAGE_FILES;
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
  let { root = extensionRoot, buildIdentity, expectedDataset, apiPort = 18080 } = options;
  if (!buildIdentity) fail("extension build identity required");
  if (![18080, 18081].includes(apiPort)) fail("EXTENSION_API_PORT_INVALID");
  if (apiPort === 18080 && !expectedDataset) fail("expected published dataset binding required");
  if (expectedDataset) {
    try { expectedDataset = validateExpectedDataset(expectedDataset); } catch { fail("EXPECTED_DATASET_INVALID"); }
  }
  const files = apiPort === 18081 && !expectedDataset
    ? PACKAGE_FILES.filter(name => name !== "expected-dataset.json") : PACKAGE_FILES;
  let expectedIdentity;
  try { assertBuildIdentityMatches(buildIdentity, buildIdentity); expectedIdentity = { ...buildIdentity }; }
  catch { fail("BUILD_IDENTITY_MISMATCH"); }
  const { sourceCommit, softwareVersion } = expectedIdentity;
  const dist = path.join(root, "dist");
  await assertNoSymlinkPath(root);
  await assertNoSymlinkPath(dist);
  for (const directory of [root, dist]) {
    const info = await lstat(directory).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) fail("package root contains symlink or is missing");
  }
  const entries = [];
  for (const name of files) {
    const file = await regularNoSymlink(dist, name);
    const bytes = await readFile(file);
    if (name === "manifest.json") {
      const manifest = JSON.parse(bytes.toString("utf8"));
      if (manifest.version !== chromeVersion(softwareVersion) || manifest.version_name !== softwareVersion) fail("manifest software version mismatch");
      if (JSON.stringify(manifest.permissions) !== JSON.stringify(["storage"])) fail("manifest permissions mismatch");
      if (JSON.stringify(manifest.host_permissions) !== JSON.stringify([`http://127.0.0.1:${apiPort}/*`])) fail("manifest host permissions mismatch");
    }
    if (name === "build-identity.json") {
      const identity = JSON.parse(bytes.toString("utf8"));
      try { assertBuildIdentityMatches(identity, expectedIdentity); }
      catch { fail("EXTENSION_IDENTITY_MISMATCH"); }
    }
    if (name === "expected-dataset.json") {
      let actual;
      try { actual = validateExpectedDataset(JSON.parse(bytes.toString("utf8"))); } catch { fail("EXPECTED_DATASET_INVALID"); }
      if (JSON.stringify(actual) !== JSON.stringify(expectedDataset)) fail("EXPECTED_DATASET_MISMATCH");
    }
    entries.push([name, bytes]);
  }
  validateExtensionArchiveEntries(new Map(entries), { buildIdentity: expectedIdentity, expectedDataset, apiPort });
  const binaryArchive = zipSync(Object.fromEntries(entries), ZIP_OPTIONS);
  const digest = createHash("sha256").update(binaryArchive).digest("hex");
  const filename = `lexiflow-extension-${softwareVersion}.zip`;
  const descriptor = { buildIdentity: expectedIdentity, softwareVersion, sourceCommit, filename, bytes: binaryArchive.byteLength, sha256: digest };
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
export { publishExtensionArchive };

export async function packageRelease(options = {}) {
  if (!options || typeof options !== "object" || Array.isArray(options)
      || Object.keys(options).some(key => !["repoRoot", "expectedDataset"].includes(key))) {
    fail("EXTENSION_PACKAGE_MODE_INVALID");
  }
  let { repoRoot = repositoryRoot, expectedDataset } = options;
  const source = checkReleaseSource(repoRoot);
  if (!expectedDataset) fail("expected published dataset binding required");
  try { expectedDataset = validateExpectedDataset(expectedDataset); } catch { fail("EXPECTED_DATASET_INVALID"); }
  const buildIdentity = resolveBuildIdentity(repoRoot);
  const extension = path.join(repoRoot, "extension");
  execFileSync("npm", ["--prefix", extension, "run", "build"], { cwd: repoRoot, stdio: ["ignore", "ignore", "inherit"], env: { ...process.env, LEXIFLOW_API_PORT: "18080", LEXIFLOW_EXPECTED_DATASET: JSON.stringify(expectedDataset) } });
  const after = checkReleaseSource(repoRoot);
  const afterIdentity = resolveBuildIdentity(repoRoot);
  try { assertBuildIdentityMatches(buildIdentity, afterIdentity); }
  catch { fail("release source changed during build"); }
  if (after.sourceCommit !== source.sourceCommit || after.softwareVersion !== source.softwareVersion) fail("release source changed during build");
  const result = await createExtensionZip({ root: extension, buildIdentity, expectedDataset, apiPort: 18080 });
  let packagingIdentity;
  try {
    checkReleaseSource(repoRoot);
    packagingIdentity = resolveBuildIdentity(repoRoot);
    assertBuildIdentityMatches(buildIdentity, packagingIdentity);
  }
  catch { fail("release source changed during packaging"); }
  await publishExtensionArchive(result.archive, `${result.descriptor.sha256}  ${result.descriptor.filename}\n`, path.join(repoRoot, "tmp/releases", source.softwareVersion, source.sourceCommit), result.descriptor.filename);
  process.stdout.write(`${JSON.stringify(result.descriptor)}\n`);
  return result.descriptor;
}

/** Build a CI-only development preview at the fixed 18081 loopback port. */
export async function packageDevelopment(options = {}) {
  if (!options || typeof options !== "object" || Array.isArray(options)
      || Object.keys(options).some(key => key !== "repoRoot")) {
    fail("EXTENSION_PACKAGE_MODE_INVALID");
  }
  const { repoRoot = repositoryRoot } = options;
  const source = checkReleaseSource(repoRoot);
  const buildIdentity = resolveBuildIdentity(repoRoot);
  const extension = path.join(repoRoot, "extension");
  const environment = { ...process.env, LEXIFLOW_API_PORT: "18081" };
  delete environment.LEXIFLOW_EXPECTED_DATASET;
  execFileSync("npm", ["--prefix", extension, "run", "build"], {
    cwd: repoRoot,
    stdio: ["ignore", "ignore", "inherit"],
    env: environment,
  });
  const after = checkReleaseSource(repoRoot);
  try { assertBuildIdentityMatches(buildIdentity, resolveBuildIdentity(repoRoot)); }
  catch { fail("release source changed during development build"); }
  if (after.sourceCommit !== source.sourceCommit || after.softwareVersion !== source.softwareVersion) {
    fail("release source changed during development build");
  }
  const result = await createExtensionZip({ root: extension, buildIdentity, apiPort: 18081 });
  try {
    checkReleaseSource(repoRoot);
    assertBuildIdentityMatches(buildIdentity, resolveBuildIdentity(repoRoot));
  } catch { fail("release source changed during development packaging"); }
  const outputDirectory = path.join(repoRoot, "tmp/ci-preview", source.softwareVersion, source.sourceCommit);
  await publishExtensionArchive(
    result.archive,
    `${result.descriptor.sha256}  ${result.descriptor.filename}\n`,
    outputDirectory,
    result.descriptor.filename,
  );
  process.stdout.write(`${JSON.stringify(result.descriptor)}\n`);
  return result.descriptor;
}

function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === "--development") {
    packageDevelopment().catch(error => { process.stderr.write(`extension package rejected: ${error.message}\n`); process.exitCode = 1; });
  }
  else if (args.length !== 2 || args[0] !== "--expected-dataset" || !path.isAbsolute(args[1])) { process.stderr.write("extension package rejected: expected --expected-dataset <absolute-file> or --development\n"); process.exitCode = 1; }
  else {
    let expectedDataset;
    try { expectedDataset = validateExpectedDataset(JSON.parse(readStableRegularFile(args[1], 16 * 1024).toString("utf8"))); }
    catch (error) { process.stderr.write(`extension package rejected: ${error.message}\n`); process.exitCode = 1; }
    if (expectedDataset) packageRelease({ expectedDataset }).catch(error => { process.stderr.write(`extension package rejected: ${error.message}\n`); process.exitCode = 1; });
  }
}
