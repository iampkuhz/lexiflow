// 可分享的只读诊断：只输出固定原因与受限版本字段，不转发工具/API原文。
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';

const PHASES = new Set(['new', 'built', 'source', 'initializing', 'initialized', 'ready']);
const actions = {
  SUPPORTED: '环境满足此项要求', MISSING_TOOL: '安装所需工具后重试', UNSUPPORTED_VERSION: '选择 Java 25 和 Node.js 22 或以上',
  UNSUPPORTED_HOST: '在 M 芯片 macOS 上运行', ENGINE_UNAVAILABLE: '检查 Podman machine 是否已启动', COMPOSE_UNAVAILABLE: '安装或检查 Podman Compose provider',
  NOT_INSTALLED: '运行 install 首次部署', INVALID_STATE: '保留安装现场，不手动修改状态文件', INSTALL_INCOMPLETE: '建库前失败可重试 install',
  INITIALIZATION_UNCERTAIN: '保留数据库与日志，不自动重导', NO_LOCK: '没有安装锁', ACTIVE_LOCK: '等待原安装结束，不同时重试',
  STALE_LOCK: '原进程已消失，可重试 install', LEGACY_LOCK: '确认原安装退出后 recover --confirm-stopped', INVALID_LOCK: '保留未知锁，勿手工删除',
  NO_CONTAINERS: '安装完成后可运行 up；首次安装运行 install', CONTAINERS_STOPPED: '运行 up 启动已有安装', CONTAINER_OWNERSHIP: '容器归属异常，保留现场',
  CONTAINERS_RUNNING: '应用与数据库容器均在运行', RUNTIME_UNAVAILABLE: '检查服务日志和安装阶段', RUNTIME_NOT_READY: '查看安装阶段及服务日志',
  RUNTIME_INVALID: '运行状态不符合协议，检查镜像与扩展版本', RUNTIME_READY: '服务正式就绪；仍需人工验证 Chrome 和视频体验',
};
function probe(command, argv, env, includeStderr = false) {
  const result = spawnSync(command, argv, { env, encoding: 'utf8', timeout: 5000, maxBuffer: 1024 * 1024 });
  return result.error || result.status !== 0 ? null : `${result.stdout || ''}${includeStderr ? result.stderr || '' : ''}`;
}
function jsonFile(file, maxBytes = 32768) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > maxBytes) throw new Error('invalid');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function noSymlink(target) {
  let current = path.parse(target).root;
  for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    try { if (fs.lstatSync(current).isSymbolicLink()) return false; }
    catch (error) { if (error.code !== 'ENOENT') return false; }
  }
  return true;
}
function live(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== 'ESRCH'; }
}
async function readStatus(port, endpoint, timeoutMs) {
  // 原生 http 固定直连 loopback；不消费环境代理或全局 fetch dispatcher。
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port, path: endpoint, agent: false }, response => {
      if (response.statusCode >= 300 && response.statusCode < 400) { reject(new Error('HTTP_REDIRECT')); response.destroy(); return; }
      if (response.statusCode !== 200) { resolve({ available: false }); response.destroy(); return; }
      let bytes = 0;
      const parts = [];
      response.on('data', part => {
        bytes += part.length;
        if (bytes > 8192) { resolve({ invalid: true }); response.destroy(); }
        else parts.push(part);
      });
      response.on('end', () => {
        try { resolve({ available: true, body: JSON.parse(Buffer.concat(parts).toString('utf8')) }); }
        catch { resolve({ invalid: true }); }
      });
      response.on('error', reject);
      response.on('aborted', () => reject(new Error('HTTP_RESPONSE_ABORTED')));
    });
    const timer = setTimeout(() => request.destroy(new Error('HTTP_TIMEOUT')), Math.max(1, timeoutMs));
    request.on('error', reject);
    request.on('close', () => clearTimeout(timer));
  });
}
export async function runtime(port, expectedVersion, timeoutMs = 6000) {
  const deadline = performance.now() + timeoutMs;
  const remaining = () => Math.max(1, Math.min(3000, deadline - performance.now()));
  const health = await readStatus(port, '/actuator/health/readiness', remaining());
  if (health.invalid) return ['FAIL', 'RUNTIME_INVALID'];
  if (!health.available) return ['BLOCKED', 'RUNTIME_NOT_READY'];
  if (!health.body || typeof health.body.status !== 'string') return ['FAIL', 'RUNTIME_INVALID'];
  if (health.body.status !== 'UP') return ['BLOCKED', 'RUNTIME_NOT_READY'];
  const status = await readStatus(port, '/api/v1/runtime-status', remaining());
  if (status.invalid) return ['FAIL', 'RUNTIME_INVALID'];
  if (!status.available) return ['BLOCKED', 'RUNTIME_UNAVAILABLE'];
  const body = status.body;
  if (typeof body !== 'object' || body === null || body.softwareVersion !== expectedVersion || body.apiContract !== 'caption-hints.v1') return ['FAIL', 'RUNTIME_INVALID'];
  if (body.mode !== 'formal' || body.ready !== true || body.reason !== 'OK') return ['BLOCKED', 'RUNTIME_NOT_READY'];
  return ['PASS', 'RUNTIME_READY'];
}
export async function diagnose(dir, env) {
  const checks = [];
  const add = (id, status, reason, details = {}) => checks.push({ id, status, reason, next: actions[reason], ...details });
  add('host', os.platform() === 'darwin' && os.arch() === 'arm64' ? 'PASS' : 'BLOCKED', os.platform() === 'darwin' && os.arch() === 'arm64' ? 'SUPPORTED' : 'UNSUPPORTED_HOST');
  const node = process.versions.node;
  add('node', +node.split('.')[0] >= 22 ? 'PASS' : 'BLOCKED', +node.split('.')[0] >= 22 ? 'SUPPORTED' : 'UNSUPPORTED_VERSION', { version: node });
  const java = probe('java', ['-version'], env, true), version = java?.match(/version "([0-9]+(?:\.[0-9]+){0,3})[^"\r\n]*"/)?.[1];
  add('java', version?.split('.')[0] === '25' ? 'PASS' : 'BLOCKED', java === null ? 'MISSING_TOOL' : version?.split('.')[0] === '25' ? 'SUPPORTED' : 'UNSUPPORTED_VERSION', version ? { version } : {});
  const engine = probe('podman', ['info', '--format', 'json'], env);
  let engineOK = false;
  try { const info = JSON.parse(engine); engineOK = ['arm64', 'aarch64'].includes(info.host?.arch) && info.host?.os === 'linux'; } catch { /* 仅固定枚举，不输出工具原文。 */ }
  add('podman', engineOK ? 'PASS' : 'BLOCKED', engineOK ? 'SUPPORTED' : 'ENGINE_UNAVAILABLE');
  const compose = probe('podman', ['compose', 'version'], env);
  add('compose', compose === null ? 'BLOCKED' : 'PASS', compose === null ? 'COMPOSE_UNAVAILABLE' : 'SUPPORTED');
  let state;
  if (!path.isAbsolute(dir) || !noSymlink(dir)) { add('installation', 'FAIL', 'INVALID_STATE'); return report(checks); }
  dir = path.resolve(dir);
  if (!fs.existsSync(dir)) { add('installation', 'BLOCKED', 'NOT_INSTALLED'); return report(checks); }
  try {
    state = jsonFile(path.join(dir, 'state.json'));
    if (state.schema !== 1 || state.root !== dir || !/^[a-f0-9]{32}$/.test(state.id) || state.project !== `lexiflow-local-${state.id}` || !PHASES.has(state.phase) || !Number.isInteger(state.apiPort) || state.apiPort < 1 || state.apiPort > 65535 || !/^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}$/.test(state.version)) throw new Error('invalid');
  } catch { add('installation', 'FAIL', 'INVALID_STATE'); return report(checks); }
  const installed = ['initialized', 'ready'].includes(state.phase);
  add('installation', installed ? 'PASS' : 'BLOCKED', installed ? 'SUPPORTED' : state.phase === 'initializing' ? 'INITIALIZATION_UNCERTAIN' : 'INSTALL_INCOMPLETE', { phase: state.phase });
  const lock = path.join(dir, '.lock');
  try {
    if (fs.existsSync(path.join(dir, '.lock-claim'))) add('lock', 'BLOCKED', 'ACTIVE_LOCK');
    else if (!fs.existsSync(lock)) add('lock', 'PASS', 'NO_LOCK');
    else if (!noSymlink(lock)) add('lock', 'FAIL', 'INVALID_LOCK');
    else {
      const files = fs.readdirSync(lock);
      if (files.length === 0) add('lock', 'BLOCKED', 'LEGACY_LOCK');
      else {
        const owner = jsonFile(path.join(lock, 'owner.json'));
        if (files.length !== 1 || owner.schema !== 1 || !/^[a-f0-9]{32}$/.test(owner.token) || owner.root !== dir || owner.installation !== state.id || !Number.isSafeInteger(owner.pid) || owner.pid <= 1 || (owner.childPid !== null && (!Number.isSafeInteger(owner.childPid) || owner.childPid <= 1))) throw new Error('invalid');
        add('lock', 'BLOCKED', live(owner.pid) || (owner.childPid && live(-owner.childPid)) ? 'ACTIVE_LOCK' : 'STALE_LOCK');
      }
    }
  } catch { add('lock', 'FAIL', 'INVALID_LOCK'); }
  let containersOK = false;
  if (engineOK) {
    try {
      const rows = JSON.parse(probe('podman', ['ps', '-a', '--filter', `label=com.docker.compose.project=${state.project}`, '--format', 'json'], env));
      if (!Array.isArray(rows)) throw new Error('invalid');
      if (rows.some(row => row.Labels?.['lexiflow.installation'] !== state.id || row.Labels?.['com.docker.compose.project'] !== state.project)) add('containers', 'FAIL', 'CONTAINER_OWNERSHIP');
      else {
        containersOK = ['postgres', 'api'].every(service => rows.some(row => row.Labels?.['com.docker.compose.service'] === service && row.State === 'running'));
        add('containers', containersOK ? 'PASS' : 'BLOCKED', containersOK ? 'CONTAINERS_RUNNING' : rows.length ? 'CONTAINERS_STOPPED' : 'NO_CONTAINERS');
      }
    } catch { add('containers', 'BLOCKED', 'ENGINE_UNAVAILABLE'); }
  }
  if (containersOK) {
    try { const [status, reason] = await runtime(state.apiPort, state.version); add('runtime', status, reason); }
    catch { add('runtime', 'BLOCKED', 'RUNTIME_UNAVAILABLE'); }
  } else add('runtime', 'BLOCKED', 'RUNTIME_UNAVAILABLE');
  return report(checks);
}
function report(checks) {
  return { schema: 'lexiflow.local-doctor.v1', status: checks.some(c => c.status === 'FAIL') ? 'FAIL' : checks.some(c => c.status === 'BLOCKED') ? 'BLOCKED' : 'PASS', checks };
}
export async function doctor(dir, env, json) {
  const result = await diagnose(dir, env);
  if (json) console.log(JSON.stringify(result, null, 2));
  else {
    for (const check of result.checks) console.log(`[${check.status}] ${check.id}: ${check.next}`);
    console.log(`自检: ${result.status}；仅诊断，不代表完整验收。可加 --json 生成可分享摘要。`);
  }
  if (result.status !== 'PASS') process.exitCode = result.status === 'FAIL' ? 1 : 3;
}
