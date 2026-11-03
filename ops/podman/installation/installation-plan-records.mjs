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
  const target = input.databaseTarget;
  if (!target || target.kind !== 'file' || typeof target.file !== 'string' || !path.isAbsolute(target.file)
      || path.basename(target.file) !== 'lexicon.db' || /[\r\n\0]/.test(target.file)) fail('DEPLOYMENT_DATABASE_TARGET_INVALID');
  if (input.apiPortMigration !== undefined && (input.apiPortMigration?.from !== 18080 || input.apiPortMigration?.to !== 18081)) fail('DEPLOYMENT_PLAN_INVALID');
  if (!['DATABASE_ABSENT', 'DATABASE_CORRUPT', 'OK', 'RECORD_MISSING', 'RECORD_INVALID', 'GENERATION_CHANGED'].includes(input.dataReason)) fail('DEPLOYMENT_DATA_REASON_INVALID');
  if (!['NEW_INSTALL', 'INSTALL_REFRESH', 'BUILD_CHANGED', 'START_REQUIRED', 'CURRENT'].includes(input.applicationReason)) fail('DEPLOYMENT_APPLICATION_REASON_INVALID');
  return sortValue({ schema: 1, operation: input.operation, installation: input.installation,
    target: { buildId: input.targetBuildId, generationVersion: input.generationVersion,
      ...(input.apiPortMigration ? { apiPortMigration: input.apiPortMigration } : {}) },
    database: { target, fingerprint: input.databaseFingerprint, create: Boolean(input.createDatabase) },
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
export async function confirmDeploymentIntent({ databaseFile, apiPort, previousApiPort, input = process.stdin, output = process.stdout, signal } = {}) {
  if (typeof databaseFile !== 'string' || !path.isAbsolute(databaseFile) || path.basename(databaseFile) !== 'lexicon.db') fail('DEPLOYMENT_DATABASE_TARGET_INVALID');
  if (apiPort !== undefined && (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535)
      || previousApiPort !== undefined && (previousApiPort !== 18080 || apiPort !== 18081)) fail('DEPLOYMENT_DATABASE_TARGET_INVALID');
  if (signal?.aborted) fail('DEPLOYMENT_CONFIRMATION_CANCELLED');
  if (!input.isTTY || !output.isTTY) fail('DEPLOYMENT_CONFIRMATION_REQUIRED');
  const apiTarget = apiPort === undefined ? '' : previousApiPort === undefined
    ? `API 127.0.0.1:${apiPort}；` : `API 127.0.0.1:${previousApiPort} → 127.0.0.1:${apiPort}；`;
  output.write(`目标：${apiTarget}SQLite 文件 ${databaseFile}。本次 install/upgrade 仅管理本安装目录中的数据库文件与 API；缺失或损坏的资料文件仅按明确重建计划处理，拒绝非本安装目录的文件目标，不触及其他服务或目录。确认仅授权本次调用。\n继续安装并使用本机 LexiFlow 数据库？[y/N] `);
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
