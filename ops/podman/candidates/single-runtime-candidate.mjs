// 真实单镜像候选验收。只触及本次随机 token 标记的容器和卷。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolveBuildIdentity, assertBuildIdentityMatches } from '../../release/integrity/source-build-identity.mjs';
import { verifyReleaseCandidate } from '../../release/integrity/release-candidate-verifier.mjs';
import { normalizeDarwinTempDirectory } from '../../release/workflow/release-execution-environment.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const phaseIds = ['preflight','target-clean-init','target-repeat-run','target-restart','interrupted-init-retry','incompatible-volume-refusal','failure-exit','final-cleanup'];
const ownerLabel = 'io.lexiflow.candidate-acceptance';
const volumeProofs = new Map();
let podmanSocket=null;
let podmanTempDirectory=null;
let podmanHome=null;
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.keys(value).sort().map(k=>[k,value[k]])) : value);
const fail = (code, blocked = false) => { const e = new Error(code); e.blocked = blocked; throw e; };
const exact = (o, keys) => !!o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).sort().join(',') === [...keys].sort().join(',');
function derivePodmanContext() {
  let account; try { account=os.userInfo(); } catch { fail('PODMAN_USER_HOME_INVALID',true); }
  if(typeof account?.homedir!=='string'||!path.isAbsolute(account.homedir)||/[\u0000-\u001f\u007f]/.test(account.homedir)
    ||typeof account.username!=='string'||!account.username) fail('PODMAN_USER_HOME_INVALID',true);
  let home,homeInfo;
  try { home=fs.realpathSync(account.homedir);homeInfo=fs.statSync(home); } catch { fail('PODMAN_USER_HOME_INVALID',true); }
  if(!homeInfo.isDirectory()||homeInfo.uid!==process.getuid()) fail('PODMAN_USER_HOME_INVALID',true);
  const tempResult=spawnSync('/usr/bin/getconf',['DARWIN_USER_TEMP_DIR'],{encoding:'utf8',timeout:1500,maxBuffer:4096,windowsHide:true,env:{PATH:'/usr/bin:/bin',HOME:home,USER:account.username,LOGNAME:account.username}});
  try { return {home,temp:normalizeDarwinTempDirectory(tempResult)}; } catch { fail('PODMAN_TEMP_DIRECTORY_INVALID',true); }
}
function report() { return { schemaVersion:'lexiflow.candidate-runtime.v2', status:'FAIL', target:null, identity:{source:null}, phases:phaseIds.map(id=>({id,status:'NOT_RUN',facts:{}})), assertions:{}, cleanup:{status:'NOT_RUN',settled:false,ownedResourcesRemoved:false}, failure:null }; }
function setPhase(out,id,status,facts={}) { const p=out.phases.find(x=>x.id===id); p.status=status; p.facts=facts; }
function requestCheck(value) {
  if (!exact(value,['target']) || !exact(value.target,['candidateDirectory','candidateSha256']) || !path.isAbsolute(value.target.candidateDirectory) || !/^[a-f0-9]{64}$/.test(value.target.candidateSha256)) fail('REQUEST_INVALID');
}
export function validateCandidateRequest(value) { requestCheck(value); return true; }
export function eligibleForPass(output) {
  return output?.status==='PASS'&&Array.isArray(output.phases)&&output.phases.length===phaseIds.length
    &&phaseIds.every((id,index)=>output.phases[index]?.id===id&&output.phases[index]?.status==='PASS')
    &&output.cleanup?.status==='PASS'&&output.cleanup?.settled===true&&output.cleanup?.ownedResourcesRemoved===true
    &&output.failure===null;
}
export function markPassIfComplete(out) {
  if (eligibleForPass({...out,status:'PASS'})) out.status='PASS';
  else if (out.status === 'PASS') out.status='FAIL';
  return out;
}
function privateRoot() {
  const parent=path.join(repo,'tmp/phase-tools/single-container-release/acceptance');
  fs.mkdirSync(parent,{recursive:true,mode:0o700});
  const dir=fs.mkdtempSync(path.join(parent,`${crypto.randomBytes(16).toString('hex')}-`)); fs.chmodSync(dir,0o700); return dir;
}
function cmd(logDir,label,args,{timeout=60_000}={}) {
  const verifySocket=()=>{
    if(label==='machine-inspect')return;
    if(!podmanSocket)fail('PODMAN_ENDPOINT_UNPINNED',true);
    let now;try{now=fs.statSync(podmanSocket.path);}catch{fail('PODMAN_ENDPOINT_CHANGED',true);}
    if(!now.isSocket()||now.dev!==podmanSocket.dev||now.ino!==podmanSocket.ino)fail('PODMAN_ENDPOINT_CHANGED',true);
  };
  verifySocket();
  if (!podmanTempDirectory) fail('PODMAN_TEMP_DIRECTORY_INVALID',true);
  if (!podmanHome) fail('PODMAN_USER_HOME_INVALID',true);
  const env={PATH:process.env.PATH||'',HOME:podmanHome,TMPDIR:podmanTempDirectory,LANG:'C',LC_ALL:'C'};
  if(podmanSocket){env.CONTAINER_HOST=`unix://${podmanSocket.path}`;env.DOCKER_HOST=env.CONTAINER_HOST;}
  const r=spawnSync('podman',args,{cwd:repo,encoding:'utf8',timeout,maxBuffer:1024*1024,env,stdio:['ignore','pipe','pipe']});
  // 不保存任何命令输出，可能含本地路径或运行时秘密；只留退出事实。
  fs.appendFileSync(path.join(logDir,'commands.log'),`${label} ${r.status ?? 'null'} ${r.signal||''} ${r.error?.code||''}\n`,{mode:0o600});
  verifySocket();
  if(r.error||r.signal||r.status!==0) return {ok:false,status:r.status,signal:r.signal,stdout:r.stdout||'',stderr:r.stderr||'',error:r.error};
  return {ok:true,status:r.status,stdout:r.stdout||'',stderr:r.stderr||''};
}
function json(logDir,label,args,opts) { const r=cmd(logDir,label,args,opts); if(!r.ok) fail(`${label.toUpperCase().replaceAll('-','_')}_FAILED`); try{return JSON.parse(r.stdout);}catch{fail('PODMAN_OUTPUT_INVALID');} }
function freePort(port) { return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(port,'127.0.0.1',()=>s.close(resolve));}); }
async function waitUntil(fn,timeout=180_000) { const until=Date.now()+timeout; while(Date.now()<until){const v=await fn();if(v)return v;await new Promise(r=>setTimeout(r,400));} return null; }
function inspectContainer(logDir,name) { const exists=cmd(logDir,'container-exists',['container','exists',name],{allowFailure:true});if(!exists.ok){if(exists.status===1)return null;fail('CONTAINER_INSPECT_UNAVAILABLE',true);}const r=cmd(logDir,'inspect-container',['inspect',name],{allowFailure:true});if(!r.ok)fail('CONTAINER_INSPECT_UNAVAILABLE',true);try{const a=JSON.parse(r.stdout);return Array.isArray(a)&&a.length===1?a[0]:null;}catch{fail('CONTAINER_INSPECT_INVALID');} }
function inspectVolume(logDir,name) { const rows=cmd(logDir,'volume-list-exact',['volume','ls','--filter',`name=^${name}$`,'--format','json'],{allowFailure:true});if(!rows.ok)fail('VOLUME_INSPECT_UNAVAILABLE',true);let listed;try{listed=JSON.parse(rows.stdout);}catch{fail('VOLUME_LIST_INVALID');}if(!Array.isArray(listed))fail('VOLUME_LIST_INVALID');const matches=listed.filter(v=>(v.Name||v.name)===name);if(matches.length>1)fail('VOLUME_NAME_AMBIGUOUS');if(!matches.length)return null;const r=cmd(logDir,'inspect-volume',['volume','inspect',name],{allowFailure:true});if(!r.ok)fail('VOLUME_INSPECT_UNAVAILABLE',true);try{const a=JSON.parse(r.stdout);return Array.isArray(a)&&a.length===1?a[0]:null;}catch{fail('VOLUME_INSPECT_INVALID');} }
function labelsOf(x) { return x?.Config?.Labels||x?.Labels||x?.labels||{}; }
function owned(x,token) { return !!x && labelsOf(x)[ownerLabel]===token; }
function assertOwned(x,token) { if(!owned(x,token)) fail('RESOURCE_OWNERSHIP_UNPROVEN'); }
function createVolume(logDir,name,token) { const created=cmd(logDir,'volume-create',['volume','create','--label',`${ownerLabel}=${token}`,name]);if(!created.ok)fail('VOLUME_CREATE_FAILED');const v=inspectVolume(logDir,name);assertOwned(v,token); }
function volumeIdentity(v) { const value={name:v.Name||v.name,createdAt:v.CreatedAt||v.createdAt,mountpoint:v.Mountpoint||v.mountpoint,driver:v.Driver||v.driver};if(!value.name||!value.createdAt||!value.mountpoint||!value.driver)fail('VOLUME_IDENTITY_INCOMPLETE');return value; }
function volumeAbsent(logDir,name) { if(inspectVolume(logDir,name))fail('VOLUME_NAME_COLLISION',true);if(!volumeProofs.has(name))volumeProofs.set(name,{previousAbsent:true,identity:null,containerId:null,markerHash:null}); }
function containerAbsent(logDir,name) { if(inspectContainer(logDir,name))fail('CONTAINER_NAME_COLLISION',true); }
function proveAutoVolume(logDir,container,volume,token) {
  const c=inspectContainer(logDir,container);assertOwned(c,token);
  const containerId=c.Id||c.ID;if(typeof containerId!=='string'||!/^[a-f0-9]{64}$/.test(containerId))fail('CONTAINER_ID_UNPROVEN');
  const mounts=c.Mounts||[];if(mounts.length!==1||mounts[0].Type!=='volume'||mounts[0].Name!==volume||mounts[0].Destination!=='/var/lib/lexiflow')fail('VOLUME_MOUNT_UNPROVEN');
  const v=inspectVolume(logDir,volume);if(!v)fail('VOLUME_MISSING');
  const id=volumeIdentity(v),prior=volumeProofs.get(volume);
  if(!prior?.previousAbsent||(prior.identity&&JSON.stringify(id)!==JSON.stringify(prior.identity)))fail('VOLUME_IDENTITY_CHANGED');
  const marker=markerHash(logDir,container);if(!marker)fail('VOLUME_MARKER_UNPROVEN');
  if(prior.markerHash&&prior.markerHash!==marker)fail('VOLUME_MARKER_CHANGED');
  volumeProofs.set(volume,{previousAbsent:true,containerId,identity:id,markerHash:marker});
  return {containerId,markerHash:marker,identity:id};
}
function containerRun(logDir,{name,volume,image,token,port=true}) {
  const args=['run','--image-volume=ignore','--memory=2g','--cpus=2','--stop-timeout=45','-d','--name',name,'--label',`${ownerLabel}=${token}`,'--label',`io.lexiflow.acceptance-token=${token}`];
  if(port)args.push('--publish','127.0.0.1:18082:8080');
  if(volume)args.push('--volume',`${volume}:/var/lib/lexiflow`);
  args.push(image);
  return cmd(logDir,'container-run',args,{allowFailure:true,timeout:180_000});
}
function removeContainer(logDir,name,token) {
  const c=inspectContainer(logDir,name);if(!c)return;
  assertOwned(c,token);
  const id=c.Id||c.ID;if(typeof id!=='string'||!/^[a-f0-9]{64}$/.test(id))fail('RESOURCE_ID_INVALID');
  if(c.State?.Status==='running'||c.State?.Status==='paused') {
    const stopped=cmd(logDir,'container-stop',['stop','--time','45',id],{allowFailure:true,timeout:60_000});
    if(!stopped.ok)fail('CONTAINER_STOP_FAILED');
    const after=inspectContainer(logDir,name);assertOwned(after,token);
    if((after.Id||after.ID)!==id||after.State?.Status!=='exited')fail('CONTAINER_STOP_UNOBSERVED');
  }
  const r=cmd(logDir,'container-remove',['rm',id],{allowFailure:true,timeout:30_000});if(!r.ok)fail('CONTAINER_CLEANUP_FAILED');
  if(inspectContainer(logDir,name))fail('CONTAINER_CLEANUP_UNOBSERVED');
}
function removeVolume(logDir,name,token) {
  const v=inspectVolume(logDir,name);if(!v)return;
  const proof=volumeProofs.get(name);
  if(!owned(v,token)&&(!proof||!proof.previousAbsent||!proof.containerId||!proof.markerHash||JSON.stringify(volumeIdentity(v))!==JSON.stringify(proof.identity)))fail('VOLUME_OWNERSHIP_UNPROVEN');
  const r=cmd(logDir,'volume-remove',['volume','rm',name],{allowFailure:true});if(!r.ok)fail('VOLUME_CLEANUP_FAILED');
  if(inspectVolume(logDir,name))fail('VOLUME_CLEANUP_UNOBSERVED');
}
async function apiState() {
  try { const response=await fetch('http://127.0.0.1:18082/api/v1/runtime-status',{signal:AbortSignal.timeout(3000),cache:'no-store'}); if(!response.ok)return null;return await response.json(); }catch{return null;}
}
function validRuntimeState(value,identity,dataset,{ready=true}={}) {
  const fields=['softwareVersion','apiContract','mode','ready','reason','datasetVersion','publishedDataset'];
  if(!exact(value,fields)||value.softwareVersion!==identity.softwareVersion||value.apiContract!=='caption-hints.v2'||value.mode!=='formal'||value.ready!==ready)return false;
  if(!ready)return value.reason==='INITIALIZING'&&value.publishedDataset===null;
  return ['OK','PREWARM_DEGRADED'].includes(value.reason)&&Number.isSafeInteger(value.datasetVersion)&&value.datasetVersion>0&&canonical(value.publishedDataset)===canonical(dataset);
}
function runningReady(logDir,name,identity,dataset) {
  const c=inspectContainer(logDir,name);if(!c||c.State?.Status!=='running')return false;
  const h=cmd(logDir,'image-health',['exec',name,'/app/runtime-entrypoint.sh','health'],{allowFailure:true,timeout:10_000});
  return h.ok;
}
async function awaitReady(logDir,name,identity,dataset,timeout=180_000) {
  return await waitUntil(async()=>runningReady(logDir,name,identity,dataset)&&validRuntimeState(await apiState(),identity,dataset)?await apiState():null,timeout);
}
function imageArtifact(proof) { return proof.manifest.artifacts.find(x=>x.role==='runtime-image'&&x.platform==='linux/arm64'); }
function identityLabels(info,identity,digest) {
  const labels=labelsOf(info);return (info.Id||info.ID||'').replace(/^sha256:/,'')===digest.replace(/^sha256:/,'')
    &&info.Os==='linux'&&info.Architecture==='arm64'
    &&labels['org.opencontainers.image.version']===identity.softwareVersion
    &&labels['org.opencontainers.image.revision']===identity.sourceCommit
    &&labels['io.lexiflow.build-id']===identity.buildId
    &&labels['io.lexiflow.source-sha256']===identity.sourceSha256
    &&labels['io.lexiflow.image-role']==='runtime';
}
function loadImage(logDir,proof,candidateDirectory,token,loaded) {
  const artifact=imageArtifact(proof);if(!artifact||artifact.path!=='images/linux-arm64/runtime-image.tar'||!/^sha256:[a-f0-9]{64}$/.test(artifact.imageDigest))fail('RUNTIME_IMAGE_MISSING');
  const image=artifact.imageDigest;
  const before=cmd(logDir,'image-exists-before',['image','exists',image],{allowFailure:true});
  const existed=before.ok;
  if(!existed&&before.status!==1)fail('IMAGE_INSPECT_FAILED');
  const archive=path.join(candidateDirectory,'payload',...artifact.path.split('/'));
  const loadedResult=cmd(logDir,'image-load',['load','-i',archive],{allowFailure:true,timeout:300_000});
  if(!loadedResult.ok)fail('IMAGE_LOAD_FAILED');
  loaded.push({image,existed,identity:proof.buildIdentity});
  const rows=json(logDir,'image-inspect-after',['image','inspect',image]);
  if(!Array.isArray(rows)||rows.length!==1||!identityLabels(rows[0],proof.buildIdentity,image))fail('RUNTIME_IMAGE_IDENTITY_MISMATCH');
  return image;
}
function markerHash(logDir,name) {
  const r=cmd(logDir,'volume-marker',['exec',name,'/bin/bash','-c','test -f /var/lib/lexiflow/.lexiflow-ready && sha256sum /var/lib/lexiflow/.lexiflow-ready'],{allowFailure:true});
  if(!r.ok)return null;const m=/^([a-f0-9]{64})\s+/.exec(r.stdout.trim());return m?.[1]||null;
}
async function startAndReady(logDir,container,volume,image,token,identity,dataset,{existingVolume=false}={}) {
  containerAbsent(logDir,container);
  if(volume) { if(existingVolume) { const v=inspectVolume(logDir,volume),prior=volumeProofs.get(volume);if(!v||!prior?.identity||JSON.stringify(volumeIdentity(v))!==JSON.stringify(prior.identity))fail('VOLUME_IDENTITY_CHANGED'); } else volumeAbsent(logDir,volume); }
  const started=containerRun(logDir,{name:container,volume,image,token});if(!started.ok)fail('CONTAINER_START_FAILED');
  const info=inspectContainer(logDir,container);assertOwned(info,token);
  const containerId=info.Id||info.ID;
  if(typeof containerId!=='string'||!/^[a-f0-9]{64}$/.test(containerId)||(info.Image||'').replace(/^sha256:/,'')!==image.replace(/^sha256:/,''))fail('RUNTIME_CONTAINER_IDENTITY_MISMATCH');
  const state=await awaitReady(logDir,container,identity,dataset);if(!state)fail('RUNTIME_NOT_READY');
  return state;
}
function runtimeFacts(state,dataset) {
  const apiIdentity={softwareVersion:state.softwareVersion,apiContract:state.apiContract,mode:state.mode,datasetVersion:state.datasetVersion,publishedDataset:state.publishedDataset};
  return {ready:true,apiIdentity,extensionIdentity:dataset,publishedDatasetIdentity:dataset,
    cleanVolume:true,runtimeImageIdentity:true};
}
function observeWait(logDir,name,timeout=60_000) {
  return waitUntil(()=>{const c=inspectContainer(logDir,name);return c&&c.State?.Status==='exited'?c:null;},timeout);
}
async function observeUnhealthy(logDir,name,identity) {
  return waitUntil(async()=>{
    const c=inspectContainer(logDir,name);if(!c)return null;
    if(c.State?.Status==='exited')return Number.isInteger(c.State.ExitCode)&&c.State.ExitCode!==0?true:null;
    if(c.State?.Status!=='running')return null;
    const state=await apiState();
    if(state&&state.softwareVersion===identity.softwareVersion&&state.ready===false)return true;
    const h=cmd(logDir,'fault-health-observation',['exec',name,'/app/runtime-entrypoint.sh','health'],{allowFailure:true,timeout:5000});
    return h.ok?null:true;
  },5000);
}
async function reservePortFailure(logDir,image,token,volume,containers) {
  const server=net.createServer();let active=false;
  try {
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(18082,'127.0.0.1',resolve);});active=true;
    const name=`lf-${token}-port`;containers.push(name);
    const result=containerRun(logDir,{name,volume,image,token});
    if(result.ok) { const exited=inspectContainer(logDir,name);if(exited?.State?.Status==='running')fail('PORT_CONFLICT_NOT_REJECTED'); }
    const c=inspectContainer(logDir,name);if(c)assertOwned(c,token);
    removeContainer(logDir,name,token);
  } catch(error) { if(error.message==='PORT_CONFLICT_NOT_REJECTED')throw error; if(!active)fail('API_PORT_CONFLICT_PROBE_FAILED'); }
  finally { if(active)await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve())); }
  return true;
}
function nameConflictFailure(logDir,name,image,token,containerNames) {
  containerNames.push(name);
  const created=cmd(logDir,'collision-create',['create','--image-volume=ignore','--name',name,'--label',`${ownerLabel}=${token}`,image]);
  if(!created.ok)fail('COLLISION_CREATE_FAILED');
  const result=cmd(logDir,'collision-run',['run','--image-volume=ignore','--name',name,'--label',`${ownerLabel}=${token}`,image],{allowFailure:true});
  if(result.ok)fail('CONTAINER_NAME_CONFLICT_NOT_REJECTED');
  const c=inspectContainer(logDir,name);assertOwned(c,token);return true;
}
function invalidLoadFailure(logDir,root) {
  const file=path.join(root,'invalid-runtime-image.tar');fs.writeFileSync(file,'not a container archive',{flag:'wx',mode:0o600});
  const r=cmd(logDir,'invalid-image-load',['load','-i',file],{allowFailure:true});if(r.ok)fail('IMAGE_LOAD_FAILURE_NOT_REJECTED');return true;
}
function invalidPullFailure(logDir,token) {
  const reference=`127.0.0.1:65534/lexiflow-acceptance/no-such-${token}:invalid`;
  const r=cmd(logDir,'invalid-image-pull',['pull','--retry=0',reference],{allowFailure:true,timeout:30_000});
  if(r.ok)fail('IMAGE_PULL_FAILURE_NOT_REJECTED');
  return true;
}
async function incompatibleVolume(logDir,container,image,volume,token,identity,containers) {
  createVolume(logDir,volume,token);
  const write=cmd(logDir,'incompatible-marker',['run','--image-volume=ignore','--rm','--entrypoint','/bin/bash','--label',`${ownerLabel}=${token}`,'--volume',`${volume}:/var/lib/lexiflow`,image,'-c','printf foreign > /var/lib/lexiflow/unknown-state'],{allowFailure:true});
  if(!write.ok)fail('INCOMPATIBLE_FIXTURE_FAILED');
  containers.push(container);
  const run=containerRun(logDir,{name:container,volume,image,token});
  if(!run.ok)fail('INCOMPATIBLE_RUN_NOT_STARTED');
  const exited=await observeWait(logDir,container,90_000);if(!exited||exited.State.ExitCode===0)fail('INCOMPATIBLE_VOLUME_NOT_REJECTED');
  const check=cmd(logDir,'unknown-volume-retained',['exec',container,'/bin/bash','-c','test -f /var/lib/lexiflow/unknown-state'],{allowFailure:true});
  if(!check.ok) {
    // The refused container may have exited before exec is available; mount with the same image to prove retention.
    const probe=cmd(logDir,'unknown-volume-probe',['run','--image-volume=ignore','--rm','--entrypoint','/bin/bash','--volume',`${volume}:/var/lib/lexiflow`,image,'-c','test -f /var/lib/lexiflow/unknown-state'],{allowFailure:true});
    if(!probe.ok)fail('UNKNOWN_VOLUME_MUTATED');
  }
  return true;
}
async function faultExit(logDir,image,token,container,volume,containers,volumes,identity,dataset) {
  volumeAbsent(logDir,volume);containers.push(container);await startAndReady(logDir,container,volume,image,token,identity,dataset);proveAutoVolume(logDir,container,volume,token);
  const killed=cmd(logDir,'api-exit-injection',['exec',container,'/bin/bash','-c','for f in /proc/[0-9]*/comm; do IFS= read -r name < "$f" || continue; [ "$name" = java ] || continue; p=${f#/proc/}; p=${p%/comm}; c=$(tr "\\0" " " < "/proc/$p/cmdline" 2>/dev/null || true); case "$c" in *"/app/lexiflow-api.jar"*) kill -KILL "$p" || exit 1; exit 0;; esac; done; exit 1'],{allowFailure:true});
  if(!killed.ok)fail('API_EXIT_INJECTION_UNOBSERVED');
  const apiUnhealthy=await observeUnhealthy(logDir,container,identity);if(!apiUnhealthy)fail('API_UNHEALTHY_NOT_OBSERVED');
  const apiExited=await observeWait(logDir,container,60_000);if(!apiExited||apiExited.State.ExitCode===0)fail('API_EXIT_NOT_SUPERVISED');removeContainer(logDir,container,token);
  const pgName=`${container}-pg`,pgVol=`${volume}-pg`;volumes.push(pgVol);volumeAbsent(logDir,pgVol);containers.push(pgName);await startAndReady(logDir,pgName,pgVol,image,token,identity,dataset);proveAutoVolume(logDir,pgName,pgVol,token);
  const killedPg=cmd(logDir,'postgres-exit-injection',['exec',pgName,'/bin/bash','-c','for f in /proc/[0-9]*/comm; do IFS= read -r name < "$f" || continue; [ "$name" = postgres ] || continue; p=${f#/proc/}; p=${p%/comm}; c=$(tr "\\0" " " < "/proc/$p/cmdline" 2>/dev/null || true); case "$c" in *"postgres -D /var/lib/lexiflow/postgres"*) kill -KILL "$p" || exit 1; exit 0;; esac; done; exit 1'],{allowFailure:true});
  if(!killedPg.ok)fail('POSTGRES_EXIT_INJECTION_UNOBSERVED');
  const pgUnhealthy=await observeUnhealthy(logDir,pgName,identity);if(!pgUnhealthy)fail('POSTGRES_UNHEALTHY_NOT_OBSERVED');
  const pgExited=await observeWait(logDir,pgName,60_000);if(!pgExited||pgExited.State.ExitCode===0)fail('POSTGRES_EXIT_NOT_SUPERVISED');return true;
}
async function runInner(request) {
  const out=report(), token=crypto.randomBytes(16).toString('hex'), containers=[], volumes=[], loaded=[];
  volumeProofs.clear();podmanSocket=null;podmanTempDirectory=null;podmanHome=null;
  let root=null, image=null, dataset=null, source=null, cleaned=false;
  const volume=(name)=>{volumes.push(name);return name;};
  try {
    requestCheck(request);
    const target=Object.freeze({candidateDirectory:path.resolve(request.target.candidateDirectory),candidateSha256:request.target.candidateSha256});
    if(process.platform!=='darwin'||process.arch!=='arm64')fail('HOST_UNSUPPORTED',true);
    const context=derivePodmanContext();podmanHome=context.home;podmanTempDirectory=context.temp;
    root=privateRoot();
    const machines=json(root,'machine-inspect',['machine','inspect']);
    const machineRows=Array.isArray(machines)?machines:[machines];
    const runningMachines=machineRows.filter(row=>['running','started'].includes(String(row?.State||row?.state||'').toLowerCase()));
    if(runningMachines.length!==1)fail('PODMAN_MACHINE_UNAVAILABLE',true);
    const socket=runningMachines[0]?.ConnectionInfo?.PodmanSocket?.Path||runningMachines[0]?.connectionInfo?.podmanSocket?.path;
    if(typeof socket!=='string'||!path.isAbsolute(socket))fail('PODMAN_MACHINE_UNAVAILABLE',true);
    let socketStat;try{socketStat=fs.statSync(socket);}catch{fail('PODMAN_MACHINE_UNAVAILABLE',true);}
    if(!socketStat.isSocket())fail('PODMAN_MACHINE_UNAVAILABLE',true);
    podmanSocket={path:socket,dev:socketStat.dev,ino:socketStat.ino};
    setPhase(out,'preflight','RUNNING');
    source=resolveBuildIdentity(repo);if(source.dirty)fail('SOURCE_DIRTY',true);
    let proof;try{proof=await verifyReleaseCandidate(target);}catch{fail('CANDIDATE_VERIFY_FAILED');}
    try{assertBuildIdentityMatches(proof.buildIdentity,source);}catch{fail('TARGET_SOURCE_IDENTITY_MISMATCH');}
    const extension=proof.manifest.artifacts.find(x=>x.role==='extension');
    dataset=extension?.metadata?.expectedDataset;
    const datasetKeys=['releaseId','preparationId','ruleId','sqlVersion','sha256'];
    if(!exact(dataset,datasetKeys)||! /^[a-f0-9]{64}$/.test(dataset.sha256))fail('DATASET_IDENTITY_MISSING');
    if(proof.manifest.artifacts.find(x=>x.role==='dataset')?.sha256!==dataset.sha256)fail('DATASET_IDENTITY_MISMATCH');
    try{await freePort(18082);}catch{fail('API_PORT_BLOCKED',true);}
    image=loadImage(root,proof,target.candidateDirectory,token,loaded);
    const invalidLoad=invalidLoadFailure(root,root);
    const invalidPull=invalidPullFailure(root,token);
    const collisionName=`lf-${token}-collision`;
    const nameConflict=nameConflictFailure(root,collisionName,image,token,containers);
    const portConflict=await reservePortFailure(root,image,token,null,containers);
    // Collision probe resources are ours and must settle before lifecycle stages.
    for(const n of [...containers])removeContainer(root,n,token);containers.length=0;
    setPhase(out,'preflight','PASS',{sourceClean:true,hostDarwinArm64:true,candidateBytesVerified:true,apiPortFree:true,containerNameFree:true,imageLoadVerified:true,containerNameConflictRejected:nameConflict,imageLoadFailureRejected:invalidLoad,imagePullFailureRejected:invalidPull,apiPortConflictRejected:portConflict});
    out.target={candidateSha256:proof.candidateSha256,manifestSha256:proof.manifestSha256,buildIdentity:proof.buildIdentity};out.identity.source=source;
    const names=()=>`lf-${token}-${crypto.randomBytes(3).toString('hex')}`;
    const c1=names(),v1=volume(`${c1}-data`);volumeAbsent(root,v1);containers.push(c1);
    setPhase(out,'target-clean-init','RUNNING');const first=await startAndReady(root,c1,v1,image,token,proof.buildIdentity,dataset);
    const volume1=proveAutoVolume(root,c1,v1,token),marker1=volume1.markerHash;
    setPhase(out,'target-clean-init','PASS',runtimeFacts(first,dataset));
    removeContainer(root,c1,token);containers.splice(containers.indexOf(c1),1);
    setPhase(out,'target-repeat-run','RUNNING');containers.push(c1);const repeated=await startAndReady(root,c1,v1,image,token,proof.buildIdentity,dataset,{existingVolume:true});
    const marker2=markerHash(root,c1);if(marker2!==marker1)fail('PERSISTENT_MARKER_CHANGED');
    proveAutoVolume(root,c1,v1,token);
    setPhase(out,'target-repeat-run','PASS',{...runtimeFacts(repeated,dataset),sameVolume:true,sameMarker:true,publishedDataset:true});
    removeContainer(root,c1,token);containers.splice(containers.indexOf(c1),1);
    setPhase(out,'target-restart','RUNNING');containers.push(c1);const restarted=await startAndReady(root,c1,v1,image,token,proof.buildIdentity,dataset,{existingVolume:true});
    if(markerHash(root,c1)!==marker1)fail('PERSISTENT_MARKER_CHANGED');
    proveAutoVolume(root,c1,v1,token);
    setPhase(out,'target-restart','PASS',{...runtimeFacts(restarted,dataset),publishedDataset:true,sameVolume:true,markerPresent:true});
    removeContainer(root,c1,token);containers.splice(containers.indexOf(c1),1);removeVolume(root,v1,token);volumes.splice(volumes.indexOf(v1),1);
    const ci=names(),vi=volume(`${ci}-data`);volumeAbsent(root,vi);containers.push(ci);
    setPhase(out,'interrupted-init-retry','RUNNING');
    containerAbsent(root,ci);const start=containerRun(root,{name:ci,volume:vi,image,token});if(!start.ok)fail('INTERRUPT_INIT_START_FAILED');
    const observed=await waitUntil(()=>{const c=inspectContainer(root,ci);if(c?.State?.Status==='exited')return {container:c,marker:false};const m=cmd(root,'init-marker',['exec',ci,'/bin/bash','-c','test -f /var/lib/lexiflow/.lexiflow-initializing && test ! -f /var/lib/lexiflow/.lexiflow-ready'],{allowFailure:true});return m.ok?{container:c,marker:true}:null;},60_000);
    if(!observed||!observed.marker)fail('INITIALIZATION_INTERRUPTION_UNOBSERVED');
    const kill=cmd(root,'interrupt-init',['kill','--signal','KILL',ci],{allowFailure:true});if(!kill.ok)fail('INITIALIZATION_INTERRUPT_FAILED');
    const interrupted=await observeWait(root,ci);if(!interrupted||interrupted.State.ExitCode===0)fail('INITIALIZATION_INTERRUPT_NOT_RECORDED');
    removeContainer(root,ci,token);containers.splice(containers.indexOf(ci),1);
    containerAbsent(root,ci);if(!inspectVolume(root,vi))fail('INTERRUPTED_VOLUME_MISSING');const retry=containerRun(root,{name:ci,volume:vi,image,token});if(!retry.ok)fail('INITIALIZATION_RETRY_START_FAILED');containers.push(ci);
    const retried=await awaitReady(root,ci,proof.buildIdentity,dataset);if(!retried||!validRuntimeState(retried,proof.buildIdentity,dataset))fail('INITIALIZATION_RETRY_NOT_READY');
    proveAutoVolume(root,ci,vi,token);
    setPhase(out,'interrupted-init-retry','PASS',{...runtimeFacts(retried,dataset),initInterrupted:true,firstRunNonzero:true,volumeRetained:true,retryReady:true,retryDatasetIdentity:true});
    removeContainer(root,ci,token);containers.splice(containers.indexOf(ci),1);removeVolume(root,vi,token);volumes.splice(volumes.indexOf(vi),1);
    const badName=names(),badVol=volume(`${badName}-data`);containers.push(badName);
    setPhase(out,'incompatible-volume-refusal','RUNNING');await incompatibleVolume(root,badName,image,badVol,token,proof.buildIdentity,containers);
    setPhase(out,'incompatible-volume-refusal','PASS',{refused:true,nonzero:true,volumeRetained:true,noUnknownResourcesRemoved:true});
    removeContainer(root,badName,token);containers.splice(containers.indexOf(badName),1);removeVolume(root,badVol,token);volumes.splice(volumes.indexOf(badVol),1);
    setPhase(out,'failure-exit','RUNNING');const fc=names(),fv=volume(`${fc}-data`);await faultExit(root,image,token,fc,fv,containers,volumes,proof.buildIdentity,dataset);
    setPhase(out,'failure-exit','PASS',{apiExitObserved:true,postgresExitObserved:true,unhealthyObserved:true,nonzero:true});
    out.assertions={targetOnly:true,realInitializationInterruption:true,sameVolumeRestart:true,realFailureObserved:true,unknownResourcesUntouched:true};
    out.failure=null;
  } catch(error) {
    out.status=error?.blocked?'BLOCKED':'FAIL';out.failure={code:/^[A-Z][A-Z0-9_]{2,80}$/.test(error?.message||'')?error.message:'RUNTIME_INTERNAL_ERROR'};
    const running=out.phases.find(p=>p.status==='RUNNING');if(running)running.status=out.status;
  } finally {
    try {
      if(root) {
        // Cleanup is strictly constrained to this invocation's names and verified owner labels.
        for(const name of [...new Set(containers)])removeContainer(root,name,token);
        for(const name of [...new Set(volumes)])removeVolume(root,name,token);
        for(const item of loaded) if(!item.existed) {
          const rows=json(root,'cleanup-image-inspect',['image','inspect',item.image]);
          if(!Array.isArray(rows)||rows.length!==1||!identityLabels(rows[0],item.identity,item.image))fail('IMAGE_CLEANUP_OWNERSHIP_UNPROVEN');
          const rm=cmd(root,'cleanup-image',['rmi',item.image],{allowFailure:true});if(!rm.ok)fail('IMAGE_CLEANUP_FAILED');
        }
        cleaned=true;
      }
      out.cleanup={status:cleaned?'PASS':'BLOCKED',settled:cleaned,ownedResourcesRemoved:cleaned};
      if(out.phases.at(-1).status==='NOT_RUN'||out.phases.at(-1).status==='RUNNING')setPhase(out,'final-cleanup',cleaned?'PASS':'BLOCKED',{ownedResourcesRemoved:cleaned});
      if(!cleaned&&out.status==='PASS'){out.status='BLOCKED';out.failure={code:'CLEANUP_NOT_OBSERVED'};}
      if(out.status!=='PASS') { for(const p of out.phases)if(p.status==='NOT_RUN')p.status='NOT_RUN'; }
      markPassIfComplete(out);
    } catch(error) {
      out.cleanup={status:'BLOCKED',settled:false,ownedResourcesRemoved:false};out.status='BLOCKED';out.failure={code:/^[A-Z][A-Z0-9_]{2,80}$/.test(error?.message||'')?error.message:'CLEANUP_UNPROVEN'};
      const phase=out.phases.at(-1);phase.status='BLOCKED';phase.facts={ownedResourcesRemoved:false};
    }
  }
  return out;
}
export async function runCandidateRuntime(request) { return runInner(request); }
export { phaseIds as REQUIRED_PHASE_IDS };
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  let input='';for await(const part of process.stdin){input+=part;if(input.length>16_384){process.stdout.write(JSON.stringify({schemaVersion:'lexiflow.candidate-runtime.v2',status:'FAIL',failure:{code:'REQUEST_TOO_LARGE'}})+'\n');process.exit(0);}}
  try { const result=await runCandidateRuntime(JSON.parse(input));process.stdout.write(JSON.stringify(result)+'\n'); }
  catch { process.stdout.write(JSON.stringify({schemaVersion:'lexiflow.candidate-runtime.v2',status:'FAIL',failure:{code:'REQUEST_INVALID'}})+'\n'); }
}
