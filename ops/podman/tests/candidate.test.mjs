import { packageGeneration } from '../installation/installation-upgrade-workflow.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { assembleReleaseCandidate } from '../../release/build/release-candidate-assembler.mjs';
import { resolveBuildIdentity } from '../../release/integrity/source-build-identity.mjs';
import { extensionBytes, makeZip } from '../../release/tests/zip-fixture.mjs';
import { dataGeneration } from '../../dataset/dataset-generation-identity.mjs';
import { readCandidate, installCandidateArtifacts } from '../candidates/candidate-artifact-installer.mjs';
import { dockerSaveFixture, dockerSaveIdentity } from './docker-save-fixture.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
// CI provisions Python on PATH; do not pin a developer-only ignored venv.
const python = 'python3';

const transportOwnerDriver = String.raw`
import json, os, pathlib, sys, traceback
repo, bin_dir, owner_dir = map(pathlib.Path, sys.argv[1:4])
sys.path.insert(0, str(repo / 'ops/docker/tests'))
from podman_fake_transport import PodmanFakeTransport, _CANDIDATE_ROUTE
transport = None
status = 0
try:
    transport = PodmanFakeTransport(bin_dir / 'podman', bin_dir, owner_dir, _CANDIDATE_ROUTE)
    transport.start()
    print(f'READY {transport.server.pid}', flush=True)
    if sys.stdin.buffer.readline() != b'LEASE\n':
        raise RuntimeError('owner lease missing')
    if sys.stdin.buffer.read(1):
        raise RuntimeError('unexpected owner lease data')
except BaseException:
    traceback.print_exc(file=sys.stderr)
    status = 1
finally:
    if transport is not None:
        server = transport.server
        try:
            transport.close()
        except BaseException:
            traceback.print_exc(file=sys.stderr)
            status = 1
        pid = server.pid if server is not None else 0
        code = server.returncode if server is not None else -1
        try:
            (owner_dir / 'candidate-transport-owner.closed').write_text(
                json.dumps({'pid': pid, 'exit_code': code, 'owner_status': status}) + '\n'
            )
        except BaseException:
            traceback.print_exc(file=sys.stderr)
            status = 1
        try:
            print(f'CLOSED {pid} {code}', flush=True)
        except BrokenPipeError:
            sys.stdout = open(os.devnull, 'w')
raise SystemExit(status)
`;

async function startCandidateTransportOwner(root, bin) {
  const child = spawn(python, ['-u', '-c', transportOwnerDriver, repo, bin, root], {
    cwd: repo, detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stdout = '', stderr = '', buffered = '';
  let stdinError = null;
  const lines = [];
  const waiters = [];
  child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
  child.stdin.on('error', error => { stdinError = error; });
  child.stdout.on('data', chunk => {
    stdout += chunk; buffered += chunk;
    for (;;) {
      const newline = buffered.indexOf('\n');
      if (newline < 0) break;
      const line = buffered.slice(0, newline); buffered = buffered.slice(newline + 1);
      const waiter = waiters.shift(); if (waiter) waiter.resolve(line); else lines.push(line);
    }
  });
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-65536); });
  const exited = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal }));
  });
  const nextLine = () => lines.length ? Promise.resolve(lines.shift()) : new Promise((resolve, reject) => waiters.push({ resolve, reject }));
  const withTimeout = (promise, milliseconds, label) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds);
    promise.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  const killOwnedGroupAndWait = async () => {
    if (process.platform === 'win32') child.kill('SIGKILL');
    else {
      try { process.kill(-child.pid, 'SIGKILL'); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    return withTimeout(exited, 5000, 'owned candidate owner group reap');
  };
  let serverPid = null;
  try {
    const ready = await withTimeout(Promise.race([
      nextLine(), exited.then(result => { throw new Error(`owner exited before READY: ${JSON.stringify(result)} ${stderr}`); }),
    ]), 5000, 'candidate transport owner startup');
    const match = /^READY ([1-9][0-9]*)$/.exec(ready);
    if (!match) throw new Error(`invalid candidate transport owner readiness: ${ready}`);
    serverPid = Number(match[1]);
    if (!child.stdin.write('LEASE\n')) throw new Error('candidate transport owner lease write failed');
    await new Promise(resolve => setImmediate(resolve));
    if (stdinError) throw new Error(`candidate transport owner lease failed: ${stdinError.message}`);
  } catch (error) {
    if (!child.stdin.destroyed && !child.stdin.writableEnded) child.stdin.end();
    try { await withTimeout(exited, 7000, 'candidate owner startup cleanup'); }
    catch (cleanupError) {
      try { await killOwnedGroupAndWait(); }
      catch (reapError) { throw new AggregateError([error, cleanupError, reapError], 'candidate owner startup cleanup failed'); }
    }
    throw new Error(`${error.message}; owner stdout=${stdout}; stderr=${stderr}`);
  }
  let closed = false;
  return {
    serverPid,
    async close() {
      if (closed) return; closed = true;
      if (!child.stdin.destroyed && !child.stdin.writableEnded) child.stdin.end();
      let result;
      try { result = await withTimeout(exited, 8000, 'candidate transport owner EOF cleanup'); }
      catch (error) {
        let reaped;
        try { reaped = await killOwnedGroupAndWait(); }
        catch (reapError) { throw new AggregateError([error, reapError], 'candidate transport owner cleanup/reap failed'); }
        throw new Error(`${error.message}; owned group reaped ${JSON.stringify(reaped)}; owner stdout=${stdout}; stderr=${stderr}`);
      }
      if (stdinError) throw new Error(`candidate transport owner lease pipe failed: ${stdinError.message}; ${stderr}`);
      const closeLine = lines.shift() ?? buffered.trim();
      assert.deepEqual(result, { code: 0, signal: null }, `transport owner exit; ${stderr}`);
      assert.equal(closeLine, `CLOSED ${serverPid} 0`, `transport server close record; ${stdout} ${stderr}`);
      const marker = JSON.parse(await fs.readFile(path.join(root, 'candidate-transport-owner.closed'), 'utf8'));
      assert.deepEqual(marker, { pid: serverPid, exit_code: 0, owner_status: 0 });
    },
  };
}

async function assembled(baseVersion = '2.0.0-SNAPSHOT', sqlText = 'synthetic schema\n') {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-install-')));
  const out = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-output-')));
  const artifactRoot = path.join(root, 'input'); await fs.mkdir(path.join(root, 'ops/release'), { recursive: true }); await fs.mkdir(artifactRoot);
  await fs.writeFile(path.join(root, '.gitignore'), '/input/\n/images/\n/output/\n/descriptor.json\n');
  await fs.writeFile(path.join(root, 'ops/release/version.txt'), `${baseVersion}\n`);
  for (const file of ['integrity/source-build-identity.mjs', 'build/release-runtime-entry-generator.mjs', 'runtime/release-artifact-verification.sh', 'runtime/release-installation-state.sh', 'runtime/release-installation-docker.sh', 'runtime/release-installation-lifecycle.sh']) {
    await fs.mkdir(path.dirname(path.join(root, 'ops/release', file)), { recursive: true });
    await fs.writeFile(path.join(root, 'ops/release', file), await fs.readFile(path.join(repo, 'ops/release', file)));
  }
  execFileSync('git', ['init', '-q', root]); execFileSync('git', ['-C', root, 'add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean candidate fixture']);
  const identity = resolveBuildIdentity(root), commit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const licenses = [], records = [], components = ['LexiFlow', 'extension-third-party', 'API-runtime', 'PostgreSQL', 'dataset'];
  const ids = ['lexiflow', 'extension', 'api', 'postgres', 'dataset'];
  for (let i = 0; i < ids.length; i++) {
    const noticePath = `licenses/${ids[i]}.txt`, bytes = Buffer.from(`Synthetic ${ids[i]} notice\n`);
    await fs.mkdir(path.dirname(path.join(artifactRoot, noticePath)), { recursive: true }); await fs.writeFile(path.join(artifactRoot, noticePath), bytes);
    licenses.push({ id: ids[i], component: components[i], licenseId: `TEST-${ids[i]}`, licenseName: `Test ${ids[i]}`, sourceUrl: `https://example.invalid/${ids[i]}`, noticePath, noticeBytes: bytes.length, noticeSha256: hash(bytes) });
    records.push({ role: 'license', path: noticePath, bytes: bytes.length, sha256: hash(bytes), licenseIds: [ids[i]] });
  }
  const shardInputs = [];
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const shard = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-shard-'))), shardRecords = [];
    for (const [role, filename, licenseIds] of [
      ['api-image', 'api-image.tar', ['lexiflow', 'api']],
      ['postgres-image', 'postgres-image.tar', ['postgres']],
    ]) {
      const { bytes, imageDigest } = dockerSaveFixture({ role: role === 'api-image' ? 'api' : 'postgres', identity, platform });
      const relative = `images/${platform.replace('/', '-')}/${filename}`;
      await fs.mkdir(path.dirname(path.join(artifactRoot, relative)), { recursive: true }); await fs.writeFile(path.join(artifactRoot, relative), bytes);
      const record = { role, platform, path: relative, bytes: bytes.length, sha256: hash(bytes), imageDigest, licenseIds };
      records.push({ role, platform, licenseIds }); shardRecords.push(record);
      const copy = path.join(shard, relative); await fs.mkdir(path.dirname(copy), { recursive: true }); await fs.writeFile(copy, bytes);
    }
    const baseImages = [{ platform, javaRuntime: { reference: `registry.example.invalid/java/runtime@sha256:${'1'.repeat(64)}`, imageId: `sha256:${'c'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres/runtime@sha256:${'2'.repeat(64)}`, imageId: `sha256:${'d'.repeat(64)}` } }];
    const shardManifest = { schemaVersion: 1, kind: 'lexiflow-image-candidate', buildIdentity: identity, softwareVersion: identity.softwareVersion, sourceCommit: commit,
      platform, buildInputSha256: 'e'.repeat(64), baseImages, artifacts: shardRecords.map(({ licenseIds: _ids, ...item }) => item) };
    const marker = Buffer.from(`${JSON.stringify(shardManifest, null, 2)}\n`); await fs.writeFile(path.join(shard, 'candidate.json'), marker);
    shardInputs.push({ directory: shard, sha256: hash(marker) });
  }
  const add = async (role, relative, bytes, licenseIds, metadata) => {
    const target = path.join(artifactRoot, relative); await fs.mkdir(path.dirname(target), { recursive: true }); await fs.writeFile(target, bytes);
    records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, ...(metadata ? { metadata } : {}) });
  };
  const compose = await fs.readFile(path.join(repo, 'ops/docker/compose.yaml'));
  await add('compose', 'compose.yaml', compose, ['lexiflow']); await add('sql', 'database/schema.sql', Buffer.from(sqlText), ['lexiflow']);
  const dataset = { releaseId: 'release-test', preparationId: 'prep-test', ruleId: 'rules-test' };
  const generation = dataGeneration({ codeVersion: 'f'.repeat(64), inputSha256: hash('fixture csv'), upstream: {} });
  const packageManifest = { schemaVersion: 2, generationVersion: generation.generationVersion,
    generationCodeVersion: generation.codeVersion, generationSourceDigest: generation.sourceDigest,
    generationInputs: { inputSha256: generation.inputSha256, upstream: generation.upstream },
    generationConfig: generation.config };
  await add('dataset', 'data/dataset.zip', makeZip([['manifest.json', JSON.stringify(packageManifest)]]), ['dataset'], { ...dataset, sqlVersion: 'sql-test' });
  await add('extension', 'extension.zip', extensionBytes(identity), ['lexiflow', 'extension'], { softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit });
  const descriptor = { schemaVersion: 1, buildIdentity: identity, softwareVersion: identity.softwareVersion, sourceCommit: commit, apiContract: 'api-test', sqlVersion: 'sql-test', dataset, platforms: ['linux/amd64', 'linux/arm64'], artifacts: records, licenses };
  const result = await assembleReleaseCandidate({ repoRoot: root, imageCandidates: shardInputs, artifactRoot, descriptor, outputParent: out });
  const marker = await fs.readFile(path.join(result.candidateDirectory, 'candidate.json'));
  return { ...result, input: { candidateDirectory: result.candidateDirectory, candidateSha256: hash(marker) }, identity, generation, root, out, shardInputs,
    cleanup: async () => { await fs.rm(root, { recursive: true, force: true }); await fs.rm(out, { recursive: true, force: true }); for (const shard of shardInputs) await fs.rm(shard.directory, { recursive: true, force: true }); } };
}

test('candidate adapter consumes a clean assembled archive and loads only inspected ARM64 identities', async t => {
  const f = await assembled(); t.after(f.cleanup);
  const candidate = await readCandidate(f.input);
  assert.equal(candidate.candidateSha256, f.input.candidateSha256);
  const ids = { api: candidate.artifacts['api-image:linux/arm64'].imageDigest, postgres: candidate.artifacts['postgres-image:linux/arm64'].imageDigest }, calls = [];
  const aliases = new Map();
  const run = async (_cmd, argv, options = {}) => {
    calls.push(argv);
    if (argv[0] === 'load') { const loaded = dockerSaveIdentity(await fs.readFile(argv.at(-1))); aliases.set(loaded.alias, loaded.imageDigest); return ''; }
    if (argv[0] === 'image' && argv[1] === 'exists') return { commandExitCode: 1, stdout: '' };
    if (argv[0] === 'images') return JSON.stringify([{ Id: ids.api }, { Id: ids.postgres }]);
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      const id = aliases.get(argv.at(-1)) ?? argv.at(-1);
      return JSON.stringify([{ Id: id, Os: 'linux', Architecture: 'arm64', Config: { Labels: {
        'org.opencontainers.image.version': f.identity.softwareVersion, 'org.opencontainers.image.revision': f.identity.sourceCommit,
        'io.lexiflow.build-id': f.identity.buildId, 'io.lexiflow.source-sha256': f.identity.sourceSha256,
        'io.lexiflow.image-role': id === ids.api ? 'api' : 'postgres',
        'io.lexiflow.deployment-version': f.identity.baseVersion.toLowerCase(),
      } } }]);
    }
    return '';
  };
  const installDir = path.join(f.root, 'installation'); await fs.mkdir(installDir);
  const result = await installCandidateArtifacts(candidate, { run, installDir, apiPort: 18080, baseVersion: f.identity.baseVersion, installationId: '1'.repeat(32) });
  assert.equal(result.apiImage, ids.api); assert.equal(result.postgresImage, ids.postgres);
  assert.ok(calls.some(argv => argv[0] === 'load'));
  assert.equal(await fs.readFile(path.join(installDir, 'extension/manifest.json'), 'utf8').then(text => JSON.parse(text).version_name), f.identity.softwareVersion);
  await assert.rejects(installCandidateArtifacts(candidate, { run, installDir, apiPort: 18081 }), /CANDIDATE_API_PORT_FIXED/);
  await assert.rejects(readCandidate({ ...f.input, candidateSha256: '0'.repeat(64) }), /CANDIDATE/);
  const prep = path.join(f.root, 'upgrade-prep'); await fs.mkdir(prep);
  const before = calls.filter(argv => argv[0] === 'load').length;
  await installCandidateArtifacts(candidate, { run, installDir: prep, apiPort: 18080, includePostgres: false, baseVersion: f.identity.baseVersion, installationId: '1'.repeat(32) });
  assert.equal(calls.filter(argv => argv[0] === 'load').length, before + 1, 'upgrade preparation must not load PostgreSQL');
  await fs.writeFile(path.join(prep, 'extension/unknown'), 'private');
  await assert.rejects(installCandidateArtifacts(candidate, { run, installDir: prep, apiPort: 18080, includePostgres: false, baseVersion: f.identity.baseVersion, installationId: '1'.repeat(32) }), /EXTENSION_TARGET_EXISTS/);
  assert.equal(await fs.readFile(path.join(prep, 'extension/unknown'), 'utf8'), 'private');
});

test('candidate request rejects caller-injected fields', async () => {
  await assert.rejects(readCandidate({ candidateDirectory: '/tmp/candidate', candidateSha256: 'a'.repeat(64), pass: true }), /ARGUMENTS_INVALID/);
});

test('candidate transport owner closes its private server after parent cancellation', async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-owner-cancel-')));
  const bin = path.join(root, 'bin');
  let parent = null;
  let parentExit = null;
  let parentClosed = false;
  let failure = null;
  const cleanupErrors = [];
  const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const withTimeout = (promise, milliseconds, label) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds);
    promise.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  const waitForJson = async (file, milliseconds) => {
    const deadline = Date.now() + milliseconds;
    while (Date.now() < deadline) {
      try { return JSON.parse(await fs.readFile(file, 'utf8')); }
      catch { await wait(20); }
    }
    return null;
  };
  const waitGone = async pid => {
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
      try { process.kill(pid, 0); }
      catch (error) { if (error.code === 'ESRCH') return true; throw error; }
      await wait(20);
    }
    return false;
  };
  try {
    await fs.mkdir(bin);
    const model = path.join(bin, 'podman');
    await fs.writeFile(model, '#!/usr/bin/env python3\nprint("synthetic")\n'); await fs.chmod(model, 0o700);
    const parentSource = `import fs from 'node:fs'; import {spawn} from 'node:child_process';
const ownerDir=${JSON.stringify(root)}, bin=${JSON.stringify(bin)};
let owner=null, serverPid=null, cancelling=false;
const writeResult=(code,signal)=>{fs.writeFileSync(ownerDir+'/candidate-owner-parent.closed',JSON.stringify({ownerPid:owner?.pid??null,serverPid,code,signal})+'\\n');};
function cancel(){if(cancelling)return;cancelling=true;if(!owner){process.exitCode=143;return;}owner.stdin.end();const timer=setTimeout(()=>{try{process.kill(-owner.pid,'SIGKILL');}catch{}},7000);owner.once('close',(code,signal)=>{clearTimeout(timer);writeResult(code,signal);process.exitCode=143;});}
process.once('SIGTERM',cancel);
owner=spawn(${JSON.stringify(python)},['-u','-c',${JSON.stringify(transportOwnerDriver)},${JSON.stringify(repo)},bin,ownerDir],{detached:${process.platform !== 'win32'},stdio:['pipe','pipe','ignore']});
let pending='';owner.stdout.setEncoding('utf8');
owner.stdout.on('data',chunk=>{pending+=chunk;for(;;){const i=pending.indexOf('\\n');if(i<0)break;const line=pending.slice(0,i);pending=pending.slice(i+1);const match=/^READY ([1-9][0-9]*)$/.exec(line);if(match){serverPid=Number(match[1]);process.stdout.write('READY '+owner.pid+' '+serverPid+'\\n');if(!owner.stdin.write('LEASE\\n'))throw new Error('lease write failed');}}});
owner.once('close',(code,signal)=>{if(!cancelling){writeResult(code,signal);process.exitCode=code===0?0:1;}});
owner.once('error',error=>{console.error(error);process.exitCode=1;});`;
    parent = spawn(process.execPath, ['--input-type=module', '-e', parentSource], { stdio: ['ignore', 'pipe', 'pipe'] });
    let errors = '', output = '', buffered = '', readyResolve;
    const ready = new Promise(resolve => { readyResolve = resolve; });
    parent.stdout.setEncoding('utf8'); parent.stderr.setEncoding('utf8');
    parent.stdout.on('data', chunk => {
      output += chunk; buffered += chunk;
      const newline = buffered.indexOf('\n');
      if (newline >= 0) readyResolve(buffered.slice(0, newline));
    });
    parent.stderr.on('data', chunk => { errors = (errors + chunk).slice(-65536); });
    parentExit = new Promise((resolve, reject) => {
      parent.once('error', reject);
      parent.once('close', (code, signal) => { parentClosed = true; resolve({ code, signal }); });
    });
    const readyLine = await withTimeout(Promise.race([
      ready, parentExit.then(result => { throw new Error(`cancel-test parent exited before readiness: ${JSON.stringify(result)} ${errors}`); }),
    ]), 6000, 'cancel-test parent startup');
    const match = /^READY ([1-9][0-9]*) ([1-9][0-9]*)$/.exec(readyLine);
    assert.ok(match, `invalid parent readiness: ${readyLine}; ${errors}`);
    const [ownerPid, serverPid] = match.slice(1).map(Number);
    assert.equal(parent.kill('SIGTERM'), true, 'parent cancellation signal was not delivered');
    assert.deepEqual(await withTimeout(parentExit, 9000, 'cancel-test parent cleanup'), { code: 143, signal: null }, errors);
    const closed = await waitForJson(path.join(root, 'candidate-transport-owner.closed'), 1000);
    const parentRecord = await waitForJson(path.join(root, 'candidate-owner-parent.closed'), 1000);
    assert.deepEqual(closed, { pid: serverPid, exit_code: 0, owner_status: 0 });
    assert.deepEqual(parentRecord, { ownerPid, serverPid, code: 0, signal: null });
    assert.equal(await waitGone(ownerPid), true, `owned transport owner ${ownerPid} survived parent cancellation`);
    assert.equal(await waitGone(serverPid), true, `owned transport server ${serverPid} survived parent cancellation`);
  } catch (error) {
    failure = error;
  } finally {
    if (parent && !parentClosed) {
      try { if (parent.exitCode === null && parent.signalCode === null) parent.kill('SIGTERM'); }
      catch (error) { cleanupErrors.push(error); }
      if (parentExit) {
        try { await withTimeout(parentExit, 9000, 'cancel-test cleanup after assertion failure'); }
        catch (error) {
          cleanupErrors.push(error);
          try {
            if (parent.exitCode === null && parent.signalCode === null) parent.kill('SIGKILL');
            await withTimeout(parentExit, 5000, 'cancel-test parent reap after SIGKILL');
            parentClosed = true;
          } catch (killError) { cleanupErrors.push(killError); }
        }
      }
    }
    const ownerProof = await waitForJson(path.join(root, 'candidate-transport-owner.closed'), 9000);
    const parentProof = await waitForJson(path.join(root, 'candidate-owner-parent.closed'), 1000);
    let ownedProcessesGone = true;
    for (const pid of [parentProof?.ownerPid, parentProof?.serverPid].filter(Number.isInteger)) {
      try {
        if (!await waitGone(pid)) {
          try { if (process.platform === 'win32') process.kill(pid, 'SIGKILL'); else process.kill(-pid, 'SIGKILL'); }
          catch (error) { if (error.code !== 'ESRCH') cleanupErrors.push(error); }
          if (!await waitGone(pid)) { ownedProcessesGone = false; cleanupErrors.push(new Error(`owned pid ${pid} survived cancellation cleanup`)); }
        }
      } catch (error) { ownedProcessesGone = false; cleanupErrors.push(error); }
    }
    if (ownedProcessesGone && parentClosed && ownerProof && parentProof) {
      try { await fs.rm(root, { recursive: true, force: true }); }
      catch (error) { cleanupErrors.push(error); }
    } else {
      cleanupErrors.push(new Error(`owned transport cleanup unproven; preserved ignored root ${root}`));
    }
  }
  if (failure && cleanupErrors.length) throw new AggregateError([failure, ...cleanupErrors], 'cancellation assertion and cleanup failed');
  if (failure) throw failure;
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'cancellation cleanup failed');
});

test('runLocal installs, refreshes, upgrades and retries candidates through shared lifecycle with isolated Podman/network substitutes', async t => {
  const first = await assembled('8.0.0-SNAPSHOT'), second = await assembled('8.0.1-SNAPSHOT');
  const incompatible = await assembled('8.0.2-SNAPSHOT', 'incompatible schema\n');
  let transportOwner = null;
  t.after(async () => {
    try { if (transportOwner) await transportOwner.close(); }
    finally { await first.cleanup(); await second.cleanup(); await incompatible.cleanup(); }
  });
  const root = path.join(first.root, 'local-run'), dir = path.join(root, 'private-install'), bin = path.join(root, 'bin');
  await fs.mkdir(bin, { recursive: true }); const callsFile = path.join(root, 'podman-calls.jsonl');
  const badLabelMarker = path.join(root, 'bad-label-once'), failMarker = path.join(root, 'api-start-failure');
  const cancelMarker = path.join(root, 'cancel-once');
  const imageMap = {};
  const archiveMap = {};
  for (const candidate of [first, second, incompatible]) for (const role of ['api-image', 'postgres-image']) {
    const key = role + ':linux/arm64';
    const proof = await readCandidate(candidate.input), artifact = proof.artifacts[key];
    imageMap[artifact.imageDigest] = { Id: artifact.imageDigest, Os: 'linux', Architecture: 'arm64', Config: { Labels: {
      'org.opencontainers.image.version': proof.buildIdentity.softwareVersion, 'org.opencontainers.image.revision': proof.buildIdentity.sourceCommit,
      'io.lexiflow.build-id': proof.buildIdentity.buildId, 'io.lexiflow.source-sha256': proof.buildIdentity.sourceSha256,
      'io.lexiflow.image-role': role === 'api-image' ? 'api' : 'postgres',
      'io.lexiflow.deployment-version': proof.buildIdentity.baseVersion.toLowerCase(),
    } } };
    imageMap[artifact.imageDigest].Labels = { ...imageMap[artifact.imageDigest].Config.Labels };
    archiveMap[path.join(candidate.candidateDirectory, 'payload', ...artifact.path.split('/'))] = artifact.imageDigest;
  }
  const podman = '#!/usr/bin/env python3\n';
  const podmanBody = String.raw`
import json, os, signal, sys, hashlib
from pathlib import Path
CALLS=Path(${JSON.stringify(callsFile)}); initial=json.loads(${JSON.stringify(JSON.stringify(imageMap))}); archives=json.loads(${JSON.stringify(JSON.stringify(archiveMap))}); kit=Path(${JSON.stringify(dir)})
DB=Path(${JSON.stringify(path.join(root, 'podman-state.json'))})
GENERATION=${JSON.stringify(first.generation.generationVersion)}
BAD_LABEL=Path(${JSON.stringify(badLabelMarker)}); FAIL_API=Path(${JSON.stringify(failMarker)}); CANCEL=Path(${JSON.stringify(cancelMarker)})
a=sys.argv[1:]; CALLS.open('a').write(json.dumps(a)+'\n')
s=json.loads(DB.read_text()) if DB.exists() else {'loaded':{},'tags':{},'containers':{},'generation':None,'datasetVersion':None,'sequence':0}
def save(): DB.write_text(json.dumps(s))
def image(key):
    ident=s['tags'].get(key,key)
    return s['loaded'].get(ident)
def env(name):
    return next((line.split('=',1)[1] for line in (kit/'release.env').read_text().splitlines() if line.startswith(name+'=')),None)
def labels(role):
    state=json.loads((kit/'state.json').read_text())
    return {'lexiflow.installation':state['id'],'com.docker.compose.project':state['project'],'com.docker.compose.service':role}
if a[:1]==['info']: print('{}')
elif a[:2]==['image','exists']:
    sys.exit(0 if a[2] in s['tags'] else 1)
elif a[:1]==['load']:
    if CANCEL.exists(): CANCEL.unlink(); os.kill(os.getppid(),signal.SIGINT)
    import tarfile
    with tarfile.open(a[-1]) as t:
        manifest=json.load(t.extractfile('manifest.json'))
    ident='sha256:'+manifest[0]['Config'].removesuffix('.json')
    if ident not in initial: sys.exit(7)
    s['loaded'][ident]=initial[ident]
    for tag in manifest[0]['RepoTags']: s['tags'][tag]=ident
    save(); print('UNTRUSTED load output ignored')
elif a[:1]==['tag']:
    if image(a[1]) is None: sys.exit(7)
    s['tags'][a[2]]=s['tags'].get(a[1],a[1]); save()
elif a[:1]==['rmi']:
    if a[-1] not in s['tags']: sys.exit(7)
    del s['tags'][a[-1]]; save()
elif a[:2]==['image','inspect']:
    fmt=a[a.index('--format')+1] if '--format' in a else None; key=a[2] if a[2]!='--format' else a[-1]; item=image(key)
    if item is None: print('missing image',file=sys.stderr); sys.exit(1)
    if BAD_LABEL.exists() and fmt in (None,'json'): BAD_LABEL.unlink(); item={**item,'Labels':{**item['Config']['Labels'],'io.lexiflow.build-id':'0'*64}}
    if fmt=='{{json .Labels}}': print(json.dumps(item['Config']['Labels']))
    elif fmt=='{{json .Os}} {{json .Architecture}}': print('"linux" "arm64"')
    elif fmt=='{{.Id}}': print(item['Id'])
    else: print(json.dumps([item]))
elif a[:1]==['ps']:
    rows=list(s['containers'].values()); rows=rows if '-a' in a else [row for row in rows if row['State']['Running']]
    if '--filter' in a:
        ancestor=a[a.index('--filter')+1].split('=',1)[1]
        rows=[row for row in rows if row['Image'].replace('sha256:','')==ancestor]
        print('\n'.join(row['Id'] for row in rows))
    else: print(json.dumps([{'Id':row['Id'],'Labels':row['Config']['Labels']} for row in rows]))
elif a[:2]==['container','inspect']:
    row=s['containers'].get(a[2]);
    if row is None: sys.exit(1)
    print(json.dumps([row]))
elif a[:1]==['inspect']:
    row=s['containers'].get(a[-1]);
    if row is None: sys.exit(1)
    print(row['Image'])
elif a[:1]==['stop']:
    row=s['containers'].get(a[-1]);
    if row is None: sys.exit(1)
    row['State']['Running']=False; save()
elif a[:1]==['rm']:
    if a[-1] not in s['containers']: sys.exit(1)
    del s['containers'][a[-1]]; save()
elif a[:2]==['volume','inspect']:
    state=json.loads((kit/'state.json').read_text())
    name=state['project']+'_pgdata'
    if a[2]!=name: sys.exit(1)
    print(json.dumps([{'Name':name,'Labels':labels('postgres')}]))
elif a[:1] in (['volume'],['network']): print('[]')
elif a[:1]==['run'] and '--deployment-data' in a:
    if 'apply' in a:
        s['generation']=GENERATION; s['datasetVersion']=7; save()
        print(json.dumps({'status':'PASS','rebuilt':True,'generationVersion':GENERATION,'datasetVersion':7}))
    else:
        complete=s['generation'] is not None
        print(json.dumps({'status':'PASS','database':'lexiflow','schema':'lexiflow_release','fingerprint':'f'*64,
          'needsRebuild':not complete,'reason':'OK' if complete else 'SCHEMA_MISSING',
          'generationVersion':s['generation'],'datasetVersion':s['datasetVersion']}))
elif a[:1]==['compose'] and 'logs' in a: print('synthetic logs')
elif a[:1]==['compose'] and 'ps' in a: print('synthetic status')
elif a[:1]==['compose'] and 'run' in a: pass
elif a[:1]==['compose'] and 'config' in a: print('valid')
elif a[:1]==['compose'] and 'up' in a:
    role=a[-1]
    if role=='api' and FAIL_API.exists(): FAIL_API.unlink(); sys.exit(8)
    ref=env('LEXIFLOW_'+('API' if role=='api' else 'POSTGRES')+'_IMAGE')
    item=image(ref)
    if item is None: print('compose image absent',file=sys.stderr); sys.exit(7)
    existing=[row for row in s['containers'].values() if row['Config']['Labels']['com.docker.compose.service']==role]
    if existing and '--force-recreate' not in a and existing[0]['Image']==item['Id']:
        existing[0]['State']['Running']=True; save(); sys.exit(0)
    for row in existing: del s['containers'][row['Id']]
    s['sequence']+=1
    ident=hashlib.sha256((role+item['Id']+str(s['sequence'])).encode()).hexdigest()
    row={'Id':ident,'Image':item['Id'],'Config':{'Labels':labels(role)},'State':{'Running':True}}
    if role=='postgres':
        state=json.loads((kit/'state.json').read_text())
        row['HostConfig']={'PortBindings':{'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':str(state['dbPort'])}]}}
        row['Mounts']=[{'Type':'volume','Name':state['project']+'_pgdata','Destination':'/var/lib/postgresql/data','RW':True}]
        row['State']['Health']={'Status':'healthy'}
    s['containers'][ident]=row
    save()
elif a[:1]==['compose'] and 'stop' in a:
    for role in a[a.index('stop')+1:]:
        for row in s['containers'].values():
            if row['Config']['Labels']['com.docker.compose.service']==role: row['State']['Running']=False
    save()
else: print('unexpected command '+json.dumps(a), file=sys.stderr); sys.exit(7)
`;
  const script = podman + podmanBody;
  const podmanFile = path.join(bin, 'podman'); await fs.writeFile(podmanFile, script); await fs.chmod(podmanFile, 0o755);
  // Keep the per-case model owner alive across CLI calls. Only its finite
  // non-signalling routes use the server; load/run/pull/logs remain native exec.
  transportOwner = await startCandidateTransportOwner(root, bin);
  const preload = path.join(root, 'preload.mjs');
  await fs.writeFile(preload, `import fs from 'node:fs'; import os from 'node:os'; import net from 'node:net'; import http from 'node:http'; import { EventEmitter } from 'node:events'; import { Readable } from 'node:stream';
os.platform=()=> 'darwin'; os.arch=()=> 'arm64';
net.createServer=()=>({ once(){return this;}, listen(_port,_host,cb){setImmediate(cb);return this;}, close(cb){setImmediate(cb);return this;} });
http.get=(options,callback)=>{const req=new EventEmitter(); req.destroy=(err)=>{if(err)req.emit('error',err);req.emit('close');}; setImmediate(()=>{const env=fs.readFileSync(process.env.INSTALL_DIR+'/release.env','utf8');const image=env.match(/^LEXIFLOW_API_IMAGE=(.+)$/m)?.[1];const versions=JSON.parse(process.env.IMAGE_VERSIONS);const value=options.path.endsWith('/readiness')?{status:'UP'}:{mode:'formal',ready:true,reason:'OK',softwareVersion:versions[image],apiContract:'caption-hints.v2',datasetVersion:7};const res=Readable.from([Buffer.from(JSON.stringify(value))]);res.statusCode=200;res.on('end',()=>req.emit('close'));callback(res);});return req;};`);
  const source = `import fs from 'node:fs'; import path from 'node:path'; import {runLocal} from ${JSON.stringify(pathToFileURL(path.join(repo,'ops/podman/commands/manage-podman-installation.mjs')).href)};\n`+
    `async function deploy(action,input){let hash;const log=console.log;console.log=(...parts)=>{for(const part of parts){try{const row=JSON.parse(part);if(row.planHash)hash=row.planHash;}catch{}}log(...parts);};const planned=await runLocal([action,'--dir',${JSON.stringify(dir)},'--plan'],input);console.log=log;return planned===0&&hash?runLocal([action,'--dir',${JSON.stringify(dir)},'--confirm-plan',hash],input):1;}\n`+
    `if(await runLocal(['install'],${JSON.stringify(first.input)})===0)throw new Error('candidate default dir accepted'); if(await runLocal(['install','--dir',${JSON.stringify(dir)},'--api-port','18081'],${JSON.stringify(first.input)})===0)throw new Error('candidate port override accepted'); if(fs.existsSync(${JSON.stringify(dir)}))throw new Error('invalid inputs created installation');\n`+
    `fs.writeFileSync(${JSON.stringify(badLabelMarker)},'1'); const rejected=await deploy('install',${JSON.stringify(first.input)}); if(rejected===0)throw new Error('bad image labels accepted');\n`+
    `const one=await deploy('install',${JSON.stringify(first.input)}); if(one!==0)process.exitCode=one;\n`+
    `const repeat=await deploy('install',${JSON.stringify(first.input)}); if(repeat!==0)process.exitCode=repeat;\n`+
    `const beforeUpgrade=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')); const oldPassword=fs.readFileSync(${JSON.stringify(path.join(dir,'secrets/app-password'))}); const oldDataset=fs.readFileSync(path.join(beforeUpgrade.dataset,'dataset.zip')); fs.writeFileSync(${JSON.stringify(path.join(root,'before-upgrade.json'))},JSON.stringify({state:beforeUpgrade,password:oldPassword.toString('hex'),dataset:oldDataset.toString('hex')})); fs.writeFileSync(${JSON.stringify(failMarker)},'1');\n`+
    `const failed=await deploy('upgrade',${JSON.stringify(second.input)}); if(failed===0)throw new Error('failed API start accepted');\n`+
    `const pending=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')); if(pending.phase!=='initialized'||pending.buildIdentity.buildId===beforeUpgrade.buildIdentity.buildId||pending.postgresImage===beforeUpgrade.postgresImage||pending.apiPort!==beforeUpgrade.apiPort||pending.dbPort!==beforeUpgrade.dbPort)throw new Error('forward target not preserved'); if(!fs.readFileSync(${JSON.stringify(path.join(dir,'secrets/app-password'))}).equals(oldPassword))throw new Error('password changed');\n`+
    `const recovery=await runLocal(['recover','--dir',${JSON.stringify(dir)}]); if(recovery!==0)process.exitCode=recovery;\n`+
    `fs.writeFileSync(${JSON.stringify(cancelMarker)},'1'); const cancelled=await deploy('upgrade',${JSON.stringify(incompatible.input)}); if(cancelled===0)throw new Error('cancelled upgrade accepted'); if(JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')).apiImage!==pending.apiImage)throw new Error('cancel changed installed target');\n`+
    `const refresh=await deploy('upgrade',${JSON.stringify(second.input)}); if(refresh!==0)process.exitCode=refresh;\n`+
    `if(await deploy('install',${JSON.stringify(second.input)})!==0)throw new Error('repeat install after upgrade rejected');\n`+
    `const sqlUpgrade=await deploy('upgrade',${JSON.stringify(incompatible.input)}); if(sqlUpgrade!==0)process.exitCode=sqlUpgrade;\n`+
    `for(const action of ['up','status','logs','stop']) { const code=await runLocal([action,'--dir',${JSON.stringify(dir)}]); if(code!==0)throw new Error(action+' requires candidate again or failed'); }\n`;
  const result = spawnSync(process.execPath, ['--import', preload, '--input-type=module', '-e', source], {
    encoding: 'utf8', timeout: 240000, env: { ...process.env, INSTALL_DIR: dir, IMAGE_VERSIONS: JSON.stringify(Object.fromEntries(Object.entries(imageMap).map(([id, image]) => [id, image.Config.Labels['org.opencontainers.image.version']]))), PATH: `${bin}${path.delimiter}${process.env.PATH}` },
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\nCALLS=${(await fs.readFile(callsFile,'utf8').catch(()=>'none')).split('\n').slice(-8).join('\n')}`);
  const installed = JSON.parse(await fs.readFile(path.join(dir, 'state.json'), 'utf8'));
  assert.equal(installed.phase, 'ready'); assert.equal(installed.version, (await readCandidate(incompatible.input)).buildIdentity.softwareVersion);
  assert.equal(installed.apiPort, 18080); assert.equal(installed.packageType, 'release-package');
  const before = JSON.parse(await fs.readFile(path.join(root, 'before-upgrade.json'), 'utf8'));
  assert.equal(installed.id, before.state.id); assert.equal(installed.project, before.state.project);
  assert.notEqual(installed.postgresImage, before.state.postgresImage); assert.equal(installed.apiPort, before.state.apiPort); assert.equal(installed.dbPort, before.state.dbPort);
  assert.deepEqual(installed.datasetIdentity, before.state.datasetIdentity);
  assert.equal((await fs.readFile(path.join(dir, 'secrets/app-password'))).toString('hex'), before.password);
  assert.equal((await fs.readFile(path.join(installed.dataset, 'dataset.zip'))).toString('hex'), before.dataset);
  assert.equal((await fs.readFile(path.join(dir, 'infra/postgres/schema.sql'), 'utf8')), 'incompatible schema\n');
  const calls = (await fs.readFile(callsFile, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  assert.ok(calls.filter(row => row[0] === 'load').length >= 4, 'candidate image loads are bounded to inspection/preparation');
  assert.equal(calls.filter(row => row.includes('initialize')).length, 0);
  assert.equal(calls.some(row => row[0] === 'images'), false);
});

test('candidate generation consumes the real latest export object contract and rejects legacy or corrupt records', async () => {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-generation-contract-')));
  try {
    const generation = dataGeneration({ codeVersion: 'a'.repeat(64), inputSha256: 'b'.repeat(64), upstream: {} });
    const manifest = { schemaVersion: 2, generationVersion: generation.generationVersion,
      generationCodeVersion: generation.codeVersion, generationSourceDigest: generation.sourceDigest,
      generationInputs: { inputSha256: generation.inputSha256, upstream: generation.upstream }, generationConfig: generation.config };
    const archive = path.join(directory, 'dataset.zip');
    await fs.writeFile(archive, makeZip([['manifest.json', JSON.stringify(manifest)]]));
    assert.deepEqual(await packageGeneration(archive), generation);
    for (const invalid of [
      { ...manifest, generationInputs: undefined, generationInputsJson: JSON.stringify(manifest.generationInputs) },
      { ...manifest, generationConfig: JSON.stringify(manifest.generationConfig) },
      { ...manifest, generationVersion: 'c'.repeat(64) },
      { ...manifest, generationSourceDigest: 'c'.repeat(64) },
      { ...manifest, schemaVersion: 1 },
    ]) {
      await fs.writeFile(archive, makeZip([['manifest.json', JSON.stringify(invalid)]]));
      await assert.rejects(packageGeneration(archive), { message: 'DEPLOYMENT_PACKAGE_GENERATION_INVALID' });
    }
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});
