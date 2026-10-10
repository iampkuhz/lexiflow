import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { canonicalJson } from '../../dataset/dataset-generation-identity.mjs';
import { probeExternal, inspectDevelopmentTrust } from '../environment/local-database-target.mjs';
import { assertDevelopmentTargetIdentity } from '../installation/installation-upgrade-workflow.mjs';

function fixture() {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-db-target-'));
  const passwordFile = path.join(root, 'password'); fs.writeFileSync(passwordFile, 'private arbitrary password!\n', { mode: 0o600 });
  const calls = [], state = { owner: 'fixture_user', version: 17, canCreate: true, connect: true };
  const run = async (command, args, options) => {
    calls.push({ command, args, options });
    if (!state.connect) throw Error('private arbitrary password!');
    const sql = args.at(-1);
    if (sql.includes('server_version_num')) return JSON.stringify({ serverVersion: state.version, currentUser: 'fixture_user', userOid: 10, databaseOid: 42, databaseOwner: 'fixture_user' });
    if (sql.includes('pg_namespace')) return JSON.stringify({ schemaExists: true, schemaOid: 3, owner: state.owner, canCreateSchema: state.canCreate });
    throw Error('unexpected SQL');
  };
  return { root, state, calls, input: { run, port: 15432, user: 'fixture_user', passwordFile, postgresImage: 'docker.io/library/postgres:17' },
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }) };
}

test('probe connects only existing lexiflow, never maintenance or CREATE DATABASE', async () => {
  const f = fixture(); try {
    const p = await probeExternal(f.input);
    assert.equal(p.owner, 'fixture_user'); assert.match(p.fingerprint, /^[a-f0-9]{64}$/);
    assert.equal(f.calls.length, 2);
    for (const call of f.calls) {
      assert.equal(call.options.logOutput, false); assert.equal(call.options.capture, true);
      assert.ok(call.args.includes('--pull=never'));
      assert.equal(call.args.at(-2), 'lexiflow');
      assert.ok(!call.args.join(' ').includes('private arbitrary'));
      assert.ok(!call.args.at(-1).includes('CREATE DATABASE'));
    }
  } finally { f.cleanup(); }
});
test('wrong owner, missing CREATE privilege and PostgreSQL 17 mismatch are distinct blockers', async () => {
  const f = fixture(); try {
    f.state.owner = 'other'; await assert.rejects(probeExternal(f.input), /OWNER_MISMATCH/);
    f.state.owner = 'fixture_user'; f.state.canCreate = false;
    await assert.rejects(probeExternal(f.input), /SCHEMA_CREATE_PERMISSION_REQUIRED/);
    f.state.canCreate = true; f.state.version = 16;
    await assert.rejects(probeExternal(f.input), /POSTGRES_17_REQUIRED/);
  } finally { f.cleanup(); }
});
test('password symlink/public permissions fail before command', async () => {
  const f = fixture(); try {
    fs.chmodSync(f.input.passwordFile, 0o644);
    await assert.rejects(probeExternal(f.input), /PASSWORD_FILE_INVALID/);
    fs.chmodSync(f.input.passwordFile, 0o600);
    const link = path.join(f.root, 'link'); fs.symlinkSync(f.input.passwordFile, link);
    await assert.rejects(probeExternal({ ...f.input, passwordFile: link }), /PASSWORD_FILE_INVALID/);
    assert.equal(f.calls.length, 0);
  } finally { f.cleanup(); }
});
test('connection or authentication failure is not interpreted as an empty database', async () => {
  const f = fixture(); try {
    f.state.connect = false;
    await assert.rejects(probeExternal(f.input), error => error.message === 'DATABASE_AUTH_OR_CONNECT_FAILED');
    assert.equal(f.calls.length, 1);
  } finally { f.cleanup(); }
});

function developmentFixture() {
  const repo = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-dev-target-'));
  const compose = path.join(repo, 'infra/local/compose.yaml');
  fs.mkdirSync(path.dirname(compose), { recursive: true });
  fs.copyFileSync(path.resolve(import.meta.dirname, '../../../infra/local/compose.yaml'), compose);
  const id = 'a'.repeat(64);
  const row = { Id: id, Image: `sha256:${'b'.repeat(64)}`, ImageName: 'docker.io/library/postgres:17-alpine',
    Config: { Image: 'docker.io/library/postgres:17-alpine', Env: ['POSTGRES_DB=lexiflow', 'POSTGRES_HOST_AUTH_METHOD=trust'],
      Labels: { 'com.docker.compose.project': 'lexiflow-local', 'com.docker.compose.service': 'postgres',
        'com.docker.compose.project.config_files': compose, 'com.docker.compose.project.working_dir': path.dirname(compose),
        'io.podman.compose.project': 'lexiflow-local', 'io.podman.compose.service': 'postgres' } },
    State: { Running: true }, HostConfig: { PortBindings: { '5432/tcp': [{ HostIp: '127.0.0.1', HostPort: '15432' }] } },
    Mounts: [{ Type: 'volume', Name: 'lexiflow-local_lexiflow-postgres-data', Destination: '/var/lib/postgresql/data' }] };
  const calls = [];
  const run = async (_command, args) => { calls.push(args);
    if (args[0] === 'ps') return id;
    if (args[0] === 'inspect') return JSON.stringify([row]);
    throw Error('unexpected command'); };
  return { repo, compose, row, calls, run, cleanup: () => fs.rmSync(repo, { recursive: true }) };
}

test('development trust is recognized only by exact current Compose, running image and container facts', async () => {
  const f = developmentFixture();
  try {
    const result = await inspectDevelopmentTrust({ repo: f.repo, run: f.run });
    assert.equal(result.user, 'postgres'); assert.match(result.fingerprint, /^[a-f0-9]{64}$/);
    assert.equal(result.composeSha256, undefined);
    assert.deepEqual(f.calls.map(args => args[0]), ['ps', 'inspect']);
    for (const mutate of [
      row => { row.ImageName = 'docker.io/library/postgres:170-alpine'; },
      row => { row.Config.Env.push('POSTGRES_PASSWORD='); },
      row => { row.Config.Env.push('POSTGRES_DB=lexiflow'); },
      row => { row.HostConfig.PortBindings['5432/tcp'][0].HostIp = '0.0.0.0'; },
      row => { row.Mounts[0].Name = 'other'; },
      row => { row.Config.Labels['com.docker.compose.project.config_files'] = '/other/compose.yaml'; },
    ]) {
      const before = structuredClone(f.row); mutate(f.row);
      try { assert.equal(await inspectDevelopmentTrust({ repo: f.repo, run: f.run }), null); }
      catch (error) { assert.match(error.message, /^DEVELOPMENT_DATABASE_/); }
      Object.assign(f.row, before);
    }
    fs.writeFileSync(f.compose, fs.readFileSync(f.compose, 'utf8').replace('POSTGRES_HOST_AUTH_METHOD: trust', 'POSTGRES_HOST_AUTH_METHOD: md5'));
    await assert.rejects(inspectDevelopmentTrust({ repo: f.repo, run: f.run }), /DEVELOPMENT_DATABASE_SOURCE_INVALID/);
  } finally { f.cleanup(); }
});

test('Redis, comments, links, formatting and mapping order do not change database identity', async () => {
  const f = developmentFixture();
  try {
    const oldComposeSha = crypto.createHash('sha256').update(fs.readFileSync(f.compose)).digest('hex');
    const first = await inspectDevelopmentTrust({ repo: f.repo, run: f.run });
    fs.appendFileSync(f.compose, '\n# harmless local note\n');
    fs.writeFileSync(f.compose, fs.readFileSync(f.compose, 'utf8').replace('image: docker.io/library/redis:7', 'image: docker.io/library/redis:7-alpine'));
    let irrelevant = fs.readFileSync(f.compose, 'utf8')
      .replace('    command: ["redis-server", "--save", "", "--appendonly", "no"]', '    command: >\n      redis-server --appendonly no')
      .replace('    healthcheck:\n      test: ["CMD", "redis-cli", "ping"]', '    volumes:\n      - redis-cache:/data\n    healthcheck:\n      test: ["CMD", "redis-cli", "ping"]');
    irrelevant = irrelevant.replace('volumes:\n  lexiflow-postgres-data:\n', 'volumes:\n  lexiflow-postgres-data:\n  redis-cache:\n    driver: local\n    driver_opts:\n      type: tmpfs\n');
    fs.writeFileSync(f.compose, irrelevant);
    let source = fs.readFileSync(f.compose, 'utf8').replace('name: lexiflow-local', '"name": "lexiflow-local"')
      .replace('POSTGRES_DB: lexiflow', "'POSTGRES_DB': 'lexiflow'")
      .replace('POSTGRES_HOST_AUTH_METHOD: trust', 'POSTGRES_HOST_AUTH_METHOD: "trust"')
      .replace('image: docker.io/library/postgres:17-alpine', "image: 'docker.io/library/postgres:17-alpine'")
      .replace("'POSTGRES_DB': 'lexiflow'\n      POSTGRES_HOST_AUTH_METHOD: \"trust\"", "POSTGRES_HOST_AUTH_METHOD: \"trust\"\n      'POSTGRES_DB': 'lexiflow'");
    fs.writeFileSync(f.compose, source.replace('    ports:\n      - "127.0.0.1:15432:5432"', '    ports:\n      - \'127.0.0.1:15432:5432\''));
    const formatted = fs.readFileSync(f.compose, 'utf8');
    assert.match(formatted, /POSTGRES_HOST_AUTH_METHOD:[^\n]*\n\s+['"]?POSTGRES_DB['"]?:/);
    const later = await inspectDevelopmentTrust({ repo: f.repo, run: f.run });
    fs.writeFileSync(f.compose, formatted.replace(/^ +/gm, spaces => ' '.repeat(spaces.length * 2)));
    assert.equal((await inspectDevelopmentTrust({ repo: f.repo, run: f.run })).fingerprint, later.fingerprint);
    assert.equal(later.containerId, first.containerId);
    assert.equal(later.composeSha256, undefined);
    assert.equal(later.fingerprint, first.fingerprint);
    assert.equal((await assertDevelopmentTargetIdentity({ repo: f.repo, run: f.run, state: {
      databaseTarget: { kind: 'external', authMode: 'development-trust', port: 15432,
        host: '127.0.0.1', user: 'postgres', developmentIdentityFingerprint: first.fingerprint, developmentIdentity: first } } })).fingerprint, first.fingerprint);
    const { fingerprint: _fingerprint, ...legacyFacts } = first;
    legacyFacts.composeSha256 = oldComposeSha;
    const legacyIdentity = { ...first, fingerprint: crypto.createHash('sha256')
      .update(canonicalJson(legacyFacts)).digest('hex') };
    legacyIdentity.composeSha256 = oldComposeSha;
    assert.equal((await assertDevelopmentTargetIdentity({ repo: f.repo, run: f.run, state: {
      databaseTarget: { kind: 'external', authMode: 'development-trust', port: 15432,
        host: '127.0.0.1', user: 'postgres', developmentIdentityFingerprint: legacyIdentity.fingerprint,
        developmentIdentity: legacyIdentity } } })).fingerprint, first.fingerprint);
    const tamperedLegacy = { ...legacyIdentity, composeSha256: '0'.repeat(64) };
    await assert.rejects(assertDevelopmentTargetIdentity({ repo: f.repo, run: f.run, state: {
      databaseTarget: { kind: 'external', authMode: 'development-trust', port: 15432,
        host: '127.0.0.1', user: 'postgres', developmentIdentityFingerprint: tamperedLegacy.fingerprint,
        developmentIdentity: tamperedLegacy } } }), /DEVELOPMENT_DATABASE_RECORD_INVALID/);
  } finally { f.cleanup(); }
});

test('development trust has no password mount or inherited password environment in probe', async () => {
  const f = fixture();
  try {
    f.input.authMode = 'development-trust'; f.input.user = 'postgres'; delete f.input.passwordFile;
    f.input.developmentIdentity = { fingerprint: 'd'.repeat(64) };
    f.input.run = async (_command, args, options) => { f.calls.push({ args, options });
      const sql = args.at(-1);
      if (sql.includes('server_version_num')) return JSON.stringify({ serverVersion: 17, currentUser: 'postgres', userOid: 10, databaseOid: 42, databaseOwner: 'postgres' });
      return JSON.stringify({ schemaExists: false, schemaOid: null, owner: null, canCreateSchema: true }); };
    const result = await probeExternal(f.input);
    assert.equal(result.schemaExists, false);
    for (const { args } of f.calls) {
      assert.ok(!args.includes('--volume')); assert.ok(args.includes('PGPASSWORD=')); assert.ok(args.includes('PGPASSFILE='));
      assert.ok(args.join(' ').includes('unset PGPASSWORD PGPASSFILE')); assert.ok(args.join(' ').includes('--no-password'));
    }
  } finally { f.cleanup(); }
});

test('database-affecting Compose fields and data-volume options are rejected', async () => {
  const f = developmentFixture();
  try {
    for (const replacement of [
      ['    healthcheck:', '    command: ["sh", "-c", "echo unsafe"]\n    healthcheck:'],
      ['      POSTGRES_HOST_AUTH_METHOD: trust', '      POSTGRES_HOST_AUTH_METHOD: password'],
      ['  lexiflow-postgres-data:\n', '  lexiflow-postgres-data:\n    external: true\n'],
      ['  lexiflow-postgres-data:\n', '  lexiflow-postgres-data:\n    driver: other\n'],
    ]) {
      const original = fs.readFileSync(f.compose, 'utf8');
      fs.writeFileSync(f.compose, original.replace(...replacement));
      await assert.rejects(inspectDevelopmentTrust({ repo: f.repo, run: f.run }), /DEVELOPMENT_DATABASE_SOURCE_INVALID/);
      fs.writeFileSync(f.compose, original);
    }
  } finally { f.cleanup(); }
});

test('indentless PostgreSQL lists preserve the same database facts', async () => {
  const f = developmentFixture();
  try {
    const first = await inspectDevelopmentTrust({ repo: f.repo, run: f.run });
    fs.writeFileSync(f.compose, fs.readFileSync(f.compose, 'utf8')
      .replace('      - "127.0.0.1:15432:5432"', '    - "127.0.0.1:15432:5432"')
      .replace('      - lexiflow-postgres-data:/var/lib/postgresql/data', '    - lexiflow-postgres-data:/var/lib/postgresql/data'));
    assert.deepEqual(await inspectDevelopmentTrust({ repo: f.repo, run: f.run }), first);
  } finally { f.cleanup(); }
});

test('database declarations cannot inherit trust through a prototype key', async () => {
  const f = developmentFixture();
  try {
    fs.writeFileSync(f.compose, fs.readFileSync(f.compose, 'utf8').replace(
      '      POSTGRES_DB: lexiflow\n      POSTGRES_HOST_AUTH_METHOD: trust',
      '      __proto__: {"POSTGRES_DB":"lexiflow","POSTGRES_HOST_AUTH_METHOD":"trust"}'));
    await assert.rejects(inspectDevelopmentTrust({ repo: f.repo, run: f.run }), /DEVELOPMENT_DATABASE_SOURCE_INVALID/);
    assert.equal(f.calls.length, 0);
  } finally { f.cleanup(); }
});

test('record corruption is not normalized away when comparing database facts', async () => {
  const f = developmentFixture();
  try {
    const first = await inspectDevelopmentTrust({ repo: f.repo, run: f.run });
    for (const identity of [{ ...first, composeSha256: 'invalid' }, { ...first, fingerprint: 'f'.repeat(64) }]) {
      await assert.rejects(assertDevelopmentTargetIdentity({ repo: f.repo, run: f.run, state: {
        databaseTarget: { kind: 'external', authMode: 'development-trust', port: 15432,
          host: '127.0.0.1', user: 'postgres', developmentIdentityFingerprint: identity.fingerprint,
          developmentIdentity: identity } } }), /DEVELOPMENT_DATABASE_RECORD_INVALID/);
    }
  } finally { f.cleanup(); }
});

test('multiple matching development containers are rejected without probing PostgreSQL', async () => {
  const f = developmentFixture();
  try {
    const second = 'c'.repeat(64);
    const run = async (_command, args) => {
      if (args[0] === 'ps') return `${f.row.Id}\n${second}\n`;
      if (args[0] === 'inspect') return JSON.stringify([{ ...f.row, Id: args[1] }]);
      throw Error('database command must not run');
    };
    await assert.rejects(inspectDevelopmentTrust({ repo: f.repo, run }), /DEVELOPMENT_DATABASE_AMBIGUOUS/);
  } finally { f.cleanup(); }
});
