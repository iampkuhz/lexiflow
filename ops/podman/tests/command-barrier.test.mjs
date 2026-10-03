import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { runCommand } from '../command.mjs';

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
  const commandModule = pathToFileURL(path.resolve(import.meta.dirname, '../command.mjs')).href;
  const code = `import fs from 'node:fs'; import {runCommand} from ${JSON.stringify(commandModule)};
await runCommand(process.execPath,['-e',${JSON.stringify(`require('fs').writeFileSync(${JSON.stringify(marker)},'ran')`)}],{
 timeout:5000,onStart:pid=>{fs.writeFileSync(${JSON.stringify(record)},String(pid));process.kill(process.pid,'SIGKILL');}});`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', code], { stdio: 'ignore' });
  const done = new Promise(resolve => { child.once('error', error => resolve({ error })); child.once('exit', (exit, signal) => resolve({ exit, signal })); });
  try {
    assert.equal((await done).signal, 'SIGKILL');
    const wrapper = Number(fs.readFileSync(record, 'utf8'));
    assert.ok(Number.isSafeInteger(wrapper) && wrapper > 1);
    const deadline = Date.now() + 5000;
    let alive = true;
    while (Date.now() < deadline) {
      try { process.kill(-wrapper, 0); }
      catch (error) { if (error.code !== 'ESRCH') throw error; alive = false; break; }
      await sleep(20);
    }
    assert.equal(alive, false, 'barrier must exit on EOF after parent SIGKILL');
    assert.equal(fs.existsSync(marker), false);
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await done;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
