// 受管安装的准备/激活/恢复事务；不导入资料、不管理 PostgreSQL、不删除引擎资源。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { cleanupPreparedTree, preparationLimits } from './prepare-workspace.mjs';

export const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new Error(code); };
const clone = value => JSON.parse(JSON.stringify(value));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hex = /^[a-f0-9]{64}$/;

export const API_LOGGING = '    logging:\n      driver: k8s-file\n      options:\n        max-size: 10mb\n';
export function boundedApiLogging(compose) {
  if (compose.includes(API_LOGGING)) return compose;
  const marker = '    command: [api]\n';
  if (compose.split(marker).length !== 2) fail('UPGRADE_COMPOSE_UNSUPPORTED');
  return compose.replace(marker, marker + API_LOGGING);
}
export function privateFile(root, relative) {
  if (fs.lstatSync(root).isSymbolicLink()) fail('INSTALL_SYMLINK_REJECTED');
  if (path.isAbsolute(relative) || relative.split('/').some(part => !part || part === '..' || part === '.')) fail('UNSAFE_INSTALL_PATH');
  let cursor = root;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part);
    const info = fs.lstatSync(cursor);
    if (info.isSymbolicLink()) fail('INSTALL_SYMLINK_REJECTED');
  }
  if (!fs.lstatSync(cursor).isFile()) fail('INSTALL_FILE_REQUIRED');
  return cursor;
}
export function atomicJson(file, value) {
  atomicText(file, JSON.stringify(value, null, 2) + '\n');
}
export function atomicText(file, text) {
  const temporary = `${file}.${crypto.randomBytes(16).toString('hex')}.next`;
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, file);
}
export function treeDigest(root) {
  const entries = [];
  function visit(relative) {
    const file = path.join(root, relative), info = fs.lstatSync(file);
    if (info.isSymbolicLink()) fail('EXTENSION_SYMLINK_REJECTED');
    if (info.isDirectory()) for (const name of fs.readdirSync(file).sort()) visit(path.join(relative, name));
    else if (info.isFile()) entries.push([relative, sha256(fs.readFileSync(file))]);
    else fail('EXTENSION_FILE_REQUIRED');
  }
  visit('');
  if (!entries.length) fail('EXTENSION_EMPTY');
  return sha256(JSON.stringify(entries));
}
function readJson(root, name) {
  const file = privateFile(root, name);
  if (fs.statSync(file).size > 1024 * 1024) fail('INSTALL_RECORD_TOO_LARGE');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function exists(file) { try { fs.lstatSync(file); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } }
function ensureDirectory(directory) {
  if (!exists(directory)) fs.mkdirSync(directory, { mode: 0o700 });
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink()) fail('INSTALL_DIRECTORY_INVALID');
}
export function verifyExtension(directory, identity, port) {
  const manifest = readJson(directory, 'manifest.json');
  const embedded = readJson(directory, 'build-identity.json');
  if (!same(embedded, identity) || manifest.version_name !== identity.softwareVersion || manifest.version !== identity.chromeVersion
      || !same(manifest.host_permissions, [`http://127.0.0.1:${port}/*`])) fail('EXTENSION_IDENTITY_MISMATCH');
  return treeDigest(directory);
}
export function installationRecord({ operation, kind, previous, target, startedAt, status, artifacts = null, dataset = null }) {
  return { schema: 1, operation, kind, startedAt, completedAt: new Date().toISOString(), status,
    previousVersion: previous?.version ?? null, targetVersion: target.softwareVersion,
    sourceCommit: target.sourceCommit, buildId: target.buildId, artifacts, dataset };
}
export function publishRecord(dir, record) {
  if (!/^[a-f0-9]{32}$/.test(record.operation)) fail('INSTALL_OPERATION_INVALID');
  const root = path.join(dir, 'installation-records'); ensureDirectory(root);
  const file = path.join(root, `${record.operation}.json`);
  if (exists(file)) {
    if (!same(readJson(root, `${record.operation}.json`), record)) fail('INSTALL_RECORD_CONFLICT');
    return;
  }
  // 先完整落盘再独占发布，强制终止不能留下可被误读为成功的半条记录。
  const temporary = `${file}.${crypto.randomBytes(16).toString('hex')}.next`;
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(record, null, 2) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  try { fs.linkSync(temporary, file); }
  finally { fs.unlinkSync(temporary); }
}
function journalPath(dir) { return path.join(dir, 'upgrade.json'); }
function persistJournal(dir, journal) {
  const destination = journalPath(dir), temporary = `${destination}.next`;
  if (exists(temporary)) fail('UPGRADE_JOURNAL_TEMP_DRIFT');
  const text = JSON.stringify(journal, null, 2) + '\n';
  if (Buffer.byteLength(text) > preparationLimits.maxJournalBytes) fail('UPGRADE_WORKSPACE_LIMIT');
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, destination);
}
function workspace(dir, journal) { return path.join(dir, `.upgrade-${journal.operation}`); }
function validateJournal(dir, state, journal) {
  if (journal.schema !== 1 || journal.installation !== state.id || journal.root !== dir
      || !/^[a-f0-9]{32}$/.test(journal.operation) || !['preparing', 'switching', 'committing', 'restored'].includes(journal.phase)
      || journal.previous?.id !== state.id || journal.previous?.root !== dir || journal.previous?.project !== state.project
      || !hex.test(journal.extensionBefore) || !hex.test(journal.target?.buildId)
      || sha256(journal.envBefore) !== journal.previous.digests?.['release.env']
      || sha256(journal.composeBefore) !== journal.previous.digests?.['compose.yaml']) fail('UPGRADE_JOURNAL_INVALID');
  if (!same(state, journal.previous) && !same(state, journal.candidate)) fail('UPGRADE_STATE_DRIFT');
  if (journal.candidate && (journal.candidate.id !== state.id || journal.candidate.project !== state.project
      || journal.candidate.postgresImage !== journal.previous.postgresImage || journal.candidate.dataset !== journal.previous.dataset
      || journal.candidate.apiPort !== journal.previous.apiPort || journal.candidate.dbPort !== journal.previous.dbPort
      || journal.candidate.buildIdentity?.buildId !== journal.target.buildId)) fail('UPGRADE_CANDIDATE_INVALID');
}
// 仅删除 journal 中本次创建的目录，逐项比对内容；未知文件不删除。
function finish(dir, journal) {
  const work = workspace(dir, journal);
  if (exists(work)) {
    ensureDirectory(work);
    if (journal.preparePlan) cleanupPreparedTree(work, journal.preparePlan, { preserve: ['previous-extension'] });
    const allowed = new Set(['api', 'extension', 'previous-extension']);
    for (const name of fs.readdirSync(work)) if (!allowed.has(name)) fail('UPGRADE_WORKSPACE_DRIFT');
    for (const [name, digest] of [['extension', journal.extensionAfter], ['previous-extension', journal.extensionBefore]]) {
      const root = path.join(work, name);
      if (exists(root)) {
        if (!digest || treeDigest(root) !== digest) fail('UPGRADE_WORKSPACE_DRIFT');
        fs.rmSync(root, { recursive: true });
      }
    }
    // API 构建上下文没有持久字节计划时不得依据文件名推断归属并递归删除。
    if (exists(path.join(work, 'api'))) fail('UPGRADE_WORKSPACE_DRIFT');
    fs.rmdirSync(work);
  }
  fs.unlinkSync(journalPath(dir));
}

/** 已存在的事务必须先恢复；被打断的切换绝不能当成升级成功。 */
export async function recoverUpgrade(ctx) {
  const { dir } = ctx;
  if (exists(`${journalPath(dir)}.next`)) fail('UPGRADE_JOURNAL_TEMP_DRIFT');
  if (!exists(journalPath(dir))) return null;
  const journal = readJson(dir, 'upgrade.json');
  const state = ctx.loadState();
  validateJournal(dir, state, journal);
  const stagedExtension = path.join(workspace(dir, journal), 'extension');
  if (!journal.preparePlan && !journal.extensionAfter && exists(stagedExtension)) {
    try { journal.extensionAfter = verifyExtension(stagedExtension, journal.target, journal.previous.apiPort); } catch { /* 部分构建保留在本次工作目录。 */ }
  }

  // 非事务文件（含密码、SQL、资料）的漂移始终拒绝，不替用户修复或覆盖。
  const actual = ctx.digests(journal.previous);
  for (const [name, digest] of Object.entries(journal.previous.digests)) {
    if (!['release.env', 'compose.yaml'].includes(name) && actual[name] !== digest) fail('UPGRADE_CONFIG_DRIFT');
  }
  const envFile = privateFile(dir, 'release.env');
  const env = fs.readFileSync(envFile, 'utf8');
  if (env !== journal.envBefore && env !== journal.envAfter) fail('UPGRADE_CONFIG_DRIFT');
  const composeFile = privateFile(dir, 'compose.yaml'), compose = fs.readFileSync(composeFile, 'utf8');
  if (compose !== journal.composeBefore && compose !== journal.composeAfter) fail('UPGRADE_CONFIG_DRIFT');
  const extension = path.join(dir, 'extension'), work = workspace(dir, journal);
  if (same(state, journal.candidate) && journal.phase === 'committing') {
    if (env !== journal.envAfter || compose !== journal.composeAfter || verifyExtension(extension, journal.target, state.apiPort) !== journal.extensionAfter) fail('UPGRADE_COMMIT_DRIFT');
    await ctx.ready(state);
    publishRecord(dir, state.lastOperation); finish(dir, journal);
    return state;
  }
  const previous = path.join(work, 'previous-extension'), staged = path.join(work, 'extension');
  if (exists(previous)) {
    if (treeDigest(previous) !== journal.extensionBefore) fail('UPGRADE_BACKUP_DRIFT');
    if (exists(extension)) {
      if (treeDigest(extension) !== journal.extensionAfter || exists(staged)) fail('UPGRADE_EXTENSION_DRIFT');
      fs.renameSync(extension, staged);
    }
    fs.renameSync(previous, extension);
  } else if (!exists(extension) || treeDigest(extension) !== journal.extensionBefore) fail('UPGRADE_EXTENSION_DRIFT');
  atomicText(envFile, journal.envBefore);
  atomicText(composeFile, journal.composeBefore);
  if (journal.phase !== 'preparing' && journal.phase !== 'restored') {
    await ctx.activate(journal.previous);
    await ctx.ready(journal.previous);
  }
  ctx.saveState(journal.previous);
  journal.phase = 'restored';
  journal.failureRecord ||= installationRecord({ operation: journal.operation, kind: 'upgrade', previous: journal.previous,
    target: journal.target, startedAt: journal.startedAt, status: 'FAIL', artifacts: journal.candidate?.artifacts,
    dataset: journal.previous.datasetIdentity ?? null });
  persistJournal(dir, journal);
  publishRecord(dir, journal.failureRecord); finish(dir, journal);
  return journal.previous;
}

export async function upgradeInstallation(ctx, target) {
  const { dir } = ctx;
  if (exists(`${journalPath(dir)}.next`)) fail('UPGRADE_JOURNAL_TEMP_DRIFT');
  if (exists(journalPath(dir))) fail('UPGRADE_RECOVERY_REQUIRED');
  const previous = clone(ctx.loadState());
  if (!['initialized', 'ready'].includes(previous.phase)) fail('UPGRADE_REQUIRES_INITIALIZED_INSTALL');
  await ctx.verify(previous);
  if (!previous.buildIdentity) {
    const manifest = readJson(path.join(dir, 'extension'), 'manifest.json');
    if (manifest.version !== previous.version || !same(manifest.host_permissions, [`http://127.0.0.1:${previous.apiPort}/*`] )) fail('EXTENSION_IDENTITY_MISMATCH');
  }
  const oldExtension = previous.buildIdentity ? verifyExtension(path.join(dir, 'extension'), previous.buildIdentity, previous.apiPort) : treeDigest(path.join(dir, 'extension'));
  if (previous.artifacts?.extension && previous.artifacts.extension !== oldExtension) fail('UPGRADE_EXTENSION_DRIFT');
  if (previous.buildIdentity?.buildId === target.buildId) return { updated: false, state: previous };
  // 没有经过验证的兼容迁移合同就明确停止，绝不清库或把应用恢复当作数据库回滚。
  if (sha256(fs.readFileSync(privateFile(dir, 'infra/postgres/schema.sql'))) !== ctx.schemaDigest()) {
    publishRecord(dir, installationRecord({ operation: crypto.randomBytes(16).toString('hex'), kind: 'upgrade', previous,
      target, startedAt: new Date().toISOString(), status: 'FAIL', dataset: previous.datasetIdentity ?? null }));
    fail('UPGRADE_SCHEMA_INCOMPATIBLE：数据库结构不兼容，未切换应用，也未清库或重导');
  }
  const journal = { schema: 1, root: dir, installation: previous.id, operation: crypto.randomBytes(16).toString('hex'),
    startedAt: new Date().toISOString(), phase: 'preparing', previous, target: clone(target),
    envBefore: fs.readFileSync(privateFile(dir, 'release.env'), 'utf8'),
    composeBefore: fs.readFileSync(privateFile(dir, 'compose.yaml'), 'utf8'), extensionBefore: oldExtension };
  persistJournal(dir, journal);
  const work = workspace(dir, journal); ensureDirectory(work);
  try {
    ctx.checkCancelled();
    const prepared = await ctx.prepare(work, target, previous, plan => { journal.preparePlan = plan; persistJournal(dir, journal); });
    // 即使镜像构建在返回前失败，完整生成的扩展也可按摘要安全清理。
    if (!/^(sha256:)?[a-f0-9]{64}$/.test(prepared.apiImage)) fail('UPGRADE_IMAGE_INVALID');
    journal.extensionAfter = verifyExtension(path.join(work, 'extension'), target, previous.apiPort);
    persistJournal(dir, journal);
    ctx.checkCancelled();
    await ctx.verify(previous);
    if (treeDigest(path.join(dir, 'extension')) !== oldExtension) fail('UPGRADE_EXTENSION_DRIFT');
    const matches = journal.envBefore.match(/^LEXIFLOW_API_IMAGE=.*$/gm);
    if (!matches || matches.length !== 1 || matches[0] !== `LEXIFLOW_API_IMAGE=${previous.apiImage}`) fail('UPGRADE_ENV_INVALID');
    journal.envAfter = journal.envBefore.replace(matches[0], `LEXIFLOW_API_IMAGE=${prepared.apiImage}`);
    journal.composeAfter = boundedApiLogging(journal.composeBefore);
    journal.candidate = { ...previous, phase: 'ready', version: target.softwareVersion, buildIdentity: clone(target),
      apiImage: prepared.apiImage, datasetIdentity: previous.datasetIdentity ?? prepared.datasetIdentity ?? null, artifacts: { ...prepared.artifacts, extension: journal.extensionAfter },
      digests: { ...previous.digests, 'release.env': sha256(journal.envAfter), 'compose.yaml': sha256(journal.composeAfter) } };
    journal.phase = 'switching'; persistJournal(dir, journal);
    atomicText(path.join(dir, 'release.env'), journal.envAfter);
    atomicText(path.join(dir, 'compose.yaml'), journal.composeAfter);
    await ctx.activate(journal.candidate); await ctx.ready(journal.candidate);
    ctx.checkCancelled();
    fs.renameSync(path.join(dir, 'extension'), path.join(work, 'previous-extension'));
    fs.renameSync(path.join(work, 'extension'), path.join(dir, 'extension'));
    await ctx.verify(journal.candidate);
    if (verifyExtension(path.join(dir, 'extension'), target, previous.apiPort) !== journal.extensionAfter) fail('UPGRADE_EXTENSION_DRIFT');
    journal.candidate.lastOperation = installationRecord({ operation: journal.operation, kind: 'upgrade', previous, target,
      startedAt: journal.startedAt, status: 'PASS', artifacts: journal.candidate.artifacts, dataset: journal.candidate.datasetIdentity });
    journal.phase = 'committing'; persistJournal(dir, journal);
    ctx.saveState(journal.candidate); publishRecord(dir, journal.candidate.lastOperation);
    finish(dir, journal);
    return { updated: true, state: journal.candidate };
  } catch (error) {
    // 失败不吞掉恢复失败；保留 journal 供同一入口重试，不发布虚假成功。
    try { await ctx.restore(() => recoverUpgrade(ctx)); }
    catch { throw new Error('UPGRADE_RECOVERY_REQUIRED：保留事务与数据；原命令退出后再次运行 upgrade 恢复', { cause: error }); }
    throw error;
  }
}
