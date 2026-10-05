import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { acquireProcessLock } from '../process-lock.mjs';

const modulePath = path.resolve(import.meta.dirname, '..', 'process-lock.mjs');
const helper = String.raw`import { pathToFileURL } from 'node:url';
const { acquireProcessLock } = await import(pathToFileURL(process.argv[1]));
const release=acquireProcessLock(process.argv[2]);
if(process.argv[3]==='hold') { process.stdout.write('locked\n'); setInterval(()=>{},1000); }
else { release(); process.stdout.write('released\n'); }
`;
const argv = (directory, mode) => ['--input-type=module', '-e', helper, modulePath, directory, mode];

test('parent retains helper-acquired lock; release and SIGKILL allow reacquisition of same inode', { timeout: 15000 }, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-process-lock-'));
  let unlock, killed, finished;
  try {
    unlock = acquireProcessLock(directory);
    const guard = path.join(directory, '.lock-guard');
    const inode = fs.statSync(guard);
    // acquireProcessLock 的系统 helper 已退出；另一个进程仍必须被父进程的 FD 排除。
    const contender = spawnSync(process.execPath, argv(directory, 'release'), { encoding: 'utf8', timeout: 5000 });
    assert.equal(contender.error, undefined);
    assert.notEqual(contender.status, 0);
    assert.match(contender.stderr, /INSTALL_LOCK_BUSY/);
    assert.throws(() => acquireProcessLock(directory), /INSTALL_LOCK_BUSY/);
    unlock(); unlock(); unlock = undefined;
    const released = spawnSync(process.execPath, argv(directory, 'release'), { encoding: 'utf8', timeout: 5000 });
    assert.equal(released.status, 0, released.stderr);
    assert.equal(released.stdout, 'released\n');
    assert.deepEqual([fs.statSync(guard).dev, fs.statSync(guard).ino], [inode.dev, inode.ino]);

    killed = spawn(process.execPath, argv(directory, 'hold'), { stdio: ['ignore', 'pipe', 'pipe'] });
    finished = new Promise(resolve => { killed.once('error', error => resolve({ error })); killed.once('close', (code, signal) => resolve({ code, signal })); });
    await new Promise((resolve, reject) => {
      let data = '';
      const timer = setTimeout(() => reject(new Error('child readiness timed out')), 5000);
      killed.stdout.on('data', chunk => { data += chunk; if (data.includes('\n')) { clearTimeout(timer); resolve(); } });
      finished.then(result => { clearTimeout(timer); reject(new Error(`child exited before readiness: ${JSON.stringify(result)}`)); });
    });
    assert.throws(() => acquireProcessLock(directory), /INSTALL_LOCK_BUSY/);
    killed.kill('SIGKILL');
    assert.equal((await finished).signal, 'SIGKILL');
    unlock = acquireProcessLock(directory);
    assert.deepEqual([fs.statSync(guard).dev, fs.statSync(guard).ino], [inode.dev, inode.ino]);
  } finally {
    if (killed && killed.exitCode === null && killed.signalCode === null) killed.kill('SIGKILL');
    if (finished) await finished;
    unlock?.();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('guard rejects symlink, hardlink and publicly accessible inode', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-process-lock-invalid-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const outside = path.join(directory, 'outside');
  fs.writeFileSync(outside, ''); fs.chmodSync(outside, 0o600);
  fs.symlinkSync(outside, path.join(directory, '.lock-guard'));
  assert.throws(() => acquireProcessLock(directory));
  fs.unlinkSync(path.join(directory, '.lock-guard'));
  fs.linkSync(outside, path.join(directory, '.lock-guard'));
  assert.throws(() => acquireProcessLock(directory), /INSTALL_LOCK_GUARD_INVALID/);
  fs.unlinkSync(path.join(directory, '.lock-guard'));
  fs.unlinkSync(outside);
  fs.writeFileSync(path.join(directory, '.lock-guard'), ''); fs.chmodSync(path.join(directory, '.lock-guard'), 0o644);
  assert.throws(() => acquireProcessLock(directory), /INSTALL_LOCK_GUARD_INVALID/);
});
