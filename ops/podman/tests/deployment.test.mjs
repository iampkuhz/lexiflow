import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createDeploymentPlan, deploymentPlanHash, confirmDeploymentPlan, confirmDeploymentIntent, writeOperationJournal, readOperationJournal, executeDeployment } from '../installation/installation-plan-records.mjs';

const hex = 'a'.repeat(64);
const plan = extra => createDeploymentPlan({ operation: 'install', installation: 'local-x', targetBuildId: hex,
  generationVersion: 'b'.repeat(64), databaseFingerprint: 'c'.repeat(64),
  databaseTarget: { kind: 'file', file: '/tmp/lexiflow/data/lexicon.db' },
  applicationUpdate: true, applicationStart: true, applicationReason: 'BUILD_CHANGED',
  dataRebuild: false, dataReason: 'OK', ...extra });

test('deployment plan hash is canonical and excludes credentials and volatile values', () => {
  const first = plan({ credential: 'secret', at: 'one' });
  const second = plan({ at: 'two', credential: 'other' });
  assert.deepEqual(first, second);
  assert.equal(deploymentPlanHash(first), deploymentPlanHash(second));
  assert.notEqual(deploymentPlanHash(first), deploymentPlanHash(plan({ dataRebuild: true })));
  assert.notEqual(deploymentPlanHash(first), deploymentPlanHash(plan({ databaseTarget: { kind: 'file', file: '/tmp/other/data/lexicon.db' } })));
  assert.equal(plan({ dataRebuild: true, dataReason: 'DATABASE_CORRUPT' }).actions.dataReason, 'DATABASE_CORRUPT');
});

test('confirmation rejects mismatch and non-interactive implicit consent', async () => {
  const hash = deploymentPlanHash(plan());
  await assert.rejects(confirmDeploymentPlan(hash, { suppliedHash: 'd'.repeat(64) }), /DEPLOYMENT_PLAN_CHANGED/);
  await assert.rejects(confirmDeploymentPlan(hash, { input: { isTTY: false }, output: { isTTY: false } }), /DEPLOYMENT_CONFIRMATION_REQUIRED/);
  assert.equal(await confirmDeploymentPlan(hash, { suppliedHash: hash }), true);
});

test('interactive confirmation accepts only y/yes/是 and defaults to refusal', async () => {
  for (const [answer, expected] of [['y', true], ['yes', true], ['是', true], ['', false], ['no', false], ['a'.repeat(64), false]]) {
    const input = new PassThrough(); input.isTTY = true;
    const output = { isTTY: true, text: '', write(value) { this.text += value; } };
    const pending = confirmDeploymentIntent({ databaseFile: '/tmp/lexiflow/data/lexicon.db', input, output });
    input.write(`${answer}\n`);
    assert.equal(await pending, expected);
    assert.match(output.text, /SQLite 文件 \/tmp\/lexiflow\/data\/lexicon\.db[\s\S]*\[y\/N\]/);
    assert.equal(output.text.split('继续安装并使用本机 LexiFlow 数据库？[y/N]').length - 1, 1);
    assert.equal(input.listenerCount('data'), 0);
  }
});

test('interactive confirmation EOF and cancellation terminate promptly without consent', async () => {
  for (const kind of ['eof', 'abort']) {
    const input = new PassThrough(); input.isTTY = true;
    const output = { isTTY: true, write: () => {} };
    const controller = new AbortController();
    const pending = confirmDeploymentIntent({ databaseFile: '/tmp/lexiflow/data/lexicon.db', input, output, signal: controller.signal });
    if (kind === 'eof') input.end(); else controller.abort();
    await assert.rejects(pending, kind === 'eof' ? /DEPLOYMENT_CONFIRMATION_EOF/ : /DEPLOYMENT_CONFIRMATION_CANCELLED/);
    assert.equal(input.listenerCount('data'), 0);
  }
});

test('confirmation terminates a real child process with stdin still open', async () => {
  const moduleUrl = new URL('../installation/installation-plan-records.mjs', import.meta.url).href;
  for (const answer of ['y\n', 'n\n', null, 'abort']) {
    const script = `import { confirmDeploymentIntent } from ${JSON.stringify(moduleUrl)};
      process.stdin.isTTY = true; process.stdout.isTTY = true;
      const abort = new AbortController();
      const pending = confirmDeploymentIntent({ databaseFile: '/tmp/lexiflow/data/lexicon.db', signal: abort.signal });
      if (${JSON.stringify(answer)} === 'abort') abort.abort();
      try { await pending; } catch { process.exitCode = 2; }`;
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], { stdio: ['pipe', 'pipe', 'pipe'] });
    if (answer === null) child.stdin.end();
    else if (answer !== 'abort') child.stdin.write(answer);
    const code = await Promise.race([
      new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject); }),
      new Promise((_, reject) => setTimeout(() => { child.kill(); reject(new Error('confirmation kept stdin live')); }, 3000)),
    ]);
    assert.equal(code, answer === null || answer === 'abort' ? 2 : 0);
    child.stdin.destroy();
  }
});

test('interactive confirmation names the SQLite file and rejects non-file targets', async () => {
  const input = new PassThrough(); input.isTTY = true;
  const output = { isTTY: true, text: '', write(value) { this.text += value; } };
  const pending = confirmDeploymentIntent({ databaseFile: '/tmp/lexiflow/data/lexicon.db', input, output });
  input.write('n\n');
  assert.equal(await pending, false);
  assert.match(output.text, /拒绝非本安装目录的文件目标/);
  assert.throws(() => plan({ databaseTarget: { kind: 'external', host: '127.0.0.1', port: 45678 } }), /DEPLOYMENT_DATABASE_TARGET_INVALID/);
});

test('database plan rejects non-file targets and recovery journal is validated', () => {
  assert.throws(() => plan({ databaseTarget: { kind: 'managed', host: '127.0.0.1', port: 45678 } }), /DEPLOYMENT_DATABASE_TARGET_INVALID/);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-deployment-'));
  try {
    const file = path.join(directory, 'operation.json');
    const record = { schema: 1, operation: 'd'.repeat(32), planHash: deploymentPlanHash(plan()), stage: 'data-committed' };
    writeOperationJournal(file, record);
    assert.deepEqual(readOperationJournal(file), record);
    assert.throws(() => writeOperationJournal(file, { ...record, stage: 'success' }), /DEPLOYMENT_JOURNAL_INVALID/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('shared executor does not activate unless DB commit is verified and records commit stage', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-executor-'));
  const file = path.join(directory, 'operation.json'), calls = [];
  const callbacks = Object.fromEntries(['prepare', 'verifyPlan', 'stopApplication', 'applyData', 'activateApplication', 'verifyApplication']
    .map(name => [name, async () => { calls.push(name); return name === 'applyData' ? { status: 'BLOCKED' } : {}; }]));
  try {
    const selected = plan();
    await assert.rejects(executeDeployment({ plan: selected, confirmedHash: deploymentPlanHash(selected), journalFile: file, callbacks }), /DEPLOYMENT_DATA_COMMIT_UNCONFIRMED/);
    assert.deepEqual(calls, ['prepare', 'verifyPlan', 'stopApplication', 'applyData']);
    assert.equal(readOperationJournal(file).stage, 'application-stopped');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('shared executor writes nothing before an exact one-run confirmation', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-confirm-'));
  const file = path.join(directory, 'operation.json');
  try {
    await assert.rejects(executeDeployment({ plan: plan(), confirmedHash: 'f'.repeat(64), journalFile: file, callbacks: {} }), /DEPLOYMENT_CONFIRMATION_REQUIRED/);
    assert.equal(fs.existsSync(file), false);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
