import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { runLocal } from '../commands/manage-podman-installation.mjs';
import { renderCompose, readPreparedTarget } from '../installation/installation-upgrade-workflow.mjs';

test('runtime Compose mounts the sole SQLite file read-only and starts no database service', () => {
  const config = JSON.parse(renderCompose('/unused', { apiPort: 18080, databaseFile: '/private/install/data/lexicon.db' }));
  assert.deepEqual(Object.keys(config.services), ['api']);
  assert.deepEqual(config.services.api.ports, ['127.0.0.1:18080:8080']);
  assert.deepEqual(config.services.api.networks, ['published']);
  assert.equal(config.services.api.environment.SPRING_DATASOURCE_URL, 'jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro');
  assert.equal(config.services.api.volumes[0], '${LEXIFLOW_DATABASE_FILE:?LEXIFLOW_DATABASE_FILE is required}:/var/lib/lexiflow/lexicon.db:ro');
  assert.deepEqual(Object.keys(config.services), ['api']);
});

test('prepared target recovery binds a SQLite file, not a server or secret', () => {
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-trust-recovery-'));
  const dir = path.join(parent, 'install'); fs.mkdirSync(dir);
  const journal = { operation: 'a'.repeat(32), planHash: 'b'.repeat(64) };
  const file = path.join(dir, 'prepared-target.json');
  const prepared = { schema: 1, operation: journal.operation, planHash: journal.planHash,
    targetState: { root: dir, apiImage: `sha256:${'c'.repeat(64)}`, generationVersion: 'd'.repeat(64),
      inputSha256: 'e'.repeat(64), generation: { generationVersion: 'd'.repeat(64) },
      databaseFile: path.join(dir, 'data/lexicon.db'), dataset: path.join(dir, 'data/source'), databaseTarget: { kind: 'file', file: path.join(dir, 'data/lexicon.db') } },
    apiImage: `sha256:${'c'.repeat(64)}`, previousApiImage: null, dataFile: path.join(dir, 'data/source/stardict.csv'),
    databaseFile: path.join(dir, 'data/lexicon.db'), extensionStage: null, actions: {}, extensionSha256: '1'.repeat(64) };
  try {
    fs.writeFileSync(file, JSON.stringify(prepared));
    assert.equal(readPreparedTarget(file, journal, dir).databaseFile, path.join(dir, 'data/lexicon.db'));
    fs.writeFileSync(file, JSON.stringify({ ...prepared, databaseFile: path.join(dir, 'data/other.db') }));
    assert.throws(() => readPreparedTarget(file, journal, dir), /DEPLOYMENT_PREPARED_TARGET_INVALID/);
  } finally { fs.rmSync(parent, { recursive: true }); }
});

test('actual runLocal rejects conflicting plan/confirmation before creating installation state', async () => {
  const oldPlatform = os.platform, oldArch = os.arch;
  os.platform = () => 'darwin'; os.arch = () => 'arm64';
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-plan-'));
  const dir = path.join(parent, 'installation');
  try {
    const code = await runLocal(['install', '--dir', dir, '--plan', '--confirm-plan', 'a'.repeat(64)]);
    assert.equal(code, 1);
    assert.equal(fs.existsSync(dir), false);
  } finally { os.platform = oldPlatform; os.arch = oldArch; fs.rmSync(parent, { recursive: true }); }
});

test('actual runLocal will not materialize a missing upgrade or accept obsolete database arguments', async () => {
  const oldPlatform = os.platform, oldArch = os.arch;
  os.platform = () => 'darwin'; os.arch = () => 'arm64';
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-upgrade-'));
  const dir = path.join(parent, 'installation');
  try {
    assert.equal(await runLocal(['upgrade', '--dir', dir, '--plan']), 1);
    assert.equal(await runLocal(['install', '--dir', dir, '--db-port', '45678', '--plan']), 1);
    assert.equal(fs.existsSync(dir), false);
  } finally { os.platform = oldPlatform; os.arch = oldArch; fs.rmSync(parent, { recursive: true }); }
});

test('unknown path error containing DRIFT or CHANGED never echoes synthetic secret', async () => {
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-private-'));
  const original = console.error, messages = [];
  console.error = value => messages.push(String(value));
  try {
    for (const marker of ['DRIFT', 'CHANGED']) {
      const link = path.join(parent, `${marker}-synthetic-secret`);
      fs.symlinkSync(parent, link);
      assert.equal(await runLocal(['install', '--dir', path.join(link, 'installation')]), 1);
    }
    assert.equal(messages.length, 2);
    assert.ok(messages.every(message => message.includes('UNEXPECTED') && !message.includes('synthetic-secret') && !message.includes('漂移')));
  } finally { console.error = original; fs.rmSync(parent, { recursive: true }); }
});

test('unknown all-uppercase exception is not treated as a trusted machine error code', async () => {
  const originalPlatform = os.platform, originalError = console.error, messages = [];
  os.platform = () => { throw new Error('SYNTHETIC_SECRET_TOKEN'); };
  console.error = value => messages.push(String(value));
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-uppercase-'));
  try {
    assert.equal(await runLocal(['install', '--dir', path.join(parent, 'installation')]), 1);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /UNEXPECTED/);
    assert.doesNotMatch(messages[0], /SYNTHETIC_SECRET_TOKEN/);
  } finally { os.platform = originalPlatform; console.error = originalError; fs.rmSync(parent, { recursive: true }); }
});

test('trusted extension identity mismatch has a fixed Chinese diagnostic rather than UNEXPECTED', async () => {
  const originalPlatform = os.platform, originalError = console.error, messages = [];
  os.platform = () => { throw new Error('EXTENSION_IDENTITY_MISMATCH'); };
  console.error = value => messages.push(String(value));
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-extension-'));
  try {
    assert.equal(await runLocal(['install', '--dir', path.join(parent, 'installation')]), 1);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /EXTENSION_IDENTITY_MISMATCH.*停止后续部署/);
    assert.doesNotMatch(messages[0], /UNEXPECTED/);
  } finally { os.platform = originalPlatform; console.error = originalError; fs.rmSync(parent, { recursive: true }); }
});

test('known build failure reports its actual stage without copying the command exception', async () => {
  const originalPlatform = os.platform, originalError = console.error, messages = [];
  os.platform = () => { throw new Error('构建 API 镜像失败（退出码 125）；synthetic-secret'); };
  console.error = value => messages.push(String(value));
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-build-error-'));
  try {
    assert.equal(await runLocal(['install', '--dir', path.join(parent, 'installation')]), 1);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /^\[LexiFlow\] 构建 API 镜像失败：/);
    assert.doesNotMatch(messages[0], /检查参数|synthetic-secret|退出码 125/);
  } finally { os.platform = originalPlatform; console.error = originalError; fs.rmSync(parent, { recursive: true }); }
});

test('broad data failure does not guess that the request or dataset is invalid', async () => {
  const originalPlatform = os.platform, originalError = console.error, messages = [];
  os.platform = () => { throw new Error('DEPLOYMENT_DATABASE_FAIL:REQUEST_OR_DATA_INVALID'); };
  console.error = value => messages.push(String(value));
  const parent = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'lexiflow-local-data-error-'));
  try {
    assert.equal(await runLocal(['install', '--dir', path.join(parent, 'installation')]), 1);
    assert.equal(messages.length, 1);
    assert.match(messages[0], /数据库资料处理失败.*无法确定更具体原因/);
    assert.doesNotMatch(messages[0], /请求或资料校验失败|UNEXPECTED/);
  } finally { os.platform = originalPlatform; console.error = originalError; fs.rmSync(parent, { recursive: true }); }
});
