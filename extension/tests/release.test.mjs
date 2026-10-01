import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile, cp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { createHash } from "node:crypto";
import { PACKAGE_FILES, createExtensionZip, publishExtensionArchive, packageRelease } from "../scripts/release.mjs";
import { readVersion } from "../../ops/release/version.mjs";

const version = await readVersion();
async function fixture() {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), "lexiflow-package-test-")));
  const dist = path.join(root, "dist");
  await mkdir(path.join(dist, "assets"), { recursive: true });
  for (const file of PACKAGE_FILES) {
    const data = file === "manifest.json" ? Buffer.from(JSON.stringify({ version, permissions: ["storage"], host_permissions: ["http://127.0.0.1:18080/*"] })) : Buffer.from(`fixture:${file}`);
    await writeFile(path.join(dist, file), data);
  }
  return { root, dist, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test("build uses the single software version source and emits fixed production permissions", async () => {
  const template = JSON.parse(await readFile(new URL("../manifest.json", import.meta.url)));
  const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url)));
  const lock = JSON.parse(await readFile(new URL("../package-lock.json", import.meta.url)));
  assert.equal(Object.hasOwn(template, "version"), false);
  assert.equal(Object.hasOwn(pkg, "version"), false);
  assert.equal(Object.hasOwn(lock, "version"), false);
  assert.equal(Object.hasOwn(lock.packages[""], "version"), false);
  const generated = JSON.parse(await readFile(new URL("../dist/manifest.json", import.meta.url)));
  assert.equal(generated.version, version);
  assert.deepEqual(generated.host_permissions, ["http://127.0.0.1:18080/*"]);
});

test("ZIP has exact allowlist, stable bytes and matching SHA-256 across timezones", async () => {
  const { root, cleanup } = await fixture();
  try {
    await writeFile(path.join(root, "dist/private.env"), "synthetic excluded data");
    await writeFile(path.join(root, "dist/content.js.map"), "synthetic source map");
    const first = await createExtensionZip({ root, softwareVersion: version });
    const second = await createExtensionZip({ root, softwareVersion: version });
    assert.deepEqual(first.files, PACKAGE_FILES);
    assert.deepEqual(Object.keys(first.unzip()).sort(), [...PACKAGE_FILES].sort());
    assert.deepEqual(first.archive, second.archive);
    assert.equal(first.descriptor.sha256, createHash("sha256").update(first.archive).digest("hex"));
    for (const file of PACKAGE_FILES) assert.deepEqual(Buffer.from(first.unzip()[file]), await readFile(path.join(root, "dist", file)));
    const modulePath = new URL("../scripts/release.mjs", import.meta.url).pathname;
    const script = `import {createExtensionZip} from ${JSON.stringify(new URL(`file://${modulePath}`).href)}; const r=await createExtensionZip({root:${JSON.stringify(root)},softwareVersion:${JSON.stringify(version)}}); process.stdout.write(Buffer.from(r.archive).toString('base64'))`;
    for (const TZ of ["Pacific/Honolulu", "Pacific/Kiritimati"]) {
      const other = execFileSync(process.execPath, ["--input-type=module", "-e", script], { env: { ...process.env, TZ }, encoding: "utf8" });
      assert.deepEqual(Buffer.from(other, "base64"), Buffer.from(first.archive));
    }
  } finally { await cleanup(); }
});

test("rejects missing files, root/path symlinks, bad manifest version and permissions", async () => {
  for (const mutate of [
    async ({ dist }) => rm(path.join(dist, "popup.css")),
    async ({ dist }) => { await rm(path.join(dist, "assets/icon-16.png")); await symlink("icon-32.png", path.join(dist, "assets/icon-16.png")); },
    async ({ root, dist }) => { await rm(path.join(dist, "assets"), { recursive: true }); await symlink(root, path.join(dist, "assets")); },
    async ({ root, dist }) => { await rm(dist, { recursive: true }); await symlink(root, dist); },
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version: "9.9.9", permissions: ["storage"], host_permissions: ["http://127.0.0.1:18080/*"] })),
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version, permissions: ["storage"], host_permissions: ["http://127.0.0.1:9999/*"] })),
    async ({ dist }) => writeFile(path.join(dist, "manifest.json"), JSON.stringify({ version, permissions: ["tabs"], host_permissions: ["http://127.0.0.1:18080/*"] }))
  ]) {
    const { root, dist, cleanup } = await fixture();
    try { await mutate({ root, dist }); await assert.rejects(createExtensionZip({ root, softwareVersion: version })); }
    finally { await cleanup(); }
  }
});

test("rejects dirty Git input and safely reuses only byte-identical output", async () => {
  const repo = await realpath(await mkdtemp(path.join(tmpdir(), "lexiflow-dirty-git-")));
  try {
    await mkdir(path.join(repo, "ops/release"), { recursive: true });
    await writeFile(path.join(repo, "ops/release/version.txt"), `${version}\n`);
    execFileSync("git", ["init", "-q", repo]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "."]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]);
    await writeFile(path.join(repo, "dirty.txt"), "uncommitted");
    await assert.rejects(packageRelease({ repoRoot: repo }), /release input must be clean/u);
    const dirtyScript = `import {checkReleaseSource} from ${JSON.stringify(new URL("../../ops/release/version.mjs", import.meta.url).href)}; checkReleaseSource(${JSON.stringify(repo)})`;
    const dirty = spawnSync(process.execPath, ["--input-type=module", "-e", dirtyScript], { encoding: "utf8" });
    assert.notEqual(dirty.status, 0);
    assert.match(dirty.stderr, /release input must be clean/u);
  } finally { await rm(repo, { recursive: true, force: true }); }

  const { root, cleanup } = await fixture();
  const output = path.join(root, "out");
  try {
    const a = await createExtensionZip({ root, softwareVersion: version, sourceCommit: "abc" });
    const checksum = `${a.descriptor.sha256}  ${a.descriptor.filename}\n`;
    await publishExtensionArchive(a.archive, checksum, output, a.descriptor.filename);
    const zip = path.join(output, a.descriptor.filename);
    const original = await readFile(zip);
    await publishExtensionArchive(a.archive, checksum, output, a.descriptor.filename);
    assert.deepEqual(await readFile(zip), original);
    await writeFile(path.join(root, "dist/popup.html"), "different");
    const changed = await createExtensionZip({ root, softwareVersion: version, sourceCommit: "abc" });
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
    const artifact = await createExtensionZip({ root, softwareVersion: version, sourceCommit: "abc" });
    await assert.rejects(publishExtensionArchive(artifact.archive, `${artifact.descriptor.sha256}  ${artifact.descriptor.filename}\n`, output, artifact.descriptor.filename), /symlink/u);
  } finally { await cleanup(); }
});

test("rejects symlinked source ancestors, non-regular artifact targets, and unknown CLI args", async () => {
  const { root, cleanup } = await fixture();
  try {
    const alias = `${root}-alias`;
    await symlink(root, alias);
    await assert.rejects(createExtensionZip({ root: alias, softwareVersion: version }), /symlink/u);
    await rm(alias);
    const artifact = await createExtensionZip({ root, softwareVersion: version, sourceCommit: "abc" });
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
    const moduleUrl = new URL("../scripts/release.mjs", import.meta.url).href;
    const imported = spawnSync(process.execPath, ["-e", `import(${JSON.stringify(moduleUrl)})`], { encoding: "utf8" });
    assert.equal(imported.status, 0, imported.stderr);
    const args = spawnSync(process.execPath, [new URL("../scripts/release.mjs", import.meta.url).pathname, "--version"], { encoding: "utf8" });
    assert.notEqual(args.status, 0);
    assert.match(args.stderr, /unexpected arguments/u);
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
    await mkdir(path.join(repo, "ops/release"), { recursive: true });
    await cp(new URL("../../ops/release/version.mjs", import.meta.url), path.join(repo, "ops/release/version.mjs"));
    await cp(new URL("../../ops/release/version.txt", import.meta.url), path.join(repo, "ops/release/version.txt"));
    await symlink(path.resolve(path.dirname(new URL("../package.json", import.meta.url).pathname), "node_modules"), path.join(extension, "node_modules"));
    await writeFile(path.join(repo, ".gitignore"), "/tmp/\n/extension/dist/\n/extension/node_modules/\n");
    await mkdir(path.join(extension, "dist"), { recursive: true });
    await writeFile(path.join(extension, "dist/stale.map"), "must disappear");
    execFileSync("git", ["init", "-q", repo]);
    execFileSync("git", ["-C", repo, "add", "."]);
    execFileSync("git", ["-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "clean release fixture"]);
    const cli = path.join(extension, "scripts/release.mjs");
    const result = spawnSync(process.execPath, [cli], { cwd: repo, encoding: "utf8", env: { ...process.env, LEXIFLOW_API_PORT: "19876" } });
    assert.equal(result.status, 0, result.stderr);
    const descriptor = JSON.parse(result.stdout);
    assert.equal(descriptor.softwareVersion, version);
    assert.equal(descriptor.sourceCommit, execFileSync("git", ["-C", repo, "rev-parse", "HEAD"], { encoding: "utf8" }).trim());
    const output = path.join(repo, "tmp/releases", version, descriptor.sourceCommit);
    const archive = await readFile(path.join(output, descriptor.filename));
    assert.equal(archive.byteLength, descriptor.bytes);
    assert.equal(createHash("sha256").update(archive).digest("hex"), descriptor.sha256);
    assert.equal((await readFile(path.join(output, `${descriptor.filename}.sha256`), "utf8")).trim(), `${descriptor.sha256}  ${descriptor.filename}`);
    assert.equal(await readFile(path.join(extension, "dist/stale.map")).then(() => true, () => false), false);
    assert.deepEqual(JSON.parse(await readFile(path.join(extension, "dist/manifest.json"), "utf8")).host_permissions, ["http://127.0.0.1:18080/*"]);
  } finally { await rm(repo, { recursive: true, force: true }); }
});
