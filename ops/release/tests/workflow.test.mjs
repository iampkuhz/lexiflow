import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, lstat, rm, symlink, realpath, chmod } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { executeFormal, prepareWorkflow, resumePromotion, runCommand } from '../workflow/run-release-workflow.mjs';
import { normalizeDarwinTempDirectory, resolveFormalInvocation, resolveReleaseExecutionEnvironment } from '../workflow/release-execution-environment.mjs';
import { resolveHostBinding } from '../host_resources/release-host-identity.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const repo = process.cwd();
const ident = (commit, version = '1.2.3') => {
  const sourceSha256 = sha(commit);
  const base = { baseVersion: version, sourceCommit: commit, sourceSha256, dirty: false };
  return { schemaVersion: 1, ...base, softwareVersion: version, chromeVersion: `${version}.1`,
    buildId: sha(JSON.stringify(base)), channel: 'release' };
};
const targetIdentity = ident('a'.repeat(40));
const notice = Buffer.from('Approved license notice');
const components = ['LexiFlow', 'API-runtime', 'PostgreSQL', 'extension-third-party', 'dataset'];

async function fixture(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-workflow-')));
  t.after(async () => rm(root, { recursive: true, force: true }));
  const outputParent = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-workflow-output-')));
  t.after(async () => rm(outputParent, { recursive: true, force: true }));
  const noticesRoot = path.join(root, 'notices');
  await mkdir(noticesRoot);
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await writeFile(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  for (const directory of ['build', 'integrity', 'runtime']) await mkdir(path.join(root, 'ops/release', directory), { recursive: true });
  await mkdir(path.join(root, 'ops/docker'), { recursive: true });
  await mkdir(path.join(root, 'infra/postgres'), { recursive: true });
  await writeFile(path.join(root, 'ops/docker/compose.yaml'), 'compose');
  await writeFile(path.join(root, 'infra/postgres/schema.sql'), 'schema');
  await writeFile(path.join(noticesRoot, 'notice.txt'), notice);
  const licenses = components.map((component, index) => ({
    id: `license-${index}`, component, licenseId: 'MIT', licenseName: 'MIT',
    sourceUrl: 'https://example.org/license', noticePath: 'notice.txt',
    noticeBytes: notice.length, noticeSha256: sha(notice),
  }));
  await writeFile(path.join(root, 'ops/release/distribution-licenses.json'),
    JSON.stringify({ schema_version: 'lexiflow.distribution-licenses.v1', licenses }));
  const datasetFile = path.join(root, 'dataset.zip');
  await writeFile(datasetFile, 'approved dataset');
  const request = path.join(root, 'request.json');
  const body = { releaseTag: 'v1.2.3', dataset: { path: datasetFile, sha256: sha('approved dataset'),
    releaseId: 'r1', preparationId: 'p1', ruleId: 'rule1', sqlVersion: 'sql1' },
  noticesRoot, outputParent,
  endpoint: 'unix:///tmp/podman.sock' };
  await writeFile(request, JSON.stringify(body));
  return { root, outputParent, request, body, licenses };
}

function boundaries(f, overrides = {}) {
  const calls = [];
  const imageDirectory = path.join(f.outputParent, 'images', 'image-candidate');
  const candidateDirectory = path.join(f.outputParent, 'releases', 'release-candidate');
  const imageRecords = [{ role: 'runtime-image', platform: 'linux/arm64', path: 'images/linux-arm64/runtime-image.tar',
    bytes: 3, sha256: sha('runtime'), imageDigest: `sha256:${sha('runtime-image')}` }];
  const runCommand = async (command, args, settings) => {
    calls.push(['command', command, args, settings]);
    if (args.includes(':api:bootJar')) {
      const folder = path.join(f.root, 'backend/product/api/build/libs');
      await mkdir(folder, { recursive: true });
      await writeFile(path.join(folder, 'api-1.2.3.jar'), 'real jar bytes');
    }
    if (command === 'node' && args.includes('extension/scripts/packaging/package-extension-release.mjs')) {
      const zip = Buffer.from('real extension zip');
      const folder = path.join(f.root, 'tmp/releases/1.2.3', targetIdentity.sourceCommit);
      await mkdir(folder, { recursive: true });
      await writeFile(path.join(folder, 'lexiflow-extension-1.2.3.zip'), zip);
      return JSON.stringify({ buildIdentity: targetIdentity, softwareVersion: '1.2.3',
        sourceCommit: targetIdentity.sourceCommit, filename: 'lexiflow-extension-1.2.3.zip',
        bytes: zip.length, sha256: sha(zip) });
    }
    return '';
  };
  const buildCandidateImages = async input => {
    calls.push(['images', input]);
    await mkdir(imageDirectory);
    await writeFile(path.join(imageDirectory, 'candidate.json'),
      JSON.stringify({ platform: 'linux/arm64', artifacts: imageRecords }));
    return { candidateDirectory: imageDirectory };
  };
  const assembleReleaseCandidate = async input => {
    calls.push(['assemble', input]);
    await mkdir(candidateDirectory);
    await writeFile(path.join(candidateDirectory, 'candidate.json'), 'target candidate');
    return { candidateDirectory };
  };
  const verifyReleaseCandidate = async input => {
    calls.push(['verify', input]);
    return { candidateSha256: sha('target candidate'), buildIdentity: targetIdentity };
  };
  const environmentResolver = ({ root }) => ({
    PATH: '/controlled/bin', HOME: '/controlled/home', USER: 'release-user', LOGNAME: 'release-user',
    TMPDIR: '/tmp', TEMP: '/tmp', TMP: '/tmp', LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8', TZ: 'UTC',
    PYTHONDONTWRITEBYTECODE: '1', PYTHONHASHSEED: '0', JAVA_HOME: '/controlled/jdk-25',
    LEXIFLOW_JAVA_HOME: '/controlled/jdk-25',
    LEXIFLOW_POSTGRES_TEST_JDBC_URL: 'jdbc:postgresql://127.0.0.1:5432/test',
    LEXIFLOW_REDIS_TEST_ENDPOINT: 'redis://127.0.0.1:6379/0',
    LEXIFLOW_RELEASE_ARM64_DOCKER_HOST: 'unix:///tmp/podman.sock',
    LEXIFLOW_RELEASE_GRADLE_CACHE: '/controlled/gradle-cache',
    LEXIFLOW_RELEASE_NPM_CACHE: '/controlled/npm-cache',
    LEXIFLOW_CANDIDATE_RUNTIME_REQUEST: path.join(root, 'tmp/quality/release-workflow/1.2.3/candidate-runtime-request.json'),
  });
  return { calls, candidateDirectory, options: { root: f.root, platform: 'darwin', arch: 'arm64',
    tagChecker: () => targetIdentity, identityReader: () => targetIdentity, ledgerTrackedCheck: () => {},
    endpointChecker: async () => {},
    baseImageLoader: async () => [{ platform: 'linux/amd64' }, { platform: 'linux/arm64' }],
    runCommand, buildCandidateImages, environmentResolver,
    assembleReleaseCandidate, verifyReleaseCandidate, ...overrides } };
}

async function releaseEnvironmentFixture(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-release-env-')));
  t.after(async () => rm(root, { recursive: true, force: true }));
  const pythonBin = path.join(root, '.local/lexiflow-python/bin');
  const javaHome = path.join(root, 'private-jdk');
  const cacheGradle = path.join(root, 'gradle-cache'), cacheNpm = path.join(root, 'npm-cache');
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await writeFile(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  const quality = path.join(root, 'tmp/quality/release-workflow/1.2.3');
  await Promise.all([mkdir(pythonBin, { recursive: true }), mkdir(path.join(javaHome, 'bin'), { recursive: true }),
    mkdir(cacheGradle), mkdir(cacheNpm), mkdir(quality, { recursive: true })]);
  await writeFile(path.join(pythonBin, 'python3'), '#!/bin/sh\nexit 0\n');
  await chmod(path.join(pythonBin, 'python3'), 0o700);
  await writeFile(path.join(javaHome, 'bin/java'), '#!/bin/sh\nexit 0\n');
  await chmod(path.join(javaHome, 'bin/java'), 0o700);
  await writeFile(path.join(quality, 'candidate-runtime-request.json'), '{}\n');
  const source = {
    LEXIFLOW_RELEASE_NODE_BIN: path.dirname(process.execPath),
    LEXIFLOW_JAVA_HOME: javaHome,
    LEXIFLOW_POSTGRES_TEST_JDBC_URL: 'jdbc:postgresql://127.0.0.1:5432/one',
    LEXIFLOW_REDIS_TEST_ENDPOINT: 'redis://127.0.0.1:6379/0',
    LEXIFLOW_RELEASE_ARM64_DOCKER_HOST: 'unix:///tmp/podman.sock',
    LEXIFLOW_RELEASE_GRADLE_CACHE: cacheGradle,
    LEXIFLOW_RELEASE_NPM_CACHE: cacheNpm,
    PATH: '/ambient/path', HOME: '/ambient/home', USER: 'ambient-user', LOGNAME: 'ambient-logname',
    TERM: 'ambient-term', TMPDIR: '/ambient/temp', TEMP: '/ambient/temp2', TMP: '/ambient/temp3',
    LANG: 'ambient-locale', LC_ALL: 'ambient-locale', TZ: 'ambient-zone',
    GITHUB_TOKEN: 'synthetic-token', CODEX_HOME: '/trusted/codex-home',
    CODEX_THREAD_ID: 'thread-native', CODEX_SESSION_ID: 'session-native', CODEX_TMPDIR: '/ambient/codex-temp',
    LEXIFLOW_CANDIDATE_RUNTIME_REQUEST: '/attacker/request.json',
  };
  return { root, javaHome, source };
}

test('release environment canonicalizes ambient values and fails closed on missing capability', async t => {
  const f = await releaseEnvironmentFixture(t);
  const first = resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'formal', source: f.source });
  const ambientChanged = { ...f.source, PATH: '/other/path', HOME: '/other/home', USER: 'other', LOGNAME: 'other',
    TERM: 'other-term', TMPDIR: '/other/tmp', TEMP: '/other/temp', TMP: '/other/tmp2',
    LANG: 'C', LC_ALL: 'C', TZ: 'Pacific/Honolulu' };
  const second = resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'formal', source: ambientChanged });
  for (const name of ['PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'TZ'])
    assert.equal(first[name], second[name], `${name} must be normalized`);
  assert.equal(first.PATH.split(path.delimiter)[0], path.join(f.root, '.local/lexiflow-python/bin'));
  assert.equal(first.PATH.split(path.delimiter)[1], path.dirname(process.execPath));
  assert.equal(first.TMPDIR, process.platform === 'darwin'
    ? normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: execFileSync('/usr/bin/getconf', ['DARWIN_USER_TEMP_DIR'], { encoding: 'utf8' }) })
    : await realpath('/tmp'));
  assert.equal(normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: `${f.root}\n` }), f.root,
    'a real current-user-owned 0700 directory is accepted');
  const promotion = resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'promotion', source: ambientChanged });
  assert.equal(first.TMPDIR, promotion.TMPDIR, 'formal and promotion share the same OS temp root');
  assert.throws(() => normalizeDarwinTempDirectory({ status: 1, signal: null, stdout: '' }), /RELEASE_STANDARD_PATH_INVALID/);
  assert.throws(() => normalizeDarwinTempDirectory({ error: new Error('spawn failed'), status: null, stdout: '' }), /RELEASE_STANDARD_PATH_INVALID/);
  assert.throws(() => normalizeDarwinTempDirectory({ status: 0, signal: 'SIGTERM', stdout: `${f.root}\n` }), /RELEASE_STANDARD_PATH_INVALID/);
  for (const output of ['relative\n', `${f.root}\n${f.root}\n`, `${f.root}\0\n`])
    assert.throws(() => normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: output }), /RELEASE_STANDARD_PATH_INVALID/);
  assert.throws(() => normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: `${path.join(f.root, 'missing')}\n` }), /RELEASE_STANDARD_PATH_INVALID/);
  const ordinaryFile = path.join(f.root, 'ordinary-file');
  await writeFile(ordinaryFile, 'not a directory');
  assert.throws(() => normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: `${ordinaryFile}\n` }), /RELEASE_STANDARD_PATH_INVALID/);
  await chmod(f.root, 0o755);
  assert.throws(() => normalizeDarwinTempDirectory({ status: 0, signal: null, stdout: `${f.root}\n` }), /RELEASE_STANDARD_PATH_INVALID/);
  await chmod(f.root, 0o700);
  assert.equal(first.LEXIFLOW_CANDIDATE_RUNTIME_REQUEST, path.join(f.root, 'tmp/quality/release-workflow/1.2.3/candidate-runtime-request.json'));
  assert.equal(first.CODEX_HOME, '/trusted/codex-home');
  assert.equal(first.CODEX_THREAD_ID, 'thread-native');
  assert.equal(first.CODEX_SESSION_ID, 'session-native');
  for (const forbidden of ['TERM', 'LANGUAGE', 'GITHUB_TOKEN', 'CODEX_TMPDIR', 'LEXIFLOW_RELEASE_NODE_BIN'])
    assert.equal(first[forbidden], undefined, `${forbidden} must not be forwarded`);

  const required = ['LEXIFLOW_RELEASE_NODE_BIN', 'LEXIFLOW_JAVA_HOME', 'LEXIFLOW_POSTGRES_TEST_JDBC_URL', 'LEXIFLOW_REDIS_TEST_ENDPOINT',
    'LEXIFLOW_RELEASE_ARM64_DOCKER_HOST', 'LEXIFLOW_RELEASE_GRADLE_CACHE', 'LEXIFLOW_RELEASE_NPM_CACHE'];
  for (const name of required) {
    const missing = { ...f.source }; delete missing[name];
    assert.throws(() => resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'formal', source: missing }));
  }
  assert.equal(first.GITHUB_TOKEN, undefined);
  await rm(path.join(f.root, 'tmp/quality/release-workflow/1.2.3/candidate-runtime-request.json'));
  assert.throws(() => resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'promotion', source: f.source }), /CANDIDATE_RUNTIME_REQUEST_MISSING/);
});

test('private environment capability still changes Python child environment fingerprint', async t => {
  const python = execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], {
    cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
  const f = await releaseEnvironmentFixture(t);
  const fingerprint = source => {
    const env = resolveReleaseExecutionEnvironment({ root: f.root, purpose: 'promotion', source });
    const code = `from pathlib import Path\nfrom scripts.environment import resolve_check_environment\nfrom scripts.verification.execution.check_runner import build_child_environment, fingerprint_json\nr=resolve_check_environment(Path.cwd(), {'required_environment':['postgres-test-jdbc-url']})\nassert r.diagnostic['status']=='PASS'\ne=build_child_environment(runtime_environment=r.values)\nprint(fingerprint_json({k:e[k] for k in sorted(e)}))`;
    return execFileSync(python, ['-c', code], {
      cwd: repo, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
  };
  const changed = { ...f.source, LEXIFLOW_POSTGRES_TEST_JDBC_URL: 'jdbc:postgresql://127.0.0.1:5432/two' };
  assert.notEqual(fingerprint(f.source), fingerprint(changed));
});

test('formal CLI admits only the four fixed acceptance mappings and propagates command failures', async t => {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-formal-cli-')));
  t.after(async () => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'tmp/quality/release-workflow/1.2.3'), { recursive: true });
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await writeFile(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  const submission = '12345678-1234-4234-8234-123456789abc';
  const validation = '22345678-1234-4234-8234-123456789abc';
  assert.deepEqual(resolveFormalInvocation(['submit', 'LF-TSK-QLT-2420'], root), {
    command: 'python3', args: ['-m', 'scripts.acceptance', 'submit', '--task-id', 'LF-TSK-QLT-2420', '--release-handoff'], cwd: root,
  });
  assert.deepEqual(resolveFormalInvocation(['validate', submission], root).args,
    ['-m', 'scripts.acceptance', 'validate', '--submission-id', submission]);
  assert.deepEqual(resolveFormalInvocation(['review', validation, '/tmp/findings.json'], root).args,
    ['-m', 'scripts.acceptance', 'review', '--validation-id', validation, '--findings-json', '/tmp/findings.json']);
  assert.deepEqual(resolveFormalInvocation(['check', submission], root).args,
    ['-m', 'scripts.acceptance', 'check', '--submission-id', submission]);
  for (const invalid of [[], ['submit', 'bad'], ['submit', 'LF-TSK-QLT-2420', '--repo-root', root],
    ['validate', submission, root], ['review', validation, 'relative.json'], ['check', submission, '--actor', 'fake']])
    assert.throws(() => resolveFormalInvocation(invalid, root), /FORMAL_ARGUMENTS_INVALID/);
  const calls = [];
  const output = await executeFormal(['validate', submission], {
    root,
    environmentResolver: input => ({ CODEX_HOME: '/trusted/home', CODEX_THREAD_ID: 'native-thread', CODEX_SESSION_ID: 'native-session' }),
    run: async (command, args, settings) => { calls.push({ command, args, settings }); return '{"result":"PASS"}'; },
  });
  assert.equal(output, '{"result":"PASS"}');
  assert.equal(calls[0].command, 'python3');
  assert.equal(calls[0].settings.timeout, 5_400_000);
  assert.deepEqual(calls[0].settings.env, { CODEX_HOME: '/trusted/home', CODEX_THREAD_ID: 'native-thread', CODEX_SESSION_ID: 'native-session' });
  const reviewCall = await executeFormal(['review', validation], {
    root, environmentResolver: () => ({}),
    run: async (_command, _args, settings) => { assert.equal(settings.timeout, 1_800_000); return '{"result":"BLOCKED"}'; },
  });
  assert.equal(reviewCall, '{"result":"BLOCKED"}');
  await assert.rejects(executeFormal(['check', submission], {
    root, environmentResolver: () => ({}), run: async () => { const error = new Error('CHILD_FAILED'); error.publicOutput = '{"result":"FAIL"}'; throw error; },
  }), /CHILD_FAILED/);
});

test('real staging bytes and exact builder/assembler contracts precede private handoff', async t => {
  const f = await fixture(t); const b = boundaries(f);
  const result = await prepareWorkflow(f.request, b.options);
  assert.deepEqual(result, { status: 'PASS', stage: 'awaiting-native-formal' });
  const stages = b.calls.filter(call => ['images', 'assemble', 'verify'].includes(call[0]));
  assert.deepEqual(stages.map(call => call[0]), ['images', 'assemble', 'verify']);
  const images = stages[0][1], assemble = stages[1][1];
  assert.deepEqual(Object.keys(images).sort(), ['artifactRoot', 'descriptor', 'endpoint', 'outputParent', 'platform', 'repoRoot']);
  assert.equal(images.platform, 'linux/arm64');
  assert.equal(images.endpoint, f.body.endpoint);
  assert.deepEqual(Object.keys(images.descriptor).sort(), ['apiContract', 'dataset', 'jar', 'notices', 'schemaVersion', 'softwareVersion', 'sourceCommit', 'sqlVersion']);
  assert.deepEqual(images.descriptor.jar, { path: 'api.jar', bytes: 14, sha256: sha('real jar bytes') });
  assert.equal(images.descriptor.dataset.path, 'dataset.zip');
  assert.equal(images.descriptor.dataset.sha256, sha('approved dataset'));
  assert.deepEqual(images.descriptor.notices, [{ path: 'notice.txt', bytes: notice.length, sha256: sha(notice) }]);
  assert.deepEqual(assemble.imageCandidates, [{ directory: path.join(f.outputParent, 'images', 'image-candidate'),
    sha256: sha(JSON.stringify({ platform: 'linux/arm64', artifacts: [{ role: 'runtime-image', platform: 'linux/arm64', path: 'images/linux-arm64/runtime-image.tar', bytes: 3, sha256: sha('runtime'), imageDigest: `sha256:${sha('runtime-image')}` }] })) }]);
  assert.deepEqual(assemble.descriptor.artifacts.filter(a => a.role === 'runtime-image'), [
    { role: 'runtime-image', platform: 'linux/arm64', licenseIds: ['license-0', 'license-1', 'license-2'] },
  ]);
  assert.equal(assemble.descriptor.apiContract, 'caption-hints.v2');
  assert.deepEqual(assemble.descriptor.dataset, { releaseId: 'r1', preparationId: 'p1', ruleId: 'rule1' });
  assert.deepEqual(assemble.descriptor.artifacts.filter(a => a.role === 'license').map(a => a.path), ['notice.txt']);
  assert.equal(await readFile(path.join(images.artifactRoot, 'api.jar'), 'utf8'), 'real jar bytes');
  assert.equal(await readFile(path.join(images.artifactRoot, 'dataset.zip'), 'utf8'), 'approved dataset');
  assert.equal(await readFile(path.join(images.artifactRoot, 'extension.zip'), 'utf8'), 'real extension zip');
  const quality = path.join(f.root, 'tmp/quality/release-workflow/1.2.3');
  const handoff = JSON.parse(await readFile(path.join(quality, 'handoff.json')));
  assert.equal(handoff.schemaVersion, 2);
  assert.deepEqual(Object.keys(handoff).sort(), ['buildIdentity', 'hostBinding', 'releaseTag', 'requestFile', 'requestSha256', 'root', 'runtimeRequestSha256', 'schemaVersion', 'target']);
  assert.deepEqual(Object.keys(handoff.hostBinding).sort(), ['scheme', 'sha256']);
  assert.match(handoff.hostBinding.sha256, /^[a-f0-9]{64}$/u);
  const helperPath = path.join(repo, 'ops/release/host_resources/release_host_identity.py');
  const pythonBinding = JSON.parse(execFileSync('python3', [helperPath, '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  assert.deepEqual(handoff.hostBinding, pythonBinding, 'Node must use the same Python identity implementation');
  const runtime = JSON.parse(await readFile(path.join(quality, 'candidate-runtime-request.json')));
  assert.deepEqual(runtime, { target: handoff.target });
  assert.equal(handoff.requestSha256, sha(await readFile(f.request)));
  assert.equal((await lstat(path.join(quality, 'handoff.json'))).mode & 0o777, 0o600);
  const cmds = b.calls.filter(call => call[0] === 'command');
  assert.deepEqual(cmds.map(call => [call[1], call[2].slice(0, call[1] === 'node' ? 2 : 3)]),
    [['python3', ['-m', 'scripts.local.run_with_java_toolchain', 'backend/gradlew']],
      ['npm', ['ci']], ['node', ['extension/scripts/packaging/package-extension-release.mjs', '--expected-dataset']],
      ['python3', ['-m', 'scripts.local.run_with_java_toolchain', 'java']]]);
  assert.ok(cmds[2][2].includes('--expected-dataset'));
  assert.ok(cmds[3][2].includes('--release-dataset'));
  assert.ok(cmds[3][2].includes('--expected-sha256'));
});

test('bad license, digest, tag and command failure cannot hand off', async t => {
  const reasons = ['LICENSE_LEDGER_INVALID', 'DATASET_DIGEST_MISMATCH', 'WORKFLOW_FAILED', 'WORKFLOW_FAILED'];
  for (const [index, mutate] of [
    async f => { await writeFile(path.join(f.root, 'ops/release/distribution-licenses.json'),
      JSON.stringify({ schema_version: 'lexiflow.distribution-licenses.v1', licenses: [] })); },
    async f => { f.body.dataset.sha256 = sha('wrong'); await writeFile(f.request, JSON.stringify(f.body)); },
    async (_f, b) => { b.options.tagChecker = () => { throw Error('wrong tag'); }; },
    async (_f, b) => { b.options.runCommand = async () => { throw Error('child failed'); }; },
  ].entries()) {
    const f = await fixture(t), b = boundaries(f); await mutate(f, b);
    assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: reasons[index] });
    await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json')));
  }
});

test('existing version-scoped handoff, duplicate JSON, symlink and oversize requests are refused', async t => {
  const f = await fixture(t); const b = boundaries(f);
  await mkdir(path.join(f.root, 'tmp/quality/release-workflow/1.2.3'), { recursive: true });
  const handoff = path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json');
  await writeFile(handoff, 'keep');
  assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: 'WORKFLOW_RESIDUE' });
  assert.equal(await readFile(handoff, 'utf8'), 'keep');
  const g = await fixture(t);
  await writeFile(g.request, `{"releaseTag":"v1.2.3","releaseTag":"v1.2.3"}`);
  assert.deepEqual(await prepareWorkflow(g.request, boundaries(g).options), { status: 'BLOCKED', reason: 'JSON_DUPLICATE_KEY' });
  const h = await fixture(t);
  const link = path.join(h.root, 'request-link.json');
  await symlink(h.request, link);
  assert.equal((await prepareWorkflow(link, boundaries(h).options)).status, 'BLOCKED');
  const i = await fixture(t);
  await writeFile(i.request, ' '.repeat(2_000_001));
  assert.deepEqual(await prepareWorkflow(i.request, boundaries(i).options), { status: 'BLOCKED', reason: 'JSON_INVALID' });
  const j = await fixture(t);
  await writeFile(j.request, `{"releaseTag":"v1.2.3"\u00a0}`);
  assert.deepEqual(await prepareWorkflow(j.request, boundaries(j).options), { status: 'BLOCKED', reason: 'JSON_INVALID' });
  const malformed = await fixture(t);
  await writeFile(malformed.request, JSON.stringify({ ...malformed.body, token: 'forbidden' }));
  assert.equal((await prepareWorkflow(malformed.request, boundaries(malformed).options)).status, 'BLOCKED');
  const nonfinite = await fixture(t);
  await writeFile(nonfinite.request, JSON.stringify(nonfinite.body).replace('"releaseTag":"v1.2.3"', '"releaseTag":1e999'));
  assert.equal((await prepareWorkflow(nonfinite.request, boundaries(nonfinite).options)).status, 'BLOCKED');
  const k = await fixture(t);
  await writeFile(path.join(k.outputParent, 'existing'), 'owned by another run');
  assert.deepEqual(await prepareWorkflow(k.request, boundaries(k).options), { status: 'BLOCKED', reason: 'OUTPUT_PARENT_INVALID' });
  assert.equal(await readFile(path.join(k.outputParent, 'existing'), 'utf8'), 'owned by another run');
  const unsafeNotice = await fixture(t);
  await rm(path.join(unsafeNotice.body.noticesRoot, 'notice.txt'));
  await symlink(unsafeNotice.body.dataset.path, path.join(unsafeNotice.body.noticesRoot, 'notice.txt'));
  assert.equal((await prepareWorkflow(unsafeNotice.request, boundaries(unsafeNotice).options)).status, 'BLOCKED');
});

test('source drift during preparation never writes a handoff', async t => {
  const f = await fixture(t); const b = boundaries(f);
  let reads = 0;
  b.options.identityReader = () => ++reads === 1 ? targetIdentity : previousIdentity;
  assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: 'WORKFLOW_FAILED' });
  await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json')));
});

test('request bytes changed during preparation never write a handoff', async t => {
  const f = await fixture(t); const b = boundaries(f);
  const original = b.options.assembleReleaseCandidate;
  b.options.assembleReleaseCandidate = async input => {
    const result = await original(input);
    await writeFile(f.request, JSON.stringify({ ...f.body, endpoint: 'unix:///changed.sock' }));
    return result;
  };
  assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: 'REQUEST_CHANGED' });
  await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json')));
});

test('host binding drift during preparation never writes handoff', async t => {
  const f = await fixture(t); const b = boundaries(f); let reads = 0;
  const binding = resolveHostBinding();
  b.options.testOnlyHostBindingResolver = () => ++reads === 1 ? binding : { ...binding, sha256: '0'.repeat(64) };
  assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: 'HOST_BINDING_DRIFT' });
  await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json')));
});

test('real child runner bounds logs, timeout, cancellation and process groups', async t => {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-command-')));
  t.after(async () => rm(root, { recursive: true, force: true }));
  const env = { PATH: process.env.PATH ?? '' };
  const run = (name, code, extra = {}) => runCommand(process.execPath, ['-e', code],
    { cwd: root, env, logFile: path.join(root, `${name}.log`), ...extra });
  assert.equal(await run('normal', 'process.stdout.write("done")'), 'done');
  assert.equal(await readFile(path.join(root, 'normal.log'), 'utf8'), 'done');
  await assert.rejects(run('failure', 'process.stderr.write("private");process.exit(7)'),
    error => error.message === 'CHILD_FAILED');
  assert.equal(await readFile(path.join(root, 'failure.log'), 'utf8'), 'private');
  await assert.rejects(run('limit', 'process.stdout.write("x".repeat(1100000))'),
    error => error.message === 'CHILD_OUTPUT_LIMIT');
  assert.ok((await lstat(path.join(root, 'limit.log'))).size <= 1_000_000);
  await assert.rejects(run('timeout', 'setInterval(()=>{},1000)', { timeout: 50 }),
    error => error.message === 'CHILD_TIMEOUT');
  const controller = new AbortController();
  const cancelled = run('cancel', 'setInterval(()=>{},1000)', { abortSignal: controller.signal });
  controller.abort();
  await assert.rejects(cancelled, error => error.message === 'CHILD_CANCELLED');
  await assert.rejects(run('descendant', 'require("child_process").spawn(process.execPath,["-e","setInterval(()=>{},1000)"],{stdio:"ignore"}).unref();'),
    error => error.message === 'CHILD_GROUP_LEFT_RUNNING');
});

test('resume binds UUID, host, source, original request and candidate; only publish receives token', async t => {
  const f = await fixture(t); const b = boundaries(f);
  assert.equal((await prepareWorkflow(f.request, b.options)).status, 'PASS');
  const submissionId = '12345678-1234-4234-8234-123456789abc';
  const outputs = [];
  const run = async (_cmd, args, settings) => {
    outputs.push({ args, settings });
    return JSON.stringify({ result: 'PASS', scope: args.includes('--publish') ? 'promotion-publish' : 'promotion-precheck', published: args.includes('--publish') });
  };
  const common = { root: f.root, submissionId, releaseTag: 'v1.2.3',
    run, identityReader: () => targetIdentity, tagChecker: () => targetIdentity,
    verifier: b.options.verifyReleaseCandidate, environmentResolver: b.options.environmentResolver };
  let driftRunCalls = 0;
  const currentBinding = JSON.parse(execFileSync('python3', [path.join(repo, 'ops/release/host_resources/release_host_identity.py'), '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  assert.equal((await resumePromotion({ ...common,
    testOnlyHostBindingResolver: () => ({ ...currentBinding, sha256: '0'.repeat(64) }),
    run: async () => { driftRunCalls++; return ''; },
  })).status, 'BLOCKED');
  assert.equal(driftRunCalls, 0, 'host drift must be checked before invoking the publisher');
  assert.equal((await resumePromotion(common)).status, 'PASS');
  assert.equal(outputs[0].settings.env.GITHUB_TOKEN, undefined);
  assert.equal(outputs[0].settings.env.LEXIFLOW_RELEASE_PODMAN_ENDPOINT, undefined);
  assert.equal((await resumePromotion({ ...common, releaseTag: 'v1.2.4' })).status, 'BLOCKED');
  const handoffPath = path.join(f.root, 'tmp/quality/release-workflow/1.2.3/handoff.json');
  const savedHandoff = JSON.parse(await readFile(handoffPath));
  await writeFile(handoffPath, JSON.stringify({ ...savedHandoff, hostBinding: { scheme: 'lexiflow.host-binding.v1', sha256: '0'.repeat(64) } }));
  assert.equal((await resumePromotion(common)).status, 'BLOCKED');
  await writeFile(handoffPath, JSON.stringify({ ...savedHandoff, schemaVersion: 1, host: 'legacy-host' }));
  assert.equal((await resumePromotion(common)).status, 'BLOCKED', 'old schema must fail closed');
  await writeFile(handoffPath, JSON.stringify({ ...savedHandoff, extra: true }));
  assert.equal((await resumePromotion(common)).status, 'BLOCKED', 'extra fields must fail closed');
  await writeFile(handoffPath, JSON.stringify(savedHandoff));
  const oldToken = process.env.GITHUB_TOKEN;
  const oldConfig = process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN;
  try {
    delete process.env.GITHUB_TOKEN;
    delete process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN;
    assert.deepEqual(await resumePromotion({ ...common, publish: true }),
      { status: 'BLOCKED', reason: 'PUBLISH_TOKEN_MISSING' });
    process.env.GITHUB_TOKEN = 'synthetic-test-token';
    process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN = 'synthetic-config-token';
    const published = await resumePromotion({ ...common, publish: true });
    assert.equal(published.status, 'PASS');
    assert.equal(outputs[1].settings.env.GITHUB_TOKEN, 'synthetic-test-token');
    assert.equal(outputs[1].settings.env.LEXIFLOW_RELEASE_PODMAN_ENDPOINT, f.body.endpoint);
    assert.equal(outputs[1].settings.env.LEXIFLOW_RELEASE_CONFIG_TOKEN, 'synthetic-config-token');
    const unknown = await resumePromotion({ ...common, publish: true, run: async () => {
      const error = new Error('CHILD_FAILED');
      error.publicOutput = JSON.stringify({ result: 'BLOCKED', published: 'unknown',
        write_stage: 'upload', release_id: 42 });
      throw error;
    } });
    assert.deepEqual(unknown, { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN',
      published: 'unknown', writeStage: 'upload', releaseId: 42 });
    const registryUnknown = await resumePromotion({ ...common, publish: true, run: async () => {
      const error = new Error('CHILD_FAILED');
      error.publicOutput = JSON.stringify({ result: 'BLOCKED', published: 'unknown',
        write_stage: 'registry-push', registry_role: 'runtime-image', release_id: null });
      throw error;
    } });
    assert.deepEqual(registryUnknown, { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN',
      published: 'unknown', writeStage: 'registry-push', registryRole: 'runtime-image', releaseId: null });
    const cleanup = await resumePromotion({ ...common, publish: true, run: async () => {
      const error = new Error('CHILD_FAILED');
      error.publicOutput = JSON.stringify({ result: 'BLOCKED', published: true,
        cleanup: 'BLOCKED', release_id: 42 });
      throw error;
    } });
    assert.deepEqual(cleanup, { status: 'BLOCKED', reason: 'PUBLISHED_CLEANUP_BLOCKED',
      published: true, releaseId: 42 });
    const crash = await resumePromotion({ ...common, publish: true, run: async () => {
      throw new Error('CHILD_TIMEOUT');
    } });
    assert.deepEqual(crash, { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN',
      published: 'unknown', writeStage: null, releaseId: null });
    const malformedOutput = await resumePromotion({ ...common, publish: true,
      run: async () => 'not-json' });
    assert.deepEqual(malformedOutput, { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN',
      published: 'unknown', writeStage: null, releaseId: null });
  } finally {
    if (oldToken === undefined) delete process.env.GITHUB_TOKEN;
    else process.env.GITHUB_TOKEN = oldToken;
    if (oldConfig === undefined) delete process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN;
    else process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN = oldConfig;
  }
  await writeFile(f.request, JSON.stringify({ ...f.body, endpoint: 'unix:///other.sock' }));
  assert.equal((await resumePromotion(common)).status, 'BLOCKED');
  assert.equal(outputs.length, 2);
});

test('confirmed recovery is a strict publish-only boolean and reaches the promotion CLI', async t => {
  const f = await fixture(t); const b = boundaries(f);
  assert.equal((await prepareWorkflow(f.request, b.options)).status, 'PASS');
  const captured = [];
  const common = { root: f.root, submissionId: '12345678-1234-4234-8234-123456789abc', releaseTag: 'v1.2.3',
    run: async (_cmd, args) => { captured.push(args); return JSON.stringify({ result: 'PASS', scope: 'promotion-publish', published: true }); },
    identityReader: () => targetIdentity, tagChecker: () => targetIdentity,
    verifier: b.options.verifyReleaseCandidate, environmentResolver: b.options.environmentResolver };
  assert.equal((await resumePromotion({ ...common, confirmedRecovery: 'true' })).status, 'FAIL');
  assert.equal((await resumePromotion({ ...common, confirmedRecovery: true })).reason, 'RECOVERY_CONFIRMATION_REQUIRES_PUBLISH');
  const oldToken = process.env.GITHUB_TOKEN, oldConfig = process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN;
  try {
    process.env.GITHUB_TOKEN = 'synthetic-test-token'; process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN = 'synthetic-config-token';
    assert.equal((await resumePromotion({ ...common, publish: true, confirmedRecovery: true })).status, 'PASS');
    assert.deepEqual(captured[0].slice(-2), ['--publish', '--confirmed-recovery']);
  } finally {
    if (oldToken === undefined) delete process.env.GITHUB_TOKEN; else process.env.GITHUB_TOKEN = oldToken;
    if (oldConfig === undefined) delete process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN; else process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN = oldConfig;
  }
  const cliArgs = [path.join(repo, 'ops/release/workflow/run-release-workflow.mjs'), 'resume', common.submissionId, common.releaseTag];
  const cli = spawnSync(process.execPath, [...cliArgs, '--publish', '--confirmed-recovery'], { cwd: repo, encoding: 'utf8' });
  assert.notEqual(JSON.parse(cli.stdout).reason, 'ARGUMENTS_INVALID');
  const badCli = spawnSync(process.execPath, [...cliArgs, '--confirmed-recovery'], { cwd: repo, encoding: 'utf8' });
  assert.equal(JSON.parse(badCli.stdout).reason, 'ARGUMENTS_INVALID');
});


for (const publish of [false, true]) {
  for (const scenario of ['repeat', 'failure-recovery', 'legacy-log']) {
    test(`晋升尝试保留独立不可覆盖日志：${publish ? 'publish' : 'precheck'}/${scenario}`, async t => {
      const f = await fixture(t); const b = boundaries(f);
      assert.equal((await prepareWorkflow(f.request, b.options)).status, 'PASS');
      const stage = publish ? 'publish' : 'precheck';
      const quality = path.join(f.root, 'tmp/quality/release-workflow/1.2.3');
      const legacyLog = path.join(quality, `promotion-${stage}.log`);
      const legacyBytes = Buffer.from('先前尝试的不可变原始日志\n');
      if (scenario === 'legacy-log') await writeFile(legacyLog, legacyBytes, { flag: 'wx', mode: 0o600 });
      const prior = [process.env.GITHUB_TOKEN, process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN];
      if (publish) {
        process.env.GITHUB_TOKEN = 'synthetic-publish-token';
        process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN = 'synthetic-config-token';
        t.after(() => ['GITHUB_TOKEN', 'LEXIFLOW_RELEASE_CONFIG_TOKEN'].forEach((key, index) => {
          if (prior[index] === undefined) delete process.env[key]; else process.env[key] = prior[index];
        }));
      }
      const attempts = [];
      const common = { root: f.root, submissionId: '12345678-1234-4234-8234-123456789abc',
        releaseTag: 'v1.2.3', publish, identityReader: () => targetIdentity, tagChecker: () => targetIdentity,
        verifier: b.options.verifyReleaseCandidate, environmentResolver: b.options.environmentResolver,
        run: async (command, args, settings) => {
          assert.equal(command, 'python3');
          assert.deepEqual(args.slice(0, 2), ['-m', 'ops.release.workflow.promote_verified_release']);
          assert.equal(args.includes('--publish'), publish);
          assert.equal(settings.timeout, 1_800_000);
          const failed = scenario === 'failure-recovery' && attempts.length === 0;
          const output = JSON.stringify({ result: failed ? 'BLOCKED' : 'PASS',
            published: failed ? false : publish, scope: `promotion-${stage}` }) + '\n';
          attempts.push({ logFile: settings.logFile, output });
          // 只替换业务子进程为无网络的合成 Node；日志创建和写入走真实 runCommand。
          return runCommand(process.execPath,
            ['-e', `process.stdout.write(${JSON.stringify(output)}); process.exitCode=${failed ? 2 : 0};`], settings);
        } };
      const first = await resumePromotion(common);
      assert.equal(first.status, scenario === 'failure-recovery' ? 'BLOCKED' : 'PASS');
      if (scenario === 'failure-recovery') assert.equal(first.reason, 'CHILD_FAILED');
      const firstBytes = await readFile(attempts[0].logFile);
      assert.equal((await resumePromotion(common)).status, 'PASS');
      assert.equal(attempts.length, 2);
      assert.equal(new Set(attempts.map(item => item.logFile)).size, 2);
      assert.deepEqual(await readFile(attempts[0].logFile), firstBytes);
      for (const attempt of attempts) {
        assert.equal(path.dirname(attempt.logFile), quality);
        assert.match(path.basename(attempt.logFile), new RegExp(`^promotion-${stage}-[0-9a-f-]{36}\\.log$`));
        assert.equal(await readFile(attempt.logFile, 'utf8'), attempt.output);
        assert.equal((await lstat(attempt.logFile)).mode & 0o777, 0o600);
        await assert.rejects(runCommand(process.execPath, ['-e', 'process.stdout.write("overwrite")'],
          { cwd: f.root, env: {}, logFile: attempt.logFile }), error => error.code === 'EEXIST');
        assert.equal(await readFile(attempt.logFile, 'utf8'), attempt.output);
      }
      if (scenario === 'legacy-log') assert.deepEqual(await readFile(legacyLog), legacyBytes);
    });
  }
}

test('private readers and unused release cache forwarding are not public interfaces', async t => {
  const workflow = await import('../workflow/run-release-workflow.mjs');
  const dockerCommand = await import('../build/release-build-process.mjs');
  assert.equal(workflow.readBoundedJson, undefined);
  assert.equal(dockerCommand.dockerOutputLimit, undefined);
  const names = ['LEXIFLOW_RELEASE_GRADLE_CACHE', 'LEXIFLOW_RELEASE_NPM_CACHE'];
  const previous = names.map(name => process.env[name]);
  for (const name of names) process.env[name] = 'synthetic-unused-cache';
  t.after(() => names.forEach((name, index) => { if (previous[index] === undefined) delete process.env[name]; else process.env[name] = previous[index]; }));
  const f = await fixture(t); const b = boundaries(f);
  assert.equal((await prepareWorkflow(f.request, b.options)).status, 'PASS');
  for (const call of b.calls.filter(call => call[0] === 'command')) {
    for (const name of names) assert.ok(!Object.hasOwn(call[3].env, name));
  }
});
