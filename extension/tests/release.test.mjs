import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile, cp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { PACKAGE_FILES, createExtensionZip, publishExtensionArchive, packageRelease, packageDevelopment } from "../scripts/packaging/package-extension-release.mjs";
import { checkExtensionArchive } from "../scripts/checks/check-extension-archive.mjs";
import { zipSync } from "fflate";
import { resolveBuildIdentity, chromeVersion } from "../../ops/release/integrity/source-build-identity.mjs";

const buildIdentity = resolveBuildIdentity();
const version = buildIdentity.softwareVersion;
const expectedDataset = { releaseId: "release-1", preparationId: "prep-1", ruleId: "rules-1", sqlVersion: "4", sha256: "a".repeat(64) };
const extensionRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
async function fixture() {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), "lexiflow-package-test-")));
  const dist = path.join(root, "dist");
  await mkdir(path.join(dist, "assets"), { recursive: true });
  for (const file of PACKAGE_FILES) {
    const data = file === "manifest.json" ? Buffer.from(JSON.stringify({ manifest_version: 3, version: chromeVersion(version), version_name: version, permissions: ["storage"], host_permissions: ["http://127.0.0.1:18080/*"], background: { service_worker: "background.js" }, action: { default_popup: "popup.html" } })) : file === "build-identity.json" ? Buffer.from(JSON.stringify(buildIdentity)) : file === "expected-dataset.json" ? Buffer.from(JSON.stringify(expectedDataset) + "\n") : file === "popup.html" ? Buffer.from('<link href="popup.css"><script src="popup.js"></script>') : Buffer.from(`fixture:${file}`);
    await writeFile(path.join(dist, file), data);
  }
  return { root, dist, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test("build uses the single software version source and emits fixed production permissions", async () => {
  execFileSync("npm", ["run", "build"], { cwd: extensionRoot, stdio: "ignore" });
  const template = JSON.parse(await readFile(new URL("../manifest.json", import.meta.url)));
  const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url)));
  const lock = JSON.parse(await readFile(new URL("../package-lock.json", import.meta.url)));
  assert.equal(Object.hasOwn(template, "version"), false);
  assert.equal(Object.hasOwn(pkg, "version"), false);
  assert.equal(Object.hasOwn(lock, "version"), false);
  assert.equal(Object.hasOwn(lock.packages[""], "version"), false);
  const generated = JSON.parse(await readFile(new URL("../dist/manifest.json", import.meta.url)));
  assert.equal(generated.version, chromeVersion(version));
  assert.equal(generated.version_name, version);
  assert.deepEqual(generated.host_permissions, ["http://127.0.0.1:18080/*"]);
});

test("ZIP has exact allowlist, stable bytes and matching SHA-256 across timezones", async () => {
  const { root, cleanup } = await fixture();
  try {
    await writeFile(path.join(root, "dist/private.env"), "synthetic excluded data");
    await writeFile(path.join(root, "dist/content.js.map"), "synthetic source map");
    const first = await createExtensionZip({ root, buildIdentity, expectedDataset });
    const second = await createExtensionZip({ root, buildIdentity, expectedDataset });
    assert.deepEqual(first.files, PACKAGE_FILES);
    assert.deepEqual(Object.keys(first.unzip()).sort(), [...PACKAGE_FILES].sort());
    assert.deepEqual(JSON.parse(Buffer.from(first.unzip()["expected-dataset.json"]).toString()), expectedDataset);
    assert.deepEqual(first.archive, second.archive);
    assert.equal(first.descriptor.sha256, createHash("sha256").update(first.archive).digest("hex"));
    for (const file of PACKAGE_FILES) assert.deepEqual(Buffer.from(first.unzip()[file]), await readFile(path.join(root, "dist", file)));
    const modulePath = new URL("../scripts/packaging/package-extension-release.mjs", import.meta.url).pathname;
    const script = `import {createExtensionZip} from ${JSON.stringify(new URL(`file://${modulePath}`).href)}; const r=await createExtensionZip({root:${JSON.stringify(root)},buildIdentity:${JSON.stringify(buildIdentity)},expectedDataset:${JSON.stringify(expectedDataset)}}); process.stdout.write(Buffer.from(r.archive).toString('base64'))`;
    for (const TZ of ["Pacific/Honolulu", "Pacific/Kiritimati"]) {
      const other = execFileSync(process.execPath, ["--input-type=module", "-e", script], { env: { ...process.env, TZ }, encoding: "utf8" });
      assert.deepEqual(Buffer.from(other, "base64"), Buffer.from(first.archive));
    }
  } finally { await cleanup(); }
});

test("ZIP packager rejects ports outside the two fixed local slots", async () => {
  const { root, cleanup } = await fixture();
  try {
    for (const apiPort of [0, -1, 18082, 65536, 1.5, "18080"]) {
      await assert.rejects(createExtensionZip({ root, buildIdentity, expectedDataset, apiPort }), { message: "EXTENSION_API_PORT_INVALID" });
    }
    const result = await createExtensionZip({ root, buildIdentity, expectedDataset });
    assert.deepEqual(JSON.parse(Buffer.from(result.unzip()["manifest.json"]).toString()).host_permissions,
      ["http://127.0.0.1:18080/*"]);
  } finally { await cleanup(); }
});

test("rejects missing files, root/path symlinks, bad manifest version and permissions", async () => {
  for (const mutate of [
    async ({ dist }) => rm(path.join(dist, "popup.css")),
    async ({ dist }) => { await rm(path.join(dist, "assets/icon-16.png")); await symlink("icon-32.png", path.join(dist, "assets/icon-16.png")); },
    async ({ root, dist }) => { await rm(path.join(dist, "assets"), { recursive: true }); await symlink(root, path.join(dist, "assets")); },
    async ({ root, dist }) => { await rm(dist, { recursive: true }); await symlink(root, dist); },
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version: "9.9.9", permissions: ["storage"], host_permissions: ["http://127.0.0.1:18080/*"] })),
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version: chromeVersion(version), version_name: version, permissions: ["storage"], host_permissions: ["http://127.0.0.1:9999/*"] })),
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version: chromeVersion(version), version_name: version, permissions: ["tabs"], host_permissions: ["http://127.0.0.1:18080/*"] }))
  ]) {
    const { root, dist, cleanup } = await fixture();
    try { await mutate({ root, dist }); await assert.rejects(createExtensionZip({ root, buildIdentity, expectedDataset })); }
    finally { await cleanup(); }
  }
});

test("requires and verifies the complete embedded extension identity", async () => {
  const { root, dist, cleanup } = await fixture();
  try {
    await assert.rejects(createExtensionZip({ root }), /identity required/u);
    await assert.rejects(createExtensionZip({ root, buildIdentity }), /expected published dataset binding required/u);
    const sourceSha256 = `${buildIdentity.sourceSha256.slice(0, 12)}${buildIdentity.sourceSha256.slice(12) === "0".repeat(52) ? "1".repeat(52) : "0".repeat(52)}`;
    const altered = { ...buildIdentity, sourceSha256 };
    altered.buildId = createHash("sha256").update(JSON.stringify({ baseVersion: altered.baseVersion, sourceCommit: altered.sourceCommit, sourceSha256, dirty: altered.dirty, ...(altered.channel === "snapshot" ? { sourceDate: altered.softwareVersion.split("-SNAPSHOT.")[1].split(".")[0] } : {}) })).digest("hex");
    assert.equal(altered.softwareVersion, buildIdentity.softwareVersion);
    assert.equal(altered.sourceCommit, buildIdentity.sourceCommit);
    await writeFile(path.join(dist, "build-identity.json"), JSON.stringify(altered));
    await assert.rejects(createExtensionZip({ root, buildIdentity, expectedDataset }), { message: "EXTENSION_IDENTITY_MISMATCH" });
  } finally { await cleanup(); }
});

test("formal and development package APIs reject mixed mode options", async () => {
  const { root, cleanup } = await fixture();
  try {
    await assert.rejects(packageRelease({ repoRoot: root, apiPort: 18081 }), { message: "EXTENSION_PACKAGE_MODE_INVALID" });
    await assert.rejects(packageDevelopment({ repoRoot: root, expectedDataset }), { message: "EXTENSION_PACKAGE_MODE_INVALID" });
    await assert.rejects(createExtensionZip({ root, buildIdentity, apiPort: 18080 }), { message: "expected published dataset binding required" });
  } finally { await cleanup(); }
});

test("CI calls only the explicit development preview package, checker and isolated CFT smoke modes", async () => {
  const workflow = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  assert.match(workflow, /package-extension-release\.mjs --development/u);
  assert.match(workflow, /tmp\/ci-preview\/\$\{d\.softwareVersion\}\/\$\{d\.sourceCommit\}/u);
  assert.match(workflow, /check-extension-archive\.mjs --development/u);
  assert.match(workflow, /archive-smoke\.mjs --development/u);
  assert.match(workflow, /actions\/upload-artifact/u);
});

test("rejects dirty Git input and safely reuses only byte-identical output", async () => {
  const repo = await realpath(await mkdtemp(path.join(tmpdir(), "lexiflow-dirty-git-")));
  try {
    await mkdir(path.join(repo, "ops/release/integrity"), { recursive: true });
    await writeFile(path.join(repo, "ops/release/version.txt"), `2.0.0\n`);
    execFileSync("git", ["init", "-q", repo]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "."]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]);
    await writeFile(path.join(repo, "dirty.txt"), "uncommitted");
    await assert.rejects(packageRelease({ repoRoot: repo }), /release input must be clean/u);
    const dirtyScript = `import {checkReleaseSource} from ${JSON.stringify(new URL("../../ops/release/integrity/source-build-identity.mjs", import.meta.url).href)}; checkReleaseSource(${JSON.stringify(repo)})`;
    const dirty = spawnSync(process.execPath, ["--input-type=module", "-e", dirtyScript], { encoding: "utf8" });
    assert.notEqual(dirty.status, 0);
    assert.match(dirty.stderr, /release input must be clean/u);
  } finally { await rm(repo, { recursive: true, force: true }); }

  const { root, cleanup } = await fixture();
  const output = path.join(root, "out");
  try {
    const a = await createExtensionZip({ root, buildIdentity, expectedDataset });
    const checksum = `${a.descriptor.sha256}  ${a.descriptor.filename}\n`;
    await publishExtensionArchive(a.archive, checksum, output, a.descriptor.filename);
    const zip = path.join(output, a.descriptor.filename);
    const original = await readFile(zip);
    await publishExtensionArchive(a.archive, checksum, output, a.descriptor.filename);
    assert.deepEqual(await readFile(zip), original);
    const corruptedChecksum = `${checksum[0] === "0" ? "1" : "0"}${checksum.slice(1)}`;
    assert.notEqual(corruptedChecksum, checksum);
    await assert.rejects(publishExtensionArchive(a.archive, corruptedChecksum, output, a.descriptor.filename), /ARCHIVE_PUBLICATION_INVALID/u);
    await assert.rejects(publishExtensionArchive(a.archive, checksum, output, '../escape.zip'), /ARCHIVE_PUBLICATION_INVALID/u);
    await writeFile(path.join(root, "dist/popup.html"), '<link href="popup.css"><script src="popup.js"></script><!-- different -->');
    const changed = await createExtensionZip({ root, buildIdentity, expectedDataset });
    await assert.rejects(publishExtensionArchive(changed.archive, `${changed.descriptor.sha256}  ${changed.descriptor.filename}\n`, output, changed.descriptor.filename), /different release artifact/u);
    assert.deepEqual(await readFile(zip), original);
    const sum = await readFile(`${zip}.sha256`, "utf8");
    assert.match(sum, new RegExp(`^${a.descriptor.sha256}  ${a.descriptor.filename}\\n$`, "u"));
  } finally { await cleanup(); }
});

test("refuses a symlinked release output directory", async () => {
  const { root, cleanup } = await fixture();
  try {
    const outside = path.join(root, "outside");
    const output = path.join(root, "releases");
    await mkdir(outside);
    await symlink(outside, output);
    const artifact = await createExtensionZip({ root, buildIdentity, expectedDataset });
    await assert.rejects(publishExtensionArchive(artifact.archive, `${artifact.descriptor.sha256}  ${artifact.descriptor.filename}\n`, output, artifact.descriptor.filename), /symlink/u);
  } finally { await cleanup(); }
});

test("archive checker binds sidecar, full current identity, exact entries and local resource references", async () => {
  const { root, cleanup } = await fixture();
  const current = resolveBuildIdentity();
  const output = path.join(root, `lexiflow-extension-${current.softwareVersion}.zip`);
  try {
    const built = await createExtensionZip({ root, buildIdentity: current, expectedDataset });
    await writeFile(output, built.archive);
    await writeFile(`${output}.sha256`, `${built.descriptor.sha256}  ${path.basename(output)}\n`);
    const accepted = await checkExtensionArchive(output, current.sourceCommit);
    assert.deepEqual(accepted.buildIdentity, current);
    assert.equal(accepted.sha256, built.descriptor.sha256);
    await writeFile(`${output}.sha256`, `${"0".repeat(64)}  ${path.basename(output)}\n`);
    await assert.rejects(checkExtensionArchive(output, current.sourceCommit), /CHECKSUM_MISMATCH/u);
    await writeFile(`${output}.sha256`, `${built.descriptor.sha256}  ${path.basename(output)}\n`);
    await assert.rejects(checkExtensionArchive(output, "0".repeat(40)), /EXPECTED_COMMIT_MISMATCH/u);
  } finally { await cleanup(); }
});

test("archive checker rejects extra entries, missing resources and traversal names", async () => {
  const { root, cleanup } = await fixture();
  const current = resolveBuildIdentity();
  const output = path.join(root, `lexiflow-extension-${current.softwareVersion}.zip`);
  try {
    const built = await createExtensionZip({ root, buildIdentity: current, expectedDataset });
    const original = built.unzip();
    for (const entries of [
      { ...original, "private.env": Buffer.from("synthetic secret") },
      { ...original, "popup.html": Buffer.from('<script src="missing.js"></script>') },
      { ...original, "../escape": Buffer.from("x") },
    ]) {
      const archive = zipSync(entries);
      const digest = createHash("sha256").update(archive).digest("hex");
      await writeFile(output, archive);
      await writeFile(`${output}.sha256`, `${digest}  ${path.basename(output)}\n`);
      await assert.rejects(checkExtensionArchive(output, current.sourceCommit));
    }
  } finally { await cleanup(); }
});

test("archive checker requires an explicit preview mode and rejects formal-preview mixing", async () => {
  const { root, dist, cleanup } = await fixture();
  const current = resolveBuildIdentity();
  const output = path.join(root, `lexiflow-extension-${current.softwareVersion}.zip`);
  try {
    const manifestPath = path.join(dist, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.host_permissions = ["http://127.0.0.1:18081/*"];
    await writeFile(manifestPath, JSON.stringify(manifest));
    await rm(path.join(dist, "expected-dataset.json"));
    const preview = await createExtensionZip({ root, buildIdentity: current, apiPort: 18081 });
    await writeFile(output, preview.archive);
    await writeFile(`${output}.sha256`, `${preview.descriptor.sha256}  ${path.basename(output)}\n`);
    const checkedPreview = await checkExtensionArchive(output, current.sourceCommit, root,
      { mode: "development", verifyCurrentIdentity: false });
    assert.equal(checkedPreview.dataset, null);
    assert.equal(checkedPreview.mode, "development");
    await assert.rejects(checkExtensionArchive(output, current.sourceCommit, root,
      { verifyCurrentIdentity: false }), { message: "ARCHIVE_FILE_SET_MISMATCH" });

    manifest.host_permissions = ["http://127.0.0.1:18080/*"];
    await writeFile(manifestPath, JSON.stringify(manifest));
    await writeFile(path.join(dist, "expected-dataset.json"), `${JSON.stringify(expectedDataset)}\n`);
    const formal = await createExtensionZip({ root, buildIdentity: current, expectedDataset });
    const formalPath = path.join(root, "formal", formal.descriptor.filename);
    await mkdir(path.dirname(formalPath));
    await writeFile(formalPath, formal.archive);
    await writeFile(`${formalPath}.sha256`, `${formal.descriptor.sha256}  ${path.basename(formalPath)}\n`);
    const checkedFormal = await checkExtensionArchive(formalPath, current.sourceCommit, root,
      { verifyCurrentIdentity: false });
    assert.deepEqual(checkedFormal.dataset, expectedDataset);
    await assert.rejects(checkExtensionArchive(formalPath, current.sourceCommit, root,
      { mode: "development", verifyCurrentIdentity: false }), { message: "ARCHIVE_MODE_MISMATCH" });
  } finally { await cleanup(); }
});

test("rejects symlinked source ancestors, non-regular artifact targets, and unknown CLI args", async () => {
  const { root, cleanup } = await fixture();
  try {
    const alias = `${root}-alias`;
    await symlink(root, alias);
    await assert.rejects(createExtensionZip({ root: alias, buildIdentity, expectedDataset }), /symlink/u);
    await rm(alias);
    const artifact = await createExtensionZip({ root, buildIdentity, expectedDataset });
    const output = path.join(root, "out");
    await mkdir(output);
    const outside = path.join(root, "external.zip");
    await writeFile(outside, "preserve me");
    await symlink(outside, path.join(output, artifact.descriptor.filename));
    await assert.rejects(publishExtensionArchive(artifact.archive, `${artifact.descriptor.sha256}  ${artifact.descriptor.filename}\n`, output, artifact.descriptor.filename), /symlink/u);
    assert.equal(await readFile(outside, "utf8"), "preserve me");
    await rm(path.join(output, artifact.descriptor.filename));
    await mkdir(path.join(output, artifact.descriptor.filename));
    await assert.rejects(publishExtensionArchive(artifact.archive, `${artifact.descriptor.sha256}  ${artifact.descriptor.filename}\n`, output, artifact.descriptor.filename), /not a regular file/u);
    const moduleUrl = new URL("../scripts/packaging/package-extension-release.mjs", import.meta.url).href;
    const imported = spawnSync(process.execPath, ["-e", `import(${JSON.stringify(moduleUrl)})`], { encoding: "utf8" });
    assert.equal(imported.status, 0, imported.stderr);
    const args = spawnSync(process.execPath, [new URL("../scripts/packaging/package-extension-release.mjs", import.meta.url).pathname, "--version"], { encoding: "utf8" });
    assert.notEqual(args.status, 0);
    assert.match(args.stderr, /expected --expected-dataset/u);
  } finally { await cleanup(); }
});

test("clean release CLI rebuilds stale dist at port 18080 and emits only its JSON descriptor", async () => {
  const repo = await realpath(await mkdtemp(path.join(tmpdir(), "lexiflow-clean-release-")));
  try {
    const extension = path.join(repo, "extension");
    await mkdir(extension);
    for (const name of ["scripts", "src", "assets", "manifest.json", "package.json", "package-lock.json", "tsconfig.json"]) {
      await cp(new URL(`../${name}`, import.meta.url), path.join(extension, name), { recursive: true });
    }
    await mkdir(path.join(repo, "ops/release/integrity"), { recursive: true });
    await cp(new URL("../../ops/release/integrity/source-build-identity.mjs", import.meta.url), path.join(repo, "ops/release/integrity/source-build-identity.mjs"));
    await writeFile(path.join(repo, "ops/release/version.txt"), "2.0.0\n");
    await symlink(path.resolve(path.dirname(new URL("../package.json", import.meta.url).pathname), "node_modules"), path.join(extension, "node_modules"));
    await writeFile(path.join(repo, ".gitignore"), "/tmp/\n/extension/dist/\n/extension/node_modules\n");
    await mkdir(path.join(extension, "dist"), { recursive: true });
    await writeFile(path.join(extension, "dist/stale.map"), "must disappear");
    execFileSync("git", ["init", "-q", repo]);
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "clean release fixture"]);
    const cli = path.join(extension, "scripts/packaging/package-extension-release.mjs");
    await assert.rejects(packageRelease({ repoRoot: repo }), /expected published dataset binding required/u);
    const bindingFile = path.join(repo, "tmp/expected-dataset.json");
    await mkdir(path.dirname(bindingFile), { recursive: true });
    await writeFile(bindingFile, JSON.stringify(expectedDataset));
    const result = spawnSync(process.execPath, [cli, "--expected-dataset", bindingFile], { cwd: repo, encoding: "utf8", env: { ...process.env, LEXIFLOW_API_PORT: "19876" } });
    assert.equal(result.status, 0, result.stderr);
    const descriptor = JSON.parse(result.stdout);
    assert.equal(descriptor.softwareVersion, "2.0.0");
    assert.equal(descriptor.sourceCommit, execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], { encoding: "utf8" }).trim());
    const output = path.join(repo, "tmp/releases", descriptor.softwareVersion, descriptor.sourceCommit);
    const archive = await readFile(path.join(output, descriptor.filename));
    assert.equal(archive.byteLength, descriptor.bytes);
    assert.equal(createHash("sha256").update(archive).digest("hex"), descriptor.sha256);
    assert.equal((await readFile(path.join(output, `${descriptor.filename}.sha256`), "utf8")).trim(), `${descriptor.sha256}  ${descriptor.filename}`);
    assert.equal(await readFile(path.join(extension, "dist/stale.map")).then(() => true, () => false), false);
    assert.deepEqual(JSON.parse(await readFile(path.join(extension, "dist/manifest.json"), "utf8")).host_permissions, ["http://127.0.0.1:18080/*"]);

    const preview = spawnSync(process.execPath, [cli, "--development"], { cwd: repo, encoding: "utf8",
      env: { ...process.env, LEXIFLOW_API_PORT: "18080", LEXIFLOW_EXPECTED_DATASET: JSON.stringify(expectedDataset) } });
    assert.equal(preview.status, 0, preview.stderr);
    const previewDescriptor = JSON.parse(preview.stdout);
    const previewZip = path.join(repo, "tmp/ci-preview", previewDescriptor.softwareVersion, previewDescriptor.sourceCommit, previewDescriptor.filename);
    const previewArchive = await checkExtensionArchive(previewZip, previewDescriptor.sourceCommit, repo, { mode: "development" });
    assert.equal(previewArchive.dataset, null);
    assert.equal(previewArchive.buildIdentity.sourceCommit, previewDescriptor.sourceCommit);
    assert.deepEqual(JSON.parse(await readFile(path.join(extension, "dist/manifest.json"), "utf8")).host_permissions, ["http://127.0.0.1:18081/*"]);
    const formalZip = path.join(output, descriptor.filename);
    await assert.rejects(checkExtensionArchive(previewZip, previewDescriptor.sourceCommit, repo), /ARCHIVE_FILE_SET_MISMATCH/u);
    await assert.rejects(checkExtensionArchive(formalZip, descriptor.sourceCommit, repo, { mode: "development" }), /ARCHIVE_MODE_MISMATCH/u);
  } finally { await rm(repo, { recursive: true, force: true }); }
});


test("explicit development packaging uses 18081 without claiming a published dataset", async () => {
  const { root, dist, cleanup } = await fixture();
  try {
    const manifestPath = path.join(dist, "manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    manifest.host_permissions = ["http://127.0.0.1:18081/*"];
    await writeFile(manifestPath, JSON.stringify(manifest));
    await rm(path.join(dist, "expected-dataset.json"));
    const result = await createExtensionZip({ root, buildIdentity, apiPort: 18081 });
    assert.equal(Object.hasOwn(result.unzip(), "expected-dataset.json"), false);
    const { validateExtensionArchiveEntries } = await import("../scripts/packaging/extension-archive-contract.mjs");
    const entries = new Map(Object.entries(result.unzip()).map(([name, bytes]) => [name, Buffer.from(bytes)]));
    assert.equal(validateExtensionArchiveEntries(entries, { buildIdentity, apiPort: 18081 }).dataset, null);
    assert.throws(() => validateExtensionArchiveEntries(entries, { buildIdentity, apiPort: 18080 }), /ARCHIVE_FILE_SET_MISMATCH/);
    assert.throws(() => validateExtensionArchiveEntries(entries, { buildIdentity, apiPort: 18081, expectedDataset }), /ARCHIVE_FILE_SET_MISMATCH/);
    await assert.rejects(createExtensionZip({ root, buildIdentity }), /expected published dataset binding required/);
  } finally { await cleanup(); }
});
