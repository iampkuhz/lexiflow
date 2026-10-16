import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { chmod, lstat, mkdir, open, readFile, rename, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { hostname, homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { AsyncLocalStorage } from 'node:async_hooks';

const REPO = 'https://github.com/iampkuhz/lexiflow';
const OWNER = 'iampkuhz'; const NAME = 'lexiflow'; const LABEL = 'lexiflow-release';
const context = new AsyncLocalStorage();
const currentDeps = () => context.getStore() || {};
const home = () => currentDeps().home || homedir();
const runnerDir = () => path.join(home(), '.local/share/lexiflow/actions-runner');
const stateFile = () => path.join(home(), '.local/share/lexiflow/runner-state.json');
const BREWFILE = fileURLToPath(new URL('./Brewfile', import.meta.url));
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const fail = reason => { throw Object.assign(new Error(reason), { reason }); };
const runnerName = () => `lexiflow-release-${currentDeps().hostname || hostname()}`;

function execReal(command, args, { cwd, env = process.env, timeout = 120_000, stdoutOnly = false, maxOutputBytes = 1_000_000 } = {}) {
  return new Promise((resolve, reject) => {
    let child; try { child = spawn(command, args, { cwd, env, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] }); } catch { reject(new Error('CHILD_START_FAILED')); return; }
    let out = ''; let err = ''; let size = 0; let terminal; let settled = false;
    const killGroup = signal => { if (!child.pid) return; try { process.kill(process.platform === 'win32' ? child.pid : -child.pid, signal); } catch { /* already exited */ } };
    const onSignal = () => { terminal = 'CHILD_CANCELLED'; killGroup('SIGTERM'); setTimeout(() => killGroup('SIGKILL'), 1500).unref(); };
    const timer = setTimeout(() => { terminal = 'CHILD_TIMEOUT'; killGroup('SIGTERM'); setTimeout(() => killGroup('SIGKILL'), 1500).unref(); }, timeout);
    process.on('SIGINT', onSignal); process.on('SIGTERM', onSignal);
    for (const stream of [child.stdout, child.stderr]) stream.on('data', b => {
      size += b.length; if (size > maxOutputBytes) { terminal = 'CHILD_OUTPUT_LIMIT'; killGroup('SIGKILL'); return; }
      if (stream === child.stdout) out += b.toString(); else err += b.toString();
    });
    const cleanup = () => { if (settled) return; settled = true; clearTimeout(timer); process.off('SIGINT', onSignal); process.off('SIGTERM', onSignal); };
    child.on('error', () => { cleanup(); reject(new Error('CHILD_START_FAILED')); });
    child.on('close', code => { cleanup(); const output = stdoutOnly ? out : out + err; code === 0 && !terminal ? resolve(output) : reject(Object.assign(new Error(terminal || 'CHILD_FAILED'), { publicOutput: out + err })); });
  });
}
function exec(command, args, options = {}) { return currentDeps().execute ? currentDeps().execute(command, args, options) : execReal(command, args, options); }
const pause = ms => currentDeps().sleep ? currentDeps().sleep(ms) : new Promise(resolve => setTimeout(resolve, ms));
async function checkedDir(dir, create = false) {
  let cursor = path.parse(dir).root;
  for (const segment of dir.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, segment);
    const s = await lstat(cursor).catch(e => e.code === 'ENOENT' ? null : fail('PATH_UNSAFE'));
    if (s?.isSymbolicLink() || (s && !s.isDirectory())) fail('PATH_UNSAFE');
    if (!s && create) await mkdir(cursor, { mode: 0o700 }).catch(e => e.code === 'EEXIST' ? undefined : fail('PATH_UNSAFE'));
  }
}
async function safeStateRead(file = stateFile()) {
  await checkedDir(path.dirname(file), false);
  const s = await lstat(file).catch(e => e.code === 'ENOENT' ? null : fail('STATE_INVALID'));
  if (!s) return null;
  if (s.isSymbolicLink() || !s.isFile() || (s.mode & 0o077)) fail('STATE_UNSAFE');
  let value; try { value = JSON.parse(await readFile(file, 'utf8')); } catch { fail('STATE_INVALID'); }
  if (value?.schema !== 1 || value.repo !== REPO || value.user !== (currentDeps().user || process.env.USER) || !SAFE_NAME.test(value.name || '')
    || value.directory !== runnerDir() || value.os !== 'osx' || value.architecture !== 'arm64'
    || typeof value.registered !== 'boolean' || typeof value.serviceInstalled !== 'boolean' || !['not-started', 'unknown', 'complete'].includes(value.registration)
    || (value.registered && !Number.isSafeInteger(value.runnerId))) fail('STATE_INVALID');
  return value;
}
async function safeStateWrite(value, file = stateFile()) {
  await checkedDir(path.dirname(file), true);
  const existing = await lstat(file).catch(e => e.code === 'ENOENT' ? null : fail('STATE_UNSAFE'));
  if (existing && (existing.isSymbolicLink() || !existing.isFile() || (existing.mode & 0o077))) fail('STATE_UNSAFE');
  const temp = `${file}.${randomUUID()}.tmp`;
  const fd = await open(temp, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
  try { await fd.writeFile(JSON.stringify(value)); await fd.sync(); } finally { await fd.close(); }
  await rename(temp, file); await chmod(file, 0o600);
}
async function configureDockerPlugin() {
  const prefix = (await exec('brew', ['--prefix'])).trim();
  if (!/^\/[A-Za-z0-9_./@-]+$/.test(prefix)) fail('BREW_PREFIX_INVALID');
  const config = path.join(home(), '.docker/config.json'); await checkedDir(path.dirname(config), true);
  const info = await lstat(config).catch(e => e.code === 'ENOENT' ? null : fail('DOCKER_CONFIG_UNSAFE'));
  let value = {};
  if (info) {
    if (info.isSymbolicLink() || !info.isFile() || (info.mode & 0o077)) fail('DOCKER_CONFIG_UNSAFE');
    try { value = JSON.parse(await readFile(config, 'utf8')); } catch { fail('DOCKER_CONFIG_INVALID'); }
    if (!value || Array.isArray(value) || typeof value !== 'object') fail('DOCKER_CONFIG_INVALID');
  }
  const dirs = value.cliPluginsExtraDirs;
  const required = path.join(prefix, 'lib/docker/cli-plugins');
  if (dirs !== undefined && (!Array.isArray(dirs) || dirs.some(x => typeof x !== 'string' || !path.isAbsolute(x)))) fail('DOCKER_CONFIG_INVALID');
  if (Array.isArray(dirs) && dirs.includes(required)) return;
  value.cliPluginsExtraDirs = Array.isArray(dirs) ? [...dirs, required] : [required];
  const temp = `${config}.${randomUUID()}.tmp`;
  const fd = await open(temp, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
  try { await fd.writeFile(`${JSON.stringify(value, null, 2)}\n`); await fd.sync(); } finally { await fd.close(); }
  await rename(temp, config); await chmod(config, 0o600);
}
export function validateRunnerMetadata(m) {
  if (!m || m.os !== 'osx' || m.architecture !== 'arm64' || typeof m.filename !== 'string'
    || !/^actions-runner-osx-arm64-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$/.test(m.filename)
    || typeof m.download_url !== 'string' || !/^https:\/\/github\.com\/actions\/runner\/releases\/download\/v[0-9]+\.[0-9]+\.[0-9]+\/actions-runner-osx-arm64-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$/.test(m.download_url)
    || !/^[a-f0-9]{64}$/i.test(m.sha256_checksum || '')
    || !m.download_url.endsWith(`/${m.filename}`)
    || m.download_url.split('/').at(-2) !== `v${m.filename.match(/arm64-([0-9]+\.[0-9]+\.[0-9]+)/)?.[1]}`) fail('RUNNER_METADATA_INVALID');
  return m;
}
export function selectRunnerMetadata(payload) {
  const matches = Array.isArray(payload) ? payload.filter(item => item?.os === 'osx' && item?.architecture === 'arm64') : [payload];
  if (matches.length !== 1) fail('RUNNER_METADATA_INVALID');
  return validateRunnerMetadata(matches[0]);
}
async function download(url, target, expected) {
  if (currentDeps().download) {
    await currentDeps().download(url, target, expected);
    await verifyDownloadedDigest(target, expected);
    return;
  }
  const initial = new URL(url);
  if (initial.protocol !== 'https:' || initial.hostname !== 'github.com' || !initial.pathname.startsWith('/actions/runner/releases/download/')) fail('RUNNER_DOWNLOAD_URL_INVALID');
  const placeholder = await open(target, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
  await placeholder.close();
  let effective;
  try {
    effective = (await exec('curl', ['--fail', '--silent', '--show-error', '--location', '--max-time', '120', '--connect-timeout', '15',
      '--max-filesize', '300000000', '--proto', '=https', '--proto-redir', '=https', '--output', target, '--write-out', '%{url_effective}', url],
    { timeout: 123_000, stdoutOnly: true, maxOutputBytes: 4096 })).trim();
  } catch { fail('RUNNER_DOWNLOAD_FAILED'); }
  let finalUrl; try { finalUrl = new URL(effective); } catch { fail('RUNNER_DOWNLOAD_URL_INVALID'); }
  if (finalUrl.protocol !== 'https:' || !['github.com', 'release-assets.githubusercontent.com'].includes(finalUrl.hostname)
    || (finalUrl.hostname === 'github.com' && !finalUrl.pathname.startsWith('/actions/runner/releases/download/'))) fail('RUNNER_DOWNLOAD_URL_INVALID');
  await verifyDownloadedDigest(target, expected);
}
async function verifyDownloadedDigest(file, expected) {
  const info = await lstat(file).catch(() => null);
  if (!info || info.isSymbolicLink() || !info.isFile() || info.size < 1 || info.size > 300_000_000) fail('RUNNER_DOWNLOAD_INVALID');
  const fd = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  const hash = createHash('sha256'); const chunk = Buffer.alloc(64 * 1024); let bytes = 0;
  try { while (bytes < info.size) { const read = await fd.read(chunk, 0, Math.min(chunk.length, info.size - bytes), null); if (!read.bytesRead) break; bytes += read.bytesRead; hash.update(chunk.subarray(0, read.bytesRead)); } }
  finally { await fd.close(); }
  if (bytes !== info.size || hash.digest('hex').toLowerCase() !== expected.toLowerCase()) fail('RUNNER_CHECKSUM_MISMATCH');
}
async function archiveSafe(file) {
  const options = { stdoutOnly: true, maxOutputBytes: 4 * 1024 * 1024 };
  const listing = await exec('tar', ['-tvzf', file], options);
  const details = listing.split(/\r?\n/).filter(Boolean);
  if (details.some(line => !/^[-dl][rwxStTs-]{9}\s/.test(line))) fail('RUNNER_ARCHIVE_UNSAFE');
  const names = await exec('tar', ['-tzf', file], options);
  const entries = names.split(/\r?\n/).filter(Boolean);
  const targets = entries.map((entry, index) => {
    if (details[index]?.[0] !== 'l') return null;
    const marker = ` ${entry} -> `;
    const offset = details[index].lastIndexOf(marker);
    if (offset < 0) fail('RUNNER_ARCHIVE_UNSAFE');
    return details[index].slice(offset + marker.length);
  });
  validateRunnerArchiveEntries(entries, details.map(line => line[0]), targets);
}
export function validateRunnerArchiveEntries(entries, types, targets = []) {
  if (!entries.length || entries.length > 20_000 || entries.length !== types.length) fail('RUNNER_ARCHIVE_UNSAFE');
  const members = new Map();
  for (const [index, entry] of entries.entries()) {
    if (typeof entry !== 'string' || /[\u0000-\u001f\u007f\\]/.test(entry)
      || !['-', 'd', 'l'].includes(types[index])) fail('RUNNER_ARCHIVE_UNSAFE');
    const name = entry.replace(/^\.\//, '').replace(/\/$/, '');
    if (name === '' && entry === './' && types[index] === 'd') continue;
    if (!name || name === '.' || name.startsWith('/') || name.split('/').some(x => !x || x === '..' || x === '.')
      || members.has(name)) fail('RUNNER_ARCHIVE_UNSAFE');
    members.set(name, { type: types[index], target: targets[index] });
  }
  for (const [name, member] of members) {
    let parent = path.posix.dirname(name);
    while (parent !== '.') {
      if (members.has(parent) && members.get(parent).type !== 'd') fail('RUNNER_ARCHIVE_UNSAFE');
      parent = path.posix.dirname(parent);
    }
    if (member.type !== 'l') continue;
    const target = member.target;
    if (typeof target !== 'string' || !target || target.startsWith('/') || /[\u0000-\u001f\u007f\\]/.test(target)) fail('RUNNER_ARCHIVE_UNSAFE');
    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(name), target));
    if (resolved === '..' || resolved.startsWith('../') || members.get(resolved)?.type !== '-') fail('RUNNER_ARCHIVE_UNSAFE');
  }
}
async function getState() { return safeStateRead(); }
async function gh(args, options) { return exec('gh', args, options); }
async function remoteRunner(state) {
  if (!state?.runnerId) return { found: false };
  try { return JSON.parse(await gh(['api', `repos/${OWNER}/${NAME}/actions/runners/${state.runnerId}`])); }
  catch { fail('REMOTE_STATE_UNKNOWN'); }
}
async function assertUniqueReleaseRunner(state, { beforeRegistration = false } = {}) {
  let existing;
  try { existing = JSON.parse(await gh(['api', 'repos/iampkuhz/lexiflow/actions/runners?per_page=100'])); }
  catch { fail('REMOTE_STATE_UNKNOWN'); }
  if (!Array.isArray(existing.runners) || !Number.isSafeInteger(existing.total_count) || existing.total_count < 0
    || existing.total_count !== existing.runners.length
    || existing.runners.some(x => !Number.isSafeInteger(x?.id) || x.id < 1 || typeof x.name !== 'string'
      || !Array.isArray(x.labels) || x.labels.some(label => typeof label !== 'string' && typeof label?.name !== 'string'))
    || new Set(existing.runners.map(x => x.id)).size !== existing.runners.length) fail('REMOTE_RUNNER_LIST_INCOMPLETE');
  if (beforeRegistration && existing.runners.some(x => x.name === state.name)) fail('REMOTE_RUNNER_ALREADY_EXISTS');
  const labelled = existing.runners.filter(x => x.labels.some(label => (typeof label === 'string' ? label : label.name) === LABEL));
  if (labelled.some(x => beforeRegistration || x.id !== state.runnerId)) fail('RELEASE_RUNNER_LABEL_CONFLICT');
  if (!beforeRegistration) {
    if (labelled.length !== 1) fail('REMOTE_BINDING_MISMATCH');
    assertRemoteBinding(labelled[0], state);
  }
}
async function authCheck() {
  try { await gh(['auth', 'status']); } catch { fail('GH_AUTH_REQUIRED'); }
  let permission;
  try { permission = JSON.parse(await gh(['api', 'repos/iampkuhz/lexiflow']))?.permissions; }
  catch { fail('GH_REPO_ACCESS_UNKNOWN'); }
  if (!permission?.admin) fail('GH_ADMIN_REQUIRED');
}
function assertSupportedHost() { if ((currentDeps().platform || process.platform) !== 'darwin' || (currentDeps().arch || process.arch) !== 'arm64') fail('HOST_UNSUPPORTED'); }
async function readLocalRegistration(file) {
  try { return JSON.parse((await readFile(file, 'utf8')).replace(/^\uFEFF/, '')); }
  catch { fail('LOCAL_REGISTRATION_INVALID'); }
}
async function register(state) {
  assertSupportedHost();
  await authCheck();
  if (!state || state.name !== runnerName() || state.repo !== REPO) fail('RUNNER_BINDING_CONFLICT');
  await verifyOwnedRunner(state, { requireRunner: Boolean(state.registered) });
  const localFile = path.join(runnerDir(), '.runner');
  const localInfo = await lstat(localFile).catch(e => e.code === 'ENOENT' ? null : fail('LOCAL_REGISTRATION_INVALID'));
  if (localInfo && (localInfo.isSymbolicLink() || !localInfo.isFile())) fail('LOCAL_REGISTRATION_INVALID');
  if (state.registered || (state.registration === 'unknown' && localInfo)) {
    if (!localInfo) fail('LOCAL_REGISTRATION_INVALID');
    const local = await readLocalRegistration(localFile);
    if (!Number.isSafeInteger(local.agentId) || local.agentId < 1 || (state.registered && local.agentId !== state.runnerId)
      || local.agentName !== state.name || local.gitHubUrl !== REPO) fail('RUNNER_BINDING_CONFLICT');
    const verified = { ...state, runnerId: local.agentId, registered: true, registration: 'complete' };
    await verifyOwnedRunner(verified);
    assertRemoteBinding(await remoteRunner(verified), verified);
    await assertUniqueReleaseRunner(verified);
    if (!state.registered) await safeStateWrite(verified);
    return verified;
  }
  if (localInfo) fail('LOCAL_REGISTRATION_CONFLICT');
  if (state.registration !== 'not-started') fail('REGISTRATION_RECOVERY_REQUIRED');
  await assertUniqueReleaseRunner(state, { beforeRegistration: true });
  await safeStateWrite({ ...state, registration: 'unknown' });
  const tokenText = await gh(['api', '-X', 'POST', 'repos/iampkuhz/lexiflow/actions/runners/registration-token']);
  let token; try { token = JSON.parse(tokenText)?.token; } catch { fail('REGISTRATION_TOKEN_INVALID'); }
  if (typeof token !== 'string' || token.length < 20) fail('REGISTRATION_TOKEN_INVALID');
  try {
    await exec('/bin/bash', ['./config.sh', '--unattended', '--url', REPO, '--name', state.name, '--labels', LABEL, '--work', '_work'], {
      cwd: runnerDir(), env: { ...process.env, ACTIONS_RUNNER_INPUT_TOKEN: token },
    });
  } catch { await safeStateWrite({ ...state, registration: 'unknown' }); fail('REGISTRATION_OUTCOME_UNKNOWN'); }
  const local = await readLocalRegistration(localFile);
  const runnerId = local.agentId;
  if (!Number.isSafeInteger(runnerId) || local.agentName !== state.name || local.gitHubUrl !== REPO) fail('LOCAL_REGISTRATION_INVALID');
  const updated = { ...state, runnerId, registered: true, registration: 'complete' };
  await verifyOwnedRunner(updated);
  const remote = await remoteRunner(updated); assertRemoteBinding(remote, updated);
  await assertUniqueReleaseRunner(updated);
  await safeStateWrite(updated); return updated;
}
function assertRemoteBinding(remote, state) {
  const labels = Array.isArray(remote?.labels) ? remote.labels.map(x => typeof x === 'string' ? x : x.name) : [];
  if (remote?.id !== state.runnerId || remote?.name !== state.name || remote?.os !== 'macOS' || !labels.includes(LABEL) || typeof remote.busy !== 'boolean') fail('REMOTE_BINDING_MISMATCH');
  return remote;
}
async function localService(action) { return exec('/bin/bash', [`./svc.sh`, action], { cwd: runnerDir() }); }
async function configureServicePath() {
  const brewPrefix = (await exec('brew', ['--prefix'])).trim();
  const pythonPrefix = (await exec('brew', ['--prefix', 'python@3.14'])).trim();
  if (![brewPrefix, pythonPrefix].every(x => /^\/[A-Za-z0-9_./@-]+$/.test(x))) fail('BREW_PREFIX_INVALID');
  const envFile = path.join(runnerDir(), '.env'); const info = await lstat(envFile).catch(e => e.code === 'ENOENT' ? null : fail('RUNNER_ENV_UNSAFE'));
  let original = '';
  if (info) {
    if (info.isSymbolicLink() || !info.isFile() || (info.mode & 0o022) || info.size > 64 * 1024) fail('RUNNER_ENV_UNSAFE');
    original = await readFile(envFile, 'utf8');
  }
  const lines = original ? original.split(/\n/) : [];
  const pathLines = lines.filter(line => /^PATH=/.test(line));
  if (pathLines.length > 1) fail('RUNNER_ENV_INVALID');
  const pathCapture = path.join(runnerDir(), '.path'); const captureInfo = await lstat(pathCapture).catch(e => e.code === 'ENOENT' ? null : fail('RUNNER_PATH_UNSAFE'));
  let captured = '';
  if (captureInfo) {
    if (captureInfo.isSymbolicLink() || !captureInfo.isFile() || (captureInfo.mode & 0o022) || captureInfo.size > 16 * 1024) fail('RUNNER_PATH_UNSAFE');
    captured = (await readFile(pathCapture, 'utf8')).trim();
  }
  const existingEnvPath = pathLines.length ? pathLines[0].slice(5) : '';
  const basePath = captured || existingEnvPath || process.env.PATH || '/usr/bin:/bin:/usr/sbin:/sbin';
  const baseParts = basePath.split(':');
  if (baseParts.some(item => !item.startsWith('/') || /[\u0000-\u001f\u007f]/.test(item))) fail('RUNNER_PATH_INVALID');
  const required = [`${brewPrefix}/bin`, `${pythonPrefix}/libexec/bin`];
  const merged = [...required, ...baseParts, ...existingEnvPath.split(':').filter(Boolean)].filter((item, index, all) => all.indexOf(item) === index).join(':');
  if (pathLines.length) {
    lines[lines.indexOf(pathLines[0])] = `PATH=${merged}`;
  } else lines.push(`PATH=${merged}`);
  const updated = `${lines.join('\n').replace(/\n+$/, '')}\n`;
  if (updated === original) return;
  const temp = `${envFile}.${randomUUID()}.tmp`;
  const fd = await open(temp, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
  try { await fd.writeFile(updated); await fd.sync(); } finally { await fd.close(); }
  await rename(temp, envFile); await chmod(envFile, 0o600);
}
async function verifyOwnedRunner(state, { requireRunner = true } = {}) {
  await checkedDir(path.dirname(runnerDir()), false);
  const dirInfo = await lstat(runnerDir()).catch(() => null);
  if (!dirInfo || dirInfo.isSymbolicLink() || !dirInfo.isDirectory() || (dirInfo.mode & 0o077)) fail('RUNNER_DIRECTORY_UNSAFE');
  await checkedDir(path.join(runnerDir(), 'bin'), false);
  for (const name of (requireRunner ? ['config.sh', 'bin/Runner.Listener', 'svc.sh'] : ['config.sh', 'bin/Runner.Listener'])) {
    const script = await lstat(path.join(runnerDir(), name)).catch(() => null);
    if (!script || script.isSymbolicLink() || !script.isFile() || !(script.mode & 0o111)) fail('RUNNER_SCRIPT_UNSAFE');
  }
  if (requireRunner) {
    const runnerFile = path.join(runnerDir(), '.runner'); const info = await lstat(runnerFile).catch(() => null);
    if (!info || info.isSymbolicLink() || !info.isFile()) fail('LOCAL_REGISTRATION_INVALID');
    const local = await readLocalRegistration(runnerFile);
    if (local.agentId !== state.runnerId || local.agentName !== state.name || local.gitHubUrl !== REPO) fail('RUNNER_BINDING_CONFLICT');
  }
}
export function parseLocalServiceState(output) {
  if (/\b(?:Stopped|not installed|not loaded|not running)\b/i.test(output)) return 'stopped';
  if (/\b(?:Loaded|Running|started)\b|PID\s*=\s*[1-9]/i.test(output)) return 'running';
  return 'unknown';
}
async function statusCore({ verifyRouting = true } = {}) {
  const state = await getState();
  if (!state) return { status: 'BLOCKED', reason: 'RUNNER_NOT_INSTALLED' };
  await verifyOwnedRunner(state);
  if (verifyRouting) await assertUniqueReleaseRunner(state);
  const localOutput = await exec('/bin/bash', ['./svc.sh', 'status'], { cwd: runnerDir() }).catch(e => e.publicOutput || '');
  const local = parseLocalServiceState(localOutput);
  const remote = await remoteRunner(state);
  const labels = Array.isArray(remote.labels) ? remote.labels.map(x => typeof x === 'string' ? x : x.name) : [];
  const binding = remote.id === state.runnerId && remote.name === state.name && remote.os === 'macOS' && labels.includes(LABEL) && typeof remote.busy === 'boolean';
  return { status: binding ? 'PASS' : 'BLOCKED', ...(binding ? {} : { reason: 'REMOTE_BINDING_MISMATCH' }), local, remote: remote.status || 'unknown', busy: typeof remote.busy === 'boolean' ? remote.busy : null, name: state.name };
}
async function doInstall() {
  assertSupportedHost();
  await checkedDir(path.dirname(runnerDir()), true);
  const existing = await lstat(runnerDir()).catch(e => e.code === 'ENOENT' ? null : fail('PATH_UNSAFE'));
  if (existing) {
    const state = await getState();
    if (!state || state.directory !== runnerDir()) fail('RUNNER_DIRECTORY_OWNED_BY_UNKNOWN');
    await verifyOwnedRunner(state, { requireRunner: state.registered });
    return state;
  }
  const metadata = selectRunnerMetadata(JSON.parse(await gh(['api', 'repos/iampkuhz/lexiflow/actions/runners/downloads'])));
  const parent = path.dirname(runnerDir()); const temp = path.join(parent, `.runner-${randomUUID()}`);
  await mkdir(temp, { mode: 0o700 });
  try {
    const archive = path.join(temp, 'runner.tar.gz'); await download(metadata.download_url, archive, metadata.sha256_checksum);
    await archiveSafe(archive); const unpack = path.join(temp, 'unpacked'); await mkdir(unpack, { mode: 0o700 });
    await exec('tar', ['-xzf', archive, '-C', unpack]); await rm(archive);
    for (const relative of ['config.sh', 'bin/Runner.Listener']) {
      const item = await lstat(path.join(unpack, relative)).catch(() => null);
      if (!item || item.isSymbolicLink() || !item.isFile() || !(item.mode & 0o111)) fail('RUNNER_ARCHIVE_INVALID');
    }
    await chmod(unpack, 0o700); await rename(unpack, runnerDir());
    const state = { schema: 1, repo: REPO, user: currentDeps().user || process.env.USER, name: runnerName(), os: 'osx', architecture: 'arm64', directory: runnerDir(), registered: false, registration: 'not-started', serviceInstalled: false };
    await safeStateWrite(state); return state;
  } finally { await rm(temp, { recursive: true, force: true }); }
}
async function doctor() {
  const tools = {};
  for (const [key, args] of Object.entries({ gh: ['--version'], node: ['--version'], python3: ['--version'], podman: ['--version'], docker: ['--version'], 'docker-compose': ['version'] })) {
    try { await exec(key, args); tools[key] = true; } catch { tools[key] = false; }
  }
  let java25 = false;
  try { const o = await exec('python3', ['-m', 'scripts.local.run_with_java_toolchain', 'java', '-version'], { cwd: ROOT }); java25 = /25(?:\.|\s)/.test(o); } catch { /* existing project toolchain unavailable */ }
  let compose = false; try { await exec('docker', ['compose', 'version']); compose = true; } catch { /* report unavailable */ }
  let auth = false, repoAdmin = false; try { await gh(['auth', 'status']); auth = true; await authCheck(); repoAdmin = true; } catch { /* doctor is informational */ }
  let podman = false; try { await exec('podman', ['info', '--format', 'json']); podman = true; } catch { /* informational */ }
  const ready = Object.values(tools).every(Boolean) && java25 && auth && repoAdmin && podman && compose;
  return { status: ready ? 'PASS' : 'BLOCKED', ...(ready ? {} : { reason: 'DOCTOR_REQUIREMENTS_MISSING' }), tools, java25, ghAuth: auth, repoAdmin, podmanConnection: podman, dockerCompose: compose, note: '本机 runner 在线不代表正式发行就绪。' };
}
async function deps() {
  await exec('brew', ['bundle', '--no-upgrade', '--file', BREWFILE], { env: { ...process.env, HOMEBREW_NO_AUTO_UPDATE: '1', HOMEBREW_NO_INSTALL_CLEANUP: '1' } });
  await configureDockerPlugin();
  return { status: 'PASS', stage: 'dependencies-checked' };
}
async function start() {
  assertSupportedHost();
  const state = await getState(); if (!state?.registered) fail('RUNNER_NOT_REGISTERED');
  await verifyOwnedRunner(state);
  const remote = assertRemoteBinding(await remoteRunner(state), state); if (remote.busy) fail('RUNNER_BUSY');
  await assertUniqueReleaseRunner(state);
  await configureServicePath();
  const existingStatus = await statusCore();
  if (!state.serviceInstalled) { await localService('install'); state.serviceInstalled = true; await safeStateWrite(state); }
  if (existingStatus.local !== 'running') await localService('start');
  for (let i = 0; i < 20; i++) { const next = await statusCore(); if (next.local === 'running' && next.remote === 'online') return { status: 'PASS', stage: 'runner-online' }; await pause(1500); }
  fail('RUNNER_ONLINE_TIMEOUT');
}
async function stop() {
  assertSupportedHost();
  const state = await getState(); if (!state?.registered) fail('RUNNER_NOT_REGISTERED');
  await verifyOwnedRunner(state);
  const remote = assertRemoteBinding(await remoteRunner(state), state); if (remote.busy) fail('RUNNER_BUSY');
  const before = await statusCore({ verifyRouting: false });
  if (before.local === 'running') await localService('stop');
  let after;
  for (let i = 0; i < 20; i++) {
    after = await statusCore({ verifyRouting: false });
    if (after.local === 'stopped' && after.remote !== 'online' && after.remote !== 'unknown') break;
    await pause(1500);
  }
  if (after.local !== 'stopped' || after.remote === 'online' || after.remote === 'unknown') fail('RUNNER_STOP_UNCONFIRMED');
  return { status: 'PASS', stage: 'runner-stopped' };
}
async function runCommandCore(command) {
  try {
    if (!['doctor', 'deps', 'install', 'register', 'start', 'stop', 'status', 'setup'].includes(command)) fail('ARGUMENTS_INVALID');
    const result = command === 'doctor' ? await doctor() : command === 'deps' ? await deps()
      : command === 'install' ? (await doInstall(), { status: 'PASS', stage: 'runner-installed' })
        : command === 'register' ? (await register(await getState()), { status: 'PASS', stage: 'runner-registered' })
          : command === 'start' ? await start() : command === 'stop' ? await stop()
            : command === 'status' ? await statusCore()
              : (await authCheck(), await doInstall(), await register(await getState()), await start());
    return result;
  } catch (error) {
    const reason = /^[A-Z][A-Z0-9_]+$/.test(error?.reason || '') ? error.reason : 'RUNNER_OPERATION_FAILED';
    const blocked = ['GH_AUTH_REQUIRED', 'GH_ADMIN_REQUIRED', 'GH_REPO_ACCESS_UNKNOWN', 'REMOTE_RUNNER_LIST_INCOMPLETE', 'REMOTE_STATE_UNKNOWN', 'RUNNER_BUSY', 'RELEASE_RUNNER_LABEL_CONFLICT'].includes(reason);
    return { status: blocked ? 'BLOCKED' : 'FAIL', reason, ...(reason === 'GH_AUTH_REQUIRED' ? { guidance: 'Run gh auth login and retry.' } : {}) };
  }
}
export function createRunnerManager(options = {}) {
  const allowed = ['execute', 'home', 'download', 'platform', 'arch', 'user', 'hostname', 'sleep'];
  if (Object.keys(options).some(key => !allowed.includes(key)) || (options.execute !== undefined && typeof options.execute !== 'function'))
    throw new TypeError('runner manager dependencies invalid');
  return { run: command => context.run(options, () => runCommandCore(command)) };
}
export async function runRunnerCommand(command) { return createRunnerManager().run(command); }
