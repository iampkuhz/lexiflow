import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, cp, rm, realpath } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';
import { extensionBytes } from './zip-fixture.mjs';
import { packageManifest } from '../build/package-release-artifacts.mjs';
import { generateRuntimeEntry } from '../build/release-runtime-entry-generator.mjs';
import { createTransport } from './fake-docker-daemon.mjs';

const sha = (value) => createHash('sha256').update(value).digest('hex');
const daemonByTest = new WeakMap();
export async function lifecycleFixture(t, full = true, variant = '') {
  const tmp = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lf-lifecycle-fixture-')));
  let daemonOwnsTmp=false;
  t.after(async()=>{if(!daemonOwnsTmp) await rm(tmp,{recursive:true,force:true});});
  const repo = path.join(tmp, 'repo'); await mkdir(path.join(repo, 'ops/release'), { recursive: true });
  for (const directory of ['build', 'integrity', 'runtime']) await mkdir(path.join(repo, 'ops/release', directory), { recursive: true });
  const local = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
  for (const name of ['version.txt','build/release-runtime-entry-generator.mjs','runtime/release-artifact-verification.sh','runtime/release-installation-state.sh','runtime/release-installation-docker.sh','runtime/release-installation-lifecycle.sh']) await cp(path.join(local,name),path.join(repo,'ops/release',name));
  await writeFile(path.join(repo, 'ops/release/version.txt'), '0.1.0\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'fixture@example.invalid']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'Fixture']);
  execFileSync('git', ['-C', repo, 'add', '.']); execFileSync('git', ['-C', repo, 'commit', '-qm', '合成输入']);
  const sourceCommit=execFileSync('git',['-C',repo,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
  const buildIdentity = resolveBuildIdentity(repo);
  const root = path.join(tmp, 'artifacts with spaces'); await mkdir(root);
  const notices = [['api-runtime','API-runtime'],['lexiflow','LexiFlow'],['postgres','PostgreSQL'],['extension-third-party','extension-third-party'],['dataset-license','dataset']];
  const licenses = notices.map(([id, component]) => ({ id, component, licenseId:'MIT', licenseName:'MIT', sourceUrl:'https://example.invalid/license', noticePath:`licenses/${id}.txt`, noticeBytes:1, noticeSha256:sha('L') }));
  const artifacts=[];
  async function add(role, file, content, licenseIds, extra={}) { await mkdir(path.dirname(path.join(root,file)),{recursive:true}); await writeFile(path.join(root,file),content); artifacts.push({role,path:file,bytes:Buffer.byteLength(content),sha256:sha(content),licenseIds,...extra}); }
  for (const lic of licenses) await add('license',lic.noticePath,'L',[lic.id]);
  await add('api-image','images/api.tar','synthetic api',['api-runtime','lexiflow'],{platform:'linux/amd64',imageDigest:`sha256:${'a'.repeat(64)}`});
  await add('postgres-image','images/db.tar','synthetic db',['postgres'],{platform:'linux/amd64',imageDigest:`sha256:${'b'.repeat(64)}`});
  const compose = await readFile(path.resolve(local,'../docker/compose.yaml'));
  await add('compose','compose.yaml',compose,['lexiflow']);
  await add('dataset','dataset.zip',`synthetic dataset ${variant}`,['dataset-license'],{metadata:{releaseId:'r',preparationId:'p',ruleId:'rule',sqlVersion:'sql1'}});
  await add('extension','extension.zip',extensionBytes(buildIdentity),['lexiflow','extension-third-party'],{metadata:{softwareVersion:'0.1.0',sourceCommit}});
  await add('sql','schema.sql','synthetic schema',['lexiflow']);
  const descriptor={schemaVersion:1,buildIdentity,softwareVersion:'0.1.0',sourceCommit,apiContract:'api1',sqlVersion:'sql1',dataset:{releaseId:'r',preparationId:'p',ruleId:'rule'},platforms:['linux/amd64'],artifacts,licenses};
  let generated;
  if (full === true) {
    generated=await generateRuntimeEntry({repoRoot:repo,descriptor,artifactRoot:root});
  } else if (full === 'owner-init-fault') {
    // Test-only synthetic fault: exercise the real generated prepare path while
    // making the real lf_owner_init stop after mkdir and before owner publish.
    const { generateRuntimeEntry } = await import('../build/release-runtime-entry-generator.mjs');
    const [state,docker,lifecycle]=await Promise.all(['runtime/release-installation-state.sh','runtime/release-installation-docker.sh','runtime/release-installation-lifecycle.sh']
      .map((name)=>readFile(path.resolve(local,name),'utf8')));
    const lifecycleBody=`${state}\n${docker}\nlf_random_hex() { return 1; }\n${lifecycle}`;
    generated=await generateRuntimeEntry({repoRoot:repo,descriptor,artifactRoot:root,lifecycleBody});
  } else {
    let body=`lf_verify_release || exit 1\nlf_select_platform linux/amd64 || exit 1\nif [ "\${1:-}" = verify ]; then exit 0; fi\nexit 64`;
    if (full === 'adapter') {
      body=`lf_verify_release || exit 1\nlf_select_platform linux/amd64 || exit 1\ncase "\${1:-}" in\n _fixture_init) LF_ROOT=$2; parent=$(dirname -- "$LF_ROOT"); lf_owner_init && lf_state_load && printf '%s|%s|%s\\n' "$LF_INSTALL_ID" "$LF_PHASE" "$LF_ACTIVE";;\n _fixture_write) LF_ROOT=$2; lf_state_load && lf_lock_acquire && lf_state_write prepared none none $3 none none;;\n _fixture_load) LF_ROOT=$2; lf_state_load && printf '%s|%s|%s\\n' "$LF_PHASE" "$LF_ACTIVE" "$LF_CANDIDATE";;\n *) exit 64;;\nesac`;
      const [state,docker]=await Promise.all(['runtime/release-installation-state.sh','runtime/release-installation-docker.sh'].map((name)=>readFile(path.resolve(local,name),'utf8')));
      body=`${state}\n${docker}\n${body}`;
    }
    generated=await generateRuntimeEntry({repoRoot:repo,descriptor,artifactRoot:root,lifecycleBody:body});
  }
  const descriptorPath=path.join(tmp,'descriptor.json'); await writeFile(descriptorPath,JSON.stringify({...descriptor,artifacts:[...descriptor.artifacts,generated.artifact]}));
  const pkg=path.join(tmp,'package'); await packageManifest({repoRoot:repo,descriptorFile:descriptorPath,artifactRoot:root,outputDirectory:pkg});
  const bin=path.join(tmp,'bin'); await mkdir(bin);
  let daemon=daemonByTest.get(t);
  if (!daemon) {
    daemon={state:path.join(tmp,'docker-daemon.json'),ledger:path.join(tmp,'docker-actions.jsonl'),control:path.join(tmp,'docker-control.json'),tmp,fixtures:[],fatal:null};
    await writeFile(daemon.state,JSON.stringify({daemons:{primary:{id:'synthetic-daemon-id',resources:[]},empty:{id:'empty-daemon-id',resources:[]},clone:{id:'clone-daemon-id',resources:[]}}}));
    await writeFile(daemon.control,JSON.stringify({contextEndpoint:'unix:///lexiflow-synthetic-docker.sock',endpoints:{'unix:///lexiflow-synthetic-docker.sock':'primary','unix:///other-empty.sock':'empty','unix:///other-clone.sock':'clone'}}));
    daemonByTest.set(t,daemon);
    daemon.mailbox=path.join(tmp,'mailbox');
    daemon.transport=await createTransport({...daemon,directory:daemon.mailbox});
    daemon.closed=false;
    t.after(async()=>{
      let failure=daemon.transport.fatal;
      try { await daemon.transport.close(); daemon.closed=true; } catch(error) { failure ||= error; }
      for(const dir of daemon.fixtures) { try { await rm(dir,{recursive:true}); } catch(error) { failure ||= error; } }
      if(failure) throw failure;
    });
  }
  daemon.fixtures.push(tmp);
  daemonOwnsTmp=true;
  const fake=daemon.transport.clientSource();
  await writeFile(path.join(bin,'docker'),fake,{mode:0o755});
  const healthy=()=>daemon.transport.assertHealthy();
  const runWith=(environment,...args)=>{try{return execFileSync('sh',[path.join(pkg,'lexiflow.sh'),...args],{encoding:'utf8',timeout:60000,env:{...process.env,PATH:`${bin}:${process.env.PATH}`,DOCKER_HOST:'',DOCKER_CONTEXT:'',DOCKER_CONFIG:'',...environment},stdio:['ignore','pipe','pipe']});}finally{healthy();}};
  const run=(...args)=>runWith({},...args);
  t.after(()=>healthy());
  const daemonState=async(name='primary')=>JSON.parse(await readFile(daemon.state,'utf8')).daemons[name];
  const daemonControl=async()=>JSON.parse(await readFile(daemon.control,'utf8'));
  const setDaemonControl=async(patch)=>writeFile(daemon.control,JSON.stringify({...await daemonControl(),...patch}));
  const cloneResources=async(source='primary',target='clone')=>{
    const data=JSON.parse(await readFile(daemon.state,'utf8'));
    data.daemons[target].resources=structuredClone(data.daemons[source].resources);
    await writeFile(daemon.state,JSON.stringify(data));
  };
  const daemonActions=async()=>{try{return (await readFile(daemon.ledger,'utf8')).trim().split('\n').filter(Boolean).map((line)=>JSON.parse(line));}catch(error){if(error.code==='ENOENT')return [];throw error;}};
  return {tmp,repo,root,pkg,descriptor,generated,run,runWith,state:daemon.state,daemonState,daemonActions,daemonControl,setDaemonControl,cloneResources,bin,daemon,healthy};
}
