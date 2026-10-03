import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import childProcess from 'node:child_process';
import { mock } from 'node:test';
import { syncBuiltinESMExports } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runLocal } from '../local.mjs';
import { doctor } from '../doctor.mjs';

const cli = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../local.mjs');
const quiet = async callback => {
  const original = console.log;
  const errors = console.error;
  let output = '';
  console.log = console.error = (...parts) => { output += `${parts.join(' ')}\n`; };
  try { return { value: await callback(), output }; }
  finally { console.log = original; console.error = errors; }
};

test('library import and invocation leave argv, exitCode and signal listeners to the caller', () => {
  const script = String.raw`
    import assert from 'node:assert/strict';
    import fs from 'node:fs'; import cp from 'node:child_process';
    import { syncBuiltinESMExports } from 'node:module';
    const argv = [...process.argv]; const exitCode = 17;
    process.exitCode = exitCode;
    const before = ['SIGINT','SIGTERM'].map(signal => process.listeners(signal));
    const writes = [];
    for (const name of ['writeFileSync','appendFileSync','mkdirSync','renameSync','unlinkSync','rmSync','rmdirSync','copyFileSync','cpSync','createWriteStream'])
      fs[name] = (...args) => { writes.push('fs.' + name); throw new Error('unexpected filesystem write'); };
    for (const name of ['spawn','spawnSync','exec','execSync','execFile','execFileSync'])
      cp[name] = (...args) => { writes.push('child_process.' + name); throw new Error('unexpected command'); };
    syncBuiltinESMExports();
    const { runLocal } = await import(${JSON.stringify(cli)});
    assert.deepEqual(process.argv, argv);
    assert.equal(process.exitCode, exitCode);
    assert.deepEqual(['SIGINT','SIGTERM'].map(signal => process.listeners(signal)), before);
    assert.deepEqual(writes, []);
    process.exitCode = 0;
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});

test('each call copies argv and help/invalid/help does not consume or contaminate later input', async () => {
  const previousExitCode = process.exitCode;
  const listeners = ['SIGINT', 'SIGTERM'].map(signal => process.listeners(signal));
  const argv = Object.freeze(['help']);
  const before = [...argv];
  const first = await quiet(() => runLocal(argv));
  assert.equal(first.value, 0);
  assert.match(first.output, /用法:/);
  assert.deepEqual(argv, before);
  const invalid = await quiet(() => runLocal(['not-a-command']));
  assert.equal(invalid.value, 1);
  assert.match(invalid.output, /用法:/);
  const last = await quiet(() => runLocal(['--help']));
  assert.equal(last.value, 0);
  assert.match(last.output, /用法:/);
  assert.equal((await quiet(() => runLocal(null))).value, 1);
  assert.equal((await quiet(() => runLocal(['help', 3]))).value, 1);
  assert.equal((await quiet(() => runLocal(['help']))).value, 0);
  assert.equal(process.exitCode, previousExitCode);
  assert.deepEqual(['SIGINT', 'SIGTERM'].map(signal => process.listeners(signal)), listeners);
});

test('same module rejects concurrent sessions during real async version lookup and releases signal listeners', () => {
  const preload = String.raw`
    import https from 'node:https'; import { EventEmitter } from 'node:events';
    let enter; globalThis.requestEntered = new Promise(resolve => { enter = resolve; });
    https.get = (_url, _options, callback) => {
      const request = new EventEmitter(); request.destroy = () => {};
      enter();
      globalThis.releaseRequest = () => { const response = new EventEmitter(); response.statusCode = 404; response.destroy = () => {}; callback(response); request.emit('close'); };
      return request;
    };
  `;
  const script = String.raw`
    import assert from 'node:assert/strict';
    import os from 'node:os'; import path from 'node:path'; import fs from 'node:fs';
    const { runLocal } = await import(${JSON.stringify(cli)});
    process.exitCode = 23;
    const before = ['SIGINT','SIGTERM'].map(signal => process.listeners(signal).length);
    const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-session-'));
    const dir = path.join(root, 'not-installed');
    const first = runLocal(['version', '--dir', dir]);
    await globalThis.requestEntered;
    assert.deepEqual(['SIGINT','SIGTERM'].map(signal => process.listeners(signal).length), before.map(count => count + 1));
    assert.equal(process.exitCode, 23);
    process.exitCode = 24;
    assert.equal(await runLocal(['help']), 1);
    assert.equal(process.exitCode, 24);
    assert.equal(await runLocal(['invalid-action']), 1);
    assert.equal(process.exitCode, 24);
    process.emit('SIGTERM');
    globalThis.releaseRequest();
    assert.equal(await first, 0);
    assert.equal(process.exitCode, 24);
    assert.deepEqual(['SIGINT','SIGTERM'].map(signal => process.listeners(signal).length), before);
    assert.equal(await runLocal(['help']), 0);
    assert.equal(process.exitCode, 24);
    fs.rmdirSync(root);
    process.exitCode = 0;
  `;
  const result = spawnSync(process.execPath, ['--import', `data:text/javascript,${encodeURIComponent(preload)}`, '--input-type=module', '-e', script], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /拒绝并发调用/);
});

test('doctor returns CLI status without changing the host process exitCode', async t => {
  const spawnMock = mock.method(childProcess, 'spawnSync', () => ({ error: new Error('not available'), status: null, stdout: '', stderr: '' }));
  syncBuiltinESMExports();
  t.after(() => { spawnMock.mock.restore(); syncBuiltinESMExports(); });
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-doctor-session-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const previous = process.exitCode;
  process.exitCode = 29;
  try {
    const result = await quiet(() => doctor(path.join(root, 'not-installed'), {}, false));
    assert.equal(result.value, 3);
    assert.equal(spawnMock.mock.callCount() > 0, true);
    assert.equal(process.exitCode, 29);
  } finally { process.exitCode = previous; }
});

test('CLI help succeeds and invalid action fails with the existing usage error', () => {
  const help = spawnSync(process.execPath, [cli, '--help'], { encoding: 'utf8' });
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /用法:/);
  const invalid = spawnSync(process.execPath, [cli, 'not-a-command'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /用法:/);
});
