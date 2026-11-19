import test from 'node:test';
import assert from 'node:assert/strict';
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { lifecycleFixture, runRuntime } from './lifecycle-fixture.mjs';

async function allowRuntimeUid(f) {
  await writeFile(path.join(f.bin, 'id'), '#!/bin/sh\n[ "$1" = -u ] && { printf 10001; exit 0; }\nexit 2\n', { mode: 0o700 });
}
async function addPreferencesVolume(f) {
  await mkdir(f.preferences); await writeFile(f.preferencesDatabase,'synthetic');
  await writeFile(f.preferencesMountInfo,`12 1 0:99 / ${f.preferences} rw - tmpfs tmpfs rw\n`);
}

test('preferences initialization is an explicit bounded command using only its own mounted database', async t => {
  const f=await lifecycleFixture(t);await mkdir(f.preferences);
  await writeFile(f.preferencesMountInfo,`12 1 0:99 / ${f.preferences} rw - tmpfs tmpfs rw\n`);
  const log=path.join(f.tmp,'preferences-init');
  await writeFile(path.join(f.bin,'java'),`#!/bin/sh\nprintf '%s\\n' "$*" > '${log}'\nexit 0\n`,{mode:0o700});
  const result=spawnSync('/bin/bash',[f.entry,'init-preferences'],{encoding:'utf8',env:{...process.env,PATH:`${f.bin}${path.delimiter}${process.env.PATH}`}});
  assert.equal(result.status,0,`${result.stderr}\n${await readFile(log,'utf8').catch(()=>'<no-java-log>')}`);
  assert.match(await readFile(log,'utf8'),new RegExp(`--init-preferences jdbc:sqlite:${f.preferencesDatabase.replaceAll('/','\\/')}`,'u'));
});

test('runtime bootstraps an empty separate preferences mount before starting the API', async t => {
  const f=await lifecycleFixture(t);await allowRuntimeUid(f);await mkdir(f.preferences);
  await writeFile(f.preferencesMountInfo,`12 1 0:99 / ${f.preferences} rw - tmpfs tmpfs rw\n`);
  const log=path.join(f.tmp,'java-calls');
  await writeFile(path.join(f.bin,'java'),`#!/bin/sh\nprintf '%s\\n' "$*" >> '${log}'\nprevious=; for arg do if [ "$previous" = --init-preferences ]; then p=${'${arg#jdbc:sqlite:}'}; : > "$p"; exit 0; fi; previous=$arg; done\nexpect=0; db=; for arg do if [ "$expect" = 1 ]; then db=$arg; expect=0; elif [ "$arg" = --expected-file ]; then expect=1; fi; done\nif [ -n "$db" ]; then : > "$db"; exit 0; fi\nsleep 0.1\n`,{mode:0o700});
  const result=runRuntime(f,'http_status_body() { HTTP_BODY="{\\"datasetVersion\\":42}"; return 0; }; runtime_health() { [ -f "$RUN/verified.json" ] && [ ! -e "$RUN/initializing" ] || return 1; return 0; }; runtime_start');
  assert.equal(result.status,0,`${result.stderr}\n${await readFile(log,'utf8').catch(()=>'<no-java-log>')}`);
  const calls=await readFile(log,'utf8');
  assert.match(calls,new RegExp(`--init-preferences jdbc:sqlite:${f.preferencesDatabase.replaceAll('/','\\/')}`,'u'));
  assert.ok(calls.indexOf('--init-preferences')<calls.indexOf('--release-dataset initialize'));
  assert.ok(calls.indexOf('--release-dataset initialize')<calls.indexOf('-Dserver.address='));
});

test('runtime refuses to initialize a preferences directory that is not a separate mount', async t => {
  const f=await lifecycleFixture(t);await allowRuntimeUid(f);await mkdir(f.preferences);
  const log=path.join(f.tmp,'java-calls');
  await writeFile(path.join(f.bin,'java'),`#!/bin/sh\nprintf started > '${log}'\nexit 0\n`,{mode:0o700});
  const result=runRuntime(f,'runtime_start');
  assert.equal(result.status,1);assert.match(result.stderr,/preferences-volume-not-mounted/u);
  assert.equal(await readFile(log,'utf8').then(()=>true,()=>false),false);
  assert.equal(await readFile(f.preferencesDatabase,'utf8').then(()=>true,()=>false),false);
});

test('unknown files and foreign directory structures reject without mutation', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f); await addPreferencesVolume(f);
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
  const f = await lifecycleFixture(t); await allowRuntimeUid(f); await addPreferencesVolume(f);
  await writeFile(path.join(f.root, '.lexiflow-owner'), 'lexiflow-runtime\n');
  await mkdir(f.database);
  const result = runRuntime(f, 'runtime_start');
  assert.equal(result.status, 1); assert.match(result.stderr, /sqlite-file-invalid/u);
  assert.equal(await (await import('node:fs/promises')).stat(f.database).then(x => x.isDirectory()), true);
});

test('offline initializer runs before read-only API and missing database never reaches API after init failure', async t => {
  const f = await lifecycleFixture(t); await allowRuntimeUid(f); await addPreferencesVolume(f);
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
  const f = await lifecycleFixture(t); await allowRuntimeUid(f); await addPreferencesVolume(f);
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
