import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import fsPromises from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { fileURLToPath } from 'node:url';
import { assembleReleaseCandidate } from '../candidate.mjs';
import { extensionBytes } from './zip-fixture.mjs';
import { resolveBuildIdentity } from '../version.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = (value) => createHash('sha256').update(value).digest('hex');
async function tree(root, relative = '') {
  const result = [];
  for (const name of await (await import('node:fs/promises')).readdir(path.join(root, relative))) {
    const child = relative ? `${relative}/${name}` : name;
    const stat = await (await import('node:fs/promises')).lstat(path.join(root, child));
    if (stat.isDirectory()) result.push(...await tree(root, child)); else result.push(child);
  }
  return result.sort();
}

async function fixture(baseVersion = '2.0.0') {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-fixture-')));
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await writeFile(path.join(root, 'ops/release/version.txt'), `${baseVersion}\n`);
  await writeFile(path.join(root, 'ops/release/version.mjs'), await readFile(path.join(repoRoot, 'ops/release/version.mjs')));
  await writeFile(path.join(root, 'ops/release/runtime-entry.mjs'), await readFile(path.join(repoRoot, 'ops/release/runtime-entry.mjs')));
  await writeFile(path.join(root, 'ops/release/runtime-verification.sh'), await readFile(path.join(repoRoot, 'ops/release/runtime-verification.sh')));
  for (const name of ['lifecycle.mjs', 'lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh']) {
    await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repoRoot, 'ops/release', name)));
  }
  await writeFile(path.join(root, '.gitignore'), '/input/\n/images/\n/output/\n/descriptor.json\n');
  execFileSync('git', ['init', '-q', root]);
  execFileSync('git', ['-C', root, 'add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean candidate fixture']);
  const sourceCommit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const buildIdentity = resolveBuildIdentity(root);
  const softwareVersion = buildIdentity.softwareVersion;
  const artifactRoot = path.join(root, 'input');
  const outputParent = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-output-')));
  await mkdir(artifactRoot);
  const licenses = []; const records = [];
  const candidateBases = [];
  async function add(role, relative, value, extra = {}, licenseIds = ['lexiflow']) {
    const bytes = Buffer.from(value); const target = path.join(artifactRoot, relative);
    await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, bytes);
    records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, ...extra });
    return bytes;
  }
  const components = ['LexiFlow', 'extension-third-party', 'API-runtime', 'PostgreSQL', 'dataset'];
  const ids = ['lexiflow', 'ext-third-party', 'api-runtime', 'postgresql', 'dataset-license'];
  for (let i = 0; i < components.length; i += 1) {
    const noticePath = `licenses/${ids[i]}.txt`; const notice = Buffer.from(`Synthetic notice ${ids[i]}\n`);
    licenses.push({ id: ids[i], component: components[i], licenseId: `TEST-${ids[i]}`, licenseName: `Synthetic ${ids[i]}`,
      sourceUrl: `https://example.invalid/${ids[i]}`, noticePath, noticeBytes: notice.length, noticeSha256: hash(notice) });
    await add('license', noticePath, notice, {}, [ids[i]]);
  }
  const dataset = { releaseId: 'release-test', preparationId: 'prep-test', ruleId: 'rules-test' };
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const javaRuntime = { reference: `registry.example.invalid/java/runtime@sha256:${'1'.repeat(64)}`, imageId: `sha256:${'a'.repeat(64)}` };
    const postgresRuntime = { reference: `registry.example.invalid/postgres/runtime@sha256:${'2'.repeat(64)}`, imageId: `sha256:${'b'.repeat(64)}` };
    const base = { platform, javaRuntime, postgresRuntime };
    candidateBases.push(base);
    for (const [role, filename, imageDigest, licenseIds] of [
      ['api-image', 'api-image.tar', `sha256:${'c'.repeat(64)}`, ['lexiflow', 'api-runtime']],
      ['postgres-image', 'postgres-image.tar', `sha256:${'d'.repeat(64)}`, ['postgresql']]
    ]) {
      const relative = `images/${platform.replace('/', '-')}/${filename}`;
      const bytes = Buffer.from(`synthetic ${platform} ${role} archive`);
      await mkdir(path.dirname(path.join(artifactRoot, relative)), { recursive: true });
      await writeFile(path.join(artifactRoot, relative), bytes);
      records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, platform, imageDigest });
    }
  }
  const descriptor = { schemaVersion: 1, buildIdentity, softwareVersion, sourceCommit, apiContract: 'api-test', sqlVersion: 'sql-test', dataset,
    platforms: ['linux/amd64', 'linux/arm64'], artifacts: records, licenses };
  for (const image of records.filter((record) => record.role === 'api-image' || record.role === 'postgres-image')) {
    const bytes = await readFile(path.join(artifactRoot, image.path));

  }
  await add('compose', 'compose.yaml', 'synthetic compose');
  await add('sql', 'database/schema.sql', 'synthetic SQL');
  await add('dataset', 'data/dataset.bin', 'synthetic dataset', { metadata: { ...dataset, sqlVersion: 'sql-test' } }, ['dataset-license']);
  await add('extension', 'extension/package.zip', extensionBytes(buildIdentity), { metadata: { softwareVersion, sourceCommit } }, ['lexiflow', 'ext-third-party']);
  const imageCandidateDirectories = [];
  const buildInputSha256 = 'e'.repeat(64);
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const directory = await realpath(await mkdtemp(path.join(tmpdir(), `lexiflow-shard-${platform.split('/')[1]}-`)));
    const candidateRecords = records.filter((record) => record.role === 'api-image' || record.role === 'postgres-image').filter((record) => record.platform === platform)
      .map(({ role, platform: p, path: artifactPath, bytes, sha256, imageDigest }) => ({ role, platform: p, path: artifactPath, bytes, sha256, imageDigest }));
    const imageCandidate = { schemaVersion: 1, kind: 'lexiflow-image-candidate', buildIdentity, softwareVersion, sourceCommit, platform, buildInputSha256,
      baseImages: [candidateBases.find((base) => base.platform === platform)], artifacts: candidateRecords };
    for (const record of candidateRecords) { const target = path.join(directory, record.path); await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, await readFile(path.join(artifactRoot, record.path))); }
    const marker = `${JSON.stringify(imageCandidate, null, 2)}\n`; await writeFile(path.join(directory, 'candidate.json'), marker);
    imageCandidateDirectories.push({ directory, sha256: hash(Buffer.from(marker)) });
  }
  const descriptorFile = path.join(root, 'descriptor.json'); await writeFile(descriptorFile, JSON.stringify(descriptor));
  return { root, artifactRoot, imageCandidateDirectories: imageCandidateDirectories.map(({ directory, sha256 }, index) => ({ directory, sha256, platform: index === 0 ? 'linux/amd64' : 'linux/arm64' })), outputParent, descriptor, descriptorFile,
    cleanup: async () => { await rm(root, { recursive: true, force: true }); await rm(outputParent, { recursive: true, force: true }); for (const { directory } of imageCandidateDirectories) await rm(directory, { recursive: true, force: true }); } };
}

test('assembles a two-platform private candidate with exact payload and stable marker', async () => {
  const f = await fixture();
  try {
    const before = await Promise.all(f.imageCandidateDirectories.map(({ directory }) => readFile(path.join(directory, 'candidate.json'))));
    const result = await assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent });
    assert.deepEqual(result.candidate, { schemaVersion: 1, kind: 'lexiflow-release-candidate', buildIdentity: resolveBuildIdentity(f.root), softwareVersion: '2.0.0',
      sourceCommit: f.descriptor.sourceCommit, manifestPath: 'payload/manifest.json',
      manifestSha256: hash(await readFile(path.join(result.candidateDirectory, 'payload/manifest.json'))), buildInputSha256: 'e'.repeat(64), imageCandidates: f.imageCandidateDirectories.map(({ platform, sha256 }) => ({ platform, sha256 })) });
    assert.deepEqual(await tree(result.candidateDirectory), [...result.manifest.artifacts.map((item) => `payload/${item.path}`),
      'candidate.json', 'payload/manifest.json', 'payload/manifest.json.sha256'].sort());
    assert.deepEqual(await Promise.all(f.imageCandidateDirectories.map(({ directory }) => readFile(path.join(directory, 'candidate.json')))), before);
    assert.equal(result.candidate.supported, undefined); assert.equal(result.candidate.verified, undefined);
    assert.equal(result.manifest.artifacts.find((item) => item.role === 'runtime-entry').path, 'lexiflow.sh');
  } finally { await f.cleanup(); }
});

test('assembles exactly the descriptor platform subset and rejects omitted selected candidate', async () => {
  const f = await fixture();
  try {
    f.descriptor.platforms = ['linux/arm64'];
    f.descriptor.artifacts = f.descriptor.artifacts.filter((item) =>
      !['api-image', 'postgres-image'].includes(item.role) || item.platform === 'linux/arm64');
    const selected = f.imageCandidateDirectories.filter((item) => item.platform === 'linux/arm64');
    const result = await assembleReleaseCandidate({ repoRoot: f.root,
      imageCandidates: selected.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent });
    assert.deepEqual(result.candidate.imageCandidates.map((item) => item.platform), ['linux/arm64']);
    const wrongPlatform = f.imageCandidateDirectories.filter((item) => item.platform === 'linux/amd64');
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root,
      imageCandidates: wrongPlatform.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }),
    { message: 'IMAGE_CANDIDATE_INVALID' });
  } finally { await f.cleanup(); }
});

test('rejects identity, image binding, descriptor schema and runtime entry injection', async () => {
  for (const mutate of [
    (f) => { f.descriptor.sourceCommit = 'f'.repeat(40); },
    (f) => { f.descriptor.artifacts.find((item) => item.role === 'api-image').sha256 = 'f'.repeat(64); },
    (f) => { f.descriptor.artifacts.find((item) => item.role === 'api-image').platform = 'linux/ppc64'; },
    (f) => { f.descriptor.extra = true; },
    (f) => { f.descriptor.artifacts.push({ role: 'runtime-entry', path: 'lexiflow.sh' }); }
  ]) {
    const f = await fixture();
    try { mutate(f); await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }));
      assert.deepEqual(await (await import('node:fs/promises')).readdir(f.outputParent), []); }
    finally { await f.cleanup(); }
  }
});

test('rejects bad image candidate data and preserves source candidate bytes', async () => {
  for (const change of [
    (value) => { value.extra = true; },
    (value) => { value.sourceCommit = 'f'.repeat(40); },
    (value) => { value.artifacts[0].sha256 = 'f'.repeat(64); },
    (value) => { value.artifacts[0].platform = 'linux/ppc64'; },
    (value) => { value.artifacts[0].path = '../../escape'; }
  ]) {
    const f = await fixture();
    try {
      const file = path.join(f.imageCandidateDirectories[0].directory, 'candidate.json'); const original = JSON.parse(await readFile(file, 'utf8'));
      change(original); const changedText = JSON.stringify(original); await writeFile(file, changedText); f.imageCandidateDirectories[0].sha256 = hash(Buffer.from(changedText));
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }));
      assert.deepEqual(await (await import('node:fs/promises')).readdir(f.outputParent), []);
    } finally { await f.cleanup(); }
  }
});

test('候选记录原始字节摘要篡改在摘要绑定层拒绝', async () => {
  const f = await fixture();
  try {
    const inputs = f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 }));
    inputs[0].sha256 = 'f'.repeat(64);
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: inputs,
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), { message: 'IMAGE_CANDIDATE_DIGEST_MISMATCH' });
    assert.deepEqual(await fsPromises.readdir(f.outputParent), []);
  } finally { await f.cleanup(); }
});

test('重复候选平台与不完整双平台输入在候选集合层拒绝', async () => {
  const f = await fixture();
  try {
    const amd64 = f.imageCandidateDirectories.find(({ platform }) => platform === 'linux/amd64');
    const inputs = [amd64, amd64].map(({ directory, sha256 }) => ({ directory, sha256 }));
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: inputs,
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), { message: 'IMAGE_CANDIDATE_INVALID' });
    assert.deepEqual(await fsPromises.readdir(f.outputParent), []);
  } finally { await f.cleanup(); }
});

test('两平台候选 buildInputSha256 不一致在来源一致性层拒绝', async () => {
  const f = await fixture();
  try {
    const arm64 = f.imageCandidateDirectories.find(({ platform }) => platform === 'linux/arm64');
    const file = path.join(arm64.directory, 'candidate.json');
    const value = JSON.parse(await readFile(file, 'utf8'));
    value.buildInputSha256 = 'f'.repeat(64);
    const bytes = Buffer.from(JSON.stringify(value, null, 2) + '\n');
    await writeFile(file, bytes);
    arm64.sha256 = hash(bytes);
    const inputs = f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 }));
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: inputs,
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), { message: 'SOURCE_IDENTITY_MISMATCH' });
    assert.deepEqual(await fsPromises.readdir(f.outputParent), []);
  } finally { await f.cleanup(); }
});

test('拒绝缺失或伪造的候选构建身份并拒绝跨平台身份不一致', async () => {
  for (const mutate of [
    (value) => { delete value.buildIdentity; },
    (value) => { value.buildIdentity.buildId = 'f'.repeat(64); },
    (value) => { value.buildIdentity.sourceSha256 = 'f'.repeat(64); },
    (value) => { value.buildIdentity.dirty = true; },
    (value) => { value.buildIdentity.softwareVersion = '9.0.0'; },
  ]) {
    const f = await fixture();
    try {
      const file = path.join(f.imageCandidateDirectories[0].directory, 'candidate.json');
      const value = JSON.parse(await readFile(file, 'utf8')); mutate(value);
      const bytes = Buffer.from(JSON.stringify(value, null, 2) + '\n'); await writeFile(file, bytes);
      f.imageCandidateDirectories[0].sha256 = hash(bytes);
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root,
        imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), { message: 'IMAGE_CANDIDATE_INVALID' });
    } finally { await f.cleanup(); }
  }

  const f = await fixture();
  try {
    const arm = f.imageCandidateDirectories.find(({ platform }) => platform === 'linux/arm64');
    const file = path.join(arm.directory, 'candidate.json'); const value = JSON.parse(await readFile(file, 'utf8'));
    value.buildIdentity.sourceSha256 = 'f'.repeat(64);
    value.buildIdentity.buildId = hash(JSON.stringify({ baseVersion: value.buildIdentity.baseVersion,
      sourceCommit: value.buildIdentity.sourceCommit, sourceSha256: value.buildIdentity.sourceSha256, dirty: false }));
    const bytes = Buffer.from(JSON.stringify(value, null, 2) + '\n'); await writeFile(file, bytes); arm.sha256 = hash(bytes);
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root,
      imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), { message: 'SOURCE_IDENTITY_MISMATCH' });
  } finally { await f.cleanup(); }
});

test('rejects artifact and parent symlinks and output containment without touching neighbors', async () => {
  const f = await fixture();
  try {
    const neighbor = path.join(f.outputParent, 'neighbor'); await writeFile(neighbor, 'keep');
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.artifactRoot }));
    const original = path.join(f.artifactRoot, 'compose.yaml'); await rm(original); await symlink('../descriptor.json', original);
    await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }));
    assert.equal(await readFile(neighbor, 'utf8'), 'keep');
  } finally { await f.cleanup(); }
});

// 畸形输入不能泄漏 TypeError 或文件系统路径。
test('拒绝空许可记录和非法基础镜像引用并返回固定原因', async () => {
  for (const mutate of [
    (f) => { f.descriptor.licenses = [null]; },
    (f) => { f.descriptor.licenses = {}; },
    (f) => { f.descriptor.artifacts = [null]; }
  ]) {
    const f = await fixture();
    try {
      mutate(f);
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), (error) => /^[A-Z_]+$/.test(error.message));
    } finally { await f.cleanup(); }
  }
  for (const reference of ['bad_host.example/repo', 'registry.example//repo', 'registry.example/.hidden', '127.0.0.1/repo', 'registry.example:65536/repo']) {
    const f = await fixture();
    try {
      const file = path.join(f.imageCandidateDirectories[0].directory, 'candidate.json');
      const value = JSON.parse(await readFile(file, 'utf8'));
      value.baseImages[0].javaRuntime.reference = `${reference}@sha256:${'a'.repeat(64)}`;
      const text = JSON.stringify(value); await writeFile(file, text); f.imageCandidateDirectories[0].sha256 = hash(Buffer.from(text));
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), /IMAGE_CANDIDATE_INVALID/);
    } finally { await f.cleanup(); }
  }
});

test('拒绝损坏超大及符号链接镜像记录并保留邻居', async () => {
  for (const mode of ['broken', 'oversized', 'symlink']) {
    const f = await fixture();
    try {
      const file = path.join(f.imageCandidateDirectories[0].directory, 'candidate.json');
      const neighbor = path.join(f.outputParent, 'keep'); await writeFile(neighbor, 'keep');
      if (mode === 'broken') { await writeFile(file, '{'); f.imageCandidateDirectories[0].sha256 = hash(Buffer.from('{')); }
      if (mode === 'oversized') { const huge = ' '.repeat(2_000_001); await writeFile(file, huge); f.imageCandidateDirectories[0].sha256 = hash(Buffer.from(huge)); }
      if (mode === 'symlink') { await rm(file); await symlink(neighbor, file); }
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), /IMAGE_CANDIDATE_INVALID/);
      assert.deepEqual(await fsPromises.readdir(f.outputParent), ['keep']);
      assert.equal(await readFile(neighbor, 'utf8'), 'keep');
    } finally { await f.cleanup(); }
  }
});

test('装配后原始制品或候选记录漂移时撤销整个输出', async () => {
  for (const mode of ['artifact', 'marker', 'source']) {
    const f = await fixture(); const originalRead = fsPromises.readFile;
    try {
      let changed = false;
      fsPromises.readFile = async function (file, ...args) {
        const value = await originalRead(file, ...args);
        if (!changed && String(file).endsWith('/payload/manifest.json')) {
          changed = true;
          const target = mode === 'artifact' ? path.join(f.artifactRoot, 'compose.yaml')
            : mode === 'marker' ? path.join(f.imageCandidateDirectories[0].directory, 'candidate.json') : path.join(f.root, 'changed.txt');
          await writeFile(target, mode === 'marker' ? `${await originalRead(target, 'utf8')} ` : 'changed');
        }
        return value;
      };
      syncBuiltinESMExports();
      await assert.rejects(assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
        artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent }), /ARTIFACT_DIGEST_MISMATCH|IMAGE_CANDIDATE_CHANGED|RELEASE_SOURCE_CHANGED/);
      assert.equal(changed, true);
      assert.deepEqual(await fsPromises.readdir(f.outputParent), []);
    } finally { fsPromises.readFile = originalRead; syncBuiltinESMExports(); await f.cleanup(); }
  }
});

test('clean SNAPSHOT assembly preserves the full identity in candidate and manifest', async () => {
  const f = await fixture('2.0.0-SNAPSHOT');
  try {
    const result = await assembleReleaseCandidate({ repoRoot: f.root, imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })), artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent });
    assert.equal(result.candidate.buildIdentity.channel, 'snapshot');
    assert.equal(result.candidate.buildIdentity.dirty, false);
    assert.deepEqual(result.manifest.buildIdentity, result.candidate.buildIdentity);
  } finally { await f.cleanup(); }
});
