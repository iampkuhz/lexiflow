import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile, unlink, rmdir, readFile, chmod, symlink, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { lifecycleFixture } from './lifecycle-fixture.mjs';

// 原六个 case 分别由下列五个具名断言阶段及主 test 的两代闭环承载；
// 不使用 nested test，TAP 仅有一个顶层 case。
const mutatingActions=new Set(['compose-up','compose-run','stop','remove','image-load']);
const mutationCount=(actions)=>actions.filter((item)=>mutatingActions.has(item.action)).length;
const defaultDockerEnv=(bin)=>({...process.env,PATH:`${bin}:${process.env.PATH}`,DOCKER_HOST:'',DOCKER_CONTEXT:'',DOCKER_CONFIG:''});
const restoreControl=async (f,base)=>f.setDaemonControl({...base,idOverride:null,idQueryFail:false,composeFail:false,switchAfterIdQueries:null});

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
