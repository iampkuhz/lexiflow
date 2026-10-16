#!/usr/bin/env node
// 生命周期合成 Docker daemon：只保存非秘密资源状态与动作摘要。
import { createHash } from 'node:crypto';
import { readFile, writeFile, appendFile, mkdir, readdir, rm } from 'node:fs/promises';
import { readFileSync, writeFileSync } from 'node:fs';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { format } from 'node:util';
class ModelExit extends Error { constructor(status){super('model exit');this.status=status;} }
export async function execute(statePath, ledgerPath, controlPath, argvInput, env={}, stdinData=Buffer.alloc(0)) {
 const argv=[...argvInput]; let stdout=''; const stderr=''; const log=(...args)=>{stdout+=format(...args)+'\n';};
const fail = () => { throw new ModelExit(64); };
const sha = (text) => createHash('sha256').update(text).digest('hex');
if (!statePath || !ledgerPath || !controlPath || !argv.length) return {status:64,stdout,stderr};
function valueAfter(args, flag) { const at = args.indexOf(flag); return at < 0 ? null : args[at + 1]; }

async function load() { return JSON.parse(await readFile(statePath, 'utf8')); }
async function save(data) { await writeFile(statePath, JSON.stringify(data)); }
async function record(action, resource = null, daemon = null) {
  await appendFile(ledgerPath, `${JSON.stringify({ action, resource, daemon })}\n`);
}
function host(args) {
  if (args[0] !== '--host' || !args[1]?.startsWith('unix:///')) fail();
  return args.splice(0, 2)[1];
}
function renderResource(resource, kind) {
  const labels = resource.labels;
  if (kind === 'container') return `${resource.id}|/${resource.name}|${labels.project}|${labels.installation}|${labels.release}`;
  return `${resource.id}|${resource.name}|${labels.project}|${labels.installation}|${labels.release}`;
}
function makeResource(data, kind, name, project, installation, release, running = true) {
  const existing = data.resources.find((item) => item.kind === kind && item.name === name && item.labels.project === project);
  if (existing) {
    if (existing.labels.installation !== installation || existing.labels.release !== release) fail();
    if (kind === 'container') {
      existing.running = running;
      existing.health = running ? 'healthy' : 'none';
    }
    return existing;
  }
  const id = kind === 'volume' ? name : sha(`${kind}:${project}:${name}`);
  const resource={ kind, id, name, project, installation, release, running,
    health: kind === 'container' && running ? 'healthy' : 'none',
    labels: { project, installation, release } };
  data.resources.push(resource);
  return resource;
}

try {
  const all = await load();
  const control = JSON.parse(await readFile(controlPath, 'utf8'));
  if (argv[0] === 'context' && argv[1] === 'inspect') {
    if (argv.includes('--format')) { log(control.contextEndpoint); await record('context-inspect'); throw new ModelExit(0); }
    fail();
  }
  const endpoint = host(argv);
  const daemonName = control.endpoints[endpoint];
  const data = all.daemons[daemonName];
  if (!data || !argv.length) fail();
  const [command, subcommand] = argv;
  if (command === 'version' && argv.includes('--format')) {
    const format = valueAfter(argv, '--format');
    if (format === '{{range .Server.Components}}{{.Name}}{{"\\n"}}{{end}}') {
      const components = control.serverComponents ?? ['Engine'];
      if (!Array.isArray(components) || components.some((name) => typeof name !== 'string')) fail();
      log(components.join('\n'));
      await record('server-components', { count: components.length }, daemonName);
      throw new ModelExit(0);
    }
    fail();
  }
  if (command === 'compose') {
    const project = valueAfter(argv, '--project-name');
    const installation = env.LEXIFLOW_INSTALLATION_ID;
    const release = env.LEXIFLOW_RELEASE_KEY;
    const operationAt = argv.indexOf('-f') + 2;
    const operation = argv[operationAt];
    if (argv[1] === 'version') { log('Docker Compose version v2.0.0'); await record('compose-version',null,daemonName); throw new ModelExit(0); }
    if (control.composeFail === true) { await record('compose-failed', { project, operation },daemonName); throw new ModelExit(71); }
    if (!project || !installation || !release || !/^[a-f0-9]{32}$/.test(installation) || !/^[a-f0-9]{64}$/.test(release)) fail();
    if (operation === 'up') {
      const service = argv.at(-1);
      if (service === 'postgres') {
        makeResource(data, 'container', `${project}-postgres-1`, project, installation, release);
        makeResource(data, 'network', `${project}_private`, project, installation, release);
        makeResource(data, 'volume', `${project}_pgdata`, project, installation, release);
      } else if (service === 'api') {
        makeResource(data, 'container', `${project}-api-1`, project, installation, release);
      } else fail();
      await save(all); await record('compose-up', { project, installation, release, service },daemonName); throw new ModelExit(0);
    }
    if (operation === 'run') {
      const name = valueAfter(argv, '--name');
      const serviceAt = argv.indexOf(name) + 1;
      const role = argv[serviceAt];
      if (role === 'private-file-importer') {
        const mode=argv[serviceAt+1], transfer=argv[serviceAt+2];
        if (!name || name !== `${project}-private-file-importer` || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(transfer)) fail();
        const volumeSuffix={pg64hex:'postgres-secret',app64hex:'app-secret',zip:'dataset'}[mode];
        if(!volumeSuffix)fail();
        const volumeName=`${project}_${volumeSuffix}`;
        const volume=data.resources.find(item=>item.kind==='volume'&&item.name===volumeName);
        if(!volume)fail();
        if(!volume.files)volume.files={};
        let payload=stdinData, expectedSize, expectedHash;
        if(mode==='zip'){
          expectedSize=Number(argv[serviceAt+3]); expectedHash=argv[serviceAt+4];
          if(!Number.isSafeInteger(expectedSize)||expectedSize<=0||expectedSize>3*1024*1024*1024||! /^[a-f0-9]{64}$/.test(expectedHash))fail();
        }else{
          const newline=stdinData.indexOf(0x0a);
          if(newline<0)fail();
          expectedHash=stdinData.subarray(0,newline).toString('ascii'); payload=stdinData.subarray(newline+1); expectedSize=64;
          if(!/^[a-f0-9]{64}$/.test(expectedHash))fail();
        }
        if(payload.length!==expectedSize)fail();
        const actualHash=createHash('sha256').update(payload).digest('hex');
        if(actualHash!==expectedHash||(mode!=='zip'&&!/^[a-f0-9]{64}$/.test(payload.toString('ascii'))))fail();
        const path=mode==='pg64hex'?'postgres-password':mode==='app64hex'?'app-password':'dataset.zip';
        const existing=volume.files[path];
        if(existing&&(existing.bytes!==expectedSize||existing.sha256!==expectedHash))fail();
        volume.files[path] ||= {bytes:expectedSize,sha256:expectedHash,mode:mode==='zip'?0o444:0o444,links:1};
        const importer=makeResource(data,'container',name,project,installation,release,false);
        data.resources=data.resources.filter(item=>item!==importer);
        await save(all); await record('private-file-import',{project,mode,volume:volumeName,bytes:expectedSize,existing:Boolean(existing)},daemonName); throw new ModelExit(0);
      }
      if (!name || role !== 'initialize' && role !== 'api') fail();
      if (role === 'initialize') makeResource(data,'volume',`${project}_initialization-work`,project,installation,release,false);
      if (role === 'api') makeResource(data, 'container', name, project, installation, release);
      await save(all); await record('compose-run', { project, installation, release, role, name },daemonName); throw new ModelExit(0);
    }
    fail();
  }
  if (command === 'info') {
    const format = valueAfter(argv, '--format');
    if (format === '{{.ID}}') {
      if (control.idQueryFail === true) { await record('info-failed',{format},daemonName); fail(); }
      log(control.idOverride ?? data.id);
      if (control.switchAfterIdQueries) {
        const {remaining,endpoint:switchEndpoint,target} = control.switchAfterIdQueries;
        if (remaining === 1) {
          control.endpoints[switchEndpoint] = target;
          delete control.switchAfterIdQueries;
        } else control.switchAfterIdQueries.remaining = remaining - 1;
        await writeFile(controlPath,JSON.stringify(control));
      }
    }
    else if (format === '{{.OSType}}') log('linux');
    else if (format === '{{.Architecture}}') log('x86_64');
    else fail();
    await record('info', { format },daemonName); throw new ModelExit(0);
  }
  if (command === 'load') { if (!valueAfter(argv, '-i')) fail(); await record('image-load',null,daemonName); throw new ModelExit(0); }
  if (command === 'image' && subcommand === 'inspect') {
    const image = argv.at(-1);
    if (!image?.startsWith('sha256:')) fail();
    log(`${image}|linux|amd64`); await record('image-inspect', { image },daemonName); throw new ModelExit(0);
  }
  if (command === 'inspect' && argv.includes('--format')) {
    const id = argv.at(-1); const format = valueAfter(argv, '--format');
    const resource = data.resources.find((item) => item.id === id);
    if (!resource) throw new ModelExit(2);
    if (format === '{{.State.Running}}' && resource.kind === 'container') log(String(resource.running));
    else if (format === '{{.State.Health.Status}}' && resource.kind === 'container') log(resource.health);
    else if (argv.includes('container') || resource.kind === 'container') log(renderResource(resource, 'container'));
    else log(renderResource(resource, resource.kind));
    await record('inspect', { kind: resource.kind, id: resource.id, name: resource.name },daemonName); throw new ModelExit(0);
  }
  if (['container', 'network', 'volume'].includes(command)) {
    const kind = command;
    if (kind === 'volume' && subcommand === 'create') {
      const name=argv.at(-1);
      const labels=argv.flatMap((item,index)=>item==='--label'&&argv[index+1]?[argv[index+1]]:[]);
      const required=['com.docker.compose.project=','lexiflow.installation=','lexiflow.release='];
      if(!name||labels.length!==3||required.some((prefix)=>labels.filter((value)=>value.startsWith(prefix)).length!==1))fail();
      const project=labels.find((value)=>value.startsWith(required[0]))?.slice(required[0].length);
      const installation=labels.find((value)=>value.startsWith(required[1]))?.slice(required[1].length);
      const release=labels.find((value)=>value.startsWith(required[2]))?.slice(required[2].length);
      if(!/^[a-f0-9]{32}$/.test(installation)||!/^[a-f0-9]{64}$/.test(release)||!['postgres-secret','app-secret','dataset'].some((suffix)=>name===`${project}_${suffix}`))fail();
      if(data.resources.some((item)=>item.kind==='volume'&&item.name===name))throw new ModelExit(2);
      makeResource(data,'volume',name,project,installation,release,false);
      await save(all); log(name); await record('volume-create',{name,project},daemonName); throw new ModelExit(0);
    }
    if (subcommand === 'ls') {
      const format = valueAfter(argv, '--format');
      if (!format) fail();
      const projectFilter = (argv.filter((item) => item.startsWith('--filter=')).map((item) => item.slice(9)));
      const separateFilter = valueAfter(argv, '--filter');
      const filters = [...projectFilter, ...(separateFilter ? [separateFilter] : [])];
      if (!filters.length || filters.some((filter) => !filter.startsWith('name=') && !filter.startsWith('label=com.docker.compose.project='))) fail();
      for (const resource of data.resources.filter((item) => item.kind === kind && filters.some((filter) =>
        filter === `name=${item.project}` || filter === `label=com.docker.compose.project=${item.project}` ||
        (filter.startsWith('name=') && item.name.includes(filter.slice(5)))))) {
        log(`${resource.id}|${kind === 'container' ? resource.name : resource.name}`);
      }
      await record('list', { kind, filters },daemonName); throw new ModelExit(0);
    }
    if (subcommand === 'inspect' && argv.includes('--format')) {
      const id = argv.at(-1); const resource = data.resources.find((item) => item.kind === kind && item.id === id);
      if (!resource) throw new ModelExit(2);
      log(renderResource(resource, kind)); await record('inspect', { kind, id, name: resource.name },daemonName); throw new ModelExit(0);
    }
    if (subcommand === 'stop' && kind === 'container') {
      const id = argv.at(-1); const resource = data.resources.find((item) => item.kind === kind && item.id === id);
      if (!resource || !valueAfter(argv, '--time')) throw new ModelExit(2);
      resource.running = false; await save(all); await record('stop', { kind, id, name: resource.name },daemonName); throw new ModelExit(0);
    }
    if (subcommand === 'rm') {
      const id = argv.at(-1); const index = data.resources.findIndex((item) => item.kind === kind && item.id === id);
      if (index < 0) throw new ModelExit(2);
      const resource = data.resources[index]; data.resources.splice(index, 1);
      await save(all); await record('remove', { kind, id, name: resource.name, project: resource.project },daemonName); throw new ModelExit(0);
    }
  }
  fail();
} catch (error) { return {status:error instanceof ModelExit ? error.status : 70,stdout,stderr}; }

}

// 每次真实 Docker CLI 都经私有目录传输；模型本身没有 transport 分支。
export async function createTransport({state,ledger,control,directory}) {
  await mkdir(directory,{recursive:true,mode:0o700});
  const failed=`${directory}/failed`;
  const worker=new Worker(new URL(import.meta.url),{workerData:{state,ledger,control,directory}});
  let closed=false, readySettled=false, resolveReady, rejectReady, fatalError=null;
  const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
  const markFatal=(error)=>{
    const message=error instanceof Error ? error.message : String(error);
    fatalError ||= new Error(message);
    if(!closed) {
      try { writeFileSync(failed,`${message}\n`,{flag:'wx',mode:0o600}); }
      catch (writeError) { if(writeError.code!=='EEXIST') fatalError=writeError; }
    }
    if(!readySettled){readySettled=true;rejectReady(new Error(message));}
  };
  worker.on('message',message=>{
    if(message==='ready' && !readySettled){readySettled=true;resolveReady();}
    else if(message?.fatal) markFatal(message.fatal);
  });
  worker.on('error',markFatal);
  worker.on('exit',code=>{if(!closed) markFatal(`worker exited unexpectedly: ${code}`);});
  const timer=setTimeout(()=>markFatal('worker ready timeout'),5000);
  try { await ready; }
  catch(error){closed=true;await worker.terminate();await rm(directory,{recursive:true,force:true});throw error;}
  finally {clearTimeout(timer);}
  worker.unref();
  return {
    directory, worker,
    get fatal(){if(fatalError)return fatalError;try{return new Error(readFileSync(failed,'utf8').trim());}catch(error){if(error.code==='ENOENT')return null;throw error;}},
    assertHealthy(){const error=this.fatal;if(error)throw error;},
    clientSource(){return `#!/bin/sh
box='${directory.replaceAll("'", "'\\''")}'
fail() { printf '%s\\n' "transport failure: $1" > "$box/failed"; exit 97; }
[ ! -e "$box/failed" ] || exit 97
req="$box/q.$$"
mkdir -m 700 "$req" || fail 'request directory'
trap 'printf "%s\\n" "transport signal" > "$box/failed"; exit 97' HUP INT TERM
printf '%s\\n' "$#" > "$req/count" || fail 'argument count'
{ printf '%s\\000' "\${LEXIFLOW_INSTALLATION_ID-}" "\${LEXIFLOW_RELEASE_KEY-}"; for arg do printf '%s\\000' "$arg"; done; } > "$req/args" || fail 'arguments'
private_input=0
for arg do [ "$arg" = private-file-importer ] && private_input=1; done
if [ "$private_input" -eq 1 ]; then (umask 077; cat > "$req/stdin") || fail 'private stdin'; else : > "$req/stdin" || fail 'stdin marker'; fi
input_bytes=$(wc -c < "$req/stdin") || fail 'stdin size'
[ "$input_bytes" -le 16777216 ] || fail 'stdin bound'
# Empty marker creation publishes only fully closed args/count files.
: > "$req/ready" || fail 'ready publish'
n=0
while [ ! -f "$req/done" ]; do
 [ ! -e "$box/failed" ] || exit 97
 n=$((n+1)); [ "$n" -lt 5000 ] || fail 'response timeout'
 if [ "$n" -gt 200 ]; then sleep 0.002; fi
done
[ -f "$req/out" ] && [ -f "$req/err" ] && [ -f "$req/status" ] && [ -f "$req/lengths" ] || fail 'incomplete response'
IFS=' ' read -r outlen errlen < "$req/lengths" || fail 'response lengths'
# One wc keeps both byte counts tied to the same request snapshot; validate its
# complete six-token shape and fixed filenames before using either length.
counts=$(cd "$req" && wc -c out err) || fail 'response byte counts'
set -- $counts
[ "$#" -eq 6 ] && [ "$2" = out ] && [ "$4" = err ] && [ "$6" = total ] || fail 'response byte counts'
[ "$1" -eq "$outlen" ] && [ "$3" -eq "$errlen" ] || fail 'response length mismatch'
exec 3< "$req/status" || fail 'response status'
IFS= read -r status <&3 || fail 'response status'
extra=
if IFS= read -r extra <&3 || [ -n "$extra" ]; then fail 'trailing status'; fi
exec 3<&-
case "$status" in ''|*[!0-9]*) fail 'invalid status';; esac
[ "$status" -ge 0 ] && [ "$status" -le 255 ] || fail 'status range'
[ ! -s "$req/out" ] || cat "$req/out" || fail 'stdout'
[ ! -s "$req/err" ] || cat "$req/err" >&2 || fail 'stderr'
# rm -d is non-recursive: the explicit known file list is removed and the
# request directory must then be empty, so unknown residue fails closed.
rm -d -- "$req/args" "$req/count" "$req/stdin" "$req/out" "$req/err" "$req/status" "$req/lengths" "$req/done" "$req" || fail 'response cleanup'
trap - HUP INT TERM
exit "$status"
`;},
    async close(){
      let failure=this.fatal;
      try { const pending=(await readdir(directory)).filter(name=>name!=='failed');
        if(pending.length) failure ||= new Error(`transport residue: ${pending.join(',')}`);
      } catch(error){failure ||= error;}
      closed=true;
      try { await worker.terminate(); } catch(error){failure ||= error;}
      try { await rm(directory,{recursive:true,force:true}); } catch(error){failure ||= error;}
      if(failure)throw failure;
    },
  };
}

if(!isMainThread && workerData?.directory){
  const {state,ledger,control,directory}=workerData;
  let busy=false, failed=false;
  const poison=async error=>{
    failed=true;
    try {await writeFile(`${directory}/failed`,`${String(error)}\n`,{flag:'wx',mode:0o600});}
    catch(writeError){if(writeError.code!=='EEXIST') parentPort.postMessage({fatal:String(writeError)});}
    parentPort.postMessage({fatal:String(error)});
  };
  // Consumers cannot read any response until the final empty done marker exists.
  const publish=(path,data)=>writeFile(path,data,{flag:'wx',mode:0o600});
  const scan=async()=>{
    if(busy||failed)return;
    busy=true;
    try {
      for(const name of (await readdir(directory)).filter(name=>name.startsWith('q.')).sort()){
        const prefix=`${directory}/${name}`;
        let entries;
        try { entries=await readdir(prefix); }
        catch(error){if(error.code==='ENOENT')continue;throw error;}
        if(!entries.includes('ready'))continue;
        const countText=(await readFile(`${prefix}/count`,'utf8')).trim();
        if(!/^(0|[1-9][0-9]*)$/.test(countText))throw new Error('malformed argument count');
        const count=Number(countText);
        if(!Number.isSafeInteger(count))throw new Error('argument count range');
        const bytes=await readFile(`${prefix}/args`);
        if(!bytes.length||bytes.at(-1)!==0)throw new Error('malformed argv terminator');
        const fields=bytes.subarray(0,-1).toString('utf8').split('\0');
        if(fields.length!==count+2)throw new Error('malformed request fields');
        await rm(`${prefix}/ready`);
        const [installation,release,...argv]=fields;
        const stdinData=await readFile(`${prefix}/stdin`);
        const result=await execute(state,ledger,control,argv,{LEXIFLOW_INSTALLATION_ID:installation,LEXIFLOW_RELEASE_KEY:release},stdinData);
        await publish(`${prefix}/out`,result.stdout);
        await publish(`${prefix}/err`,result.stderr);
        await publish(`${prefix}/status`,`${result.status}\n`);
        await publish(`${prefix}/lengths`,`${Buffer.byteLength(result.stdout)} ${Buffer.byteLength(result.stderr)}\n`);
        await publish(`${prefix}/done`,'');
      }
    }catch(error){await poison(error);}finally{busy=false;}
  };
  setInterval(scan,2);
  parentPort.postMessage('ready');
}

if(isMainThread && process.argv[1]===new URL(import.meta.url).pathname){
  const [state,ledger,control,...argv]=process.argv.slice(2);
  const result=await execute(state,ledger,control,argv,process.env);
  process.stdout.write(result.stdout);process.stderr.write(result.stderr);process.exitCode=result.status;
}
