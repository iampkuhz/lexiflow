import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { chmod, lstat, mkdir, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runDocker } from '../build-command.mjs';
import { buildCandidateImages } from '../build-execution.mjs';
import { resolveBuildIdentity } from '../version.mjs';
import { jarBytes, makeZip } from './zip-fixture.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const templates = ['ops/docker/.dockerignore', 'ops/docker/Dockerfile', 'ops/docker/Dockerfile.postgres', 'ops/docker/bootstrap.sh', 'ops/docker/entrypoint.sh'];
const hash = (data) => createHash('sha256').update(data).digest('hex');
const ids = { 'linux/amd64': ['sha256:' + 'a'.repeat(64), 'sha256:' + 'b'.repeat(64)], 'linux/arm64': ['sha256:' + 'c'.repeat(64), 'sha256:' + 'd'.repeat(64)] };
const finalIds = { 'linux/amd64': ['sha256:' + 'e'.repeat(64), 'sha256:' + 'f'.repeat(64)], 'linux/arm64': ['sha256:' + '1'.repeat(64), 'sha256:' + '2'.repeat(64)] };
const git = (root, args, options = {}) => execFileSync('git', ['-C', root, ...args], { timeout: 10_000, ...options });

async function fixture() {
  const root = await realpath(await import('node:fs/promises').then(({ mkdtemp }) => mkdtemp(path.join(os.tmpdir(), 'lexiflow-execution-'))));
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await mkdir(path.join(root, 'ops/docker'), { recursive: true });
  for (const relative of templates) await writeFile(path.join(root, relative), await readFile(path.join(repo, relative)));
  const syntheticLock = ['linux/amd64', 'linux/arm64'].map((platform) => ({ platform,
    javaRuntime: { reference: `registry.example.invalid/library/java@${ids[platform][0]}`, imageId: ids[platform][0] },
    postgresRuntime: { reference: `registry.example.invalid/library/postgres@${ids[platform][1]}`, imageId: ids[platform][1] } }));
  await writeFile(path.join(root, 'ops/docker/base-images.json'), `${JSON.stringify({ schemaVersion: 1, baseImages: syntheticLock }, null, 2)}\n`);
  for (const name of ['version.mjs', 'manifest.mjs']) await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repo, 'ops/release', name)));
  await writeFile(path.join(root, 'ops/release/version.txt'), '2.0.0\n');
  await writeFile(path.join(root, '.gitignore'), '/artifact/\n');
  execFileSync('git', ['init', '-q', root], { timeout: 10_000 });
  git(root, ['add', '.']); git(root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture']);
  const sourceCommit = git(root, ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const artifactRoot = path.join(root, 'artifact'); await mkdir(artifactRoot);
  const jar = jarBytes(resolveBuildIdentity(root)); await writeFile(path.join(artifactRoot, 'app.jar'), jar);
  const ref = (name, letter) => `registry.example.invalid/library/${name}@sha256:${letter.repeat(64)}`;
  const descriptor = {
    schemaVersion: 1, softwareVersion: '2.0.0', sourceCommit,
    jar: { path: 'app.jar', bytes: jar.length, sha256: hash(jar) },
    baseImages: structuredClone(syntheticLock),
  };
  const refs = Object.fromEntries(descriptor.baseImages.flatMap(({ platform, javaRuntime, postgresRuntime }) => [[javaRuntime.reference, { platform, imageId: javaRuntime.imageId }], [postgresRuntime.reference, { platform, imageId: postgresRuntime.imageId }]]));
  const outputParent = await realpath(await import('node:fs/promises').then(({ mkdtemp }) => mkdtemp(path.join(os.tmpdir(), 'lexiflow-output-'))));
  const fakeRoot = await import('node:fs/promises').then(({ mkdtemp }) => mkdtemp(path.join(os.tmpdir(), 'lexiflow-fake-docker-')));
  const bin = path.join(fakeRoot, 'bin'); await mkdir(bin);
  await writeFile(path.join(fakeRoot, 'state.json'), JSON.stringify({ mode: 'ok', ids, finalIds, refs }));
  const driver = path.join(fakeRoot, 'fake-docker.mjs');
  await writeFile(driver, `
import fs from 'node:fs'; import path from 'node:path'; import { spawn } from 'node:child_process';
const root = path.dirname(new URL(import.meta.url).pathname); const state = JSON.parse(fs.readFileSync(path.join(root, 'state.json'), 'utf8'));
const raw = process.argv.slice(2); const args = raw.slice(4); const op = args[0];
fs.appendFileSync(path.join(root, 'calls.jsonl'), JSON.stringify({ args: raw, op, mode: state.mode, cwd: process.cwd(), env: { PATH: process.env.PATH, HOME: process.env.HOME, LANG: process.env.LANG, LC_ALL: process.env.LC_ALL, DOCKER_HOST: process.env.DOCKER_HOST, DOCKER_CONFIG: process.env.DOCKER_CONFIG, COMPOSE_FILE: process.env.COMPOSE_FILE, BUILDX_CONFIG: process.env.BUILDX_CONFIG, SSH_AUTH_SOCK: process.env.SSH_AUTH_SOCK, BUILD_SECRET: process.env.BUILD_SECRET } }) + '\\n');
if (state.mode === 'hang-with-child') { spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'inherit' }); setInterval(()=>{},1000); }
else if (state.mode === 'overflow') { (async () => { for (let n = 0; n < 18; n += 1) await new Promise((resolve) => process.stdout.write('x'.repeat(65536), resolve)); process.exit(0); })(); }
else {
if (state.failOp === op && (!state.failSub || args[1] === state.failSub)) { process.stderr.write('private synthetic failure'); process.exit(29); }
if (op === 'version') { if (state.preseedMarker) fs.writeFileSync(path.join(process.cwd(), 'candidate.json'), 'external marker'); process.stdout.write(JSON.stringify({ Client: { Version: 'synthetic' }, Server: { Version: 'synthetic' } })); process.exit(0); }
if (op === 'info') { const infoCalls = fs.existsSync(path.join(root, 'calls.jsonl')) ? fs.readFileSync(path.join(root, 'calls.jsonl'), 'utf8').split('\\n').filter((line) => line && JSON.parse(line).op === 'info').length : 0; process.stdout.write(JSON.stringify({ OSType: 'linux', Architecture: infoCalls > 1 ? (state.finalInfoArchitecture ?? state.daemonArchitecture ?? 'amd64') : (state.daemonArchitecture ?? 'amd64') })); process.exit(0); }
if (op === 'image' && args[1] === 'inspect') {
  const target = args[2]; let platform; let imageId;
  if (state.refs[target]) { platform = state.refs[target].platform; imageId = state.refs[target].imageId; }
  for (const [p, bases] of Object.entries(state.ids)) { const finals = state.finalIds[p]; if (bases.includes(target)) { platform = p; imageId = target; } if (finals.includes(target)) { platform = p; imageId = target; } }
  if (!platform) { process.stderr.write('unknown synthetic image'); process.exit(30); }
  const architecture = platform.endsWith('amd64') ? 'amd64' : 'arm64';
  const value = { Id: imageId, Os: 'linux', Architecture: state.badArchitecture && state.badArchitecture === target ? 'arm64' : architecture };
  if (state.builtImages?.[target]) {
    value.Config = { Labels: { ...state.builtImages[target] } };
    if (state.labelMode === 'missing') delete value.Config.Labels['io.lexiflow.build-id'];
    if (state.labelMode === 'bad') value.Config.Labels['io.lexiflow.source-sha256'] = '0'.repeat(64);
    if (state.labelMode === 'conflict') value.Config.Labels['org.opencontainers.image.version'] = 'conflicting';
  }
  process.stdout.write(JSON.stringify([value])); process.exit(0);
}
if (op === 'build') {
  const platform = args[args.indexOf('--platform') + 1]; const iid = args[args.indexOf('--iidfile') + 1];
  const expected = ['.dockerignore','Dockerfile','Dockerfile.postgres','bootstrap.sh','entrypoint.sh','lexiflow-api.jar'];
  const actual = fs.readdirSync(process.cwd()).sort(); fs.appendFileSync(path.join(root, 'contexts.jsonl'), JSON.stringify({ platform, files: actual }) + '\\n');
  if (JSON.stringify(actual) !== JSON.stringify(expected)) { process.stderr.write('wrong context'); process.exit(31); }
  const imageIndex = iid.startsWith('api') ? 0 : 1;
  const labelArgs = [];
  for (let i = 0; i < args.length; i += 1) if (args[i] === '--label') labelArgs.push(args[i + 1]);
  state.builtImages ??= {};
  state.builtImages[state.finalIds[platform][imageIndex]] = Object.fromEntries(labelArgs.map((entry) => { const at = entry.indexOf('='); return [entry.slice(0, at), entry.slice(at + 1)]; }));
  fs.writeFileSync(path.join(root, 'state.json'), JSON.stringify(state));
  if (state.iidMode === 'symlink') fs.symlinkSync('/etc/hosts', path.join(process.cwd(), iid));
  else if (state.iidMode === 'bad') fs.writeFileSync(path.join(process.cwd(), iid), 'not-an-image-id');
  else if (state.iidMode === 'empty') fs.writeFileSync(path.join(process.cwd(), iid), '');
  else { const index = iid.startsWith('api') ? 0 : 1; fs.writeFileSync(path.join(process.cwd(), iid), state.finalIds[platform][index] + '\\n'); }
  process.exit(0);
}
if (op === 'image' && args[1] === 'save') {
  const output = args[args.indexOf('--output') + 1];
  if (state.archiveMode === 'symlink') fs.symlinkSync('/etc/hosts', output);
  else fs.writeFileSync(output, state.archiveMode === 'empty' ? '' : Buffer.from('synthetic image archive:' + args.at(-1)));
  process.exit(0);
}
process.stderr.write('unrecognized synthetic docker command'); process.exit(32);
}
`);
  const executable = path.join(bin, 'docker');
  await writeFile(executable, `#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' '${driver.replaceAll("'", "'\\''")}' "$@"\n`);
  await chmod(executable, 0o700);
  return { root, artifactRoot, descriptor, outputParent, fakeRoot, bin, cleanup: async () => { await rm(root, { recursive: true, force: true }); await rm(outputParent, { recursive: true, force: true }); await rm(fakeRoot, { recursive: true, force: true }); } };
}

function withFakePath(f, callback) {
  const old = process.env.PATH; process.env.PATH = `${f.bin}${path.delimiter}${old ?? ''}`;
  return Promise.resolve().then(callback).finally(() => { if (old === undefined) delete process.env.PATH; else process.env.PATH = old; });
}
async function state(f) { return JSON.parse(await readFile(path.join(f.fakeRoot, 'state.json'), 'utf8')); }
async function setState(f, patch) { await writeFile(path.join(f.fakeRoot, 'state.json'), JSON.stringify({ ...(await state(f)), ...patch })); }
async function calls(f) { return (await readFile(path.join(f.fakeRoot, 'calls.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map((line) => JSON.parse(line)); }
const buildInput = (f, extra = {}) => ({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent, endpoint: 'unix:///tmp/synthetic-docker.sock', platform: 'linux/amd64', ...extra });

test('buildCandidateImages 执行固定预检/构建/导出并留下四镜像候选', async () => {
  const f = await fixture();
  try {
    await withFakePath(f, async () => {
      process.env.DOCKER_HOST = 'tcp://attacker.invalid'; process.env.DOCKER_CONFIG = '/attacker'; process.env.COMPOSE_FILE = '/attacker'; process.env.BUILDX_CONFIG = '/attacker'; process.env.SSH_AUTH_SOCK = '/attacker'; process.env.BUILD_SECRET = 'must-not-pass';
      const result = await buildCandidateImages(buildInput(f));
      const execution = await calls(f); const operations = execution.map((entry) => entry.op);
      assert.deepEqual(operations.slice(0, 2), ['version', 'info']);
      const baseChecks = execution.filter(({ args }) => args.includes('inspect') && args.some((arg) => arg.startsWith('registry.example.invalid/')));
      assert.equal(baseChecks.length, 2);
      const lastBaseInspection = Math.max(...execution.map((entry, index) => baseChecks.includes(entry) ? index : -1));
      assert.ok(lastBaseInspection < operations.indexOf('build'));
      assert.equal(operations.filter((op) => op === 'build').length, 2);
      assert.equal(execution.filter(({ args }) => args.includes('save')).length, 2);
      assert.ok(baseChecks.every(({ args }) => args[0] === '--config' && args[2] === '--host' && args[3] === 'unix:///tmp/synthetic-docker.sock'));
      assert.ok(execution.every(({ env, args }) => env.HOME === args[1] && env.LANG === 'C' && env.LC_ALL === 'C'
        && env.DOCKER_HOST === undefined && env.DOCKER_CONFIG === undefined && env.COMPOSE_FILE === undefined && env.BUILDX_CONFIG === undefined && env.SSH_AUTH_SOCK === undefined && env.BUILD_SECRET === undefined));
      const contexts = (await readFile(path.join(f.fakeRoot, 'contexts.jsonl'), 'utf8')).trim().split('\n').map((line) => JSON.parse(line));
      assert.equal(contexts.length, 2); assert.ok(contexts.every(({ files }) => files.length === 6));
      const { candidate, candidateDirectory } = result;
      assert.deepEqual(Object.keys(candidate), ['schemaVersion', 'kind', 'buildIdentity', 'softwareVersion', 'sourceCommit', 'platform', 'buildInputSha256', 'baseImages', 'artifacts']);
      assert.deepEqual(candidate.buildIdentity, resolveBuildIdentity(f.root));
      assert.equal(candidate.platform, 'linux/amd64');
      assert.equal(candidate.baseImages.length, 1);
      assert.equal(candidate.artifacts.length, 2);
      assert.deepEqual(candidate.artifacts.map(({ path: itemPath }) => itemPath), [...candidate.artifacts.map(({ path: itemPath }) => itemPath)].sort());
      for (const item of candidate.artifacts) {
        const bytes = await readFile(path.join(candidateDirectory, item.path));
        assert.equal(bytes.length, item.bytes); assert.equal(hash(bytes), item.sha256); assert.equal(item.imageDigest, item.role === 'api-image' ? finalIds[item.platform][0] : finalIds[item.platform][1]);
      }
      assert.equal((await readFile(path.join(candidateDirectory, 'candidate.json'), 'utf8')), `${JSON.stringify(candidate, null, 2)}\n`);
      assert.equal(await lstat(path.join(candidateDirectory, 'config')).then(() => false, () => true), true);
      assert.equal(await lstat(path.join(candidateDirectory, 'work')).then(() => false, () => true), true);
    });
  } finally { delete process.env.DOCKER_HOST; delete process.env.DOCKER_CONFIG; delete process.env.COMPOSE_FILE; delete process.env.BUILDX_CONFIG; delete process.env.SSH_AUTH_SOCK; delete process.env.BUILD_SECRET; await f.cleanup(); }
});

test('arm64-only descriptor 生成绑定子集摘要的原生候选', async () => {
  const f = await fixture();
  try {
    f.descriptor.baseImages = f.descriptor.baseImages.filter((item) => item.platform === 'linux/arm64');
    await setState(f, { daemonArchitecture: 'arm64' });
    await withFakePath(f, async () => {
      const result = await buildCandidateImages(buildInput(f, { platform: 'linux/arm64' }));
      assert.equal(result.candidate.platform, 'linux/arm64');
      assert.equal(result.candidate.baseImages[0].platform, 'linux/arm64');
      assert.equal((await calls(f)).filter(({ op }) => op === 'build').length, 2);
    });
  } finally { await f.cleanup(); }
});

test('inspect 实际镜像标签而非仅信任 build argv', async () => {
  for (const labelMode of ['missing', 'bad', 'conflict']) {
    const f = await fixture();
    try {
      await setState(f, { labelMode });
      await withFakePath(f, async () => assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_LABEL_MISMATCH' }));
    } finally { await f.cleanup(); }
  }
});

test('拒绝参数、端点、输出目录边界及不支持daemon架构', async () => {
  const f = await fixture();
  try {
    for (const endpoint of ['', 'tcp://127.0.0.1:2375', 'ssh://host', 'relative', 'unix://relative', 'unix:///tmp/a\0b']) {
      await assert.rejects(buildCandidateImages(buildInput(f, { endpoint })), { message: 'ENDPOINT_INVALID' });
    }
    await assert.rejects(buildCandidateImages({ ...buildInput(f), surprise: true }), { message: 'BUILD_INPUT_INVALID' });
    const missingPlatform = buildInput(f); delete missingPlatform.platform;
    await assert.rejects(buildCandidateImages(missingPlatform), { message: 'BUILD_INPUT_INVALID' });
    await assert.rejects(buildCandidateImages(buildInput(f, { outputParent: f.root })), { message: 'OUTPUT_PARENT_INVALID' });
    await assert.rejects(buildCandidateImages(buildInput(f, { outputParent: 'relative' })), { message: 'OUTPUT_PARENT_INVALID' });
    await assert.rejects(buildCandidateImages(buildInput(f, { outputParent: null })), { message: 'OUTPUT_PARENT_INVALID' });
    const parentAlias = path.join(f.outputParent, 'alias'); await symlink(f.outputParent, parentAlias);
    await assert.rejects(buildCandidateImages(buildInput(f, { outputParent: parentAlias })), { message: 'BUILD_INPUT_INVALID' });
    await rm(parentAlias);
    await withFakePath(f, async () => {
      await setState(f, { daemonArchitecture: 'arm64' });
      const driverPath = path.join(f.fakeRoot, 'fake-docker.mjs');
      let driver = await readFile(driverPath, 'utf8');
      driver = driver.replace("Architecture: 'amd64'", "Architecture: state.daemonArchitecture ?? 'amd64'");
      await writeFile(driverPath, driver);
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'DOCKER_PLATFORM_MISMATCH' });
      const initialMismatchCalls = await calls(f);
      assert.deepEqual(initialMismatchCalls.map(({ op }) => op), ['version', 'info']);
      assert.equal(initialMismatchCalls.some(({ args }) => args.includes('inspect') || args.includes('build') || args.includes('save')), false);
      assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(f.outputParent)), []);
      await setState(f, { daemonArchitecture: null, preseedMarker: true });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'BUILD_CANDIDATE_FAILED' });
      assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(f.outputParent)), []);
    });
  } finally { await f.cleanup(); }
});

test('最终 Docker info 架构漂移时在发布候选标记前拒绝', async () => {
  const f = await fixture();
  try {
    await withFakePath(f, async () => {
      await setState(f, { finalInfoArchitecture: 'arm64' });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'DOCKER_PLATFORM_MISMATCH' });
      const execution = await calls(f);
      assert.equal(execution.filter(({ op }) => op === 'info').length, 2);
      assert.equal(execution.some(({ args }) => args.includes('inspect') || args.includes('build') || args.includes('save')), true);
      assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(f.outputParent)), []);
    });
  } finally { await f.cleanup(); }
});

test('完整计划摘要跨平台稳定且 JAR 字节及 descriptor 同步变化后改变', async () => {
  const f = await fixture();
  try {
    await withFakePath(f, async () => {
      const amd64 = await buildCandidateImages(buildInput(f, { platform: 'linux/amd64' }));
      await setState(f, { daemonArchitecture: 'arm64' });
      const arm64 = await buildCandidateImages(buildInput(f, { platform: 'linux/arm64' }));
      assert.equal(amd64.candidate.buildInputSha256, arm64.candidate.buildInputSha256);
      const jarPath = path.join(f.artifactRoot, f.descriptor.jar.path);
      const identity = resolveBuildIdentity(f.root);
      const changedJar = makeZip([
        ['META-INF/lexiflow-build.json', `${JSON.stringify(identity)}\n`],
        ['META-INF/lexiflow-version.txt', `${identity.softwareVersion}\n`],
        ['BOOT-INF/classes/synthetic/extra.class', 'synthetic changed application jar'],
      ]);
      await writeFile(jarPath, changedJar);
      f.descriptor.jar.bytes = changedJar.length;
      f.descriptor.jar.sha256 = hash(changedJar);
      await setState(f, { daemonArchitecture: 'amd64' });
      const changed = await buildCandidateImages(buildInput(f, { platform: 'linux/amd64' }));
      assert.notEqual(changed.candidate.buildInputSha256, amd64.candidate.buildInputSha256);
    });
  } finally { await f.cleanup(); }
});

test('拒绝基础镜像错配、构建失败及坏/符号链接 IID', async () => {
  const f = await fixture();
  const neighbor = path.join(f.outputParent, 'neighbor'); await mkdir(neighbor); await writeFile(path.join(neighbor, 'keep.txt'), 'keep');
  try {
    await withFakePath(f, async () => {
      await setState(f, { badArchitecture: f.descriptor.baseImages[0].javaRuntime.reference });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'BASE_IMAGE_MISMATCH' });
      assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(neighbor)), ['keep.txt']);
      await setState(f, { badArchitecture: null, failOp: 'build', failSub: null });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'DOCKER_COMMAND_FAILED' });
      await setState(f, { failOp: null, failSub: null, iidMode: 'bad' });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_ID_INVALID' });
      await setState(f, { iidMode: 'symlink' });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_ID_INVALID' });
      assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(neighbor)), ['keep.txt']);
    });
  } finally { await f.cleanup(); }
});

for (const driftPath of ['ops/release/version.txt', 'ops/docker/base-images.json']) {
test(`拒绝空/符号链接归档并在 ${driftPath} 漂移后不留下候选标记`, async () => {
  const f = await fixture();
  try {
    await withFakePath(f, async () => {
      await setState(f, { archiveMode: 'empty' });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_ARCHIVE_INVALID' });
      await setState(f, { archiveMode: 'symlink' });
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_ARCHIVE_INVALID' });
      await setState(f, { archiveMode: null, mutateLockOnSave: true, repoRoot: f.root, driftPath });
      const driverPath = path.join(f.fakeRoot, 'fake-docker.mjs');
      let driver = await readFile(driverPath, 'utf8');
      driver = driver.replace("if (op === 'image' && args[1] === 'save') {", "if (op === 'image' && args[1] === 'save') { if (state.mutateLockOnSave) fs.appendFileSync(path.join(state.repoRoot, state.driftPath), '\\n');")
        .replace("const state = JSON.parse(fs.readFileSync(path.join(root, 'state.json'), 'utf8'));", "const state = JSON.parse(fs.readFileSync(path.join(root, 'state.json'), 'utf8')); state.repoRoot = state.repoRoot || process.env.SYNTHETIC_REPO;");
      await writeFile(driverPath, driver);
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'BUILD_INPUT_CHANGED' });
      assert.ok(!(await import('node:fs/promises').then(({ readdir }) => readdir(f.outputParent))).some((name) => name.startsWith('.lexiflow-image-candidate-')));
    });
  } finally { await f.cleanup(); }
});
}

test('runDocker固定argv、屏蔽环境并有界处理缺工具、非零、输出溢出与后代持管超时', async () => {
  const f = await fixture(); const configDirectory = path.join(f.fakeRoot, 'private-config');
  await mkdir(configDirectory);
  try {
    await withFakePath(f, async () => {
      process.env.DOCKER_HOST = 'attacker'; process.env.SSH_AUTH_SOCK = 'attacker';
      const output = await runDocker(['version', '--format', '{{json .}}'], { cwd: f.root, configDirectory, endpoint: 'unix:///tmp/docker.sock', timeoutMs: 3_000 });
      assert.deepEqual(JSON.parse(output), { Client: { Version: 'synthetic' }, Server: { Version: 'synthetic' } });
      const [entry] = await calls(f);
      assert.deepEqual(entry.args.slice(0, 4), ['--config', configDirectory, '--host', 'unix:///tmp/docker.sock']);
      assert.equal(entry.env.DOCKER_HOST, undefined); assert.equal(entry.env.SSH_AUTH_SOCK, undefined);
      await setState(f, { failOp: 'version' });
      await assert.rejects(runDocker(['version'], { cwd: f.root, configDirectory, endpoint: 'unix:///tmp/docker.sock', timeoutMs: 1_000 }), { message: 'DOCKER_COMMAND_FAILED' });
      await setState(f, { mode: 'overflow', failOp: null });
      let overflowResult; try { overflowResult = await runDocker(['version'], { cwd: f.root, configDirectory, endpoint: 'unix:///tmp/docker.sock', timeoutMs: 3_000 }); } catch (error) { assert.equal(error.message, 'DOCKER_OUTPUT_LIMIT'); overflowResult = null; }
      assert.equal(overflowResult, null);
      await setState(f, { mode: 'hang-with-child' });
      const started = Date.now();
      await assert.rejects(runDocker(['version'], { cwd: f.root, configDirectory, endpoint: 'unix:///tmp/docker.sock', timeoutMs: 150 }), { message: 'DOCKER_COMMAND_TIMEOUT' });
      assert.ok(Date.now() - started < 3_000);
      const oldPath = process.env.PATH; process.env.PATH = '';
      try { await assert.rejects(runDocker(['version'], { cwd: f.root, configDirectory, endpoint: 'unix:///tmp/docker.sock', timeoutMs: 1_000 }), { message: 'DOCKER_UNAVAILABLE' }); }
      finally { process.env.PATH = oldPath; }
    });
  } finally { delete process.env.DOCKER_HOST; delete process.env.SSH_AUTH_SOCK; await f.cleanup(); }
});

test('后续镜像导出篡改先前归档时拒绝发布候选记录', async () => {
  const f = await fixture();
  try {
    const driverPath = path.join(f.fakeRoot, 'fake-docker.mjs');
    const driver = await readFile(driverPath, 'utf8');
    await writeFile(driverPath, driver.replace(
      "const output = args[args.indexOf('--output') + 1];",
      "const output = args[args.indexOf('--output') + 1]; if (output.endsWith('linux-amd64/postgres-image.tar')) fs.appendFileSync(path.join(process.cwd(), 'images/linux-amd64/api-image.tar'), 'tampered');",
    ));
    await withFakePath(f, async () => {
      await assert.rejects(buildCandidateImages(buildInput(f)), { message: 'IMAGE_ARCHIVE_CHANGED' });
    });
    assert.deepEqual(await import('node:fs/promises').then(({ readdir }) => readdir(f.outputParent)), []);
  } finally { await f.cleanup(); }
});
