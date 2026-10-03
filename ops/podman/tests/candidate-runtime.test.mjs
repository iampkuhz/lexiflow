import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCandidateRuntime, REQUIRED_PHASE_IDS, eligibleForPass, assertResourceOwnership, assertTargetApiContainer, stopVerifiedTargetApi } from '../candidate-runtime.mjs';
import { startCandidateSession } from '../candidate-session.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const cli = path.join(repo, 'ops/podman/candidate-runtime.mjs');
const candidate = n => ({ candidateDirectory: `/tmp/lexiflow-synthetic-${n}`, candidateSha256: n.repeat(64) });
const request = { previous: candidate('a'), target: candidate('b') };
const owned = { id: 'a'.repeat(32), project: `lexiflow-local-${'a'.repeat(32)}` };
const labels = { 'com.docker.compose.project': owned.project, 'io.podman.compose.project': owned.project,
  'lexiflow.installation': owned.id, 'com.docker.compose.service': 'api', 'io.podman.compose.service': 'api' };

test('exact request rejects caller command, alternate directory and claimed PASS before side effects', async () => {
  for (const extra of [{ action: 'install' }, { root: '/tmp/other' }, { status: 'PASS' }, { hooks: {} }]) {
    const result = await runCandidateRuntime({ ...request, ...extra });
    assert.equal(result.status, 'FAIL');
    assert.equal(result.failure.code, 'REQUEST_INVALID');
    assert.equal(result.phases.every(p => p.status === 'NOT_RUN'), true);
    assert.equal(result.cleanup.settled, true);
  }
  const bad = await runCandidateRuntime({ ...request, previous: { ...request.previous, candidateSha256: 'not-hex' } });
  assert.equal(bad.failure.code, 'REQUEST_INVALID');
});

test('host refusal is a bounded BLOCKED report and never claims a phase', async () => {
  const original = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { ...original, value: 'linux' });
  try {
    const result = await runCandidateRuntime(request);
    assert.equal(result.status, 'BLOCKED');
    assert.equal(result.failure.code, 'HOST_UNSUPPORTED');
    assert.equal(result.candidates.previous, null);
    assert.equal(result.phases.every(p => p.status === 'NOT_RUN'), true);
  } finally { Object.defineProperty(process, 'platform', original); }
});

test('CLI uses one JSON stdout report and invalid JSON exits nonzero', () => {
  const valid = spawnSync(process.execPath, [cli], { input: JSON.stringify({ ...request, command: 'podman rm -f anything' }), encoding: 'utf8' });
  assert.equal(valid.status, 0);
  assert.equal(valid.stdout.trim().split('\n').length, 1);
  const report = JSON.parse(valid.stdout);
  assert.equal(report.failure.code, 'REQUEST_INVALID');
  assert.equal(valid.stderr, '');
  const malformed = spawnSync(process.execPath, [cli], { input: '{', encoding: 'utf8' });
  assert.notEqual(malformed.status, 0);
  assert.equal(malformed.stdout, '');
});

test('PASS requires every fixed real phase and settled successful cleanup', () => {
  const output = { phases: REQUIRED_PHASE_IDS.map(id => ({ id, status: 'PASS' })), cleanup: { status: 'PASS', settled: true } };
  assert.equal(eligibleForPass(output), true);
  for (const id of REQUIRED_PHASE_IDS) {
    const changed = structuredClone(output);
    changed.phases.find(p => p.id === id).status = 'NOT_RUN';
    assert.equal(eligibleForPass(changed), false, id);
  }
  output.cleanup.settled = false;
  assert.equal(eligibleForPass(output), false, 'unknown descendants must retain resources');
  output.cleanup.settled = true; output.cleanup.status = 'FAIL';
  assert.equal(eligibleForPass(output), false, 'unknown ownership must retain resources');
});

test('wrong project, owner, service or image is rejected before any stop decision', () => {
  const info = { Id: 'c'.repeat(64), Image: 'sha256:' + 'd'.repeat(64), State: { Running: true }, Config: { Labels: labels } };
  assert.equal(assertTargetApiContainer(info, owned, 'sha256:' + 'd'.repeat(64)), true);
  for (const change of [
    { 'com.docker.compose.project': 'foreign' },
    { 'io.podman.compose.project': 'foreign' },
    { 'lexiflow.installation': 'foreign' },
    { 'com.docker.compose.service': 'postgres' },
    { 'io.podman.compose.service': 'postgres' },
  ]) {
    const changed = { ...info, Config: { Labels: { ...labels, ...change } } };
    assert.throws(() => assertResourceOwnership(changed, owned, 'container', 'api'), /RESOURCE_OWNER_MISMATCH/);
    assert.throws(() => assertTargetApiContainer(changed, owned, 'sha256:' + 'd'.repeat(64)));
  }
  assert.throws(() => assertTargetApiContainer(info, owned, 'sha256:' + 'e'.repeat(64)), /API_IMAGE_MISMATCH/);
});

test('observer requires two matching live inspections and observed stop, never stopping an unknown target', () => {
  const image = 'sha256:' + 'd'.repeat(64);
  const first = { Id: 'c'.repeat(64), Image: image, State: { Running: true }, Config: { Labels: labels } };
  let stops = 0;
  const gate = (changedFirst, changedSecond, stopped) => stopVerifiedTargetApi({ state: owned,
    first: changedFirst, expectedImage: image, reinspect: () => changedSecond,
    stop: () => { stops++; }, inspectStopped: () => stopped });
  const foreign = { ...first, Config: { Labels: { ...labels, 'lexiflow.installation': 'foreign' } } };
  assert.throws(() => gate(foreign, first, { ...first, State: { Running: false } }), /RESOURCE_OWNER_MISMATCH/);
  assert.throws(() => gate(first, foreign, { ...first, State: { Running: false } }), /RESOURCE_OWNER_MISMATCH/);
  assert.throws(() => gate(first, { ...first, Image: 'sha256:' + 'e'.repeat(64) }, { ...first, State: { Running: false } }), /API_IMAGE_MISMATCH/);
  assert.equal(stops, 0);
  assert.throws(() => gate(first, first, first), /TARGET_API_STOP_NOT_OBSERVED/);
  assert.equal(stops, 1, 'the missing-fault case must not be reported as PASS');
  assert.equal(gate(first, first, { ...first, State: { Running: false } }), true);
  assert.equal(stops, 2);
});

test('fixed child session rejects alternate operations and missing bounded DB port', () => {
  const base = { action: 'install', directory: '/tmp/lexiflow-test', candidate: request.target,
    dbPort: 15432, logFile: '/tmp/lexiflow-test.log' };
  for (const change of [{ action: 'stop' }, { dbPort: 18080 }, { dbPort: 0 },
    { candidate: { ...request.target, command: 'exit 0' } }, { directory: 'relative' }, { logFile: 'relative' }]) {
    assert.throws(() => startCandidateSession({ ...base, ...change }), /SESSION_REQUEST_INVALID/);
  }
});

test('fixed fd3 control pipe closes an idle child normally and cancels on explicit marker or parent EOF', async () => {
  const source = `import {watchCancel} from ${JSON.stringify(new URL('../candidate-session.mjs', import.meta.url).href)};
    let cancelled = false; const control = watchCancel(() => { cancelled = true; process.stdout.write('CANCEL\\n'); control.destroy(); });
    process.stdout.write('READY\\n');
    setTimeout(() => { if (!cancelled) { control.destroy(); process.stdout.write('DONE\\n'); } }, 30);`;
  const run = (signal) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', source], { stdio: ['ignore', 'pipe', 'pipe', 'pipe'] });
    let output = '', stderr = '', sent = false;
    const timer = setTimeout(() => { child.stdio[3].destroy(); reject(new Error('CONTROL_PIPE_HUNG')); }, 2000);
    child.stdout.on('data', chunk => {
      output += chunk;
      if (!sent && output.includes('READY') && signal !== 'none') {
        sent = true; child.stdio[3].end(signal === 'marker' ? 'cancel\n' : undefined);
      }
    });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('close', code => { clearTimeout(timer); resolve({ code, output, stderr }); });
  });
  const normal = await run('none');
  assert.equal(normal.code, 0, normal.stderr);
  assert.match(normal.output, /DONE/);
  assert.doesNotMatch(normal.output, /CANCEL/);
  const marked = await run('marker');
  assert.equal(marked.code, 0, marked.stderr);
  assert.match(marked.output, /CANCEL/);
  const eof = await run('eof');
  assert.equal(eof.code, 0, eof.stderr);
  assert.match(eof.output, /CANCEL/);
});

// Append to ops/podman/tests/candidate-runtime.test.mjs. The copied orchestrator is byte-identical.
import fs from 'node:fs';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { mock } from 'node:test';

const flowHex = c => c.repeat(64);
const flowSha = value => crypto.createHash('sha256').update(value).digest('hex');
const flowCandidate = (name, letter) => ({ candidateDirectory: `/synthetic/${name}`, candidateSha256: flowHex(letter) });
const flowPrevious = flowCandidate('previous', 'a');
const flowTarget = flowCandidate('target', 'b');
const flowIdentity = (_name, letter) => {
  const source = { baseVersion: '2.0.0-SNAPSHOT', sourceCommit: letter.repeat(40), sourceSha256: flowHex(letter), dirty: false };
  return { schemaVersion: 1, ...source, softwareVersion: `2.0.0-SNAPSHOT.g${letter.repeat(7)}`,
    chromeVersion: '2.0.0.0', buildId: flowSha(JSON.stringify(source)), channel: 'snapshot' };
};
const flowVerified = (name, letter) => ({ candidateSha256: flowHex(letter), manifestSha256: flowHex(letter === 'a' ? 'c' : 'd'), buildIdentity: flowIdentity(name, letter), manifest: { artifacts: [
  { role: 'api-image', platform: 'linux/arm64', imageDigest: `sha256:${flowHex(letter)}` },
  { role: 'postgres-image', platform: 'linux/arm64', imageDigest: `sha256:${flowHex(letter === 'a' ? 'e' : 'f')}` },
  { role: 'sql', sha256: flowHex('9') },
] } });
const flowCandidates = { [flowPrevious.candidateSha256]: flowVerified('previous', 'a'), [flowTarget.candidateSha256]: flowVerified('target', 'b') };
const flowStub = (file, body) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, body); };

async function withRuntimeFlowFixture(scenario, check) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-flow-'));
  const runtimeFile = path.join(root, 'ops/podman/candidate-runtime.mjs');
  flowStub(runtimeFile, fs.readFileSync(path.join(repo, 'ops/podman/candidate-runtime.mjs')));
  flowStub(path.join(root, 'ops/release/version.mjs'), `export const resolveBuildIdentity=()=>globalThis.__lexiflowFlow.source; export { assertBuildIdentityMatches } from ${JSON.stringify(new URL('../../release/version.mjs', import.meta.url).href)};`);
  flowStub(path.join(root, 'ops/release/verified-candidate.mjs'), `export const verifyReleaseCandidate=async c=>globalThis.__lexiflowFlow.verify(c);`);
  flowStub(path.join(root, 'ops/podman/upgrade.mjs'), `export const verifyExtension=(d,i,p)=>globalThis.__lexiflowFlow.extension(d,i,p); export const treeDigest=d=>globalThis.__lexiflowFlow.tree(d);`);
  flowStub(path.join(root, 'ops/podman/process-lock.mjs'), `export const acquireProcessLock=d=>globalThis.__lexiflowFlow.lock(d);`);
  flowStub(path.join(root, 'ops/podman/candidate-session.mjs'), `export const runCandidateSession=i=>globalThis.__lexiflowFlow.run(i); export const startCandidateSession=i=>globalThis.__lexiflowFlow.start(i);`);
  const socketPath = path.join(root, 'podman.sock');
  const socket = net.createServer();
  await new Promise((resolve, reject) => { socket.once('error', reject); socket.listen(socketPath, resolve); });
  const original = ['platform', 'arch'].map(key => [key, Object.getOwnPropertyDescriptor(process, key)]);
  const osPlatform = Object.getOwnPropertyDescriptor(os, 'platform'), osArch = Object.getOwnPropertyDescriptor(os, 'arch');
  const flow = new FlowEngine(scenario, socketPath);
  globalThis.__lexiflowFlow = flow;
  Object.defineProperty(process, 'platform', { configurable: true, value: 'darwin' });
  Object.defineProperty(process, 'arch', { configurable: true, value: 'arm64' });
  Object.defineProperty(os, 'platform', { configurable: true, value: () => 'darwin' });
  Object.defineProperty(os, 'arch', { configurable: true, value: () => 'arm64' });
  // The real Unix socket above exercises the inode fence; port probes are synthetic.
  mock.method(net, 'createServer', () => ({
    once() { return this; },
    listen(...args) { queueMicrotask(args.at(-1)); return this; },
    address() { return { port: 15432 }; },
    close(callback) { if (callback) queueMicrotask(callback); return this; },
  }));
  mock.method(childProcess, 'spawnSync', (...args) => flow.command(...args));
  syncBuiltinESMExports();
  try {
    const runtime = await import(`file://${runtimeFile}`);
    await check(runtime, flow);
  } finally {
    mock.restoreAll(); syncBuiltinESMExports();
    for (const [key, descriptor] of original) Object.defineProperty(process, key, descriptor);
    Object.defineProperty(os, 'platform', osPlatform); Object.defineProperty(os, 'arch', osArch);
    delete globalThis.__lexiflowFlow;
    await new Promise(resolve => socket.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

class FlowEngine {
  constructor(scenario, socketPath) { this.scenario = scenario; this.socketPath = socketPath; this.installs = new Map(); this.mutations = []; this.stops = 0; this.next = 0; this.source = flowCandidates[flowTarget.candidateSha256].buildIdentity; }
  verify(c) { const v = flowCandidates[c.candidateSha256]; if (!v) throw Error('UNKNOWN_CANDIDATE'); return v; }
  extension(dir, identity, port) { assert.equal(port, 18080); assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'build-identity.json'))), identity); return this.tree(dir); }
  tree(dir) { return flowSha(fs.readFileSync(path.join(dir, 'build-identity.json'))); }
  lock(dir) { assert.ok(fs.existsSync(path.join(dir, 'state.json'))); return () => {}; }
  identify(input) { return this.verify(input.candidate).buildIdentity; }
  record(install, kind, status, identity) {
    const operation = (++this.next).toString(16).padStart(32, '0');
    const record = { schema: 1, operation, kind, status, buildId: identity.buildId, targetVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit, startedAt: '2026-01-01T00:00:00Z', completedAt: '2026-01-01T00:00:01Z', artifacts: install.state.artifacts, dataset: install.state.datasetIdentity };
    fs.writeFileSync(path.join(install.dir, 'installation-records', `${operation}.json`), JSON.stringify(record));
    if (status === 'PASS') install.state.lastOperation = record;
    return record;
  }
  save(install) { fs.writeFileSync(path.join(install.dir, 'state.json'), JSON.stringify(install.state)); }
  ext(install, identity) { const dir = path.join(install.dir, 'extension'); fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, 'build-identity.json'), JSON.stringify(identity)); }
  make(input) {
    const v = this.verify(input.candidate), dir = input.directory, id = (++this.next).toString(16).padStart(32, '0');
    const project = `lexiflow-local-${id}`, dataset = path.join(dir, 'dataset');
    fs.mkdirSync(path.join(dir, 'installation-records'), { recursive: true });
    fs.mkdirSync(path.join(dir, 'secrets')); fs.mkdirSync(dataset);
    fs.writeFileSync(path.join(dir, 'secrets/app-password'), 'synthetic-app');
    fs.writeFileSync(path.join(dir, 'secrets/postgres-password'), 'synthetic-pg');
    fs.writeFileSync(path.join(dataset, 'dataset.zip'), 'synthetic-dataset');
    const api = v.manifest.artifacts[0].imageDigest, pg = v.manifest.artifacts[1].imageDigest;
    const state = { schema: 1, id, project, root: dir, apiPort: 18080, dbPort: input.dbPort, phase: 'ready', version: v.buildIdentity.softwareVersion, buildIdentity: v.buildIdentity, packageType: 'release-package', apiImage: api, postgresImage: pg, candidateSha256: input.candidate.candidateSha256, manifestSha256: v.manifestSha256, dataset, datasetPackageSha256: flowSha('synthetic-dataset'), datasetIdentity: { version: 'dataset-v1', sourceSha256: flowSha('synthetic-dataset'), releasePackageSha256: flowSha('synthetic-dataset') }, artifacts: { apiImage: api, postgresImage: pg, image: api, candidateSha256: input.candidate.candidateSha256, manifestSha256: v.manifestSha256 } };
    const labels = service => ({ 'com.docker.compose.project': project, 'io.podman.compose.project': project, 'lexiflow.installation': id, ...(service ? { 'com.docker.compose.service': service, 'io.podman.compose.service': service } : {}) });
    const volumeA = `${project}_pgdata`, volumeB = `${project}_initialization-work`;
    const install = { dir, state, labels, containers: [
      { Id: flowHex('1'), Image: api, State: { Running: true }, Config: { Labels: labels('api') } },
      { Id: flowHex('2'), Image: pg, State: { Running: true }, Config: { Labels: labels('postgres') }, Mounts: [{ Type: 'volume', Destination: '/var/lib/postgresql/data', Name: volumeA }] },
    ], volumes: [volumeA, volumeB].map(Name => ({ Name, CreatedAt: '2026-01-01T00:00:00Z', Labels: labels() })), networks: [{ Name: `${project}_default`, Labels: labels() }] };
    if (this.scenario === 'lock-claim') fs.mkdirSync(path.join(dir, '.lock-claim'));
    this.installs.set(dir, install); this.ext(install, v.buildIdentity); this.record(install, 'install', 'PASS', v.buildIdentity); this.save(install); return install;
  }
  change(install, input) {
    const v = this.verify(input.candidate), image = v.manifest.artifacts[0].imageDigest;
    install.state = { ...install.state, phase: 'ready', version: v.buildIdentity.softwareVersion, buildIdentity: v.buildIdentity, apiImage: image, artifacts: { ...install.state.artifacts, apiImage: image, image, candidateSha256: input.candidate.candidateSha256, manifestSha256: v.manifestSha256 } };
    install.containers[0] = { ...install.containers[0], Id: flowSha(`api-${++this.next}`), Image: image, State: { Running: true } };
    this.ext(install, v.buildIdentity); this.save(install);
  }
  async run(input) {
    const existing = this.installs.get(input.directory);
    if (!existing) {
      this.make(input);
      return { code: 0, signal: null, settled: this.scenario !== 'unsettled' };
    }
    if (input.action === 'install' || existing.state.buildIdentity.buildId === this.identify(input).buildId) return { code: 0, signal: null, settled: true };
    this.change(existing, input); this.record(existing, 'upgrade', 'PASS', this.identify(input)); this.save(existing);
    return { code: 0, signal: null, settled: true };
  }
  start(input) {
    const existing = this.installs.get(input.directory), old = structuredClone(existing.state);
    // 真正事务在readiness成功前仍保留旧state和extension，只切换运行中的API。
    existing.containers[0] = { ...existing.containers[0], Id: flowSha(`api-${++this.next}`),
      Image: this.verify(input.candidate).manifest.artifacts[0].imageDigest, State: { Running: true } };
    return { isClosed: () => this.scenario === 'missing-fault', wait: async () => {
      if (this.scenario === 'unsettled-fault') return { code: 1, signal: null, settled: false };
      if (this.scenario === 'missing-fault') return { code: 1, signal: null, settled: true };
      assert.equal(existing.containers[0].State.Running, false, 'failure must follow an observed stop');
      existing.state = old; existing.containers[0] = { ...existing.containers[0], Id: flowSha(`api-${++this.next}`), Image: old.apiImage, State: { Running: true } };
      this.ext(existing, old.buildIdentity); this.record(existing, 'upgrade', 'FAIL', this.identify(input)); this.save(existing);
      return { code: 1, signal: null, settled: true };
    } };
  }
  command(binary, argv) {
    assert.equal(binary, 'podman', 'no real Podman command is allowed');
    const json = value => ({ status: 0, stdout: `${JSON.stringify(value)}\n`, stderr: '', signal: null });
    if (argv[0] === 'machine') return json([{ State: 'running', ConnectionInfo: { PodmanSocket: { Path: this.socketPath } } }]);
    const installs = [...this.installs.values()];
    const ownerOf = id => installs.find(x => [...x.containers, ...x.volumes, ...x.networks].some(r => r.Id === id || r.Name === id));
    const collection = kind => installs.flatMap(x => kind === 'container' ? x.containers : kind === 'volume' ? x.volumes : x.networks);
    if (argv[0] === 'ps') return json(collection('container').map(x => ({ Id: x.Id, Labels: x.Config.Labels })));
    if (argv[1] === 'ls') return json(collection(argv[0]).map(x => ({ Name: x.Name, Labels: x.Labels })));
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      const candidate = Object.values(flowCandidates).find(v => v.manifest.artifacts[0].imageDigest === argv[2]);
      assert.ok(candidate); const i = candidate.buildIdentity;
      return json([{ Id: argv[2], Os: 'linux', Architecture: 'arm64', Config: { Labels: { 'org.opencontainers.image.version': i.softwareVersion, 'org.opencontainers.image.revision': i.sourceCommit, 'io.lexiflow.build-id': i.buildId, 'io.lexiflow.source-sha256': i.sourceSha256 } } }]);
    }
    if (argv[0] === 'inspect' || argv[1] === 'inspect') {
      const id = argv.at(-1), kind = argv[0] === 'inspect' ? 'container' : argv[0];
      const value = collection(kind).find(x => x.Id === id || x.Name === id); assert.ok(value, `unknown ${kind} ${id}`);
      if (this.scenario === 'wrong-owner' && kind === 'container' && value.Config?.Labels?.['com.docker.compose.service'] === 'api' && this.mutations.length === 0) return json([{ ...value, Config: { Labels: { ...value.Config.Labels, 'lexiflow.installation': 'foreign' } } }]);
      return json([value]);
    }
    if (argv[0] === 'exec') return { status: 0, stdout: 'lexiflow-candidate-runtime-v1\n', stderr: '', signal: null };
    if (argv[0] === 'stop') { const id = argv.at(-1), install = ownerOf(id); assert.ok(install); assert.equal(install.containers[0].Id, id); install.containers[0].State.Running = false; this.stops++; this.mutations.push(['stop', id]); return json({}); }
    if (argv[0] === 'rm' || argv[1] === 'rm') {
      const kind = argv[0] === 'rm' ? 'container' : argv[0], id = argv.at(-1), install = ownerOf(id); assert.ok(install);
      const key = kind === 'container' ? 'containers' : kind === 'volume' ? 'volumes' : 'networks';
      install[key] = install[key].filter(x => x.Id !== id && x.Name !== id); this.mutations.push(['remove', kind, id]); return json({});
    }
    throw Error(`UNEXPECTED_FAKE_COMMAND ${argv.join(' ')}`);
  }
}

test('copied runtime orchestrates all thirteen phases, real injected stop, recovery, retry, and owned cleanup', async () => {
  await withRuntimeFlowFixture('success', async (runtime, flow) => {
    const result = await runtime.runCandidateRuntime({ previous: flowPrevious, target: flowTarget });
    assert.equal(result.status, 'PASS', JSON.stringify(result));
    assert.deepEqual(result.phases.map(p => [p.id, p.status]), runtime.REQUIRED_PHASE_IDS.map(id => [id, 'PASS']));
    assert.equal(flow.stops, 1);
    assert.equal(result.cleanup.settled, true);
    assert.equal(flow.mutations.filter(x => x[0] === 'remove').length, 10); // two installs, 2 containers + 1 network + 2 volumes each
    const previous = [...flow.installs.values()].find(x => x.dir.endsWith('/previous-install'));
    assert.equal(previous.state.postgresImage, flowCandidates[flowPrevious.candidateSha256].manifest.artifacts[1].imageDigest, 'upgrade must preserve previous PG image');
    assert.equal(previous.state.artifacts.candidateSha256, flowTarget.candidateSha256);
  });
});

test('missing fault can never claim PASS even when upgrade child exits nonzero', async () => {
  await withRuntimeFlowFixture('missing-fault', async (runtime, flow) => {
    const result = await runtime.runCandidateRuntime({ previous: flowPrevious, target: flowTarget });
    assert.equal(result.status, 'FAIL', JSON.stringify(result)); assert.equal(result.failure.code, 'FAULT_NOT_PROVEN');
    assert.equal(flow.stops, 0); assert.equal(runtime.eligibleForPass(result), false);
  });
});

test('unsettled child retains every unknown resource, without remove mutation', async () => {
  await withRuntimeFlowFixture('unsettled', async (runtime, flow) => {
    const result = await runtime.runCandidateRuntime({ previous: flowPrevious, target: flowTarget });
    assert.equal(result.status, 'FAIL', JSON.stringify(result)); assert.equal(result.failure.code, 'SESSION_NOT_SETTLED');
    assert.equal(result.cleanup.settled, false); assert.equal(flow.mutations.length, 0);
    assert.equal([...flow.installs.values()][0].containers.length, 2);
  });
});

test('foreign owner is never removed, including catch-path cleanup retry', async () => {
  await withRuntimeFlowFixture('wrong-owner', async (runtime, flow) => {
    const result = await runtime.runCandidateRuntime({ previous: flowPrevious, target: flowTarget });
    assert.equal(result.status, 'FAIL', JSON.stringify(result)); assert.equal(result.failure.code, 'RESOURCE_OWNER_MISMATCH');
    assert.equal(result.cleanup.status, 'FAIL'); assert.equal(flow.mutations.length, 0);
  });
});


test('unknown handoff lock blocks every cleanup mutation and preserves resources', async () => {
  await withRuntimeFlowFixture('lock-claim', async (runtime, flow) => {
    const result = await runtime.runCandidateRuntime({ previous: flowPrevious, target: flowTarget });
    assert.equal(result.status, 'FAIL', JSON.stringify(result));
    assert.equal(result.failure.code, 'CLEANUP_LOCK_UNKNOWN');
    assert.equal(result.cleanup.status, 'FAIL');
    assert.equal(flow.mutations.length, 0);
    const install = [...flow.installs.values()][0];
    assert.equal(install.containers.length, 2);
    assert.equal(fs.statSync(path.join(install.dir, '.lock-claim')).isDirectory(), true);
  });
});
