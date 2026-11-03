import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCandidateRuntime, REQUIRED_PHASE_IDS, validateCandidateRequest, eligibleForPass } from '../candidates/check-podman-candidate-lifecycle.mjs';
import { markPassIfComplete } from '../candidates/single-runtime-candidate.mjs';
import { normalizeDarwinTempDirectory } from '../../release/workflow/release-execution-environment.mjs';

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
    const { execute: probe, source } = await lifecycleFunction('incompatibleVolume', 'faultSqliteDatabase', {
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

test('SQLite corruption probe snapshots, corrupts, rejects, verifies unchanged bytes, and restores owned data', async () => {
  const calls=[];
  const { execute: snapshot, source } = await lifecycleFunction('sqliteSnapshot', 'faultSqliteDatabase', {
    ownerLabel:'io.lexiflow.candidate-acceptance', cmd:(_root,label,args)=>{calls.push([label,Array.from(args)]);return {ok:true,stdout:'a'.repeat(64)+'  /snapshot/lexicon.db\n'};},
    fail:code=>{throw new Error(code);},
  });
  assert.equal(snapshot('', 'image','owner','lexiflow-data','lexiflow-backup','snapshot'), 'a'.repeat(64));
  snapshot('', 'image','owner','lexiflow-data','lexiflow-backup','corrupt');
  snapshot('', 'image','owner','lexiflow-data','lexiflow-backup','restore');
  snapshot('', 'image','owner','lexiflow-data',null,'marker');
  assert.equal(calls.length,4);
  assert.ok(calls[3][1].includes('--read-only'));
  assert.ok(calls[3][1].includes('lexiflow-data:/var/lib/lexiflow:ro'));
  assert.match(calls[3][1].at(-1),/sha256sum .*\.lexiflow-ready/u);
  assert.throws(()=>snapshot('', 'image','owner','lexiflow-data',null,'unknown'),/SQLITE_OPERATION_INVALID/);
  assert.match(calls[0][1].at(-1),/cp .*lexicon\.db.*snapshot/u);
  assert.match(calls[1][1].at(-1),/LEXIFLOW-CORRUPT-DB/u);
  assert.match(calls[2][1].at(-1),/mv .*lexicon\.db/u);
  assert.match(source,/sha256sum/u);
});

test('stopped corrupt-database evidence is read offline and still rejects identity, marker and exit drift', async () => {
  for (const variant of ['valid','exit','volume','marker','bytes']) {
    const operations=[];let waits=0,snapshots=0,proofs=0;
    const original='a'.repeat(64),corrupt='b'.repeat(64),marker='c'.repeat(64);
    const identity={name:'owned-data',createdAt:'frozen',mountpoint:'/owned',driver:'local'};
    const {execute}=await lifecycleFunction('faultSqliteDatabase','runInner',{
      volumeAbsent:()=>{},startAndReady:async()=>({ready:true}),
      proveAutoVolume:()=>{proofs++;return {identity,markerHash:marker};},
      cmd:()=>({ok:true}),observeUnhealthy:async()=>true,removeContainer:()=>{},
      observeWait:async()=>({State:{ExitCode:++waits===2&&variant==='exit'?0:1}}),
      createVolume:()=>{},containerRun:()=>({ok:true}),inspectContainer:()=>({State:{Status:'exited'}}),assertOwned:()=>{},
      inspectVolume:()=>({...identity,...(variant==='volume'?{createdAt:'replaced'}:{})}),volumeIdentity:x=>x,
      validRuntimeState:()=>true,
      sqliteSnapshot:(_root,_image,_token,_volume,_snapshot,operation)=>{
        operations.push(operation);
        if(operation==='snapshot')return ++snapshots===1?original:variant==='bytes'?original:corrupt;
        if(operation==='corrupt')return corrupt;
        if(operation==='marker')return variant==='marker'?original:marker;
        if(operation==='restore')return original;
        throw Error('unexpected operation');
      },
    });
    const run=()=>execute('','image','owner','container','volume',[],[],{},{});
    if(variant==='valid'){
      const result=await run();assert.equal(result.otherOwnedDataPreserved,true);
      assert.deepEqual(operations,['snapshot','corrupt','snapshot','marker','restore']);
      assert.equal(proofs,3,'no exec-based volume proof while corrupt container is stopped');
    }else await assert.rejects(run(),new RegExp({exit:'SQLITE_CORRUPT_DB_EXIT_NOT_OBSERVED',volume:'VOLUME_IDENTITY_CHANGED',marker:'SQLITE_CORRUPT_DB_OTHER_DATA_CHANGED',bytes:'SQLITE_CORRUPT_DB_MUTATED'}[variant]));
  }
});

test('runtime entrypoint fixes the SQLite file, verifies before API, and refuses unknown volume contents', async () => {
  const { readFile } = await import('node:fs/promises');
  const runtime = await readFile(path.join(repo,'ops/docker/runtime-entrypoint.sh'),'utf8');
  assert.match(runtime,/ROOT=\/var\/lib\/lexiflow/u);
  assert.match(runtime,/DB=\/var\/lib\/lexiflow\/lexicon\.db/u);
  assert.match(runtime,/verified\.json/u);
  assert.match(runtime,/10001/u);
  assert.match(runtime,/mode=ro/u);
  assert.ok(runtime.indexOf('--release-dataset initialize') < runtime.indexOf('SPRING_DATASOURCE_URL'));
  assert.match(runtime,/sqlite-file-missing/u);
  assert.match(runtime,/volume-unknown-content/u);
});
