#!/usr/bin/env node
// 本地验证编排：只消费既有构建与 Java 导入入口，不是产品运行时或正式发行器。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { javaBuildEnvironment } from './java-runtime.mjs';
import { fileURLToPath } from 'node:url';
import { runCommand, HEARTBEAT_MS } from './command.mjs';
import { acquireProcessLock } from './process-lock.mjs';
import { doctor, runtime, runtimeDataset } from './doctor.mjs';
import { resolveBuildIdentity } from '../release/version.mjs';
import { upgradeInstallation, recoverUpgrade, verifyExtension, boundedApiLogging, atomicJson, installationRecord, publishRecord } from './upgrade.mjs';
import { githubLatestRelease, versionSummary } from './versions.mjs';
import { repairNetwork } from './network-repair.mjs';
import { writePreparedTree, preparationLimits, readBoundedFile } from './prepare-workspace.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const help = '用法: node ops/podman/local.mjs install|upgrade|version|up|status|stop|logs|recover|doctor [--dir 绝对路径] [install: --api-port 18080 --db-port 15432]';
let sessionBusy = false;
export async function runLocal(argv, candidateRequest) {
  if (!Array.isArray(argv) || !argv.every(value => typeof value === 'string')) {
    console.error('[LexiFlow] argv 必须是字符串数组');
    return 1;
  }
  if (sessionBusy) { console.error('[LexiFlow] 本模块已有本机安装会话正在运行，拒绝并发调用'); return 1; }
  const args = [...argv], action = args.shift();
  sessionBusy = true;
  let dir = path.join(repo, '.local/podman'), apiPort = 18080, dbPort = 15432;
  let captionDebug = false, restoring = false;
  let unlockProcess, lockOwned = false, state, step = '检查参数', logFile, lockRecord, interrupted = false;
  let environment = Object.fromEntries(['HOME', 'PATH', 'JAVA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSH_AUTH_SOCK', 'CONTAINER_HOST', 'CONTAINER_CONNECTION', 'DOCKER_HOST', 'PODMAN_COMPOSE_PROVIDER'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
  const markInterrupted = () => { interrupted = true; };
  process.on('SIGINT', markInterrupted);
  process.on('SIGTERM', markInterrupted);
  const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
  let candidate, candidateAdapter;
  const fail = (message) => { throw new Error(message); };
  function regular(file) {
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink()) fail(`需要普通文件: ${file}`);
  }
  function writeCandidateFile(file, content) {
    const bytes = Buffer.isBuffer(content) ? content : Buffer.from(content);
    if (fs.existsSync(file)) {
      const info = fs.lstatSync(file);
      if (!info.isFile() || info.isSymbolicLink() || info.size !== bytes.length || info.size > 1024 * 1024
          || !fs.readFileSync(file).equals(bytes)) fail('CANDIDATE_TARGET_DRIFT');
      return;
    }
    const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW ?? 0), 0o600);
    try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
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
    if (argv[0] === 'build') return path.basename(argv.at(-1)) === 'api' ? '构建 API 镜像' : path.basename(argv.at(-1)) === 'postgres' ? '构建 PostgreSQL 镜像' : '构建词库工具镜像';
    if (argv[0] === 'run') return '下载、校验并精简 ECDICT';
    if (argv[0] === 'compose') {
      if (argv.includes('initialize')) return '导入词库到 PostgreSQL';
      if (argv.includes('up')) return argv.at(-1) === 'postgres' ? '启动 PostgreSQL' : '启动 API';
      if (argv.includes('stop')) return '停止服务';
    }
    return undefined;
  }
  function run(command, argv, { cwd = dir, timeout = 600000, capture = false, env = environment, logOutput = true } = {}) {
    if (interrupted && !restoring) fail('安装已取消；已保存当前状态，可重新执行 install（初始化状态不明除外）');
    const label = commandLabel(command, argv);
    return runCommand(command, argv, { cwd, timeout, capture, env, logFile: logOutput ? logFile : undefined, label: label || `执行 ${command} 检查`, announce: Boolean(label), onStart: pid => writeLock(pid), onClose: ({ groupGone }) => { if (groupGone) writeLock(null); } });
  }
  function save() {
    const next = path.join(dir, `.state-${crypto.randomBytes(16).toString('hex')}.next`);
    fs.writeFileSync(next, JSON.stringify(state, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
    fs.renameSync(next, path.join(dir, 'state.json'));
  }
  function digestFiles(installed = state) {
    if (installed.dataset && !installed.dataset.startsWith(path.join(dir, 'data') + path.sep)) fail('资料目录不属于此安装');
    const names = ['compose.yaml', 'release.env', 'secrets/postgres-password', 'secrets/app-password', 'infra/postgres/schema.sql'];
    if (installed.captionDebug) names.push('caption-debug.yaml');
    if (installed.dataset) names.push(path.relative(dir, path.join(installed.dataset, installed.packageType === 'release-package' ? 'dataset.zip' : 'stardict.csv')));
    return Object.fromEntries(names.map(name => {
      const file = path.join(dir, name); safePath(file); regular(file); return [name, sha(fs.readFileSync(file))];
    }));
  }
  function verifyConfig(installed = state) {
    if (JSON.stringify(digestFiles(installed)) !== JSON.stringify(installed.digests)) fail('安装配置或密码发生变化，拒绝操作；不要手改受管目录');
  }
  function composeArgs(...argv) {
    return ['compose', '--env-file', 'release.env', '-p', state.project, '-f', 'compose.yaml', ...(state.captionDebug ? ['-f', 'caption-debug.yaml'] : []), ...argv];
  }
  function compose(...argv) {
    return run('podman', composeArgs(...argv), { capture: true });
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
  async function ready(installed = state) {
    const started = performance.now(), deadline = started + 120000;
    let lastNotice = started, reason = 'RUNTIME_UNAVAILABLE';
    console.log('[LexiFlow]   等待 API 正式就绪（最多 120 秒）…');
    while (performance.now() < deadline) {
      if (interrupted && !restoring) fail('等待已取消，已初始化的数据保留，可用 up 再次启动');
      try {
        const [status, currentReason] = await runtime(installed.apiPort, installed.version, deadline - performance.now());
        reason = currentReason;
        if (status === 'PASS' && performance.now() <= deadline) {
          await verifyRunningImage(installed);
          const datasetVersion = await runtimeDataset(installed.apiPort, installed.version);
          if (installed.datasetIdentity?.version && datasetVersion !== installed.datasetIdentity.version) fail('API 响应资料版本改变，拒绝宣称保留资料升级成功');
          console.log(`[LexiFlow]   API 已正式就绪（${Math.floor((performance.now() - started) / 1000)} 秒）`);
          return datasetVersion;
        }
        if (status === 'FAIL') fail('API 响应协议或软件版本不匹配，请检查镜像与扩展版本');
      } catch (error) {
        if (error.message.startsWith('API 响应') || error.message.startsWith('RUNTIME_')) throw error;
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
        internalReady = body.ready === true && body.mode === 'formal' && body.reason === 'OK' && body.softwareVersion === installed.version && body.apiContract === 'caption-hints.v2';
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
  function checkpoint(phase) { if (interrupted && !restoring) fail('安装已取消，保留当前阶段'); state.phase = phase; save(); }
  function progress(text) { step = text; console.log(`[LexiFlow] ${text}`); }
  function sourceFingerprint() {
    // 安装恢复与软件构建使用同一 Git 可见输入闭包，避免漏掉版本解析器等间接依赖。
    // ignored 运行日志、字幕、缓存和数据仍由共享解析器排除。
    return resolveBuildIdentity(repo).buildId;
  }

  function writeLock(childPid) {
    if (!lockOwned) return;
    lockRecord.childPid = childPid;
    // 临时文件在锁目录外；强杀不能留下半条 owner 或污染 .lock 的内容集合。
    const temporary = path.join(dir, `.lock-owner-${lockRecord.token}.next`);
    const fd = fs.openSync(temporary, 'wx', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(lockRecord)); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    fs.renameSync(temporary, path.join(dir, '.lock/owner.json'));
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
    }
    // 先退出活动锁命名空间；清理中被强杀也不留下无法辨识的空活动锁。
    const retired = path.join(dir, `.lock-retired-${crypto.randomBytes(16).toString('hex')}`);
    fs.renameSync(root, retired);
    if (entries.length) fs.unlinkSync(path.join(retired, 'owner.json'));
    fs.rmdirSync(retired);
  }
  function acquireLock(confirmLegacy = false) {
    unlockProcess = acquireProcessLock(dir);
    try {
      // 旧协议无 owner 的交接目录不能当作新内核锁回收，不删除未知遗留物。
      if (fs.existsSync(path.join(dir, '.lock-claim'))) fail('发现未知交接锁；保留现场，拒绝自动删除');
      retireLock(confirmLegacy);
      lockRecord = { schema: 1, root: dir, installation: state.id, pid: process.pid, token: crypto.randomBytes(16).toString('hex'), childPid: null };
      const prepared = path.join(dir, `.lock-prepared-${lockRecord.token}`);
      fs.mkdirSync(prepared, { mode: 0o700 });
      const owner = fs.openSync(path.join(prepared, 'owner.json'), 'wx', 0o600);
      try { fs.writeFileSync(owner, JSON.stringify(lockRecord)); fs.fsyncSync(owner); }
      finally { fs.closeSync(owner); }
      // 只有完整 owner 记录才成为活动锁；准备中的残留不具有锁资格。
      fs.renameSync(prepared, path.join(dir, '.lock'));
      lockOwned = true;
    } catch (error) {
      unlockProcess(); unlockProcess = undefined;
      throw error;
    }
  }
  function releaseLock() {
    try {
      if (!lockOwned) return;
      if (lockRecord.childPid && alive(-lockRecord.childPid)) {
        console.error('[LexiFlow] 子进程组仍存在，安装锁已保留；不要同时启动下一次安装');
        return;
      }
      const ownerFile = path.join(dir, '.lock/owner.json');
      const owner = JSON.parse(fs.readFileSync(ownerFile, 'utf8'));
      if (owner.token !== lockRecord.token || owner.pid !== process.pid) return;
      const retired = path.join(dir, `.lock-retired-${lockRecord.token}`);
      fs.renameSync(path.join(dir, '.lock'), retired);
      fs.unlinkSync(path.join(retired, 'owner.json')); fs.rmdirSync(retired);
      lockOwned = false;
    } finally {
      unlockProcess?.(); unlockProcess = undefined;
    }
  }
  async function apiContainerId(installed, { all = false, optional = false } = {}) {
    const rows = JSON.parse(await run('podman', ['ps', ...(all ? ['-a'] : []), '--format', 'json'], { capture: true, timeout: 10000 }));
    const owned = rows.filter(row => {
      const labels = row.Labels || row.labels || {};
      return labels['lexiflow.installation'] === installed.id
        && (labels['com.docker.compose.project'] || labels['io.podman.compose.project']) === installed.project
        && (labels['com.docker.compose.service'] || labels['io.podman.compose.service']) === 'api';
    });
    if (optional && owned.length === 0) return null;
    const container = owned.length === 1 ? (owned[0].Id || owned[0].ID) : '';
    if (!/^[a-f0-9]{64}$/.test(container)) fail('RUNTIME_CONTAINER_IDENTITY_MISMATCH');
    return container;
  }
  async function verifyRunningImage(installed) {
    const container = await apiContainerId(installed);
    const image = await run('podman', ['inspect', '--format', '{{.Image}}', container], { capture: true, timeout: 10000 });
    if (image.replace(/^sha256:/, '') !== installed.apiImage.replace(/^sha256:/, '')) fail('RUNTIME_IMAGE_MISMATCH');
    if (installed.buildIdentity) {
      const labels = JSON.parse(await run('podman', ['image', 'inspect', '--format', '{{json .Labels}}', installed.apiImage], { capture: true, timeout: 10000 }));
      if (labels['io.lexiflow.build-id'] !== installed.buildIdentity.buildId
          || labels['org.opencontainers.image.revision'] !== installed.buildIdentity.sourceCommit
          || labels['org.opencontainers.image.version'] !== installed.version
          || labels['io.lexiflow.source-sha256'] !== installed.buildIdentity.sourceSha256) fail('RUNTIME_BUILD_IDENTITY_MISMATCH');
      const platform = await run('podman', ['image', 'inspect', '--format', '{{json .Os}} {{json .Architecture}}', installed.apiImage], { capture: true, timeout: 10000 });
      if (platform !== '"linux" "arm64"') fail('RUNTIME_PLATFORM_MISMATCH');
    }
  }
  function imageLabels(identity) {
    return ['--label', `org.opencontainers.image.version=${identity.softwareVersion}`, '--label', `org.opencontainers.image.revision=${identity.sourceCommit}`, '--label', `io.lexiflow.build-id=${identity.buildId}`, '--label', `io.lexiflow.source-sha256=${identity.sourceSha256}`];
  }
  function upgradeContext() {
    return {
      dir,
      loadState: () => { regular(path.join(dir, 'state.json')); return JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8')); },
      saveState: value => { atomicJson(path.join(dir, 'state.json'), value); state = value; },
      digests: digestFiles,
      verify: async installed => { verifyConfig(installed); await ownedResources(); },
      schemaDigest: () => candidate ? candidate.artifacts.sql.sha256 : sha(fs.readFileSync(path.join(repo, 'infra/postgres/schema.sql'))),
      checkCancelled: () => { if (interrupted) fail('升级已取消'); },
      restore: async work => { restoring = true; try { return await work(); } finally { restoring = false; } },
      activate: async installed => {
        await ownedResources();
        const previous = await apiContainerId(installed, { all: true, optional: true });
        await compose('config');
        // 升级与恢复明确替换 API，不依赖 provider 对镜像/config-hash 的自动判定。
        // 不重建依赖服务；相同构建的 no-op 在事务入口已返回，不会来到这里。
        await compose('up', '-d', '--no-deps', '--force-recreate', 'api');
        const current = await apiContainerId(installed);
        if (current === previous) fail('UPGRADE_API_NOT_RECREATED：API 容器未替换，拒绝声明升级成功');
        console.log(`[LexiFlow] API 容器已替换: ${previous ?? '不存在'} -> ${current}；继续核验镜像与就绪状态`);
      },
      ready,
      prepare: async (work, identity, installed, persistPlan) => {
        if (candidate) {
          await ready(installed);
          const frozen = await candidateAdapter.readCandidate(candidateRequest);
          if (frozen.candidateSha256 !== candidate.candidateSha256 || frozen.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
          const prepared = await candidateAdapter.installCandidateArtifacts(frozen, { run, installDir: work, apiPort: installed.apiPort, includePostgres: false, persistPlan });
          // Upgrade preparation loads only API; PostgreSQL image and volume stay untouched.
          return { apiImage: prepared.apiImage, artifacts: { image: prepared.apiImage, candidateSha256: frozen.candidateSha256, manifestSha256: frozen.manifestSha256,
            apiArchiveSha256: frozen.artifacts['api-image:linux/arm64'].sha256, extensionArchiveSha256: frozen.artifacts.extension.sha256 }, datasetIdentity: installed.datasetIdentity };
        }
        if (!candidate) {
          environment = javaBuildEnvironment(environment);
        }
        const datasetVersion = await ready(installed);
        progress('构建新版应用和插件；原服务继续运行');
        await run('./gradlew', ['--no-daemon', ':api:bootJar'], { cwd: path.join(repo, 'backend') });
        await run('npm', ['ci'], { cwd: path.join(repo, 'extension') });
        await run('npm', ['run', 'build'], { cwd: path.join(repo, 'extension'), env: { ...environment, LEXIFLOW_API_PORT: String(installed.apiPort) } });
        if (resolveBuildIdentity(repo).buildId !== identity.buildId) fail('升级构建期间源码已改变，请重试');
        const planned = [];
        let plannedBytes = 0;
        function addPlanned(name, file) {
          const info = fs.lstatSync(file);
          if (!info.isFile() || info.isSymbolicLink()) fail('EXTENSION_FILE_REQUIRED');
          plannedBytes += info.size;
          if (planned.length >= preparationLimits.maxFiles || info.size > preparationLimits.maxBytes || plannedBytes > preparationLimits.maxBytes) fail('UPGRADE_WORKSPACE_LIMIT');
          planned.push({ path: name, bytes: readBoundedFile(file, preparationLimits.maxBytes - (plannedBytes - info.size)) });
        }
        for (const [source, name] of [[`backend/product/api/build/libs/api-${identity.softwareVersion}.jar`, 'lexiflow-api.jar'], ['ops/docker/Dockerfile', 'Dockerfile'], ['ops/docker/entrypoint.sh', 'entrypoint.sh']]) {
          addPlanned(`api/${name}`, path.join(repo, source));
        }
        const extensionRoot = path.join(repo, 'extension/dist');
        let visited = 0;
        function collect(relative = '', depth = 0) {
          if (depth > 32 || ++visited > preparationLimits.maxFiles * 2) fail('UPGRADE_WORKSPACE_LIMIT');
          const directory = relative ? path.join(extensionRoot, relative) : extensionRoot;
          const info = fs.lstatSync(directory); if (info.isSymbolicLink() || !info.isDirectory()) fail('EXTENSION_SYMLINK_REJECTED');
          for (const name of fs.readdirSync(directory).sort()) {
            const child = relative ? `${relative}/${name}` : name, childPath = path.join(directory, name), childInfo = fs.lstatSync(childPath);
            if (childInfo.isSymbolicLink()) fail('EXTENSION_SYMLINK_REJECTED');
            if (childInfo.isDirectory()) collect(child, depth + 1);
            else if (childInfo.isFile()) addPlanned(`extension/${child}`, childPath);
            else fail('EXTENSION_FILE_REQUIRED');
          }
        }
        collect();
        await writePreparedTree(work, planned, persistPlan);
        verifyExtension(path.join(work, 'extension'), identity, installed.apiPort);
        const api = path.join(work, 'api');
        const base = 'docker.io/library/eclipse-temurin:25-jre';
        await run('podman', ['pull', '--platform', 'linux/arm64', base]);
        const baseId = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', base], { capture: true });
        const tag = `localhost/lexiflow-api:${identity.buildId}`;
        await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `JAVA_RUNTIME_IMAGE=${baseId}`, ...imageLabels(identity), '-t', tag, api]);
        const apiImage = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', tag], { capture: true });
        if (resolveBuildIdentity(repo).buildId !== identity.buildId) fail('升级准备期间源码已改变，请重试');
        const datasetIdentity = installed.datasetIdentity ?? (installed.packageType === 'release-package'
          ? { version: datasetVersion, sourceSha256: installed.datasetPackageSha256, releasePackageSha256: installed.datasetPackageSha256 }
          : { version: datasetVersion, sourceSha256: installed.digests[path.relative(dir, path.join(installed.dataset, 'stardict.csv'))] });
        return { apiImage, datasetIdentity, artifacts: { jar: sha(fs.readFileSync(path.join(api, 'lexiflow-api.jar'))), image: apiImage } };
      },
    };
  }
  async function main() {
    if (candidateRequest !== undefined) {
      candidateAdapter = await import('./candidate.mjs');
      candidate = await candidateAdapter.readCandidate(candidateRequest);
      if (!argv.some((value, index) => value === '--dir' && index + 1 < argv.length)) fail('CANDIDATE_DIR_REQUIRED');
    }
    if (action === '--help' || action === 'help') { console.log(help); return; }
    if (!['install', 'upgrade', 'version', 'up', 'status', 'stop', 'logs', 'recover', 'doctor'].includes(action)) fail(help);
    if (candidate && action !== 'install' && action !== 'upgrade' && action !== 'recover') fail('CANDIDATE_ACTION_UNSUPPORTED');
    const seen = new Set();
    let confirmStopped = false, jsonReport = false;
    while (args.length) {
      const key = args.shift();
      if (action === 'doctor' && key === '--json' && !jsonReport) { jsonReport = true; continue; }
      if (action === 'recover' && key === '--confirm-stopped' && !confirmStopped) { confirmStopped = true; continue; }
      if (action === 'install' && key === '--caption-debug' && !seen.has(key)) { seen.add(key); console.log('[LexiFlow] --caption-debug 已无需指定；字幕日志默认开启。'); continue; }
      const value = args.shift();
      if (!value || seen.has(key)) fail(help); seen.add(key);
      if (key === '--dir') dir = value;
      else if (action === 'install' && ['--api-port', '--db-port'].includes(key) && /^[1-9][0-9]{0,4}$/.test(value) && +value <= 65535) {
        if (key === '--api-port') apiPort = +value; else dbPort = +value;
      } else fail(help);
    }
    if (action === 'doctor') return doctor(dir, environment, jsonReport);
    safePath(dir); dir = path.resolve(dir);
    if (dir === repo || dir === os.homedir() || dir === '/') fail('不能使用源码根目录、HOME 或文件系统根作为安装目录');
    if (candidate) {
      if (seen.has('--api-port') && apiPort !== 18080) fail('CANDIDATE_API_PORT_FIXED');
      apiPort = 18080;
    }
    if (apiPort === dbPort) fail('API 与数据库端口不能相同');
    if (Number(process.versions.node.split('.')[0]) < 22) fail('需要 Node.js 22 或以上');
    if (action === 'version') {
      let installed = null;
      if (fs.existsSync(dir)) { regular(path.join(dir, 'state.json')); installed = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8')); if (installed.root !== dir) fail('安装归属不符'); }
      const info = versionSummary(installed, resolveBuildIdentity(repo), await githubLatestRelease());
      console.log(`已安装版本: ${info.installed ?? '未安装'}\n本地源码版本: ${info.local}\nGitHub 最新正式版: ${info.latestRelease ?? '未知（查询失败或尚无正式发行）'}`);
      return;
    }
    if (os.platform() !== 'darwin' || os.arch() !== 'arm64') fail('本入口仅用于 M 芯片 macOS');
    console.log('[LexiFlow] 检查安装目录与 Podman 环境…');
    const existed = fs.existsSync(dir);
    if (existed) {
      regular(path.join(dir, 'state.json'));
      state = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));
      if (!Number.isInteger(state.apiPort) || state.apiPort < 1 || state.apiPort > 65535 || !Number.isInteger(state.dbPort) || state.dbPort < 1 || state.dbPort > 65535 || state.apiPort === state.dbPort) fail('安装端口无效');
      if (state.schema !== 1 || state.root !== dir || !/^[a-f0-9]{32}$/.test(state.id) || state.project !== `lexiflow-local-${state.id}` || !['new', 'built', 'source', 'initializing', 'initialized', 'ready'].includes(state.phase)) fail('安装身份或状态不合法，拒绝接管');
      if (candidate && state.apiPort !== 18080) fail('CANDIDATE_API_PORT_FIXED');
      if (action === 'install' && ((seen.has('--api-port') && apiPort !== state.apiPort) || (seen.has('--db-port') && dbPort !== state.dbPort))) fail('已有安装沿用原端口；升级请使用 upgrade，无需重复指定端口');
    } else {
      if (action !== 'install') fail('尚未安装，请先运行 install');
      await run('podman', ['info'], { cwd: repo, timeout: 30000 });
      await run('podman', ['compose', 'version'], { cwd: repo, timeout: 30000 });
      await freePort(apiPort); await freePort(dbPort);
      fs.mkdirSync(path.dirname(dir), { recursive: true, mode: 0o700 });
      fs.mkdirSync(dir, { mode: 0o700 });
      const id = crypto.randomBytes(16).toString('hex');
      const buildIdentity = candidate?.buildIdentity ?? resolveBuildIdentity(repo);
      state = { schema: 1, id, project: `lexiflow-local-${id}`, root: dir, apiPort, dbPort, captionDebug: false, phase: 'new', version: buildIdentity.softwareVersion, buildIdentity, installationStartedAt: new Date().toISOString(), installationOperation: crypto.randomBytes(16).toString('hex'), source: candidate ? null : sourceFingerprint(), ...(candidate ? { packageType: 'release-package', candidateSha256: candidate.candidateSha256, manifestSha256: candidate.manifestSha256 } : {}) };
      save();
    }
    if (action === 'logs') {
      progress('持续查看服务日志（仅显示新日志；Ctrl+C 退出，不停止服务）');
      // 查看日志不占用安装锁，也不触发升级恢复或创建 operation 日志。
      if (state.digests) verifyConfig();
      await run('podman', ['info'], { timeout: 30000 });
      await ownedResources();
      if (interrupted) return;
      await runCommand('podman', composeArgs('logs', '--follow', '--tail=0', 'postgres', 'api'), {
        cwd: dir, env: environment, follow: true, label: '查看服务日志',
      });
      return;
    }
    if (state.packageType === 'release-package' && !['initialized', 'ready'].includes(state.phase)
      && ['install', 'recover'].includes(action)) {
      if (!candidate || state.candidateSha256 !== candidate.candidateSha256 || state.manifestSha256 !== candidate.manifestSha256) fail('候选安装重试必须提供原候选目录及摘要');
    }
    if (action === 'recover' && !fs.existsSync(path.join(dir, 'upgrade.json'))) {
      if (!confirmStopped) fail('恢复旧锁需明确确认原安装已经退出：recover --confirm-stopped');
      if (!['new', 'built'].includes(state.phase)) fail('只允许恢复建库前的安装；已进入数据库阶段时保留现场，不自动清库或重导');
      acquireLock(true);
      // 旧脚本升级后的建库前安装完整重建；保留密码、词库和已有镜像，不接管数据库。
      state.installationOperation = crypto.randomBytes(16).toString('hex'); state.installationStartedAt = new Date().toISOString();
      if (state.packageType === 'release-package') {
        state.source = null; state.buildIdentity = candidate.buildIdentity;
      } else {
        state.source = sourceFingerprint(); state.buildIdentity = resolveBuildIdentity(repo);
      }
      state.version = state.buildIdentity.softwareVersion; state.phase = 'new'; save();
      console.log('[LexiFlow] 建库前安装锁已恢复，数据和密码未删除；请重新执行 install');
      return;
    }
    acquireLock(action === 'recover' && confirmStopped);
    if (action === 'install' && !state.lastOperation && state.buildIdentity) {
      state.installationOperation = crypto.randomBytes(16).toString('hex'); state.installationStartedAt = new Date().toISOString(); save();
    }
    logFile = path.join(dir, `operation-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.log`);
    fs.writeFileSync(logFile, '', { mode: 0o600, flag: 'wx' });
    console.log(`[LexiFlow] 详细日志（实时写入）: ${logFile}`);
    await run('podman', ['info'], { timeout: 30000 });
    await ownedResources();
    const recovered = await upgradeContext().restore(() => recoverUpgrade(upgradeContext()));
    if (recovered) { state = recovered; console.log('[LexiFlow] 已处理上次升级事务，数据库未重建；不代表数据库回滚。'); }
    if (action === 'recover') return;
    if (state.digests) {
      const repaired = state.packageType === 'release-package' ? false : repairNetwork({ dir, state, template: fs.readFileSync(path.join(repo, 'ops/podman/compose.validation.yaml'), 'utf8'), actualDigests: digestFiles, save, enabled: ['install', 'up'].includes(action) });
      if (repaired) console.log('[LexiFlow] 已自动修复端口发布网络；保留原数据库、词库和密码，正在重建服务连接。');
      verifyConfig();
    }
    if (['install', 'upgrade'].includes(action) && ['initialized', 'ready'].includes(state.phase)) {
      const target = candidate?.buildIdentity ?? resolveBuildIdentity(repo);
      console.log(`已安装版本: ${state.version}\n${candidate ? '本次候选版本' : '本地源码版本'}: ${target.softwareVersion}`);
      if (action === 'upgrade') {
        progress('原地升级（保留 PostgreSQL、端口、密码和词库）');
        const result = await upgradeInstallation(upgradeContext(), target); state = result.state;
        console.log(result.updated ? '升级成功。请在 chrome://extensions 重新加载原插件，再刷新视频页面。' : '同一构建，无需升级。');
        return;
      }
      console.log('本次未更新应用或插件；install 仅启动已有安装。');
      if (versionSummary(state, target).needsUpgrade) console.log(`更新请执行: node ops/podman/local.mjs upgrade${dir === path.join(repo, '.local/podman') ? '' : ` --dir '${dir.replaceAll("'", "'\\''")}'`}`);
    }
    if (action === 'upgrade') fail('安装尚未完成，先运行 install；升级不会重新导入数据库');
    if (action === 'install' && !['initialized', 'ready'].includes(state.phase)) {
      if (state.phase === 'initializing') fail('上次数据库初始化未确认完成，已保留现场；禁止自动重导或清库，请查看日志');
      if (state.packageType !== 'release-package' && state.source !== sourceFingerprint()) fail('未完成安装的源码已变化；建库前可运行 recover --confirm-stopped 后在原目录重试，数据库阶段须保留现场');
      if (candidate && state.packageType !== 'release-package') fail('不能将候选接入源码安装');
      if (candidate && state.packageType === 'release-package' && (candidate.candidateSha256 !== state.candidateSha256 || candidate.manifestSha256 !== state.manifestSha256)) fail('候选身份改变，拒绝接续安装');
      if (!candidate) {
        environment = javaBuildEnvironment(environment);
      }
      if (candidate && state.phase === 'new') {
        progress('准备已核验发行候选及固定本机资料');
        const loaded = await candidateAdapter.installCandidateArtifacts(candidate, { run, installDir: dir, apiPort: state.apiPort });
        state.apiImage = loaded.apiImage; state.postgresImage = loaded.postgresImage;
        state.dataset = path.join(dir, 'data', `release-package.${candidate.artifacts.dataset.sha256.slice(0, 16)}`);
        fs.mkdirSync(state.dataset, { recursive: true, mode: 0o700 });
        const datasetDigest = await candidateAdapter.copyCandidateArtifact(candidate, 'dataset', path.join(state.dataset, 'dataset.zip'));
        fs.mkdirSync(path.join(dir, 'infra/postgres'), { recursive: true, mode: 0o700 });
        await candidateAdapter.copyCandidateArtifact(candidate, 'sql', path.join(dir, 'infra/postgres/schema.sql'));
        state.datasetPackageSha256 = datasetDigest;
        state.schemaSha256 = candidate.artifacts.sql.sha256;
        const baseCompose = fs.readFileSync(path.join(repo, 'ops/docker/compose.yaml'), 'utf8');
        let localCompose = baseCompose.replace('ports: ["127.0.0.1:18080:8080"]', `ports: ["127.0.0.1:${state.apiPort}:8080"]`);
        localCompose = localCompose.replace('    secrets: [postgres-password, app-password]\n    volumes:', '    secrets: [postgres-password, app-password]\n    ports: ["127.0.0.1:' + state.dbPort + ':5432"]\n    volumes:');
        localCompose = localCompose.replace('    networks: [private]\n    mem_limit: 768m', '    networks: [private, published]\n    mem_limit: 768m');
        localCompose = localCompose.replace('    networks: [private]\n    depends_on:\n      postgres:\n        condition: service_healthy\n      initialize:\n        condition: service_completed_successfully\n', '    networks: [private, published]\n    depends_on:\n      postgres:\n        condition: service_healthy\n');
        localCompose = localCompose.replace('    environment: LEXIFLOW_COMPOSE_POSTGRES_PASSWORD', '    file: ./secrets/postgres-password').replace('    environment: LEXIFLOW_COMPOSE_APP_PASSWORD', '    file: ./secrets/app-password');
        localCompose = localCompose.replace(/(networks:\n  private:\n    internal: true\n    labels:\n      lexiflow.installation: \$\{LEXIFLOW_INSTALLATION_ID:\?LEXIFLOW_INSTALLATION_ID is required\}\n      lexiflow.release: \$\{LEXIFLOW_RELEASE_KEY:\?LEXIFLOW_RELEASE_KEY is required\}\n)$/, '$1  published:\n    driver: bridge\n    labels:\n      lexiflow.installation: ${LEXIFLOW_INSTALLATION_ID:?LEXIFLOW_INSTALLATION_ID is required}\n      lexiflow.release: ${LEXIFLOW_RELEASE_KEY:?LEXIFLOW_RELEASE_KEY is required}\n');
        if (!localCompose.includes(`127.0.0.1:${state.apiPort}:8080`) || !localCompose.includes(`127.0.0.1:${state.dbPort}:5432`)
          || (localCompose.match(/file: \.\/secrets\//g) || []).length !== 2 || !localCompose.includes('  published:\n')
          || !localCompose.includes('networks: [private, published]') || localCompose.includes('condition: service_completed_successfully')
          || !localCompose.includes('read_only: true') || !localCompose.includes('cap_drop: [ALL]')
          || !localCompose.includes('security_opt: [no-new-privileges:true]')) fail('CANDIDATE_COMPOSE_ADAPTATION_FAILED');
        const secretDir = path.join(dir, 'secrets'); fs.mkdirSync(secretDir, { recursive: true, mode: 0o700 });
        for (const name of ['postgres-password', 'app-password']) { const file = path.join(secretDir, name); if (!fs.existsSync(file)) fs.writeFileSync(file, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o400, flag: 'wx' }); regular(file); }
        const envText = `LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE=${state.apiImage}\nLEXIFLOW_POSTGRES_IMAGE=${state.postgresImage}\nLEXIFLOW_INSTALLATION_ID=${state.id}\nLEXIFLOW_RELEASE_KEY=local-${state.id}\nLEXIFLOW_DATASET_FILE="${path.join(state.dataset, 'dataset.zip')}"\nLEXIFLOW_DATASET_SHA256=${state.datasetPackageSha256}\n`;
        writeCandidateFile(path.join(dir, 'release.env'), envText);
        writeCandidateFile(path.join(dir, 'compose.yaml'), boundedApiLogging(localCompose));
        state.artifacts = { apiImage: state.apiImage, postgresImage: state.postgresImage, extension: verifyExtension(path.join(dir, 'extension'), candidate.buildIdentity, 18080), candidateSha256: state.candidateSha256, manifestSha256: state.manifestSha256,
          apiArchiveSha256: candidate.artifacts['api-image:linux/arm64'].sha256, postgresArchiveSha256: candidate.artifacts['postgres-image:linux/arm64'].sha256, extensionArchiveSha256: candidate.artifacts.extension.sha256, datasetSha256: state.datasetPackageSha256 };
        state.digests = digestFiles(); checkpoint('source');
      }
      if (state.phase === 'new') {
        progress('1/6 构建 Java 应用与 Chrome 扩展');
        await run('./gradlew', ['--no-daemon', ':api:bootJar'], { cwd: path.join(repo, 'backend') });
        await run('npm', ['ci'], { cwd: path.join(repo, 'extension') });
        await run('npm', ['run', 'build'], { cwd: path.join(repo, 'extension'), env: { ...environment, LEXIFLOW_API_PORT: String(state.apiPort) } });
        if (resolveBuildIdentity(repo).buildId !== state.buildIdentity?.buildId) fail('构建源码已变化，拒绝安装身份不一致的制品；请保留现场并重试恢复');
        for (const [from, to] of [
          [`backend/product/api/build/libs/api-${state.version}.jar`, 'build/api/lexiflow-api.jar'], ['ops/docker/Dockerfile', 'build/api/Dockerfile'], ['ops/docker/entrypoint.sh', 'build/api/entrypoint.sh'], ['ops/docker/Dockerfile.postgres', 'build/postgres/Dockerfile'], ['ops/docker/bootstrap.sh', 'build/postgres/bootstrap.sh'], ['infra/postgres/schema.sql', 'infra/postgres/schema.sql'], ['ops/dataset/ecdict-source.lock.json', 'ops/dataset/ecdict-source.lock.json'], ['scripts/environment/ecdict_bundle.py', 'scripts/environment/ecdict_bundle.py'], ['ops/podman/fetch-ecdict.sh', 'ops/podman/fetch-ecdict.sh'], ['ops/podman/source-tools.Containerfile', 'ops/podman/source-tools.Containerfile'],
        ]) copy(from, to);
        fs.cpSync(path.join(repo, 'extension/dist'), path.join(dir, 'extension'), { recursive: true });
        state.artifacts = { jar: sha(fs.readFileSync(path.join(dir, 'build/api/lexiflow-api.jar'))), extension: verifyExtension(path.join(dir, 'extension'), state.buildIdentity, state.apiPort) };
        let template = fs.readFileSync(path.join(repo, 'ops/podman/compose.validation.yaml'), 'utf8');
        template = template.replace('127.0.0.1:18080:8080', `127.0.0.1:${state.apiPort}:8080`).replace('127.0.0.1:15432:5432', `127.0.0.1:${state.dbPort}:5432`);
        fs.writeFileSync(path.join(dir, 'compose.yaml'), boundedApiLogging(template));
        progress('2/6 构建 ARM64 容器镜像');
        for (const [name, base, arg] of [['api', 'docker.io/library/eclipse-temurin:25-jre', 'JAVA_RUNTIME_IMAGE'], ['postgres', 'docker.io/library/postgres:17-bookworm', 'POSTGRES_RUNTIME_IMAGE']]) {
          await run('podman', ['pull', '--platform', 'linux/arm64', base]);
          const baseId = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', base], { capture: true });
          const tag = `localhost/lexiflow-${name}:${state.id}`;
          await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `${arg}=${baseId}`, ...(name === 'api' ? imageLabels(state.buildIdentity) : []), '-t', tag, `build/${name}`]);
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
        if (state.captionDebug) fs.writeFileSync(path.join(dir, 'caption-debug.yaml'), 'services:\n  api:\n    command: [api-debug]\n', { mode: 0o600 });
        state.digests = digestFiles(); checkpoint('source');
      }
      if (state.phase === 'source') {
        await freePort(state.apiPort);
        // 已归属 PG 可能在 checkpoint 前启动过；不要把自己的端口当作外部冲突。
        if (!(await ownedResources()).has('postgres')) await freePort(state.dbPort);
        verifyConfig();
        progress('4/6 启动 PostgreSQL'); await compose('config'); await compose('up', '-d', 'postgres');
        // compose initialize 的 service_healthy 依赖负责等待 PG，而不是错误即重导。
        progress('5/6 首次初始化资料（不要中断）'); checkpoint('initializing');
        await compose('run', '--rm', 'initialize'); checkpoint('initialized');
      }
    }
    if (!state.digests) fail('安装未完成，请运行 install 重试建库前步骤');
    verifyConfig(); await ownedResources();
    if (action === 'stop') { progress('停止服务，保留数据库'); await compose('stop'); return; }
    if (action === 'status') { console.log(await compose('ps')); await ready(); summary(); return; }
    if (!['initialized', 'ready'].includes(state.phase)) fail('数据库尚未确认初始化完成；请查看安装日志');
    progress('6/6 启动应用并验证就绪');
    await compose('up', '-d', 'postgres'); await compose('up', '-d', '--no-deps', 'api');
    const datasetVersion = await ready();
    if (!state.datasetIdentity) state.datasetIdentity = state.packageType === 'release-package'
      ? { version: datasetVersion, sourceSha256: state.datasetPackageSha256, releasePackageSha256: state.datasetPackageSha256 }
      : { version: datasetVersion, sourceSha256: state.digests[path.relative(dir, path.join(state.dataset, 'stardict.csv'))] };
    if (state.installationOperation && !state.lastOperation) {
      state.lastOperation = installationRecord({ operation: state.installationOperation, kind: 'install', previous: null, target: state.buildIdentity, startedAt: state.installationStartedAt, status: 'PASS', artifacts: { ...state.artifacts, image: state.apiImage }, dataset: state.datasetIdentity ?? null });
    }
    checkpoint('ready');
    if (state.lastOperation) publishRecord(dir, state.lastOperation);
    summary();
  }
  try {
    return (await main()) ?? 0;
  } catch (error) {
    if (lockOwned && action === 'install' && state?.buildIdentity && state.installationOperation && !state.lastOperation) {
      try { publishRecord(dir, installationRecord({ operation: state.installationOperation, kind: 'install', previous: null, target: state.buildIdentity, startedAt: state.installationStartedAt, status: 'FAIL', artifacts: state.artifacts ?? null, dataset: state.datasetIdentity ?? null })); }
      catch { console.error('[LexiFlow] 失败安装记录未能写入，请保留操作日志。'); }
    }
    console.error(`[LexiFlow] ${step}失败：${error.message}`);
    return 1;
  } finally {
    process.removeListener('SIGINT', markInterrupted);
    process.removeListener('SIGTERM', markInterrupted);
    try { releaseLock(); }
    catch (error) { console.error(`[LexiFlow] 安装锁清理失败：${error.message}`); return 1; }
    finally { sessionBusy = false; }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runLocal(process.argv.slice(2)).then(code => { process.exitCode = code; });
}
