import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { activatePreparedTarget } from '../installation/installation-upgrade-workflow.mjs';
import { treeDigest, verifyExtension, installationRecord, publishRecord } from '../installation/installation-update-transaction.mjs';
import { versionSummary } from '../installation/installation-version-summary.mjs';
import { EXTENSION_PACKAGE_FILES as PACKAGE_FILES } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';
import { makeZip } from '../../release/tests/zip-fixture.mjs';
import { assertExtensionTreeMatches, materializeExtensionTree } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';

const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const identity = n => {
  const baseVersion = '2.0.0-SNAPSHOT', sourceCommit = n.repeat(40), sourceSha256 = 'a'.repeat(64), sourceDate = '20261006';
  const softwareVersion = `2.0.0-SNAPSHOT.${sourceDate}.g${sourceCommit.slice(0, 7)}`;
  const buildId = sha(JSON.stringify({ baseVersion, sourceCommit, sourceSha256, dirty: false, sourceDate }));
  return { schemaVersion: 1, baseVersion, softwareVersion, chromeVersion: '2.0.0.0', sourceCommit, sourceSha256, buildId, dirty: false, channel: 'snapshot' };
};
function extension(dir, id) {
  fs.mkdirSync(dir, { recursive: true });
  for (const name of PACKAGE_FILES) {
    const bytes = Buffer.from(name === 'manifest.json'
      ? JSON.stringify({ manifest_version: 3, version: id.chromeVersion, version_name: id.softwareVersion, permissions: ['storage'], host_permissions: ['http://127.0.0.1:18080/*'], background: { service_worker: 'background.js' }, action: { default_popup: 'popup.html' } })
      : name === 'build-identity.json' ? JSON.stringify(id) : name === 'popup.html' ? '<link href="popup.css"><script src="popup.js"></script>' : `fixture:${name}`);
    const target = path.join(dir, ...name.split('/')); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, bytes);
  }
}
function archive(dir, id) {
  const filename = `lexiflow-extension-${id.softwareVersion}.zip`;
  const entries = Object.fromEntries(PACKAGE_FILES.map(name => [name, Buffer.from(name === 'manifest.json'
    ? JSON.stringify({ manifest_version: 3, version: id.chromeVersion, version_name: id.softwareVersion, permissions: ['storage'], host_permissions: ['http://127.0.0.1:18080/*'], background: { service_worker: 'background.js' }, action: { default_popup: 'popup.html' } })
    : name === 'build-identity.json' ? JSON.stringify(id) : name === 'popup.html' ? '<link href="popup.css"><script src="popup.js"></script>' : `fixture:${name}`)]));
  const bytes = makeZip(Object.entries(entries)), digest = sha(bytes);
  const relative = `extension-artifacts/${id.buildId}/${filename}`, target = path.join(dir, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, bytes);
  fs.writeFileSync(`${target}.sha256`, `${digest}  ${filename}\n`);
  return { path: relative, bytes: bytes.length, sha256: digest, buildIdentity: id };
}
function fixture() {
  const dir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-forward-upgrade-'));
  const old = identity('1'), next = identity('2'), operation = 'b'.repeat(32);
  const dataFile = path.join(dir, 'data/ecdict-source.test/stardict.csv');
  fs.mkdirSync(path.dirname(dataFile), { recursive: true }); fs.writeFileSync(dataFile, 'synthetic-csv');
  const secret = path.join(dir, 'secrets/app-password'); fs.mkdirSync(path.dirname(secret)); fs.writeFileSync(secret, 'secret');
  const sql = path.join(dir, 'infra/postgres/schema.sql'); fs.mkdirSync(path.dirname(sql), { recursive: true }); fs.writeFileSync(sql, 'old sql');
  const stagedSql = path.join(dir, `.schema-${operation}.sql`); fs.writeFileSync(stagedSql, 'target sql');
  fs.writeFileSync(path.join(dir, 'compose.yaml'), 'old compose'); fs.writeFileSync(path.join(dir, 'release.env'), 'old env');
  extension(path.join(dir, 'extension'), old);
  const stage = path.join(dir, `.prepared-${operation}/extension`); extension(stage, next);
  const nextArchive = archive(dir, next);
  fs.writeFileSync(path.join(dir, 'synthetic-marker.json'), '{"preserve":true}\n');
  const oldDigests = Object.fromEntries(['compose.yaml', 'release.env', 'infra/postgres/schema.sql']
    .map(name => [name, sha(fs.readFileSync(path.join(dir, name)))]));
  const previous = { id: 'a'.repeat(32), root: dir, apiPort: 18080, buildIdentity: old, digests: oldDigests };
  const generationVersion = 'c'.repeat(64);
  const targetState = { ...previous, buildIdentity: next, version: next.softwareVersion,
    apiImage: 'sha256:' + 'd'.repeat(64), dataset: path.dirname(dataFile), inputSha256: sha(fs.readFileSync(dataFile)),
    generationVersion, generation: { generationVersion } };
  targetState.artifacts = { extensionArchive: nextArchive };
  const prepared = { operation, targetState, dataFile, apiImage: targetState.apiImage,
    nextSql: stagedSql, extensionStage: stage, composeText: 'target compose', envText: 'target env',
    sqlSha256: sha('target sql'), extensionSha256: treeDigest(stage),
    previousExtensionSha256: treeDigest(path.join(dir, 'extension')), secretSha256: sha('secret') };
  const fact = { needsRebuild: false, generationVersion, datasetVersion: 73 };
  const calls = [];
  let current;
  const callbacks = { dir, prepared, previous, fact, digestFiles: () => ({}),
    saveState: value => { current = structuredClone(value); fs.writeFileSync(path.join(dir, 'state.json'), JSON.stringify(value)); calls.push('save'); },
    setState: value => { current = value; }, compose: async (...args) => { calls.push(`compose:${args.join(' ')}`); },
    ready: async () => { calls.push('ready'); } };
  return { dir, old, next, operation, previous, prepared, fact, calls, callbacks,
    get current() { return current; }, cleanup() { fs.rmSync(dir, { recursive: true }); } };
}

test('database-fact recovery activates target only, retaining marker, installation identity, DB port and secret', async () => {
  const f = fixture();
  try {
    const marker = fs.readFileSync(path.join(f.dir, 'synthetic-marker.json'));
    const secret = fs.readFileSync(path.join(f.dir, 'secrets/app-password'));
    const result = await activatePreparedTarget(f.callbacks);
    assert.equal(result.phase, 'ready'); assert.equal(result.id, f.previous.id);
    assert.equal(result.apiPort, f.previous.apiPort); assert.deepEqual(fs.readFileSync(path.join(f.dir, 'secrets/app-password')), secret);
    assert.deepEqual(fs.readFileSync(path.join(f.dir, 'synthetic-marker.json')), marker);
    assert.equal(verifyExtension(path.join(f.dir, 'extension'), f.next, 18080), f.prepared.extensionSha256);
    assert.equal(fs.readFileSync(path.join(f.dir, 'infra/postgres/schema.sql'), 'utf8'), 'target sql');
    assert.deepEqual(f.calls.slice(-3), ['compose:up -d --no-deps --force-recreate api', 'ready', 'save']);
  } finally { f.cleanup(); }
});

test('uncommitted or unknown database fact never activates prepared target or alters files', async () => {
  for (const fact of [
    { needsRebuild: true, generationVersion: 'c'.repeat(64), datasetVersion: 73 },
    { needsRebuild: false, generationVersion: 'e'.repeat(64), datasetVersion: 73 },
    { needsRebuild: false, generationVersion: 'c'.repeat(64), datasetVersion: null },
  ]) {
    const f = fixture();
    try {
      await assert.rejects(activatePreparedTarget({ ...f.callbacks, fact }), /DEPLOYMENT_RECOVERY_FACT_DRIFT/);
      assert.deepEqual(f.calls, []);
      assert.equal(fs.readFileSync(path.join(f.dir, 'compose.yaml'), 'utf8'), 'old compose');
      assert.equal(fs.readFileSync(path.join(f.dir, 'infra/postgres/schema.sql'), 'utf8'), 'old sql');
      assert.equal(fs.existsSync(f.prepared.extensionStage), true);
    } finally { f.cleanup(); }
  }
});

test('unknown config/secret/extension drift blocks before switching application', async () => {
  for (const mutate of [
    f => fs.writeFileSync(path.join(f.dir, 'compose.yaml'), 'unknown compose'),
    f => fs.writeFileSync(path.join(f.dir, 'secrets/app-password'), 'unknown secret'),
    f => fs.writeFileSync(path.join(f.dir, 'extension/worker.js'), 'unknown worker'),
  ]) {
    const f = fixture();
    try {
      mutate(f);
      await assert.rejects(activatePreparedTarget(f.callbacks), /DEPLOYMENT_RECOVERY_(CONFIG|FACT|EXTENSION)_DRIFT/);
      assert.equal(f.calls.some(call => call.startsWith('compose:')), false);
      assert.equal(fs.readFileSync(path.join(f.dir, 'infra/postgres/schema.sql'), 'utf8'), 'old sql');
      assert.equal(fs.readFileSync(path.join(f.dir, 'release.env'), 'utf8'), 'old env');
    } finally { f.cleanup(); }
  }
});

test('state-save failure after DB fact retains target files and retry advances, never restores old app', async () => {
  const f = fixture();
  try {
    const save = f.callbacks.saveState; let once = true;
    f.callbacks.saveState = value => { if (once) { once = false; throw new Error('DISK_SAVE_FAILED'); } save(value); };
    await assert.rejects(activatePreparedTarget(f.callbacks), /DISK_SAVE_FAILED/);
    assert.equal(fs.readFileSync(path.join(f.dir, 'compose.yaml'), 'utf8'), 'target compose');
    assert.equal(fs.readFileSync(path.join(f.dir, 'infra/postgres/schema.sql'), 'utf8'), 'target sql');
    assert.equal(fs.readFileSync(path.join(f.dir, 'release.env'), 'utf8'), 'target env');
    assert.equal(verifyExtension(path.join(f.dir, 'extension'), f.next, 18080), f.prepared.extensionSha256);
    const recovered = await activatePreparedTarget(f.callbacks);
    assert.equal(recovered.phase, 'ready'); assert.equal(f.calls.filter(call => call === 'ready').length, 1);
  } finally { f.cleanup(); }
});

test('extension mismatch and immutable installation record publication remain guarded', () => {
  const f = fixture();
  try {
    assert.throws(() => verifyExtension(path.join(f.dir, 'extension'), f.next, 18080), /EXTENSION_IDENTITY_MISMATCH/);
    assert.throws(() => verifyExtension(f.prepared.extensionStage, f.next, 19000), /EXTENSION_IDENTITY_MISMATCH/);
    const record = installationRecord({ operation: f.operation, kind: 'upgrade', previous: { version: f.old.softwareVersion },
      target: f.next, startedAt: new Date().toISOString(), status: 'PASS' });
    publishRecord(f.dir, record); publishRecord(f.dir, record);
    assert.equal(fs.readdirSync(path.join(f.dir, 'installation-records')).length, 1);
    assert.throws(() => publishRecord(f.dir, { ...record, status: 'FAIL' }), /INSTALL_RECORD_CONFLICT/);
    assert.deepEqual(versionSummary({ version: f.old.softwareVersion }, f.next, null), {
      installed: f.old.softwareVersion, local: f.next.softwareVersion, latestRelease: null });
  } finally { f.cleanup(); }
});

test('shared extraction repairs only known byte-prefix leftovers and rejects unknown tree content', () => {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-shared-extract-'));
  const target = path.join(root, 'extension'), entries = new Map([['manifest.json', Buffer.from('complete')], ['assets/icon.png', Buffer.from('icon')]]);
  try {
    fs.mkdirSync(path.join(target, 'assets'), { recursive: true }); fs.writeFileSync(path.join(target, 'manifest.json'), 'com');
    materializeExtensionTree(target, entries); assertExtensionTreeMatches(target, entries);
    fs.writeFileSync(path.join(target, 'unknown'), 'private');
    assert.throws(() => materializeExtensionTree(target, entries), /EXTENSION_TARGET_EXISTS/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
