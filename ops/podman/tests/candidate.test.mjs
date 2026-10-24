import { packageGeneration } from '../installation/installation-upgrade-workflow.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dataGeneration } from '../../dataset/dataset-generation-identity.mjs';
import { makeZip } from '../../release/tests/zip-fixture.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
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


test('owned process transport closes after parent cancellation', async () => {
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


test('source package generation consumes the current export object contract and rejects legacy or corrupt records', async () => {
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
