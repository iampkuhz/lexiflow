// Install 与 upgrade 共用的确认计划及操作阶段记录原语。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const fail = code => { throw new Error(code); };
const sortValue = value => Array.isArray(value) ? value.map(sortValue)
  : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sortValue(value[key])])) : value;
export const canonicalJson = value => JSON.stringify(sortValue(value));
export const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

/** 生成不含凭据、时间或随机操作号的确定性部署计划。 */
export function createDeploymentPlan(input) {
  if (!input || !['install', 'upgrade'].includes(input.operation)) fail('DEPLOYMENT_PLAN_INVALID');
  if (!/^[a-f0-9]{64}$/.test(input.targetBuildId ?? '') || !/^[a-f0-9]{64}$/.test(input.generationVersion ?? '')
      || !/^[a-f0-9]{64}$/.test(input.databaseFingerprint ?? '')) fail('DEPLOYMENT_OBSERVATION_INVALID');
  if (input.takeover && input.databaseAuthority?.verified !== true) fail('DATABASE_TAKEOVER_AUTHORITY_REQUIRED');
  const target = input.databaseTarget;
  if (!target || !['managed', 'external'].includes(target.kind) || target.host !== '127.0.0.1'
      || !Number.isInteger(target.port) || target.port < 1 || target.port > 65535
      || target.database !== 'lexiflow' || target.schema !== 'lexiflow_release' || target.serverMajor !== 17
      || typeof target.user !== 'string' || !target.user || Buffer.byteLength(target.user) > 63 || /[\r\n\0]/.test(target.user)
      || target.owner !== target.user
      || !['managed', 'password', 'development-trust'].includes(target.authMode)
      || (target.kind === 'external') !== (target.authMode !== 'managed')
      || (target.authMode === 'development-trust' && (target.user !== 'postgres'
        || !/^[a-f0-9]{64}$/.test(target.developmentIdentityFingerprint ?? '')))) fail('DEPLOYMENT_DATABASE_TARGET_INVALID');
  if (!['DATABASE_ABSENT', 'OK', 'SCHEMA_MISSING', 'SCHEMA_INVALID', 'RECORD_MISSING', 'RECORD_INVALID', 'GENERATION_CHANGED'].includes(input.dataReason)) fail('DEPLOYMENT_DATA_REASON_INVALID');
  if (!['NEW_INSTALL', 'INSTALL_REFRESH', 'BUILD_CHANGED', 'START_REQUIRED', 'CURRENT'].includes(input.applicationReason)) fail('DEPLOYMENT_APPLICATION_REASON_INVALID');
  return sortValue({ schema: 1, operation: input.operation, installation: input.installation,
    target: { buildId: input.targetBuildId, generationVersion: input.generationVersion },
    database: { target, fingerprint: input.databaseFingerprint, takeover: Boolean(input.takeover), create: Boolean(input.createDatabase) },
    actions: { applicationUpdate: Boolean(input.applicationUpdate), applicationStart: Boolean(input.applicationStart),
      applicationReason: input.applicationReason, dataRebuild: Boolean(input.dataRebuild), dataReason: input.dataReason } });
}
export const deploymentPlanHash = plan => sha256(canonicalJson(plan));

/** 必须由用户明确确认当前完整计划；不把确认保存为后续运行权限。 */
export async function confirmDeploymentPlan(hash, { suppliedHash, signal } = {}) {
  if (!/^[a-f0-9]{64}$/.test(hash)) fail('DEPLOYMENT_PLAN_HASH_INVALID');
  if (signal?.aborted) fail('DEPLOYMENT_CONFIRMATION_CANCELLED');
  if (suppliedHash === undefined) fail('DEPLOYMENT_CONFIRMATION_REQUIRED');
  if (suppliedHash !== hash) fail('DEPLOYMENT_PLAN_CHANGED');
  return true;
}

/** 普通交互只在任何数据库连接或重型准备前询问一次；拒绝不生成计划。 */
export async function confirmDeploymentIntent({ port, user, schema = 'lexiflow_release', authMode = 'managed', input = process.stdin, output = process.stdout, signal } = {}) {
  if (!Number.isInteger(port) || port < 1 || port > 65535 || typeof user !== 'string' || !user
      || Buffer.byteLength(user) > 63 || /[\x00-\x1f\x7f-\x9f]/.test(user)
      || schema !== 'lexiflow_release' || !['managed', 'password', 'development-trust'].includes(authMode)
      || authMode === 'development-trust' && user !== 'postgres') fail('DEPLOYMENT_DATABASE_TARGET_INVALID');
  if (signal?.aborted) fail('DEPLOYMENT_CONFIRMATION_CANCELLED');
  if (!input.isTTY || !output.isTTY) fail('DEPLOYMENT_CONFIRMATION_REQUIRED');
  const authority = authMode === 'development-trust' ? '已识别本仓库 PostgreSQL 17 开发容器，postgres 无密码 trust；仅管理 lexiflow_release，不接管容器、卷或 public。'
    : authMode === 'password' ? '沿用本安装记录的外部目标与受限凭据；仅管理 lexiflow_release，不接管数据库服务。'
      : '使用本安装专用 PostgreSQL；仅管理 lexiflow_release。';
  output.write(`目标：127.0.0.1:${port}/lexiflow；用户 ${user}；schema ${schema}。${authority} 本次 install/upgrade 将替换同软件版本的本安装 API/托管 PostgreSQL 容器与镜像，保留数据库卷；失败后可能需要重试，不能回退已删除的同版本二进制。数据导入版本变化或完整性异常时替换资料；确认仅授权本次调用。\n继续安装并使用本机 LexiFlow 数据库？[y/N] `);
  const line = await new Promise((resolve, reject) => {
    let value = '';
    let settled = false;
    const done = (answer, error) => {
      if (settled) return;
      settled = true;
      input.removeListener('data', onData); input.removeListener('end', onEnd);
      signal?.removeEventListener('abort', onAbort);
      // 终端 stdin 在 data 模式下会维持事件循环；确认结束后必须交还句柄。
      input.pause();
      if (input === process.stdin) input._handle?.unref?.();
      error ? reject(error) : resolve(answer);
    };
    const onData = chunk => { value += chunk.toString('utf8'); const index = value.indexOf('\n'); if (index >= 0) done(value.slice(0, index).trim()); };
    const onEnd = () => done(null), onAbort = () => done(null, new Error('DEPLOYMENT_CONFIRMATION_CANCELLED'));
    input.on('data', onData); input.once('end', onEnd); signal?.addEventListener('abort', onAbort, { once: true });
  });
  if (line === null) fail('DEPLOYMENT_CONFIRMATION_EOF');
  return ['y', 'yes', '是'].includes(line.toLowerCase());
}

const stages = ['planned', 'prepared', 'application-stopped', 'data-committed', 'application-activated', 'complete'];
/** 按原子替换保存 recovery journal；它只记录阶段，不是数据库成功事实。 */
export function writeOperationJournal(file, record) {
  if (!record || record.schema !== 1 || !/^[a-f0-9]{32}$/.test(record.operation ?? '') || !stages.includes(record.stage)
      || !/^[a-f0-9]{64}$/.test(record.planHash ?? '')) fail('DEPLOYMENT_JOURNAL_INVALID');
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${crypto.randomBytes(12).toString('hex')}.next`;
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(record, null, 2) + '\n'); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(temporary, file);
}
export function readOperationJournal(file) {
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!record || record.schema !== 1 || !/^[a-f0-9]{32}$/.test(record.operation ?? '') || !stages.includes(record.stage)
      || !/^[a-f0-9]{64}$/.test(record.planHash ?? '')) fail('DEPLOYMENT_JOURNAL_INVALID');
  return record;
}

/** Install/upgrade 的共同顺序控制；数据库提交后的失败绝不回滚应用到旧构建。 */
export async function executeDeployment({ plan, confirmedHash, journalFile, callbacks, operation = crypto.randomBytes(16).toString('hex') }) {
  const planHash = deploymentPlanHash(plan);
  if (confirmedHash !== planHash) fail('DEPLOYMENT_CONFIRMATION_REQUIRED');
  const journal = { schema: 1, operation, planHash, stage: 'planned' };
  const save = stage => { journal.stage = stage; writeOperationJournal(journalFile, journal); };
  save('planned');
  const prepared = await callbacks.prepare(plan);
  save('prepared');
  await callbacks.verifyPlan(plan, prepared);
  await callbacks.stopApplication(plan);
  save('application-stopped');
  const committed = await callbacks.applyData(plan, prepared);
  if (!committed || committed.status !== 'PASS') fail('DEPLOYMENT_DATA_COMMIT_UNCONFIRMED');
  save('data-committed');
  // 从这里起仅向前恢复；即使启动/就绪失败，数据库仍为权威提交事实。
  await callbacks.activateApplication(plan, prepared, committed);
  save('application-activated');
  await callbacks.verifyApplication(plan, prepared, committed);
  save('complete');
  return { planHash, committed, prepared, operation };
}

/** 恢复以数据库的已提交 generation 为事实，不按本机 journal 猜测是否成功。 */
export async function recoverDeployment({ journalFile, callbacks }) {
  const journal = readOperationJournal(journalFile);
  const database = await callbacks.inspectDatabase();
  if (!database || database.status !== 'PASS') fail('DEPLOYMENT_RECOVERY_DATABASE_UNAVAILABLE');
  const committed = database.generationVersion === callbacks.targetGenerationVersion;
  if (committed) {
    await callbacks.activateTarget();
    await callbacks.verifyTarget();
    journal.stage = 'complete';
    writeOperationJournal(journalFile, journal);
    return { status: 'PASS', action: 'activate-committed-target' };
  }
  // 未提交：不自动恢复/清理数据，也不回切或杀进程；由显式重计划继续。
  return { status: 'BLOCKED', action: 'preserve-and-replan', stage: journal.stage };
}
