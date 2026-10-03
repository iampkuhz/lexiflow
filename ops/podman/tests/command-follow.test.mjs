import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { runCommand } from '../command.mjs';

const moduleUrl = new URL('../command.mjs', import.meta.url).href;

function viewer(code, options = {}) {
  const script = `import {runCommand} from ${JSON.stringify(moduleUrl)};
try { await runCommand(process.execPath,['-e',${JSON.stringify(code)}],${JSON.stringify({ follow: true, ...options })}); }
catch(error) { console.error(error.message); process.exitCode=1; }`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', script], { stdio: ['ignore', 'pipe', 'pipe'] });
  let bytes = 0, stderr = '';
  child.stdout.on('data', chunk => { bytes += chunk.length; });
  child.stderr.on('data', chunk => { stderr += chunk; });
  const done = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal, bytes, stderr }));
  });
  return { child, done };
}

test('follow does not retain output or apply build duration/total-output limits', { timeout: 15000 }, async () => {
  const { done } = viewer(`const fs=require('fs'); setTimeout(()=>{for(let i=0;i<25;i++) fs.writeSync(1,Buffer.alloc(1024*1024,120)); fs.writeSync(2,'synthetic-stderr');},50);`, { timeout: 1 });
  const result = await done;
  assert.equal(result.code, 0, result.stderr);
  assert.equal(result.bytes, 25 * 1024 * 1024);
  assert.equal(result.stderr, 'synthetic-stderr');
});

test('follow refuses capture and operation-file persistence', async () => {
  for (const options of [{ capture: true }, { logFile: '/must-not-write' }]) {
    await assert.rejects(runCommand(process.execPath, ['-e', 'process.exit(99)'], { follow: true, ...options }), /不能同时捕获或保存/);
  }
});

test('ordinary commands retain their duration limit', { timeout: 10000 }, async () => {
  await assert.rejects(runCommand(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeout: 50 }), /时限/);
});

test('follow reports command startup failure', { timeout: 10000 }, async () => {
  await assert.rejects(runCommand('/lexiflow-synthetic-missing-command', [], { follow: true }), /无法启动/);
});
