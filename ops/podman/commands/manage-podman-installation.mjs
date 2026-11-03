#!/usr/bin/env node
// 单一部署入口；install/upgrade 只调用 shared planner/executor，其他动作不隐式导入。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { runCommand, HEARTBEAT_MS } from '../execution/deployment-process-control.mjs';
import { acquireProcessLock } from '../execution/installation-operation-lock.mjs';
import { doctor, runtime, runtimeDataset } from './diagnose-podman-installation.mjs';
import { resolveBuildIdentity } from '../../release/integrity/source-build-identity.mjs';
import { githubLatestRelease, versionSummary } from '../installation/installation-version-summary.mjs';
import { runUnifiedDeployment, inspectInstalledDeployment, readPreparedTarget, activatePreparedTarget, verifyInstalledExtensionArchive } from '../installation/installation-upgrade-workflow.mjs';
import { readOperationJournal, writeOperationJournal } from '../installation/installation-plan-records.mjs';
import { deploymentSlot, acquireDeploymentSlotLock } from '../deployment-slots.mjs';
import { validateSqliteDatabaseFile } from '../environment/local-sqlite-target.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const help = '用法: node ops/podman/commands/manage-podman-installation.mjs install|upgrade|version|up|status|stop|logs|recover|doctor [--dir 绝对路径] [--plan | --confirm-plan <sha256>]';
let sessionBusy = false;

export async function runLocal(argv) {
  if (!Array.isArray(argv) || !argv.every(value => typeof value === 'string')) { console.error('[LexiFlow] argv 必须是字符串数组'); return 1; }
  if (sessionBusy) { console.error('[LexiFlow] 本模块已有本机安装会话正在运行，拒绝并发调用'); return 1; }
  sessionBusy = true;
  const args = [...argv], action = args.shift();
  const sourceSlot = deploymentSlot('source', repo);
  let dir = sourceSlot.directory, apiPort = sourceSlot.apiPort;
  let state, logFile, lockRecord, lockOwned = false, unlockProcess, unlockSlot, interrupted = false, step = '检查参数';
  const abort = new AbortController();
  let environment = Object.fromEntries(['HOME', 'PATH', 'JAVA_HOME', 'LEXIFLOW_JAVA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSH_AUTH_SOCK', 'CONTAINER_HOST', 'CONTAINER_CONNECTION', 'DOCKER_HOST', 'PODMAN_COMPOSE_PROVIDER'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
  const markInterrupted = () => { interrupted = true; abort.abort(); };
  process.on('SIGINT', markInterrupted); process.on('SIGTERM', markInterrupted);
  const sha = value => crypto.createHash('sha256').update(value).digest('hex');
  const fail = message => { throw new Error(message); };
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
    if (argv[0] === 'pull') return '拉取 Java 25 基础镜像';
    if (argv[0] === 'build') return path.basename(argv.at(-1)) === 'api' ? '构建 API 镜像' : '构建词库工具镜像';
    if (argv[0] === 'run') return argv.includes('--deployment-data')
      ? (argv.includes('apply') ? '提交资料到 SQLite 文件' : '校验 SQLite 文件')
      : argv.includes('/kit/ops/dataset/prepare_ecdict_bundle.py') ? '下载、校验并精简 ECDICT' : '检查 SQLite 数据库文件';
    if (argv[0] === 'compose') {
      if (argv.includes('initialize')) return '初始化 SQLite 资料文件';
      if (argv.includes('up')) return '启动 API';
      if (argv.includes('stop')) return '停止服务';
    }
    return undefined;
  }
  function run(command, argv, { cwd = fs.existsSync(dir) ? dir : repo, timeout = 600000, capture = false, env = environment, logOutput = true } = {}) {
    if (interrupted) { const error = new Error('DEPLOYMENT_OPERATION_CANCELLED'); error.commandInterrupted = true; throw error; }
    const label = commandLabel(command, argv);
    return runCommand(command, argv, { cwd, timeout, capture, env, logFile: logOutput ? logFile : undefined, label: label || `执行 ${command} 检查`, announce: Boolean(label), onStart: pid => writeLock(pid), onClose: ({ groupGone }) => { if (groupGone) writeLock(null); } });
  }
  async function cleanupOwnSourceTools(toolsId, owner) {
    if (!/^sha256:[a-f0-9]{64}$/.test(toolsId) || !/^lexiflow-source-tools-[a-f0-9]{32}$/.test(owner))
      fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
    // 唯一受限的取消后命令路径：仅本轮 iidfile 的完整 ID 与唯一 owner 标签。
    const cleanupRun = argv => runCommand('podman', argv, { cwd: repo, env: environment, capture: true,
      timeout: 30000, label: '清理本轮来源工具镜像', onStart: pid => writeLock(pid),
      onClose: ({ groupGone }) => { if (groupGone) writeLock(null); } });
    const labels = JSON.parse(await cleanupRun(['image', 'inspect', '--format', '{{json .Labels}}', toolsId]));
    if (labels?.['io.lexiflow.source-tools-owner'] !== owner) fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
    await cleanupRun(['rmi', toolsId]);
  }
  function save() {
    const next = path.join(dir, `.state-${crypto.randomBytes(16).toString('hex')}.next`);
    fs.writeFileSync(next, JSON.stringify(state, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
    fs.renameSync(next, path.join(dir, 'state.json'));
  }
  function digestFiles(installed = state) {
    if (installed.dataset && !installed.dataset.startsWith(path.join(dir, 'data') + path.sep)) fail('资料目录不属于此安装');
    const names = ['compose.yaml', 'release.env'];
    if (installed.captionDebug) names.push('caption-debug.yaml');
    if (installed.dataset) names.push(path.relative(dir, path.join(installed.dataset, installed.packageType === 'release-package' ? 'dataset.zip' : 'stardict.csv')));
    return Object.fromEntries(names.map(name => {
      const file = path.join(dir, name); safePath(file); regular(file); return [name, sha(fs.readFileSync(file))];
    }));
  }
  function verifyConfig(installed = state) {
    const actual = digestFiles(installed);
    if (JSON.stringify(actual) === JSON.stringify(installed.digests)) return;
    const marker = path.join(dir, 'image-replacement.json');
    if (fs.existsSync(marker)) {
      regular(marker);
      const pending = JSON.parse(fs.readFileSync(marker, 'utf8'));
      const expected = installed.digests;
      if (pending.schema === 1 && pending.installationId === installed.id && pending.root === dir
          && pending.stateSha256 === sha(fs.readFileSync(path.join(dir, 'state.json')))
          && Array.isArray(pending.allowedEnvSha256) && pending.allowedEnvSha256.length <= 4
          && pending.allowedEnvSha256.every(value => /^[a-f0-9]{64}$/.test(value))
          && pending.allowedEnvSha256.includes(actual['release.env'])
          && expected && Object.keys(actual).length === Object.keys(expected).length
          && Object.entries(actual).every(([name, digest]) => name === 'release.env' || expected[name] === digest)) return;
    }
    fail('安装配置或密码发生变化，拒绝操作；不要手改受管目录');
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
  async function ready(installed = state) {
    const started = performance.now(), deadline = started + 120000;
    let lastNotice = started, reason = 'RUNTIME_UNAVAILABLE';
    console.log('[LexiFlow]   等待 API 正式就绪（最多 120 秒）…');
    while (performance.now() < deadline) {
      if (interrupted) fail('等待已取消，已初始化的数据保留，可用 up 再次启动');
      try {
        const [status, currentReason] = await runtime(installed.apiPort, installed.version, deadline - performance.now());
        reason = currentReason;
        if (status === 'PASS' && performance.now() <= deadline) {
          await verifyRunningImage(installed);
          const datasetVersion = await runtimeDataset(installed.apiPort, installed.version);
          if (installed.datasetIdentity?.version && datasetVersion !== installed.datasetIdentity.version) fail('API 响应资料版本与安装记录不一致');
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
        const body = JSON.parse(await run('podman', ['exec', id, '/app/run-container-command.sh', 'health'], { capture: true, timeout: 5000 }));
        internalReady = body.ready === true && body.mode === 'formal' && body.reason === 'OK' && body.softwareVersion === installed.version && body.apiContract === 'caption-hints.v2';
      }
    } catch { /* 不输出工具原文，无法确认时不声称应用健康。 */ }
    fail(internalReady ? '容器内部已就绪，但宿主端口不可达（HOST_PORT_UNREACHABLE）；数据已保留。请运行 doctor；不要重新导入词库' : `API 就绪等待超时（${reason}）；内部健康未确认，请查看 logs；不要重新初始化数据库`);
  }

  function summary() {
    const database = path.join(dir, 'data', 'lexicon.db');
    const extensionZip = state.artifacts?.extensionArchive?.path ? path.join(dir, state.artifacts.extensionArchive.path) : '缺少已绑定 ZIP（请显式 upgrade）';
    console.log(`就绪。API: http://127.0.0.1:${state.apiPort}\nChrome 加载目录: ${path.join(dir, 'extension')}\n插件 ZIP: ${extensionZip}\n插件 SHA256: ${extensionZip === '缺少已绑定 ZIP（请显式 upgrade）' ? extensionZip : `${extensionZip}.sha256`}\nSQLite: ${database}`);
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
  function portTaken(port) {
    return new Promise(resolve => {
      const server = net.createServer();
      server.once('error', () => resolve(true));
      server.listen(port, '127.0.0.1', () => server.close(() => resolve(false)));
    });
  }
  async function main() {
    if (action === 'help' || action === '--help') { console.log(help); return 0; }
    if (!['install', 'upgrade', 'version', 'up', 'status', 'stop', 'logs', 'recover', 'doctor'].includes(action)) fail(help);
    const seen = new Set(); let planOnly = false, confirmPlan, jsonReport = false, confirmStopped = false;
    while (args.length) {
      const key = args.shift();
      if (action === 'doctor' && key === '--json' && !jsonReport) { jsonReport = true; continue; }
      if (action === 'recover' && key === '--confirm-stopped' && !confirmStopped) { confirmStopped = true; continue; }
      if (['install', 'upgrade'].includes(action) && key === '--plan' && !planOnly) { planOnly = true; continue; }
      const value = args.shift();
      if (!value || seen.has(key)) fail(help); seen.add(key);
      if (key === '--dir') dir = value;
      else if (['install', 'upgrade'].includes(action) && key === '--confirm-plan' && /^[a-f0-9]{64}$/.test(value)) confirmPlan = value;
      else if (action === 'install' && key === '--api-port' && /^[1-9][0-9]{0,4}$/.test(value) && +value <= 65535) apiPort = +value; else fail(help);
    }
    if (action === 'doctor') return doctor(dir, environment, jsonReport);
    safePath(dir); dir = path.resolve(dir);
    if ([repo, os.homedir(), '/'].includes(dir)) fail('不能使用源码根目录、HOME 或文件系统根作为安装目录');
    if (['install', 'upgrade'].includes(action) && seen.has('--api-port') && apiPort !== sourceSlot.apiPort) fail('SOURCE_API_PORT_FIXED');
    if (Number(process.versions.node.split('.')[0]) < 22) fail('需要 Node.js 22 或以上');
    if (action === 'version') {
      const installed = fs.existsSync(dir) ? loadState() : null;
      const info = versionSummary(installed, resolveBuildIdentity(repo), await githubLatestRelease());
      console.log(`已安装版本: ${info.installed ?? '未安装'}\n本地源码版本: ${info.local}\nGitHub 最新正式版: ${info.latestRelease ?? '未知（查询失败或尚无正式发行）'}`);
      return 0;
    }
    if (os.platform() !== 'darwin' || os.arch() !== 'arm64') fail('本入口仅用于 M 芯片 macOS');
    if (['install', 'upgrade'].includes(action)) {
      return runUnifiedDeployment({ repo, dir, action, apiPort, seen, enforceFixedSlots: true,
        environment, run, ready, compose, ownedResources, verifyConfig, acquireLock,
        verifyApiPort: async installed => {
          const [status] = await runtime(installed.apiPort, installed.version, 5000);
          if (status !== 'PASS') fail('DEPLOYMENT_API_PORT_OWNERSHIP_UNVERIFIED');
          await verifyRunningImage(installed);
        },
        saveState: next => { state = next; save(); }, setState: next => { state = next; },
        getState: () => state, progress: text => { step = text; console.log(`[LexiFlow] ${text}`); },
        imageLabels, digestFiles, cleanupOwnSourceTools, planOnly, confirmPlan, signal: abort.signal, reportReady: summary,
        interrupted: () => interrupted, setLogFile: file => { logFile = file; console.log(`[LexiFlow] 本次运行私有日志：${file}`); } });
    }
    if (!fs.existsSync(dir)) fail('尚未安装，请先运行 install');
    step = '读取安装记录';
    state = loadState();
    if (!['new', 'initialized', 'ready'].includes(state.phase)) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
    if (['status', 'up'].includes(action) && ['ready', 'initialized'].includes(state.phase)) await verifyInstalledExtensionArchive(dir, state);
    const replacementPending = fs.existsSync(path.join(dir, 'image-replacement.json'));
    if (replacementPending && action !== 'recover') fail('DEPLOYMENT_PENDING_REPLAN_REQUIRED');
    if (action === 'recover') {
      const journalFile = path.join(dir, 'deployment.json');
      const journal = fs.existsSync(journalFile) ? readOperationJournal(journalFile) : null;
      if (replacementPending && (!journal || journal.stage === 'complete'
          || !fs.existsSync(path.join(dir, 'prepared-target.json')))) fail('DEPLOYMENT_PENDING_REPLAN_REQUIRED');
      if (journal && journal.stage !== 'complete') {
        const preparedFile = path.join(dir, 'prepared-target.json');
        if (!fs.existsSync(preparedFile)) fail('DEPLOYMENT_RECOVERY_PREPARED_TARGET_MISSING');
        const prepared = readPreparedTarget(preparedFile, journal, dir);
        await ownedResources();
        const fact = await inspectInstalledDeployment({ repo, state: prepared.targetState, run, environment });
        if (fact.needsRebuild || fact.generationVersion !== prepared.targetState.generationVersion) {
          console.log('[LexiFlow] BLOCKED：数据库目标代际尚未完整提交；仅可重新计划，不会自动重导。');
          return 1;
        }
        acquireLock(confirmStopped);
        const repeated = await inspectInstalledDeployment({ repo, state: prepared.targetState, run, environment });
        if (repeated.needsRebuild || repeated.fingerprint !== fact.fingerprint) fail('DEPLOYMENT_RECOVERY_DATABASE_DRIFT');
        if (await portTaken(state.apiPort)) {
          if (!(await ownedResources()).has('api')) fail('DEPLOYMENT_RECOVERY_API_PORT_OCCUPIED');
          const id = await apiContainerId(state);
          const actual = (await run('podman', ['inspect', '--format', '{{.Image}}', id], { capture: true })).replace(/^sha256:/, '');
          if (![state.apiImage, prepared.apiImage, prepared.previousApiImage].some(value => value?.replace(/^sha256:/, '') === actual)) fail('DEPLOYMENT_RECOVERY_IMAGE_UNKNOWN');
          await compose('stop', 'api');
        }
        state = await activatePreparedTarget({ dir, prepared, previous: state, fact: repeated, digestFiles,
          setState: value => { state = value; }, saveState: value => { state = value; save(); }, compose, ready,
          });
        writeOperationJournal(journalFile, { ...journal, stage: 'complete' });
        if (replacementPending) {
          verifyConfig();
          fs.unlinkSync(path.join(dir, 'image-replacement.json'));
        }
        console.log('[LexiFlow] 已按数据库成功记录恢复目标应用；未重导资料。'); return 0;
      }
      if (!state.generation || !state.inputSha256) {
        if (!confirmStopped || state.phase !== 'new') fail('DEPLOYMENT_RECOVERY_STATE_INCOMPLETE');
        acquireLock(true);
        console.log('[LexiFlow] 建库前锁已核对；没有数据库成功事实可恢复。请重新运行 install --plan。');
        return 0;
      }
      if (state.digests) verifyConfig();
      await ownedResources();
      const fact = await inspectInstalledDeployment({ repo, state, run, environment });
      if (fact.needsRebuild || fact.generationVersion !== state.generationVersion
          || fact.datasetVersion !== state.datasetIdentity?.version) {
        console.log('[LexiFlow] BLOCKED：数据库提交事实与安装状态不一致；请运行 install/upgrade --plan 后显式确认。');
        return 1;
      }
      if (confirmStopped || fs.existsSync(path.join(dir, '.lock')) || fs.existsSync(path.join(dir, '.lock-claim')))
        acquireLock(confirmStopped);
      console.log('[LexiFlow] 数据库记录与安装状态一致，无待恢复操作。'); return 0;
    }
    step = '核验安装状态';
    if (!['initialized', 'ready'].includes(state.phase) || !state.generationVersion || !state.datasetIdentity || !state.digests) fail('DEPLOYMENT_STATE_PENDING');
    verifyConfig();
    await run('podman', ['info'], { timeout: 30000 }); await ownedResources();
    if (action === 'status') {
      const fact = await inspectInstalledDeployment({ repo, state, run, environment });
      if (fact.needsRebuild || fact.generationVersion !== state.generationVersion
          || fact.datasetVersion !== state.datasetIdentity.version) fail('DEPLOYMENT_STATE_PENDING');
    }
    if (action === 'status') { console.log(await compose('ps')); await ready(); await verifyInstalledExtensionArchive(dir, state); summary(); return 0; }
    if (action === 'logs') {
      await runCommand('podman', composeArgs('logs', '--follow', '--tail=0', 'api'),
        { cwd: dir, env: environment, follow: true, label: '查看服务日志' });
      return 0;
    }
    acquireLock();
    if (action === 'stop') {
      await compose('stop', 'api');
      return 0;
    }
    const journalFile = path.join(dir, 'deployment.json');
    if (fs.existsSync(path.join(dir, 'upgrade.json')) || fs.existsSync(journalFile) && readOperationJournal(journalFile).stage !== 'complete') fail('DEPLOYMENT_PENDING_REPLAN_REQUIRED');
    const fact = await inspectInstalledDeployment({ repo, state, run, environment });
    if (fact.needsRebuild || fact.generationVersion !== state.generationVersion
        || fact.datasetVersion !== state.datasetIdentity.version) fail('DEPLOYMENT_STATE_PENDING');
    await compose('up', '-d', '--no-deps', 'api');
    await ready(); await verifyInstalledExtensionArchive(dir, state); return 0;
  }
  function loadState() {
    const file = path.join(dir, 'state.json'); regular(file);
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (value.schema !== 2 || value.root !== dir || !/^[a-f0-9]{32}$/.test(value.id)
      || value.project !== `lexiflow-local-${value.id}` || !Number.isInteger(value.apiPort) || value.apiPort < 1 || value.apiPort > 65535
      || value.databaseTarget?.kind !== 'file' || value.databaseFile !== path.join(dir, 'data', 'lexicon.db')) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
    validateSqliteDatabaseFile(value.databaseFile, { installationRoot: dir, allowMissing: value.phase === 'new' });
    return value;
  }
  try {
    if (['install', 'upgrade'].includes(action)) { step = '获取源码部署槽位'; unlockSlot = acquireDeploymentSlotLock('source'); }
    return (await main()) ?? 0;
  }
  catch (error) {
    const code = String(error?.message ?? '');
    const known = {
      DEPLOYMENT_INPUT_DRIFT: '确认后的源码、资料或制品输入与本次计划不符，拒绝继续部署。',
      DEPLOYMENT_SOURCE_DRIFT: '已缓存来源、资料摘要或生成记录不符合本次公开固定来源，拒绝部署。',
      DEPLOYMENT_DATABASE_DRIFT: '确认后的数据库对象、权限或成功记录发生变化，拒绝写入。',
      DEPLOYMENT_TARGET_CHANGED: '指定目标与已有安装记录不同，拒绝更换目标。',
      DEPLOYMENT_SLOT_BUSY: '同类型源码部署槽位正在被另一个进程使用，拒绝并发操作。',
      DEPLOYMENT_SLOT_LOCK_INVALID: '无法取得受信的本机部署槽位锁，拒绝操作。',
      CANDIDATE_SLOT_STATE_INVALID: '现有安装的类型或端口不属于固定发行验证槽位，拒绝候选安装接管。',
      DEPLOYMENT_SOURCE_MIGRATION_OWNERSHIP_UNVERIFIED: '现有源码 API 端口或容器归属无法核验，拒绝迁移。',
      DEPLOYMENT_REPLACEMENT_RECORD_INVALID: '镜像替换记录与本安装状态不符，保留现场；请核对原安装输入。',
      DEPLOYMENT_REPLACEMENT_TARGET_CHANGED: '未完成的镜像替换绑定原构建与资料输入；恢复后只能使用相同输入再次明确确认，拒绝改用其他目标。',
      DEPLOYMENT_DATA_COPY_DRIFT: '复制的资料与已校验输入不同，拒绝部署。',
      CANDIDATE_CHANGED: '候选包内容与已校验输入不同，拒绝部署。',
      CANDIDATE_TARGET_DRIFT: '候选验证目标与本次计划不同，拒绝部署。',
      CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED: '候选镜像归档不是受支持且可安全隔离标签的 Docker archive；未加载镜像或改动源码标签。',
      CANDIDATE_IMAGE_IDENTITY_MISMATCH: '候选镜像的完整 ID、构建标签或独立验证标签与已校验清单不符，拒绝启动。',
      CANDIDATE_ALIAS_OWNERSHIP_MISMATCH: '候选固定镜像标签已指向未知镜像，拒绝删除或接管。',
      CANDIDATE_ALIAS_DRIFT: '候选固定镜像标签在清理前发生变化，拒绝删除。',
      CANDIDATE_ALIAS_INSPECT_FAILED: '无法检查候选固定镜像标签，拒绝清理。',
      CANDIDATE_ALIAS_INSPECT_INVALID: '候选固定镜像标签检查结果无效，拒绝清理。',
      CANDIDATE_ALIAS_CHECK_FAILED: '无法确认候选固定镜像标签是否存在，拒绝清理。',
      CANDIDATE_ALIAS_UNTAG_FAILED: '无法仅移除候选固定镜像标签；不会删除共享镜像 ID 或源码标签。',
      VERSIONED_IMAGE_REFERENCE_MISMATCH: '源码镜像含有未知额外标签，拒绝清理，以免影响其他安装。',
      DEPLOYMENT_RECOVERY_DATABASE_DRIFT: '恢复核对的数据库对象或成功记录发生变化，拒绝继续。',
      DEPLOYMENT_RECOVERY_FACT_DRIFT: '数据库成功记录、资料或凭据与恢复目标不符，拒绝继续。',
      DEPLOYMENT_RECOVERY_CONFIG_DRIFT: '安装配置与原安装及准备的目标均不符，拒绝恢复。',
      DEPLOYMENT_RECOVERY_EXTENSION_DRIFT: '扩展制品与已核验恢复目标不符，拒绝恢复。',
      DEPLOYMENT_CONFIRMATION_REQUIRED: '交互部署需要 TTY 中确认一次；非交互请先 --plan，再使用 --confirm-plan。',
      DEPLOYMENT_CONFIRMATION_EOF: '确认输入已结束，未执行部署。',
      DEPLOYMENT_CONFIRMATION_CANCELLED: '确认已取消，未执行部署。',
      DEPLOYMENT_PLAN_CHANGED: 'DEPLOYMENT_PLAN_CHANGED：计划输入已发生变化，请重新 --plan；未执行部署。',
      DEPLOYMENT_API_PORT_OCCUPIED: 'API 端口已被非本安装服务占用，拒绝接管。',
      DEPLOYMENT_DATABASE_OUTPUT_INVALID: '数据管理命令响应无效，无法确认数据库事实。',
      DEPLOYMENT_DATA_COMMIT_UNCONFIRMED: '数据库提交结果未获确认，应用不会激活。',
      'DEPLOYMENT_DATABASE_FAIL:REQUEST_INVALID': 'SQLite 数据请求无效，未执行写入。',
      'DEPLOYMENT_DATABASE_FAIL:REQUEST_OR_DATA_INVALID': '数据库资料处理失败，未确认写入；安全响应无法确定更具体原因。',
      DEPLOYMENT_PLAN_MODE_CONFLICT: '--plan 与 --confirm-plan 不能同时使用。',
      DEPLOYMENT_NOT_INSTALLED: '尚未安装，不能 upgrade。',
      DEPLOYMENT_INSTALL_STATE_INVALID: '安装状态记录无效，拒绝操作。',
      DEPLOYMENT_PACKAGE_GENERATION_INVALID: '资料包的数据生成身份校验失败。',
      DEPLOYMENT_GENERATION_INVALID: '数据生成身份无效。',
      DEPLOYMENT_PACKAGE_GENERATION_INVALID: '候选数据包的生成记录缺失或不符合最新版协议，未执行数据写入。',
      DEPLOYMENT_SOURCE_UNAVAILABLE: '词库来源准备失败或未产生预期资料。',
      DEPLOYMENT_OPERATION_CANCELLED: '收到中断请求；部署已取消，已保留可核对的状态。',
      SOURCE_NETWORK_ENVIRONMENT_INVALID: '本机继承的来源代理环境无法安全解析，未继续下载。',
      SOURCE_NETWORK_ENVIRONMENT_CHANGED: '确认后来源连接环境已变化，拒绝使用变更后的连接输入。',
      SOURCE_BASE_IMAGE_UNAVAILABLE: '官方 Java 25 基础镜像不可用，拒绝继续构建。',
      SOURCE_BASE_IMAGE_INVALID: '本机官方基础镜像无法核验为 linux/arm64 的目标主版本。',
      SOURCE_BASE_IMAGE_CHANGED: '基础镜像引用在核验期间发生变化，拒绝继续构建。',
      EXTENSION_IDENTITY_MISMATCH: 'EXTENSION_IDENTITY_MISMATCH：扩展构建身份或 API 端口与本次计划不符；停止后续部署，请保持源码稳定后重新运行。',
      EXTENSION_API_PORT_INVALID: '扩展 API 端口不在 1–65535 范围内，未生成制品。',
      EXTENSION_ARCHIVE_METADATA_INVALID: '安装记录缺少有效扩展 ZIP 身份；请显式运行 upgrade 生成并绑定配套归档。',
      EXTENSION_ARCHIVE_INVALID: '扩展 ZIP、SHA256 sidecar 或文件身份校验失败；拒绝报告就绪。',
      EXTENSION_ARCHIVE_IDENTITY_MISMATCH: '扩展 ZIP 的构建身份或 API 端口与本次安装不符。',
      EXTENSION_ARCHIVE_TREE_MISMATCH: 'Chrome 固定扩展目录与绑定 ZIP 内容不一致。',
      EXTENSION_ARCHIVE_CONFLICT: 'buildId 归档位置已有不同内容；拒绝覆盖不可变制品。',
      ARCHIVE_FILE_SET_MISMATCH: '扩展 ZIP 文件白名单不匹配。',
      ARCHIVE_MANIFEST_INVALID: '扩展 ZIP manifest 权限、版本或入口不符合合同。',
      ARCHIVE_RESOURCE_MISSING: '扩展 ZIP 缺少 manifest、HTML 或 CSS 引用的资源。',
      ARCHIVE_INVALID: '扩展 ZIP 结构或校验和无效。',
      JAVA_25_UNAVAILABLE: '未找到 Eclipse Temurin 25；请核对仓库本地 JDK 或显式环境配置。',
      JAVA_HOME_INVALID: '指定的 Java 路径不是绝对路径，未执行部署。',
      JAVA_RELEASE_INVALID: '选定的 Java 不是可执行的 Eclipse Temurin 25 JDK，未执行部署。',
      JAVA_VERSION_UNSUPPORTED: '选定的 Java 运行版本不是正式 Java 25，未执行部署。',
      JAVA_VERSION_UNRECOGNIZED: '无法确认选定 Java 的运行版本，未执行部署。',
      JAVA_EXECUTABLE_MISSING: '找不到选定的 Java 可执行文件，未执行部署。',
      JAVA_EXECUTABLE_DENIED: '选定的 Java 可执行文件不可执行，未执行部署。',
      JAVA_PROBE_TIMEOUT: 'Java 版本检查超时，未执行部署。',
      JAVA_PROBE_OUTPUT_LIMIT: 'Java 版本检查输出超限，未执行部署。',
      JAVA_PROBE_SIGNAL: 'Java 版本检查被中断，未执行部署。',
      JAVA_PROBE_EXIT: 'Java 版本检查失败，未执行部署。',
      JAVA_PROBE_START_FAILED: '无法启动 Java 版本检查，未执行部署。',
      'DEPLOYMENT_DATABASE_BLOCKED:DATABASE_UNAVAILABLE': '无法连接或认证目标数据库；不能区分网络与账号原因。',
      'DEPLOYMENT_DATABASE_BLOCKED:FINGERPRINT_DRIFT': '确认后的数据库观测发生变化，拒绝写入。',
    };
    const commandStages = ['编译 Java 应用', '安装扩展依赖', '构建 Chrome 扩展', '构建 API 镜像', '构建词库工具镜像',
      '拉取 Java 25 基础镜像', '下载、校验并精简 ECDICT', '校验 SQLite 资料文件', '提交资料到 SQLite 文件', '检查 SQLite 数据库文件'];
    const commandStage = commandStages.find(label => code.startsWith(`${label}失败`) || code.startsWith(`${label}无法启动`));
    const javaCode = code.split('：', 1)[0];
    const message = known[code] ?? (javaCode.startsWith('JAVA_') && known[javaCode] ? `${javaCode}：${known[javaCode]}` : undefined) ?? (code === help ? `参数不受支持；${help}`
      : code.includes('收到中断请求') || code.startsWith('安装已取消') ? '收到中断请求；部署已取消，已保留可核对的状态。'
      : code.startsWith('API 就绪等待超时') ? 'API 就绪等待超时；数据若已提交将保留，不能误报部署成功。'
        : code.startsWith('API 响应协议或软件版本不匹配') ? 'API 响应协议或软件版本不匹配；数据若已提交将保留。'
      : code.startsWith('DEPLOYMENT_DATABASE_BLOCKED:') ? 'SQLite 数据库文件检查被阻断；不会自动创建或清空文件。'
        : commandStage ? `${commandStage}失败；可检查本安装运行日志、工具与运行环境。`
          : ['install', 'upgrade'].includes(action) ? '部署检查或执行失败（UNEXPECTED）；未确认的阶段不视为成功。' : code);
    console.error(`[LexiFlow] ${commandStage ?? step}失败：${message}`); return 1;
  }
  finally {
    process.removeListener('SIGINT', markInterrupted); process.removeListener('SIGTERM', markInterrupted);
    try { releaseLock(); }
    catch (error) { console.error(`[LexiFlow] 安装锁清理失败：${error.message}`); return 1; }
    finally { unlockSlot?.(); sessionBusy = false; }
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runLocal(process.argv.slice(2)).then(code => { process.exitCode = code; });
}
