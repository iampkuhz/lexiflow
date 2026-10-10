import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { runCommand } from '../execution/deployment-process-control.mjs';

async function assertGroupGone(pgid, timeout = 5000) {
  const deadline = performance.now() + timeout;
  let lastState = 'alive';
  do {
    try { process.kill(-pgid, 0); lastState = 'alive'; }
    catch (error) {
      if (error.code === 'ESRCH') return;
      if (error.code !== 'EPERM') throw error;
      // EPERM 不能证明已退出；与生产探测一致，继续等真正 ESRCH。
      lastState = 'EPERM';
    }
    await sleep(20);
  } while (performance.now() < deadline);
  assert.fail(`barrier group must become ESRCH before deadline; last probe: ${lastState}`);
}

test('persisted child PID remains the PID executing the released command', async () => {
  let recorded;
  const output = await runCommand(process.execPath, ['-e', 'process.stdout.write(String(process.pid))'], {
    capture: true, timeout: 5000, onStart: pid => { recorded = pid; },
  });
  assert.equal(output, String(recorded));
});

test('failed owner persistence never launches the requested external action', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lf-barrier-'));
  try {
    const marker = path.join(root, 'must-not-run');
    await assert.rejects(runCommand(process.execPath, ['-e', `require('fs').writeFileSync(${JSON.stringify(marker)},'ran')`], {
      timeout: 5000, onStart: () => { throw new Error('synthetic-persist-failure'); },
    }), /无法记录子进程归属/);
    assert.equal(fs.existsSync(marker), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('SIGKILL before owner persistence closes the barrier without executing the command', { timeout: 15000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lf-barrier-crash-'));
  const record = path.join(root, 'wrapper-pid'), marker = path.join(root, 'must-not-run');
  const commandModule = pathToFileURL(path.resolve(import.meta.dirname, '../execution/deployment-process-control.mjs')).href;
  const code = `import fs from 'node:fs'; import {runCommand} from ${JSON.stringify(commandModule)};
await runCommand(process.execPath,['-e',${JSON.stringify(`require('fs').writeFileSync(${JSON.stringify(marker)},'ran')`)}],{
 timeout:5000,onStart:pid=>{fs.writeFileSync(${JSON.stringify(record)},String(pid));process.kill(process.pid,'SIGKILL');}});`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', code], { stdio: 'ignore' });
  const done = new Promise(resolve => { child.once('error', error => resolve({ error })); child.once('exit', (exit, signal) => resolve({ exit, signal })); });
  try {
    assert.equal((await done).signal, 'SIGKILL');
    const wrapper = Number(fs.readFileSync(record, 'utf8'));
    assert.ok(Number.isSafeInteger(wrapper) && wrapper > 1);
    await assertGroupGone(wrapper);
    assert.equal(fs.existsSync(marker), false);
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await done;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('group probe keeps transient EPERM unknown and rejects persistent EPERM or live owned groups', { timeout: 10000 }, async (t) => {
  const child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { detached: true, stdio: 'ignore' });
  const done = new Promise(resolve => child.once('close', resolve));
  await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
  const kill = process.kill;
  try {
    await assert.rejects(assertGroupGone(child.pid, 80), /last probe: alive/);
    assert.equal(kill(-child.pid, 0), true, 'live group timeout must not kill the owned process');
    let probes = 0;
    t.mock.method(process, 'kill', function(pid, signal) {
      if (pid === -child.pid && signal === 0) {
        probes++;
        const error = new Error('synthetic probe permission failure'); error.code = 'EPERM'; throw error;
      }
      return kill.call(this, pid, signal);
    });
    await assert.rejects(assertGroupGone(child.pid, 80), /last probe: EPERM/);
    assert.ok(probes >= 2, 'unknown state is probed again, never accepted as gone');
    assert.equal(kill(-child.pid, 0), true, 'EPERM timeout must not kill the owned process');
    t.mock.restoreAll();
    child.kill('SIGKILL');
    await done;
    probes = 0;
    t.mock.method(process, 'kill', function(pid, signal) {
      if (pid === -child.pid && signal === 0 && probes++ === 0) {
        const error = new Error('synthetic transient permission failure'); error.code = 'EPERM'; throw error;
      }
      return kill.call(this, pid, signal);
    });
    await assertGroupGone(child.pid);
    assert.ok(probes >= 2, 'success requires a later real ESRCH after the transient EPERM');
  } finally {
    t.mock.restoreAll();
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await done;
  }
});

test('nonzero captured machine output stays separate from the safe error message', async () => {
  await assert.rejects(runCommand(process.execPath, ['-e',
    'process.stdout.write(JSON.stringify({status:"BLOCKED",reason:"AUTHORIZATION_FAILED"})); process.stderr.write("private-error"); process.exitCode=3;'],
  { capture: true, timeout: 5000 }), error => {
    assert.equal(error.commandExitCode, 3);
    assert.deepEqual(JSON.parse(error.stdout), { status: 'BLOCKED', reason: 'AUTHORIZATION_FAILED' });
    assert.doesNotMatch(error.message, /private-error|AUTHORIZATION_FAILED/);
    return true;
  });
});
