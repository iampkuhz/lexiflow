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
    json: command, cmd: (_, label, args) => { command('', label, args); return { ok: label === 'collision-create' }; },
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

test('actual Podman command environment receives only the validated OS temp directory', async () => {
  const { execute, source } = await lifecycleFunction('cmd', 'json', {
    repo,
    podmanSocket: null,
    podmanHome: '/Users/actual-user',
    podmanTempDirectory: '/private/var/folders/user/T/',
    process: { env: { PATH: '/usr/bin:/bin', HOME: '/private/attacker', TMPDIR: '/private/attacker', USER: 'attacker', XDG_CONFIG_HOME: '/private/config' } }, os: { homedir: () => { throw new Error('ambient HOME must not be consulted'); } },
    spawnSync: (bin, args, options) => {
      assert.equal(bin, 'podman');
      assert.equal(options.env.HOME, '/Users/actual-user');
      assert.equal(options.env.TMPDIR, '/private/var/folders/user/T/');
      assert.deepEqual(Object.keys(options.env).sort(), ['HOME','LANG','LC_ALL','PATH','TMPDIR']);
      return { status: 0, stdout: '', stderr: '' };
    }, fs: { appendFileSync() {}, statSync() { return { isSocket: () => true, dev: 1, ino: 1 }; } },
    path, fail: code => { throw new Error(code); },
  });
  assert.equal(execute('', 'machine-inspect', ['machine','inspect']).ok, true);
  assert.match(source, /podmanTempDirectory=context\.temp/u);
  assert.match(source, /podmanHome=context\.home/u);
  assert.match(source, /TMPDIR:podmanTempDirectory/u);
});

test('OS user record derives canonical home and fixed Darwin temp without ambient identity fields', async () => {
  const { execute, source } = await lifecycleFunction('derivePodmanContext', 'requestCheck', {
    os: { userInfo: () => ({ homedir: '/Users/real', username: 'real' }), homedir: () => { throw new Error('untrusted'); } },
    path, fs: { realpathSync: p => p, statSync: () => ({ isDirectory: () => true, uid: 501 }) },
    process: { getuid: () => 501 },
    spawnSync: (bin, args, options) => {
      assert.equal(bin, '/usr/bin/getconf');
      assert.equal(JSON.stringify(args), JSON.stringify(['DARWIN_USER_TEMP_DIR']));
      assert.equal(JSON.stringify(options.env), JSON.stringify({ PATH: '/usr/bin:/bin', HOME: '/Users/real', USER: 'real', LOGNAME: 'real' }));
      return { status: 0, stdout: '/private/var/folders/real/T/\n' };
    }, normalizeDarwinTempDirectory: result => { assert.equal(result.status, 0); return '/private/var/folders/real/T/'; },
    fail: code => { throw new Error(code); },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(execute())), { home: '/Users/real', temp: '/private/var/folders/real/T/' });
  assert.match(source, /os\.userInfo\(\)/u);
  assert.doesNotMatch(source, /os\.homedir\(\)/u);
});

test('OS home and temp query failures fail closed', async () => {
  for (const scenario of ['userinfo', 'home-path', 'temp-query']) {
    const { execute } = await lifecycleFunction('derivePodmanContext', 'requestCheck', {
      os: { userInfo: () => { if (scenario === 'userinfo') throw new Error('unavailable'); return { homedir: scenario === 'home-path' ? 'relative' : '/Users/real', username: 'real' }; } },
      path, fs: { realpathSync: p => p, statSync: () => ({ isDirectory: () => true, uid: 501 }) }, process: { getuid: () => 501 },
      spawnSync: () => ({ status: scenario === 'temp-query' ? 1 : 0, stdout: '/tmp\n' }),
      normalizeDarwinTempDirectory: () => { throw new Error('RELEASE_STANDARD_PATH_INVALID'); },
      fail: (code, blocked) => { const e = new Error(code); e.blocked = blocked; throw e; },
    });
    assert.throws(() => execute(), scenario === 'temp-query' ? /PODMAN_TEMP_DIRECTORY_INVALID/ : /PODMAN_USER_HOME_INVALID/);
  }
});

test('invalid standard temp directory results fail closed before Podman can run', async () => {
  for (const result of [{ status: 1, stdout: '' }, { status: 0, stdout: '/tmp\n' }, { status: 0, stdout: '/tmp\nextra\n' }]) {
    assert.throws(() => normalizeDarwinTempDirectory(result), /RELEASE_STANDARD_PATH_INVALID/);
  }
  const { execute } = await lifecycleFunction('cmd', 'json', {
    repo,
    podmanSocket: null, podmanTempDirectory: null,
    process: { env: {} }, os: { homedir: () => '/Users/test' }, spawnSync: () => { throw new Error('must not spawn'); },
    fs: { appendFileSync() {} }, path, fail: code => { throw new Error(code); },
  });
  assert.throws(() => execute('', 'machine-inspect', ['machine','inspect']), /PODMAN_TEMP_DIRECTORY_INVALID/);
});


test('image loading uses the validated target directory, not a nonexistent verifier proof root', async () => {
  const artifact={path:'images/linux-arm64/runtime-image.tar',imageDigest:`sha256:${'a'.repeat(64)}`};
  const proof={candidateSha256:'b'.repeat(64),manifestSha256:'c'.repeat(64),buildIdentity:{},candidate:{},manifest:{artifacts:[artifact]}};
  const seen=[];
  const { execute }=await lifecycleFunction('loadImage','markerHash',{
    imageArtifact:()=>artifact, path,
    cmd:(_,label,args)=>{seen.push([label,Array.from(args)]);return {ok:true};},
    json:()=>[{}], identityLabels:()=>true,
  });
  assert.equal(Object.hasOwn(proof,'root'),false);
  assert.equal(execute('',proof,'/tmp/verified-candidate','owner',[]),artifact.imageDigest);
  assert.deepEqual(seen.find(([label])=>label==='image-load')[1],['load','-i','/tmp/verified-candidate/payload/images/linux-arm64/runtime-image.tar']);
});

test('asynchronous observations are awaited until a real value arrives', async () => {
  const { execute }=await lifecycleFunction('waitUntil','inspectContainer',{
    setTimeout:done=>queueMicrotask(done),
  });
  let calls=0;
  assert.equal(await execute(async()=>{calls++;await Promise.resolve();return calls===3?'ready':null;},3000),'ready');
  assert.equal(calls,3);
  await assert.rejects(execute(async()=>{throw new Error('observation-failed');},3000),/observation-failed/);
});

test('allowed failure never converts a failed Podman process into success', async () => {
  for (const allowFailure of [false,true]) {
    for (const processResult of [
      {status:0,stdout:'ok',stderr:''}, {status:1,stdout:'',stderr:'absent'},
      {status:125,stdout:'',stderr:'invalid archive'}, {status:null,signal:'SIGTERM'},
      {status:null,error:{code:'ETIMEDOUT'}},
    ]) {
      const { execute }=await lifecycleFunction('cmd','json',{
        repo, path, podmanSocket:null, podmanHome:'/Users/real', podmanTempDirectory:'/private/tmp/real/',
        process:{env:{PATH:'/usr/bin:/bin'}}, spawnSync:()=>processResult,
        fs:{appendFileSync(){}}, fail:code=>{throw new Error(code);},
      });
      const result=execute('', 'machine-inspect',['machine','inspect'],{allowFailure});
      assert.equal(result.ok,processResult.status===0,JSON.stringify({allowFailure,...processResult}));
      assert.equal(result.status,processResult.status);
    }
  }
});

test('create commands accept plain text and prove ownership through inspect instead of parsing stdout', async () => {
  const calls=[];
  const {execute:volume}=await lifecycleFunction('createVolume','volumeIdentity',{
    ownerLabel:'owner', json:()=>JSON.parse('volume-name\n'), cmd:(_,label)=>{calls.push(label);return {ok:true,stdout:'volume-name\n'};},
    inspectVolume:()=>({Labels:{owner:'token'}}), assertOwned:()=>{},
    fail:code=>{throw new Error(code);},
  });
  volume('', 'volume-name','token');
  assert.equal(calls[0],'volume-create');
  const containers=[];
  const {execute:create}=await lifecycleFunction('nameConflictFailure','invalidLoadFailure',{
    ownerLabel:'owner', json:()=>JSON.parse('a'.repeat(64)+'\n'), cmd:(_,label)=>{calls.push(label);return {ok:label==='collision-create',stdout:'a'.repeat(64)+'\n'};},
    inspectContainer:()=>({}), assertOwned:()=>{}, fail:code=>{throw new Error(code);},
  });
  assert.equal(create('', 'container-name','image','token',containers),true);
  assert.deepEqual(containers,['container-name']);
});

test('creation with unknown write outcome registers the owned name before failing', async () => {
  const containers=[];
  const {execute}=await lifecycleFunction('nameConflictFailure','invalidLoadFailure',{
    ownerLabel:'owner', json:()=>{throw new Error('COLLISION_CREATE_FAILED');}, cmd:()=>({ok:false,status:null}), fail:code=>{throw new Error(code);},
  });
  assert.throws(()=>execute('', 'container-name','image','token',containers),/COLLISION_CREATE_FAILED/);
  assert.deepEqual(containers,['container-name']);
});
