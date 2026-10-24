import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdir, lstat, readFile, rm, symlink, writeFile, copyFile } from 'node:fs/promises';
import fsPromises from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import { packageManifest } from '../build/package-release-artifacts.mjs';
import { buildManifest, createManifest, serializeManifest } from '../integrity/release-artifact-manifest.mjs';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';
import { assembled as candidateAssembled } from './verified-candidate-fixture.mjs';
import { extensionBytes } from './zip-fixture.mjs';

const repoRoot = process.cwd();
const packageCli = path.join(repoRoot, 'ops/release/build/package-release-artifacts.mjs');
const hash = (value) => createHash('sha256').update(value).digest('hex');
async function filesUnder(root, relative = '') {
  const fs = await import('node:fs/promises'); const files = [];
  for (const name of await fs.readdir(path.join(root, relative))) {
    const child = relative ? `${relative}/${name}` : name; const full = path.join(root, child); const stat = await fs.lstat(full);
    if (stat.isDirectory()) files.push(...await filesUnder(root, child)); else files.push(child);
  }
  return files;
}

// Use the same single-runtime candidate fixture and widen its narrow runtime marker
// into the full on-disk descriptor consumed by manifest packaging.
async function fixture() {
  const f = await candidateAssembled();
  f.artifactRoot = path.join(f.candidateDirectory, 'payload');
  f.descriptor = structuredClone(f.manifest);
  f.descriptorFile = path.join(f.root, 'descriptor.json');
  await writeFile(f.descriptorFile, `${JSON.stringify(f.descriptor, null, 2)}\n`);
  f.licenses = f.descriptor.licenses;
  return f;
}

test('copied manifest fixtures isolate source, artifacts, descriptor objects and cleanup', async () => {
  const first = await fixture(), second = await fixture();
  try {
    const expected = resolveBuildIdentity(second.root);
    const firstIdentity = resolveBuildIdentity(first.root);
    assert.equal(firstIdentity.softwareVersion, expected.softwareVersion);
    assert.deepEqual(firstIdentity, expected, 'private clones preserve the synthetic source identity exactly');
    await writeFile(path.join(first.root, 'ops/release/version.txt'), '3.0.0\n');
    execFileSync('git', ['-C', first.root, 'add', '.']);
    execFileSync('git', ['-C', first.root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'private edit']);
    assert.notEqual(resolveBuildIdentity(first.root).sourceCommit, expected.sourceCommit);
    await writeFile(path.join(first.artifactRoot, 'database/schema.sql'), 'private artifact edit');
    first.descriptor.artifacts[0].sha256 = '0'.repeat(64);
    assert.deepEqual(resolveBuildIdentity(second.root), expected);
    assert.equal(await readFile(path.join(second.artifactRoot, 'database/schema.sql'), 'utf8'), 'synthetic trusted SQL\n');
    assert.deepEqual(JSON.parse(await readFile(second.descriptorFile, 'utf8')), second.descriptor);
    await first.cleanup();
    assert.equal((await buildManifest({ descriptor: second.descriptor, repoRoot: second.root, artifactRoot: second.artifactRoot })).softwareVersion, '2.0.0');
  } finally { await first.cleanup(); await second.cleanup(); }
});

function cliArgs(f, outputDirectory) { return [packageCli, '--source-repo', f.root, '--descriptor-file', f.descriptorFile, '--artifact-root', f.artifactRoot, '--output-directory', outputDirectory]; }

test('assembles stable manifest and checksum through the real CLI from a clean Git fixture', async () => {
  const f = await fixture();
  try {
    const output = path.join(f.root, 'release');
    const first = spawnSync(process.execPath, cliArgs(f, output), { encoding: 'utf8' });
    assert.equal(first.status, 0, first.stderr);
    assert.equal(first.stdout, 'release manifest assembled\n');
    const manifestText = await readFile(path.join(output, 'manifest.json'), 'utf8');
    const manifest = JSON.parse(manifestText);
    assert.equal(manifest.sourceCommit, f.descriptor.sourceCommit);
    assert.deepEqual(manifest.platforms, ['linux/arm64']);
    assert.equal(manifest.artifacts.length, f.descriptor.artifacts.length);
    for (const artifact of f.descriptor.artifacts) assert.deepEqual(await readFile(path.join(output, artifact.path)), await readFile(path.join(f.artifactRoot, artifact.path)));
    assert.deepEqual((await filesUnder(output)).sort(), [...f.descriptor.artifacts.map((item) => item.path), 'manifest.json', 'manifest.json.sha256'].sort());
    assert.equal(await readFile(path.join(output, 'manifest.json.sha256'), 'utf8'), `${hash(manifestText)}  manifest.json\n`);
    const second = await packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: output });
    assert.deepEqual(second, manifest);
    assert.equal(await readFile(path.join(output, 'manifest.json'), 'utf8'), manifestText);
  } finally { await f.cleanup(); }
});

test('rejects schema, source, producer, dataset, digest, role, platform and license drift', async () => {
  const cases = [
    (d) => { delete d.apiContract; },
    (d) => { d.extra = true; },
    (d) => { d.platforms = ['linux/amd64']; },
    (d) => { d.sourceCommit = 'f'.repeat(40); },
    (d) => { d.artifacts.find((a) => a.role === 'extension').metadata.softwareVersion = '9.9.9'; },
    (d) => { d.artifacts.find((a) => a.role === 'dataset').metadata.ruleId = 'other'; },
    (d) => { d.artifacts = d.artifacts.filter((a) => a.role !== 'runtime-image'); },
    (d) => { d.artifacts = d.artifacts.filter((a) => a.role !== 'sql'); },
    (d) => { d.artifacts = d.artifacts.filter((a) => a.role !== 'corresponding-sources'); },
    (d) => { d.licenses = d.licenses.slice(1); },
    (d) => { d.artifacts[0].sha256 = 'z'.repeat(64); },
    (d) => { d.artifacts.find((a) => a.role === 'extension').path = '../escape'; },
    (d) => { d.artifacts[1].path = d.artifacts[0].path.toUpperCase(); },
    (d) => { d.licenses[0].sourceUrl = 'https://user:password@example.invalid/license'; },
    (d) => { d.licenses[0].id = 17; },
    (d) => { d.licenses[0].noticeSha256 = 17; },
    (d) => { d.licenses[0].licenseName = 'bad\nname'; },
    (d) => { d.licenses[0].sourceUrl = 'https://127.0.0.1/license'; },
    (d) => { d.licenses[0].noticePath = 'licenses/missing.txt'; }
  ];
  const f = await fixture();
  try {
    // 每个变体只改独立 descriptor；共享不变 Git/制品基线，拒绝后必须未发布。
    for (const mutate of cases) {
      const descriptor = structuredClone(f.descriptor);
      mutate(descriptor);
      await writeFile(f.descriptorFile, JSON.stringify(descriptor));
      const outputDirectory = path.join(f.root, 'release');
      await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory }));
      await assert.rejects(lstat(outputDirectory), { code: 'ENOENT' });
    }
  } finally { await f.cleanup(); }
});

test('rejects dirty source, artifact tampering and symlinked artifact components without publishing', async () => {
  const f = await fixture();
  try {
    await writeFile(path.join(f.root, 'dirty.txt'), 'dirty');
    await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: path.join(f.root, 'release') }), /release input must be clean/u);
  } finally { await f.cleanup(); }
  const g = await fixture();
  try {
    await writeFile(path.join(g.artifactRoot, 'database/schema.sql'), 'tampered');
    const output = path.join(g.root, 'release');
    await assert.rejects(packageManifest({ repoRoot: g.root, descriptorFile: g.descriptorFile, artifactRoot: g.artifactRoot, outputDirectory: output }), /ARTIFACT_DIGEST_MISMATCH/u);
    assert.equal(await readFile(output).then(() => true, () => false), false);
  } finally { await g.cleanup(); }
  const h = await fixture();
  try {
    const original = path.join(h.artifactRoot, 'database/schema.sql'); await rm(original); await symlink('../descriptor.json', original);
    await assert.rejects(packageManifest({ repoRoot: h.root, descriptorFile: h.descriptorFile, artifactRoot: h.artifactRoot, outputDirectory: path.join(h.root, 'release') }), /ARTIFACT_MISSING_OR_SYMLINK/u);
  } finally { await h.cleanup(); }
});

test('protects output containment, symlink parents, existing content and late publication failure', async () => {
  const f = await fixture();
  try {
    await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: path.join(f.artifactRoot, 'out') }), /OUTPUT_INSIDE_ARTIFACT_ROOT/u);
    const existing = path.join(f.root, 'release'); await mkdir(existing); await writeFile(path.join(existing, 'sentinel'), 'preserve');
    await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: existing }), /OUTPUT_EXISTS_DIFFERENT/u);
    assert.equal(await readFile(path.join(existing, 'sentinel'), 'utf8'), 'preserve');
    const outside = path.join(f.root, 'outside'); await mkdir(outside); await symlink(outside, path.join(f.root, 'alias'));
    await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: path.join(f.root, 'alias', 'release') }), /OUTPUT_PATH_SYMLINK/u);
  } finally { await f.cleanup(); }
});

test('rejects dirty input after descriptor creation and never turns a checksum change into new truth', async () => {
  const f = await fixture();
  try {
    const out = path.join(f.root, 'out');
    const initial = spawnSync(process.execPath, cliArgs(f, out), { encoding: 'utf8' }); assert.equal(initial.status, 0, initial.stderr);
    const before = await readFile(path.join(out, 'manifest.json'));
    await writeFile(path.join(f.artifactRoot, 'database/schema.sql'), 'new content');
    const retry = spawnSync(process.execPath, cliArgs(f, out), { encoding: 'utf8' });
    assert.notEqual(retry.status, 0);
    assert.deepEqual(await readFile(path.join(out, 'manifest.json')), before);
    assert.equal(await readFile(path.join(out, 'manifest.json.sha256'), 'utf8').then((sum) => sum.trim().split(' ')[0]), hash(before));
  } finally { await f.cleanup(); }
});

test('canonical bytes do not depend on descriptor key or array order', async () => {
  const f = await fixture();
  try {
    const regular = serializeManifest(await buildManifest({ repoRoot: f.root, descriptor: f.descriptor, artifactRoot: f.artifactRoot }));
    const reorder = (value) => {
      if (Array.isArray(value)) return value.map(reorder).reverse();
      if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).reverse().map(([key, item]) => [key, reorder(item)]));
      return value;
    };
    const reversed = serializeManifest(await buildManifest({ repoRoot: f.root, descriptor: reorder(f.descriptor), artifactRoot: f.artifactRoot }));
    assert.equal(reversed, regular);
  } finally { await f.cleanup(); }
});

test('pure createManifest requires one licensed ARM64 runtime image and corresponding sources', async () => {
  const f = await fixture();
  try {
    const source = f.descriptor.buildIdentity;
    const manifest = createManifest(f.descriptor, source);
    assert.equal(manifest.artifacts.filter((item) => item.role === 'runtime-image').length, 1);
    assert.equal(manifest.artifacts.find((item) => item.role === 'runtime-image').path, 'images/linux-arm64/runtime-image.tar');
    assert.equal(manifest.artifacts.filter((item) => item.role === 'corresponding-sources').length, 1);
    const missing = structuredClone(f.descriptor);
    missing.artifacts = missing.artifacts.filter((item) => item.role !== 'runtime-image');
    assert.throws(() => createManifest(missing, source), /PLATFORM_ARTIFACT_MISSING/u);
    const unlicensed = structuredClone(f.descriptor);
    unlicensed.artifacts.find((item) => item.role === 'runtime-image').licenseIds = ['dataset-license'];
    assert.throws(() => createManifest(unlicensed, source), /REQUIRED_LICENSE_MISSING/u);
    const withMetadata = structuredClone(f.descriptor);
    withMetadata.artifacts.find((item) => item.role === 'runtime-image').metadata = {};
    assert.throws(() => createManifest(withMetadata, source), /ARTIFACTS_INVALID/u);
  } finally { await f.cleanup(); }
});

test('buildManifest never bypasses on-disk artifact verification without a root', async () => {
  const f = await fixture();
  try {
    for (const artifactRoot of [undefined, '', null]) {
      await assert.rejects(buildManifest({ repoRoot: f.root, descriptor: f.descriptor, artifactRoot }), /INVALID_ARTIFACT_ROOT/u);
    }
    const missingRoot = path.join(f.root, 'not-an-artifact-root');
    await assert.rejects(buildManifest({ repoRoot: f.root, descriptor: f.descriptor, artifactRoot: missingRoot }), /INVALID_ARTIFACT_ROOT/u);
  } finally { await f.cleanup(); }
});

test('streams and packages a large synthetic artifact without changing its declared digest', async () => {
  const f = await fixture();
  try {
    const artifact = f.descriptor.artifacts.find((item) => item.role === 'runtime-image' && item.platform === 'linux/arm64');
    const bytes = Buffer.alloc(8 * 1024 * 1024, 0x5a);
    await writeFile(path.join(f.artifactRoot, artifact.path), bytes);
    artifact.bytes = bytes.length; artifact.sha256 = hash(bytes);
    await writeFile(f.descriptorFile, JSON.stringify(f.descriptor));
    const output = path.join(f.root, 'release');
    await packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: output }).catch((error) => { throw error; });
    assert.deepEqual(await readFile(path.join(output, artifact.path)), bytes);
  } finally { await f.cleanup(); }
});

test('idempotency rejects published artifact tampering, symlink replacement and extra files', async () => {
  for (const mode of ['tamper', 'symlink', 'extra', 'empty-directory']) {
    const f = await fixture();
    try {
      const output = path.join(f.root, 'release');
      const first = spawnSync(process.execPath, cliArgs(f, output), { encoding: 'utf8' }); assert.equal(first.status, 0, first.stderr);
      const target = path.join(output, f.descriptor.artifacts.find((item) => item.role === 'sql').path);
      if (mode === 'tamper') await writeFile(target, 'changed published file');
      if (mode === 'symlink') { const replacement = `${target}.real`; await (await import('node:fs/promises')).rename(target, replacement); await symlink(replacement, target); }
      if (mode === 'empty-directory') await mkdir(path.join(output, 'unexpected-empty'));
      if (mode === 'extra') await writeFile(path.join(output, 'unexpected.txt'), 'extra');
      const result = spawnSync(process.execPath, cliArgs(f, output), { encoding: 'utf8' });
      assert.notEqual(result.status, 0, mode);
      assert.equal(result.stdout, ''); assert.equal(result.stderr, 'release manifest rejected\n');
    } finally { await f.cleanup(); }
  }
});


test('single ARM64 runtime image requires each component license association', async () => {
  const f = await fixture();
  try {
    for (const missingId of ['api-runtime', 'lexiflow', 'postgresql']) {
      const descriptor = structuredClone(f.descriptor);
      const artifact = descriptor.artifacts.find((item) => item.role === 'runtime-image');
      artifact.licenseIds = artifact.licenseIds.filter((id) => id !== missingId);
      if (!artifact.licenseIds.length) artifact.licenseIds = ['dataset-license'];
      await assert.rejects(buildManifest({ repoRoot: f.root, descriptor, artifactRoot: f.artifactRoot }), /REQUIRED_LICENSE_MISSING/u);
    }
  } finally { await f.cleanup(); }
});

test('source drift during staging rejects publication and removes only its temporary directory', async (t) => {
  const f = await fixture();
  let changed = false;
  const actualMkdir = fsPromises.mkdir;
  // 在真实临时目录建立之后注入源码变动，避免依赖 watcher 配额和调度时序。
  t.mock.method(fsPromises, 'mkdir', async (target, options) => {
    const result = await actualMkdir(target, options);
    if (!changed && path.basename(target).startsWith('.release.manifest-')) {
      changed = true;
      await writeFile(path.join(f.root, 'ops/release/version.txt'), '2.0.1\n');
    }
    return result;
  });
  syncBuiltinESMExports();
  try {
    await assert.rejects(packageManifest({ repoRoot: f.root, descriptorFile: f.descriptorFile, artifactRoot: f.artifactRoot, outputDirectory: path.join(f.root, 'release') }), /RELEASE_SOURCE_CHANGED/u);
    assert.equal(changed, true);
    const { readdir, lstat } = await import('node:fs/promises');
    await assert.rejects(lstat(path.join(f.root, 'release')), { code: 'ENOENT' });
    assert.equal((await readdir(f.root)).some((name) => name.startsWith('.release.manifest-')), false);
    assert.equal(await readFile(path.join(f.root, 'ops/release/version.txt'), 'utf8'), '2.0.1\n');
  } finally { t.mock.restoreAll(); syncBuiltinESMExports(); await f.cleanup(); }
});

test('manifest rejects incomplete identity and embedded ZIP drift despite refreshed artifact checksum', async () => {
  const f = await fixture();
  try {
    assert.deepEqual((await buildManifest({ repoRoot: f.root, descriptor: f.descriptor, artifactRoot: f.artifactRoot })).buildIdentity, resolveBuildIdentity(f.root));
    const absent = structuredClone(f.descriptor); delete absent.buildIdentity;
    assert.throws(() => createManifest(absent, resolveBuildIdentity(f.root)), /DESCRIPTOR_SCHEMA/);
    const forged = structuredClone(f.descriptor); forged.buildIdentity.buildId = '0'.repeat(64);
    assert.throws(() => createManifest(forged, resolveBuildIdentity(f.root)), /SOURCE_IDENTITY_MISMATCH/);
    const record = f.descriptor.artifacts.find(item => item.role === 'extension');
    const bytes = extensionBytes({ ...f.descriptor.buildIdentity, sourceSha256: 'f'.repeat(64) });
    await writeFile(path.join(f.artifactRoot, record.path), bytes);
    record.bytes = bytes.length; record.sha256 = hash(bytes);
    await assert.rejects(buildManifest({ repoRoot: f.root, descriptor: f.descriptor, artifactRoot: f.artifactRoot }), /EXTENSION_IDENTITY_MISMATCH/);
  } finally { await f.cleanup(); }
});
