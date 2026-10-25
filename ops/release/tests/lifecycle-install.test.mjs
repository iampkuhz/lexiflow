import test from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdir, readFile, readdir, lstat, rename, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { lifecycleFixture, runRuntime } from './lifecycle-fixture.mjs';

async function snapshot(root) {
  const result = [];
  async function visit(relative = '') {
    const file = relative ? path.join(root, relative) : root;
    const info = await lstat(file);
    const item = { path: relative || '.', mode: info.mode & 0o777, type: info.isDirectory() ? 'directory' : info.isFile() ? 'file' : info.isSymbolicLink() ? 'symlink' : 'other' };
    if (info.isFile()) item.bytes = (await readFile(file)).toString('base64');
    result.push(item);
    if (info.isDirectory()) for (const name of (await readdir(file)).sort()) await visit(relative ? path.join(relative, name) : name);
  }
  await visit();
  return result;
}

test('nonempty unknown volume and ownerless pending marker reject without mutation', async (t) => {
  const f = await lifecycleFixture(t);
  await writeFile(path.join(f.root, 'unknown'), 'preserve these bytes\n');
  const before = await snapshot(f.root);
  const rejected = runRuntime(f, 'read_identity; validate_volume');
  assert.equal(rejected.status, 1);
  assert.equal(rejected.stderr.trim(), 'lexiflow-runtime: volume-unknown-content');
  assert.deepEqual(await snapshot(f.root), before);

  const drift = await lifecycleFixture(t);
  const identityLine = runRuntime(drift, 'read_identity; volume_identity_line');
  assert.equal(identityLine.status, 0, identityLine.stderr);
  await writeFile(path.join(drift.root, '.lexiflow-owner'), 'lexiflow-runtime');
  await writeFile(path.join(drift.root, '.lexiflow-format'), '2');
  await writeFile(path.join(drift.root, '.lexiflow-initializing'), identityLine.stdout.replace('releaseId=1792210462122', 'releaseId=older-release'));
  const driftBefore = await snapshot(drift.root);
  const driftResult = runRuntime(drift, 'read_identity; validate_volume');
  assert.equal(driftResult.status, 1);
  assert.equal(driftResult.stderr.trim(), 'lexiflow-runtime: volume-identity-mismatch');
  assert.deepEqual(await snapshot(drift.root), driftBefore);

  const oldFormat = await lifecycleFixture(t);
  await writeFile(path.join(oldFormat.root, '.lexiflow-owner'), 'lexiflow-runtime');
  await writeFile(path.join(oldFormat.root, '.lexiflow-format'), '1');
  await writeFile(path.join(oldFormat.root, '.lexiflow-initializing'), identityLine.stdout);
  const oldFormatBefore = await snapshot(oldFormat.root);
  const oldFormatResult = runRuntime(oldFormat, 'read_identity; validate_volume');
  assert.equal(oldFormatResult.status, 1);
  assert.match(oldFormatResult.stderr.trim(), /^lexiflow-runtime: volume-(?:format-unsupported|identity-mismatch)$/u);
  assert.deepEqual(await snapshot(oldFormat.root), oldFormatBefore);

  const pendingFixture = await lifecycleFixture(t);
  await writeFile(path.join(pendingFixture.root, '.lexiflow-owner.pending'), 'lexiflow-runtime');
  const pendingBefore = await snapshot(pendingFixture.root);
  const pendingResult = runRuntime(pendingFixture, 'read_identity; validate_volume');
  assert.equal(pendingResult.status, 1, pendingResult.stderr);
  assert.equal(pendingResult.stderr.trim(), 'lexiflow-runtime: volume-owner-missing');
  assert.deepEqual(await snapshot(pendingFixture.root), pendingBefore);
});

test('recognized interrupted initdb clears only partial PGDATA and resumes initialization', async (t) => {
  const f = await lifecycleFixture(t);
  const bin = f.bin;
  await writeFile(path.join(bin, 'initdb'), '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -D ]; then target=$2; shift 2; else shift; fi; done\nprintf "17\\n" > \"$target/PG_VERSION\"\n', { mode: 0o700 });
  const identityLine = runRuntime(f, 'read_identity; volume_identity_line');
  assert.equal(identityLine.status, 0, identityLine.stderr);
  assert.match(f.identity, /^schemaVersion=1\n/u);
  assert.match(identityLine.stdout, /^schemaVersion=2\n/u);
  await writeFile(path.join(f.root, '.lexiflow-owner'), 'lexiflow-runtime');
  await writeFile(path.join(f.root, '.lexiflow-format'), '2');
  await writeFile(path.join(f.root, '.lexiflow-initializing'), identityLine.stdout);
  await writeFile(path.join(f.root, '.postgres-password'), 'd'.repeat(64));
  await writeFile(path.join(f.root, '.app-password'), 'e'.repeat(64));
  await mkdir(f.pgdata, { mode: 0o700 });
  await mkdir(path.join(f.pgdata, 'partial-wal'));
  await writeFile(path.join(f.pgdata, 'partial-wal', 'chunk'), 'interrupted');
  const resumed = runRuntime(f, 'read_identity; validate_volume; prepare_pgdata', { env: { PATH: `${bin}${path.delimiter}${process.env.PATH}` } });
  assert.equal(resumed.status, 0, resumed.stderr);
  assert.equal((await readFile(path.join(f.pgdata, 'PG_VERSION'), 'utf8')), '17\n');
  assert.deepEqual(await readdir(f.pgdata), ['PG_VERSION']);
  assert.equal(await readFile(path.join(f.root, '.lexiflow-initdb-complete'), 'utf8'), identityLine.stdout);
});

test('restart discards only known volatile proof markers and refuses unknown RUN content unchanged', async (t) => {
  const f = await lifecycleFixture(t);
  await writeFile(path.join(f.run, 'initializing'), 'old boot');
  await writeFile(path.join(f.run, 'verified.json'), '{"datasetVersion":123}');
  await writeFile(path.join(f.run, 'verified.pending'), 'partial proof');
  const prepared = runRuntime(f, 'prepare_runtime_state');
  assert.equal(prepared.status, 0, prepared.stderr);
  assert.deepEqual(await readdir(f.run), []);

  await writeFile(path.join(f.run, 'foreign-proof'), 'not runtime-owned');
  const before = await snapshot(f.run);
  const rejected = runRuntime(f, 'prepare_runtime_state');
  assert.equal(rejected.status, 1);
  assert.equal(rejected.stderr.trim(), 'lexiflow-runtime: run-private-state-unknown');
  assert.deepEqual(await snapshot(f.run), before);
});

test('initializer failure exits before verified proof or ready marker can be published', async (t) => {
  const f = await lifecycleFixture(t);
  const body = `
id() { [ "$1" = -un ] && { printf 'postgres\\n'; return 0; }; return 1; }
prepare_pgdata() { mkdir -p "$PGDATA"; printf '17\\n' > "$PGDATA/PG_VERSION"; printf '%s\\n' "$(volume_identity_line)" > "$ROOT/.lexiflow-initdb-complete"; }
start_postgres() { sleep 60 & PG_PID=$!; }
bootstrap_database() { :; }
start_api() { sleep 60 & API_PID=$!; }
http_status_body() { HTTP_BODY='{"ready":false,"reason":"INITIALIZING"}'; return 0; }
runtime_start`;
  const result = runRuntime(f, body, { timeout: 8000 });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /lexiflow-runtime: dataset-initialize-failed/u);
  assert.equal(await readFile(path.join(f.root, '.lexiflow-ready')).then(() => true, () => false), false);
  assert.equal(await readFile(path.join(f.run, 'verified.json')).then(() => true, () => false), false);
  assert.equal(await readFile(path.join(f.run, 'initializing')).then(() => true, () => false), true);
});

test('PostgreSQL or API exit is fatal and superviser stops the remaining child', async (t) => {
  const f = await lifecycleFixture(t);
  for (const failed of ['postgres', 'api']) {
    const pg = failed === 'postgres' ? 'false' : 'sleep 60';
    const api = failed === 'api' ? 'false' : 'sleep 60';
    const failedPid = failed === 'postgres' ? 'PG_PID' : 'API_PID';
    const survivorPid = failed === 'postgres' ? 'API_PID' : 'PG_PID';
    const pidFile = path.join(f.tmp, `${failed}-survivor.pid`);
    const command = `${pg} & PG_PID=$!; ${api} & API_PID=$!; printf '%s\\n' "$${survivorPid}" > '${pidFile}'; wait "$${failedPid}" || :; supervise`;
    const result = runRuntime(f, command, { timeout: 8000 });
    assert.equal(result.status, 1, `${failed}: ${result.stderr}`);
    assert.match(result.stderr, new RegExp(`lexiflow-runtime: ${failed}-exited`));
    const pid = Number((await readFile(pidFile, 'utf8')).trim());
    assert.throws(() => process.kill(pid, 0), (error) => error.code === 'ESRCH');
  }
});


test('compatible application update preserves data while SQL or dataset drift refuses unchanged', async (t) => {
  const f = await lifecycleFixture(t);
  const initialized = runRuntime(f, 'read_identity; validate_volume');
  assert.equal(initialized.status, 0, initialized.stderr);
  await writeFile(path.join(f.root, '.lexiflow-initdb-complete'), `${runRuntime(f, 'read_identity; volume_identity_line').stdout}`);
  await writeFile(path.join(f.pgdata, 'PG_VERSION'), '17\n');
  await writeFile(path.join(f.pgdata, 'preserved-fact'), 'user data\n');
  await rename(path.join(f.root, '.lexiflow-initializing'), path.join(f.root, '.lexiflow-ready'));
  const before = await snapshot(f.root);
  const compatible = f.identity.replace('softwareVersion=2.1.0-test', 'softwareVersion=2.1.1-test');
  await writeFile(f.identityPath, compatible);
  const restarted = runRuntime(f, 'read_identity; validate_volume');
  assert.equal(restarted.status, 0, restarted.stderr);
  assert.deepEqual(await snapshot(f.root), before);

  for (const incompatible of [
    compatible.replace(`sqlVersion=${'b'.repeat(64)}`, `sqlVersion=${'d'.repeat(64)}`),
    compatible.replace(`datasetSha256=${'a'.repeat(64)}`, `datasetSha256=${'e'.repeat(64)}`),
  ]) {
    await writeFile(f.identityPath, incompatible);
    const rejected = runRuntime(f, 'read_identity; validate_volume');
    assert.equal(rejected.status, 1, rejected.stderr);
    assert.equal(rejected.stderr.trim(), 'lexiflow-runtime: volume-identity-mismatch');
    assert.deepEqual(await snapshot(f.root), before);
  }
});
