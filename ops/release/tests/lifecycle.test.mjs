import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, writeFile, unlink, rmdir, readFile, chmod, symlink, rm, stat, link, lstat, readdir } from 'node:fs/promises';
import path from 'node:path';
import { lifecycleFixture } from './lifecycle-fixture.mjs';
import { execute as executeDockerModel, createTransport } from './fake-docker-daemon.mjs';
import { mkdtemp as mkdtempTransport, mkdir as mkdirTransport, readFile as readTransport, writeFile as writeTransport, rm as rmTransport } from 'node:fs/promises';
import os from 'node:os';

// 原六个 case 分别由下列五个具名断言阶段及主 test 的两代闭环承载；
// 不使用 nested test，TAP 仅有一个顶层 case。
const mutatingActions=new Set(['compose-up','compose-run','volume-create','stop','remove','image-load']);
const mutationCount=(actions)=>actions.filter((item)=>mutatingActions.has(item.action)).length;
const defaultDockerEnv=(bin)=>({...process.env,PATH:`${bin}:${process.env.PATH}`,DOCKER_HOST:'',DOCKER_CONTEXT:'',DOCKER_CONFIG:''});
const restoreControl=async (f,base)=>f.setDaemonControl({...base,idOverride:null,idQueryFail:false,composeFail:false,switchAfterIdQueries:null});

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

async function withProjectLock(root,token,body) {
  const lock=path.join(root,'.lock');
  await mkdir(lock,{mode:0o700});
  await writeFile(path.join(lock,'token'),`${token}\n`,{flag:'wx',mode:0o600});
  try { await body(); }
  finally { await unlink(path.join(lock,'token')); await rmdir(lock); }
}

async function assertFirstBindFailures(f) {
  const base=await f.daemonControl();
  const root=path.join(f.tmp,'first-bind install');
  const invalidRoot=path.join(f.tmp,'invalid-id install');
  try {
    await f.setDaemonControl({idQueryFail:true});
    assert.throws(()=>f.run('prepare',root));
    assert.equal((await f.daemonState()).resources.length,0);
    assert.equal(await readFile(path.join(root,'engine')).then(()=>true,()=>false),false);
    assert.equal(f.run('status',root).trim(),'idle none none none','失败初绑不得提交稳定 prepared');
    await f.setDaemonControl({idQueryFail:false,idOverride:'invalid/id'});
    assert.throws(()=>f.run('prepare',invalidRoot));
    assert.equal((await f.daemonState()).resources.length,0);
    assert.equal(await readFile(path.join(invalidRoot,'engine')).then(()=>true,()=>false),false);
    assert.equal(f.run('status',invalidRoot).trim(),'idle none none none','非法 ID 不得提交稳定 prepared');
  } finally { await restoreControl(f,base); }
}

async function assertInputAndReleaseKey(f,root) {
  assert.throws(()=>f.run('status','relative-root'));
  const status=f.run('status',root);
  assert.doesNotMatch(status,/password|secret|dataset|tmp|Users/u);
  assert.throws(()=>f.run('status',root,'extra'));
  const key=status.trim().split(' ')[3];
  const wrongKey=key==='a'.repeat(64)?'b'.repeat(64):'a'.repeat(64);
  const token='c'.repeat(32);
  const before=structuredClone((await f.daemonState()).resources);
  const beforeActions=(await f.daemonActions()).length;
  const project=(target,suppliedToken)=>execFileSync('sh',[
    path.join(f.pkg,'lexiflow.sh'),'_project',root,target,suppliedToken,'verify-record',
  ],{encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe'],env:{
    ...defaultDockerEnv(f.bin),LF_RELEASE_KEY:wrongKey,
  }});
  await withProjectLock(root,token,async()=>{
    assert.equal(project(key,token),'','自身发行身份不得继承父发行 key');
    assert.throws(()=>project(wrongKey,token),'环境与请求同为错误 key 也不能通过');
    assert.throws(()=>project(key,'d'.repeat(32)),'错误锁 token 必须拒绝');
    assert.deepEqual((await f.daemonState()).resources,before,'身份验证不得变更 Docker 资源');
    assert.equal(mutationCount((await f.daemonActions()).slice(beforeActions)),0,'身份验证不得执行资源变更动作');
    assert.equal(f.run('status',root).trim(),status.trim(),'输入拒绝不得改变 journal');
  });
}

async function assertBindingFailures(f,root) {
  const key=f.run('status',root).trim().split(' ')[3];
  const status=`prepared none none ${key}`;
  const before=structuredClone((await f.daemonState()).resources);
  const beforeActions=(await f.daemonActions()).length;
  const base=await f.daemonControl();
  const assertNoEffect=async(label)=>{
    assert.equal(f.run('status',root).trim(),status,`${label} 不得提交稳定成功 journal`);
    assert.deepEqual((await f.daemonState()).resources,before,`${label} 不得变更绑定 daemon 资源`);
    assert.equal(mutationCount((await f.daemonActions()).slice(beforeActions)),0,`${label} 不得执行资源变更动作`);
  };
  try {
    assert.throws(()=>f.runWith({DOCKER_HOST:'unix:///other-empty.sock'},'activate',root),'不同 endpoint 的空 daemon 必须拒绝');
    await assertNoEffect('空 daemon');
    await f.cloneResources();
    assert.throws(()=>f.runWith({DOCKER_HOST:'unix:///other-clone.sock'},'activate',root),'不同 daemon 的同名同标签资源不得接管');
    await assertNoEffect('同标签克隆 daemon');
    await f.setDaemonControl({endpoints:{...base.endpoints,'unix:///lexiflow-synthetic-docker.sock':'clone'}});
    assert.throws(()=>f.run('activate',root),'同 endpoint 不同 ID 必须拒绝');
    await assertNoEffect('同 endpoint ID 漂移');
    await restoreControl(f,base);
    for (const patch of [{idOverride:'invalid/id'},{idQueryFail:true}]) {
      await f.setDaemonControl(patch);
      assert.throws(()=>f.run('activate',root),'非法或失败的实际 ID 查询必须拒绝');
      await assertNoEffect('ID 查询异常');
      await restoreControl(f,base);
    }
    assert.throws(()=>f.runWith({LF_DOCKER_ENDPOINT:'unix:///other-empty.sock',LF_ENGINE_ID:'clone-daemon-id',DOCKER_HOST:'unix:///other-empty.sock'},'activate',root),
      '继承的 LF 缓存不得覆盖安装绑定');
    await assertNoEffect('内部 LF 缓存');
    assert.deepEqual((await f.daemonState('empty')).resources,[]);
    assert.deepEqual((await f.daemonState('clone')).resources,before,'克隆 daemon 的标签不能替代 ID');
    const token='c'.repeat(32);
    await withProjectLock(root,token,async()=>{
      await f.setDaemonControl({idOverride:'drifted-daemon'});
      assert.throws(()=>execFileSync('sh',[path.join(f.pkg,'lexiflow.sh'),'_project',root,key,token,'verify-record'],{
        encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe'],env:defaultDockerEnv(f.bin),
      }),'delegated entry must reject daemon drift before operation');
      await assertNoEffect('委派前 ID 漂移');
      await restoreControl(f,base);
    });
  } finally { await restoreControl(f,base); }
  const engine=path.join(root,'engine');
  const original=await readFile(engine);
  const link=path.join(root,'engine-link');
  const restoreEngine=async()=>{
    await rm(engine,{force:true});
    await writeFile(engine,original,{mode:0o600});
    await chmod(engine,0o600);
    assert.deepEqual(await readFile(engine),original,'损坏绑定后必须原样恢复 bytes');
    assert.equal((await stat(engine)).mode & 0o777,0o600,'损坏绑定后必须恢复 0600 mode');
  };
  try {
    await unlink(engine);
    assert.throws(()=>f.run('activate',root),'missing binding must not auto-bind');
    await assertNoEffect('缺失 binding');
    await writeFile(link,'not-an-engine');
    await symlink(link,engine);
    assert.throws(()=>f.run('activate',root),'symlink binding must be rejected');
    await assertNoEffect('链接 binding');
    await rm(engine,{force:true});
    await writeFile(engine,`schema=lexiflow-engine-v1\nendpoint=unix:///lexiflow-synthetic-docker.sock\ndaemon_id=other-daemon\n`,{mode:0o600});
    assert.throws(()=>f.run('activate',root));
    await assertNoEffect('篡改 binding');
    await writeFile(engine,`${original.toString()}trailer\n`);
    assert.throws(()=>f.run('activate',root));
    await assertNoEffect('尾字节 binding');
    await writeFile(engine,original);
    await chmod(engine,0o644);
    assert.throws(()=>f.run('activate',root));
    await assertNoEffect('非私有 mode binding');
  } finally { await restoreEngine(); await rm(link,{force:true}); }
  assert.equal(f.run('status',root).trim(),status);
}

async function assertComposeFailure(f,root) {
  const before=structuredClone((await f.daemonState()).resources);
  const beforeActions=(await f.daemonActions()).length;
  const base=await f.daemonControl();
  try {
    await f.setDaemonControl({composeFail:true});
    assert.throws(()=>f.run('activate',root));
    assert.ok((await f.daemonActions()).slice(beforeActions).some((item)=>item.action==='compose-failed'),
      '必须实际触发合成 Compose CLI 非零');
    assert.doesNotMatch(f.run('status',root).trim(),/^idle /u,'CLI 失败不得提交稳定成功');
    const after=(await f.daemonState()).resources;
    const identity=(resources)=>resources.map(({running,health,...item})=>item);
    assert.deepEqual(identity(after),identity(before),'失败恢复不得删除、替换或新建资源');
    assert.ok(after.filter((item)=>item.kind==='container').every((item)=>!item.running),
      '启动失败后必须停止候选容器，而不是把原运行态误当不变');
    assert.match(f.run('status',root).trim(),/^prepared none none [0-9a-f]{64}$/u);
    assert.equal((await f.daemonActions()).slice(beforeActions)
      .filter((item)=>item.action==='compose-up'&&item.resource?.service==='api').length,0,
      '失败的 Compose 命令不能被后置身份成功掩盖并继续启动 API');
  } finally { await restoreControl(f,base); }
}

async function assertDelegatedDrift(first,second,root,firstKey,secondKey) {
  const stableStatus=second.run('status',root).trim();
  assert.equal(stableStatus,`prepared ${firstKey} none ${secondKey}`);
  const beforePrimary=structuredClone((await second.daemonState()).resources);
  const beforeClone=structuredClone((await second.daemonState('clone')).resources);
  const beforeActions=(await second.daemonActions()).length;
  const endpoint='unix:///lexiflow-synthetic-docker.sock';
  const control=await second.daemonControl();
  const token='e'.repeat(32);
  try {
    await withProjectLock(root,token,async()=>{
      for (const [entry,key] of [[first,firstKey],[second,secondKey]]) {
        await restoreControl(second,control);
        await second.setDaemonControl({endpoints:{...control.endpoints},
          switchAfterIdQueries:{remaining:1,endpoint,target:'clone'}});
        assert.throws(()=>execFileSync('sh',[path.join(entry.pkg,'lexiflow.sh'),'_project',root,key,token,'verify-record'],{
          encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe'],env:defaultDockerEnv(entry.bin),
        }),`发行 ${key} 的委派中 ID 切换必须拒绝`);
        const after=await second.daemonControl();
        assert.equal(after.endpoints[endpoint],'clone','切换操作点必须真正触发，而非仅预置失败值');
        assert.equal(second.run('status',root).trim(),stableStatus,'失败不得提交稳定成功 journal');
        assert.deepEqual((await second.daemonState()).resources,beforePrimary);
        assert.deepEqual((await second.daemonState('clone')).resources,beforeClone);
        assert.equal(mutationCount((await second.daemonActions()).slice(beforeActions)),0,'委派中的 ID 漂移不得产生资源变更动作');
      }
    });
  } finally { await restoreControl(second,control); }
  assert.equal(second.run('status',root).trim(),stableStatus,'控制恢复后仍须保留原 journal');
}

test('真实两代入口复用准备状态覆盖身份负例与完整生命周期', async (t) => {
  const first=await lifecycleFixture(t,true);
  const root=first.tmp+'/installation root';
  await assertFirstBindFailures(first);
  first.run('prepare',root);
  const setupActions=await first.daemonActions();
  const firstImport=setupActions.findIndex((item)=>item.action==='private-file-import');
  const privateVolumeCreates=setupActions.filter((item)=>item.action==='volume-create').map((item)=>item.resource.name);
  assert.ok(firstImport>=0,'prepare imports fixed private files');
  assert.equal(privateVolumeCreates.length,3,'all private volumes are explicit owned resources');
  assert.ok(setupActions.slice(0,firstImport).some((item)=>item.action==='volume-create') &&
    setupActions.slice(0,firstImport).filter((item)=>item.action==='volume-create').length===3,
  'all private volumes must be created/owned before the first data writer');
  const engine = await readFile(path.join(root,'engine'));
  assert.match(engine.toString(),/^schema=lexiflow-engine-v1\nendpoint=unix:\/\/\/lexiflow-synthetic-docker.sock\ndaemon_id=synthetic-daemon-id\n$/u);
  assert.match(first.run('status',root).trim(),/^prepared none none [0-9a-f]{64}$/u);
  const firstKey=first.run('status',root).trim().split(' ')[3];
  await assertInputAndReleaseKey(first,root);
  await assertBindingFailures(first,root);
  await assertComposeFailure(first,root);
  first.run('activate',root);
  assert.match(first.run('status',root).trim(),/^idle [0-9a-f]{64} none none$/u);
  const second=await lifecycleFixture(t,true,'second');
  second.run('prepare',root);
  const secondKey=second.run('status',root).trim().split(' ')[3];
  await assertDelegatedDrift(first,second,root,firstKey,secondKey);
  second.run('activate',root);
  const switched=second.run('status',root).trim().split(' ');
  assert.deepEqual(switched,['idle',secondKey,firstKey,'none']);
  second.run('recover',root);
  assert.equal(second.run('status',root).trim(),`idle ${firstKey} ${secondKey} none`);
  const beforeStop=await second.daemonState();
  const firstResourcesBefore=beforeStop.resources.filter((item)=>item.release===firstKey);
  const secondResourcesBefore=beforeStop.resources.filter((item)=>item.release===secondKey);
  const hasService=(resources,release,service)=>resources.some((item)=>item.kind==='container'&&item.name===`lf_${item.installation}_${release.slice(0,16)}-${service}-1`);
  for (const [resources,key,label] of [[firstResourcesBefore,firstKey,'第一代'],[secondResourcesBefore,secondKey,'第二代']]) {
    assert.ok(hasService(resources,key,'postgres'),`${label}发行 PostgreSQL 资源应存在`);
    assert.ok(hasService(resources,key,'api'),`${label}发行 API 资源应存在`);
  }
  const firstContainersBefore=firstResourcesBefore.filter((item)=>item.kind==='container');
  const secondContainersBefore=secondResourcesBefore.filter((item)=>item.kind==='container');
  assert.equal(firstContainersBefore.length,2,'活动第一代应有 PostgreSQL 与 API 容器');
  assert.equal(secondContainersBefore.length,2,'已恢复的第二代应保留 PostgreSQL 与 API 容器');
  assert.ok(firstContainersBefore.every((item)=>item.running),'恢复后活动第一代容器应处于运行状态');
  assert.ok(secondContainersBefore.every((item)=>!item.running),'恢复后非活动第二代容器应停止');
  const actionCountBeforeStop=(await second.daemonActions()).length;
  second.run('stop',root);
  assert.match(second.run('status',root).trim(),/^stopped /u);
  const afterStop=await second.daemonState();
  const firstResourcesAfterStop=afterStop.resources.filter((item)=>item.release===firstKey);
  const secondResourcesAfterStop=afterStop.resources.filter((item)=>item.release===secondKey);
  assert.deepEqual(firstResourcesAfterStop.map(({kind,id,name})=>({kind,id,name})),
    firstResourcesBefore.map(({kind,id,name})=>({kind,id,name})),'停止活动第一代不得删除资源');
  assert.ok(firstResourcesAfterStop.filter((item)=>item.kind==='container').every((item)=>!item.running),
    '停止应将活动第一代容器置为非运行');
  assert.deepEqual(secondResourcesAfterStop,secondResourcesBefore,'停止第一代不得改变第二代资源');
  const stopActions=(await second.daemonActions()).slice(actionCountBeforeStop)
    .filter((item)=>item.action==='stop').map((item)=>item.resource.id);
  assert.deepEqual(new Set(stopActions),new Set(firstContainersBefore.map((item)=>item.id)),'stop 仅操作活动第一代容器');
  second.run('delete',root,firstKey,'--confirm-delete-data');
  assert.equal(second.run('status',root).trim(),`stopped none ${secondKey} none`);
  const afterDelete=await second.daemonState();
  assert.equal(afterDelete.resources.some((item)=>item.release===firstKey),false,'删除只移除指定的第一代发行资源');
  assert.deepEqual(afterDelete.resources.filter((item)=>item.release===secondKey),secondResourcesAfterStop,
    '删除第一代必须保留第二代资源');
  const removed=await second.daemonActions();
  const installation=firstResourcesBefore[0].installation;
  assert.ok(removed.some((item)=>item.action==='remove'&&item.resource?.project===`lf_${installation}_${firstKey.slice(0,16)}`),
    '动作台账应记录目标发行的资源移除');
});


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
  const fields=['linux','aarch64','synthetic-host','true','present','0,501,1;1,100000,65536;','0,20,1;1,100000,65536;','overlay','/var/lib/containers/storage','/run/containers/storage','/var/lib/containers/storage/volumes','true','5.6.2'];
  for(const [index,value] of Object.entries(patch)) fields[Number(index)]=value;
  return fields.join('|')+'\n';
};
const podmanComponents=['Podman Engine','Conmon','OCI Runtime','Engine'];

test('Podman 身份使用显式 native host/store tuple 而非随机兼容 ID',async(t)=>{
  const f=await lifecycleFixture(t,true);
  const root=path.join(f.tmp,'podman native install');
  const base=await f.daemonControl();
  await f.setDaemonControl({serverComponents:podmanComponents,idOverride:'random-compat-one'});
  await f.setPodmanInfo(podmanTuple());
  const nativeProbe=spawnSync(path.join(f.bin,'podman'),['--url','unix:///lexiflow-synthetic-docker.sock','info','--format','probe'],{encoding:'utf8'});
  assert.deepEqual({status:nativeProbe.status,stdout:nativeProbe.stdout,stderr:nativeProbe.stderr},{status:0,stdout:podmanTuple(),stderr:''});
  f.run('prepare',root);
  const engine=await readFile(path.join(root,'engine'));
  assert.match(engine.toString(),/^schema=lexiflow-engine-v1\nendpoint=unix:\/\/\/lexiflow-synthetic-docker.sock\ndaemon_id=podman:[0-9a-f]{64}\n$/u);
  const key=f.run('status',root).trim().split(' ')[3];
  const stableStatus=`prepared none none ${key}`;
  const token='f'.repeat(32);
  const before=structuredClone((await f.daemonState()).resources);
  const beforeActions=(await f.daemonActions()).length;
  await withProjectLock(root,token,async()=>{
    await f.setDaemonControl({idOverride:'random-compat-two'});
    assert.equal(execFileSync('sh',[path.join(f.pkg,'lexiflow.sh'),'_project',root,key,token,'verify-record'],{
      encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe'],env:defaultDockerEnv(f.bin),
    }), '','随机变化的兼容 ID 不得改变稳定 native 身份');
    assert.deepEqual(await readFile(path.join(root,'engine')),engine);
    for(const [label,patch] of [
      ['hostname',[2,'changed-host']],
      ['store graphRoot',[8,'/other/storage']],
      ['UID mappings',[5,'0,501,1;1,200000,65536;']],
      ['UID mapping overflow',[5,'999999999999999999999,1,1;']],
      ['UID mapping extent overflow',[5,'4294967296,1,1;']],
      ['UID mapping size extent overflow',[5,'4294967295,0,2;']],
      ['malformed version',[12,'1.bad']],
      ['short version',[12,'5.6']],
    ]) {
      await f.setPodmanInfo(podmanTuple({[patch[0]]:patch[1]}));
      assert.throws(()=>execFileSync('sh',[path.join(f.pkg,'lexiflow.sh'),'_project',root,key,token,'verify-record'],{
        encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe'],env:defaultDockerEnv(f.bin),
      }),`${label} drift must reject delegated check`);
      assert.equal(f.run('status',root).trim(),stableStatus,`${label} drift不得提交状态`);
      assert.deepEqual(await f.daemonState().then((state)=>state.resources),before,`${label} drift不得改变资源`);
      assert.equal(mutationCount((await f.daemonActions()).slice(beforeActions)),0,`${label} drift不得触发后续资源变更`);
    }
  });
  await f.setDaemonControl(base);
  await f.setPodmanInfo(podmanTuple());
});

test('Podman engine 分类歧义、缺少 native 能力或非法 mappings 均不得发布绑定',async(t)=>{
  const f=await lifecycleFixture(t,true);
  const cases=[
    {label:'unknown engine',components:['Conmon','OCI Runtime']},
    {label:'unknown primary engine alongside Engine',components:['Engine','Future Engine']},
    {label:'duplicate Podman Engine',components:['Podman Engine','Podman Engine']},
    {label:'duplicate Docker Engine',components:['Engine','Engine']},
    {label:'mixed engine kinds',components:['Podman Engine','Docker Engine']},
    {label:'native CLI unavailable',components:podmanComponents,native:'__FAIL__\n'},
    {label:'ID mappings object missing',components:podmanComponents,native:podmanTuple({4:'missing'})},
    {label:'rootless mappings absent',components:podmanComponents,native:podmanTuple({5:'-',6:'-'})},
    {label:'malformed UID mapping',components:podmanComponents,native:podmanTuple({5:'0,501,x;'})},
  ];
  for(const [index,item] of cases.entries()) {
    const root=path.join(f.tmp,`rejected engine ${index}`);
    await f.setDaemonControl({serverComponents:item.components});
    await f.setPodmanInfo(item.native??podmanTuple());
    assert.throws(()=>f.run('prepare',root),`${item.label} must fail closed`);
    assert.equal(await readFile(path.join(root,'engine')).then(()=>true,()=>false),false,`${item.label} must not publish engine binding`);
    assert.equal(f.run('status',root).trim(),'idle none none none',`${item.label} must not commit transition state`);
    assert.deepEqual((await f.daemonState()).resources,[],`${item.label} must not mutate resources`);
  }
});

test('Podman mappings 接受精确 2**32 边界并拒绝无效哈希输出',async(t)=>{
  const f=await lifecycleFixture(t,true);
  const root=path.join(f.tmp,'podman boundary install');
  await f.setDaemonControl({serverComponents:podmanComponents});
  await f.setPodmanInfo(podmanTuple({5:'4294967295,0,1;'}));
  f.run('prepare',root);
  const identitySource=await readFile(new URL('../runtime/release-installation-docker.sh',import.meta.url),'utf8');
  const identityProbe=()=>spawnSync('sh',['-c',`${identitySource}\nlf_podman_identity_query "$1"`,'identity-probe','unix:///lexiflow-synthetic-docker.sock'],{
    encoding:'utf8',timeout:10000,env:defaultDockerEnv(f.bin),
  });
  for(const output of ["printf '%064d  unexpected\\n' 0","printf '%064d  -\\n%064d  -\\n' 0 0"]){
    await writeFile(path.join(f.bin,'sha256sum'),`#!/bin/sh\n${output}\n`,{mode:0o755});
    assert.equal(identityProbe().status,1,'native identity must reject malformed or multiple hash records');
  }
  await writeFile(path.join(f.bin,'shasum'),"#!/bin/sh\nprintf '%064d  unexpected\\n' 0\n",{mode:0o755});
  const rejected=path.join(f.tmp,'podman invalid hash install');
  assert.throws(()=>f.run('prepare',rejected),'hash output must have the exact sha256sum second field');
  assert.equal(await readFile(path.join(rejected,'engine')).then(()=>true,()=>false),false);
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
