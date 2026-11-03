import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const cli = path.join(repo, 'ops/podman/commands/manage-podman-installation.mjs');

function fixture(t, { foreign = false } = {}) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-logs-')));
  const install = path.join(root, 'install');
  const bin = path.join(root, 'bin');
  fs.mkdirSync(path.join(install, 'data'), { recursive: true });
  fs.mkdirSync(path.join(install, '.lock'));
  fs.mkdirSync(bin);
  const id = 'a'.repeat(32);
  const project = `lexiflow-local-${id}`;
  const compose = 'services:\n  api:\n    image: api@sha256:fixed\n';
  const env = `LEXIFLOW_INSTALLATION_ID=${id}\nLEXIFLOW_API_IMAGE=api@sha256:fixed\n`;
  fs.writeFileSync(path.join(install, 'compose.yaml'), compose);
  fs.writeFileSync(path.join(install, 'release.env'), env, { mode: 0o600 });
  fs.writeFileSync(path.join(install, 'data', 'lexicon.db'), 'synthetic-sqlite-bytes', { mode: 0o400 });
  fs.writeFileSync(path.join(install, '.lock', 'owner.json'), JSON.stringify({
    schema: 1, root: install, installation: id, pid: process.pid, childPid: null, token: 'b'.repeat(32),
  }));
  fs.writeFileSync(path.join(install, 'upgrade.json'), '{"stage":"pending"}\n');
  const state = {
    schema: 2, root: install, id, project, phase: 'ready', apiPort: 18081, version: '0.1.0',
    generationVersion: 'c'.repeat(64), datasetIdentity: { version: 1 },
    databaseTarget: { kind: 'file' }, databaseFile: path.join(install, 'data', 'lexicon.db'),
    digests: {
      'compose.yaml': crypto.createHash('sha256').update(compose).digest('hex'),
      'release.env': crypto.createHash('sha256').update(env).digest('hex'),
    },
  };
  fs.writeFileSync(path.join(install, 'state.json'), JSON.stringify(state, null, 2) + '\n');
  const calls = path.join(root, 'podman-calls');
  const rows = JSON.stringify([{ Id: 'd'.repeat(64), State: 'running', Labels: {
    'com.docker.compose.project': project,
    'lexiflow.installation': foreign ? 'e'.repeat(32) : id,
    'com.docker.compose.service': 'api',
  } }]);
  const podman = `#!/bin/sh
printf '%s\\n' "$*" >> ${JSON.stringify(calls)}
case "$*" in
  'ps -a '*) printf '%s\\n' '${rows}';;
  'volume ls'*|'network ls'*) printf '[]\\n';;
  'compose '*logs*--follow*) printf 'synthetic-log-line\\n'; exec /bin/sleep 60;;
  *) exit 0;;
esac
`;
  fs.writeFileSync(path.join(bin, 'podman'), podman, { mode: 0o700 });
  fs.chmodSync(path.join(bin, 'podman'), 0o700);
  const snapshot = () => Object.fromEntries(fs.readdirSync(install, { recursive: true }).sort().map(relative => {
    const target = path.join(install, relative);
    const stat = fs.lstatSync(target);
    return [relative, stat.isFile() ? `${stat.mode & 0o777}:${crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex')}` : `dir:${stat.mode & 0o777}`];
  }));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { root, install, bin, calls, snapshot };
}

function startLogs(install, bin) {
  const child = spawn(process.execPath, [cli, 'logs', '--dir', install], {
    cwd: repo, env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH || ''}` },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '', stderr = '';
  child.stdout.setEncoding('utf8').on('data', part => { stdout += part; });
  child.stderr.setEncoding('utf8').on('data', part => { stderr += part; });
  const closed = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
  return { child, closed, read: () => ({ stdout, stderr }) };
}

async function waitFor(predicate, message) {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.fail(message);
}

test('logs streams the API viewer without mutating SQLite, locks, pending records, or operation logs', { timeout: 15000 }, async t => {
  const value = fixture(t);
  const before = value.snapshot();
  const viewer = startLogs(value.install, value.bin);
  await waitFor(() => viewer.read().stdout.includes('synthetic-log-line'), 'logs output was not streamed');
  const calls = fs.readFileSync(value.calls, 'utf8').trim().split('\n');
  assert.deepEqual(calls.slice(0, 4), [
    'info', 'ps -a --format json', 'volume ls --format json', 'network ls --format json',
  ]);
  assert.match(calls[4], /compose .*logs --follow --tail=0 api$/);
  assert.doesNotMatch(calls[4], /initialize|run /);
  assert.deepEqual(value.snapshot(), before);
  assert.equal(fs.existsSync(path.join(value.install, 'operations.log')), false);
  viewer.child.kill('SIGINT');
  const result = await viewer.closed;
  assert.equal(result.code, 0, result.stderr);
  assert.deepEqual(value.snapshot(), before);
});

test('logs refuses foreign resources before opening a follow viewer and preserves installation state', { timeout: 10000 }, async t => {
  const value = fixture(t, { foreign: true });
  const before = value.snapshot();
  const viewer = startLogs(value.install, value.bin);
  const result = await viewer.closed;
  assert.notEqual(result.code, 0);
  assert.doesNotMatch(fs.readFileSync(value.calls, 'utf8'), /logs --follow/);
  assert.deepEqual(value.snapshot(), before);
});
