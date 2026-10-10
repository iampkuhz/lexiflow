import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import net from 'node:net';
import { PassThrough } from 'node:stream';
import { execFileSync } from 'node:child_process';
import { runUnifiedDeployment } from '../unified-local.mjs';
import { generationCodeVersion, dataGeneration } from '../../dataset/generation-version.mjs';
import { resolveBuildIdentity } from '../../release/version.mjs';
import { makeZip } from '../../release/tests/zip-fixture.mjs';

const root = path.resolve(import.meta.dirname, '../../..');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const image = letter => `sha256:${letter.repeat(64)}`;
function unusedPort() { return new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)); }); }); }
function write(file, content) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, content); }
function fixture({ candidateMode = false, failReady = false, failApply = false, structuredFailure = false, externalMissing = false, missingPgTool = false, developmentTrust = false, sourceDriftDuringBuild = false } = {}) {
  const base = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-flow-'));
  const repo = path.join(base, 'source'), dir = path.join(base, 'install'); fs.mkdirSync(repo);
  write(path.join(repo, '.gitignore'), 'tmp/\nbackend/product/api/build/\nextension/dist/\nextension/node_modules/\n');
  write(path.join(repo, 'fixture.txt'), 'fixed source\n');
  write(path.join(repo, 'application.txt'), 'fixed application\n');
  write(path.join(repo, 'ops/release/version.txt'), '9.0.0-SNAPSHOT\n');
  write(path.join(repo, 'ops/dataset/generation-inputs.json'), JSON.stringify({ schemaVersion: 1, include: ['fixture.txt'] }));
  fs.copyFileSync(path.join(root, 'ops/dataset/generation-version.mjs'), path.join(repo, 'ops/dataset/generation-version.mjs'));
  fs.copyFileSync(path.join(root, 'ops/dataset/ecdict-source.lock.json'), path.join(repo, 'ops/dataset/ecdict-source.lock.json'));
  fs.mkdirSync(path.join(repo, 'ops/docker'), { recursive: true });
  for (const name of ['Dockerfile', 'Dockerfile.postgres', 'entrypoint.sh', 'bootstrap.sh']) fs.copyFileSync(path.join(root, 'ops/docker', name), path.join(repo, 'ops/docker', name));
  fs.mkdirSync(path.join(repo, 'infra/postgres'), { recursive: true });
  fs.copyFileSync(path.join(root, 'infra/postgres/schema.sql'), path.join(repo, 'infra/postgres/schema.sql'));
  if (developmentTrust) {
    fs.mkdirSync(path.join(repo, 'infra/local'), { recursive: true });
    fs.copyFileSync(path.join(root, 'infra/local/compose.yaml'), path.join(repo, 'infra/local/compose.yaml'));
  }
  execFileSync('git', ['init', '-q', repo]); execFileSync('git', ['-C', repo, 'config', 'user.email', 'fixture@example.invalid']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'Fixture']); execFileSync('git', ['-C', repo, 'add', '-A']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'fixture']);
  let identity = resolveBuildIdentity(repo), committedGeneration;
  const calls = [], saved = [];
  const csv = Buffer.from('word,translation,oxford,tag,bnc,frq,exchange\nfixed,固定,1,,1,1,\n');
  const locked = JSON.parse(fs.readFileSync(path.join(repo, 'ops/dataset/ecdict-source.lock.json'), 'utf8'));
  const upstream = { repository_url: locked.repository_url, commit: locked.commit, files: locked.files,
    archive_member: { sha256: locked.archive_member.sha256, bytes: locked.archive_member.bytes } };
  let candidate, request, generation, dbPort, created = false, credential, pgToolPresent = !missingPgTool, sourceToolsOwner = null;
  const developmentId = 'd'.repeat(64);
  const developmentRow = { Id: developmentId, Image: image('e'), ImageName: 'docker.io/library/postgres:17-alpine',
    Config: { Image: 'docker.io/library/postgres:17-alpine', Env: ['POSTGRES_DB=lexiflow', 'POSTGRES_HOST_AUTH_METHOD=trust'],
      Labels: { 'com.docker.compose.project': 'lexiflow-local', 'com.docker.compose.service': 'postgres',
        'io.podman.compose.project': 'lexiflow-local', 'io.podman.compose.service': 'postgres',
        'com.docker.compose.project.config_files': path.join(repo, 'infra/local/compose.yaml'),
        'com.docker.compose.project.working_dir': path.join(repo, 'infra/local') } }, State: { Running: true },
    HostConfig: { PortBindings: { '5432/tcp': [{ HostIp: '127.0.0.1', HostPort: '15432' }] } },
    Mounts: [{ Type: 'volume', Name: 'lexiflow-local_lexiflow-postgres-data', Destination: '/var/lib/postgresql/data' }] };
  async function setup() {
    dbPort = developmentTrust ? 15432 : await unusedPort();
    if (externalMissing) { credential = path.join(base, 'external-password'); write(credential, 'unused\n'); fs.chmodSync(credential, 0o600); }
    generation = dataGeneration({ codeVersion: await generationCodeVersion(repo), inputSha256: hash(csv), upstream });
    if (candidateMode) {
      const candidateDir = path.join(base, 'candidate');
      const manifest = { generationVersion: generation.generationVersion, generationCodeVersion: generation.codeVersion,
        generationSourceDigest: generation.sourceDigest, generationInputsJson: JSON.stringify({ inputSha256: generation.inputSha256, upstream }),
        generationConfigJson: JSON.stringify(generation.config) };
      const zip = makeZip([['manifest.json', JSON.stringify(manifest)]]);
      write(path.join(candidateDir, 'payload/data/dataset.zip'), zip);
      write(path.join(candidateDir, 'payload/sql/schema.sql'), 'synthetic sql\n');
      write(path.join(candidateDir, 'payload/images/api.tar'), 'synthetic');
      candidate = { root: candidateDir, candidateSha256: 'c'.repeat(64), manifestSha256: 'd'.repeat(64), buildIdentity: identity,
        artifacts: { dataset: { path: 'data/dataset.zip', sha256: hash(zip) }, sql: { path: 'sql/schema.sql', sha256: hash('synthetic sql\n') },
          'api-image:linux/arm64': { path: 'images/api.tar', imageDigest: image('a') } } };
      request = { candidateDirectory: candidateDir, candidateSha256: candidate.candidateSha256 };
    }
  }
  async function run(command, argv) {
    calls.push(`${command}:${argv.join(' ')}`);
    if (command === './gradlew') { identity = resolveBuildIdentity(repo); write(path.join(repo, 'backend/product/api/build/internal/boot/api-internal.jar'), 'synthetic jar'); return ''; }
    if (command === 'npm') {
      if (argv[0] === 'run') {
        if (sourceDriftDuringBuild) {
          write(path.join(repo, 'application.txt'), 'changed application during build\n');
          identity = resolveBuildIdentity(repo);
        }
        write(path.join(repo, 'extension/dist/build-identity.json'), JSON.stringify(identity));
        write(path.join(repo, 'extension/dist/manifest.json'), JSON.stringify({ version: identity.chromeVersion,
          version_name: identity.softwareVersion, host_permissions: ['http://127.0.0.1:18080/*'] }));
      }
      return '';
    }
    if (command === 'java') return JSON.stringify({ status: 'PASS', database: 'lexiflow', schema: 'lexiflow_release', fingerprint: 'f'.repeat(64),
      needsRebuild: committedGeneration !== generation.generationVersion,
      reason: !committedGeneration ? 'SCHEMA_MISSING' : committedGeneration !== generation.generationVersion ? 'GENERATION_CHANGED' : 'OK',
      generationVersion: committedGeneration ?? null, datasetVersion: committedGeneration ? 7 : null });
    if (command !== 'podman') throw new Error('unexpected command');
    if (argv[0] === 'build' && argv.includes('--iidfile')) {
      write(argv[argv.indexOf('--iidfile') + 1], image('e'));
      sourceToolsOwner = argv.find(value => value.startsWith('io.lexiflow.source-tools-owner='))?.split('=')[1]; return '';
    }
    if (argv[0] === 'image' && argv[1] === 'exists') throw Object.assign(new Error('image absent'), { commandExitCode: 1 });
    if (argv[0] === 'image' && argv[1] === 'inspect' && argv.includes('{{json .Labels}}') && sourceToolsOwner)
      return JSON.stringify({ 'io.lexiflow.source-tools-owner': sourceToolsOwner });
    if (argv[0] === 'rmi' || argv[0] === 'tag') return '';
    if (developmentTrust && argv[0] === 'ps' && argv.includes('-q')) return developmentId;
    if (developmentTrust && argv[0] === 'inspect' && argv[1] === developmentId) return JSON.stringify([developmentRow]);
    if (argv[0] === 'run' && argv.includes('/bin/sh')) {
      const query = argv.at(-1);
      if (query.includes('pg_database')) return JSON.stringify({ serverVersion: 17, currentUser: 'lexiflow', userOid: 10,
        databaseOid: 16385, databaseOwner: 'lexiflow', ...(developmentTrust ? { currentUser: 'postgres', databaseOwner: 'postgres' } : {}) });
      if (query.includes('pg_namespace')) return JSON.stringify({ schemaExists: true, schemaOid: 16386,
        owner: developmentTrust ? 'postgres' : 'lexiflow', canCreateSchema: true });
      throw new Error('unexpected SQL');
    }
    if (argv[0] === 'run' && argv.includes('/kit/ops/podman/fetch-ecdict.sh')) {
      const mount = argv.find(arg => arg.endsWith(':/data'));
      const dataRoot = path.join(mount.slice(0, -6), 'ecdict-source.ABC123');
      write(path.join(dataRoot, 'stardict.csv'), csv);
      generation = dataGeneration({ codeVersion: await generationCodeVersion(repo), inputSha256: hash(csv), upstream });
      write(path.join(dataRoot, 'source-summary.json'), JSON.stringify({ csv_sha256: hash(csv), generation }));
      return 'dataset_dir=/data/ecdict-source.ABC123\n';
    }
    if (argv[0] === 'image' && argv[1] === 'inspect' && argv.includes('--format') && argv[argv.indexOf('--format') + 1] === 'json')
      return JSON.stringify([{ Id: argv.at(-1), Os: 'linux', Architecture: 'arm64', Labels: {
        'org.opencontainers.image.version': identity.softwareVersion, 'org.opencontainers.image.revision': identity.sourceCommit,
        'io.lexiflow.build-id': identity.buildId, 'io.lexiflow.source-sha256': identity.sourceSha256,
        'io.lexiflow.image-role': 'api', 'io.lexiflow.deployment-version': identity.baseVersion.toLowerCase() } }]);
    if (argv[0] === 'run' && argv.includes('--deployment-data')) {
      if (argv.includes('apply')) { if (structuredFailure) throw Object.assign(new Error('safe command exit'), { commandExitCode: 1,
          stdout: JSON.stringify({ status: 'FAIL', reason: 'REQUEST_OR_DATA_INVALID' }) });
        if (failApply) throw new Error('SYNTHETIC_DB_APPLY_FAILURE');
        committedGeneration = generation.generationVersion;
        return JSON.stringify({ status: 'PASS', rebuilt: true, generationVersion: generation.generationVersion, datasetVersion: 7 }); }
      return JSON.stringify({ status: 'PASS', database: 'lexiflow', schema: 'lexiflow_release', fingerprint: 'f'.repeat(64),
        needsRebuild: committedGeneration !== generation.generationVersion,
        reason: !committedGeneration ? 'SCHEMA_MISSING' : committedGeneration !== generation.generationVersion ? 'GENERATION_CHANGED' : 'OK',
        generationVersion: committedGeneration ?? null, datasetVersion: committedGeneration ? 7 : null });
    }
    if (argv[0] === 'pull' && argv.at(-1) === 'docker.io/library/postgres:17-bookworm') pgToolPresent = true;
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      if (argv.at(-1) === 'docker.io/library/postgres:17-bookworm' && !pgToolPresent) throw new Error('image absent');
      return image('e');
    }
    return '';
  }
  function ctx({ planOnly = false, confirmPlan, confirmInput, confirmOutput } = {}) {
    let current;
    return { repo, dir, action: 'install', apiPort: 18080, dbPort, seen: new Set(),
      candidate, candidateRequest: request, candidateAdapter: { readCandidate: async () => candidate,
        installCandidateArtifacts: async (_candidate, { installDir }) => {
          calls.push('candidate-install');
          write(path.join(installDir, 'extension/build-identity.json'), JSON.stringify(identity));
          write(path.join(installDir, 'extension/manifest.json'), JSON.stringify({ version: identity.chromeVersion,
            version_name: identity.softwareVersion, host_permissions: ['http://127.0.0.1:18080/*'] }));
          return { apiImage: image('a'), postgresImage: image('b') };
        } },
      // 编排 fixture 不读取真实 API 监听者；数据库占用场景仍使用独立测试端口。
      portTaken: port => port === 18080 ? Promise.resolve(false) : new Promise(resolve => {
        const server = net.createServer();
        server.once('error', error => resolve(error.code === 'EADDRINUSE'));
        server.listen(port, '127.0.0.1', () => server.close(() => resolve(false)));
      }),
      environment: { PATH: process.env.PATH }, javaEnvironment: env => env, run,
      ready: async () => { calls.push('ready'); if (failReady) throw new Error('SYNTHETIC_API_FAILURE'); return 7; },
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
    console.log = value => { try { const parsed = JSON.parse(value); if (parsed.planHash) planned = parsed; } catch {} };
    try { assert.equal(await runUnifiedDeployment({ ...ctx({ planOnly: true }), action }), 0); }
    finally { console.log = original; }
    assert.ok(planned?.planHash); if (!existed) assert.equal(fs.existsSync(dir), false);
    return planned;
  }
  return { base, repo, dir, calls, saved, developmentRow, get identity() { return identity; }, setup, ctx, plan,
    changeSource: () => write(path.join(repo, 'fixture.txt'), 'changed generation input\n'),
    changeApplication: () => write(path.join(repo, 'application.txt'), 'changed application\n'),
    get candidate() { return candidate; },
    cleanup: () => fs.rmSync(base, { recursive: true }) };
}

test('source actual planner uses Java inspect, then applies through built API image before activation', async () => {
  const f = fixture();
  try {
    await f.setup(); const { plan, planHash } = await f.plan();
    assert.equal(plan.actions.dataRebuild, true);
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), 0);
    assert.ok(f.calls.some(call => call.startsWith('podman:run') && call.includes('--deployment-data inspect')));
    assert.ok(f.calls.some(call => call.startsWith('podman:run') && call.includes('--deployment-data apply')));
    assert.ok(f.calls.filter(call => call.startsWith('podman:run') && call.includes('--deployment-data')).every(call =>
      call.includes('--cap-drop=ALL --cap-add=DAC_OVERRIDE') && call.includes('--read-only') && call.includes('--security-opt=no-new-privileges')));
    assert.ok(f.calls.filter(call => call.includes('/kit/ops/podman/fetch-ecdict.sh')).every(call => !call.includes('DAC_OVERRIDE')));
    const postgresBuild = f.calls.find(call => call.startsWith('podman:build') && call.endsWith('/postgres'));
    const postgresContext = postgresBuild?.split(' ').at(-1);
    assert.ok(postgresBuild?.includes(`-f ${path.join(postgresContext, 'Dockerfile.postgres')}`),
      'fresh managed PostgreSQL build must select the non-default Dockerfile explicitly');
    assert.ok(fs.existsSync(path.join(postgresContext, 'Dockerfile.postgres')));
    assert.ok(f.calls.indexOf('compose:up -d --no-deps --force-recreate api') > f.calls.findIndex(call => call.startsWith('podman:run') && call.includes('--deployment-data apply')));
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.phase, 'ready'); assert.equal(state.generationVersion.length, 64);
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'complete');
  } finally { f.cleanup(); }
});

test('package planner distinguishes original CSV hash from ZIP hash and mounts container input path', async () => {
  const f = fixture({ candidateMode: true });
  try {
    await f.setup(); const { planHash } = await f.plan();
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), 0);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.inputType, 'package'); assert.notEqual(state.inputSha256, state.generation.inputSha256);
    assert.ok(f.calls.some(call => call.includes('--deployment-data inspect') && call.includes('/request.json')));
    assert.ok(f.calls.some(call => call.includes('--deployment-data apply') && call.includes('--user 0:0')));
  } finally { f.cleanup(); }
});

test('API failure after DB commit leaves forward-only journal and initialized target state', async () => {
  const f = fixture({ failReady: true });
  try {
    await f.setup(); const { planHash } = await f.plan();
    await assert.rejects(runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), /SYNTHETIC_API_FAILURE/);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.phase, 'initialized');
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
  } finally { f.cleanup(); }
});

test('occupied port without recorded installation credentials blocks before build or database probe', async () => {
  const f = fixture(); let listener;
  try {
    await f.setup();
    const port = (await f.ctx({ planOnly: true })).dbPort;
    listener = net.createServer(); await new Promise(resolve => listener.listen(port, '127.0.0.1', resolve));
    await assert.rejects(f.plan(), /DEPLOYMENT_DATABASE_CREDENTIAL_REQUIRED/);
    assert.equal(f.calls.some(call => call.startsWith('./gradlew:') || call.includes('/bin/sh') || call.includes('CREATE DATABASE')), false);
    assert.equal(fs.existsSync(f.dir), false);
  } finally { await new Promise(resolve => listener?.close(resolve) ?? resolve()); f.cleanup(); }
});

test('recognized development trust uses no secret and freezes container identity in plan and state', async () => {
  const f = fixture({ developmentTrust: true }); let listener;
  try {
    await f.setup();
    listener = net.createServer();
    try { await new Promise((resolve, reject) => listener.once('error', reject).listen(15432, '127.0.0.1', resolve)); }
    catch (error) { if (error.code !== 'EADDRINUSE') throw error; listener = null; }
    const { plan, planHash } = await f.plan();
    assert.equal(plan.database.target.kind, 'external');
    assert.equal(plan.database.target.authMode, 'development-trust');
    assert.equal(plan.database.target.user, 'postgres');
    assert.match(plan.database.target.developmentIdentityFingerprint, /^[a-f0-9]{64}$/);
    assert.equal(await runUnifiedDeployment(f.ctx({ confirmPlan: planHash })), 0);
    const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    assert.equal(state.databaseTarget.authMode, 'development-trust');
    assert.equal(state.databaseTarget.developmentIdentity.containerId, f.developmentRow.Id);
    assert.equal(fs.existsSync(path.join(f.dir, 'secrets/app-password')), false);
    assert.equal(state.digests['secrets/app-password'], undefined);
    const compose = JSON.parse(fs.readFileSync(path.join(f.dir, 'compose.yaml')));
    assert.equal(compose.services.postgres, undefined);
    assert.equal(compose.services.api.secrets, undefined);
    assert.ok(f.calls.filter(call => call.startsWith('podman:run') && call.includes('--deployment-data')).every(call =>
      !call.includes(':/secret:ro') && call.includes('LEXIFLOW_DEPLOYMENT_DB_PASSWORD=')
      && call.includes('LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE')));
    const before = f.calls.length;
    f.developmentRow.Image = image('f');
    await assert.rejects(f.plan(), /DEVELOPMENT_DATABASE_IMAGE_CHANGED/);
    assert.equal(f.calls.slice(before).some(call => call.includes('--deployment-data') || call.startsWith('./gradlew:')), false);
  } finally { if (listener) await new Promise(resolve => listener.close(resolve)); f.cleanup(); }
});

test('recorded external target reuses saved secret, never creates database or manages postgres service', async () => {
  const f = fixture();
  try {
    await f.setup(); const first = await f.plan(); await runUnifiedDeployment(f.ctx({ confirmPlan: first.planHash }));
    const stateFile = path.join(f.dir, 'state.json'); const state = JSON.parse(fs.readFileSync(stateFile));
    state.databaseTarget = { kind: 'external', host: '127.0.0.1', port: state.dbPort, user: 'lexiflow' };
    fs.writeFileSync(stateFile, JSON.stringify(state));
    const before = f.calls.length, planned = await f.plan();
    assert.equal(planned.plan.database.target.kind, 'external');
    assert.equal(planned.plan.database.create, false);
    assert.equal(planned.plan.actions.dataRebuild, false);
    assert.ok(f.calls.slice(before).some(call => call.includes('/bin/sh')));
    assert.equal(f.calls.slice(before).some(call => call.includes('CREATE DATABASE') || call.includes('compose:up -d postgres')), false);
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
    assert.match(output.text, /127\.0\.0\.1:[\s\S]*lexiflow_release[\s\S]*\[y\/N\]/);
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
  } finally { f.cleanup(); }
});

test('candidate package verification is deferred until after ordinary consent', async () => {
  const f = fixture({ candidateMode: true });
  try {
    await f.setup();
    const input = new PassThrough(); input.isTTY = true; input.write('n\n');
    const output = { isTTY: true, write() {} };
    const ctx = f.ctx({ confirmInput: input, confirmOutput: output });
    ctx.candidate = undefined;
    ctx.candidateAdapter.readCandidate = async () => { f.calls.push('candidate-read'); return f.candidate; };
    assert.equal(await runUnifiedDeployment(ctx), 0);
    assert.deepEqual(f.calls.filter(call => !String(call).startsWith('progress:')), []);
    assert.equal(fs.existsSync(f.dir), false);
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

test('ordinary consent blocks recorded credential changes before its first connection read', async () => {
  const f = fixture();
  try {
    await f.setup();
    const plan = await f.plan(); await runUnifiedDeployment(f.ctx({ confirmPlan: plan.planHash }));
    const input = new PassThrough(); input.isTTY = true; input.write('y\n');
    const ctx = f.ctx({ confirmInput: input, confirmOutput: { isTTY: true, write() {} } });
    const before = f.calls.length, progress = ctx.progress;
    ctx.progress = text => {
      progress(text);
      if (text === '确认完成，开始连接目标与准备阶段') fs.writeFileSync(path.join(f.dir, 'secrets/app-password'), 'changed-private-fixture\n');
    };
    await assert.rejects(runUnifiedDeployment(ctx), /DEPLOYMENT_SECRET_DRIFT/);
    assert.equal(f.calls.slice(before).some(call => call.startsWith('./gradlew:') || call.startsWith('podman:run')), false);
  } finally { f.cleanup(); }
});

for (const candidateMode of [false, true]) test(`unchanged ${candidateMode ? 'candidate' : 'source'} refresh always prepares and restarts application without reimporting data`, async () => {
  const f = fixture({ candidateMode });
  try {
    await f.setup(); const initial = await f.plan();
    await runUnifiedDeployment(f.ctx({ confirmPlan: initial.planHash }));
    const initialState = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
    let operation = initialState.lastOperation.operation;
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
      if (candidateMode) assert.equal(execution.filter(call => call === 'candidate-install').length, 1);
      else {
        assert.equal(execution.filter(call => call.startsWith('npm:run build')).length, 1);
        assert.equal(execution.filter(call => call.startsWith('podman:build') && call.includes('-t localhost/lexiflow-api:')).length, 1);
      }
      const state = JSON.parse(fs.readFileSync(path.join(f.dir, 'state.json')));
      assert.equal(state.phase, 'ready');
      assert.equal(state.id, initialState.id);
      assert.equal(state.generationVersion, initialState.generationVersion);
      assert.notEqual(state.lastOperation.operation, operation);
      assert.equal(state.lastOperation.kind, action);
      assert.equal(JSON.parse(fs.readFileSync(path.join(f.dir, 'deployment.json'))).stage, 'complete');
      operation = state.lastOperation.operation;
    }
  } finally { f.cleanup(); }
});
