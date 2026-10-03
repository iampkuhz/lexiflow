import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { upgradeInstallation, recoverUpgrade, treeDigest, verifyExtension } from '../upgrade.mjs';
import { spawn } from 'node:child_process';
import { versionSummary } from '../versions.mjs';
import { writePreparedTree } from '../prepare-workspace.mjs';

const identity = tag => ({ schemaVersion: 1, baseVersion: '2.0.0-SNAPSHOT', softwareVersion: `2.0.0-SNAPSHOT.g${tag.repeat(7)}`, chromeVersion: '2.0.0.0', sourceCommit: tag.repeat(40).slice(0, 40), sourceSha256: 'a'.repeat(64), buildId: tag.repeat(64).slice(0, 64), dirty: false, channel: 'snapshot' });
function fixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-upgrade-'));
  const old = identity('1'), target = identity('2');
  const state = { schema: 1, id: 'b'.repeat(32), root: dir, project: 'lexiflow-local-' + 'b'.repeat(32), phase: 'ready', version: old.softwareVersion,
    buildIdentity: old, apiImage: 'sha256:' + '1'.repeat(64), postgresImage: 'postgres:17', apiPort: 18080, dbPort: 15432, dataset: 'data/synthetic',
    datasetIdentity: { version: 73, sourceSha256: 'd'.repeat(64) }, digests: { 'release.env': '', 'infra/postgres/schema.sql': 'schema', 'secrets/app-password': 'password', 'compose.yaml': '' }, artifacts: {} };
  fs.mkdirSync(path.join(dir, 'extension')); fs.mkdirSync(path.join(dir, 'infra/postgres'), { recursive: true }); fs.mkdirSync(path.join(dir, 'secrets'));
  fs.writeFileSync(path.join(dir, 'infra/postgres/schema.sql'), 'synthetic schema'); fs.writeFileSync(path.join(dir, 'secrets/app-password'), 'synthetic password');
  fs.writeFileSync(path.join(dir, 'synthetic-marker.json'), '{"marker":"preserve-me-exactly","opaque":[1,"fixture"]}\n');
  fs.writeFileSync(path.join(dir, 'release.env'), `LEXIFLOW_API_IMAGE=${state.apiImage}\n`);
  const composeBefore = 'services:\n  api:\n    command: [api]\n  postgres:\n    image: postgres:17\n';
  fs.writeFileSync(path.join(dir, 'compose.yaml'), composeBefore);
  state.digests['compose.yaml'] = hash(fs.readFileSync(path.join(dir, 'compose.yaml')));
  state.digests['release.env'] = hash(fs.readFileSync(path.join(dir, 'release.env')));
  state.digests['infra/postgres/schema.sql'] = hash(fs.readFileSync(path.join(dir, 'infra/postgres/schema.sql')));
  state.digests['secrets/app-password'] = hash(fs.readFileSync(path.join(dir, 'secrets/app-password')));
  const oldExtension = path.join(dir, 'extension'); writeExtension(oldExtension, old, state.apiPort); state.artifacts.extension = treeDigest(oldExtension);
  const calls = [];
  const stateFile = path.join(dir, 'state.json'); fs.writeFileSync(stateFile, JSON.stringify(state));
  let current = structuredClone(state), cancelled = false, failPrepare = false, failReady = false, cancelAfterReady = false;
  const ctx = { dir, loadState: () => { current = JSON.parse(fs.readFileSync(stateFile, 'utf8')); return structuredClone(current); }, saveState: value => { current = structuredClone(value); fs.writeFileSync(stateFile, JSON.stringify(current)); calls.push('save'); },
    digests: () => Object.fromEntries(Object.keys(state.digests).map(name => [name, hash(fs.readFileSync(path.join(dir, name)))])), schemaDigest: () => state.digests['infra/postgres/schema.sql'],
    verify: async value => { calls.push('verify'); assert.equal(value.id, state.id); }, checkCancelled: () => { if (cancelled) throw new Error('CANCELLED'); },
    restore: work => work(), activate: async value => { calls.push(`activate:${value.apiImage}`); }, ready: async value => { calls.push(`ready:${value.version}`); if (cancelAfterReady && value.buildIdentity.buildId === target.buildId) cancelled = true; if (failReady && value.buildIdentity.buildId === target.buildId) throw new Error('READY_FAILED'); },
    prepare: async (work, wanted) => { calls.push('prepare'); if (failPrepare) throw new Error('BUILD_FAILED'); writeExtension(path.join(work, 'extension'), wanted, state.apiPort); return { apiImage: 'sha256:' + '2'.repeat(64), artifacts: { jar: 'c'.repeat(64) } }; } };
  return { dir, old, target, state, composeBefore, calls, ctx, get current() { return JSON.parse(fs.readFileSync(stateFile, 'utf8')); }, set cancelled(v) { cancelled = v; }, set cancelAfterReady(v) { cancelAfterReady = v; }, set failPrepare(v) { failPrepare = v; }, set failReady(v) { failReady = v; } };
}
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function writeExtension(dir, id, port) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'build-identity.json'), JSON.stringify(id));
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ version: id.chromeVersion, version_name: id.softwareVersion, host_permissions: [`http://127.0.0.1:${port}/*`] }));
  fs.writeFileSync(path.join(dir, 'worker.js'), 'synthetic');
}

test('no-op never prepares artifacts', async () => { const f = fixture(); const result = await upgradeInstallation(f.ctx, f.old); assert.equal(result.updated, false); assert.ok(!f.calls.includes('prepare')); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('successful transaction preserves installation identity, DB settings and synthetic marker', async () => {
  const f = fixture(); const before = fs.readFileSync(path.join(f.dir, 'secrets/app-password'), 'utf8'); const marker = fs.readFileSync(path.join(f.dir, 'synthetic-marker.json'));
  const result = await upgradeInstallation(f.ctx, f.target); assert.equal(result.updated, true); assert.equal(f.current.id, f.state.id); assert.equal(f.current.project, f.state.project); assert.equal(f.current.apiPort, f.state.apiPort); assert.equal(f.current.dbPort, f.state.dbPort); assert.equal(f.current.postgresImage, f.state.postgresImage); assert.equal(fs.readFileSync(path.join(f.dir, 'secrets/app-password'), 'utf8'), before);
  assert.equal(fs.readFileSync(path.join(f.dir, 'infra/postgres/schema.sql'), 'utf8'), 'synthetic schema'); assert.deepEqual(fs.readFileSync(path.join(f.dir, 'synthetic-marker.json')), marker); assert.deepEqual(f.current.datasetIdentity, f.state.datasetIdentity); assert.equal(f.current.buildIdentity.baseVersion, f.target.baseVersion); assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false);
  const compose = fs.readFileSync(path.join(f.dir, 'compose.yaml'), 'utf8'); assert.match(compose, /command: \[api\][\s\S]*logging:[\s\S]*max-size: 10mb/); assert.match(compose, /postgres:\n    image: postgres:17/);
  const record = JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', fs.readdirSync(path.join(f.dir, 'installation-records'))[0]), 'utf8')); assert.equal(record.status, 'PASS'); assert.equal(record.buildId, f.target.buildId); assert.equal(f.current.buildIdentity.buildId, record.buildId); assert.equal(verifyExtension(path.join(f.dir, 'extension'), f.target, f.state.apiPort), f.current.artifacts.extension); assert.deepEqual(record.dataset, f.state.datasetIdentity); assert.equal(fs.readdirSync(path.join(f.dir, 'installation-records')).length, 1); assert.ok(f.calls.indexOf('prepare') < f.calls.findIndex(call => call.startsWith('activate:'))); fs.rmSync(f.dir, { recursive: true, force: true });
});
test('build failure does not activate or stop old API', async () => { const f = fixture(); f.failPrepare = true; await assert.rejects(upgradeInstallation(f.ctx, f.target), /BUILD_FAILED/); assert.equal(f.calls.some(call => call.startsWith('activate:')), false); assert.equal(f.current.buildIdentity.buildId, f.old.buildId); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('partial prepared write failure is cleaned and retry uses the same private workspace slot', async () => {
  const f = fixture(); let attempts = 0; const prepare = f.ctx.prepare;
  f.ctx.prepare = async (work, target, previous, persist) => {
    attempts++;
    if (attempts === 1) {
      await assert.rejects(writePreparedTree(work, [{ path: 'extension/partial.js', bytes: Buffer.from('planned bytes') }], async plan => { await persist(plan); if (plan.files[0].pending) throw new Error('COPY_INTERRUPTED'); }), /COPY_INTERRUPTED/);
      throw new Error('COPY_INTERRUPTED');
    }
    return prepare(work, target, previous, persist);
  };
  await assert.rejects(upgradeInstallation(f.ctx, f.target), /COPY_INTERRUPTED/);
  assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false);
  const slots = fs.readdirSync(f.dir).filter(name => name.startsWith('.upgrade-'));
  assert.equal(slots.length, 0, 'known partial workspace must not accumulate');
  assert.equal((await upgradeInstallation(f.ctx, f.target)).updated, true);
  assert.equal(attempts, 2); fs.rmSync(f.dir, { recursive: true, force: true });
});
test('fixed journal temp with no journal blocks without accumulating another workspace', async () => {
  const f = fixture(), temp = path.join(f.dir, 'upgrade.json.next'); fs.writeFileSync(temp, '{partial');
  await assert.rejects(upgradeInstallation(f.ctx, f.target), /UPGRADE_JOURNAL_TEMP_DRIFT/);
  await assert.rejects(recoverUpgrade(f.ctx), /UPGRADE_JOURNAL_INVALID|UPGRADE_JOURNAL_TEMP_DRIFT/);
  assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false);
  assert.equal(fs.readdirSync(f.dir).filter(name => name.startsWith('.upgrade-')).length, 0);
  assert.equal(fs.readFileSync(temp, 'utf8'), '{partial'); fs.rmSync(f.dir, { recursive: true, force: true });
});
test('readiness failure restores API, extension and state and records failure', async () => { const f = fixture(); f.failReady = true; await assert.rejects(upgradeInstallation(f.ctx, f.target), /READY_FAILED/); assert.equal(f.current.buildIdentity.buildId, f.old.buildId); assert.equal(verifyExtension(path.join(f.dir, 'extension'), f.old, f.state.apiPort), f.state.artifacts.extension); assert.equal(fs.readFileSync(path.join(f.dir, 'release.env'), 'utf8'), `LEXIFLOW_API_IMAGE=${f.state.apiImage}\n`); const record = JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', fs.readdirSync(path.join(f.dir, 'installation-records'))[0]), 'utf8')); assert.equal(record.status, 'FAIL'); assert.equal(record.buildId, f.target.buildId); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('cancel before switching restores and allows retry', async () => { const f = fixture(); f.cancelled = true; await assert.rejects(upgradeInstallation(f.ctx, f.target), /CANCELLED/); assert.equal(f.current.buildIdentity.buildId, f.old.buildId); f.cancelled = false; assert.equal((await upgradeInstallation(f.ctx, f.target)).updated, true); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('ready error plus failed recovery retains journal; retry recovers and rejects unknown config drift', async () => {
  const f = fixture(); f.failReady = true; f.ctx.restore = async () => { throw new Error('recovery unavailable'); }; await assert.rejects(upgradeInstallation(f.ctx, f.target), /UPGRADE_RECOVERY_REQUIRED/);
  const journal = JSON.parse(fs.readFileSync(path.join(f.dir, 'upgrade.json'), 'utf8')); assert.equal(journal.phase, 'switching');
  const temp = path.join(f.dir, 'upgrade.json.next'); fs.writeFileSync(temp, '{partial');
  await assert.rejects(recoverUpgrade(f.ctx), /UPGRADE_JOURNAL_TEMP_DRIFT/);
  assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), true); assert.equal(fs.readFileSync(temp, 'utf8'), '{partial'); fs.unlinkSync(temp);
  fs.writeFileSync(path.join(f.dir, 'secrets/app-password'), 'unknown-drift');
  await assert.rejects(recoverUpgrade(f.ctx), /UPGRADE_CONFIG_DRIFT/);
  fs.writeFileSync(path.join(f.dir, 'secrets/app-password'), 'synthetic password');

  const recovered = await recoverUpgrade(f.ctx); assert.equal(recovered.buildIdentity.buildId, f.old.buildId); assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false); fs.rmSync(f.dir, { recursive: true, force: true });
});
test('cancellation after candidate readiness restores and permits retry', async () => { const f = fixture(); f.cancelAfterReady = true; await assert.rejects(upgradeInstallation(f.ctx, f.target), /CANCELLED/); f.cancelAfterReady = false; f.cancelled = false; assert.equal(f.current.buildIdentity.buildId, f.old.buildId); assert.equal((await upgradeInstallation(f.ctx, f.target)).updated, true); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('subprocess SIGKILL leaves disk journal recoverable at transaction boundaries', async t => {
  const childSource = String.raw`
    import fs from 'node:fs'; import path from 'node:path'; import { createHash } from 'node:crypto'; import { upgradeInstallation } from ${JSON.stringify(new URL('../upgrade.mjs', import.meta.url).href)};
    const [dir, stage, target] = process.argv.slice(1); const stateFile = path.join(dir, 'state.json'); const state = () => JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    const kill = () => process.kill(process.pid, 'SIGKILL');
    const ctx = { dir, loadState: state, saveState: value => { fs.writeFileSync(stateFile, JSON.stringify(value)); if (stage === 'state-committed') kill(); },
      digests: value => Object.fromEntries(Object.keys(value.digests).map(name => [name, createHash('sha256').update(fs.readFileSync(path.join(dir, name))).digest('hex')])), schemaDigest: () => state().digests['infra/postgres/schema.sql'],
      verify: async () => {}, checkCancelled: () => {}, restore: work => work(), activate: async () => { if (stage === 'api-switched') kill(); }, ready: async () => {},
      prepare: async (work, wanted) => { const ext = path.join(work, 'extension'); fs.mkdirSync(ext, {recursive:true}); fs.writeFileSync(path.join(ext,'build-identity.json'),JSON.stringify(wanted)); fs.writeFileSync(path.join(ext,'manifest.json'),JSON.stringify({version:wanted.chromeVersion,version_name:wanted.softwareVersion,host_permissions:['http://127.0.0.1:18080/*']})); fs.writeFileSync(path.join(ext,'worker.js'),'synthetic'); if(stage==='preparing') kill(); return {apiImage:'sha256:'+'2'.repeat(64),artifacts:{jar:'c'.repeat(64)}}; } };
    const originalRename=fs.renameSync; fs.renameSync=(from,to)=>{ originalRename(from,to); if(stage==='extension-exchange' && path.basename(from)==='extension' && path.basename(to).startsWith('previous-extension')) kill(); };
    await upgradeInstallation(ctx, JSON.parse(target));
  `;
  for (const stage of ['preparing', 'api-switched', 'extension-exchange', 'state-committed']) {
    const f = fixture(); t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
    const child = spawn(process.execPath, ['--input-type=module', '-e', childSource, f.dir, stage, JSON.stringify(f.target)], { stdio: 'ignore' });
    const exit = await new Promise((resolve, reject) => { const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error(`child timeout at ${stage}`)); }, 5000); child.once('exit', (code, signal) => { clearTimeout(timer); resolve({ code, signal }); }); child.once('error', error => { clearTimeout(timer); reject(error); }); });
    assert.equal(exit.signal, 'SIGKILL', `${stage} child must be force-killed`);
    assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), true, `${stage} must leave transaction journal`);
    const marker = fs.readFileSync(path.join(f.dir, 'synthetic-marker.json'));
    const recovered = await recoverUpgrade(f.ctx);
    const expected = stage === 'state-committed' ? f.target : f.old;
    assert.ok(recovered); assert.equal(f.current.buildIdentity.buildId, expected.buildId);
    assert.equal(verifyExtension(path.join(f.dir, 'extension'), expected, f.state.apiPort), f.current.artifacts.extension);
    assert.deepEqual(fs.readFileSync(path.join(f.dir, 'synthetic-marker.json')), marker);
    assert.deepEqual(f.current.datasetIdentity, f.state.datasetIdentity);
    assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false);
    const files = fs.readdirSync(path.join(f.dir, 'installation-records'));
    assert.equal(files.length, 1);
    const record = JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', files[0])));
    assert.equal(record.status, stage === 'state-committed' ? 'PASS' : 'FAIL');
  }
});
test('SIGKILL during a planned file append is recovered from persisted byte-prefix evidence', async t => {
  const f = fixture(); t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const childSource = String.raw`
    import fs from 'node:fs'; import path from 'node:path'; import { createHash } from 'node:crypto';
    import { upgradeInstallation } from ${JSON.stringify(new URL('../upgrade.mjs', import.meta.url).href)};
    import { writePreparedTree } from ${JSON.stringify(new URL('../prepare-workspace.mjs', import.meta.url).href)};
    const dir=process.argv[1], target=JSON.parse(process.argv[2]), stateFile=path.join(dir,'state.json');
    const state=()=>JSON.parse(fs.readFileSync(stateFile,'utf8'));
    const original=fs.writeSync; let killed=false;
    fs.writeSync=(...args)=>{ const [fd,buffer,offset,length,...rest]=args; if(Buffer.isBuffer(buffer) && buffer[0]===0x61){ const n=original(fd,buffer,offset,Math.min(length,97),...rest); if(!killed){ killed=true; process.kill(process.pid,'SIGKILL'); } return n; } return original(...args); };
    const ctx={dir,loadState:state,saveState:v=>fs.writeFileSync(stateFile,JSON.stringify(v)),digests:v=>Object.fromEntries(Object.keys(v.digests).map(n=>[n,createHash('sha256').update(fs.readFileSync(path.join(dir,n))).digest('hex')])),schemaDigest:()=>state().digests['infra/postgres/schema.sql'],verify:async()=>{},checkCancelled:()=>{},restore:work=>work(),activate:async()=>{},ready:async()=>{},prepare:async(work,_target,_previous,persist)=>{await writePreparedTree(work,[{path:'extension/large.bin',bytes:Buffer.alloc(200000,0x61)}],persist);return {apiImage:'sha256:'+'2'.repeat(64),artifacts:{}};}};
    await upgradeInstallation(ctx,target);
  `;
  const child = spawn(process.execPath, ['--input-type=module', '-e', childSource, f.dir, JSON.stringify(f.target)], { stdio: 'ignore' });
  const exit = await new Promise((resolve, reject) => { const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('child timeout during planned file append')); }, 5000); child.once('exit', (code, signal) => { clearTimeout(timer); resolve({ code, signal }); }); child.once('error', error => { clearTimeout(timer); reject(error); }); });
  assert.equal(exit.signal, 'SIGKILL');
  const journal = JSON.parse(fs.readFileSync(path.join(f.dir, 'upgrade.json'), 'utf8'));
  assert.ok(journal.preparePlan?.files?.[0]?.pending, 'pending bytes must be durable before target bytes');
  const recovered = await recoverUpgrade(f.ctx);
  assert.equal(recovered.buildIdentity.buildId, f.old.buildId);
  assert.equal(fs.existsSync(path.join(f.dir, 'upgrade.json')), false);
  assert.equal(fs.existsSync(path.join(f.dir, `.upgrade-${journal.operation}`)), false);
});
test('schema drift blocks before preparing', async () => { const f = fixture(); f.ctx.schemaDigest = () => 'different'; await assert.rejects(upgradeInstallation(f.ctx, f.target), /UPGRADE_SCHEMA_INCOMPATIBLE/); assert.ok(!f.calls.includes('prepare')); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('extension target identity and port mismatch are rejected', () => { const f = fixture(), dir = path.join(f.dir, 'bad'); writeExtension(dir, identity('3'), f.state.apiPort); assert.throws(() => verifyExtension(dir, f.target, f.state.apiPort), /EXTENSION_IDENTITY_MISMATCH/); writeExtension(dir, f.target, 19000); assert.throws(() => verifyExtension(dir, f.target, f.state.apiPort), /EXTENSION_IDENTITY_MISMATCH/); fs.rmSync(f.dir, { recursive: true, force: true }); });
test('unknown latest release remains unknown, never latest', () => { assert.equal(versionSummary({ version: 'old' }, identity('2'), null).latestRelease, null); });
