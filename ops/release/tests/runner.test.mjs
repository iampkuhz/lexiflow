import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile, rm, realpath, chmod, symlink } from 'node:fs/promises';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRunnerManager, parseLocalServiceState, runRunnerCommand, selectRunnerMetadata, validateRunnerMetadata, validateRunnerArchiveEntries } from '../runner/runner.mjs';

const metadata = { os: 'osx', architecture: 'arm64', filename: 'actions-runner-osx-arm64-2.321.0.tar.gz',
  download_url: 'https://github.com/actions/runner/releases/download/v2.321.0/actions-runner-osx-arm64-2.321.0.tar.gz', sha256_checksum: 'a'.repeat(64) };

test('runner download metadata is pinned to official osx arm64 release and digest', () => {
  assert.equal(validateRunnerMetadata(metadata), metadata);
  for (const patch of [{ os: 'linux' }, { architecture: 'x64' }, { download_url: 'https://evil.invalid/a' },
    { filename: '../runner.tar.gz' }, { sha256_checksum: 'xyz' }, { download_url: metadata.download_url.replace('v2.321.0', 'v2.321.1') }]) {
    assert.throws(() => validateRunnerMetadata({ ...metadata, ...patch }), /RUNNER_METADATA_INVALID/);
  }
  assert.equal(selectRunnerMetadata([metadata, { ...metadata, os: 'linux' }]), metadata);
  assert.throws(() => selectRunnerMetadata([metadata, metadata]), /RUNNER_METADATA_INVALID/);
});

test('runner command rejects free-form command names without side effects', async () => {
  assert.deepEqual(await runRunnerCommand('arbitrary-command'), { status: 'FAIL', reason: 'ARGUMENTS_INVALID' });
});
test('local service status is tri-state; exit-zero or unknown text is not treated as running', () => {
  assert.equal(parseLocalServiceState('Service is running'), 'running');
  assert.equal(parseLocalServiceState('Service is stopped'), 'stopped');
  assert.equal(parseLocalServiceState('Service is not running'), 'stopped');
  assert.equal(parseLocalServiceState('exit 0'), 'unknown');
});

test('official relative npm symlinks resolve only to archive regular files', () => {
  const entries = ['./', './externals/node24/bin/npm', './externals/node24/lib/npm-cli.js'];
  assert.doesNotThrow(() => validateRunnerArchiveEntries(entries, ['d', 'l', '-'], [null, '../lib/npm-cli.js', null]));
  for (const target of ['/outside', '../../../../outside', '../missing', 'npm']) {
    assert.throws(() => validateRunnerArchiveEntries(entries, ['d', 'l', '-'], [null, target, null]), /RUNNER_ARCHIVE_UNSAFE/);
  }
  assert.throws(() => validateRunnerArchiveEntries(['a', 'a/child', 'target'], ['l', '-', '-'], ['target']), /RUNNER_ARCHIVE_UNSAFE/);
  assert.throws(() => validateRunnerArchiveEntries(['a', 'b', 'target'], ['l', 'l', '-'], ['b', 'target']), /RUNNER_ARCHIVE_UNSAFE/);
  assert.throws(() => validateRunnerArchiveEntries(['./'], ['-']), /RUNNER_ARCHIVE_UNSAFE/);
  assert.throws(() => validateRunnerArchiveEntries(['a', 'a'], ['-', '-']), /RUNNER_ARCHIVE_UNSAFE/);
});
test('archive accepts the official root directory entry but rejects traversal and unbound links', () => {
  assert.doesNotThrow(() => validateRunnerArchiveEntries(['./', './config.sh'], ['d', '-']));
  assert.throws(() => validateRunnerArchiveEntries(['../outside'], ['-']), /RUNNER_ARCHIVE_UNSAFE/);
  assert.throws(() => validateRunnerArchiveEntries(['link'], ['l']), /RUNNER_ARCHIVE_UNSAFE/);
  assert.throws(() => validateRunnerArchiveEntries(['/absolute'], ['-']), /RUNNER_ARCHIVE_UNSAFE/);
});

test('release workflow CLI routes runner help without changing prepare/resume contract', () => {
  const cli = fileURLToPath(new URL('../workflow/run-release-workflow.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [cli, 'runner', '--help'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.deepEqual(JSON.parse(result.stdout).commands, ['doctor', 'deps', 'install', 'register', 'start', 'stop', 'status', 'setup']);
});

test('install/register/start/stop use owned files, token env only, and verified service/remote state', async () => {
  const tempRoot = await realpath(os.tmpdir());
  const home = await mkdtemp(path.join(tempRoot, 'lexiflow-runner-'));
  const source = await mkdtemp(path.join(tempRoot, 'lexiflow-tar-'));
  const runner = path.join(home, '.local/share/lexiflow/actions-runner');
  await mkdir(path.join(source, 'bin'), { recursive: true }); await writeFile(path.join(source, 'config.sh'), '#!/bin/bash\n');
  await writeFile(path.join(source, 'bin/Runner.Listener'), 'fixture runner listener');
  await chmod(path.join(source, 'config.sh'), 0o755); await chmod(path.join(source, 'bin/Runner.Listener'), 0o755);
  await mkdir(path.join(source, 'externals/node24/bin'), { recursive: true });
  await mkdir(path.join(source, 'externals/node24/lib'), { recursive: true });
  await writeFile(path.join(source, 'externals/node24/lib/npm-cli.js'), 'fixture npm');
  await symlink('../lib/npm-cli.js', path.join(source, 'externals/node24/bin/npm'));
  const archive = path.join(source, 'bundle.tar.gz');
  const tar = spawnSync('tar', ['-czf', archive, '-C', source, '.']); assert.equal(tar.status, 0);
  const archiveBytes = await readFile(archive); const checksum = createHash('sha256').update(archiveBytes).digest('hex');
  let online = false; let busy = false; let busyKnown = true; let remoteUnknown = false; let ghAuth = true;
  let admin = true; let incompleteList = false; let offlineDelay = 0;
  let rawTokenSeen = false; let remoteCreates = 0; let installCalls = 0; let startCalls = 0; const calls = [];
  const execute = async (command, args, options = {}) => {
    calls.push([command, ...args]);
    if (command === 'tar' && ['-tvzf', '-tzf'].includes(args[0])) {
      assert.equal(options.stdoutOnly, true);
      assert.equal(options.maxOutputBytes, 4 * 1024 * 1024);
    }
    if (command === 'brew' && args[0] === '--prefix') return args[1] === 'python@3.14' ? '/opt/homebrew/opt/python@3.14' : '/opt/homebrew';
    if (command === 'curl') {
      assert.deepEqual(args.slice(0, 15), ['--fail', '--silent', '--show-error', '--location', '--max-time', '120', '--connect-timeout', '15', '--max-filesize', '300000000', '--proto', '=https', '--proto-redir', '=https', '--output']);
      assert.equal(args[16], '--write-out'); assert.equal(args[17], '%{url_effective}'); assert.equal(args[18], metadata.download_url); assert.equal(options.env, undefined);
      await writeFile(args[15], archiveBytes); return 'https://release-assets.githubusercontent.com/runner-asset';
    }
    if (command === 'gh' && args[0] === 'auth') { if (!ghAuth) throw new Error('not logged in'); return ''; }
    if (command === 'gh' && args[0] === 'api') {
      const route = args.at(-1);
      if (route === 'repos/iampkuhz/lexiflow/actions/runners/downloads') return JSON.stringify(metadataWithHash(checksum));
      if (route === 'repos/iampkuhz/lexiflow') return JSON.stringify({ permissions: { admin } });
      if (route === 'repos/iampkuhz/lexiflow/actions/runners?per_page=100') {
        const runners = remoteCreates ? [{ id: 77, name: 'lexiflow-release-testhost', os: 'macOS', status: online ? 'online' : 'offline', busy, labels: [{ name: 'lexiflow-release' }] }] : [];
        return JSON.stringify({ total_count: incompleteList ? 101 : runners.length, runners });
      }
      if (args.some(x => x.endsWith('/registration-token'))) { remoteCreates++; return JSON.stringify({ token: 'never-print-this-registration-token' }); }
      if (route.endsWith('/77')) {
        if (remoteUnknown) throw new Error('remote unavailable');
        if (offlineDelay > 0) { offlineDelay--; if (offlineDelay === 0) online = false; }
        return JSON.stringify({ id: 77, name: 'lexiflow-release-testhost', os: 'macOS', status: online ? 'online' : 'offline', ...(busyKnown ? { busy } : {}), labels: [{ name: 'lexiflow-release' }] });
      }
      throw new Error(`unexpected gh route ${route}`);
    }
    if (command === '/bin/bash' && args[0] === './config.sh') {
      rawTokenSeen = options.env.ACTIONS_RUNNER_INPUT_TOKEN === 'never-print-this-registration-token' && !args.includes(options.env.ACTIONS_RUNNER_INPUT_TOKEN);
      await writeFile(path.join(runner, '.runner'), '\uFEFF' + JSON.stringify({ agentId: 77, agentName: 'lexiflow-release-testhost', gitHubUrl: 'https://github.com/iampkuhz/lexiflow' }));
      await writeFile(path.join(runner, 'svc.sh'), '#!/bin/bash\n', { mode: 0o755 });
      return 'never-print-this-registration-token';
    }
    if (command === '/bin/bash' && args[0] === './svc.sh') {
      if (args[1] === 'status') return online ? 'Service is running' : 'Service is stopped';
      if (args[1] === 'install') { installCalls++; return 'installed'; }
      if (args[1] === 'start') { startCalls++; online = true; return 'started'; }
      if (args[1] === 'stop') { offlineDelay = 2; return 'stopped'; }
      throw new Error('unexpected service action');
    }
    return spawnSync(command, args, { cwd: options.cwd, encoding: 'utf8' }).stdout;
  };
  const manager = createRunnerManager({ home, user: 'test-user', hostname: 'testhost', platform: 'darwin', arch: 'arm64', execute, sleep: async () => {} });
  try {
    const installed = await manager.run('install'); assert.equal(installed.status, 'PASS', JSON.stringify(installed));
    assert.equal((await manager.run('install')).status, 'PASS');
    ghAuth = false; const noAuth = await manager.run('setup'); assert.equal(noAuth.status, 'BLOCKED'); assert.equal(noAuth.guidance, 'Run gh auth login and retry.'); ghAuth = true;
    admin = false; assert.equal((await manager.run('register')).status, 'BLOCKED'); admin = true;
    incompleteList = true; assert.equal((await manager.run('register')).reason, 'REMOTE_RUNNER_LIST_INCOMPLETE'); incompleteList = false;
    remoteUnknown = true;
    assert.equal((await manager.run('register')).reason, 'REMOTE_STATE_UNKNOWN');
    const pending = JSON.parse(await readFile(path.join(home, '.local/share/lexiflow/runner-state.json'), 'utf8'));
    assert.equal(pending.registration, 'unknown'); assert.equal(pending.registered, false);
    remoteUnknown = false;
    const registered = await manager.run('register'); assert.equal(registered.status, 'PASS', `${JSON.stringify(registered)} ${JSON.stringify(calls)}`);
    assert.equal(JSON.stringify(registered).includes('never-print-this-registration-token'), false);
    assert.equal(rawTokenSeen, true); assert.equal(remoteCreates, 1);
    assert.equal((await manager.run('register')).status, 'PASS'); assert.equal(remoteCreates, 1);
    remoteUnknown = true; assert.equal((await manager.run('status')).reason, 'REMOTE_STATE_UNKNOWN'); remoteUnknown = false;
    await writeFile(path.join(runner, '.env'), 'CUSTOM=value\n', { mode: 0o600 });
    await writeFile(path.join(runner, '.path'), '/usr/bin:/bin\n', { mode: 0o600 });
    const started = await manager.run('start'); assert.equal(started.stage, 'runner-online', JSON.stringify(started));
    const serviceEnv = await readFile(path.join(runner, '.env'), 'utf8');
    assert.match(serviceEnv, /^CUSTOM=value$/m); assert.match(serviceEnv, /PATH=\/opt\/homebrew\/bin:\/opt\/homebrew\/opt\/python@3\.14\/libexec\/bin:\/usr\/bin:\/bin/);
    await writeFile(path.join(runner, '.env'), serviceEnv.replace(/^PATH=.*$/m, 'PATH=/custom'), { mode: 0o600 });
    assert.equal((await manager.run('start')).stage, 'runner-online');
    assert.match(await readFile(path.join(runner, '.env'), 'utf8'), /PATH=.*\/custom/);
    assert.equal((await manager.run('start')).stage, 'runner-online');
    assert.equal(installCalls, 1); assert.equal(startCalls, 1);
    assert.equal((await manager.run('setup')).status, 'PASS'); assert.equal(startCalls, 1);
    busy = true; assert.equal((await manager.run('stop')).reason, 'RUNNER_BUSY');
    busy = false; busyKnown = false; assert.equal((await manager.run('stop')).reason, 'REMOTE_BINDING_MISMATCH'); busyKnown = true;
    assert.equal((await manager.run('stop')).stage, 'runner-stopped'); assert.equal(offlineDelay, 0);
    const stateContents = await readFile(path.join(home, '.local/share/lexiflow/runner-state.json'), 'utf8');
    assert.equal(stateContents.includes('never-print-this-registration-token'), false);
  } finally { await rm(home, { recursive: true, force: true }); await rm(source, { recursive: true, force: true }); }
});

test('unique release label blocks registration, reconciliation and start but permits safe local stop', async () => {
  const home = await mkdtemp(path.join(await realpath(os.tmpdir()), 'lexiflow-runner-label-'));
  const runner = path.join(home, '.local/share/lexiflow/actions-runner');
  const stateFile = path.join(home, '.local/share/lexiflow/runner-state.json');
  await mkdir(path.join(runner, 'bin'), { recursive: true }); await chmod(runner, 0o700);
  for (const file of ['config.sh', 'bin/Runner.Listener', 'svc.sh']) await writeFile(path.join(runner, file), '#!/bin/bash\n', { mode: 0o755 });
  const initial = { schema: 1, repo: 'https://github.com/iampkuhz/lexiflow', user: 'test-user', name: 'lexiflow-release-testhost', os: 'osx', architecture: 'arm64', directory: runner, registered: false, registration: 'not-started', serviceInstalled: false };
  await writeFile(stateFile, JSON.stringify(initial), { mode: 0o600 });
  const owned = { id: 77, name: initial.name, os: 'macOS', status: 'offline', busy: false, labels: [{ name: 'lexiflow-release' }] };
  const foreign = { id: 88, name: 'other-host', os: 'Linux', status: 'offline', busy: false, labels: ['lexiflow-release'] };
  let rows = [foreign]; let tokenPosts = 0; let configCalls = 0; let serviceCalls = 0;
  const execute = async (command, args) => {
    if (command === 'gh' && args[0] === 'auth') return '';
    if (command === 'gh' && args[0] === 'api') {
      const route = args.at(-1);
      if (route === 'repos/iampkuhz/lexiflow') return JSON.stringify({ permissions: { admin: true } });
      if (route.endsWith('?per_page=100')) return JSON.stringify({ total_count: rows.length, runners: rows });
      if (route.endsWith('/registration-token')) { tokenPosts++; return JSON.stringify({ token: 'private-fixture-registration-token' }); }
      if (route.endsWith('/77')) return JSON.stringify(owned);
    }
    if (command === '/bin/bash' && args[0] === './config.sh') {
      configCalls++; rows = [owned, foreign];
      await writeFile(path.join(runner, '.runner'), '\uFEFF' + JSON.stringify({ agentId: 77, agentName: initial.name, gitHubUrl: initial.repo }));
      return '';
    }
    if (command === '/bin/bash' && args[0] === './svc.sh') { serviceCalls++; assert.equal(args[1], 'status'); return 'Service is stopped'; }
    throw new Error('unexpected command');
  };
  const manager = createRunnerManager({ home, user: 'test-user', hostname: 'testhost', platform: 'darwin', arch: 'arm64', execute, sleep: async () => {} });
  try {
    assert.deepEqual(await manager.run('register'), { status: 'BLOCKED', reason: 'RELEASE_RUNNER_LABEL_CONFLICT' });
    assert.deepEqual(JSON.parse(await readFile(stateFile, 'utf8')), initial);
    assert.equal(tokenPosts, 0); assert.equal(configCalls, 0);
    rows = [{ ...foreign, name: initial.name, labels: [] }];
    assert.equal((await manager.run('register')).reason, 'REMOTE_RUNNER_ALREADY_EXISTS');
    rows = [{ ...foreign, labels: undefined }];
    assert.equal((await manager.run('register')).reason, 'REMOTE_RUNNER_LIST_INCOMPLETE');
    rows = [];
    // A second labelled host appears during config: retain uncertain state, never claim successful registration.
    assert.equal((await manager.run('register')).reason, 'RELEASE_RUNNER_LABEL_CONFLICT');
    assert.equal(JSON.parse(await readFile(stateFile, 'utf8')).registered, false);
    assert.equal((await manager.run('register')).reason, 'RELEASE_RUNNER_LABEL_CONFLICT');
    assert.equal(tokenPosts, 1); assert.equal(configCalls, 1);
    rows = [owned, { ...foreign, labels: ['unrelated-label'] }];
    assert.equal((await manager.run('register')).status, 'PASS');
    assert.equal(tokenPosts, 1); assert.equal(configCalls, 1);
    rows = [owned, foreign];
    for (const command of ['register', 'setup', 'start', 'status']) {
      assert.deepEqual(await manager.run(command), { status: 'BLOCKED', reason: 'RELEASE_RUNNER_LABEL_CONFLICT' });
    }
    assert.equal(serviceCalls, 0);
    assert.equal((await manager.run('stop')).stage, 'runner-stopped');
    assert.equal(tokenPosts, 1); assert.equal(configCalls, 1);
    rows = [];
    assert.equal((await manager.run('register')).reason, 'REMOTE_BINDING_MISMATCH');
  } finally { await rm(home, { recursive: true, force: true }); }
});

test('checksum mismatch and pre-existing unknown install directories fail without overwriting', async () => {
  const tempRoot = await realpath(os.tmpdir()); const home = await mkdtemp(path.join(tempRoot, 'lexiflow-runner-'));
  const runner = path.join(home, '.local/share/lexiflow/actions-runner');
  const execute = async (command, args) => command === 'gh' && args.at(-1) === 'repos/iampkuhz/lexiflow/actions/runners/downloads'
    ? JSON.stringify(metadata) : spawnSync(command, args, { encoding: 'utf8' }).stdout;
  const manager = createRunnerManager({ home, user: 'test-user', hostname: 'testhost', platform: 'darwin', arch: 'arm64', execute,
    download: async (_url, target) => { await writeFile(target, 'bad archive', { flag: 'wx' }); } });
  try {
    const failed = await manager.run('install'); assert.equal(failed.reason, 'RUNNER_CHECKSUM_MISMATCH');
    await mkdir(runner, { recursive: true }); await writeFile(path.join(runner, 'keep.txt'), 'unchanged');
    const unknown = await manager.run('install'); assert.equal(unknown.reason, 'RUNNER_DIRECTORY_OWNED_BY_UNKNOWN');
    assert.equal(await readFile(path.join(runner, 'keep.txt'), 'utf8'), 'unchanged');
  } finally { await rm(home, { recursive: true, force: true }); }
});

test('download transport failure is sanitized and ancestor symlink is rejected', async () => {
  const tempRoot = await realpath(os.tmpdir()); const home = await mkdtemp(path.join(tempRoot, 'lexiflow-path-'));
  const symlinkHome = await mkdtemp(path.join(tempRoot, 'lexiflow-symlink-'));
  const execute = async (command, args) => {
    if (command === 'gh' && args.at(-1) === 'repos/iampkuhz/lexiflow/actions/runners/downloads') return JSON.stringify(metadata);
    if (command === 'curl') throw Object.assign(new Error('proxy secret leaked internally'), { publicOutput: 'proxy authorization secret' });
    throw new Error('unexpected command');
  };
  const manager = createRunnerManager({ home, user: 'test-user', hostname: 'testhost', platform: 'darwin', arch: 'arm64', execute });
  try {
    const result = await manager.run('install'); assert.equal(result.reason, 'RUNNER_DOWNLOAD_FAILED');
    assert.equal(JSON.stringify(result).includes('proxy authorization secret'), false);
    await symlink(tempRoot, path.join(symlinkHome, '.local'));
    const symlinkManager = createRunnerManager({ home: symlinkHome, user: 'test-user', hostname: 'testhost', platform: 'darwin', arch: 'arm64', execute });
    assert.equal((await symlinkManager.run('status')).reason, 'PATH_UNSAFE');
  } finally { await rm(home, { recursive: true, force: true }); await rm(symlinkHome, { recursive: true, force: true }); }
});

test('config failure stdout is suppressed and persisted registration state requires recovery', async () => {
  const tempRoot = await realpath(os.tmpdir()); const home = await mkdtemp(path.join(tempRoot, 'lexiflow-register-fail-'));
  const source = await mkdtemp(path.join(tempRoot, 'lexiflow-register-archive-'));
  const runner = path.join(home, '.local/share/lexiflow/actions-runner');
  await mkdir(path.join(source, 'bin'), { recursive: true }); await writeFile(path.join(source, 'config.sh'), '#!/bin/bash\n');
  await writeFile(path.join(source, 'bin/Runner.Listener'), 'fixture runner listener');
  await chmod(path.join(source, 'config.sh'), 0o755); await chmod(path.join(source, 'bin/Runner.Listener'), 0o755);
  const archive = path.join(source, 'bundle.tar.gz'); assert.equal(spawnSync('tar', ['-czf', archive, '-C', source, '.']).status, 0);
  const archiveBytes = await readFile(archive); const checksum = createHash('sha256').update(archiveBytes).digest('hex');
  const execute = async (command, args) => {
    if (command === 'gh' && args[0] === 'auth') return '';
    if (command === 'gh' && args[0] === 'api') {
      const route = args.at(-1);
      if (route === 'repos/iampkuhz/lexiflow/actions/runners/downloads') return JSON.stringify(metadataWithHash(checksum));
      if (route === 'repos/iampkuhz/lexiflow') return JSON.stringify({ permissions: { admin: true } });
      if (route === 'repos/iampkuhz/lexiflow/actions/runners?per_page=100') return JSON.stringify({ total_count: 0, runners: [] });
      if (route.endsWith('/registration-token')) return JSON.stringify({ token: 'secret-registration-token-value' });
    }
    if (command === 'curl') { await writeFile(args[15], archiveBytes); return 'https://release-assets.githubusercontent.com/asset'; }
    if (command === 'tar') { const result = spawnSync(command, args, { encoding: 'utf8' }); if (result.status !== 0) throw new Error('tar failed'); return result.stdout; }
    if (command === '/bin/bash' && args[0] === './config.sh') throw Object.assign(new Error('do not expose'), { publicOutput: 'secret-registration-token-value' });
    throw new Error('unexpected command');
  };
  const manager = createRunnerManager({ home, user: 'test-user', hostname: 'failurehost', platform: 'darwin', arch: 'arm64', execute });
  try {
    const installed = await manager.run('install'); assert.equal(installed.status, 'PASS', JSON.stringify(installed));
    const failed = await manager.run('register'); assert.deepEqual(failed, { status: 'FAIL', reason: 'REGISTRATION_OUTCOME_UNKNOWN' });
    assert.equal(JSON.stringify(failed).includes('secret-registration-token-value'), false);
    const state = await readFile(path.join(home, '.local/share/lexiflow/runner-state.json'), 'utf8');
    assert.equal(JSON.parse(state).registration, 'unknown'); assert.equal(state.includes('secret-registration-token-value'), false);
    assert.equal((await manager.run('register')).reason, 'REGISTRATION_RECOVERY_REQUIRED');
  } finally { await rm(home, { recursive: true, force: true }); await rm(source, { recursive: true, force: true }); }
});

test('deps preserves Docker config fields atomically and rejects malformed plugin config', async () => {
  const tempRoot = await realpath(os.tmpdir()); const home = await mkdtemp(path.join(tempRoot, 'lexiflow-docker-'));
  const dockerDir = path.join(home, '.docker'); await mkdir(dockerDir);
  const config = path.join(dockerDir, 'config.json');
  const manager = createRunnerManager({ home, execute: async (command, args) => {
    if (command === 'brew' && args[0] === 'bundle') return '';
    if (command === 'brew' && args[0] === '--prefix') return '/opt/homebrew';
    throw new Error('unexpected command');
  } });
  try {
    await writeFile(config, JSON.stringify({ auths: { registry: { auth: 'redacted-test-value' } }, currentContext: 'desktop-linux' }), { mode: 0o600 });
    assert.equal((await manager.run('deps')).status, 'PASS');
    let saved = JSON.parse(await readFile(config, 'utf8'));
    assert.deepEqual(saved.auths, { registry: { auth: 'redacted-test-value' } });
    assert.equal(saved.currentContext, 'desktop-linux');
    assert.deepEqual(saved.cliPluginsExtraDirs, ['/opt/homebrew/lib/docker/cli-plugins']);
    await writeFile(config, JSON.stringify({ cliPluginsExtraDirs: 'bad' }), { mode: 0o600 });
    assert.equal((await manager.run('deps')).reason, 'DOCKER_CONFIG_INVALID');
  } finally { await rm(home, { recursive: true, force: true }); }
});

function metadataWithHash(sha256_checksum) { return { ...metadata, sha256_checksum }; }
