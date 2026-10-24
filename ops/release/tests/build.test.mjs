import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { loadBaseImageLock, prepareImageBuild } from '../build/release-image-build-plan.mjs';
import { jarBytes, makeZip } from './zip-fixture.mjs';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const args = (f, descriptor = f.descriptor) => ({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor });
const hash = (value) => createHash('sha256').update(value).digest('hex');
const templates = ['ops/docker/Dockerfile.runtime', 'ops/docker/runtime-entrypoint.sh'];
const lockPath = 'ops/docker/base-images.json';
const lockImages = [
  { platform: 'linux/amd64', javaRuntime: { reference: `registry.example.invalid/java@sha256:${'a'.repeat(64)}`, imageId: `sha256:${'a'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres@sha256:${'b'.repeat(64)}`, imageId: `sha256:${'b'.repeat(64)}` } },
  { platform: 'linux/arm64', javaRuntime: { reference: `registry.example.invalid/java@sha256:${'c'.repeat(64)}`, imageId: `sha256:${'c'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres@sha256:${'d'.repeat(64)}`, imageId: `sha256:${'d'.repeat(64)}` } },
];
const runtime = (name, id) => ({ reference: `registry.example.invalid/${name}@sha256:${id.repeat(64)}`, imageId: `sha256:${id.repeat(64)}` });
const git = (root, args, options = {}) => execFileSync('git', ['-C', root, ...args], { timeout: 10_000, ...options });

test('实际仓库基础镜像锁由正式 parser 校验，而非仅验证合成 fixture', async () => {
  const lock = await loadBaseImageLock(repo);
  assert.deepEqual(lock.map(item => item.platform), ['linux/arm64']);
  for (const item of lock) for (const role of ['javaRuntime', 'postgresRuntime'])
    assert.match(item[role].imageId, /^sha256:[a-f0-9]{64}$/);
});

async function fixture() {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-build-')));
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  for (const directory of ['build', 'integrity', 'runtime']) await mkdir(path.join(root, 'ops/release', directory), { recursive: true });
  await mkdir(path.join(root, 'ops/docker'), { recursive: true });
  for (const relative of templates) {
    const source = path.join(repo, relative); const target = path.join(root, relative);
    await writeFile(target, await readFile(source));
  }
  await writeFile(path.join(root, lockPath), `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`);
  for (const name of ['integrity/source-build-identity.mjs', 'integrity/artifact-build-identity.mjs', 'integrity/extension-archive-integrity.mjs', 'integrity/release-artifact-manifest.mjs']) await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repo, 'ops/release', name)));
  await writeFile(path.join(root, 'ops/release/version.txt'), '2.0.0\n');
  await mkdir(path.join(root, 'infra/postgres'), { recursive: true });
  await writeFile(path.join(root, 'infra/postgres/schema.sql'), 'CREATE TABLE synthetic(id integer);\n');
  await writeFile(path.join(root, '.gitignore'), '/artifact/\n');
  execFileSync('git', ['init', '-q', root], { timeout: 10_000 });
  git(root, ['add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], { timeout: 10_000 });
  const sourceCommit = git(root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const artifactRoot = path.join(root, 'artifact'); await mkdir(artifactRoot);
  const schema = await readFile(path.join(root, 'infra/postgres/schema.sql'));
  const builtJar = jarBytes(resolveBuildIdentity(root), schema); await writeFile(path.join(artifactRoot, 'app.jar'), builtJar);
  const dataset = Buffer.from('synthetic dataset'); const notice = Buffer.from('synthetic notice');
  await mkdir(path.join(artifactRoot, 'licenses'), { recursive: true });
  await writeFile(path.join(artifactRoot, 'dataset.zip'), dataset); await writeFile(path.join(artifactRoot, 'licenses/notice.txt'), notice);
  const descriptor = {
    schemaVersion: 1, softwareVersion: '2.0.0', sourceCommit,
    jar: { path: 'app.jar', bytes: builtJar.length, sha256: hash(builtJar) },
    apiContract: 'caption-hints.v2', sqlVersion: 'sql-test',
    dataset: { path: 'dataset.zip', bytes: dataset.length, sha256: hash(dataset), releaseId: 'release-1', preparationId: 'prep-1', ruleId: 'rule-1' },
    notices: [{ path: 'licenses/notice.txt', bytes: notice.length, sha256: hash(notice) }],
  };
  const refreshJar = async () => {
    const identity = resolveBuildIdentity(root);
    const bytes = jarBytes(identity, schema);
    await writeFile(path.join(artifactRoot, 'app.jar'), bytes);
    descriptor.softwareVersion = identity.softwareVersion; descriptor.sourceCommit = identity.sourceCommit;
    descriptor.jar.bytes = bytes.length; descriptor.jar.sha256 = hash(bytes);
  };
  return { root, artifactRoot, descriptor, refreshJar, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test('生成固定、双平台且无宿主路径的确定性 argv 计划', async () => {
  const f = await fixture();
  try {
    const result = await prepareImageBuild(args(f));
    const reorderedInput = structuredClone(f.descriptor);
    const reorderedJar = reorderedInput.jar;
    reorderedInput.jar = { sha256: reorderedJar.sha256, bytes: reorderedJar.bytes, path: reorderedJar.path };
    const reordered = await prepareImageBuild(args(f, reorderedInput));
    assert.deepEqual(reordered, result);
    assert.deepEqual(Object.keys(result), ['schemaVersion', 'softwareVersion', 'sourceCommit', 'buildIdentity', 'platform', 'jar', 'dataset', 'apiContract', 'sqlVersion', 'sql', 'notices', 'templates', 'bases', 'runtimeIdentity', 'runtimeIdentityEnv', 'platforms']);
    assert.equal(result.platform, 'linux/arm64');
    assert.deepEqual(result.templates.map((item) => item.path), templates);
    assert.equal(result.platforms.length, 1);
    assert.equal(result.platforms[0].platform, 'linux/arm64');
    const command = result.platforms[0].commands[0];
    assert.deepEqual(command.slice(0, 8), ['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/arm64', '--label', `org.opencontainers.image.version=${result.softwareVersion}`]);
    assert.ok(command.includes('--file') && command.includes('Dockerfile.runtime') && command.includes('--iidfile') && command.includes('runtime.iid'));
    assert.ok(command.includes('--label') && command.includes('io.lexiflow.image-role=runtime'));
    assert.ok(command.includes(`JAVA_RUNTIME_IMAGE=${lockImages[1].javaRuntime.reference}`));
    assert.ok(command.includes(`POSTGRES_RUNTIME_IMAGE=${lockImages[1].postgresRuntime.reference}`));
    assert.equal(result.contextFiles, undefined);
    assert.equal(result.platforms[0].contextFiles.length, 6);
    assert.ok(result.runtimeIdentityEnv.includes(`datasetSha256=${f.descriptor.dataset.sha256}`));
    assert.ok(!JSON.stringify(result).includes(f.root));
    const before = structuredClone(result);
    const mutableInput = structuredClone(f.descriptor);
    const independent = await prepareImageBuild(args(f, mutableInput));
    mutableInput.jar.path = 'mutated.jar';
    assert.deepEqual(independent, before);
    result.platforms[0].commands[0][0] = 'corrupted';
    assert.equal((await prepareImageBuild(args(f))).platforms[0].commands[0][0], 'docker');
  } finally { await f.cleanup(); }
});

test('计划平台唯一取自 canonical lock，不接受 descriptor 锁副本', async () => {
  const f = await fixture();
  try {
    const plan = await prepareImageBuild(args(f));
    assert.deepEqual(plan.platforms.map(item => item.platform), ['linux/arm64']);
    await assert.rejects(prepareImageBuild(args(f, { ...f.descriptor, baseImages: lockImages })), { message: 'DESCRIPTOR_INVALID' });
    await writeFile(path.join(f.root, lockPath), JSON.stringify({ schemaVersion: 1, baseImages: [lockImages[1]] }));
    git(f.root, ['add', lockPath]);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'single-platform lock']);
    await f.refreshJar();
    const subset = await prepareImageBuild(args(f));
    assert.deepEqual(subset.platforms.map(item => item.platform), ['linux/arm64']);
    assert.deepEqual(subset.bases.javaRuntime, lockImages[1].javaRuntime);
  } finally { await f.cleanup(); }
});

test('完整构建身份接受干净 SNAPSHOT，但拒绝 dirty 源码', async () => {
  const snapshot = await fixture();
  try {
    await writeFile(path.join(snapshot.root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(snapshot.root, ['add', 'ops/release/version.txt']);
    git(snapshot.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'snapshot fixture']);
    snapshot.descriptor.sourceCommit = git(snapshot.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    snapshot.descriptor.softwareVersion = resolveBuildIdentity(snapshot.root).softwareVersion;
    await snapshot.refreshJar();
    const result = await prepareImageBuild(args(snapshot));
    assert.equal(result.buildIdentity.channel, 'snapshot');
    assert.equal(result.buildIdentity.dirty, false);
  } finally { await snapshot.cleanup(); }

  const dirty = await fixture();
  try {
    await writeFile(path.join(dirty.root, 'uncommitted.txt'), 'dirty');
    await assert.rejects(prepareImageBuild(args(dirty)), { message: 'RELEASE_SOURCE_REJECTED' });
  } finally { await dirty.cleanup(); }
});

test('拒绝 schema、版本/commit 与坏 JAR', async () => {
  const f = await fixture();
  try {
    const cases = [
      ['unknown descriptor field', (d) => { d.extra = true; }, 'DESCRIPTOR_INVALID'],
      ['wrong version', (d) => { d.softwareVersion = '1.0.0'; }, 'SOURCE_IDENTITY_MISMATCH'],
      ['wrong source commit', (d) => { d.sourceCommit = '0'.repeat(40); }, 'SOURCE_IDENTITY_MISMATCH'],
      ['bad digest', (d) => { d.jar.sha256 = 'A'.repeat(64); }, 'JAR_INVALID'],
    ];
    for (const [name, mutate, expected] of cases) {
      const descriptor = structuredClone(f.descriptor); mutate(descriptor);
      await assert.rejects(prepareImageBuild(args(f, descriptor)), { message: expected }, name);
    }
  } finally { await f.cleanup(); }
});

test('JAR 内嵌完整身份及必需资源必须匹配源码身份', async () => {
  const f = await fixture();
  try {
    const identity = resolveBuildIdentity(f.root);
    const cases = [
      ['software version', { ...identity, softwareVersion: '9.9.9' }],
      ['source commit', { ...identity, sourceCommit: '0'.repeat(40) }],
      ['build id', { ...identity, buildId: '0'.repeat(64) }],
      ['source hash', { ...identity, sourceSha256: '0'.repeat(64) }],
    ];
    for (const [name, altered] of cases) {
      const bytes = jarBytes(altered);
      await writeFile(path.join(f.artifactRoot, 'app.jar'), bytes);
      f.descriptor.jar = { path: 'app.jar', bytes: bytes.length, sha256: hash(bytes) };
      await assert.rejects(prepareImageBuild(args(f)), { message: 'JAR_IDENTITY_MISMATCH' }, name);
    }
    for (const [name, bytes] of [
      ['missing metadata', makeZip([['META-INF/lexiflow-version.txt', `${identity.softwareVersion}\n`]])],
      ['malformed archive', Buffer.from('not a zip archive')],
    ]) {
      await writeFile(path.join(f.artifactRoot, 'app.jar'), bytes);
      f.descriptor.jar = { path: 'app.jar', bytes: bytes.length, sha256: hash(bytes) };
      await assert.rejects(prepareImageBuild(args(f)), name === 'malformed archive'
        ? { message: 'ARCHIVE_INVALID' } : { message: 'ARCHIVE_ENTRY_MISSING' }, name);
    }
  } finally { await f.cleanup(); }
});

test('canonical lock 唯一决定镜像身份，并拒绝未知字段、注入与缺失锁', async () => {
  const f = await fixture();
  try {
    assert.equal((await prepareImageBuild(args(f))).bases.platform, 'linux/arm64');
    for (const mutate of [
      (lock) => { lock.extra = true; },
      (lock) => { lock.schemaVersion = 2; },
      (lock) => { lock.baseImages = []; },
      (lock) => { lock.baseImages[0].javaRuntime.reference = 'registry.example.invalid/java:latest'; },
      (lock) => { lock.baseImages[1].platform = 'linux/amd64'; },
      (lock) => { lock.baseImages[0].javaRuntime.imageId = 42; },
      (lock) => { lock.baseImages[0].javaRuntime.reference = 'registry.example.invalid/java@sha256:' + 'a'.repeat(64) + ';echo'; },
      (lock) => { lock.baseImages[0].javaRuntime.reference = 'localhost/java@sha256:' + 'a'.repeat(64); },
      (lock) => { lock.baseImages[0].javaRuntime.tag = 'latest'; },
    ]) {
      const lock = { schemaVersion: 1, baseImages: structuredClone(lockImages) }; mutate(lock);
      await writeFile(path.join(f.root, lockPath), `${JSON.stringify(lock)}\n`);
      git(f.root, ['add', lockPath]);
      git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'mutate lock fixture']);
      f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
      await f.refreshJar();
      await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' });
      await writeFile(path.join(f.root, lockPath), `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`);
      git(f.root, ['add', lockPath]);
      git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'restore lock fixture']);
      f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
      await f.refreshJar();
    }
    await git(f.root, ['rm', '-q', lockPath]);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'remove lock fixture']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    try { await f.refreshJar(); } catch { /* invalid tracked inputs are rejected before artifact identity is consumed */ }
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' });
    await writeFile(path.join(f.root, '.gitignore'), '/artifact/\n/ops/docker/base-images.json\n');
    git(f.root, ['add', '.gitignore']);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'ignore lock fixture']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    await f.refreshJar();
    await writeFile(path.join(f.root, lockPath), `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages })}\n`);
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' });
  } finally { await f.cleanup(); }
});

test('锁文件专属路径拒绝 symlink、超限、未知平台与 malformed JSON', async () => {
  const f = await fixture();
  const lockFile = path.join(f.root, lockPath);
  const commitCurrentLock = async () => {
    git(f.root, ['add', '-A', '--', lockPath]);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'change lock fixture']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    try { await f.refreshJar(); } catch { /* symlink source is rejected at the source boundary */ }
  };
  try {
    await rm(lockFile);
    await symlink(path.join(f.root, 'ops/release/version.txt'), lockFile);
    await commitCurrentLock();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'RELEASE_SOURCE_REJECTED' }, 'tracked lock symlink is rejected by the complete source identity first');

    await rm(lockFile);
    await writeFile(lockFile, `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}${' '.repeat(16 * 1024)}\n`);
    await commitCurrentLock();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' }, 'oversized lock');

    await writeFile(lockFile, `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages.map((item, index) => index ? item : { ...item, platform: 'linux/ppc64le' }) })}\n`);
    await commitCurrentLock();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' }, 'unknown platform');

    await writeFile(lockFile, '{ malformed json\n');
    await commitCurrentLock();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' }, 'malformed JSON');
  } finally { await f.cleanup(); }
});

test('拒绝参数字段、非法类型与 artifact 边界', async () => {
  const f = await fixture();
  try {
    await assert.rejects(prepareImageBuild({ ...args(f), surprise: true }), { message: 'INPUT_INVALID' });
    for (const invalid of [null, {}, { ...args(f), repoRoot: 'relative' }, { ...args(f), artifactRoot: null }]) {
      await assert.rejects(prepareImageBuild(invalid), { message: 'INPUT_INVALID' });
    }
    await assert.rejects(prepareImageBuild({ ...args(f), descriptor: null }), { message: 'DESCRIPTOR_INVALID' });
    for (const bytes of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, 2 ** 40 + 1, '19']) {
      const descriptor = structuredClone(f.descriptor); descriptor.jar.bytes = bytes;
      await assert.rejects(prepareImageBuild(args(f, descriptor)), { message: 'JAR_INVALID' });
    }
    const rootFile = path.join(f.root, 'ops/release/version.txt');
    await assert.rejects(prepareImageBuild({ ...args(f), repoRoot: rootFile }), { message: 'RELEASE_SOURCE_REJECTED' });
    const outside = structuredClone(f.descriptor); outside.jar.path = '../secret';
    await assert.rejects(prepareImageBuild(args(f, outside)), { message: 'BUILD_INPUT_REJECTED' });
    const wrongBytes = structuredClone(f.descriptor); wrongBytes.jar.bytes += 1;
    await assert.rejects(prepareImageBuild(args(f, wrongBytes)), { message: 'BUILD_INPUT_REJECTED' });
    const wrongHash = structuredClone(f.descriptor); wrongHash.jar.sha256 = 'f'.repeat(64);
    await assert.rejects(prepareImageBuild(args(f, wrongHash)), { message: 'BUILD_INPUT_REJECTED' });
    await rm(path.join(f.artifactRoot, 'app.jar'));
    await mkdir(path.join(f.artifactRoot, 'app.jar'));
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BUILD_INPUT_REJECTED' });
  } finally { await f.cleanup(); }
});

test('拒绝 artifact 根/父路径 symlink 与 clean 已提交模板 symlink', async () => {
  const f = await fixture(); const alias = `${f.root}-alias`; const artifactAlias = `${f.root}-artifact-alias`;
  try {
    await symlink(f.root, alias);
    await assert.rejects(prepareImageBuild({ ...args(f), repoRoot: alias }), { message: 'INPUT_INVALID' });
    await symlink(f.artifactRoot, artifactAlias);
    await assert.rejects(prepareImageBuild({ ...args(f), artifactRoot: artifactAlias }), { message: 'INPUT_INVALID' });
    await mkdir(path.join(f.root, 'artifact-parent'));
    await symlink(f.root, path.join(f.root, 'artifact-parent', 'alias'));
    await assert.rejects(prepareImageBuild({ ...args(f), artifactRoot: path.join(f.root, 'artifact-parent', 'alias', 'artifact') }), { message: 'INPUT_INVALID' });
    await rm(path.join(f.root, templates[0]));
    await symlink(path.join(f.root, 'ops/release/version.txt'), path.join(f.root, templates[0]));
    git(f.root, ['add', '-A']);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'commit symlink template']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    // 完整源码闭包在模板读取前拒绝所有Git可见symlink。
    await assert.rejects(prepareImageBuild(args(f)), { message: 'RELEASE_SOURCE_REJECTED' });
  } finally { await rm(alias, { force: true }); await rm(artifactAlias, { force: true }); await f.cleanup(); }
});

test('拒绝clean tree中的ignored模板替身', async () => {
  const f = await fixture();
  try {
    git(f.root, ['rm', '-q', 'ops/docker/runtime-entrypoint.sh']);
    await writeFile(path.join(f.root, '.gitignore'), '/artifact/\n/ops/docker/runtime-entrypoint.sh\n');
    git(f.root, ['add', '.gitignore']);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'ignore removed template']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    await f.refreshJar();
    await writeFile(path.join(f.root, 'ops/docker/runtime-entrypoint.sh'), 'ignored replacement\n');
    assert.equal(git(f.root, ['status', '--porcelain=v1', '--untracked-files=all'], { encoding: 'utf8' }).trim(), '');
    await assert.rejects(prepareImageBuild(args(f)), { message: 'TEMPLATE_INVALID' });
  } finally { await f.cleanup(); }
});
