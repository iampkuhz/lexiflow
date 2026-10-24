import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile, readdir, lstat, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import fsPromises from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { fileURLToPath } from 'node:url';
import { verifyReleaseCandidate } from '../integrity/release-candidate-verifier.mjs';
import { serializeManifest } from '../integrity/release-artifact-manifest.mjs';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';
import { assembled } from './verified-candidate-fixture.mjs';
import { extensionBytes } from './zip-fixture.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');

async function snapshot(root) {
  const rows = [];
  async function walk(rel = '') {
    for (const name of (await readdir(path.join(root, rel))).sort()) {
      const child = rel ? `${rel}/${name}` : name;
      const info = await lstat(path.join(root, child));
      rows.push([child, info.mode, info.size, info.mtimeMs, info.ctimeMs,
        info.isFile() ? hash(await readFile(path.join(root, child))) : null]);
      if (info.isDirectory()) await walk(child);
    }
  }
  await walk(); return rows;
}
async function rewriteMarker(f, mutate) {
  const value = JSON.parse(await readFile(path.join(f.candidateDirectory, 'candidate.json'), 'utf8'));
  mutate(value);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
  await writeFile(path.join(f.candidateDirectory, 'candidate.json'), bytes);
  return { ...f.input, candidateSha256: hash(bytes) };
}
async function rejectsReadOnly(f, input = f.input) {
  const before = await snapshot(f.candidateDirectory);
  await assert.rejects(verifyReleaseCandidate(input));
  assert.deepEqual(await snapshot(f.candidateDirectory), before);
}
test('真实assemble的release与clean SNAPSHOT完整候选可只读验证', async () => {
  for (const version of ['2.0.0', '2.0.0-SNAPSHOT']) {
    const f = await assembled(version);
    try {
      const before = await snapshot(f.candidateDirectory);
      const proof = await verifyReleaseCandidate(f.input);
      assert.equal(proof.candidateSha256, f.input.candidateSha256);
      assert.equal(proof.manifestSha256, f.candidate.manifestSha256);
      assert.equal(proof.buildIdentity.dirty, false);
      assert.equal(Object.hasOwn(proof, 'formalEligible'), false);
      assert.deepEqual(await snapshot(f.candidateDirectory), before);
    } finally { await f.cleanup(); }
  }
});

test('actual corresponding-sources archive is digest-bound and cannot be replaced after assembly', async () => {
  const f = await assembled();
  try {
    const source = f.manifest.artifacts.find(item => item.role === 'corresponding-sources');
    const archive = path.join(f.candidateDirectory, 'payload', source.path);
    const original = await readFile(archive);
    assert.equal(hash(original), source.sha256);
    await writeFile(archive, Buffer.concat([original, Buffer.from('replacement') ]));
    await rejectsReadOnly(f);
  } finally { await f.cleanup(); }
});

test('cached synthetic template returns isolated cloned roots, metadata, archives and cleanup', async () => {
  const first = await assembled();
  const second = await assembled();
  try {
    assert.notEqual(first.root, second.root);
    assert.notEqual(first.imageDirectory, second.imageDirectory);
    assert.notEqual(first.outputParent, second.outputParent);
    assert.notEqual(first.candidateDirectory, second.candidateDirectory);
    assert.notEqual(first.sourceArchive.path, second.sourceArchive.path);
    assert.equal(first.sourceArchive.path.startsWith(first.candidateDirectory), true);
    assert.equal(second.sourceArchive.path.startsWith(second.candidateDirectory), true);
    assert.deepEqual(first.candidate, second.candidate);
    assert.deepEqual(first.manifest, second.manifest);
    assert.notEqual(first.descriptor, second.descriptor);
    assert.notEqual(first.descriptor.artifacts, second.descriptor.artifacts);

    const secondArchiveBefore = await readFile(second.sourceArchive.path);
    first.descriptor.dataset.releaseId = 'mutated-private-fixture';
    await writeFile(first.sourceArchive.path, Buffer.from('private replacement'));
    assert.equal(second.descriptor.dataset.releaseId, 'release-test');
    assert.deepEqual(await readFile(second.sourceArchive.path), secondArchiveBefore);

    await first.cleanup();
    assert.deepEqual(await readFile(path.join(second.candidateDirectory, 'candidate.json')), second.marker);
    const third = await assembled();
    try {
      assert.deepEqual(third.candidate, second.candidate, 'mutating and cleaning a clone cannot affect the read-only template');
      assert.equal(hash(await readFile(third.sourceArchive.path)), third.sourceArchive.sha256);
    } finally { await third.cleanup(); }
  } finally {
    await first.cleanup();
    await second.cleanup();
  }
});
test('marker、manifest、sidecar、artifact和extension内嵌身份篡改被拒', async () => {
  for (const mutation of [
    async f => writeFile(path.join(f.candidateDirectory, 'candidate.json'), '{'),
    async f => writeFile(path.join(f.candidateDirectory, 'payload/manifest.json'), '{}\n'),
    async f => writeFile(path.join(f.candidateDirectory, 'payload/manifest.json.sha256'), '0'.repeat(64) + '  manifest.json\n'),
    async f => writeFile(path.join(f.candidateDirectory, 'payload/database/schema.sql'), 'changed'),
    async f => writeFile(path.join(f.candidateDirectory, 'payload/extension/package.zip'), extensionBytes(resolveBuildIdentity(f.root)).subarray(0, 16)),
  ]) {
    const f = await assembled();
    try { await mutation(f); await rejectsReadOnly(f); } finally { await f.cleanup(); }
  }
});
test('候选marker身份、平台、schema和规范字节拒绝', async () => {
  for (const mutation of [
    x => { x.buildIdentity.buildId = 'f'.repeat(64); },
    x => { x.buildIdentity.dirty = true; },
    x => { x.imageCandidates[0].platform = 'linux/amd64'; },
    x => { x.imageCandidates.pop(); },
    x => { x.imageCandidates[0].sha256 = 'NOT_HEX'; },
    x => { x.manifestPath = '../manifest.json'; },
  ]) {
    const f = await assembled();
    try { await rejectsReadOnly(f, await rewriteMarker(f, mutation)); } finally { await f.cleanup(); }
  }
  const f = await assembled();
  try {
    const markerFile = path.join(f.candidateDirectory, 'candidate.json');
    const original = await readFile(markerFile, 'utf8');
    for (const bytes of [original.replace('{', '{\n  "schemaVersion": 1,'), original.trim(), Buffer.alloc(2_000_001, 32)]) {
      await writeFile(markerFile, bytes);
      await rejectsReadOnly(f, { ...f.input, candidateSha256: hash(bytes) });
    }
  } finally { await f.cleanup(); }
});
test('目录exact集合、大小写冲突、逃逸和非普通文件被拒', async () => {
  for (const mutation of [
    async f => writeFile(path.join(f.candidateDirectory, 'payload/extra'), 'x'),
    async f => mkdir(path.join(f.candidateDirectory, 'payload/empty')),
    async f => rm(path.join(f.candidateDirectory, 'payload/database/schema.sql')),
    async f => writeFile(path.join(f.candidateDirectory, 'payload/Compose.yaml'), 'x'),
    async f => { await rm(path.join(f.candidateDirectory, 'payload/database/schema.sql')); await symlink('manifest.json', path.join(f.candidateDirectory, 'payload/database/schema.sql')); },
    async f => { await rm(path.join(f.candidateDirectory, 'payload/database/schema.sql')); execFileSync('mkfifo', [path.join(f.candidateDirectory, 'payload/database/schema.sql')]); },
  ]) {
    const f = await assembled();
    try { await mutation(f); await rejectsReadOnly(f); } finally { await f.cleanup(); }
  }
  const f = await assembled();
  try {
    await rejectsReadOnly(f, await rewriteMarker(f, x => { x.manifestPath = 'payload/../manifest.json'; }));
    const alias = path.join(f.outputParent, 'alias'); await symlink(f.candidateDirectory, alias);
    await rejectsReadOnly(f, { candidateDirectory: alias, candidateSha256: f.input.candidateSha256 });
  } finally { await f.cleanup(); }
});

async function republishManifest(f, mutate) {
  const root = f.candidateDirectory;
  const manifest = JSON.parse(await readFile(path.join(root, 'payload/manifest.json'), 'utf8'));
  mutate(manifest);
  const bytes = Buffer.from(serializeManifest(manifest));
  await writeFile(path.join(root, 'payload/manifest.json'), bytes);
  const manifestSha256 = hash(bytes);
  await writeFile(path.join(root, 'payload/manifest.json.sha256'), `${manifestSha256}  manifest.json\n`);
  return rewriteMarker(f, candidate => { candidate.manifestSha256 = manifestSha256; });
}
test('完整manifest重签仍拒绝内部extension身份及notice摘要错配', async () => {
  const f = await assembled();
  try {
    const extensionPath = path.join(f.candidateDirectory, 'payload/extension/package.zip');
    const other = extensionBytes({ ...f.descriptor.buildIdentity, buildId: 'f'.repeat(64) });
    await writeFile(extensionPath, other);
    const input = await republishManifest(f, manifest => {
      const extension = manifest.artifacts.find(item => item.role === 'extension');
      extension.bytes = other.length; extension.sha256 = hash(other);
    });
    await rejectsReadOnly(f, input);
  } finally { await f.cleanup(); }
  const g = await assembled();
  try {
    const input = await republishManifest(g, manifest => {
      manifest.licenses[0].noticeSha256 = 'f'.repeat(64);
    });
    await rejectsReadOnly(g, input);
  } finally { await g.cleanup(); }
});

test('读中替换或截断不能形成成功证明', async () => {
  for (const replacement of [Buffer.from('tampered content'), Buffer.alloc(0)]) {
    const f = await assembled();
    const target = path.join(f.candidateDirectory, 'payload/database/schema.sql');
    const original = fsPromises.open;
    let injected = false;
    fsPromises.open = async (...args) => {
      const handle = await original(...args);
      if (!injected && args[0] === target) {
        injected = true;
        await writeFile(target, replacement);
      }
      return handle;
    };
    syncBuiltinESMExports();
    try {
      await assert.rejects(verifyReleaseCandidate(f.input));
      assert.equal(injected, true);
    } finally {
      fsPromises.open = original;
      syncBuiltinESMExports();
      await f.cleanup();
    }
  }
});

test('读取marker期间候选祖先目录替换必须拒绝', async () => {
  const f = await assembled();
  const moved = `${f.candidateDirectory}-moved`;
  const target = path.join(f.candidateDirectory, 'candidate.json');
  const original = fsPromises.open;
  let injected = false;
  fsPromises.open = async (...args) => {
    const handle = await original(...args);
    if (!injected && args[0] === target) {
      injected = true;
      await rename(f.candidateDirectory, moved);
      await mkdir(f.candidateDirectory);
    }
    return handle;
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(verifyReleaseCandidate(f.input));
    assert.equal(injected, true);
  } finally {
    fsPromises.open = original;
    syncBuiltinESMExports();
    await rm(f.candidateDirectory, { recursive: true, force: true });
    await rename(moved, f.candidateDirectory).catch(() => {});
    await f.cleanup();
  }
});

test('摘要字段拒绝数组及其他非字符串，即使隐式字符串看起来是hex', async () => {
  for (const mutate of [
    x => { x.buildInputSha256 = [x.buildInputSha256]; },
    x => { x.manifestSha256 = [x.manifestSha256]; },
    x => { x.imageCandidates[0].sha256 = [x.imageCandidates[0].sha256]; },
  ]) {
    const f = await assembled();
    try { await rejectsReadOnly(f, await rewriteMarker(f, mutate)); }
    finally { await f.cleanup(); }
  }
});

test('候选目录外同祖先的兄弟文件创建不改变候选证明', async () => {
  const f = await assembled();
  const sibling = path.join(f.outputParent, 'unrelated-sibling.txt');
  const original = fsPromises.open;
  let injected = false;
  fsPromises.open = async (...args) => {
    const handle = await original(...args);
    if (!injected && args[0] === path.join(f.candidateDirectory, 'candidate.json')) {
      injected = true;
      await writeFile(sibling, 'unrelated');
    }
    return handle;
  };
  syncBuiltinESMExports();
  try {
    const proof = await verifyReleaseCandidate(f.input);
    assert.equal(proof.candidateSha256, f.input.candidateSha256);
    assert.equal(injected, true);
  } finally {
    fsPromises.open = original;
    syncBuiltinESMExports();
    await f.cleanup();
  }
});

test('读中更换文件inode即使字节相同仍拒绝', async () => {
  const f = await assembled();
  const target = path.join(f.candidateDirectory, 'payload/database/schema.sql');
  const originalBytes = await readFile(target);
  const original = fsPromises.open;
  let injected = false;
  fsPromises.open = async (...args) => {
    const handle = await original(...args);
    if (!injected && args[0] === target) {
      injected = true;
      const old = `${target}.old`;
      await rename(target, old);
      await writeFile(target, originalBytes);
      await rm(old);
    }
    return handle;
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(verifyReleaseCandidate(f.input));
    assert.equal(injected, true);
  } finally {
    fsPromises.open = original;
    syncBuiltinESMExports();
    await f.cleanup();
  }
});

test('读中父目录rename后以symlink替代必须拒绝并恢复fixture', async () => {
  const f = await assembled();
  const parent = f.outputParent;
  const moved = `${parent}-moved`;
  const target = path.join(f.candidateDirectory, 'candidate.json');
  const original = fsPromises.open;
  let injected = false;
  fsPromises.open = async (...args) => {
    const handle = await original(...args);
    if (!injected && args[0] === target) {
      injected = true;
      await rename(parent, moved);
      await symlink(moved, parent);
    }
    return handle;
  };
  syncBuiltinESMExports();
  try {
    await assert.rejects(verifyReleaseCandidate(f.input));
    assert.equal(injected, true);
  } finally {
    fsPromises.open = original;
    syncBuiltinESMExports();
    await rm(parent, { force: true });
    await rename(moved, parent).catch(() => {});
    await f.cleanup();
  }
});


test('非UTF-8 manifest即使摘要重绑定也不是规范JSON', async () => {
  const f = await assembled();
  try {
    const manifestPath = path.join(f.candidateDirectory, 'payload/manifest.json');
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
    manifest.licenses[0].licenseName = 'INVALID_UTF8_TEST';
    const bytes = Buffer.from(serializeManifest(manifest));
    const at = bytes.indexOf('INVALID_UTF8_TEST');
    assert.ok(at > 0);
    bytes[at] = 0xff;
    await writeFile(manifestPath, bytes);
    const manifestSha256 = hash(bytes);
    await writeFile(path.join(f.candidateDirectory, 'payload/manifest.json.sha256'), `${manifestSha256}  manifest.json\n`);
    const input = await rewriteMarker(f, candidate => { candidate.manifestSha256 = manifestSha256; });
    const before = await snapshot(f.candidateDirectory);
    await assert.rejects(verifyReleaseCandidate(input), /CANDIDATE_CANONICAL_INVALID/);
    assert.deepEqual(await snapshot(f.candidateDirectory), before);
  } finally { await f.cleanup(); }
});
