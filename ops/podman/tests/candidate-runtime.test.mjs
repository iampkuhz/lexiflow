import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCandidateRuntime, REQUIRED_PHASE_IDS, validateCandidateRequest, eligibleForPass } from '../candidates/check-podman-candidate-lifecycle.mjs';
import { markPassIfComplete } from '../candidates/single-runtime-candidate.mjs';

const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const cli=path.join(repo,'ops/podman/candidates/check-podman-candidate-lifecycle.mjs');
const candidate={candidateDirectory:'/tmp/synthetic-release-candidate',candidateSha256:'a'.repeat(64)};
const request={target:candidate};

test('request contract is exact target-only and validates bounded absolute candidate identity',()=>{
  assert.equal(validateCandidateRequest(request),true);
  for(const bad of [null,{}, {previous:candidate,target:candidate}, {...request,command:['podman','rm']},
    {target:{...candidate,candidateDirectory:'relative'}},{target:{...candidate,candidateSha256:'bad'}},
    {target:candidate,status:'PASS'}]) assert.throws(()=>validateCandidateRequest(bad),/REQUEST_INVALID/);
});

test('only a complete ordered real-stage report with settled cleanup is eligible for PASS',()=>{
  const value={status:'PASS',failure:null,phases:REQUIRED_PHASE_IDS.map(id=>({id,status:'PASS'})),
    cleanup:{status:'PASS',settled:true,ownedResourcesRemoved:true}};
  assert.equal(eligibleForPass(value),true);
  for(const id of REQUIRED_PHASE_IDS){const bad=structuredClone(value);bad.phases.find(x=>x.id===id).status='NOT_RUN';assert.equal(eligibleForPass(bad),false,id);}
  for(const mutate of [x=>x.phases.reverse(),x=>x.cleanup.settled=false,x=>x.cleanup.ownedResourcesRemoved=false,x=>x.failure={code:'ERROR'}]){
    const bad=structuredClone(value);mutate(bad);assert.equal(eligibleForPass(bad),false);
  }
});

test('successful execution marks PASS only after all required phases and owned cleanup settle',()=>{
  const out={status:'FAIL',failure:null,phases:REQUIRED_PHASE_IDS.map(id=>({id,status:'PASS'})),cleanup:{status:'PASS',settled:true,ownedResourcesRemoved:true}};
  assert.equal(markPassIfComplete(out).status,'PASS');
  for(const mutate of [x=>x.phases.pop(),x=>x.phases[0].status='UNKNOWN',x=>x.phases.reverse(),x=>x.cleanup.status='UNKNOWN',x=>x.cleanup.settled=false,x=>x.cleanup.ownedResourcesRemoved=false,x=>x.failure={code:'X'}]){
    const bad=structuredClone(out);mutate(bad);assert.notEqual(markPassIfComplete(bad).status,'PASS');
  }
});

test('unsupported host is BLOCKED without claiming candidate execution or cleanup',async()=>{
  const original=Object.getOwnPropertyDescriptor(process,'platform');
  Object.defineProperty(process,'platform',{...original,value:'linux'});
  try {
    const result=await runCandidateRuntime(request);
    assert.equal(result.status,'BLOCKED');assert.equal(result.failure.code,'HOST_UNSUPPORTED');
    assert.equal(result.target,null);assert.equal(result.phases.slice(0,-1).every(p=>p.status==='NOT_RUN'),true);
    assert.equal(result.cleanup.ownedResourcesRemoved,false);
  } finally { Object.defineProperty(process,'platform',original); }
});

test('fixed CLI emits one bounded JSON result and never interprets malformed input as PASS',()=>{
  const invalid=spawnSync(process.execPath,[cli],{input:JSON.stringify({...request,extra:'forbidden'}),encoding:'utf8'});
  assert.equal(invalid.status,0);assert.equal(invalid.stderr,'');assert.equal(invalid.stdout.trim().split('\n').length,1);
  assert.equal(JSON.parse(invalid.stdout).failure.code,'REQUEST_INVALID');
  const malformed=spawnSync(process.execPath,[cli],{input:'{',encoding:'utf8'});
  assert.equal(malformed.status,0);assert.equal(JSON.parse(malformed.stdout).status,'FAIL');
});

// Exercise the real bounded lifecycle functions with fake Podman observations;
// this is a precise orchestration regression, not a real-container PASS.
async function lifecycleFunction(name, nextName, bindings) {
  const { readFile } = await import('node:fs/promises');
  const { Script, createContext } = await import('node:vm');
  const source = await readFile(path.join(repo, 'ops/podman/candidates/single-runtime-candidate.mjs'), 'utf8');
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  const end = source.search(new RegExp(`(?:async )?function ${nextName}\\(`));
  assert.ok(start >= 0 && end > start);
  const context = createContext({ ownerLabel: 'io.lexiflow.candidate-acceptance', ...bindings, fail: code => { throw new Error(code); } });
  return { execute: new Script(`${source.slice(start, end)}; ${name}`).runInContext(context), source };
}

test('unknown-volume probe waits for exit and rejects timeout or successful exit', async () => {
  for (const exit of [{ State: { ExitCode: 1 } }, null, { State: { ExitCode: 0 } }]) {
    let waited = false;
    // The setup write precedes the wait, so distinguish it from retention probes.
    const { execute: probe, source } = await lifecycleFunction('incompatibleVolume', 'faultExit', {
      createVolume: () => {}, containerRun: () => ({ ok: true }),
      cmd: (_, label) => { if (label !== 'incompatible-marker') assert.equal(waited, true); return { ok: true }; },
      observeWait: async () => { await Promise.resolve(); waited = true; return exit; },
    });
    if (exit?.State.ExitCode === 1) assert.equal(await probe('', 'container', 'image', 'volume', 'owner', {}, []), true);
    else await assert.rejects(probe('', 'container', 'image', 'volume', 'owner', {}, []), /INCOMPATIBLE_VOLUME_NOT_REJECTED/);
    assert.match(source, /await incompatibleVolume\(root,badName/u);
  }
});

test('owned running-container cleanup confirms graceful stop before removal', async () => {
  for (const stopOk of [true, false]) {
    const calls = [];
    const id = 'a'.repeat(64);
    let state = 'running';
    const { execute } = await lifecycleFunction('removeContainer', 'removeVolume', {
      inspectContainer: () => state === 'removed' ? null : { Id: id, State: { Status: state } },
      assertOwned: () => {},
      cmd: (_, label, args) => {
        calls.push([label, Array.from(args)]);
        if (label === 'container-stop') { if (stopOk) state = 'exited'; return { ok: stopOk }; }
        assert.equal(state, 'exited'); state = 'removed'; return { ok: true };
      },
    });
    if (stopOk) { execute('', 'container', 'owner'); assert.deepEqual(calls.map(c => c[0]), ['container-stop', 'container-remove']); }
    else { assert.throws(() => execute('', 'container', 'owner'), /CONTAINER_STOP_FAILED/); assert.deepEqual(calls.map(c => c[0]), ['container-stop']); }
  }
});

test('port-conflict probe waits for listener binding before running a container', async () => {
  let bound = false, closed = false;
  const server = {
    once: () => {},
    listen: (_, __, ready) => { queueMicrotask(() => { bound = true; ready(); }); },
    close: done => { closed = true; done(); },
  };
  const { execute, source } = await lifecycleFunction('reservePortFailure', 'nameConflictFailure', {
    net: { createServer: () => server },
    containerRun: () => { assert.equal(bound, true); return { ok: false }; },
    inspectContainer: () => null, removeContainer: () => {}, assertOwned: () => {},
  });
  assert.equal(await execute('', 'image', 'owner', null, []), true);
  assert.equal(closed, true);
  assert.match(source, /await reservePortFailure\(root/u);
});

test('nonzero supervised exit is unavailable while successful exit cannot prove a component fault', async () => {
  for (const exitCode of [0, 1, null]) {
    const { execute } = await lifecycleFunction('observeUnhealthy', 'reservePortFailure', {
      waitUntil: async fn => fn(),
      inspectContainer: () => ({ State: { Status: 'exited', ExitCode: exitCode } }),
      apiState: () => { throw new Error('exited container has no API'); },
    });
    assert.equal(await execute('', 'container', {}), exitCode === 1 ? true : null);
  }
});

test('real kill-probe shells select the intended process, never their own earlier-sorted bash cmdline', async t => {
  const { mkdtemp, mkdir, writeFile, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const root = await mkdtemp(path.join(tmpdir(), 'lexiflow-proc-fixture-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const scripts = [];
  const { execute } = await lifecycleFunction('faultExit', 'runInner', {
    volumeAbsent: () => {}, startAndReady: async () => {}, proveAutoVolume: () => {},
    cmd: (_, label, args) => { scripts.push([label, args.at(-1)]); return { ok: true }; },
    observeUnhealthy: async () => true, observeWait: async () => ({ State: { ExitCode: 1 } }), removeContainer: () => {},
  });
  assert.equal(await execute('', 'image', 'owner', 'container', 'volume', [], [], {}, {}), true);
  for (const [label, script] of scripts) {
    const base = path.join(root, label, 'proc');
    for (const pid of ['100', '9']) await mkdir(path.join(base, pid), { recursive: true });
    const java = label === 'api-exit-injection';
    await writeFile(path.join(base, '100', 'comm'), 'bash\n');
    await writeFile(path.join(base, '100', 'cmdline'), `/bin/bash\0-c\0${script}\0`);
    await writeFile(path.join(base, '9', 'comm'), java ? 'java\n' : 'postgres\n');
    await writeFile(path.join(base, '9', 'cmdline'), java ? 'java\0-jar\0/app/lexiflow-api.jar\0' : 'postgres\0-D\0/var/lib/lexiflow/postgres\0');
    const result = spawnSync('/bin/bash', ['-c', `kill() { printf '%s\\n' "$*"; }; ${script.replaceAll('/proc/', `${base}/`)}`], { encoding: 'utf8' });
    assert.equal(result.status, 0, `${label}: ${result.stderr}`);
    assert.equal(result.stdout, '-KILL 9\n', label);
  }
});

test('every runtime create and run ignores inherited image volumes without disabling the owned named mount', async () => {
  const calls = [];
  const command = (_, label, args) => { calls.push([label, Array.from(args)]); return { ok: true }; };
  const { execute: run } = await lifecycleFunction('containerRun', 'removeContainer', { cmd: command });
  run('', { name: 'container', volume: 'data', image: 'image', token: 'owner' });
  for (const argument of ['--image-volume=ignore', '--memory=2g', '--cpus=2', '--stop-timeout=45']) {
    assert.equal(calls[0][1].includes(argument), true, argument);
  }
  assert.equal(calls[0][1].includes('data:/var/lib/lexiflow'), true);
  const { execute: collision } = await lifecycleFunction('nameConflictFailure', 'invalidLoadFailure', {
    json: command, cmd: (_, label, args) => { command('', label, args); return { ok: false }; },
    inspectContainer: () => ({}), assertOwned: () => {},
  });
  assert.equal(collision('', 'container', 'image', 'owner', []), true);
  const { execute: incompatible } = await lifecycleFunction('incompatibleVolume', 'faultExit', {
    createVolume: () => {}, containerRun: () => ({ ok: true }),
    cmd: (_, label, args) => { command('', label, args); return { ok: label !== 'unknown-volume-retained' }; },
    observeWait: async () => ({ State: { ExitCode: 1 } }),
  });
  assert.equal(await incompatible('', 'container', 'image', 'data', 'owner', {}, []), true);
  const launches = calls.filter(([, args]) => ['create', 'run'].includes(args[0]));
  assert.equal(launches.length, 5);
  for (const [label, args] of launches) assert.equal(args.includes('--image-volume=ignore'), true, label);
});

test('auto-created volume proof rejects any additional inherited or unknown mount', async () => {
  const named = { Type: 'volume', Name: 'data', Destination: '/var/lib/lexiflow' };
  for (const mounts of [[named], [named, { Type: 'volume', Name: 'anonymous', Destination: '/var/lib/postgresql/data' }], [{ ...named, Type: 'bind' }], []]) {
    const proofs = new Map([['data', { previousAbsent: true }]]);
    const { execute } = await lifecycleFunction('proveAutoVolume', 'containerRun', {
      inspectContainer: () => ({ Id: 'a'.repeat(64), Mounts: mounts }), assertOwned: () => {},
      inspectVolume: () => ({}), volumeIdentity: () => ({ name: 'data' }),
      volumeProofs: proofs, markerHash: () => 'b'.repeat(64),
    });
    if (mounts.length === 1 && mounts[0].Type === 'volume') assert.equal(execute('', 'container', 'data', 'owner').containerId, 'a'.repeat(64));
    else assert.throws(() => execute('', 'container', 'data', 'owner'), /VOLUME_MOUNT_UNPROVEN/);
  }
});
