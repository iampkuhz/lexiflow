import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, lstat, rm, symlink, realpath } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { prepareWorkflow, resumePromotion, runCommand } from '../workflow.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const ident = (commit, version = '1.2.3') => {
  const sourceSha256 = sha(commit);
  const base = { baseVersion: version, sourceCommit: commit, sourceSha256, dirty: false };
  return { schemaVersion: 1, ...base, softwareVersion: version, chromeVersion: `${version}.1`,
    buildId: sha(JSON.stringify(base)), channel: 'release' };
};
const targetIdentity = ident('a'.repeat(40));
const previousIdentity = ident('b'.repeat(40), '1.2.2');
const notice = Buffer.from('Approved license notice');
const components = ['LexiFlow', 'API-runtime', 'PostgreSQL', 'extension-third-party', 'dataset'];

async function fixture(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-workflow-')));
  t.after(async () => rm(root, { recursive: true, force: true }));
  const outputParent = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-workflow-output-')));
  t.after(async () => rm(outputParent, { recursive: true, force: true }));
  const noticesRoot = path.join(root, 'notices');
  const previousDirectory = path.join(root, 'previous');
  await Promise.all([mkdir(noticesRoot), mkdir(previousDirectory)]);
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
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
  noticesRoot, outputParent, previous: { candidateDirectory: previousDirectory, candidateSha256: sha('previous') },
  endpoint: 'unix:///tmp/podman.sock' };
  await writeFile(request, JSON.stringify(body));
  return { root, outputParent, request, body, licenses };
}

function boundaries(f, overrides = {}) {
  const calls = [];
  const imageDirectory = path.join(f.outputParent, 'images', 'image-candidate');
  const candidateDirectory = path.join(f.outputParent, 'releases', 'release-candidate');
  const imageRecords = [
    { role: 'api-image', platform: 'linux/arm64', path: 'images/linux-arm64/api-image.tar',
      bytes: 3, sha256: sha('api'), imageDigest: `sha256:${sha('image-api')}` },
    { role: 'postgres-image', platform: 'linux/arm64', path: 'images/linux-arm64/postgres-image.tar',
      bytes: 2, sha256: sha('pg'), imageDigest: `sha256:${sha('image-pg')}` },
  ];
  const runCommand = async (command, args, settings) => {
    calls.push(['command', command, args, settings]);
    if (args.includes(':api:bootJar')) {
      const folder = path.join(f.root, 'backend/product/api/build/libs');
      await mkdir(folder, { recursive: true });
      await writeFile(path.join(folder, 'api-1.2.3.jar'), 'real jar bytes');
    }
    if (command === 'node' && args.includes('extension/scripts/release.mjs')) {
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
    return input.candidateDirectory === f.body.previous.candidateDirectory
      ? { candidateSha256: f.body.previous.candidateSha256, buildIdentity: previousIdentity }
      : { candidateSha256: sha('target candidate'), buildIdentity: targetIdentity };
  };
  return { calls, candidateDirectory, options: { root: f.root, platform: 'darwin', arch: 'arm64',
    tagChecker: () => targetIdentity, identityReader: () => targetIdentity, ledgerTrackedCheck: () => {},
    endpointChecker: async () => {},
    baseImageLoader: async () => [{ platform: 'linux/amd64' }, { platform: 'linux/arm64' }],
    runCommand, buildCandidateImages,
    assembleReleaseCandidate, verifyReleaseCandidate, ...overrides } };
}

test('real staging bytes and exact builder/assembler contracts precede private handoff', async t => {
  const f = await fixture(t); const b = boundaries(f);
  const result = await prepareWorkflow(f.request, b.options);
  assert.deepEqual(result, { status: 'PASS', stage: 'awaiting-native-formal' });
  const stages = b.calls.filter(call => ['images', 'assemble', 'verify'].includes(call[0]));
  assert.deepEqual(stages.map(call => call[0]), ['verify', 'images', 'assemble', 'verify']);
  const images = stages[1][1], assemble = stages[2][1];
  assert.deepEqual(Object.keys(images).sort(), ['artifactRoot', 'descriptor', 'endpoint', 'outputParent', 'platform', 'repoRoot']);
  assert.equal(images.platform, 'linux/arm64');
  assert.deepEqual(images.descriptor.baseImages, [{ platform: 'linux/arm64' }]);
  assert.equal(images.endpoint, f.body.endpoint);
  assert.deepEqual(images.descriptor.jar, { path: 'api.jar', bytes: 14, sha256: sha('real jar bytes') });
  assert.deepEqual(assemble.imageCandidates, [{ directory: path.join(f.outputParent, 'images', 'image-candidate'),
    sha256: sha(JSON.stringify({ platform: 'linux/arm64', artifacts: [
      { role: 'api-image', platform: 'linux/arm64', path: 'images/linux-arm64/api-image.tar', bytes: 3,
        sha256: sha('api'), imageDigest: `sha256:${sha('image-api')}` },
      { role: 'postgres-image', platform: 'linux/arm64', path: 'images/linux-arm64/postgres-image.tar', bytes: 2,
        sha256: sha('pg'), imageDigest: `sha256:${sha('image-pg')}` },
    ] })) }]);
  assert.equal(assemble.descriptor.apiContract, 'caption-hints.v2');
  assert.deepEqual(assemble.descriptor.dataset, { releaseId: 'r1', preparationId: 'p1', ruleId: 'rule1' });
  assert.deepEqual(assemble.descriptor.artifacts.filter(a => a.role === 'license').map(a => a.path), ['notice.txt']);
  assert.equal(await readFile(path.join(images.artifactRoot, 'api.jar'), 'utf8'), 'real jar bytes');
  assert.equal(await readFile(path.join(images.artifactRoot, 'dataset.zip'), 'utf8'), 'approved dataset');
  assert.equal(await readFile(path.join(images.artifactRoot, 'extension.zip'), 'utf8'), 'real extension zip');
  const quality = path.join(f.root, 'tmp/quality/release-workflow');
  const handoff = JSON.parse(await readFile(path.join(quality, 'handoff.json')));
  const runtime = JSON.parse(await readFile(path.join(quality, 'candidate-runtime-request.json')));
  assert.deepEqual(runtime, { previous: f.body.previous, target: handoff.target });
  assert.equal(handoff.requestSha256, sha(await readFile(f.request)));
  assert.equal((await lstat(path.join(quality, 'handoff.json'))).mode & 0o777, 0o600);
  const cmds = b.calls.filter(call => call[0] === 'command');
  assert.deepEqual(cmds.map(call => [call[1], call[2].slice(0, 3)]),
    [['python3', ['-m', 'scripts.environment.java_exec', 'backend/gradlew']],
      ['npm', ['ci']], ['node', ['extension/scripts/release.mjs']],
      ['python3', ['-m', 'scripts.environment.java_exec', 'java']]]);
  assert.ok(cmds[3][2].includes('--release-dataset'));
  assert.ok(cmds[3][2].includes('--expected-sha256'));
});

test('bad license, digest, tag, previous and command failure cannot hand off', async t => {
  const reasons = ['LICENSE_LEDGER_INVALID', 'DATASET_DIGEST_MISMATCH', 'WORKFLOW_FAILED', 'PREVIOUS_NOT_DISTINCT', 'WORKFLOW_FAILED'];
  for (const [index, mutate] of [
    async f => { await writeFile(path.join(f.root, 'ops/release/distribution-licenses.json'),
      JSON.stringify({ schema_version: 'lexiflow.distribution-licenses.v1', licenses: [] })); },
    async f => { f.body.dataset.sha256 = sha('wrong'); await writeFile(f.request, JSON.stringify(f.body)); },
    async (_f, b) => { b.options.tagChecker = () => { throw Error('wrong tag'); }; },
    async (_f, b) => { b.options.verifyReleaseCandidate = async () =>
      ({ candidateSha256: sha('previous'), buildIdentity: targetIdentity }); },
    async (_f, b) => { b.options.runCommand = async () => { throw Error('child failed'); }; },
  ].entries()) {
    const f = await fixture(t), b = boundaries(f); await mutate(f, b);
    assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: reasons[index] });
    await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/handoff.json')));
  }
});

test('existing handoff, duplicate JSON, symlink and oversize requests are refused', async t => {
  const f = await fixture(t); const b = boundaries(f);
  await mkdir(path.join(f.root, 'tmp/quality/release-workflow'), { recursive: true });
  const handoff = path.join(f.root, 'tmp/quality/release-workflow/handoff.json');
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
  assert.deepEqual(await prepareWorkflow(f.request, b.options), { status: 'BLOCKED', reason: 'BUILD_IDENTITY_MISMATCH' });
  await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/handoff.json')));
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
  await assert.rejects(readFile(path.join(f.root, 'tmp/quality/release-workflow/handoff.json')));
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
    return JSON.stringify({ result: 'PASS', published: args.includes('--publish') });
  };
  const common = { root: f.root, submissionId, releaseTag: 'v1.2.3',
    run, identityReader: () => targetIdentity, tagChecker: () => targetIdentity,
    verifier: b.options.verifyReleaseCandidate };
  assert.equal((await resumePromotion(common)).status, 'PASS');
  assert.equal(outputs[0].settings.env.GITHUB_TOKEN, undefined);
  assert.equal((await resumePromotion({ ...common, releaseTag: 'v1.2.4' })).status, 'BLOCKED');
  assert.equal((await resumePromotion({ ...common, host: 'other-host' })).status, 'BLOCKED');
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
    assert.equal(outputs[1].settings.env.LEXIFLOW_RELEASE_CONFIG_TOKEN, 'synthetic-config-token');
    const unknown = await resumePromotion({ ...common, publish: true, run: async () => {
      const error = new Error('CHILD_FAILED');
      error.publicOutput = JSON.stringify({ result: 'BLOCKED', published: 'unknown',
        write_stage: 'upload', release_id: 42 });
      throw error;
    } });
    assert.deepEqual(unknown, { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN',
      published: 'unknown', writeStage: 'upload', releaseId: 42 });
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
