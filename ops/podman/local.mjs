#!/usr/bin/env node
// 本地验证编排：只消费既有构建与 Java 导入入口，不是产品运行时或正式发行器。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { runCommand, HEARTBEAT_MS } from './command.mjs';
import { doctor, runtime } from './doctor.mjs';
import { repairNetwork } from './network-repair.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const help = '用法: node ops/podman/local.mjs install|up|status|stop|logs|recover|doctor [--dir 绝对路径] [install: --api-port 18080 --db-port 15432]';
const args = process.argv.slice(2), action = args.shift();
let dir = path.join(repo, '.local/podman'), apiPort = 18080, dbPort = 15432;
let lockOwned = false, state, step = '检查参数', logFile, lockRecord, interrupted = false;
const markInterrupted = () => { interrupted = true; };
process.on('SIGINT', markInterrupted);
process.on('SIGTERM', markInterrupted);
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
const fail = (message) => { throw new Error(message); };
const environment = Object.fromEntries(['HOME', 'PATH', 'JAVA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSH_AUTH_SOCK', 'CONTAINER_HOST', 'CONTAINER_CONNECTION', 'DOCKER_HOST', 'PODMAN_COMPOSE_PROVIDER'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
function regular(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) fail(`需要普通文件: ${file}`);
}
function safePath(target) {
  if (!path.isAbsolute(target) || /[\r\n:$"\\]/.test(target)) fail('安装路径必须为绝对路径，不能含换行、冒号、$、双引号或反斜杠');
  let cursor = path.parse(target).root;
  for (const part of target.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if (fs.existsSync(cursor) && fs.lstatSync(cursor).isSymbolicLink()) fail(`拒绝符号链接路径: ${cursor}`);
  }
}
function commandLabel(command, argv) {
  if (command === './gradlew') return '编译 Java 应用';
  if (command === 'npm') return argv[0] === 'ci' ? '安装扩展依赖' : '构建 Chrome 扩展';
  if (command !== 'podman') return undefined;
  if (argv[0] === 'pull') return argv.at(-1).includes('temurin') ? '拉取 Java 25 基础镜像' : '拉取 PostgreSQL 17 基础镜像';
  if (argv[0] === 'build') return argv.at(-1) === 'build/api' ? '构建 API 镜像' : argv.at(-1) === 'build/postgres' ? '构建 PostgreSQL 镜像' : '构建词库工具镜像';
  if (argv[0] === 'run') return '下载、校验并精简 ECDICT';
  if (argv[0] === 'compose') {
    if (argv.includes('initialize')) return '导入词库到 PostgreSQL';
    if (argv.includes('up')) return argv.at(-1) === 'postgres' ? '启动 PostgreSQL' : '启动 API';
    if (argv.includes('stop')) return '停止服务';
  }
  return undefined;
}
function run(command, argv, { cwd = dir, timeout = 600000, capture = false, env = environment } = {}) {
  if (interrupted) fail('安装已取消；已保存当前状态，可重新执行 install（初始化状态不明除外）');
  const label = commandLabel(command, argv);
  return runCommand(command, argv, { cwd, timeout, capture, env, logFile, label: label || `执行 ${command} 检查`, announce: Boolean(label), onStart: pid => writeLock(pid), onClose: ({ groupGone }) => { if (groupGone) writeLock(null); } });
}
function save() {
  const next = path.join(dir, `.state-${crypto.randomBytes(16).toString('hex')}.next`);
  fs.writeFileSync(next, JSON.stringify(state, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  fs.renameSync(next, path.join(dir, 'state.json'));
}
function digestFiles() {
  const names = ['compose.yaml', 'release.env', 'secrets/postgres-password', 'secrets/app-password', 'infra/postgres/schema.sql'];
  if (state.dataset) names.push(path.relative(dir, path.join(state.dataset, 'stardict.csv')));
  return Object.fromEntries(names.map(name => {
    const file = path.join(dir, name); safePath(file); regular(file); return [name, sha(fs.readFileSync(file))];
  }));
}
function verifyConfig() {
  if (JSON.stringify(digestFiles()) !== JSON.stringify(state.digests)) fail('安装配置或密码发生变化，拒绝操作；不要手改受管目录');
}
function compose(...argv) {
  return run('podman', ['compose', '--env-file', 'release.env', '-p', state.project, '-f', 'compose.yaml', ...argv], { capture: true });
}
async function ownedResources() {
  // 扫描 Compose 项目标签，两种 provider 标签均核对；不依赖易漂移的引擎 ID。
  const services = new Set();
  for (const kind of ['container', 'volume', 'network']) {
    const listArgs = kind === 'container' ? ['ps', '-a', '--format', 'json'] : [kind, 'ls', '--format', 'json'];
    const rows = JSON.parse(await run('podman', listArgs, { capture: true, timeout: 30000 }));
    for (const row of rows) {
      const labels = row.Labels || row.labels || {};
      if ([labels['com.docker.compose.project'], labels['io.podman.compose.project']].includes(state.project) && labels['lexiflow.installation'] !== state.id) fail(`发现归属不匹配的 ${kind}，拒绝操作`);
      if (kind === 'container' && labels['lexiflow.installation'] === state.id && [labels['com.docker.compose.project'], labels['io.podman.compose.project']].includes(state.project)) services.add(labels['com.docker.compose.service'] || labels['io.podman.compose.service']);
    }
  }
  return services;
}
async function freePort(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', () => reject(new Error(`本机端口 ${port} 已被占用；首次安装用 --api-port/--db-port 指定空闲端口，不要停止未知服务`)));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}
async function ready() {
  const started = performance.now(), deadline = started + 120000;
  let lastNotice = started, reason = 'RUNTIME_UNAVAILABLE';
  console.log('[LexiFlow]   等待 API 正式就绪（最多 120 秒）…');
  while (performance.now() < deadline) {
    if (interrupted) fail('等待已取消，已初始化的数据保留，可用 up 再次启动');
    try {
      const [status, currentReason] = await runtime(state.apiPort, state.version, deadline - performance.now());
      reason = currentReason;
      if (status === 'PASS' && performance.now() <= deadline) {
        console.log(`[LexiFlow]   API 已正式就绪（${Math.floor((performance.now() - started) / 1000)} 秒）`);
        return;
      }
      if (status === 'FAIL') fail('API 响应协议或软件版本不匹配，请检查镜像与扩展版本');
    } catch (error) {
      if (error.message.startsWith('API 响应')) throw error;
      reason = error.message === 'HTTP_TIMEOUT' ? 'HTTP_TIMEOUT' : ['ECONNREFUSED', 'ECONNRESET'].includes(error.code) ? error.code : 'RUNTIME_UNAVAILABLE';
    }
    if (performance.now() - lastNotice >= HEARTBEAT_MS) {
      console.log(`[LexiFlow]   等待就绪（已用 ${Math.floor((performance.now() - started) / 1000)} 秒；原因 ${reason}）`);
      lastNotice = performance.now();
    }
    const delay = Math.min(2000, deadline - performance.now());
    if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  }
  console.log('[LexiFlow]   就绪等待超时，执行一次容器内部检查（最多 10 秒）…');
  let internalReady = false;
  try {
    const rows = JSON.parse(await run('podman', ['ps', '--format', 'json'], { capture: true, timeout: 5000 }));
    const owned = rows.filter(row => {
      const labels = row.Labels || {};
      return labels['lexiflow.installation'] === state.id && (labels['com.docker.compose.project'] || labels['io.podman.compose.project']) === state.project && (labels['com.docker.compose.service'] || labels['io.podman.compose.service']) === 'api';
    });
    const id = owned.length === 1 ? (owned[0].Id || owned[0].ID) : '';
    if (/^[a-f0-9]{64}$/.test(id)) {
      const body = JSON.parse(await run('podman', ['exec', id, '/app/entrypoint.sh', 'health'], { capture: true, timeout: 5000 }));
      internalReady = body.ready === true && body.mode === 'formal' && body.reason === 'OK' && body.softwareVersion === state.version && body.apiContract === 'caption-hints.v1';
    }
  } catch { /* 不输出工具原文，无法确认时不声称应用健康。 */ }
  fail(internalReady ? '容器内部已就绪，但宿主端口不可达（HOST_PORT_UNREACHABLE）；数据已保留。请运行 doctor；不要重新导入词库' : `API 就绪等待超时（${reason}）；内部健康未确认，请查看 logs；不要重新初始化数据库`);
}

function summary() {
  console.log(`就绪。API: http://127.0.0.1:${state.apiPort}\nChrome 加载目录: ${path.join(dir, 'extension')}\nPostgreSQL: 127.0.0.1:${state.dbPort} / lexiflow / 用户 lexiflow\n密码文件（勿分享）: ${path.join(dir, 'secrets/app-password')}`);
}
function copy(from, to) {
  regular(path.join(repo, from));
  fs.mkdirSync(path.dirname(path.join(dir, to)), { recursive: true, mode: 0o700 });
  fs.copyFileSync(path.join(repo, from), path.join(dir, to));
}
function checkpoint(phase) { if (interrupted) fail('安装已取消，保留当前阶段'); state.phase = phase; save(); }
function progress(text) { step = text; console.log(`[LexiFlow] ${text}`); }
function sourceFingerprint() {
  // 恢复阶段只允许同一源码输入；不读取 ignored 本机资料。
  const roots = ['backend', 'extension', 'ops/podman', 'ops/docker', 'ops/dataset', 'ops/release/version.txt', 'infra/postgres/schema.sql', 'scripts/environment/ecdict_bundle.py'];
  const inputs = [];
  function visit(relative) {
    if (['node_modules', 'build', '.gradle', 'dist', '__pycache__'].includes(path.basename(relative))) return;
    const target = path.join(repo, relative), stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) fail(`源码输入不能为符号链接: ${relative}`);
    if (stat.isDirectory()) for (const name of fs.readdirSync(target).sort()) visit(path.join(relative, name));
    else if (stat.isFile()) {
      if (/\.(csv|zip|7z|gz|jsonl|ndjson)$/.test(relative)) return;
      inputs.push([relative, sha(fs.readFileSync(target))]);
    }
  }
  roots.forEach(visit); return sha(JSON.stringify(inputs));
}
function writeLock(childPid) {
  if (!lockOwned) return;
  lockRecord.childPid = childPid;
  fs.writeFileSync(path.join(dir, '.lock/owner.json'), JSON.stringify(lockRecord), { mode: 0o600 });
}
function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}
function retireLock(confirmLegacy = false) {
  const root = path.join(dir, '.lock');
  if (!fs.existsSync(root)) return;
  safePath(root);
  const entries = fs.readdirSync(root);
  if (entries.length === 0) {
    if (!confirmLegacy) fail('发现旧安装遗留的空锁；确认旧安装已退出后运行 recover --confirm-stopped，再重试 install');
  } else {
    if (entries.length !== 1 || entries[0] !== 'owner.json') fail('安装锁内容未知，拒绝删除');
    regular(path.join(root, 'owner.json'));
    let owner;
    try { owner = JSON.parse(fs.readFileSync(path.join(root, 'owner.json'), 'utf8')); }
    catch { fail('安装锁元数据不完整，拒绝自动删除'); }
    if (owner.schema !== 1 || owner.root !== dir || owner.installation !== state.id || !Number.isSafeInteger(owner.pid) || owner.pid <= 1 || !/^[a-f0-9]{32}$/.test(owner.token) || (owner.childPid !== null && (!Number.isSafeInteger(owner.childPid) || owner.childPid <= 1))) fail('安装锁归属无效');
    if (alive(owner.pid) || (owner.childPid && alive(-owner.childPid))) fail('安装或其子进程仍在运行，不能重试或恢复；请先结束原命令');
    // 删除前再校验原记录，拒绝已发生的身份漂移。
    if (fs.readFileSync(path.join(root, 'owner.json'), 'utf8') !== JSON.stringify(owner)) fail('安装锁已改变，请稍后重试');
    fs.unlinkSync(path.join(root, 'owner.json'));
  }
  fs.rmdirSync(root);
}
function acquireLock(confirmLegacy = false) {
  // 回收和创建必须属于同一个短同步临界区，防止两个重试删除彼此的新锁。
  const claim = path.join(dir, '.lock-claim');
  try { fs.mkdirSync(claim, { mode: 0o700 }); }
  catch { fail('另一命令正在交接安装锁或上次交接被强制终止；保留现场，不删除未知交接锁'); }
  try {
    retireLock(confirmLegacy);
    fs.mkdirSync(path.join(dir, '.lock'), { mode: 0o700 });
    lockOwned = true;
    lockRecord = { schema: 1, root: dir, installation: state.id, pid: process.pid, token: crypto.randomBytes(16).toString('hex'), childPid: null };
    writeLock(null);
  } finally { fs.rmdirSync(claim); }
}
function releaseLock() {
  if (!lockOwned) return;
  if (lockRecord.childPid && alive(-lockRecord.childPid)) {
    console.error('[LexiFlow] 子进程组仍存在，安装锁已保留；不要同时启动下一次安装');
    return;
  }
  const ownerFile = path.join(dir, '.lock/owner.json');
  const owner = JSON.parse(fs.readFileSync(ownerFile, 'utf8'));
  if (owner.token !== lockRecord.token || owner.pid !== process.pid) return;
  fs.unlinkSync(ownerFile); fs.rmdirSync(path.join(dir, '.lock'));
}
async function main() {
  if (action === '--help' || action === 'help') { console.log(help); return; }
  if (!['install', 'up', 'status', 'stop', 'logs', 'recover', 'doctor'].includes(action)) fail(help);
  const seen = new Set();
  let confirmStopped = false, jsonReport = false;
  while (args.length) {
    const key = args.shift();
    if (action === 'doctor' && key === '--json' && !jsonReport) { jsonReport = true; continue; }
    if (action === 'recover' && key === '--confirm-stopped' && !confirmStopped) { confirmStopped = true; continue; }
    const value = args.shift();
    if (!value || seen.has(key)) fail(help); seen.add(key);
    if (key === '--dir') dir = value;
    else if (action === 'install' && ['--api-port', '--db-port'].includes(key) && /^[1-9][0-9]{0,4}$/.test(value) && +value <= 65535) {
      if (key === '--api-port') apiPort = +value; else dbPort = +value;
    } else fail(help);
  }
  if (action === 'doctor') { await doctor(dir, environment, jsonReport); return; }
  safePath(dir); dir = path.resolve(dir);
  if (dir === repo || dir === os.homedir() || dir === '/') fail('不能使用源码根目录、HOME 或文件系统根作为安装目录');
  if (apiPort === dbPort) fail('API 与数据库端口不能相同');
  if (Number(process.versions.node.split('.')[0]) < 22) fail('需要 Node.js 22 或以上');
  if (os.platform() !== 'darwin' || os.arch() !== 'arm64') fail('本入口仅用于 M 芯片 macOS');
  console.log('[LexiFlow] 检查安装目录与 Podman 环境…');
  const existed = fs.existsSync(dir);
  if (existed) {
    regular(path.join(dir, 'state.json'));
    state = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));
    if (state.schema !== 1 || state.root !== dir || !/^[a-f0-9]{32}$/.test(state.id) || state.project !== `lexiflow-local-${state.id}` || !['new', 'built', 'source', 'initializing', 'initialized', 'ready'].includes(state.phase)) fail('安装身份或状态不合法，拒绝接管');
    if (action === 'install' && ((seen.has('--api-port') && apiPort !== state.apiPort) || (seen.has('--db-port') && dbPort !== state.dbPort))) fail('已有安装不能变更端口，请另选 --dir');
  } else {
    if (action !== 'install') fail('尚未安装，请先运行 install');
    await run('podman', ['info'], { cwd: repo, timeout: 30000 });
    await run('podman', ['compose', 'version'], { cwd: repo, timeout: 30000 });
    await freePort(apiPort); await freePort(dbPort);
    fs.mkdirSync(path.dirname(dir), { recursive: true, mode: 0o700 });
    fs.mkdirSync(dir, { mode: 0o700 });
    const id = crypto.randomBytes(16).toString('hex');
    state = { schema: 1, id, project: `lexiflow-local-${id}`, root: dir, apiPort, dbPort, phase: 'new', version: fs.readFileSync(path.join(repo, 'ops/release/version.txt'), 'utf8').trim(), source: sourceFingerprint() };
    save();
  }
  if (action === 'recover') {
    if (!confirmStopped) fail('恢复旧锁需明确确认原安装已经退出：recover --confirm-stopped');
    if (!['new', 'built'].includes(state.phase)) fail('只允许恢复建库前的安装；已进入数据库阶段时保留现场，不自动清库或重导');
    acquireLock(true);
    // 旧脚本升级后的建库前安装完整重建；保留密码、词库和已有镜像，不接管数据库。
    state.source = sourceFingerprint(); state.phase = 'new'; save();
    console.log('[LexiFlow] 建库前安装锁已恢复，数据和密码未删除；请重新执行 install');
    return;
  }
  acquireLock();
  logFile = path.join(dir, `operation-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.log`);
  fs.writeFileSync(logFile, '', { mode: 0o600, flag: 'wx' });
  console.log(`[LexiFlow] 详细日志（实时写入）: ${logFile}`);
  await run('podman', ['info'], { timeout: 30000 });
  await ownedResources();
  if (state.digests) {
    const repaired = repairNetwork({ dir, state, template: fs.readFileSync(path.join(repo, 'ops/podman/compose.validation.yaml'), 'utf8'), actualDigests: digestFiles, save, enabled: ['install', 'up'].includes(action) });
    if (repaired) console.log('[LexiFlow] 已自动修复端口发布网络；保留原数据库、词库和密码，正在重建服务连接。');
    verifyConfig();
  }
  if (action === 'install' && !['initialized', 'ready'].includes(state.phase)) {
    if (state.phase === 'initializing') fail('上次数据库初始化未确认完成，已保留现场；禁止自动重导或清库，请查看日志');
    if (state.source !== sourceFingerprint()) fail('未完成安装的源码已变化，请保留现场并使用新的 --dir');
    const java = spawnSync('java', ['-version'], { env: environment, encoding: 'utf8', timeout: 10000 });
    if (java.status !== 0 || !/version "25[.\"]/.test(java.stderr)) fail('请安装并选择 Java 25 JDK（设置 JAVA_HOME 和 PATH）');
    if (state.phase === 'new') {
      progress('1/6 构建 Java 应用与 Chrome 扩展');
      await run('./gradlew', ['--no-daemon', ':api:bootJar'], { cwd: path.join(repo, 'backend') });
      await run('npm', ['ci'], { cwd: path.join(repo, 'extension') });
      await run('npm', ['run', 'build'], { cwd: path.join(repo, 'extension'), env: { ...environment, LEXIFLOW_API_PORT: String(state.apiPort) } });
      for (const [from, to] of [
        [`backend/product/api/build/libs/api-${state.version}.jar`, 'build/api/lexiflow-api.jar'], ['ops/docker/Dockerfile', 'build/api/Dockerfile'], ['ops/docker/entrypoint.sh', 'build/api/entrypoint.sh'], ['ops/docker/Dockerfile.postgres', 'build/postgres/Dockerfile'], ['ops/docker/bootstrap.sh', 'build/postgres/bootstrap.sh'], ['infra/postgres/schema.sql', 'infra/postgres/schema.sql'], ['ops/dataset/ecdict-source.lock.json', 'ops/dataset/ecdict-source.lock.json'], ['scripts/environment/ecdict_bundle.py', 'scripts/environment/ecdict_bundle.py'], ['ops/podman/fetch-ecdict.sh', 'ops/podman/fetch-ecdict.sh'], ['ops/podman/source-tools.Containerfile', 'ops/podman/source-tools.Containerfile'],
      ]) copy(from, to);
      fs.cpSync(path.join(repo, 'extension/dist'), path.join(dir, 'extension'), { recursive: true });
      let template = fs.readFileSync(path.join(repo, 'ops/podman/compose.validation.yaml'), 'utf8');
      template = template.replace('127.0.0.1:18080:8080', `127.0.0.1:${state.apiPort}:8080`).replace('127.0.0.1:15432:5432', `127.0.0.1:${state.dbPort}:5432`);
      fs.writeFileSync(path.join(dir, 'compose.yaml'), template);
      progress('2/6 构建 ARM64 容器镜像');
      for (const [name, base, arg] of [['api', 'docker.io/library/eclipse-temurin:25-jre', 'JAVA_RUNTIME_IMAGE'], ['postgres', 'docker.io/library/postgres:17-bookworm', 'POSTGRES_RUNTIME_IMAGE']]) {
        await run('podman', ['pull', '--platform', 'linux/arm64', base]);
        const baseId = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', base], { capture: true });
        const tag = `localhost/lexiflow-${name}:${state.id}`;
        await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `${arg}=${baseId}`, '-t', tag, `build/${name}`]);
        state[`${name}Image`] = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', tag], { capture: true });
        if (!/^(sha256:)?[a-f0-9]{64}$/.test(state[`${name}Image`])) fail('镜像 ID 格式错误');
      }
      fs.mkdirSync(path.join(dir, 'secrets'), { recursive: true, mode: 0o700 });
      for (const name of ['postgres-password', 'app-password']) {
        const file = path.join(dir, 'secrets', name);
        if (!fs.existsSync(file)) fs.writeFileSync(file, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o444, flag: 'wx' });
        regular(file);
      }
      checkpoint('built');
    }
    if (state.phase === 'built') {
      progress('3/6 下载、校验并精简公开 ECDICT 词库');
      fs.mkdirSync(path.join(dir, 'data'), { recursive: true, mode: 0o700 });
      const tag = `localhost/lexiflow-source-tools:${state.id}`;
      await run('podman', ['build', '--platform', 'linux/arm64', '-t', tag, '-f', 'ops/podman/source-tools.Containerfile', 'ops/podman']);
      const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--memory', '2g', '-v', `${dir}/ops:/kit/ops:ro`, '-v', `${dir}/scripts:/kit/scripts:ro`, '-v', `${dir}/data:/data`, tag, '/kit/ops/podman/fetch-ecdict.sh', '/kit', '/data'], { capture: true, timeout: 1800000 });
      const matches = [...output.matchAll(/^dataset_dir=\/data\/(ecdict-source\.[A-Za-z0-9]+)$/gm)];
      if (matches.length !== 1) fail('未取得唯一且安全的词库目录');
      state.dataset = path.join(dir, 'data', matches[0][1]);
      regular(path.join(state.dataset, 'stardict.csv'));
      const envText = `LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE=${state.apiImage}\nLEXIFLOW_POSTGRES_IMAGE=${state.postgresImage}\nLEXIFLOW_INSTALLATION_ID=${state.id}\nLEXIFLOW_RELEASE_KEY=local-${state.id}\nLEXIFLOW_SOURCE_DIR="${state.dataset}"\n`;
      fs.writeFileSync(path.join(dir, 'release.env'), envText, { mode: 0o600 });
      state.digests = digestFiles(); checkpoint('source');
    }
    if (state.phase === 'source') {
      await freePort(state.apiPort);
      // 已归属 PG 可能在 checkpoint 前启动过；不要把自己的端口当作外部冲突。
      if (!(await ownedResources()).has('postgres')) await freePort(state.dbPort);
      verifyConfig();
      progress('4/6 启动 PostgreSQL'); await compose('config'); await compose('up', '-d', 'postgres');
      // compose initialize 的 service_healthy 依赖负责等待 PG，而不是错误即重导。
      progress('5/6 首次导入词库（不要中断）'); checkpoint('initializing');
      await compose('run', '--rm', 'initialize'); checkpoint('initialized');
    }
  }
  if (!state.digests) fail('安装未完成，请运行 install 重试建库前步骤');
  verifyConfig(); await ownedResources();
  if (action === 'stop') { progress('停止服务，保留数据库'); await compose('stop'); return; }
  if (action === 'logs') { console.log(await compose('logs', '--tail=100', 'postgres', 'api')); return; }
  if (action === 'status') { console.log(await compose('ps')); await ready(); summary(); return; }
  if (!['initialized', 'ready'].includes(state.phase)) fail('数据库尚未确认初始化完成；请查看安装日志');
  progress('6/6 启动应用并验证就绪');
  await compose('up', '-d', 'postgres'); await compose('up', '-d', '--no-deps', 'api');
  await ready(); checkpoint('ready'); summary();
}
main().catch(error => { console.error(`[LexiFlow] ${step}失败：${error.message}`); process.exitCode = 1; }).finally(() => {
  process.removeListener('SIGINT', markInterrupted); process.removeListener('SIGTERM', markInterrupted);
  releaseLock();
});
