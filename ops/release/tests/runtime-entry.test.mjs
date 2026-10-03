import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, realpath, rename, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { generateRuntimeEntry } from '../runtime-entry.mjs';
import { resolveBuildIdentity } from '../version.mjs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cp, rm } from 'node:fs/promises';
import { extensionBytes } from './zip-fixture.mjs';
import { packageManifest } from '../package.mjs';
import { manifestSha256, serializeManifest } from '../manifest.mjs';
import fsPromises from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';

const digest = (value) => createHash('sha256').update(value).digest('hex');
async function cleanFixture(t) {
  const temp = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-runtime-entry-fixture-')));
  const repo = path.join(temp, 'repo');
  await mkdir(repo);
  for (const item of ['ops/release', 'ops/release/tests']) await mkdir(path.join(repo, item), { recursive: true });
  const original = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
  await cp(path.join(original, 'version.txt'), path.join(repo, 'ops/release/version.txt'));
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'fixture@example.invalid']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'Fixture']);
  execFileSync('git', ['-C', repo, 'add', '.']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'fixture']);
  const source = resolveBuildIdentity(repo);
  const root = path.join(temp, 'artifacts with spaces');
  await mkdir(root);
  const licenses = [
    ['api-runtime', 'API-runtime'], ['lexiflow', 'LexiFlow'], ['postgres', 'PostgreSQL'],
    ['extension-third-party', 'extension-third-party'], ['dataset-license', 'dataset']
  ].map(([id, component]) => ({ id, component, licenseId: 'MIT', licenseName: 'MIT', sourceUrl: 'https://example.invalid/license', noticePath: `licenses/${id}.txt`, noticeBytes: 1, noticeSha256: digest('L') }));
  const artifacts = [];
  const add = async (role, file, content, licenseIds, extra = {}) => {
    await mkdir(path.dirname(path.join(root, file)), { recursive: true });
    await writeFile(path.join(root, file), content);
    artifacts.push({ role, path: file, bytes: Buffer.byteLength(content), sha256: digest(content), licenseIds, ...extra });
  };
  for (const lic of licenses) await add('license', lic.noticePath, 'L', [lic.id]);
  await add('api-image', 'images/api.tar', 'api', ['api-runtime', 'lexiflow'], { platform: 'linux/amd64', imageDigest: `sha256:${'a'.repeat(64)}` });
  await add('postgres-image', 'images/db.tar', 'db', ['postgres'], { platform: 'linux/amd64', imageDigest: `sha256:${'b'.repeat(64)}` });
  await add('compose', 'compose.yaml', 'compose', ['lexiflow']);
  await add('dataset', 'dataset.zip', 'dataset', ['dataset-license'], { metadata: { releaseId: 'release', preparationId: 'prep', ruleId: 'rule', sqlVersion: 'sql1' } });
  await add('extension', 'extension.zip', extensionBytes(source), ['lexiflow', 'extension-third-party'], { metadata: { softwareVersion: source.softwareVersion, sourceCommit: source.sourceCommit } });
  await add('sql', 'schema.sql', 'sql', ['lexiflow']);
  const descriptor = { schemaVersion: 1, buildIdentity: source, softwareVersion: source.softwareVersion, sourceCommit: source.sourceCommit, apiContract: 'api1', sqlVersion: 'sql1', dataset: { releaseId: 'release', preparationId: 'prep', ruleId: 'rule' }, platforms: ['linux/amd64'], artifacts, licenses };
  t.after(() => rm(temp, { recursive: true, force: true }));
  return { repo, root, descriptor, output: path.join(temp, 'package'), descriptorFile: path.join(temp, 'descriptor.json') };
}

const body = `lf_verify_release || exit 1\nif [ "${'${1:-}'}" = verify ]; then exit 0; fi\nif [ "${'${1:-}'}" = platform ]; then lf_select_platform "${'${2:-}'}" || exit 1; printf '%s\\n' "$LF_API_IMAGE"; exit 0; fi\nif [ "${'${1:-}'}" = identity ]; then printf '%s\\n' "$LF_RELEASE_KEY|$LF_SOFTWARE_VERSION|$LF_API_CONTRACT|$LF_SQL_VERSION|$LF_DATASET_SHA256|$LF_DATASET_PATH|$LF_COMPOSE_PATH"; exit 0; fi\nexit 64`;

async function assembledFixture(t) {
  const f = await cleanFixture(t);
  const generated = await generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body });
  await writeFile(f.descriptorFile, JSON.stringify({ ...f.descriptor, artifacts: [...f.descriptor.artifacts, generated.artifact] }));
  assert.deepEqual(await packageManifest({ repoRoot: f.repo, descriptorFile: f.descriptorFile, artifactRoot: f.root, outputDirectory: f.output }), generated.manifest);
  const run = (...args) => execFileSync('sh', [path.join(f.output, 'lexiflow.sh'), ...args], { cwd: f.output, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  return { ...f, generated, run };
}

test('runtime entry generator rejects incomplete source identity without leaking path or body', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'lexiflow-runtime-entry-'));
  const privateMarker = 'synthetic-private-marker';
  await mkdir(path.join(root, 'artifacts'));
  await writeFile(path.join(root, 'artifacts', 'lexiflow.sh'), 'sentinel');
  await assert.rejects(generateRuntimeEntry({ repoRoot: root, descriptor: {}, artifactRoot: path.join(root, 'artifacts'), lifecycleBody: `printf '${privateMarker}'` }), (error) => error.message === 'RELEASE_SOURCE_REJECTED' && !error.message.includes(privateMarker) && !error.message.includes(root));
  assert.equal((await readFile(path.join(root, 'artifacts', 'lexiflow.sh'), 'utf8')), 'sentinel');
});

test('output path rejects newline and NUL before touching artifacts', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'lexiflow-runtime-entry-'));
  await assert.rejects(generateRuntimeEntry({ repoRoot: root, descriptor: {}, artifactRoot: `${root}\ninvalid`, lifecycleBody: 'exit 0' }), /ARGUMENTS_INVALID/);
  await assert.rejects(generateRuntimeEntry({ repoRoot: root, descriptor: {}, artifactRoot: `${root}\u0000invalid`, lifecycleBody: 'exit 0' }), /ARGUMENTS_INVALID/);
});

test('generated POSIX entry verifies the actual synthetic package and rejects artifact tampering', async (t) => {
  const { output, generated, run } = await assembledFixture(t);
  execFileSync('sh', ['-n', path.join(output, 'lexiflow.sh')], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(generated.artifact.path, 'lexiflow.sh');
  assert.equal(generated.manifest.artifacts.find((item) => item.role === 'runtime-entry').bytes, Buffer.byteLength(generated.script));
  assert.equal(run('verify'), '');
  assert.match(run('platform', 'linux/amd64'), /^sha256:a/);
  assert.throws(() => run('platform', 'linux/arm64'));
  await writeFile(path.join(output, 'dataset.zip'), 'evil');
  assert.throws(() => run('verify'));
});

test('generator is deterministic, reuses identical entry, and returns the complete canonical manifest', async (t) => {
  const f = await cleanFixture(t);
  const first = await generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body });
  const second = await generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body });
  assert.deepEqual(second, first);
  assert.equal(first.artifact.sha256, digest(first.script));
  assert.equal(first.artifact.bytes, Buffer.byteLength(first.script));
  assert.deepEqual(first.manifest.artifacts.find((item) => item.role === 'runtime-entry'), first.artifact);
  assert.equal((await readdir(f.root)).filter((name) => name.startsWith('.lexiflow-')).length, 0);
  assert.equal(await readFile(path.join(f.root, 'lexiflow.sh'), 'utf8'), first.script);
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: `${body}\n# changed` }), /OUTPUT_EXISTS_DIFFERENT/u);
  assert.equal(await readFile(path.join(f.root, 'lexiflow.sh'), 'utf8'), first.script);
});

test('real sh binds exact manifest bytes, checksum LF, every artifact, entry and nested paths', async (t) => {
  const f = await assembledFixture(t);
  const manifestFile = path.join(f.output, 'manifest.json');
  const checksumFile = path.join(f.output, 'manifest.json.sha256');
  const scriptFile = path.join(f.output, 'lexiflow.sh');
  const manifestText = await readFile(manifestFile, 'utf8');
  const checksumText = await readFile(checksumFile, 'utf8');
  const scriptText = await readFile(scriptFile, 'utf8');
  const releaseKey = digest(serializeManifest({ ...f.generated.manifest, artifacts: f.generated.manifest.artifacts.filter((item) => item.role !== 'runtime-entry') }));
  assert.equal(f.run('identity'), `${releaseKey}|${f.descriptor.softwareVersion}|${f.descriptor.apiContract}|${f.descriptor.sqlVersion}|${f.descriptor.artifacts.find((item) => item.role === 'dataset').sha256}|dataset.zip|compose.yaml\n`);
  assert.equal(checksumText, manifestSha256(manifestText));
  await writeFile(checksumFile, `${checksumText}\n`);
  assert.throws(() => f.run('verify'));
  await writeFile(checksumFile, checksumText);
  assert.equal(f.run('verify'), '');

  const changed = JSON.parse(manifestText);
  changed.artifacts.find((item) => item.role === 'dataset').sha256 = 'f'.repeat(64);
  const changedText = serializeManifest(changed);
  await writeFile(manifestFile, changedText);
  await writeFile(checksumFile, manifestSha256(changedText));
  assert.throws(() => f.run('verify'));
  await writeFile(manifestFile, manifestText);
  await writeFile(checksumFile, checksumText);
  await writeFile(manifestFile, `${manifestText}\n`);
  assert.throws(() => f.run('verify'));
  await writeFile(manifestFile, manifestText);

  await writeFile(scriptFile, `${scriptText}# changed\n`);
  assert.throws(() => f.run('verify'));
  await writeFile(scriptFile, scriptText);
  const datasetFile = path.join(f.output, 'dataset.zip');
  const datasetText = await readFile(datasetFile);
  await writeFile(datasetFile, 'X'.repeat(datasetText.length));
  assert.throws(() => f.run('verify'));
  await writeFile(datasetFile, datasetText);
  await rm(datasetFile);
  assert.throws(() => f.run('verify'));
  await writeFile(datasetFile, datasetText);
  const licenseDirectory = path.join(f.output, 'licenses');
  const movedDirectory = path.join(f.output, 'real-licenses');
  await rename(licenseDirectory, movedDirectory);
  await symlink('real-licenses', licenseDirectory);
  assert.throws(() => f.run('verify'));
  await rm(licenseDirectory);
  await rename(movedDirectory, licenseDirectory);
  assert.equal(f.run('verify'), '');
});

test('generator rejects unsafe path, missing identity or license, dirty source, and pre-existing output', async (t) => {
  const f = await cleanFixture(t);
  const sentinel = path.join(f.root, 'injected');
  const unsafe = structuredClone(f.descriptor);
  unsafe.artifacts.find((item) => item.role === 'sql').path = `schema.sql;touch ${sentinel}`;
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: unsafe, artifactRoot: f.root, lifecycleBody: body }), /INVALID_PATH/u);
  await assert.rejects(readFile(sentinel));
  const missingLicense = structuredClone(f.descriptor);
  missingLicense.licenses = missingLicense.licenses.filter((item) => item.component !== 'LexiFlow');
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: missingLicense, artifactRoot: f.root, lifecycleBody: body }), /RUNTIME_ENTRY_LICENSE_MISSING/u);
  const badIdentity = structuredClone(f.descriptor);
  badIdentity.sourceCommit = 'f'.repeat(40);
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: badIdentity, artifactRoot: f.root, lifecycleBody: body }), /SOURCE_IDENTITY_MISMATCH/u);
  const declared = structuredClone(f.descriptor);
  declared.artifacts.push({ role: 'runtime-entry', path: 'lexiflow.sh' });
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: declared, artifactRoot: f.root, lifecycleBody: body }), /RUNTIME_ENTRY_ALREADY_DECLARED/u);
  await writeFile(path.join(f.root, 'lexiflow.sh'), 'preserve');
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), /OUTPUT_EXISTS_DIFFERENT/u);
  assert.equal(await readFile(path.join(f.root, 'lexiflow.sh'), 'utf8'), 'preserve');
  await rm(path.join(f.root, 'lexiflow.sh'));
  await symlink('dataset.zip', path.join(f.root, 'lexiflow.sh'));
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), /OUTPUT_EXISTS_DIFFERENT/u);
  await rm(path.join(f.root, 'lexiflow.sh'));
  await writeFile(path.join(f.repo, 'dirty.txt'), 'dirty');
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), /RELEASE_SOURCE_REJECTED/u);
});

test('real sh never invokes Docker, network or language runtimes and does not reveal private input', async (t) => {
  const f = await assembledFixture(t);
  const shim = path.join(path.dirname(f.output), 'shims');
  await mkdir(shim);
  const marker = path.join(path.dirname(f.output), 'forbidden-called');
  for (const command of ['docker', 'curl', 'wget', 'node', 'python', 'python3', 'java']) {
    await writeFile(path.join(shim, command), `#!/bin/sh\nprintf x >> '${marker}'\nexit 99\n`, { mode: 0o755 });
  }
  const result = execFileSync('sh', [path.join(f.output, 'lexiflow.sh'), 'verify'], { encoding: 'utf8', env: { ...process.env, PATH: `${shim}:${process.env.PATH}` }, stdio: ['ignore', 'pipe', 'pipe'] });
  assert.equal(result, '');
  await assert.rejects(readFile(marker));
  const script = await readFile(path.join(f.output, 'lexiflow.sh'), 'utf8');
  assert.doesNotMatch(script, /\/Users\/|\/private\/tmp\/|synthetic-private-marker/u);
});

test('generator verifies every non-entry input before publishing and rejects an output race without overwriting', async (t) => {
  const f = await cleanFixture(t);
  const dataset = path.join(f.root, 'dataset.zip');
  const original = await readFile(dataset);
  await writeFile(dataset, 'X'.repeat(original.length));
  await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), /ARTIFACT_DIGEST_MISMATCH/u);
  await assert.rejects(readFile(path.join(f.root, 'lexiflow.sh')));
  await writeFile(dataset, original);

  const actualLink = fsPromises.link;
  let injected = false;
  t.mock.method(fsPromises, 'link', async (source, target) => {
    if (!injected) {
      injected = true;
      await writeFile(target, 'competitor', { flag: 'wx' });
    }
    return actualLink(source, target);
  });
  syncBuiltinESMExports();
  try {
    await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), /OUTPUT_EXISTS_DIFFERENT/u);
    assert.equal(injected, true);
    assert.equal(await readFile(path.join(f.root, 'lexiflow.sh'), 'utf8'), 'competitor');
    assert.equal((await readdir(f.root)).filter((name) => name.startsWith('.lexiflow-')).length, 0);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});

test('synthetic publish I/O failures return fixed reasons and clean only owned temporary files', async (t) => {
  const f = await cleanFixture(t);
  const marker = 'synthetic-private-path-marker';
  const target = path.join(f.root, 'lexiflow.sh');
  const originalOpen = fsPromises.open;
  t.mock.method(fsPromises, 'open', async (...args) => {
    const handle = await originalOpen(...args);
    if (path.basename(args[0]).startsWith('.lexiflow-')) {
      handle.writeFile = async () => { throw new Error(marker); };
    }
    return handle;
  });
  syncBuiltinESMExports();
  try {
    await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), (error) => error.message === 'OUTPUT_PUBLISH_FAILED' && !error.message.includes(marker));
    await assert.rejects(readFile(target));
    assert.equal((await readdir(f.root)).filter((name) => name.startsWith('.lexiflow-')).length, 0);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
  const originalLink = fsPromises.link;
  t.mock.method(fsPromises, 'link', async () => { throw Object.assign(new Error(marker), { code: 'EACCES' }); });
  syncBuiltinESMExports();
  try {
    await assert.rejects(generateRuntimeEntry({ repoRoot: f.repo, descriptor: f.descriptor, artifactRoot: f.root, lifecycleBody: body }), (error) => error.message === 'OUTPUT_PUBLISH_FAILED' && !error.message.includes(marker));
    await assert.rejects(readFile(target));
    assert.equal((await readdir(f.root)).filter((name) => name.startsWith('.lexiflow-')).length, 0);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
    assert.equal(fsPromises.link, originalLink);
  }
});

test('real sh rejects symlinked release root and reports missing hash utility with a fixed reason', async (t) => {
  const f = await assembledFixture(t);
  const alias = path.join(path.dirname(f.output), 'alias');
  await symlink(f.output, alias);
  assert.throws(() => execFileSync('sh', [path.join(alias, 'lexiflow.sh'), 'verify'], { cwd: f.output, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }));
  const shim = path.join(path.dirname(f.output), 'no-hash-tools');
  await mkdir(shim);
  for (const command of ['wc', 'tr']) await symlink(execFileSync('/bin/sh', ['-c', `command -v ${command}`], { encoding: 'utf8' }).trim(), path.join(shim, command));
  let error;
  try {
    execFileSync('/bin/sh', [path.join(f.output, 'lexiflow.sh'), 'verify'], { cwd: f.output, env: { ...process.env, PATH: shim }, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (caught) { error = caught; }
  assert.equal(error?.stderr, 'LF_VERIFY_HASH_TOOL_MISSING\n');
});

test('real sh rejects a digest tool that prints the right hash but exits nonzero', async (t) => {
  const f = await assembledFixture(t);
  const shim = path.join(path.dirname(f.output), 'failing-hash-tool');
  await mkdir(shim);
  const scriptHash = digest(await readFile(path.join(f.output, 'lexiflow.sh')));
  await writeFile(path.join(shim, 'sha256sum'), `#!/bin/sh\nprintf '%s  -\\n' '${scriptHash}'\nexit 7\n`, { mode: 0o755 });
  let error;
  try {
    execFileSync('/bin/sh', [path.join(f.output, 'lexiflow.sh'), 'verify'], { cwd: f.output, env: { ...process.env, PATH: `${shim}:${process.env.PATH}` }, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (caught) { error = caught; }
  assert.equal(error?.stderr, 'LF_VERIFY_HASH_FAILED\n');
});

test('real sh rejects non-ASCII or uppercase digest syntax before comparing identity', async (t) => {
  const f = await assembledFixture(t);
  execFileSync('/bin/sh', ['-n', path.join(f.output, 'lexiflow.sh')], { timeout: 10000 });
  const shim = path.join(path.dirname(f.output), 'invalid-hash-tool');
  await mkdir(shim);
  await writeFile(path.join(shim, 'sha256sum'), '#!/bin/sh\nprintf \'%s  -\\n\' "$LF_TEST_HASH"\n', { mode: 0o755 });
  for (const value of ['A'.repeat(64), 'é'.repeat(64), `${'a'.repeat(63)}:`]) {
    let error;
    try {
      execFileSync('/bin/sh', [path.join(f.output, 'lexiflow.sh'), 'verify'], {
        cwd: f.output, env: { ...process.env, PATH: `${shim}:${process.env.PATH}`, LF_TEST_HASH: value },
        encoding: 'utf8', timeout: 10000, stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (caught) { error = caught; }
    assert.equal(error?.stderr, 'LF_VERIFY_HASH_FAILED\n');
    assert.equal(error?.stdout, '');
    assert.equal(error?.signal, null);
  }
});
