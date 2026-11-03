import test from 'node:test';
import assert from 'node:assert/strict';
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { lifecycleFixture, runRuntime } from './lifecycle-fixture.mjs';

async function allowRuntimeUid(f) {
  await writeFile(path.join(f.bin, 'id'), '#!/bin/sh\n[ "$1" = -u ] && { printf 10001; exit 0; }\nexit 2\n', { mode: 0o700 });
}

test('unknown files and foreign directory structures reject without mutation', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f);
  const foreign = path.join(f.root, 'foreign'); await writeFile(foreign, 'preserve');
  const result = runRuntime(f, 'runtime_start');
  assert.equal(result.status, 1); assert.match(result.stderr, /volume-unknown-content/u);
  assert.equal(await readFile(foreign, 'utf8'), 'preserve');
  await writeFile(path.join(f.root, '.lexiflow-owner'), 'lexiflow-runtime\n');
  const foreignTree = path.join(f.root, 'foreign-tree'); await mkdir(foreignTree); await writeFile(path.join(foreignTree, 'metadata'), 'preserve');
  const unknown = runRuntime(f, 'runtime_start');
  assert.equal(unknown.status, 1); assert.match(unknown.stderr, /volume-unknown-content/u);
  assert.equal(await readFile(path.join(foreignTree, 'metadata'), 'utf8'), 'preserve');
});

test('existing corrupt database or marker refuses unchanged', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f);
  await writeFile(path.join(f.root, '.lexiflow-owner'), 'lexiflow-runtime\n');
  await mkdir(f.database);
  const result = runRuntime(f, 'runtime_start');
  assert.equal(result.status, 1); assert.match(result.stderr, /sqlite-file-invalid/u);
  assert.equal(await (await import('node:fs/promises')).stat(f.database).then(x => x.isDirectory()), true);
});

test('offline initializer runs before read-only API and missing database never reaches API after init failure', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f);
  const log = path.join(f.tmp, 'java-calls');
  await writeFile(path.join(f.bin, 'java'), `#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\nexpect=0; db=; for arg do if [ "$expect" = 1 ]; then db=$arg; expect=0; elif [ "$arg" = --expected-file ]; then expect=1; fi; done\nif [ -n "$db" ]; then : > "$db"; exit 0; fi\nsleep 0.1\n`, { mode: 0o700 });
  const result = runRuntime(f, 'http_status_body() { HTTP_BODY="{\\"datasetVersion\\":42}"; return 0; }; runtime_health() { [ -f "$RUN/verified.json" ] && [ ! -e "$RUN/initializing" ] || return 1; return 0; }; runtime_start');
  assert.equal(result.status, 0, result.stderr);
  const calls = await readFile(log, 'utf8').catch(error => { error.message += `\n${result.stderr}`; throw error; });
  assert.match(calls, /--release-dataset initialize.*--expected-file/u);
  assert.match(calls, /-Dserver\.address=0\.0\.0\.0/u);
  assert.ok(calls.indexOf('--expected-file') < calls.indexOf('-Dserver.address='));
  assert.equal(await (await import('node:fs/promises')).stat(f.database).then(x => x.isFile()), true);
  assert.equal(JSON.parse(await readFile(path.join(f.run, 'verified.json'), 'utf8')).datasetVersion, 42);
  assert.match(await readFile(path.join(f.root, '.lexiflow-ready'), 'utf8'), /apiContract=caption-hints\.v2/u);
});

test('initializer failure keeps initialization gate and never launches API or creates database', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f);
  const log = path.join(f.tmp, 'java-calls');
  await writeFile(path.join(f.bin, 'java'), `#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\nexit 9\n`, { mode: 0o700 });
  const result = runRuntime(f, 'runtime_start');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /dataset-initialize-failed/u);
  const calls = await readFile(log, 'utf8');
  assert.match(calls, /--release-dataset initialize/u);
  assert.doesNotMatch(calls, /-Dserver\.address=/u);
  assert.equal(await (await import('node:fs/promises')).stat(f.database).then(() => true, () => false), false);
  assert.equal(await readFile(path.join(f.run, 'initializing'), 'utf8').then(() => true, () => false), true);
  assert.equal(await readFile(path.join(f.run, 'verified.json'), 'utf8').then(() => true, () => false), false);
});
