import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, writeFile, unlink, rmdir, readFile, chmod, symlink, rm, stat, link, lstat, readdir } from 'node:fs/promises';
import path from 'node:path';
import { execute as executeDockerModel, createTransport } from './fake-docker-daemon.mjs';
import { mkdtemp as mkdtempTransport, mkdir as mkdirTransport, readFile as readTransport, writeFile as writeTransport, rm as rmTransport } from 'node:fs/promises';
import os from 'node:os';
import { lifecycleFixture, spawnRuntime } from './lifecycle-fixture.mjs';

let renderedReceiverScript;
function receiverScript(root) {
  if (!renderedReceiverScript) {
    const compose=path.resolve(new URL('../../docker/compose.yaml',import.meta.url).pathname);
    const source=readFileSync(compose,'utf8');
    const service=source.split('  private-file-importer:\n')[1];
    assert.ok(service,'the fixed importer service is present');
    const literal=service.split('      - |\n')[1]?.split('      - private-file-importer\n')[0];
    assert.ok(literal,'the fixed receiver is one YAML literal');
    const escaped=literal.split('\n').map(line=>{
      assert.ok(line===''||line.startsWith('        '),'literal indentation is exact');
      return line.slice(8);
    }).join('\n');
    assert.equal(escaped.replaceAll('$$','').includes('$'),false,'all receiver dollars are escaped for Compose');
    // Direct shell tests need no Docker CLI. The actual Compose parser is
    // exercised separately by the real runtime check, before any installation.
    renderedReceiverScript=escaped.replaceAll('$$','$');
  }
  return renderedReceiverScript.replaceAll('/private-files',root);
}
async function runPrivateReceiver(mode, payload, {uuid='01234567-89ab-cdef-0123-456789abcdef', bytes, digest, header=true, root, chunks=false, extraArgs=[]}={}) {
  const script=receiverScript(root);
  const args=['-c',script,'private-file-importer',mode,uuid];
  let input=Buffer.from(payload);
  if(mode==='zip') args.push(String(bytes),digest);
  else if(header) input=Buffer.concat([Buffer.from(`${digest}\n`),input]);
  args.push(...extraArgs);
  if (chunks) {
    return await new Promise((resolve,reject)=>{
      const child=spawn('/bin/sh',args,{env:{PATH:`${path.join(root,'bin')}:/usr/bin:/bin`,LC_ALL:'C'},stdio:['pipe','pipe','pipe']});
      const stdout=[]; const stderr=[];
      child.stdout.on('data',chunk=>stdout.push(chunk)); child.stderr.on('data',chunk=>stderr.push(chunk));
      child.on('error',reject);
      child.on('close',status=>resolve({status,stdout:Buffer.concat(stdout).toString(),stderr:Buffer.concat(stderr).toString()}));
      child.stdin.on('error',error=>{ if(error.code!=='EPIPE') reject(error); });
      let offset=0;
      const send=()=>{
        if(child.stdin.destroyed) return;
        if(offset>=input.length) { child.stdin.end(); return; }
        const end=Math.min(input.length,offset+(typeof chunks==='number'?chunks:32768));
        child.stdin.write(input.subarray(offset,end)); offset=end;
        setTimeout(send,2);
      };
      send();
    });
  }
  const result=spawnSync('/bin/sh',args,{input,encoding:null,timeout:5000,env:{PATH:`${path.join(root,'bin')}:/usr/bin:/bin`,LC_ALL:'C'}});
  return {status:result.status,stdout:result.stdout?.toString()??'',stderr:result.stderr?.toString()??''};
}

test('fixed private-file receiver accepts three bounded modes and is idempotent without overwrite', async (t)=>{
  const root=await (await import('node:fs/promises')).mkdtemp(path.join(os.tmpdir(),'lf-private-receiver-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  await installReceiverTestTools(root);
  for(const name of ['postgres-secret','app-secret','dataset']) await mkdir(path.join(root,name));
  const secret=Buffer.from('a'.repeat(64)); const secretHash=execFileSync('sh',['-c','sha256sum | cut -d " " -f1'],{input:secret,encoding:'utf8'}).trim();
  const zip=Buffer.from('synthetic-zip-payload'); const zipHash=execFileSync('sh',['-c','sha256sum | cut -d " " -f1'],{input:zip,encoding:'utf8'}).trim();
  for(const [mode,payload,dir,target,hash] of [['pg64hex',secret,'postgres-secret','postgres-password',secretHash],['app64hex',secret,'app-secret','app-password',secretHash],['zip',zip,'dataset','dataset.zip',zipHash]]) {
    const opts={root,bytes:payload.length,digest:hash};
    let result=await runPrivateReceiver(mode,payload,opts);
    assert.equal(result.status,0,`${mode} receiver accepts exact payload (${result.stderr.trim()||result.stdout.trim()})`);
    assert.equal(result.stdout,'PRIVATE_FILE_IMPORTED\n');
    const file=path.join(root,dir,target); const first=await stat(file); const contents=await readFile(file);
    assert.equal(contents.toString(),payload.toString()); assert.equal((first.mode&0o777),0o444);
    result=await runPrivateReceiver(mode,payload,opts);
    assert.equal(result.status,0,`${mode} receiver accepts identical replay`);
    const second=await stat(file); assert.equal(second.ino,first.ino,'idempotent replay does not replace target');
  }
  const target=path.join(root,'dataset','dataset.zip');
  const conflict=await runPrivateReceiver('zip',Buffer.from('different'),{root,bytes:9,digest:'0'.repeat(64)});
  assert.notEqual(conflict.status,0,'different content is rejected');
  assert.equal((await readFile(target)).toString(),'synthetic-zip-payload');
});

test('fixed private-file receiver rejects malformed, short, extra, unknown, linked and partial files closed', async (t)=>{
  const root=await (await import('node:fs/promises')).mkdtemp(path.join(os.tmpdir(),'lf-private-receiver-negative-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  await installReceiverTestTools(root);
  for(const name of ['postgres-secret','app-secret','dataset']) await mkdir(path.join(root,name));
  const payload=Buffer.from('a'.repeat(64)); const hash=execFileSync('sh',['-c','sha256sum | cut -d " " -f1'],{input:payload,encoding:'utf8'}).trim();
  assert.notEqual((await runPrivateReceiver('pg64hex',Buffer.from('b'.repeat(64)),{root,digest:hash})).status,0,'hash mismatch rejected');
  assert.notEqual((await runPrivateReceiver('pg64hex',Buffer.from('a'.repeat(63)),{root,digest:hash})).status,0,'short secret rejected');
  assert.notEqual((await runPrivateReceiver('pg64hex',Buffer.from('a'.repeat(64)+'x'),{root,digest:hash})).status,0,'extra stdin rejected');
  assert.notEqual((await runPrivateReceiver('pg64hex',payload,{root,digest:hash.replace(/./,'z')})).status,0,'invalid header hash rejected');
  assert.notEqual((await runPrivateReceiver('pg64hex',Buffer.alloc(4096,0x61),{root,header:false})).status,0,'oversized unterminated header is bounded and rejected');
  const dir=path.join(root,'app-secret');
  await writeFile(path.join(dir,'unrecognized'),'x');
  assert.notEqual((await runPrivateReceiver('app64hex',payload,{root,digest:hash})).status,0,'unknown entry rejected');
  await unlink(path.join(dir,'unrecognized'));
  await symlink(path.join(root,'dataset'),path.join(dir,'app-password'));
  assert.notEqual((await runPrivateReceiver('app64hex',payload,{root,digest:hash})).status,0,'symlink target rejected');
  await unlink(path.join(dir,'app-password'));
  await writeFile(path.join(dir,'.pending-01234567-89ab-cdef-0123-456789abcdef'),'partial');
  const resumed=await runPrivateReceiver('app64hex',payload,{root,digest:hash});
  assert.equal(resumed.status,0,'matching transfer UUID can safely resume its partial file');
  assert.equal((await readFile(path.join(dir,'app-password'))).toString(),'a'.repeat(64));
  const pg=path.join(root,'postgres-secret'); await writeFile(path.join(pg,'foreign'),'x');
  await link(path.join(pg,'foreign'),path.join(pg,'other'));
  assert.notEqual((await runPrivateReceiver('pg64hex',payload,{root,digest:hash})).status,0,'hard-linked extra entries rejected');
});

test('rendered receiver closes fragmented pipe length, digest, mode and permission gaps', async (t)=>{
  const root=await mkdtempTransport(path.join(os.tmpdir(),'lf-private-fragmented-'));
  t.after(()=>rmTransport(root,{recursive:true,force:true}));
  await installReceiverTestTools(root);
  for(const name of ['postgres-secret','app-secret','dataset']) await mkdir(path.join(root,name));
  const secret=Buffer.from('a'.repeat(64));
  const secretHash=createHash('sha256').update(secret).digest('hex');
  const zip=Buffer.alloc(2*1048576+17,0x5a);
  zip.set([0x50,0x4b,0x03,0x04,0x00,0xff],0);
  zip.fill(0,4096,8192);
  const zipHash=createHash('sha256').update(zip).digest('hex');
  const pg={root,digest:secretHash,chunks:7};
  assert.equal((await runPrivateReceiver('pg64hex',secret,pg)).status,0,'fragmented secret reaches full 64 bytes');
  assert.equal((await readFile(path.join(root,'postgres-secret','postgres-password'))).length,64);
  assert.notEqual((await runPrivateReceiver('app64hex',secret.subarray(0,63),pg)).status,0,'fragmented short secret rejected');
  assert.notEqual((await runPrivateReceiver('app64hex',Buffer.concat([secret,Buffer.from('x')]),pg)).status,0,'fragmented extra secret rejected');
  const secretWithLf=Buffer.from('a'.repeat(63)+'\n');
  assert.notEqual((await runPrivateReceiver('app64hex',secretWithLf,{...pg,digest:createHash('sha256').update(secretWithLf).digest('hex')})).status,0,'64-byte secret with LF rejected even with matching digest');
  assert.equal((await readdir(path.join(root,'app-secret'))).length,0,'normal failure removes only authenticated pending');
  const dataset={root,bytes:zip.length,digest:zipHash,chunks:true};
  assert.equal((await runPrivateReceiver('zip',zip,dataset)).status,0,'multi-block fragmented ZIP accepted');
  assert.notEqual((await runPrivateReceiver('zip',zip.subarray(0,-1),dataset)).status,0,'fragmented short ZIP rejected');
  assert.notEqual((await runPrivateReceiver('zip',Buffer.concat([zip,Buffer.from('x')]),dataset)).status,0,'fragmented extra ZIP rejected');
  assert.notEqual((await runPrivateReceiver('zip',zip,{...dataset,digest:'0'.repeat(64)})).status,0,'fragmented wrong ZIP hash rejected');
  assert.notEqual((await runPrivateReceiver('zip',zip,{...dataset,extraArgs:['unexpected']})).status,0,'strict arity enforced');
  assert.notEqual((await runPrivateReceiver('zip',zip,{...dataset,uuid:'0123456--89ab-cdef-0123-456789abcdef'})).status,0,'internal UUID dash rejected');
  await chmod(path.join(root,'dataset','dataset.zip'),0o600);
  assert.notEqual((await runPrivateReceiver('zip',zip,dataset)).status,0,'existing target must retain mode 0444');
});

async function installReceiverTestTools(root) {
  const bin=path.join(root,'bin'); await mkdir(bin);
  // Linux 使用实际 GNU 工具；这里只为 macOS 的 BSD stat 提供测试适配。
  if (process.platform !== 'darwin') return;
  await writeFile(path.join(bin,'sha256sum'),'#!/bin/sh\nexec shasum -a 256 "$@"\n',{mode:0o755});
  await writeFile(path.join(bin,'stat'),'#!/bin/sh\n[ "$1" = -c ] || exit 2\nformat=$2; shift 2\ncase "$format" in %h) exec /usr/bin/stat -f %l "$@";; %a) exec /usr/bin/stat -f %Lp "$@";; *) exit 2;; esac\n',{mode:0o755});
  await chmod(path.join(bin,'sha256sum'),0o755); await chmod(path.join(bin,'stat'),0o755);
}

const transportArgs=['--host','unix:///synthetic.sock','info','--format','{{.ID}}'];
const transportEnv={LEXIFLOW_INSTALLATION_ID:'a'.repeat(32),LEXIFLOW_RELEASE_KEY:'b'.repeat(64)};
async function transportFixture(t,{id='id-one',composeFail=false,clientTransform=source=>source}={}) {
  const dir=await mkdtempTransport(path.join(os.tmpdir(),'lf-transport-'));
  const state=path.join(dir,'state'),control=path.join(dir,'control'),ledger=path.join(dir,'ledger'),box=path.join(dir,'box'),client=path.join(dir,'docker');
  await writeTransport(state,JSON.stringify({daemons:{primary:{id,resources:[]},clone:{id:'id-clone',resources:[]}}}));
  await writeTransport(control,JSON.stringify({contextEndpoint:'unix:///synthetic.sock',endpoints:{'unix:///synthetic.sock':'primary'},composeFail}));
  const transport=await createTransport({state,control,ledger,directory:box});
  await writeTransport(client,clientTransform(transport.clientSource()),{mode:0o700});
  let closed=false;t.after(async()=>{if(closed)return;let failure;try{await transport.close();}catch(error){failure=error;}finally{await rmTransport(dir,{recursive:true,force:true});}if(failure)throw failure;});
  const cli=(argv,env={})=>{const result=spawnSync(client,argv,{encoding:'utf8',timeout:12000,env:{...process.env,...env}});assert.equal(result.error,undefined);return {status:result.status,stdout:result.stdout,stderr:result.stderr};};
  return {dir,state,control,ledger,box,client,transport,cli,markClosed:()=>{closed=true;}};
}

test('transport 真实 shell CLI 与 direct 模型逐请求结果和 ledger 一致',async(t)=>{
  const f=await transportFixture(t);
  const cases=[transportArgs,['--host','unix:///synthetic.sock','container','ls','--filter','name=x','--format','{{.ID}}'],['--host','unix:///synthetic.sock','inspect','--format','x','missing'],['bad']];
  for(const argv of cases){
    const expected=await executeDockerModel(f.state,f.ledger,f.control,argv,{});
    const actual=f.cli(argv);
    assert.deepEqual(actual,expected);
    const node=spawnSync(process.execPath,[new URL('./fake-docker-daemon.mjs',import.meta.url).pathname,f.state,f.ledger,f.control,...argv],{encoding:'utf8'});
    assert.deepEqual({status:node.status,stdout:node.stdout,stderr:node.stderr},expected);
  }
  const actions=(await readTransport(f.ledger,'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(actions.map(x=>x.action),['info','info','info','list','list','list']);
});

test('transport 多ID观察按原顺序返回全部归属且部分失败保持非零',async(t)=>{
  const f=await transportFixture(t);
  const state=JSON.parse(await readTransport(f.state));
  for(const kind of ['container','network','volume']){
    const rows=[0,1].map(index=>({kind,id:kind==='volume'?`volume-${index}`:String(index+1).repeat(64),name:`${kind}-${index}`,labels:{project:'project',installation:'a'.repeat(32),release:'b'.repeat(64)}}));
    state.daemons.primary.resources=rows;
    await writeTransport(f.state,JSON.stringify(state));
    const render=row=>`${row.id}|${kind==='container'?'/':''}${row.name}|${row.labels.project}|${row.labels.installation}|${row.labels.release}\n`;
    for(const ids of [[rows[1].id,rows[0].id],[rows[0].id,rows[0].id],[rows[0].id,'missing'],['missing',rows[1].id]]){
      const argv=['--host','unix:///synthetic.sock',kind,'inspect','--format','fixed',...ids];
      const before=await readTransport(f.state,'utf8');
      const expectedRows=[];
      for(const id of ids){const row=rows.find(item=>item.id===id);if(!row)break;expectedRows.push(row);}
      const expected={status:ids.includes('missing')?2:0,stdout:expectedRows.map(render).join(''),stderr:''};
      const direct=await executeDockerModel(f.state,f.ledger,f.control,argv,{});
      const actual=f.cli(argv);
      assert.deepEqual(direct,expected); assert.deepEqual(actual,expected);
      assert.equal(await readTransport(f.state,'utf8'),before,'只读观察不能修改资源');
    }
  }
  const actions=(await readTransport(f.ledger,'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(actions.length,30);
  assert.ok(actions.every(item=>item.action==='inspect'));
});

test('transport 动态 control、真实 ID 切换与重复请求不缓存',async(t)=>{
  const f=await transportFixture(t);
  assert.equal(f.cli(transportArgs).stdout,'id-one\n');
  const base=JSON.parse(await readTransport(f.control));
  await writeTransport(f.control,JSON.stringify({...base,idOverride:'雪 with space\nnext'}));
  assert.equal(f.cli(transportArgs).stdout,'雪 with space\nnext\n');
  await writeTransport(f.control,JSON.stringify({...base,switchAfterIdQueries:{remaining:1,endpoint:'unix:///synthetic.sock',target:'clone'}}));
  assert.equal(f.cli(transportArgs).stdout,'id-one\n');
  assert.equal(f.cli(transportArgs).stdout,'id-clone\n');
  assert.equal(JSON.parse(await readTransport(f.control)).endpoints['unix:///synthetic.sock'],'clone');
  await writeTransport(f.control,JSON.stringify({endpoints:{'unix:///synthetic.sock':'clone'},idOverride:{value:'雪'}}));
  assert.equal(f.cli(['context','inspect','--format','x']).stdout,'undefined\n');
  assert.equal(f.cli(transportArgs).stdout,"{ value: '雪' }\n");
  assert.equal((await readTransport(f.ledger,'utf8')).trim().split('\n').length,6);
});

test('transport compose 实际身份环境、Unicode 空值与非零 2 64 70 71',async(t)=>{
  const f=await transportFixture(t);
  const compose=['--host','unix:///synthetic.sock','compose','--project-name','项目 with space','-f','file name.yaml','up','-d','--no-deps','postgres'];
  const ok=f.cli(compose,transportEnv);
  assert.equal(ok.status,0);
  const resources=JSON.parse(await readTransport(f.state)).daemons.primary.resources;
  assert.equal(resources[0].labels.installation,transportEnv.LEXIFLOW_INSTALLATION_ID);
  assert.equal(resources[0].labels.release,transportEnv.LEXIFLOW_RELEASE_KEY);
  assert.equal(resources[0].labels.project,'项目 with space');
  assert.equal(f.cli(compose,{...transportEnv,LEXIFLOW_RELEASE_KEY:''}).status,64);
  assert.equal(f.cli(['--host','unix:///synthetic.sock','inspect','--format','x','missing']).status,2);
  assert.equal(f.cli(['bad','', '雪 with space\nnext']).status,64);
  const base=JSON.parse(await readTransport(f.control));
  await writeTransport(f.control,JSON.stringify({...base,composeFail:true}));
  assert.equal(f.cli(compose,transportEnv).status,71);
  await writeTransport(f.state,'not json');
  assert.equal(f.cli(transportArgs).status,70);
  const actions=(await readTransport(f.ledger,'utf8')).trim().split('\n').map(JSON.parse);
  assert.ok(actions.some(x=>x.action==='compose-up'&&x.resource.project==='项目 with space'));
  assert.ok(actions.some(x=>x.action==='compose-failed'));
});

test('transport 两实例状态隔离、同一模型每次读取 state',async(t)=>{
  const a=await transportFixture(t,{id:'A'}),b=await transportFixture(t,{id:'B'});
  assert.equal(a.cli(transportArgs).stdout,'A\n');
  assert.equal(b.cli(transportArgs).stdout,'B\n');
  await writeTransport(a.state,JSON.stringify({daemons:{primary:{id:'A2',resources:[]},clone:{id:'C',resources:[]}}}));
  assert.equal(a.cli(transportArgs).stdout,'A2\n');
  assert.equal(b.cli(transportArgs).stdout,'B\n');
});

test('transport batching 对损坏长度、缺失响应和未知残留失败关闭',async(t)=>{
  const faults=[
    ['bad-length',`printf '999 0\\n' > "$req/lengths"`],
    ['missing-response',`rm "$req/out"`],
    ['unknown-entry',`: > "$req/unexpected"`],
  ];
  for(const [name,injection] of faults){
    const marker='done\n[ -f "$req/out" ]';
    const f=await transportFixture(t,{clientTransform:source=>{
      assert.ok(source.includes(marker),'fault injection point must remain after done and before response validation');
      return source.replace(marker,`done\n${injection}\n[ -f "$req/out" ]`);
    }});
    const result=f.cli(transportArgs);
    assert.equal(result.status,97,`${name} client must report the transport failure status`);
    const expectedFailure=name==='bad-length'?'response length mismatch':name==='missing-response'?'incomplete response':'response cleanup';
    assert.match(await readTransport(path.join(f.box,'failed'),'utf8'),new RegExp(`transport failure: ${expectedFailure}`));
    assert.ok(f.transport.fatal,`${name} failure marker must remain sticky`);
    assert.throws(()=>f.transport.assertHealthy(),/transport failure:/);
    // close is expected to reject due to sticky fatal, but must still terminate
    // the worker and remove its private directory before the fixture is marked closed.
    await assert.rejects(()=>f.transport.close(),/transport failure:/);
    f.markClosed();
    assert.equal(await stat(f.box).then(()=>true,error=>{assert.equal(error.code,'ENOENT');return false;}),false);
    assert.equal(f.transport.worker.threadId,-1);
    await rmTransport(f.dir,{recursive:true});
  }
});

const podmanTuple=(patch={})=>{
  const fields=['linux','aarch64','synthetic-host','true','present','0,501,1;1,100000,65536;','0,20,1;1,100000,65536;','overlay','/var/lib/containers/storage','/run/containers/storage','/var/lib/containers/storage/volumes','false','5.6.2'];
  for(const [index,value] of Object.entries(patch)) fields[Number(index)]=value;
  return fields.join('|')+'\n';
};
const podmanComponents='Podman Engine\nConmon\nOCI Runtime\nEngine\n';
const dockerAdapterSource=readFileSync(new URL('../runtime/release-installation-docker.sh',import.meta.url),'utf8');
const shellQuote=(value)=>`'${String(value).replaceAll("'", "'\\''")}'`;
async function podmanIdentityFixture(t,{record=podmanTuple(),components=podmanComponents,invalidHash=false}={}){
  const dir=await mkdtempTransport(path.join(os.tmpdir(),'lf-podman-identity-'));
  const bin=path.join(dir,'bin'),recordFile=path.join(dir,'record'),componentsFile=path.join(dir,'components');
  await mkdirTransport(bin);await writeTransport(recordFile,record);await writeTransport(componentsFile,components);
  const podman=`#!/bin/sh\n[ "$#" -eq 5 ] && [ "$1" = --url ] && [ "$2" = unix:///synthetic.sock ] && [ "$3" = info ] && [ "$4" = --format ] || exit 64\ncat -- ${shellQuote(recordFile)}\n`;
  const docker=`#!/bin/sh\n[ "$1" = --host ] && [ "$2" = unix:///synthetic.sock ] && [ "$3" = version ] || exit 64\ncat -- ${shellQuote(componentsFile)}\n`;
  await writeTransport(path.join(bin,'podman'),podman,{mode:0o700});
  await writeTransport(path.join(bin,'docker'),docker,{mode:0o700});
  if(invalidHash) await writeTransport(path.join(bin,'sha256sum'),'#!/bin/sh\nprintf "not-a-digest  -\\n"\n',{mode:0o700});
  t.after(()=>rmTransport(dir,{recursive:true,force:true}));
  const run=(command)=>spawnSync('/bin/sh',['-c',`${dockerAdapterSource}\n${command}`,'adapter'],{
    encoding:'utf8',timeout:15000,env:{...process.env,PATH:`${bin}:${process.env.PATH}`,DOCKER_HOST:'',DOCKER_CONTEXT:'',DOCKER_CONFIG:''},
  });
  return {dir,bin,recordFile,componentsFile,run};
}

test('Podman identity hashes an explicit native host/store tuple, not a compatibility ID',async(t)=>{
  const f=await podmanIdentityFixture(t);
  const result=f.run('lf_podman_identity_query unix:///synthetic.sock');
  const expected=createHash('sha256').update(podmanTuple().trimEnd()+'\n').digest('hex');
  assert.equal(result.status,0,result.stderr);
  assert.equal(result.stdout,`podman:${expected}\n`);
  await writeTransport(f.recordFile,podmanTuple({2:'changed-host'}));
  const changed=f.run('lf_podman_identity_query unix:///synthetic.sock');
  assert.equal(changed.status,0,changed.stderr);
  assert.notEqual(changed.stdout,result.stdout,'native host tuple changes the explicit identity');
});

test('Podman engine classification ambiguity and absent/unsupported engine evidence are rejected',async(t)=>{
  const f=await podmanIdentityFixture(t);
  for(const [label,components,accepted] of [
    ['one Podman engine',podmanComponents,true],
    ['unknown engine', 'Conmon\nOCI Runtime\n',false],
    ['unknown primary beside Engine','Engine\nFuture Engine\n',false],
    ['duplicate Podman Engine','Podman Engine\nPodman Engine\nEngine\n',false],
    ['duplicate Docker Engine','Engine\nEngine\n',false],
    ['mixed engine kinds','Podman Engine\nDocker Engine\n',false],
  ]){
    await writeTransport(f.componentsFile,components);
    const result=f.run('lf_docker_server_kind unix:///synthetic.sock');
    assert.equal(result.status,accepted?0:1,`${label}: ${result.stderr}`);
    if(accepted) assert.equal(result.stdout,'podman\n');
  }
});

test('Podman mappings accept exact 2^32 boundary and reject malformed mappings or hash output',async(t)=>{
  const boundary=podmanTuple({5:'4294967295,0,1;',6:'0,20,1;'});
  const f=await podmanIdentityFixture(t,{record:boundary});
  const valid=f.run('lf_podman_identity_query unix:///synthetic.sock');
  assert.equal(valid.status,0,valid.stderr);
  await writeTransport(f.recordFile,podmanTuple({5:'4294967296,0,1;'}));
  assert.equal(f.run('lf_podman_identity_query unix:///synthetic.sock').status,1,'mapping beyond 2^32 is rejected');
  const badHash=await podmanIdentityFixture(t,{record:boundary,invalidHash:true});
  assert.equal(badHash.run('lf_podman_identity_query unix:///synthetic.sock').status,1,'malformed digest-tool output is rejected');
  assert.equal(f.run('lf_podman_map_valid "4294967295,0,1;" true').status,0);
  assert.equal(f.run('lf_podman_map_valid "0,0,4294967297;" true').status,1);
});

test('TERM during ready supervision shuts down API and PostgreSQL within stop budget', async (t) => {
  const f = await lifecycleFixture(t);
  const pidFiles = { pg: path.join(f.tmp, 'pg.pid'), api: path.join(f.tmp, 'api.pid') };
  const health = `{"softwareVersion":"2.1.0-test","apiContract":"caption-hints.v2","mode":"formal","ready":true,"reason":"OK","datasetVersion":123,"publishedDataset":{"releaseId":"1792210462122","preparationId":"lexiflow.deterministic-preparation.v1","ruleId":"${'c'.repeat(64)}","sqlVersion":"${'b'.repeat(64)}","sha256":"${'a'.repeat(64)}"}}`;
  const body = `
id() { [ "$1" = -un ] && { printf 'postgres\\n'; return 0; }; return 1; }
prepare_pgdata() { mkdir -p "$PGDATA"; printf '17\\n' > "$PGDATA/PG_VERSION"; }
start_postgres() { sleep 60 & PG_PID=$!; printf '%s\\n' "$PG_PID" > '${pidFiles.pg}'; }
bootstrap_database() { :; }
start_api() { sleep 60 & API_PID=$!; printf '%s\\n' "$API_PID" > '${pidFiles.api}'; }
http_status_body() { case "$1" in /api/v1/runtime-status) HTTP_BODY='{"datasetVersion":123}';; *) HTTP_BODY='${health}';; esac; return 0; }
run_initializer() { return 0; }
runtime_start`;
  const child = spawnRuntime(f, body);
  let stderr = '';
  child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if (await readFile(path.join(f.root, '.lexiflow-ready')).then(() => true, () => false)) break;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.equal(await readFile(path.join(f.root, '.lexiflow-ready')).then(() => true, () => false), true, stderr);
  const pgPid = Number((await readFile(pidFiles.pg, 'utf8')).trim());
  const apiPid = Number((await readFile(pidFiles.api, 'utf8')).trim());
  child.kill('SIGTERM');
  const result = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`TERM shutdown exceeded bound: ${stderr}`)), 8000);
    child.once('error', reject);
    child.once('exit', (code, signal) => { clearTimeout(timer); resolve({ code, signal }); });
  });
  assert.ok(Date.now() - started < 8000);
  assert.deepEqual(result, { code: 0, signal: null });
  for (const pid of [pgPid, apiPid]) assert.throws(() => process.kill(pid, 0), (error) => error.code === 'ESRCH');
});

test('transport 协议损坏和 worker 停机留下 sticky fatal，close 清理且失败',async(t)=>{
  for(const fault of ['protocol','stop']){
    const f=await transportFixture(t);
    if(fault==='protocol'){
      const bad=path.join(f.box,'q.corrupt');await mkdirTransport(bad);await writeTransport(path.join(bad,'ready'),'');
    }else await f.transport.worker.terminate();
    const end=Date.now()+1000;
    while(!f.transport.fatal&&Date.now()<end)await new Promise(resolve=>setTimeout(resolve,5));
    assert.ok(f.transport.fatal,`${fault} must persist fatal`);
    assert.equal(f.cli(transportArgs).status,97);
    assert.throws(()=>f.transport.assertHealthy());
    await assert.rejects(()=>f.transport.close());f.markClosed();
    assert.equal(await stat(f.box).then(()=>true,error=>{assert.equal(error.code,'ENOENT');return false;}),false);
    assert.equal(f.transport.worker.threadId,-1,'worker must be terminated');
    await rmTransport(f.dir,{recursive:true});
  }
});
