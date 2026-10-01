import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir, rm, unlink, lstat, readdir, readlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { lifecycleFixture } from './lifecycle-fixture.mjs';

test('发行入口内嵌安装状态适配器并创建仅私有 owner/state', async (t) => {
  const f=await lifecycleFixture(t,'adapter');
  const root=path.join(f.tmp,'new installation');
  const value=f.run('_fixture_init',root).trim();
  assert.match(value,/^[0-9a-f]{32}\|idle\|none$/u);
  const state=await readFile(path.join(root,'state'),'utf8');
  assert.equal(state.split('\n').length,9);
  assert.match(state,/schema=lexiflow-installation-v1\ninstallation_id=[0-9a-f]{32}\nphase=idle/u);
  assert.equal((await (await import('node:fs/promises')).stat(path.join(root,'owner'))).mode & 0o777,0o600);
  assert.equal((await (await import('node:fs/promises')).stat(path.join(root,'state'))).mode & 0o777,0o600);
  assert.equal(await f.run('_fixture_load',root), 'idle|none|none\n');
});

async function rootSnapshot(root) {
  const entries=[];
  async function visit(relative) {
    const file=relative ? path.join(root,relative) : root;
    const info=await lstat(file);
    const entry={path:relative || '.',mode:info.mode & 0o777,
      type:info.isDirectory()?'directory':info.isFile()?'file':info.isSymbolicLink()?'symlink':'other'};
    if (info.isFile()) entry.bytes=(await readFile(file)).toString('base64');
    if (info.isSymbolicLink()) entry.target=await readlink(file);
    entries.push(entry);
    if (info.isDirectory()) for (const name of (await readdir(file)).sort()) {
      await visit(relative ? path.join(relative,name) : name);
    }
  }
  await visit('');
  return entries;
}

function failedRun(run,...args) {
  try { run(...args); } catch (error) { return error; }
  assert.fail('expected lifecycle command to fail');
}

test('prepare 对未确认的已有安装根失败关闭且不触碰 Docker 或根目录', async (t) => {
  const generated=await lifecycleFixture(t,'owner-init-fault');
  const adapter=await lifecycleFixture(t,'adapter');
  const roots=[];

  // This fixture overrides only random generation in the generated entry;
  // lf_owner_init itself creates the root before the deterministic failure.
  const interrupted=path.join(generated.tmp,'interrupted root');
  const interruptedError=failedRun(generated.run,'prepare',interrupted);
  assert.equal(interruptedError.status,1);
  assert.equal((await lstat(interrupted)).isDirectory(),true,'中断点应留下真实空目录');
  assert.deepEqual(await readdir(interrupted),[]);
  roots.push([generated,interrupted]);

  const partial=path.join(adapter.tmp,'partial owner');
  await mkdir(partial,{mode:0o700});
  await writeFile(path.join(partial,'owner'),'a'.repeat(32),{mode:0o600});
  roots.push([generated,partial]);

  const missingState=path.join(adapter.tmp,'missing state');
  adapter.run('_fixture_init',missingState);
  await unlink(path.join(missingState,'state'));
  roots.push([generated,missingState]);

  const noEngine=path.join(adapter.tmp,'missing engine');
  adapter.run('_fixture_init',noEngine);
  roots.push([generated,noEngine]);

  const unknown=path.join(adapter.tmp,'unknown file');
  await mkdir(unknown,{mode:0o700});
  await writeFile(path.join(unknown,'notes'),'synthetic unknown bytes\n',{mode:0o600});
  roots.push([generated,unknown]);

  const beforeActions=await generated.daemonActions();
  for (const [fixture,root] of roots) {
    const before=await rootSnapshot(root);
    const error=failedRun(fixture.run,'prepare',root);
    assert.equal(error.status,1,`非零退出: ${root}`);
    assert.equal(error.stderr,'LF_INSTALLATION_UNVERIFIED\n',`固定诊断: ${root}`);
    assert.equal(error.stdout,'',`不得输出未过滤诊断: ${root}`);
    assert.deepEqual(await rootSnapshot(root),before,`根目录条目、内容和权限不变: ${root}`);
  }
  assert.deepEqual(await generated.daemonActions(),beforeActions,'失败诊断期间不得新增 Docker 调用');

  const alias=path.join(adapter.tmp,'root alias');
  const aliasTarget=path.join(adapter.tmp,'alias target');
  await mkdir(aliasTarget,{mode:0o700});
  await (await import('node:fs/promises')).symlink(aliasTarget,alias);
  const invalid=failedRun(generated.run,'prepare',alias);
  assert.equal(invalid.status,1);
  assert.equal(invalid.stderr,'LF_ROOT_INVALID\n','路径非法优先于安装状态诊断');
  assert.deepEqual(await generated.daemonActions(),beforeActions);
});

test('已有安装状态拒绝额外字段、损坏 owner 与符号链接接管', async (t) => {
  const f=await lifecycleFixture(t,'adapter'); const root=path.join(f.tmp,'installation');
  f.run('_fixture_init',root);
  const stateFile=path.join(root,'state'); const original=await readFile(stateFile,'utf8');
  await writeFile(stateFile,`${original}surprise=x\n`);
  assert.throws(()=>f.run('_fixture_load',root));
  await writeFile(stateFile,original);
  await writeFile(path.join(root,'owner'),'f'.repeat(32));
  assert.throws(()=>f.run('_fixture_load',root));
  const alias=path.join(f.tmp,'alias'); await (await import('node:fs/promises')).symlink(root,alias);
  assert.throws(()=>f.run('_fixture_load',alias));
});

test('真实 Docker 生命周期入口缺受控 lease 时显式 BLOCKED', async () => {
  const script=path.resolve('ops/docker/tests/update-recovery.sh');
  const { spawnSync }=await import('node:child_process');
  const result=spawnSync('bash',[script],{encoding:'utf8',env:{...process.env,LEXIFLOW_LIFECYCLE_LEASE:''}});
  assert.equal(result.status,3);
  assert.equal(result.stderr,'');
  assert.deepEqual(JSON.parse(result.stdout), {
    status:'BLOCKED', checks_run:0, failures:0, errors:0, skipped:0,
    reason:'LIFECYCLE_LEASE_UNAVAILABLE',
  });
});
