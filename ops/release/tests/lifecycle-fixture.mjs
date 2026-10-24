import { mkdtemp, mkdir, readFile, rm, writeFile, chmod, realpath } from 'node:fs/promises';
import { spawn, spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const sourceEntrypoint = path.join(repoRoot, 'ops/docker/runtime-entrypoint.sh');
const sourceDockerfile = path.join(repoRoot, 'ops/docker/Dockerfile.runtime');
const identity = [
  'schemaVersion=1',
  'softwareVersion=2.1.0-test',
  'apiContract=caption-hints.v2',
  `sqlVersion=${'b'.repeat(64)}`,
  'releaseId=1792210462122',
  'preparationId=lexiflow.deterministic-preparation.v1',
  `ruleId=${'c'.repeat(64)}`,
  `datasetSha256=${'a'.repeat(64)}`,
].join('\n') + '\n';

function quote(value) {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}

function replaceFixedPath(source, name, value) {
  const expression = new RegExp(`^readonly ${name}=.*$`, 'm');
  if (!expression.test(source)) throw new Error(`FIXTURE_PATH_ANCHOR_MISSING:${name}`);
  return source.replace(expression, `readonly ${name}=${quote(value)}`);
}

/**
 * Copy the real PID1 script to a unique temp tree and replace only its fixed
 * image paths. This does not add runtime-configurable paths to product code.
 */
export async function lifecycleFixture(t) {
  const tmp = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-runtime-fixture-')));
  t.after(() => rm(tmp, { recursive: true, force: true }));
  const root = path.join(tmp, 'volume');
  const pgdata = path.join(root, 'postgres');
  const run = path.join(tmp, 'run');
  const identityPath = path.join(tmp, 'release-identity.env');
  const identityJson = path.join(tmp, 'release-identity.json');
  const dataset = path.join(tmp, 'dataset.zip');
  const jar = path.join(tmp, 'api.jar');
  const bin = path.join(tmp, 'bin');
  const entry = path.join(tmp, 'runtime-entrypoint.sh');
  const dockerfile = path.join(tmp, 'Dockerfile.runtime');
  await mkdir(root, { mode: 0o700 });
  await mkdir(run, { mode: 0o700 });
  await mkdir(bin, { mode: 0o700 });
  await chmod(run, 0o700);
  await writeFile(identityPath, identity, { mode: 0o600 });
  await writeFile(identityJson, '{}\n', { mode: 0o600 });
  await writeFile(dataset, 'synthetic-runtime-dataset', { mode: 0o600 });
  await writeFile(jar, 'synthetic-runtime-jar', { mode: 0o600 });
  await writeFile(path.join(bin, 'stat'), '#!/bin/sh\n[ "$1" = -c ] && [ "$2" = %a ] || exit 2\n/usr/bin/stat -c %a "$3" 2>/dev/null && exit 0\nexec /usr/bin/stat -f %Lp "$3"\n', { mode: 0o700 });
  await writeFile(path.join(bin, 'timeout'), '#!/bin/sh\nshift 2\nexec "$@"\n', { mode: 0o700 });
  await writeFile(path.join(bin, 'java'), '#!/bin/sh\nexit 37\n', { mode: 0o700 });
  let script = await readFile(sourceEntrypoint, 'utf8');
  for (const [name, value] of Object.entries({
    ROOT: root,
    PGDATA: pgdata,
    RUN: run,
    IDENTITY: identityPath,
    IDENTITY_JSON: identityJson,
    DATASET: dataset,
    JAR: jar,
  })) script = replaceFixedPath(script, name, value);
  await writeFile(entry, script, { mode: 0o700 });
  await writeFile(dockerfile, await readFile(sourceDockerfile));
  return { tmp, root, pgdata, run, identityPath, identityJson, dataset, jar, bin, entry, dockerfile, identity };
}

export function runRuntime(fixture, body, { env = {}, timeout = 10_000 } = {}) {
  return spawnSync('/bin/bash', ['-c', `source "$1"\n${body}`, 'runtime-fixture', fixture.entry], {
    cwd: fixture.tmp,
    encoding: 'utf8',
    timeout,
    env: { ...process.env, LC_ALL: 'C', PATH: `${fixture.bin}${path.delimiter}${process.env.PATH}`, ...env },
  });
}

export function spawnRuntime(fixture, body, { env = {} } = {}) {
  return spawn('/bin/bash', ['-c', `source "$1"\n${body}`, 'runtime-fixture', fixture.entry], {
    cwd: fixture.tmp,
    env: { ...process.env, LC_ALL: 'C', PATH: `${fixture.bin}${path.delimiter}${process.env.PATH}`, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}
