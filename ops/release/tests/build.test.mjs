import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { prepareImageBuild } from '../build.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const args = (f, descriptor = f.descriptor) => ({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor });
const hash = (value) => createHash('sha256').update(value).digest('hex');
const templates = ['ops/docker/.dockerignore', 'ops/docker/Dockerfile', 'ops/docker/Dockerfile.postgres', 'ops/docker/bootstrap.sh', 'ops/docker/entrypoint.sh'];
const lockPath = 'ops/docker/base-images.json';
const lockImages = [
  { platform: 'linux/amd64', javaRuntime: { reference: `registry.example.invalid/java@sha256:${'a'.repeat(64)}`, imageId: `sha256:${'a'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres@sha256:${'b'.repeat(64)}`, imageId: `sha256:${'b'.repeat(64)}` } },
  { platform: 'linux/arm64', javaRuntime: { reference: `registry.example.invalid/java@sha256:${'c'.repeat(64)}`, imageId: `sha256:${'c'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres@sha256:${'d'.repeat(64)}`, imageId: `sha256:${'d'.repeat(64)}` } },
];
const runtime = (name, id) => ({ reference: `registry.example.invalid/${name}@sha256:${id.repeat(64)}`, imageId: `sha256:${id.repeat(64)}` });
const git = (root, args, options = {}) => execFileSync('git', ['-C', root, ...args], { timeout: 10_000, ...options });

async function fixture() {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-build-')));
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await mkdir(path.join(root, 'ops/docker'), { recursive: true });
  for (const relative of templates) {
    const source = path.join(repo, relative); const target = path.join(root, relative);
    await writeFile(target, await readFile(source));
  }
  await writeFile(path.join(root, lockPath), `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`);
  for (const name of ['version.mjs', 'manifest.mjs']) await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repo, 'ops/release', name)));
  await writeFile(path.join(root, 'ops/release/version.txt'), '2.0.0\n');
  await writeFile(path.join(root, '.gitignore'), '/artifact/\n');
  execFileSync('git', ['init', '-q', root], { timeout: 10_000 });
  git(root, ['add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], { timeout: 10_000 });
  const sourceCommit = git(root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const artifactRoot = path.join(root, 'artifact'); await mkdir(artifactRoot);
  const jarBytes = Buffer.from('synthetic jar bytes'); await writeFile(path.join(artifactRoot, 'app.jar'), jarBytes);
  const descriptor = {
    schemaVersion: 1, softwareVersion: '2.0.0', sourceCommit,
    jar: { path: 'app.jar', bytes: jarBytes.length, sha256: hash(jarBytes) },
    baseImages: [
      structuredClone(lockImages[0]),
      structuredClone(lockImages[1]),
    ],
  };
  return { root, artifactRoot, descriptor, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test('生成固定、双平台且无宿主路径的确定性 argv 计划', async () => {
  const f = await fixture();
  try {
    const result = await prepareImageBuild(args(f));
    const reorderedInput = structuredClone(f.descriptor);
    reorderedInput.baseImages.reverse();
    const reordered = await prepareImageBuild(args(f, reorderedInput));
    assert.deepEqual(reordered, result);
    assert.deepEqual(Object.keys(result), ['schemaVersion', 'softwareVersion', 'sourceCommit', 'jar', 'templates', 'baseImageLock', 'platforms']);
    assert.deepEqual(result.baseImageLock, { path: lockPath, bytes: Buffer.byteLength(`${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`), sha256: hash(`${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`) });
    assert.deepEqual(result.templates.map((item) => item.path), templates);
    assert.deepEqual(result.platforms.map((item) => item.platform), ['linux/amd64', 'linux/arm64']);
    assert.deepEqual(result.platforms.map(({ commands }) => commands), [
      [
        ['docker', 'image', 'inspect', 'sha256:' + 'a'.repeat(64)],
        ['docker', 'image', 'inspect', 'sha256:' + 'b'.repeat(64)],
        ['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/amd64', '--build-arg', `JAVA_RUNTIME_IMAGE=${runtime('java', 'a').reference}`, '--file', 'Dockerfile', '--iidfile', 'api.iid', '.'],
        ['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/amd64', '--build-arg', `POSTGRES_RUNTIME_IMAGE=${runtime('postgres', 'b').reference}`, '--file', 'Dockerfile.postgres', '--iidfile', 'postgres.iid', '.'],
      ],
      [
        ['docker', 'image', 'inspect', 'sha256:' + 'c'.repeat(64)],
        ['docker', 'image', 'inspect', 'sha256:' + 'd'.repeat(64)],
        ['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/arm64', '--build-arg', `JAVA_RUNTIME_IMAGE=${runtime('java', 'c').reference}`, '--file', 'Dockerfile', '--iidfile', 'api.iid', '.'],
        ['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/arm64', '--build-arg', `POSTGRES_RUNTIME_IMAGE=${runtime('postgres', 'd').reference}`, '--file', 'Dockerfile.postgres', '--iidfile', 'postgres.iid', '.'],
      ],
    ]);
    assert.ok(!JSON.stringify(result).includes(f.root));
    const before = structuredClone(result);
    const mutableInput = structuredClone(f.descriptor);
    const independent = await prepareImageBuild(args(f, mutableInput));
    mutableInput.jar.path = 'mutated.jar';
    mutableInput.baseImages[0].javaRuntime.reference = 'mutated';
    assert.deepEqual(independent, before);
    result.platforms[0].commands[0][0] = 'corrupted';
    assert.equal((await prepareImageBuild(args(f))).platforms[0].commands[0][0], 'docker');
  } finally { await f.cleanup(); }
});

test('平台子集只进入计划且仍绑定 lock 中的精确镜像身份', async () => {
  const f = await fixture();
  try {
    const descriptor = structuredClone(f.descriptor);
    descriptor.baseImages = descriptor.baseImages.filter((item) => item.platform === 'linux/arm64');
    const plan = await prepareImageBuild(args(f, descriptor));
    assert.deepEqual(plan.platforms.map((item) => item.platform), ['linux/arm64']);
    assert.ok(!JSON.stringify(plan).includes('linux/amd64'));
  } finally { await f.cleanup(); }
});

test('拒绝 schema、版本/commit、坏 jar、缺平台与注入的 runtime reference', async () => {
  const f = await fixture();
  try {
    const cases = [
      ['unknown descriptor field', (d) => { d.extra = true; }, 'SOURCE_IDENTITY_MISMATCH'],
      ['wrong version', (d) => { d.softwareVersion = '1.0.0'; }, 'SOURCE_IDENTITY_MISMATCH'],
      ['wrong source commit', (d) => { d.sourceCommit = '0'.repeat(40); }, 'SOURCE_IDENTITY_MISMATCH'],
      ['bad digest', (d) => { d.jar.sha256 = 'A'.repeat(64); }, 'JAR_INVALID'],
      ['duplicate platform', (d) => { d.baseImages[1].platform = 'linux/amd64'; }, 'BASE_IMAGES_INVALID'],
      ['tag reference', (d) => { d.baseImages[0].javaRuntime.reference = 'registry.example.invalid/java:latest'; }, 'BASE_IMAGES_INVALID'],
      ['argument injection', (d) => { d.baseImages[0].javaRuntime.reference = 'registry.example.invalid/java@sha256:' + 'a'.repeat(64) + ';echo'; }, 'BASE_IMAGES_INVALID'],
      ['private registry host', (d) => { d.baseImages[0].javaRuntime.reference = 'localhost/java@sha256:' + 'a'.repeat(64); }, 'BASE_IMAGES_INVALID'],
      ['unknown runtime field', (d) => { d.baseImages[0].javaRuntime.tag = 'latest'; }, 'BASE_IMAGES_INVALID'],
    ];
    for (const [name, mutate, expected] of cases) {
      const descriptor = structuredClone(f.descriptor); mutate(descriptor);
      await assert.rejects(prepareImageBuild(args(f, descriptor)), { message: expected }, name);
    }
  } finally { await f.cleanup(); }
});

test('descriptor 镜像身份由 Git 锁唯一决定且比较不依赖字段顺序', async () => {
  const f = await fixture();
  try {
    const reordered = structuredClone(f.descriptor);
    reordered.baseImages[0].javaRuntime = { imageId: reordered.baseImages[0].javaRuntime.imageId, reference: reordered.baseImages[0].javaRuntime.reference };
    assert.equal((await prepareImageBuild(args(f, reordered))).baseImageLock.path, lockPath);
    const mismatch = structuredClone(f.descriptor);
    mismatch.baseImages[0].javaRuntime.imageId = `sha256:${'f'.repeat(64)}`;
    await assert.rejects(prepareImageBuild(args(f, mismatch)), { message: 'BASE_IMAGES_INVALID' });
    for (const mutate of [
      (lock) => { lock.extra = true; },
      (lock) => { lock.schemaVersion = 2; },
      (lock) => { lock.baseImages[0].javaRuntime.reference = 'registry.example.invalid/java:latest'; },
      (lock) => { lock.baseImages[1].platform = 'linux/amd64'; },
      (lock) => { lock.baseImages[0].javaRuntime.imageId = 42; },
    ]) {
      const lock = { schemaVersion: 1, baseImages: structuredClone(lockImages) }; mutate(lock);
      await writeFile(path.join(f.root, lockPath), `${JSON.stringify(lock)}\n`);
      git(f.root, ['add', lockPath]);
      git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'mutate lock fixture']);
      f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
      await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' });
      await writeFile(path.join(f.root, lockPath), `${JSON.stringify({ schemaVersion: 1, baseImages: lockImages }, null, 2)}\n`);
      git(f.root, ['add', lockPath]);
      git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'restore lock fixture']);
      f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    }
    await git(f.root, ['rm', '-q', lockPath]);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'remove lock fixture']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' });
    await writeFile(path.join(f.root, '.gitignore'), '/artifact/\n/ops/docker/base-images.json\n');
    git(f.root, ['add', '.gitignore']);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'ignore lock fixture']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
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
  };
  try {
    await rm(lockFile);
    await symlink(path.join(f.root, 'ops/release/version.txt'), lockFile);
    await commitCurrentLock();
    await assert.rejects(prepareImageBuild(args(f)), { message: 'BASE_IMAGES_INVALID' }, 'tracked lock symlink');

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
    await assert.rejects(prepareImageBuild({ ...args(f), descriptor: null }), { message: 'SOURCE_IDENTITY_MISMATCH' });
    for (const bytes of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, 2 ** 40 + 1, '19']) {
      const descriptor = structuredClone(f.descriptor); descriptor.jar.bytes = bytes;
      await assert.rejects(prepareImageBuild(args(f, descriptor)), { message: 'JAR_INVALID' });
    }
    const rootFile = path.join(f.root, 'ops/release/version.txt');
    await assert.rejects(prepareImageBuild({ ...args(f), repoRoot: rootFile }), { message: 'INPUT_INVALID' });
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
    await assert.rejects(prepareImageBuild(args(f)), { message: 'TEMPLATE_INVALID' });
  } finally { await rm(alias, { force: true }); await rm(artifactAlias, { force: true }); await f.cleanup(); }
});

test('拒绝clean tree中的ignored模板替身', async () => {
  const f = await fixture();
  try {
    git(f.root, ['rm', '-q', 'ops/docker/bootstrap.sh']);
    await writeFile(path.join(f.root, '.gitignore'), '/artifact/\n/ops/docker/bootstrap.sh\n');
    git(f.root, ['add', '.gitignore']);
    git(f.root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'ignore removed template']);
    f.descriptor.sourceCommit = git(f.root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    await writeFile(path.join(f.root, 'ops/docker/bootstrap.sh'), 'ignored replacement\n');
    assert.equal(git(f.root, ['status', '--porcelain=v1', '--untracked-files=all'], { encoding: 'utf8' }).trim(), '');
    await assert.rejects(prepareImageBuild(args(f)), { message: 'TEMPLATE_INVALID' });
  } finally { await f.cleanup(); }
});
