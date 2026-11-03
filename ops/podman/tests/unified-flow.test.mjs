import nodeTest from 'node:test';
let testQueue=Promise.resolve();
function test(name, run) { nodeTest(name, async context => { const prior=testQueue; let finish; testQueue=new Promise(resolve=>{finish=resolve;}); await prior; try { return await run(context); } finally { finish(); } }); }
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import net from 'node:net';
import { PassThrough } from 'node:stream';
import { execFileSync } from 'node:child_process';
import { runUnifiedDeployment } from '../installation/installation-upgrade-workflow.mjs';
import { generationCodeVersion, dataGeneration } from '../../dataset/dataset-generation-identity.mjs';
import { resolveBuildIdentity } from '../../release/integrity/source-build-identity.mjs';
import { makeZip } from '../../release/tests/zip-fixture.mjs';
import { EXTENSION_PACKAGE_FILES as PACKAGE_FILES } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';
import { createExtensionZip } from '../../../extension/scripts/packaging/package-extension-release.mjs';
import { readExtensionFiles } from '../../release/integrity/extension-archive-integrity.mjs';

const root = path.resolve(import.meta.dirname, '../../..');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const image = letter => `sha256:${letter.repeat(64)}`;
function unusedPort() { return new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)); }); }); }
function write(file, content) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, content); }
function extensionFiles(identity, apiPort) {
  const files = apiPort === 18081 ? PACKAGE_FILES.filter(name => name !== 'expected-dataset.json') : PACKAGE_FILES;
  return new Map(files.map(name => [name, Buffer.from(name === 'manifest.json'
    ? JSON.stringify({ manifest_version: 3, version: identity.chromeVersion, version_name: identity.softwareVersion,
      permissions: ['storage'], host_permissions: [`http://127.0.0.1:${apiPort}/*`],
      background: { service_worker: 'background.js' }, action: { default_popup: 'popup.html' } })
    : name === 'build-identity.json' ? JSON.stringify(identity)
    : name === 'popup.html' ? '<link href="popup.css"><script src="popup.js"></script>' : `synthetic:${name}`)]));
}
function writeExtension(directory, files) { for (const [name, bytes] of files) write(path.join(directory, name), bytes); }
function fixture({ apiPort = 18081, failReady = false, failApply = false, structuredFailure = false, sourceDriftDuringBuild = false } = {}) {
  const base = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-flow-'));
  const repo = path.join(base, 'source'), dir = path.join(base, 'install'); fs.mkdirSync(repo);
  write(path.join(repo, '.gitignore'), 'tmp/\nbackend/product/api/build/\nextension/dist/\nextension/node_modules/\n');
  write(path.join(repo, 'fixture.txt'), 'fixed source\n');
  write(path.join(repo, 'application.txt'), 'fixed application\n');
  write(path.join(repo, 'ops/release/version.txt'), '9.0.0-SNAPSHOT\n');
  write(path.join(repo, 'ops/dataset/generation-inputs.json'), JSON.stringify({ schemaVersion: 1, include: ['fixture.txt'] }));
  fs.copyFileSync(path.join(root, 'ops/dataset/dataset-generation-identity.mjs'), path.join(repo, 'ops/dataset/dataset-generation-identity.mjs'));
  fs.copyFileSync(path.join(root, 'ops/dataset/ecdict-source.lock.json'), path.join(repo, 'ops/dataset/ecdict-source.lock.json'));
  fs.mkdirSync(path.join(repo, 'ops/docker'), { recursive: true });
  for (const name of ['Dockerfile', 'run-container-command.sh']) fs.copyFileSync(path.join(root, 'ops/docker', name), path.join(repo, 'ops/docker', name));
  fs.mkdirSync(path.join(repo, 'infra/sqlite'), { recursive: true });
  fs.copyFileSync(path.join(root, 'infra/sqlite/schema.sql'), path.join(repo, 'infra/sqlite/schema.sql'));
  execFileSync('git', ['init', '-q', repo]); execFileSync('git', ['-C', repo, 'config', 'user.email', 'fixture@example.invalid']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'Fixture']); execFileSync('git', ['-C', repo, 'add', '-A']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'fixture']);
  let identity = resolveBuildIdentity(repo), committedGeneration, rejectJavaBasePull = false, failReadyNow = failReady;
  const calls = [], saved = [];
  const csv = Buffer.from('word,translation,oxford,tag,bnc,frq,exchange\nfixed,固定,1,,1,1,\n');
  const locked = JSON.parse(fs.readFileSync(path.join(repo, 'ops/dataset/ecdict-source.lock.json'), 'utf8'));
  const upstream = { repository_url: locked.repository_url, commit: locked.commit, files: locked.files,
    archive_member: { sha256: locked.archive_member.sha256, bytes: locked.archive_member.bytes } };
  let generation, created = false, sourceToolsOwner = null;
  async function setup() {

    generation = dataGeneration({ codeVersion: await generationCodeVersion(repo), inputSha256: hash(csv), upstream });

  }
  async function run(command, argv, { env } = {}) {
    calls.push(`${command}:${argv.join(' ')}`);
    if (command === './gradlew') { identity = resolveBuildIdentity(repo); write(path.join(repo, 'backend/product/api/build/internal/boot/api-internal.jar'), 'synthetic jar'); return ''; }
    if (command === 'npm') {
      if (argv[0] === 'run') {
        if (sourceDriftDuringBuild) {
          write(path.join(repo, 'application.txt'), 'changed application during build\n');
          identity = resolveBuildIdentity(repo);
        }
        writeExtension(path.join(repo, 'extension/dist'), extensionFiles(identity, Number(env.LEXIFLOW_API_PORT)));
      }
      return '';
    }
    if (command === 'java') { const file=argv[argv.indexOf('--expected-file')+1]; return JSON.stringify({status:'PASS',databaseFile:file,fingerprint:'f'.repeat(64),needsRebuild:committedGeneration !== generation.generationVersion,reason:!committedGeneration?'DATABASE_ABSENT':committedGeneration!==generation.generationVersion?'GENERATION_CHANGED':'OK',generationVersion:committedGeneration??null,datasetVersion:committedGeneration?7:null}); }
    if (command !== 'podman') throw new Error('unexpected command');
    if (argv[0] === 'build' && argv.includes('--iidfile')) {
      write(argv[argv.indexOf('--iidfile') + 1], image('e'));
      sourceToolsOwner = argv.find(value => value.startsWith('io.lexiflow.source-tools-owner='))?.split('=')[1]; return '';
    }
    if (argv[0] === 'image' && argv[1] === 'exists') throw Object.assign(new Error('image absent'), { commandExitCode: 1 });
    if (argv[0] === 'pull' && argv.at(-1) === 'docker.io/library/eclipse-temurin:25-jre') { if(rejectJavaBasePull) throw Object.assign(new Error('unavailable'),{commandExitCode:125}); return ''; }
    if (argv[0] === 'image' && argv[1] === 'inspect' && argv.includes('{{json .Labels}}') && sourceToolsOwner)
      return JSON.stringify({ 'io.lexiflow.source-tools-owner': sourceToolsOwner });
    if (argv[0] === 'rmi' || argv[0] === 'tag') return '';
    if (argv[0] === 'run' && argv.includes('/kit/ops/dataset/prepare_ecdict_bundle.py')) {
      const mount = argv.find(arg => arg.endsWith(':/data'));
      const dataRoot = path.join(mount.slice(0, -6), 'ecdict-source.ABC123');
      write(path.join(dataRoot, 'stardict.csv'), csv);
      generation = dataGeneration({ codeVersion: await generationCodeVersion(repo), inputSha256: hash(csv), upstream });
      write(path.join(dataRoot, 'source-summary.json'), JSON.stringify({ csv_sha256: hash(csv), generation }));
      return 'dataset_dir=/data/ecdict-source.ABC123\n';
    }
    if (argv[0] === 'run' && argv.includes('--deployment-data')) {
      const expected=argv[argv.indexOf('--expected-file')+1];
      if (argv.includes('apply')) { if (structuredFailure) throw Object.assign(new Error('safe command exit'), { commandExitCode: 1, stdout: JSON.stringify({status:'FAIL',reason:'REQUEST_OR_DATA_INVALID'}) }); if(failApply)throw new Error('SYNTHETIC_DB_APPLY_FAILURE'); committedGeneration=generation.generationVersion;
        const mount=argv.find(value=>value.includes(':/db:rw')); const host=mount.slice(0,mount.indexOf(':/db:')); const db=path.join(host,'lexicon.db'); write(db,Buffer.concat([Buffer.from('SQLite format 3\0'),Buffer.alloc(100)]));
        return JSON.stringify({status:'PASS',databaseFile:expected,fingerprint:'f'.repeat(64),needsRebuild:false,generationVersion:generation.generationVersion,datasetVersion:7}); }
      return JSON.stringify({status:'PASS',databaseFile:expected,fingerprint:'f'.repeat(64),needsRebuild:committedGeneration!==generation.generationVersion,reason:!committedGeneration?'DATABASE_ABSENT':'GENERATION_CHANGED',generationVersion:committedGeneration??null,datasetVersion:committedGeneration?7:null});
    }
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      const ref = argv[2];
      if (argv.includes('--format') && argv[argv.indexOf('--format') + 1] === 'json' && ref.includes('/library/')) {
        const java = ref === 'docker.io/library/eclipse-temurin:25-jre';
        return JSON.stringify([{ Id: image(java ? 'a' : 'b'), Os: 'linux', Architecture: 'arm64', Config: { Env: java
          ? ['JAVA_VERSION=jdk-25.0.1+1'] : [] } }]);
      }
      return image('e');
    }
    return '';
  }
  function ctx({ planOnly = false, confirmPlan, confirmInput, confirmOutput } = {}) {
    let current;
    return { repo, dir, action: 'install', apiPort, seen: new Set(),
      // 编排 fixture 不读取真实 API 监听者；数据库占用场景仍使用独立测试端口。
      portTaken: port => port === apiPort ? Promise.resolve(false) : new Promise(resolve => {
        const server = net.createServer();
        server.once('error', error => resolve(error.code === 'EADDRINUSE'));
        server.listen(port, '127.0.0.1', () => server.close(() => resolve(false)));
      }),
      environment: { PATH: process.env.PATH }, javaEnvironment: env => env, run,
      ready: async () => { calls.push('ready'); if (failReadyNow) throw new Error('SYNTHETIC_API_FAILURE'); return 7; },
      compose: async (...args) => { calls.push(`compose:${args.join(' ')}`); return ''; },
      ownedResources: async () => new Set(), verifyConfig: () => {}, verifyApiPort: async () => {},
      acquireLock: () => calls.push('lock'), digestFiles: () => ({}), imageLabels: () => [],
      saveState: value => { current = value; saved.push(structuredClone(value)); write(path.join(dir, 'state.json'), JSON.stringify(value)); },
      setState: value => { current = value; }, getState: () => current, progress: text => calls.push(`progress:${text}`),
      planOnly, confirmPlan, confirmInput, confirmOutput,
      interrupted: () => false, setLogFile: () => {} };
  }
  async function plan(action = 'install') {
    const original = console.log; let planned; const existed = fs.existsSync(dir);
    const recordRoot = path.join(dir, 'installation-records');
    const recordsBefore = fs.existsSync(recordRoot) ? fs.readdirSync(recordRoot).sort() : [];
    console.log = value => { try { const parsed = JSON.parse(value); if (parsed.planHash) planned = parsed; } catch {} };
    try { assert.equal(await runUnifiedDeployment({ ...ctx({ planOnly: true }), action }), 0); }
    finally { console.log = original; }
    assert.ok(planned?.planHash); if (!existed) assert.equal(fs.existsSync(dir), false);
    assert.deepEqual(fs.existsSync(recordRoot) ? fs.readdirSync(recordRoot).sort() : [], recordsBefore,
      'plan-only must not publish an installation record');
    return planned;
  }
  return { base, repo, dir, calls, saved, get identity() { return identity; }, setup, ctx, plan,
    changeSource: () => write(path.join(repo, 'fixture.txt'), 'changed generation input\n'),
    changeApplication: () => write(path.join(repo, 'application.txt'), 'changed application\n'),
    setFailReady: value => { failReadyNow = value; },
    rejectJavaBasePull: () => { rejectJavaBasePull = true; },
    cleanup: () => fs.rmSync(base, { recursive: true }) };
}

test('source actual planner uses Java inspect, then applies through built API image before activation', async () => {
  const f = fixture();
  try {
    await f.setup(); const { plan, planHash } = await f.plan();
    assert.equal(plan.actions.dataRebuild, true);
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), 0);
    assert.equal(f.calls.some(call => call.includes('--deployment-data inspect')), false, 'missing inspect target is handled without creating or opening a DB');
    assert.ok(f.calls.some(call => call.startsWith('podman:run') && call.includes('--deployment-data apply')));
    assert.ok(f.calls.filter(call => call.startsWith('podman:run') && call.includes('--deployment-data')).every(call =>
      call.includes('--cap-drop=ALL --cap-add=DAC_OVERRIDE') && call.includes('--read-only') && call.includes('--security-opt=no-new-privileges')));
    const sourceRuns = f.calls.filter(call => call.startsWith('podman:run') && call.includes('/kit/ops/dataset/prepare_ecdict_bundle.py'));
    assert.equal(sourceRuns.length, 2, 'plan and confirmed execution each prepare the frozen source');
    assert.ok(sourceRuns.every(call => call.endsWith('/kit/ops/dataset/prepare_ecdict_bundle.py /kit /data')
      && call.includes('LEXIFLOW_GENERATION_CODE_VERSION=') && !call.includes('DAC_OVERRIDE') && !call.includes('--entrypoint')));
    assert.ok(f.calls.indexOf('compose:up -d --no-deps --force-recreate api') > f.calls.findIndex(call => call.startsWith('podman:run') && call.includes('--deployment-data apply')));
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.phase, 'ready'); assert.equal(state.generationVersion.length, 64);
    const records = fs.readdirSync(path.join(f.dir, 'installation-records')).map(file => JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', file))));
    assert.equal(records.length, 1); assert.equal(records[0].status, 'PASS');
    assert.deepEqual(records[0], state.lastOperation);
    assert.ok(Date.parse(records[0].startedAt) <= Date.parse(records[0].completedAt));
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'complete');
  } finally { f.cleanup(); }
});

test('missing Java base fails before source API image replacement or stop', async () => {
  const f = fixture();
  try {
    await f.setup(); const baseline = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: baseline.planHash })), 0);
    f.changeApplication(); f.rejectJavaBasePull();
    const input = new PassThrough(), output = new PassThrough(); input.isTTY = true; output.isTTY = true; input.end('y\n');
    const prior = f.calls.length, retry = f.ctx({ confirmInput: input, confirmOutput: output });
    retry.action = 'upgrade';
    await assert.rejects(runUnifiedDeployment(retry), /SOURCE_BASE_IMAGE_UNAVAILABLE/);
    const calls = f.calls.slice(prior);
    assert.equal(calls.some(value => value.startsWith('podman:stop ') || value.startsWith('podman:rm ') || value.startsWith('podman:rmi ')), false);
    assert.equal(calls.some(value => value.includes('image exists localhost/')), false);
  } finally { f.cleanup(); }
});

test('API failure after committed upgrade leaves forward-only journal and records exact target with preserved dataset', async () => {
  const f = fixture();
  try {
    await f.setup(); const initial = await f.plan();
    await runUnifiedDeployment(f.ctx({ confirmPlan: initial.planHash }));
    const baseline = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    f.changeApplication(); f.changeSource(); f.setFailReady(true);
    const { planHash } = await f.plan('upgrade');
    await assert.rejects(runUnifiedDeployment({ ...f.ctx({ confirmPlan: planHash }), action: 'upgrade' }), /SYNTHETIC_API_FAILURE/);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.phase, 'initialized');
    const records = fs.readdirSync(path.join(f.dir, 'installation-records')).map(file => JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', file))));
    assert.equal(records.length, 2);
    const failed = records.find(record => record.status === 'FAIL');
    assert.ok(failed); assert.equal(failed.kind, 'upgrade');
    assert.equal(failed.buildId, f.identity.buildId); assert.equal(failed.sourceCommit, f.identity.sourceCommit);
    assert.equal(failed.previousVersion, baseline.version); assert.equal(failed.targetVersion, f.identity.softwareVersion);
    assert.deepEqual(failed.dataset, state.datasetIdentity, 'failure record preserves the confirmed data commit fact');
    assert.notDeepEqual(failed.dataset, baseline.datasetIdentity, 'committed target dataset replaces the previous fact');
    assert.ok(Date.parse(failed.startedAt) <= Date.parse(failed.completedAt));
    assert.equal(state.lastOperation.operation, baseline.lastOperation.operation, 'failed operation must not overwrite the prior PASS operation');
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'application-activated');
    assert.equal(f.calls.filter(call => call.startsWith('progress:')).at(-1), 'progress:验证应用就绪');
    assert.equal(f.calls.some(call => call.includes('old-api') || call.includes('rollback')), false);
  } finally { f.cleanup(); }
});

test('failed DB apply reports the data phase and never activates the API', async () => {
  const f = fixture({ failApply: true });
  try {
    await f.setup(); const { planHash } = await f.plan();
    await assert.rejects(runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), /SYNTHETIC_DB_APPLY_FAILURE/);
    assert.equal(f.calls.filter(call => call.startsWith('progress:')).at(-1), 'progress:核对或提交资料');
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'application-stopped');
    assert.equal(f.calls.some(call => call.includes('force-recreate api')), false);
    const records = fs.readdirSync(path.join(f.dir, 'installation-records')).map(file => JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', file))));
    assert.equal(records.length, 1); assert.equal(records[0].status, 'FAIL');
    assert.equal(records[0].dataset, null, 'failed apply cannot invent a committed dataset');
  } finally { f.cleanup(); }
});

test('failure record write failure remains a failure and retains the operation cause', async () => {
  const f = fixture();
  try {
    await f.setup(); const { planHash } = await f.plan();
    const ctx = f.ctx({ confirmPlan: planHash });
    const original = new Error('SYNTHETIC_API_FAILURE');
    ctx.ready = async () => {
      fs.writeFileSync(path.join(f.dir, 'installation-records'), 'record destination is not a directory');
      throw original;
    };
    await assert.rejects(runUnifiedDeployment(ctx), error => {
      assert.equal(error.name, 'AggregateError');
      assert.equal(error.message, 'DEPLOYMENT_FAILURE_RECORD_WRITE_FAILED');
      assert.equal(error.cause, original);
      assert.equal(error.errors[0], original);
      assert.equal(error.errors[1].message, 'INSTALL_DIRECTORY_INVALID');
      return true;
    });
  } finally { f.cleanup(); }
});

test('ordinary interaction asks once before heavy work and emits no machine plan JSON', async () => {
  const f = fixture();
  try {
    await f.setup();
    const input = new PassThrough(); input.isTTY = true; const output = { isTTY: true, text: '', write(value) { this.text += value; } };
    input.write('n\n');
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmInput: input, confirmOutput: output })), 0);
    assert.equal(f.calls.filter(call => !String(call).startsWith('progress:')).length, 0); assert.equal(fs.existsSync(f.dir), false);
    assert.match(output.text, /SQLite 文件[\s\S]*\[y\/N\]/);
    const yes = new PassThrough(); yes.isTTY = true; yes.write('yes\n');
    const lines = [], original = console.log; console.log = value => lines.push(String(value));
    try { assert.equal(await runUnifiedDeployment(f.ctx({ confirmInput: yes, confirmOutput: output })), 0); }
    finally { console.log = original; }
    assert.equal(lines.some(line => line.startsWith('{') && line.includes('planHash')), false);
    assert.equal(f.calls.filter(call => call.startsWith('./gradlew:')).length, 1);
  } finally { f.cleanup(); }
});

test('non-TTY, EOF and cancellation block before build, connection or state writes', async () => {
  const f = fixture();
  try {
    await f.setup();
    const output = { isTTY: true, write() {} };
    await assert.rejects(runUnifiedDeployment(f.ctx({ confirmInput: { isTTY: false }, confirmOutput: output })), /DEPLOYMENT_CONFIRMATION_REQUIRED/);
    const ended = new PassThrough(); ended.isTTY = true;
    const pending = runUnifiedDeployment(f.ctx({ confirmInput: ended, confirmOutput: output })); ended.end();
    await assert.rejects(pending, /DEPLOYMENT_CONFIRMATION_EOF/);
    const controller = new AbortController(); controller.abort();
    await assert.rejects(runUnifiedDeployment({ ...f.ctx({ confirmInput: ended, confirmOutput: output }), signal: controller.signal }), /DEPLOYMENT_CONFIRMATION_CANCELLED/);
    assert.equal(f.calls.filter(call => !String(call).startsWith('progress:')).length, 0); assert.equal(fs.existsSync(f.dir), false);
    assert.equal(fs.existsSync(path.join(f.dir, 'installation-records')), false);
  } finally { f.cleanup(); }
});

test('application-only source change updates app but skips data apply', async () => {
  const f = fixture();
  try {
    await f.setup(); const initial = await f.plan(); await runUnifiedDeployment(f.ctx({ confirmPlan: initial.planHash }));
    f.changeApplication(); const planned = await f.plan();
    assert.equal(planned.plan.actions.applicationUpdate, true);
    assert.equal(planned.plan.actions.dataRebuild, false);
    const before = f.calls.length; await runUnifiedDeployment(f.ctx({ confirmPlan: planned.planHash }));
    assert.equal(f.calls.slice(before).filter(call => call.includes('--deployment-data apply')).length, 0);
  } finally { f.cleanup(); }
});

test('source main path plans GENERATION_CHANGED and rebuilds once without an alternate executor', async () => {
  const f = fixture();
  try {
    await f.setup(); const initial = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: initial.planHash })), 0);
    const before = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    f.changeSource(); const changed = await f.plan();
    assert.equal(changed.plan.actions.dataReason, 'GENERATION_CHANGED');
    assert.equal(changed.plan.actions.dataRebuild, true);
    assert.notEqual(changed.planHash, initial.planHash);
    const offset = f.calls.length;
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: changed.planHash })), 0);
    const after = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.notEqual(after.generationVersion, before.generationVersion);
    assert.equal(f.calls.slice(offset).filter(call => call.includes('--deployment-data apply')).length, 1);
    assert.equal(after.id, before.id);
  } finally { f.cleanup(); }
});

test('nonzero data protocol failure keeps its category and cannot activate application', async () => {
  const f = fixture({ structuredFailure: true });
  try {
    await f.setup(); const { planHash } = await f.plan();
    await assert.rejects(runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), /DEPLOYMENT_DATABASE_FAIL:REQUEST_OR_DATA_INVALID/);
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'application-stopped');
    assert.equal(f.calls.some(call => call.includes('force-recreate api')), false);
  } finally { f.cleanup(); }
});

test('cached upstream file identity ignores JSON key order without reimporting', async () => {
  const f = fixture();
  try {
    await f.setup(); const first = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: first.planHash })), 0);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    const file = path.join(state.dataset, 'source-summary.json');
    const summary = JSON.parse(fs.readFileSync(file));
    summary.generation.upstream.files = Object.fromEntries(Object.entries(summary.generation.upstream.files).reverse());
    fs.writeFileSync(file, JSON.stringify(summary));
    const applies = f.calls.filter(call => call.includes('--deployment-data apply')).length;
    const next = await f.plan();
    assert.equal(next.plan.actions.dataRebuild, false);
    assert.equal(await runUnifiedDeployment({ ...f.ctx({ confirmPlan: next.planHash }), action: 'install' }), 0);
    assert.equal(f.calls.filter(call => call.includes('--deployment-data apply')).length, applies);
    summary.generation.upstream.files.LICENSE = '0'.repeat(64);
    fs.writeFileSync(file, JSON.stringify(summary));
    await assert.rejects(f.plan(), /DEPLOYMENT_SOURCE_DRIFT/);
  } finally { f.cleanup(); }
});

test('source drift during extension build blocks before API image build, database writes or application stop', async () => {
  const f = fixture({ sourceDriftDuringBuild: true });
  try {
    await f.setup(); const { planHash } = await f.plan(); const before = f.calls.length;
    await assert.rejects(runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), /DEPLOYMENT_INPUT_DRIFT/);
    const executionCalls = f.calls.slice(before);
    assert.equal(executionCalls.some(call => call.startsWith('podman:build') && call.includes('-t localhost/lexiflow-api:')), false);
    assert.equal(f.calls.some(call => call.includes('--deployment-data apply')), false);
    assert.equal(f.calls.some(call => call.startsWith('compose:')), false);
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'planned');
  } finally { f.cleanup(); }
});

test('ordinary consent blocks source changes after yes before the first build identity read', async () => {
  const f = fixture();
  try {
    await f.setup();
    const input = new PassThrough(); input.isTTY = true; input.write('y\n');
    const ctx = f.ctx({ confirmInput: input, confirmOutput: { isTTY: true, write() {} } });
    const progress = ctx.progress;
    ctx.progress = text => {
      progress(text);
      if (text === '确认完成，开始连接目标与准备阶段') f.changeApplication();
    };
    await assert.rejects(runUnifiedDeployment(ctx), /DEPLOYMENT_INPUT_DRIFT/);
    assert.equal(f.calls.some(call => call.startsWith('./gradlew:') || call.startsWith('podman:run')), false);
    assert.equal(fs.existsSync(f.dir), false);
  } finally { f.cleanup(); }
});

test('unchanged source refresh always prepares and restarts application without reimporting data', async () => {
  const f = fixture();
  try {
    await f.setup(); const initial = await f.plan();
    await runUnifiedDeployment(f.ctx({ confirmPlan: initial.planHash }));
    const initialState = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    let operation = initialState.lastOperation.operation;
    const initialArchive = initialState.artifacts.extensionArchive;
    for (const action of ['install', 'upgrade']) {
      const planned = await f.plan(action);
      assert.equal(planned.plan.actions.applicationReason, 'INSTALL_REFRESH');
      assert.equal(planned.plan.actions.applicationUpdate, true);
      assert.equal(planned.plan.actions.applicationStart, true);
      assert.equal(planned.plan.actions.dataRebuild, false);
      const offset = f.calls.length;
      assert.equal(await runUnifiedDeployment({ ...f.ctx({ confirmPlan: planned.planHash }), action }), 0);
      const execution = f.calls.slice(offset);
      assert.equal(execution.filter(call => call.includes('--deployment-data apply')).length, 0);
      assert.equal(execution.filter(call => call === 'compose:stop api').length, 1);
      assert.equal(execution.filter(call => call === 'compose:up -d --no-deps --force-recreate api').length, 1);
      assert.equal(execution.filter(call => call === 'ready').length, 1);
      assert.equal(execution.filter(call => call.startsWith('npm:run build')).length, 1);
      assert.equal(execution.filter(call => call.startsWith('podman:build') && call.includes('-t localhost/lexiflow-api:')).length, 1);
      const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
      assert.equal(state.phase, 'ready');
      assert.equal(state.id, initialState.id);
      assert.equal(state.generationVersion, initialState.generationVersion);
      assert.notEqual(state.lastOperation.operation, operation);
      assert.equal(state.lastOperation.kind, action);
      assert.deepEqual(state.artifacts.extensionArchive, initialArchive);
      assert.deepEqual(state.lastOperation.artifacts.extensionArchive, state.artifacts.extensionArchive);
      const zipPath = path.join(f.dir, state.artifacts.extensionArchive.path);
      assert.equal(hash(fs.readFileSync(zipPath)), state.artifacts.extensionArchive.sha256);
      const files = await readExtensionFiles(zipPath, state.artifacts.extensionArchive.sha256);
      assert.deepEqual([...files.keys()].sort(), [...PACKAGE_FILES.filter(name => name !== 'expected-dataset.json')].sort());
      for (const [name, bytes] of files) assert.deepEqual(fs.readFileSync(path.join(f.dir, 'extension', name)), bytes);
      assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'complete');
      operation = state.lastOperation.operation;
    }
  } finally { f.cleanup(); }
});

for (const apiPort of [18081]) test(`source installation packages API port ${apiPort} with the shared release packer`, async () => {
  const f = fixture({ apiPort });
  try {
    await f.setup(); const { planHash } = await f.plan();
    await runUnifiedDeployment(f.ctx({ confirmPlan: planHash }));
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json'))), descriptor = state.artifacts.extensionArchive;
    const result = await createExtensionZip({ root: path.join(f.repo, 'extension'), buildIdentity: state.buildIdentity, apiPort });
    const zipPath = path.join(f.dir, descriptor.path);
    assert.deepEqual(fs.readFileSync(zipPath), Buffer.from(result.archive));
    assert.equal(descriptor.sha256, result.descriptor.sha256);
    assert.equal(descriptor.bytes, result.descriptor.bytes);
    assert.deepEqual(descriptor.buildIdentity, state.buildIdentity);
    assert.equal(fs.readFileSync(`${zipPath}.sha256`, 'utf8'), `${descriptor.sha256}  ${path.basename(zipPath)}\n`);
    for (const [name, bytes] of await readExtensionFiles(zipPath, descriptor.sha256)) assert.deepEqual(fs.readFileSync(path.join(f.dir, 'extension', name)), bytes);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.dir, 'extension/manifest.json'))).host_permissions, [`http://127.0.0.1:${apiPort}/*`]);
  } finally { f.cleanup(); }
});

for (const target of ['zip', 'sidecar', 'tree']) test(`tampering ${target} during readiness cannot publish a successful installation`, async () => {
  const f = fixture();
  try {
    await f.setup(); const { planHash } = await f.plan(), ctx = f.ctx({ confirmPlan: planHash });
    const originalReady = ctx.ready;
    ctx.ready = async state => {
      await originalReady(state);
      const zipPath = path.join(f.dir, state.artifacts.extensionArchive.path);
      const file = target === 'zip' ? zipPath : target === 'sidecar' ? `${zipPath}.sha256` : path.join(f.dir, 'extension/content.js');
      fs.appendFileSync(file, 'tampered');
    };
    await assert.rejects(runUnifiedDeployment(ctx), /EXTENSION_ARCHIVE_(INVALID|TREE_MISMATCH)/);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.phase, 'initialized');
    assert.equal(state.lastOperation, undefined);
    const records = fs.readdirSync(path.join(f.dir, 'installation-records')).map(file => JSON.parse(fs.readFileSync(path.join(f.dir, 'installation-records', file))));
    assert.equal(records.length, 1); assert.equal(records[0].status, 'FAIL');
    assert.equal(records[0].operation, JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).operation);
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'application-activated');
  } finally { f.cleanup(); }
});

test('ordinary retry replans failed software preparation only with unchanged data and database facts', async () => {
  const f = fixture({ apiPort: 18081, fixedDbPort: true });
  try {
    await f.setup(); const baseline = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: baseline.planHash })), 0);
    const stateFile = path.join(f.dir, 'state.json'), state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    const generation = state.generationVersion, inputSha256 = state.inputSha256;
    const marker = { schema: 1, root: f.dir, installationId: state.id, stateSha256: hash(fs.readFileSync(stateFile)),
      targetBuildId: state.buildIdentity.buildId, inputSha256, generationVersion: generation,
      baseVersion: state.buildIdentity.baseVersion, databaseFingerprint: 'f'.repeat(64), databaseTarget: 'file',
      allowedEnvSha256: [hash(fs.readFileSync(path.join(f.dir, 'release.env')))] };
    write(path.join(f.dir, 'image-replacement.json'), JSON.stringify(marker));
    write(path.join(f.dir, 'deployment.json'), JSON.stringify({ schema: 1, operation: '1'.repeat(32),
      planHash: '2'.repeat(64), stage: 'planned' }));
    try { fs.unlinkSync(path.join(f.dir, 'prepared-target.json')); } catch {}
    f.changeApplication();
    const input = new PassThrough(), output = new PassThrough(); input.isTTY = true; output.isTTY = true; input.end('y\n');
    const retry = f.ctx({ confirmInput: input, confirmOutput: output });
    Object.assign(retry, { action: 'install', apiPort: 18081 });
    assert.equal(await runUnifiedDeployment(retry), 0);
    const after = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    assert.equal(after.apiPort, 18081); assert.equal(after.generationVersion, generation);
    assert.equal(after.inputSha256, inputSha256);
    assert.equal(f.calls.filter(value => value.includes('--deployment-data apply')).length, 1, 'retry reuses verified database success');
    assert.equal(fs.existsSync(path.join(f.dir, 'image-replacement.json')), false);
    assert.equal(fs.readdirSync(f.dir).some(name => /^image-replacement-[a-f0-9-]+-previous\.json$/.test(name)), true);
  } finally { f.cleanup(); }
});

test('failed-preparation replan rejects changed input, generation, base version, or database fingerprint', async () => {
  const f = fixture({ apiPort: 18081, fixedDbPort: true });
  try {
    await f.setup(); const baseline = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: baseline.planHash })), 0);
    const stateFile = path.join(f.dir, 'state.json'), state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    f.changeApplication();
    const variants = [
      ['inputSha256', 'a'.repeat(64)], ['generationVersion', 'b'.repeat(64)], ['baseVersion', 'changed'],
      ['databaseFingerprint', 'c'.repeat(64)],
    ];
    for (const [field, changed] of variants) {
      const marker = { schema: 1, root: f.dir, installationId: state.id, stateSha256: hash(fs.readFileSync(stateFile)),
        targetBuildId: state.buildIdentity.buildId, inputSha256: state.inputSha256,
        generationVersion: state.generationVersion, baseVersion: state.buildIdentity.baseVersion,
        databaseFingerprint: 'f'.repeat(64), databaseTarget: 'file',
        allowedEnvSha256: [hash(fs.readFileSync(path.join(f.dir, 'release.env')))] };
      marker[field] = changed;
      write(path.join(f.dir, 'image-replacement.json'), JSON.stringify(marker));
      write(path.join(f.dir, 'deployment.json'), JSON.stringify({ schema: 1, operation: '1'.repeat(32),
        planHash: '2'.repeat(64), stage: 'planned' }));
      try { fs.unlinkSync(path.join(f.dir, 'prepared-target.json')); } catch {}
      const input = new PassThrough(), output = new PassThrough(); input.isTTY = true; output.isTTY = true; input.end('y\n');
      const retry = f.ctx({ confirmInput: input, confirmOutput: output });
      Object.assign(retry, { action: 'install', apiPort: 18081 });
      await assert.rejects(runUnifiedDeployment(retry), /DEPLOYMENT_REPLACEMENT_TARGET_CHANGED/);
      assert.equal(JSON.parse(fs.readFileSync(stateFile, 'utf8')).apiPort, 18081);
    }
  } finally { f.cleanup(); }
});

test('same-slot 18081 failed preparation accepts the same constrained ordinary install retry', async () => {
  const f = fixture({ apiPort: 18081, fixedDbPort: true });
  try {
    await f.setup(); const baseline = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: baseline.planHash })), 0);
    const stateFile = path.join(f.dir, 'state.json'), state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    write(path.join(f.dir, 'image-replacement.json'), JSON.stringify({ schema: 1, root: f.dir, installationId: state.id,
      stateSha256: hash(fs.readFileSync(stateFile)), targetBuildId: state.buildIdentity.buildId,
      inputSha256: state.inputSha256, generationVersion: state.generationVersion,
      baseVersion: state.buildIdentity.baseVersion, databaseFingerprint: 'f'.repeat(64), databaseTarget: 'file',
      allowedEnvSha256: [hash(fs.readFileSync(path.join(f.dir, 'release.env')))] }));
    write(path.join(f.dir, 'deployment.json'), JSON.stringify({ schema: 1, operation: '3'.repeat(32), planHash: '4'.repeat(64), stage: 'planned' }));
    try { fs.unlinkSync(path.join(f.dir, 'prepared-target.json')); } catch {}
    f.changeApplication();
    const input = new PassThrough(), output = new PassThrough(); input.isTTY = true; output.isTTY = true; input.end('y\n');
    const retry = f.ctx({ confirmInput: input, confirmOutput: output });
    Object.assign(retry, { action: 'install', apiPort: 18081 });
    assert.equal(await runUnifiedDeployment(retry), 0);
    assert.equal(JSON.parse(fs.readFileSync(stateFile, 'utf8')).apiPort, 18081);
  } finally { f.cleanup(); }
});

test('failed replan preserves old intent when input or config drifts after confirmation', async () => {
  for (const drift of ['input', 'config']) {
    const f = fixture({ apiPort: 18081, fixedDbPort: true });
    try {
      await f.setup(); const baseline = await f.plan();
      assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: baseline.planHash })), 0);
      const stateFile = path.join(f.dir, 'state.json'), state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      const originalMarker = { schema: 1, root: f.dir, installationId: state.id, stateSha256: hash(fs.readFileSync(stateFile)),
        targetBuildId: state.buildIdentity.buildId, inputSha256: state.inputSha256, generationVersion: state.generationVersion,
        baseVersion: state.buildIdentity.baseVersion, databaseFingerprint: 'f'.repeat(64), databaseTarget: 'file',
        allowedEnvSha256: [hash(fs.readFileSync(path.join(f.dir, 'release.env')))] };
      write(path.join(f.dir, 'image-replacement.json'), JSON.stringify(originalMarker));
      write(path.join(f.dir, 'deployment.json'), JSON.stringify({ schema: 1, operation: '5'.repeat(32), planHash: '6'.repeat(64), stage: 'planned' }));
      try { fs.unlinkSync(path.join(f.dir, 'prepared-target.json')); } catch {}
      f.changeApplication();
      const input = new PassThrough(), output = new PassThrough(); input.isTTY = true; output.isTTY = true; input.end('y\n');
      const retry = f.ctx({ confirmInput: input, confirmOutput: output });
      retry.apiPort = 18081;
      const progress=retry.progress; retry.progress=text=>{progress(text); if(text==='确认完成，开始连接目标与准备阶段'){ if(drift==='input') write(path.join(f.repo,'application.txt'),'changed again after confirmation\n'); else write(path.join(f.dir,'release.env'),'changed config\n'); }};
      if (drift === 'config') retry.verifyConfig = () => {
        if (hash(fs.readFileSync(path.join(f.dir, 'release.env'))) !== state.digests['release.env']) throw Error('DEPLOYMENT_CONFIG_DRIFT');
      };
      await assert.rejects(runUnifiedDeployment(retry), new RegExp(drift === 'input' ? 'DEPLOYMENT_INPUT_DRIFT' : 'DEPLOYMENT_CONFIG_DRIFT'));
      assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.dir, 'image-replacement.json'), 'utf8')), originalMarker);
      assert.equal(fs.readdirSync(f.dir).some(name => /^image-replacement-[a-f0-9-]+-previous\.json$/.test(name)), false);
    } finally { f.cleanup(); }
  }
});

test('database selection and mount contracts are SQLite-only', async () => {
  const { renderCompose } = await import('../installation/installation-upgrade-workflow.mjs');
  const root=fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()),'lexiflow-compose-contract-'));
  try {
    const databaseFile=path.join(root,'data','lexicon.db');
    const compose=JSON.parse(renderCompose(root,{apiPort:18081,databaseFile}));
    assert.deepEqual(Object.keys(compose.services),['api']);
    assert.equal(compose.services.api.environment.SPRING_DATASOURCE_URL,'jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro');
    assert.deepEqual(compose.services.api.volumes,['${LEXIFLOW_DATABASE_FILE:?LEXIFLOW_DATABASE_FILE is required}:/var/lib/lexiflow/lexicon.db:ro']);
  } finally { fs.rmSync(root,{recursive:true,force:true}); }
});
