#!/usr/bin/env node
// 本地验证编排：只消费既有构建与 Java 导入入口，不是产品运行时或正式发行器。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const help = '用法: node ops/podman/local.mjs install|up|status|stop|logs [--dir 绝对路径] [install: --api-port 18080 --db-port 15432]';
const args = process.argv.slice(2), action = args.shift();
let dir = path.join(repo, '.local/podman'), apiPort = 18080, dbPort = 15432;
let lockOwned = false, state, step = '检查参数', logFile;
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
function run(command, argv, { cwd = dir, timeout = 600000, capture = false, env = environment } = {}) {
  const result = spawnSync(command, argv, { cwd, env, encoding: 'utf8', timeout, maxBuffer: 24 * 1024 * 1024 });
  if (logFile) fs.appendFileSync(logFile, `\n[${step}] ${command}\n${result.stdout || ''}${result.stderr || ''}\nexit=${result.status}\n`, { mode: 0o600 });
  if (result.error || result.status !== 0) fail(`${command} 执行失败或超时${logFile ? `；详情见 ${logFile}` : '；检查工具安装与运行状态'}`);
  return capture ? (result.stdout || '').trim() : undefined;
}
function save() {
  const next = path.join(dir, 'state.json.next');
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
function ownedResources() {
  // 扫描 Compose 项目标签，两种 provider 标签均核对；不依赖易漂移的引擎 ID。
  const services = new Set();
  for (const kind of ['container', 'volume', 'network']) {
    const listArgs = kind === 'container' ? ['ps', '-a', '--format', 'json'] : [kind, 'ls', '--format', 'json'];
    const rows = JSON.parse(run('podman', listArgs, { capture: true, timeout: 30000 }));
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
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const health = await fetch(`http://127.0.0.1:${state.apiPort}/actuator/health/readiness`, { signal: AbortSignal.timeout(2000) });
      const status = await fetch(`http://127.0.0.1:${state.apiPort}/api/v1/runtime-status`, { signal: AbortSignal.timeout(2000) });
      const body = await status.json();
      if (health.ok && status.ok && body.mode === 'formal' && body.ready === true && body.reason === 'OK' && body.softwareVersion === state.version) return body;
    } catch { /* 有界等待容器就绪，不输出响应内容。 */ }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  fail('应用未达到 formal/ready/OK；查看 logs，不重复初始化数据库');
}
function summary() {
  console.log(`就绪。API: http://127.0.0.1:${state.apiPort}\nChrome 加载目录: ${path.join(dir, 'extension')}\nPostgreSQL: 127.0.0.1:${state.dbPort} / lexiflow / 用户 lexiflow\n密码文件（勿分享）: ${path.join(dir, 'secrets/app-password')}`);
}
function copy(from, to) {
  regular(path.join(repo, from));
  fs.mkdirSync(path.dirname(path.join(dir, to)), { recursive: true, mode: 0o700 });
  fs.copyFileSync(path.join(repo, from), path.join(dir, to));
}
function checkpoint(phase) { state.phase = phase; save(); }
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
async function main() {
  if (action === '--help' || action === 'help') { console.log(help); return; }
  if (!['install', 'up', 'status', 'stop', 'logs'].includes(action)) fail(help);
  const seen = new Set();
  while (args.length) {
    const key = args.shift(), value = args.shift();
    if (!value || seen.has(key)) fail(help); seen.add(key);
    if (key === '--dir') dir = value;
    else if (action === 'install' && ['--api-port', '--db-port'].includes(key) && /^[1-9][0-9]{0,4}$/.test(value) && +value <= 65535) {
      if (key === '--api-port') apiPort = +value; else dbPort = +value;
    } else fail(help);
  }
  safePath(dir); dir = path.resolve(dir);
  if (dir === repo || dir === os.homedir() || dir === '/') fail('不能使用源码根目录、HOME 或文件系统根作为安装目录');
  if (apiPort === dbPort) fail('API 与数据库端口不能相同');
  if (Number(process.versions.node.split('.')[0]) < 22) fail('需要 Node.js 22 或以上');
  if (os.platform() !== 'darwin' || os.arch() !== 'arm64') fail('本入口仅用于 M 芯片 macOS');
  const existed = fs.existsSync(dir);
  if (existed) {
    regular(path.join(dir, 'state.json'));
    state = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));
    if (state.schema !== 1 || state.root !== dir || !/^[a-f0-9]{32}$/.test(state.id) || state.project !== `lexiflow-local-${state.id}` || !['new', 'built', 'source', 'initializing', 'initialized', 'ready'].includes(state.phase)) fail('安装身份或状态不合法，拒绝接管');
    if (action === 'install' && ((seen.has('--api-port') && apiPort !== state.apiPort) || (seen.has('--db-port') && dbPort !== state.dbPort))) fail('已有安装不能变更端口，请另选 --dir');
  } else {
    if (action !== 'install') fail('尚未安装，请先运行 install');
    run('podman', ['info'], { cwd: repo, timeout: 30000 });
    run('podman', ['compose', 'version'], { cwd: repo, timeout: 30000 });
    await freePort(apiPort); await freePort(dbPort);
    fs.mkdirSync(path.dirname(dir), { recursive: true, mode: 0o700 });
    fs.mkdirSync(dir, { mode: 0o700 });
    const id = crypto.randomBytes(16).toString('hex');
    state = { schema: 1, id, project: `lexiflow-local-${id}`, root: dir, apiPort, dbPort, phase: 'new', version: fs.readFileSync(path.join(repo, 'ops/release/version.txt'), 'utf8').trim(), source: sourceFingerprint() };
    save();
  }
  try { fs.mkdirSync(path.join(dir, '.lock'), { mode: 0o700 }); lockOwned = true; }
  catch { fail('安装正在被其他命令操作或上次被强制中断；不要删除未知锁，先核对运行进程'); }
  logFile = path.join(dir, `operation-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.log`);
  fs.writeFileSync(logFile, '', { mode: 0o600, flag: 'wx' });
  run('podman', ['info'], { timeout: 30000 });
  if (state.digests) verifyConfig();
  ownedResources();
  if (action === 'install' && !['initialized', 'ready'].includes(state.phase)) {
    if (state.phase === 'initializing') fail('上次数据库初始化未确认完成，已保留现场；禁止自动重导或清库，请查看日志');
    if (state.source !== sourceFingerprint()) fail('未完成安装的源码已变化，请保留现场并使用新的 --dir');
    const java = spawnSync('java', ['-version'], { env: environment, encoding: 'utf8', timeout: 10000 });
    if (java.status !== 0 || !/version "25[.\"]/.test(java.stderr)) fail('请安装并选择 Java 25 JDK（设置 JAVA_HOME 和 PATH）');
    if (state.phase === 'new') {
      progress('1/6 构建 Java 应用与 Chrome 扩展');
      run('./gradlew', ['--no-daemon', ':api:bootJar'], { cwd: path.join(repo, 'backend') });
      run('npm', ['ci'], { cwd: path.join(repo, 'extension') });
      run('npm', ['run', 'build'], { cwd: path.join(repo, 'extension'), env: { ...environment, LEXIFLOW_API_PORT: String(state.apiPort) } });
      for (const [from, to] of [
        [`backend/product/api/build/libs/api-${state.version}.jar`, 'build/api/lexiflow-api.jar'], ['ops/docker/Dockerfile', 'build/api/Dockerfile'], ['ops/docker/entrypoint.sh', 'build/api/entrypoint.sh'], ['ops/docker/Dockerfile.postgres', 'build/postgres/Dockerfile'], ['ops/docker/bootstrap.sh', 'build/postgres/bootstrap.sh'], ['infra/postgres/schema.sql', 'infra/postgres/schema.sql'], ['ops/dataset/ecdict-source.lock.json', 'ops/dataset/ecdict-source.lock.json'], ['scripts/environment/ecdict_bundle.py', 'scripts/environment/ecdict_bundle.py'], ['ops/podman/fetch-ecdict.sh', 'ops/podman/fetch-ecdict.sh'], ['ops/podman/source-tools.Containerfile', 'ops/podman/source-tools.Containerfile'],
      ]) copy(from, to);
      fs.cpSync(path.join(repo, 'extension/dist'), path.join(dir, 'extension'), { recursive: true });
      let template = fs.readFileSync(path.join(repo, 'ops/podman/compose.validation.yaml'), 'utf8');
      template = template.replace('127.0.0.1:18080:8080', `127.0.0.1:${state.apiPort}:8080`).replace('127.0.0.1:15432:5432', `127.0.0.1:${state.dbPort}:5432`);
      fs.writeFileSync(path.join(dir, 'compose.yaml'), template);
      progress('2/6 构建 ARM64 容器镜像');
      for (const [name, base, arg] of [['api', 'docker.io/library/eclipse-temurin:25-jre', 'JAVA_RUNTIME_IMAGE'], ['postgres', 'docker.io/library/postgres:17-bookworm', 'POSTGRES_RUNTIME_IMAGE']]) {
        run('podman', ['pull', '--platform', 'linux/arm64', base]);
        const baseId = run('podman', ['image', 'inspect', '--format', '{{.Id}}', base], { capture: true });
        const tag = `localhost/lexiflow-${name}:${state.id}`;
        run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `${arg}=${baseId}`, '-t', tag, `build/${name}`]);
        state[`${name}Image`] = run('podman', ['image', 'inspect', '--format', '{{.Id}}', tag], { capture: true });
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
      run('podman', ['build', '--platform', 'linux/arm64', '-t', tag, '-f', 'ops/podman/source-tools.Containerfile', 'ops/podman']);
      const output = run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--memory', '2g', '-v', `${dir}/ops:/kit/ops:ro`, '-v', `${dir}/scripts:/kit/scripts:ro`, '-v', `${dir}/data:/data`, tag, '/kit/ops/podman/fetch-ecdict.sh', '/kit', '/data'], { capture: true, timeout: 1800000 });
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
      if (!ownedResources().has('postgres')) await freePort(state.dbPort);
      verifyConfig();
      progress('4/6 启动 PostgreSQL'); compose('config'); compose('up', '-d', 'postgres');
      // compose initialize 的 service_healthy 依赖负责等待 PG，而不是错误即重导。
      progress('5/6 首次导入词库（不要中断）'); checkpoint('initializing');
      compose('run', '--rm', 'initialize'); checkpoint('initialized');
    }
  }
  if (!state.digests) fail('安装未完成，请运行 install 重试建库前步骤');
  verifyConfig(); ownedResources();
  if (action === 'stop') { progress('停止服务，保留数据库'); compose('stop'); return; }
  if (action === 'logs') { console.log(compose('logs', '--tail=100', 'postgres', 'api')); return; }
  if (action === 'status') { console.log(compose('ps')); await ready(); summary(); return; }
  if (!['initialized', 'ready'].includes(state.phase)) fail('数据库尚未确认初始化完成；请查看安装日志');
  progress('6/6 启动应用并验证就绪');
  compose('up', '-d', 'postgres'); compose('up', '-d', '--no-deps', 'api');
  await ready(); checkpoint('ready'); summary();
}
main().catch(error => { console.error(`[LexiFlow] ${step}失败：${error.message}`); process.exitCode = 1; }).finally(() => { if (lockOwned) fs.rmdirSync(path.join(dir, '.lock')); });
