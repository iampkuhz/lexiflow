// Darwin arm64 的真实候选运行验收；stdout 只包含有界事实，不包含工具输出。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { resolveBuildIdentity, assertBuildIdentityMatches } from '../release/version.mjs';
import { verifyReleaseCandidate } from '../release/verified-candidate.mjs';
import { verifyExtension, treeDigest } from './upgrade.mjs';
import { acquireProcessLock } from './process-lock.mjs';
import { runCandidateSession, startCandidateSession } from './candidate-session.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
let socketFence = null;
let runtimeBusy = false;
export const REQUIRED_PHASE_IDS = Object.freeze([
  'preflight', 'target-install', 'target-repeat', 'target-noop', 'target-cleanup',
  'previous-install', 'previous-baseline', 'upgrade-failure', 'automatic-recovery',
  'upgrade-retry', 'retry-noop', 'preservation', 'final-cleanup',
]);
const hex = /^[a-f0-9]{64}$/;
const digest = b => crypto.createHash('sha256').update(b).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function failureCode(error) { return /^[A-Z][A-Z0-9_]{2,80}$/.test(error?.message || '') ? error.message : 'RUNTIME_INTERNAL_ERROR'; }
function fail(code, blocked = false) { const error = new Error(code); error.blocked = blocked; throw error; }
function exact(value, names) { return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join('\0') === [...names].sort().join('\0'); }
function validate(request) {
  if (!exact(request, ['previous', 'target'])) fail('REQUEST_INVALID');
  for (const item of [request.previous, request.target]) if (!exact(item, ['candidateDirectory', 'candidateSha256'])
    || typeof item.candidateDirectory !== 'string' || !path.isAbsolute(item.candidateDirectory)
    || typeof item.candidateSha256 !== 'string' || !hex.test(item.candidateSha256)) fail('REQUEST_INVALID');
  if (request.previous.candidateSha256 === request.target.candidateSha256) fail('CANDIDATES_MUST_DIFFER');
}
async function free(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', () => reject(Object.assign(new Error(port === 18080 ? 'API_PORT_BLOCKED' : 'DB_PORT_BLOCKED'), { blocked: true })));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}
async function dbPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function privateRoot() {
  const parent = path.join(repo, 'tmp/quality/candidate-runtime');
  fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
  const root = fs.mkdtempSync(path.join(parent, `${crypto.randomBytes(16).toString('hex')}-`));
  fs.chmodSync(root, 0o700);
  return root;
}
function report() { return { schemaVersion: 'lexiflow.candidate-runtime.v1', status: 'FAIL',
  candidates: { previous: null, target: null }, identity: { source: null },
  phases: REQUIRED_PHASE_IDS.map(id => ({ id, status: 'NOT_RUN', facts: {} })), assertions: {},
  cleanup: { status: 'NOT_RUN', settled: false }, failure: null }; }
function phase(out, id, status, facts = {}) { const item = out.phases.find(p => p.id === id); item.status = status; item.facts = facts; }
function command(root, name, argv, { allowFailure = false, timeout = 30_000 } = {}) {
  // 原始inspect、SQL和命令输出只进忽略目录的私有日志。
  if (socketFence && name !== 'machine-inspect') {
    let current;
    try { current = fs.statSync(socketFence.path); } catch { fail('PODMAN_ENDPOINT_CHANGED'); }
    if (!current.isSocket() || current.dev !== socketFence.dev || current.ino !== socketFence.ino) fail('PODMAN_ENDPOINT_CHANGED');
  }
  const result = spawnSync('podman', argv, { encoding: 'utf8', timeout, maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
  fs.appendFileSync(path.join(root, 'podman.log'), `${name}\n${result.stdout || ''}${result.stderr || ''}\n`, { mode: 0o600 });
  if (result.error || result.signal || (!allowFailure && result.status !== 0)) fail('PODMAN_COMMAND_FAILED', !!result.error && result.error.code === 'ENOENT');
  if (socketFence && name !== 'machine-inspect') {
    let current;
    try { current = fs.statSync(socketFence.path); } catch { fail('PODMAN_ENDPOINT_CHANGED'); }
    if (!current.isSocket() || current.dev !== socketFence.dev || current.ino !== socketFence.ino) fail('PODMAN_ENDPOINT_CHANGED');
  }
  return result;
}
function pinMachine(root) {
  const machines = jsonCommand(root, 'machine-inspect', ['machine', 'inspect']);
  const rows = Array.isArray(machines) ? machines : [machines];
  const running = rows.filter(item => String(item?.State || item?.state || '').toLowerCase() === 'running');
  if (running.length !== 1) fail('PODMAN_MACHINE_UNAVAILABLE', true);
  const socket = running[0]?.ConnectionInfo?.PodmanSocket?.Path || running[0]?.connectionInfo?.podmanSocket?.path;
  if (typeof socket !== 'string' || !path.isAbsolute(socket)) fail('PODMAN_MACHINE_UNAVAILABLE', true);
  let info;
  try { info = fs.statSync(socket); } catch { fail('PODMAN_MACHINE_UNAVAILABLE', true); }
  if (!info.isSocket()) fail('PODMAN_MACHINE_UNAVAILABLE', true);
  socketFence = { path: socket, dev: info.dev, ino: info.ino };
  const address = `unix://${socket}`;
  process.env.CONTAINER_HOST = address;
  process.env.DOCKER_HOST = address;
  delete process.env.CONTAINER_CONNECTION;
}
function jsonCommand(root, name, argv) {
  let value;
  try { value = JSON.parse(command(root, name, argv).stdout); } catch { fail('PODMAN_INSPECT_INVALID'); }
  return value;
}
function state(dir) {
  let value;
  try { value = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8')); } catch { fail('INSTALL_STATE_INVALID'); }
  if (value.schema !== 1 || value.root !== dir || !/^[a-f0-9]{32}$/.test(value.id)
    || value.project !== `lexiflow-local-${value.id}` || value.apiPort !== 18080
    || !Number.isInteger(value.dbPort) || value.dbPort < 1024) fail('INSTALL_STATE_INVALID');
  return value;
}
function fileDigest(file, maxBytes = 2_000_000_000) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > maxBytes) fail('PRIVATE_FILE_INVALID');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  const hash = crypto.createHash('sha256'); const block = Buffer.alloc(1024 * 1024);
  const same = (a, b) => ['dev', 'ino', 'size', 'mode', 'mtimeMs', 'ctimeMs'].every(k => a[k] === b[k]);
  try {
    const opened = fs.fstatSync(fd);
    if (!same(info, opened)) fail('PRIVATE_FILE_CHANGED');
    let count = 0, n;
    while ((n = fs.readSync(fd, block, 0, block.length, null)) > 0) {
      count += n; if (count > opened.size || count > maxBytes) fail('PRIVATE_FILE_CHANGED');
      hash.update(block.subarray(0, n));
    }
    if (count !== opened.size || !same(opened, fs.fstatSync(fd)) || !same(opened, fs.lstatSync(file))) fail('PRIVATE_FILE_CHANGED');
  } finally { fs.closeSync(fd); }
  return hash.digest('hex');
}
function labelValue(labels, docker, podman, expected) {
  if (labels[docker] !== undefined && labels[docker] !== expected) fail('RESOURCE_OWNER_MISMATCH');
  if (labels[podman] !== undefined && labels[podman] !== expected) fail('RESOURCE_OWNER_MISMATCH');
  if (labels[docker] === undefined && labels[podman] === undefined) fail('RESOURCE_OWNER_MISMATCH');
}
export function assertResourceOwnership(info, s, kind, service) {
  const labels = info?.Config?.Labels || info?.Labels || info?.labels || {};
  labelValue(labels, 'com.docker.compose.project', 'io.podman.compose.project', s.project);
  if (labels['lexiflow.installation'] !== s.id) fail('RESOURCE_OWNER_MISMATCH');
  if (kind === 'container') labelValue(labels, 'com.docker.compose.service', 'io.podman.compose.service', service);
  return true;
}
export function assertTargetApiContainer(info, s, expectedImage) {
  assertResourceOwnership(info, s, 'container', 'api');
  if ((info.Image || '').replace(/^sha256:/, '') !== expectedImage.replace(/^sha256:/, '')) fail('API_IMAGE_MISMATCH');
  if (info.State?.Running !== true) fail('TARGET_API_NOT_RUNNING');
  return true;
}
/** Mutation gate shared by the real observer and deterministic refusal tests. */
export function stopVerifiedTargetApi({ state: s, first, reinspect, stop, inspectStopped, expectedImage }) {
  assertTargetApiContainer(first, s, expectedImage);
  const second = reinspect();
  if (second.Id !== first.Id) fail('RESOURCE_ID_CHANGED');
  assertTargetApiContainer(second, s, expectedImage);
  stop();
  const stopped = inspectStopped();
  if (stopped.Id !== first.Id) fail('RESOURCE_ID_CHANGED');
  assertResourceOwnership(stopped, s, 'container', 'api');
  if ((stopped.Image || '').replace(/^sha256:/, '') !== expectedImage.replace(/^sha256:/, '')
    || stopped.State?.Running !== false) fail('TARGET_API_STOP_NOT_OBSERVED');
  return true;
}
export function eligibleForPass(output) {
  return output.phases.length === REQUIRED_PHASE_IDS.length
    && REQUIRED_PHASE_IDS.every(id => output.phases.find(p => p.id === id)?.status === 'PASS')
    && output.cleanup.status === 'PASS' && output.cleanup.settled === true;
}
function listed(root, s, kind) {
  const argv = kind === 'container' ? ['ps', '-a', '--format', 'json'] : [kind, 'ls', '--format', 'json'];
  const rows = jsonCommand(root, `list-${kind}`, argv);
  if (!Array.isArray(rows)) fail('RESOURCE_LIST_INVALID');
  const output = [];
  for (const row of rows) {
    const labels = row.Labels || row.labels || {};
    if (labels['lexiflow.installation'] === s.id
      && labels['com.docker.compose.project'] !== s.project && labels['io.podman.compose.project'] !== s.project) fail('RESOURCE_OWNER_MISMATCH');
    if (labels['com.docker.compose.project'] !== s.project && labels['io.podman.compose.project'] !== s.project) continue;
    // 列表本身也不得隐藏同名不同owner或双provider冲突。
    labelValue(labels, 'com.docker.compose.project', 'io.podman.compose.project', s.project);
    if (labels['lexiflow.installation'] !== s.id) fail('RESOURCE_OWNER_MISMATCH');
    const id = kind === 'container' ? row.Id || row.ID || row.id : kind === 'volume' ? row.Name || row.name : row.Name || row.name || row.ID || row.Id;
    if (typeof id !== 'string' || !id) fail('RESOURCE_LIST_INVALID');
    if (kind === 'container' ? !hex.test(id) : !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(id)) fail('RESOURCE_IDENTIFIER_INVALID');
    output.push({ id, labels, service: labels['com.docker.compose.service'] || labels['io.podman.compose.service'] });
  }
  return output;
}
function inspect(root, s, kind, item, service) {
  const values = jsonCommand(root, `inspect-${kind}`, kind === 'container' ? ['inspect', item.id] : [kind, 'inspect', item.id]);
  if (Array.isArray(values) && values.length !== 1) fail('RESOURCE_INSPECT_INVALID');
  const info = Array.isArray(values) ? values[0] : values;
  if (!info) fail('RESOURCE_INSPECT_INVALID');
  const id = kind === 'container' ? info.Id || info.ID : info.Name || info.name;
  if (id !== item.id) fail('RESOURCE_ID_CHANGED');
  assertResourceOwnership(info, s, kind, service);
  return info;
}
function imageId(value) {
  if (typeof value !== 'string' || !/^(sha256:)?[a-f0-9]{64}$/.test(value)) fail('IMAGE_ID_INVALID');
  return value.replace(/^sha256:/, '');
}
function apiImage(verified) { return verified.manifest.artifacts.find(a => a.role === 'api-image' && a.platform === 'linux/arm64')?.imageDigest; }
function postgresImage(verified) { return verified.manifest.artifacts.find(a => a.role === 'postgres-image' && a.platform === 'linux/arm64')?.imageDigest; }
function verifyApi(root, s, verified) {
  const rows = listed(root, s, 'container').filter(r => r.service === 'api');
  if (rows.length !== 1) fail('API_CONTAINER_NOT_UNIQUE');
  const info = inspect(root, s, 'container', rows[0], 'api');
  if (info.State?.Running !== true) fail('API_CONTAINER_NOT_RUNNING');
  if ((info.Image || '').replace(/^sha256:/, '') !== apiImage(verified)?.replace(/^sha256:/, '')) fail('API_IMAGE_MISMATCH');
  const image = jsonCommand(root, 'image-inspect', ['image', 'inspect', apiImage(verified)]);
  if (!Array.isArray(image) || image.length !== 1) fail('API_IMAGE_IDENTITY_MISMATCH');
  const entry = Array.isArray(image) ? image[0] : null;
  const labels = entry?.Config?.Labels || entry?.Labels || {};
  const identity = verified.buildIdentity;
  if ((entry?.Id || entry?.ID || '').replace(/^sha256:/, '') !== apiImage(verified)?.replace(/^sha256:/, '')
    || entry?.Os !== 'linux' || entry?.Architecture !== 'arm64'
    || labels['org.opencontainers.image.version'] !== identity.softwareVersion
    || labels['org.opencontainers.image.revision'] !== identity.sourceCommit
    || labels['io.lexiflow.build-id'] !== identity.buildId
    || labels['io.lexiflow.source-sha256'] !== identity.sourceSha256) fail('API_IMAGE_IDENTITY_MISMATCH');
  return rows[0].id;
}
function recordFiles(dir) {
  const root = path.join(dir, 'installation-records');
  const files = fs.readdirSync(root).sort();
  if (files.length > 16 || files.some(f => !/^[a-f0-9]{32}\.json$/.test(f))) fail('INSTALL_RECORD_INVALID');
  return files.map(f => {
    const info = fs.lstatSync(path.join(root, f));
    if (!info.isFile() || info.isSymbolicLink() || info.size > 65536) fail('INSTALL_RECORD_INVALID');
    const record = JSON.parse(fs.readFileSync(path.join(root, f), 'utf8'));
    if (`${record.operation}.json` !== f) fail('INSTALL_RECORD_INVALID');
    return record;
  });
}
function verifyReady(root, dir, verified, { count, kind } = {}) {
  const s = state(dir);
  try { assertBuildIdentityMatches(s.buildIdentity, verified.buildIdentity); }
  catch { fail('INSTALL_STATE_MISMATCH'); }
  if (s.phase !== 'ready' || s.version !== verified.buildIdentity.softwareVersion
    || s.packageType !== 'release-package' || imageId(s.apiImage) !== imageId(apiImage(verified))
    || s.apiPort !== 18080 || (s.artifacts?.apiImage && imageId(s.artifacts.apiImage) !== imageId(apiImage(verified)))
    || (s.artifacts?.image && imageId(s.artifacts.image) !== imageId(apiImage(verified)))) fail('INSTALL_STATE_MISMATCH');
  if (kind === 'install' && imageId(s.postgresImage) !== imageId(postgresImage(verified))) fail('PG_IMAGE_MISMATCH');
  if (s.artifacts?.candidateSha256 !== verified.candidateSha256
    || s.artifacts?.manifestSha256 !== verified.manifestSha256) fail('INSTALL_CANDIDATE_BINDING_MISMATCH');
  verifyExtension(path.join(dir, 'extension'), verified.buildIdentity, 18080);
  verifyApi(root, s, verified);
  const records = recordFiles(dir);
  if (count !== undefined && records.length !== count) fail('INSTALL_RECORD_COUNT_MISMATCH');
  if (kind && (!s.lastOperation || !records.some(r => r.operation === s.lastOperation.operation
    && r.kind === kind && r.status === 'PASS' && r.buildId === verified.buildIdentity.buildId
    && r.sourceCommit === verified.buildIdentity.sourceCommit && r.targetVersion === verified.buildIdentity.softwareVersion
    && r.artifacts?.candidateSha256 === verified.candidateSha256 && r.artifacts?.manifestSha256 === verified.manifestSha256
    && JSON.stringify(r) === JSON.stringify(s.lastOperation)))) fail('INSTALL_RECORD_MISMATCH');
  return { s, records, extension: treeDigest(path.join(dir, 'extension')) };
}
function snapshot(root, dir, verified) {
  const current = verifyReady(root, dir, verified);
  const s = current.s;
  const pg = listed(root, s, 'container').filter(x => x.service === 'postgres');
  const volumes = listed(root, s, 'volume');
  if (pg.length !== 1 || volumes.length !== 2) fail('PG_RESOURCE_MISSING');
  const pgInfo = inspect(root, s, 'container', pg[0], 'postgres');
  if (pgInfo.State?.Running !== true || (pgInfo.Image || '').replace(/^sha256:/, '') !== s.postgresImage?.replace(/^sha256:/, '')) fail('PG_IMAGE_MISMATCH');
  const volumeFacts = volumes.map(volume => { const v = inspect(root, s, 'volume', volume); return [volume.id, v.CreatedAt || v.createdAt]; }).sort((a, b) => a[0].localeCompare(b[0]));
  if (volumeFacts.some(v => !v[1])) fail('PG_VOLUME_IDENTITY_MISSING');
  const mounts = pgInfo.Mounts || [];
  const pgMounts = mounts.filter(m => m.Type === 'volume' && m.Destination === '/var/lib/postgresql/data');
  if (pgMounts.length !== 1 || !volumes.some(v => v.id === pgMounts[0].Name)) fail('PG_VOLUME_MOUNT_MISMATCH');
  const passwordHashes = ['secrets/app-password', 'secrets/postgres-password'].map(f => fileDigest(path.join(dir, f), 4096));
  const datasetFile = path.join(s.dataset, 'dataset.zip');
  const datasetHash = fileDigest(datasetFile);
  if (datasetHash !== s.datasetPackageSha256) fail('DATASET_DRIFT');
  return { pg: pg[0].id, pgImage: pgInfo.Image, volumes: volumeFacts, pgMount: pgMounts[0].Name,
    marker: (inspect(root, s, 'container', pg[0], 'postgres'), marker(root, pg[0].id, false)), passwordHashes, datasetHash,
    datasetIdentity: s.datasetIdentity, dbPort: s.dbPort, apiPort: s.apiPort,
    extension: current.extension, records: current.records.length };
}
function marker(root, pg, create) {
  const sql = create
    ? "CREATE TABLE IF NOT EXISTS public.lexiflow_candidate_runtime_marker (id integer PRIMARY KEY, value text NOT NULL); INSERT INTO public.lexiflow_candidate_runtime_marker(id,value) VALUES (1,'lexiflow-candidate-runtime-v1') ON CONFLICT (id) DO NOTHING; SELECT value FROM public.lexiflow_candidate_runtime_marker WHERE id=1;"
    : 'SELECT value FROM public.lexiflow_candidate_runtime_marker WHERE id=1;';
  const result = command(root, 'synthetic-marker', ['exec', pg, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'lexiflow', '-Atqc', sql]);
  if (!result.stdout.trim().endsWith('lexiflow-candidate-runtime-v1')) fail('PG_MARKER_MISMATCH');
  return digest(result.stdout.trim().split('\n').at(-1));
}
function samePreserved(before, after) {
  return ['pg', 'pgImage', 'volumes', 'pgMount', 'marker', 'passwordHashes', 'datasetHash', 'datasetIdentity', 'dbPort', 'apiPort']
    .every(key => JSON.stringify(before[key]) === JSON.stringify(after[key]));
}
function clean(root, dir) {
  if (path.dirname(dir) !== root || fs.lstatSync(root).isSymbolicLink() || fs.lstatSync(dir).isSymbolicLink()) fail('CLEANUP_ROOT_INVALID');
  // runLocal和清理互斥；有owner目录、未知lock或活跃子进程时保留现场。
  const unlock = acquireProcessLock(dir);
  try {
  if (fs.readdirSync(dir).some(name => name === '.lock' || name === '.lock-claim')) fail('CLEANUP_LOCK_UNKNOWN');
  const s = state(dir);
  let removed = 0;
  // 不运行 compose down/prune；每项 mutation 前均重新 inspect ID、两种 provider 标签和 owner。
  for (const kind of ['container', 'network', 'volume']) {
    const rows = listed(root, s, kind);
    if (rows.length > (kind === 'container' ? 4 : kind === 'network' ? 1 : 2)) fail('RESOURCE_SET_UNEXPECTED');
    for (const item of rows) {
      if (kind === 'container' && !['api', 'postgres', 'initialize'].includes(item.service)) fail('RESOURCE_SERVICE_UNEXPECTED');
      inspect(root, s, kind, item, kind === 'container' ? item.service : undefined);
      command(root, `remove-${kind}`, kind === 'container' ? ['rm', '-f', item.id] : [kind, 'rm', item.id]);
      removed++;
    }
  }
  if (['container', 'network', 'volume'].some(k => listed(root, s, k).length)) fail('RESOURCE_CLEANUP_INCOMPLETE');
  return removed;
  } finally { unlock(); }
}
async function inject(root, dir, verified, session) {
  // 仅观察本project/id/service 的 target image；再inspect、再stop一次。错归属绝不stop。
  for (let i = 0; i < 240 && !session.isClosed(); i++) {
    const s = state(dir);
    const rows = listed(root, s, 'container').filter(x => x.service === 'api');
    if (rows.length > 1) fail('API_CONTAINER_NOT_UNIQUE');
    if (rows.length === 1) {
      const info = inspect(root, s, 'container', rows[0], 'api');
      if ((info.Image || '').replace(/^sha256:/, '') === apiImage(verified)?.replace(/^sha256:/, '')) {
        // 二次inspection与post-stop observation均在同一受限gate内。
        return stopVerifiedTargetApi({ state: s, first: info, expectedImage: apiImage(verified),
          reinspect: () => inspect(root, s, 'container', rows[0], 'api'),
          stop: () => command(root, 'fault-stop-target-api', ['stop', '--time=0', rows[0].id]),
          inspectStopped: () => inspect(root, s, 'container', rows[0], 'api') });
      }
    }
    await sleep(250);
  }
  return false;
}
async function runCandidateRuntimeInner(request) {
  const out = report(); let root, activeDir, settled = true;
  const deadline = Date.now() + 1_800_000;
  const execute = async input => {
    settled = false;
    const result = await runCandidateSession(input);
    if (!result.settled) fail('SESSION_NOT_SETTLED');
    settled = true;
    return result;
  };
  try {
    validate(request);
    if (process.platform !== 'darwin' || process.arch !== 'arm64' || os.platform() !== 'darwin' || os.arch() !== 'arm64') fail('HOST_UNSUPPORTED', true);
    phase(out, 'preflight', 'RUNNING');
    let source;
    try { source = resolveBuildIdentity(repo); } catch { fail('SOURCE_UNAVAILABLE', true); }
    if (source.dirty) fail('SOURCE_DIRTY', true);
    const previous = await verifyReleaseCandidate(request.previous);
    const target = await verifyReleaseCandidate(request.target);
    try { assertBuildIdentityMatches(target.buildIdentity, source); } catch { fail('TARGET_SOURCE_IDENTITY_MISMATCH'); }
    if (previous.buildIdentity.buildId === target.buildIdentity.buildId || apiImage(previous) === apiImage(target)) fail('CANDIDATES_NOT_DISTINCT');
    if (previous.manifest.artifacts.find(a => a.role === 'sql')?.sha256
      !== target.manifest.artifacts.find(a => a.role === 'sql')?.sha256) fail('SQL_INCOMPATIBLE');
    await free(18080);
    const port = await dbPort(); await free(port);
    out.candidates.previous = { candidateSha256: previous.candidateSha256, manifestSha256: previous.manifestSha256, buildIdentity: previous.buildIdentity };
    out.candidates.target = { candidateSha256: target.candidateSha256, manifestSha256: target.manifestSha256, buildIdentity: target.buildIdentity };
    out.identity.source = source;
    root = privateRoot();
    pinMachine(root);
    phase(out, 'preflight', 'PASS', { sourceClean: true, hostDarwinArm64: true, candidateBytesVerified: true, sqlCompatible: true, apiPortFree: true });
    const targetDir = path.join(root, 'target-install'), previousDir = path.join(root, 'previous-install');
    const session = (name, action, directory, candidate) => {
      const timeoutMs = Math.min(900_000, deadline - Date.now() - 180_000);
      if (timeoutMs < 1000) fail('RUNTIME_DEADLINE_EXCEEDED');
      return { action, directory, candidate, dbPort: port, logFile: path.join(root, `${name}.log`), timeoutMs };
    };
    let r;
    activeDir = targetDir; phase(out, 'target-install', 'RUNNING');
    r = await execute(session('01-target-install', 'install', targetDir, request.target));
    if (r.code !== 0 || r.signal) fail('TARGET_INSTALL_FAILED');
    const targetFirst = verifyReady(root, targetDir, target, { count: 1, kind: 'install' });
    phase(out, 'target-install', 'PASS', { ready: true, apiIdentity: true, extensionIdentity: true, installRecord: true });
    phase(out, 'target-repeat', 'RUNNING');
    r = await execute(session('02-target-repeat', 'install', targetDir, request.target));
    if (r.code !== 0 || r.signal) fail('TARGET_REPEAT_FAILED');
    const targetRepeat = verifyReady(root, targetDir, target, { count: 1, kind: 'install' });
    if (JSON.stringify(targetFirst.records) !== JSON.stringify(targetRepeat.records) || targetFirst.extension !== targetRepeat.extension) fail('TARGET_REPEAT_CHANGED');
    phase(out, 'target-repeat', 'PASS', { noExtraRecord: true, sameExtension: true });
    phase(out, 'target-noop', 'RUNNING');
    r = await execute(session('03-target-noop', 'upgrade', targetDir, request.target));
    if (r.code !== 0 || r.signal) fail('TARGET_NOOP_FAILED');
    const targetNoop = verifyReady(root, targetDir, target, { count: 1, kind: 'install' });
    if (JSON.stringify(targetRepeat.records) !== JSON.stringify(targetNoop.records) || targetRepeat.extension !== targetNoop.extension) fail('TARGET_NOOP_CHANGED');
    phase(out, 'target-noop', 'PASS', { noExtraRecord: true, sameExtension: true });
    phase(out, 'target-cleanup', 'RUNNING');
    clean(root, targetDir); phase(out, 'target-cleanup', 'PASS', { ownedResourcesRemoved: true }); activeDir = null;
    await free(18080); await free(port);
    activeDir = previousDir; phase(out, 'previous-install', 'RUNNING');
    r = await execute(session('04-previous-install', 'install', previousDir, request.previous));
    if (r.code !== 0 || r.signal) fail('PREVIOUS_INSTALL_FAILED');
    verifyReady(root, previousDir, previous, { count: 1, kind: 'install' });
    phase(out, 'previous-install', 'PASS', { ready: true, apiIdentity: true, extensionIdentity: true, installRecord: true });
    phase(out, 'previous-baseline', 'RUNNING');
    const pg = listed(root, state(previousDir), 'container').filter(x => x.service === 'postgres');
    if (pg.length !== 1) fail('PG_RESOURCE_MISSING');
    inspect(root, state(previousDir), 'container', pg[0], 'postgres'); marker(root, pg[0].id, true);
    const baseline = snapshot(root, previousDir, previous);
    phase(out, 'previous-baseline', 'PASS', { pgOwned: true, volumeOwned: true, markerPresent: true, secretsAndDatasetHashed: true });
    phase(out, 'upgrade-failure', 'RUNNING');
    settled = false;
    const running = startCandidateSession(session('05-upgrade-failure', 'upgrade', previousDir, request.target));
    let observed = false, observerError;
    try { observed = await inject(root, previousDir, target, running); } catch (error) { observerError = error; }
    try { r = await running.wait(); } catch (error) { settled = false; throw error; }
    if (!r.settled) fail('SESSION_NOT_SETTLED');
    settled = true;
    if (observerError) throw observerError;
    if (!observed || r.code === 0 || r.signal) fail('FAULT_NOT_PROVEN');
    phase(out, 'upgrade-failure', 'PASS', { targetApiStoppedOnce: true, upgradeNonzero: true });
    phase(out, 'automatic-recovery', 'RUNNING');
    verifyReady(root, previousDir, previous, { count: 2 });
    const recovered = snapshot(root, previousDir, previous);
    if (!samePreserved(baseline, recovered) || recovered.extension !== baseline.extension) fail('RECOVERY_PRESERVATION_FAILED');
    const failureRecords = recordFiles(previousDir).filter(v => v.kind === 'upgrade' && v.status === 'FAIL' && v.buildId === target.buildIdentity.buildId);
    if (failureRecords.length !== 1) fail('RECOVERY_RECORD_MISSING');
    phase(out, 'automatic-recovery', 'PASS', { previousApiRestored: true, previousExtensionRestored: true, pgAndMarkerPreserved: true, failureRecord: true });
    phase(out, 'upgrade-retry', 'RUNNING');
    r = await execute(session('06-upgrade-retry', 'upgrade', previousDir, request.target));
    if (r.code !== 0 || r.signal) fail('UPGRADE_RETRY_FAILED');
    const upgraded = verifyReady(root, previousDir, target, { count: 3, kind: 'upgrade' });
    phase(out, 'upgrade-retry', 'PASS', { ready: true, targetApiIdentity: true, targetExtensionIdentity: true, successRecord: true });
    phase(out, 'retry-noop', 'RUNNING');
    r = await execute(session('07-retry-noop', 'upgrade', previousDir, request.target));
    if (r.code !== 0 || r.signal) fail('RETRY_NOOP_FAILED');
    const noop = verifyReady(root, previousDir, target, { count: 3, kind: 'upgrade' });
    if (JSON.stringify(upgraded.records) !== JSON.stringify(noop.records) || upgraded.extension !== noop.extension) fail('RETRY_NOOP_CHANGED');
    phase(out, 'retry-noop', 'PASS', { noExtraRecord: true, sameExtension: true });
    phase(out, 'preservation', 'RUNNING');
    const after = snapshot(root, previousDir, target);
    if (!samePreserved(baseline, after)) fail('UPGRADE_PRESERVATION_FAILED');
    phase(out, 'preservation', 'PASS', { pgContainerSame: true, volumesSame: true, markerSame: true, secretsSame: true, datasetSame: true, portsSame: true });
    phase(out, 'final-cleanup', 'RUNNING');
    clean(root, previousDir); activeDir = null;
    phase(out, 'final-cleanup', 'PASS', { ownedResourcesRemoved: true });
    const [finalPrevious, finalTarget] = await Promise.all([
      verifyReleaseCandidate(request.previous), verifyReleaseCandidate(request.target),
    ]);
    if (finalPrevious.manifestSha256 !== previous.manifestSha256 || finalTarget.manifestSha256 !== target.manifestSha256) fail('CANDIDATE_CHANGED');
    const finalSource = resolveBuildIdentity(repo);
    assertBuildIdentityMatches(finalSource, source);
    if (finalSource.dirty) fail('SOURCE_CHANGED');
    out.assertions = { sourceAndCandidatesUnchanged: true, realFaultAndRecovery: true, samePgAndDataset: true };
    out.cleanup = { status: 'PASS', settled: true, ownedResourcesRemoved: true, privateLogsRetained: true };
    if (!eligibleForPass(out)) fail('PHASE_INCOMPLETE');
    out.status = 'PASS'; return out;
  } catch (error) {
    let code = failureCode(error);
    out.status = error.blocked ? 'BLOCKED' : 'FAIL';
    // 失败路径也复核冻结输入；漂移不能被原有运行错误遮蔽。
    if (out.candidates.previous && out.candidates.target) {
      try {
        const [p, t] = await Promise.all([verifyReleaseCandidate(request.previous), verifyReleaseCandidate(request.target)]);
        if (p.manifestSha256 !== out.candidates.previous.manifestSha256 || t.manifestSha256 !== out.candidates.target.manifestSha256) fail('CANDIDATE_CHANGED');
        assertBuildIdentityMatches(p.buildIdentity, out.candidates.previous.buildIdentity);
        assertBuildIdentityMatches(t.buildIdentity, out.candidates.target.buildIdentity);
        assertBuildIdentityMatches(resolveBuildIdentity(repo), out.identity.source);
      } catch { code = 'SOURCE_OR_CANDIDATE_CHANGED'; out.status = 'FAIL'; }
    }
    out.failure = { code };
    const current = out.phases.find(p => p.status === 'RUNNING');
    if (current) phase(out, current.id, out.status);
    if (root && activeDir && settled) {
      try { clean(root, activeDir); out.cleanup = { status: 'PASS', settled: true, ownedResourcesRemoved: true, privateLogsRetained: true }; }
      catch { out.cleanup = { status: 'FAIL', settled: true, ownedResourcesRemoved: false, privateLogsRetained: true }; }
    } else out.cleanup = { status: root && activeDir ? 'FAIL' : 'NOT_REQUIRED', settled,
      ownedResourcesRemoved: !activeDir, privateLogsRetained: !!root };
    return out;
  }
}
export async function runCandidateRuntime(request) {
  if (runtimeBusy) { const output = report(); output.failure = { code: 'RUNTIME_BUSY' }; return output; }
  runtimeBusy = true;
  const prior = Object.fromEntries(['CONTAINER_HOST', 'DOCKER_HOST', 'CONTAINER_CONNECTION'].map(key => [key, process.env[key]]));
  socketFence = null;
  try { return await runCandidateRuntimeInner(request); }
  finally {
    for (const [key, value] of Object.entries(prior)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    socketFence = null;
    runtimeBusy = false;
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const chunks = []; let size = 0;
    for await (const chunk of process.stdin) { size += chunk.length; if (size > 64 * 1024) throw new Error('REQUEST_TOO_LARGE'); chunks.push(chunk); }
    const result = await runCandidateRuntime(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) { process.stderr.write(`${failureCode(error)}\n`); process.exitCode = 2; }
}
