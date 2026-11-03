import { sourceToolsNetwork, sourceNetworkFingerprint } from '../environment/source-network-environment.mjs';
// install/upgrade 的唯一部署编排；plan 与执行共用实际输入和数据库观测。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readZipEntries } from '../../release/integrity/extension-archive-integrity.mjs';
import { readExtensionFiles } from '../../release/integrity/extension-archive-integrity.mjs';
import { validateExtensionArchiveEntries, assertExtensionTreeMatches, materializeExtensionTree, readStableRegularFile } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';
import { generationCodeVersion, dataGeneration } from '../../dataset/dataset-generation-identity.mjs';
import { canonicalJson, createDeploymentPlan, deploymentPlanHash, confirmDeploymentPlan, confirmDeploymentIntent, executeDeployment, readOperationJournal } from './installation-plan-records.mjs';
import { javaBuildEnvironment } from '../environment/java-build-environment.mjs';
import { installationRecord, publishRecord, verifyExtension, treeDigest } from './installation-update-transaction.mjs';
import { resolveBuildIdentity, assertBuildIdentityMatches } from '../../release/integrity/source-build-identity.mjs';
import { versionedImageReference, versionedImageLabels, removeVersionedImage } from '../images/versioned-images.mjs';
import { acquireImageSlotLock } from '../images/image-slot-lock.mjs';
import { resolveSourceBaseImage } from '../images/source-base-image.mjs';
import { sqliteJdbcUrl, validateSqliteDatabaseFile } from '../environment/local-sqlite-target.mjs';

const hex = /^[a-f0-9]{64}$/;
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fail = code => { throw new Error(code); };
const extensionArchiveRelative = (identity, apiPort) => `extension-artifacts/${identity.buildId}/${apiPort === 18081 ? 'api-18081/' : ''}lexiflow-extension-${identity.softwareVersion}.zip`;
async function verifyExtensionArchive(dir, descriptor, identity, apiPort, extensionDirectory) {
  if (!descriptor || descriptor.path !== extensionArchiveRelative(identity, apiPort) || descriptor.buildIdentity === undefined
      || !Number.isSafeInteger(descriptor.bytes)
      || descriptor.bytes < 22 || !hex.test(descriptor.sha256 ?? '')) fail('EXTENSION_ARCHIVE_METADATA_INVALID');
  try { assertBuildIdentityMatches(descriptor.buildIdentity, identity); } catch { fail('EXTENSION_ARCHIVE_METADATA_INVALID'); }
  const zip = path.join(dir, descriptor.path), sidecar = `${zip}.sha256`;
  let bytes, filename = path.basename(zip), sidecarText, files;
  try {
    bytes = readStableRegularFile(zip, 64 * 1024 * 1024); sidecarText = readStableRegularFile(sidecar, 512).toString('utf8');
    if (bytes.length !== descriptor.bytes || sha(bytes) !== descriptor.sha256
        || sidecarText !== `${descriptor.sha256}  ${filename}\n`) fail('EXTENSION_ARCHIVE_INVALID');
    files = await readExtensionFiles(zip, descriptor.sha256);
  } catch (error) { if (error.message.startsWith('EXTENSION_ARCHIVE_')) throw error; fail('EXTENSION_ARCHIVE_INVALID'); }
  validateExtensionArchiveEntries(files, { buildIdentity: identity, apiPort, filename });
  if (extensionDirectory) assertExtensionTreeMatches(extensionDirectory, files);
  return files;
}
function regular(file) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) fail('DEPLOYMENT_INPUT_INVALID');
  return stat;
}
function fileHash(file) { regular(file); return sha(fs.readFileSync(file)); }
function privateJson(file) {
  const stat = regular(file);
  if (stat.size > 64 * 1024) fail('DEPLOYMENT_METADATA_TOO_LARGE');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function tempRoot(repo) {
  const root = path.join(repo, 'tmp/phase-tools/unified-deployment-data-version');
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return fs.mkdtempSync(path.join(root, 'probe-'));
}
function writeRequest(root, request) {
  const file = path.join(root, `request-${crypto.randomBytes(8).toString('hex')}.json`);
  fs.writeFileSync(file, JSON.stringify(request) + '\n', { mode: 0o600, flag: 'wx' });
  return file;
}
function atomicJson(file, value) {
  const next = `${file}.${crypto.randomBytes(12).toString('hex')}.next`;
  fs.writeFileSync(next, JSON.stringify(value, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  fs.renameSync(next, file);
}
function atomicText(file, value) {
  const next = `${file}.${crypto.randomBytes(12).toString('hex')}.next`;
  fs.writeFileSync(next, value, { mode: 0o600, flag: 'wx' });
  fs.renameSync(next, file);
}
function databaseResponse(output) {
  if (Buffer.byteLength(output) > 4096) fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID');
  const lines = output.trim().split(/\r?\n/);
  if (lines.length !== 1) fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID');
  let value;
  try { value = JSON.parse(lines[0]); } catch { fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID'); }
  if (!value || !['PASS', 'FAIL', 'BLOCKED'].includes(value.status)) fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID');
  if (value.status !== 'PASS') {
    const reasons = new Set(['DATABASE_FILE_INVALID', 'DATABASE_CORRUPT', 'DATABASE_UNAVAILABLE', 'AUTHORIZATION_FAILED', 'FINGERPRINT_DRIFT',
      'CROSS_SCHEMA_DEPENDENCY', 'REQUEST_INVALID', 'REQUEST_OR_DATA_INVALID', 'TARGET_CHANGED', 'INPUT_CHANGED', 'SECRET_CHANGED']);
    fail(`DEPLOYMENT_DATABASE_${value.status}:${reasons.has(value.reason) ? value.reason : 'UNAVAILABLE'}`);
  }
  if (typeof value.databaseFile !== 'string' || !path.isAbsolute(value.databaseFile) || !hex.test(value.fingerprint)) fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID');
  return value;
}
export function renderCompose(repo, { apiPort, databaseFile, slot = 'source' }) {
  void repo;
  if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535 || typeof databaseFile !== 'string'
      || !path.isAbsolute(databaseFile) || path.basename(databaseFile) !== 'lexicon.db'
      || /[\r\n\0:$"\\]/.test(databaseFile) || slot !== 'source') fail('DEPLOYMENT_COMPOSE_INPUT_INVALID');
  const labels = { 'lexiflow.slot': slot, 'lexiflow.installation': '${LEXIFLOW_INSTALLATION_ID:?LEXIFLOW_INSTALLATION_ID is required}',
    'lexiflow.release': '${LEXIFLOW_RELEASE_KEY:?LEXIFLOW_RELEASE_KEY is required}' };
  const policy = { pull_policy: 'never', platform: '${LEXIFLOW_PLATFORM:?LEXIFLOW_PLATFORM is required}' };
  const hardened = { read_only: true, cap_drop: ['ALL'], security_opt: ['no-new-privileges:true'],
    tmpfs: ['/tmp:size=128m'], mem_limit: '1280m' };
  const api = { labels, image: '${LEXIFLOW_API_IMAGE:?LEXIFLOW_API_IMAGE is required}', ...policy,
    command: ['api'], logging: { driver: 'k8s-file', options: { 'max-size': '10mb' } },
    ports: [`127.0.0.1:${apiPort}:8080`],
    networks: ['published'],
    environment: { SPRING_DATASOURCE_URL: 'jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro' },
    volumes: ['${LEXIFLOW_DATABASE_FILE:?LEXIFLOW_DATABASE_FILE is required}:/var/lib/lexiflow/lexicon.db:ro'],
    ...hardened, stop_grace_period: '30s', healthcheck: {
      test: ['CMD', '/app/run-container-command.sh', 'health'], interval: '30s', timeout: '5s', retries: 3, start_period: '30s' } };
  const config = { services: { api }, networks: { published: { driver: 'bridge', labels } } };
  return `${JSON.stringify(config, null, 2)}\n`;
}
export async function packageGeneration(file) {
  try {
    const entries = await readZipEntries(file, ['manifest.json']);
    const manifest = JSON.parse(entries.get('manifest.json').toString('utf8'));
    const inputs = manifest.generationInputs, config = manifest.generationConfig;
    if (manifest.schemaVersion !== 4 || !inputs || typeof inputs !== 'object' || Array.isArray(inputs)
        || !config || typeof config !== 'object' || Array.isArray(config)) fail('DEPLOYMENT_PACKAGE_GENERATION_INVALID');
    const generation = dataGeneration({ codeVersion: manifest.generationCodeVersion, inputSha256: inputs.inputSha256,
      upstream: inputs.upstream, config });
    if (generation.generationVersion !== manifest.generationVersion || generation.sourceDigest !== manifest.generationSourceDigest)
      fail('DEPLOYMENT_PACKAGE_GENERATION_INVALID');
    return generation;
  } catch { fail('DEPLOYMENT_PACKAGE_GENERATION_INVALID'); }
}

/** 只允许 probe 目录在确认前写入；安装状态、秘密和数据库均在确认后创建。 */
export async function runUnifiedDeployment(ctx) {
  const { repo, dir, action, run } = ctx;
  const isPortTaken = ctx.portTaken ?? portTaken;
  if (ctx.planOnly && ctx.confirmPlan) fail('DEPLOYMENT_PLAN_MODE_CONFLICT');
  ctx.progress?.('读取安装记录');
  const existed = fs.existsSync(dir);
  if (action === 'upgrade' && !existed) fail('DEPLOYMENT_NOT_INSTALLED');
  let installed = null;
  let replacement = null;
  let potentialFailedPreparationReplan = false;
  let failedPreparationReplan = false;
  let sourceManagedState = false;
  if (existed) {
    installed = privateJson(path.join(dir, 'state.json'));
    if (installed.root !== dir || installed.schema !== 2 || !/^[a-f0-9]{32}$/.test(installed.id)
        || installed.project !== `lexiflow-local-${installed.id}` || !['new', 'initialized', 'ready'].includes(installed.phase)) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
    if (ctx.enforceFixedSlots && (installed.packageType !== 'source'
        || ![18080, 18081].includes(installed.apiPort))) fail('DEPLOYMENT_SOURCE_SLOT_STATE_INVALID');
    sourceManagedState = installed.packageType === 'source'
      && installed.databaseTarget?.kind === 'file' && [18080, 18081].includes(installed.apiPort);
    if (ctx.seen.has('--api-port') && ctx.apiPort !== installed.apiPort) fail('DEPLOYMENT_TARGET_CHANGED');
    if (installed.apiPort !== ctx.apiPort) fail('DEPLOYMENT_TARGET_CHANGED');
    ctx.setState(installed);
    if (installed.digests) ctx.verifyConfig();
    const replacementFile = path.join(dir, 'image-replacement.json');
    if (fs.existsSync(replacementFile)) {
      replacement = privateJson(replacementFile);
      if (replacement.schema !== 1 || replacement.root !== dir || replacement.installationId !== installed.id
          || !hex.test(replacement.stateSha256 ?? '') || !hex.test(replacement.targetBuildId ?? '')
          || !hex.test(replacement.inputSha256 ?? '') || !hex.test(replacement.generationVersion ?? '')
          || !hex.test(replacement.databaseFingerprint ?? '') || !Array.isArray(replacement.allowedEnvSha256)
          || replacement.stateSha256 !== fileHash(path.join(dir, 'state.json')))
        fail('DEPLOYMENT_REPLACEMENT_RECORD_INVALID');
    }
    if (sourceManagedState && replacement && !ctx.planOnly && !ctx.confirmPlan
        && replacement.targetBuildId !== resolveBuildIdentity(repo).buildId) {
      const journalFile = path.join(dir, 'deployment.json');
      let journal, targetRecord;
      try {
        journal = fs.existsSync(journalFile) ? readOperationJournal(journalFile) : null;
        if (journal && fs.existsSync(path.join(dir, 'prepared-target.json')))
          targetRecord = JSON.parse(fs.readFileSync(path.join(dir, 'prepared-target.json'), 'utf8'));
      } catch { journal = null; targetRecord = { invalid: true }; }
      potentialFailedPreparationReplan = journal?.stage === 'planned'
        && !targetRecord?.invalid && (!targetRecord || targetRecord.operation !== journal.operation);
    }
    await ctx.ownedResources();
  }
  const fresh = !installed || installed.phase === 'new';
  const apiPort = installed?.apiPort ?? ctx.apiPort;
  const databaseFile = validateSqliteDatabaseFile(path.join(dir, 'data', 'lexicon.db'), { installationRoot: dir });
  const target = { kind: 'file', file: databaseFile };
  const ordinaryConsent = !ctx.planOnly && !ctx.confirmPlan;
  if (ordinaryConsent) ctx.progress?.('核验部署输入');
  // 确认前只做只读快照，不连接数据库或准备制品；答是后的首次读取也不得换成新的基准。
  const consentBuildId = ordinaryConsent
    ? resolveBuildIdentity(repo).buildId : null;
  const consentNetwork = ordinaryConsent
    ? sourceNetworkFingerprint(ctx.environment ?? process.env) : null;
  if (ordinaryConsent) {
    ctx.progress?.('等待部署确认');
    const accepted = await confirmDeploymentIntent({ databaseFile, apiPort, signal: ctx.signal,
      input: ctx.confirmInput ?? process.stdin, output: ctx.confirmOutput ?? process.stdout });
    if (!accepted) { console.log('[LexiFlow] 已拒绝本次部署，未访问数据库文件或准备制品。'); return 0; }
  }
  ctx.progress?.('确认完成，开始连接目标与准备阶段');
  if (consentNetwork && sourceNetworkFingerprint(ctx.environment ?? process.env) !== consentNetwork) fail('SOURCE_NETWORK_ENVIRONMENT_CHANGED');
  if (consentBuildId && resolveBuildIdentity(repo).buildId !== consentBuildId) fail('DEPLOYMENT_INPUT_DRIFT');
  if (ordinaryConsent && installed?.digests) ctx.verifyConfig();
  // 安装目录尚未建立时先用 ignored 私有日志接住准备命令；拒绝路径绝不创建。
  const preparationLog = path.join(tempRoot(repo), 'preparation.log');
  fs.writeFileSync(preparationLog, '', { mode: 0o600, flag: 'wx' });
  ctx.setLogFile?.(preparationLog);
  let installedServices = installed ? await ctx.ownedResources() : new Set();
  const apiListener = await isPortTaken(apiPort);
  if (apiListener) {
    if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
    await ctx.verifyApiPort(installed);
  }
  const probe = tempRoot(repo);
  let imageSlotLock = null;
  let deploymentLocked = false;
  let dataset, generation, identity, jar;
  try {
    identity = resolveBuildIdentity(repo);
    const codeVersion = await generationCodeVersion(repo);
    ctx.progress?.('检查 Eclipse Temurin 25 并编译 Java 应用');
    const buildEnv = (ctx.javaEnvironment ?? javaBuildEnvironment)(ctx.environment, { repo });
    await run('./gradlew', ['--no-daemon', ':api:bootJar'], { cwd: path.join(repo, 'backend'), env: buildEnv });
    jar = path.join(repo, 'backend/product/api/build/internal/boot/api-internal.jar');
    regular(jar);
    const reusable = installed?.dataset && fs.existsSync(path.join(installed.dataset, 'stardict.csv'))
      && fs.existsSync(path.join(installed.dataset, 'source-summary.json'))
      && privateJson(path.join(installed.dataset, 'source-summary.json')).generation?.codeVersion === codeVersion;
    if (reusable) {
      dataset = path.join(installed.dataset, 'stardict.csv');
      const summary = privateJson(path.join(installed.dataset, 'source-summary.json'));
      const locked = privateJson(path.join(repo, 'ops/dataset/ecdict-source.lock.json'));
      if (fileHash(dataset) !== summary.csv_sha256 || summary.generation?.inputSha256 !== summary.csv_sha256
          || summary.generation.upstream?.commit !== locked.commit
          || summary.generation.upstream?.repository_url !== locked.repository_url
          || canonicalJson(summary.generation.upstream?.files) !== canonicalJson(locked.files)
          || summary.generation.upstream?.archive_member?.sha256 !== locked.archive_member.sha256
          || summary.generation.upstream?.archive_member?.bytes !== locked.archive_member.bytes) fail('DEPLOYMENT_SOURCE_DRIFT');
      generation = dataGeneration({ codeVersion, inputSha256: summary.csv_sha256, upstream: summary.generation.upstream });
      if (generation.generationVersion !== summary.generation.generationVersion) fail('DEPLOYMENT_SOURCE_DRIFT');
    } else {
      const toolsImage = `lexiflow-source-tools-${crypto.randomBytes(16).toString('hex')}`;
      const iidFile = path.join(probe, 'source-tools.iid');
      let toolsId;
      try {
        await run('podman', ['build', '--platform', 'linux/arm64', '--iidfile', iidFile,
          '--label', `io.lexiflow.source-tools-owner=${toolsImage}`, '-f', 'ops/podman/source-tools.Containerfile', 'ops/podman'], { cwd: repo });
        toolsId = fs.readFileSync(iidFile, 'utf8').trim();
        if (!/^sha256:[a-f0-9]{64}$/.test(toolsId)) fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
        const inspected = JSON.parse(await run('podman', ['image', 'inspect', '--format', '{{json .Labels}}', toolsId], { capture: true }));
        if (inspected?.['io.lexiflow.source-tools-owner'] !== toolsImage) fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
        const network = sourceToolsNetwork(ctx.environment ?? process.env);
        const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--memory', '2g',
          ...network.args,
          '-e', `LEXIFLOW_GENERATION_CODE_VERSION=${codeVersion}`, '-v', `${repo}/ops:/kit/ops:ro`,
          '-v', `${repo}/scripts:/kit/scripts:ro`, '-v', `${probe}:/data`, toolsId,
          '/kit/ops/dataset/prepare_ecdict_bundle.py', '/kit', '/data'], { cwd: repo, env: network.environment, capture: true, timeout: 1800000 });
        const match = [...output.matchAll(/^dataset_dir=\/data\/(ecdict-source\.[A-Za-z0-9]+)$/gm)];
        if (match.length !== 1) fail('DEPLOYMENT_SOURCE_UNAVAILABLE');
        dataset = path.join(probe, match[0][1], 'stardict.csv');
        const summary = privateJson(path.join(path.dirname(dataset), 'source-summary.json'));
        if (fileHash(dataset) !== summary.csv_sha256 || summary.generation?.codeVersion !== codeVersion) fail('DEPLOYMENT_SOURCE_DRIFT');
        generation = summary.generation;
      } finally {
        if (!toolsId && fs.existsSync(iidFile)) toolsId = fs.readFileSync(iidFile, 'utf8').trim();
        if (toolsId) {
          if (!/^sha256:[a-f0-9]{64}$/.test(toolsId)) fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
          if (ctx.cleanupOwnSourceTools) await ctx.cleanupOwnSourceTools(toolsId, toolsImage);
          else {
            const labels = JSON.parse(await run('podman', ['image', 'inspect', '--format', '{{json .Labels}}', toolsId], { capture: true }));
            if (labels?.['io.lexiflow.source-tools-owner'] !== toolsImage) fail('DEPLOYMENT_SOURCE_TOOLS_IMAGE_INVALID');
            await run('podman', ['rmi', toolsId]);
          }
        }
        try { fs.unlinkSync(iidFile); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      }
    }
    const inputSha256 = fileHash(dataset);
    if (!hex.test(generation.generationVersion) || !hex.test(generation.inputSha256)
        || generation.inputSha256 !== inputSha256) fail('DEPLOYMENT_GENERATION_INVALID');
    if (replacement && (replacement.inputSha256 !== inputSha256
        || replacement.generationVersion !== generation.generationVersion || replacement.baseVersion !== identity.baseVersion))
      fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED');
    if (replacement && replacement.targetBuildId !== identity.buildId) {
      const journalFile = path.join(dir, 'deployment.json');
      let journal;
      try { journal = fs.existsSync(journalFile) ? readOperationJournal(journalFile) : null; } catch { journal = null; }
      let prepared = null;
      if (journal?.operation && fs.existsSync(path.join(dir, 'prepared-target.json'))) {
        let targetRecord;
        try { targetRecord = JSON.parse(fs.readFileSync(path.join(dir, 'prepared-target.json'), 'utf8')); } catch { fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED'); }
        if (targetRecord.operation === journal.operation) prepared = readPreparedTarget(path.join(dir, 'prepared-target.json'), journal, dir);
      }
      failedPreparationReplan = ordinaryConsent
        && sourceManagedState && potentialFailedPreparationReplan
        && installed?.phase === 'ready' && installed.packageType === 'source'
        && installed.databaseTarget?.kind === 'file'
        && replacement.stateSha256 === fileHash(path.join(dir, 'state.json'))
        && replacement.targetBuildId !== identity.buildId
        && journal?.stage === 'planned' && !prepared;
      if (!failedPreparationReplan) fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED');
    }
    if (failedPreparationReplan) {
      // SQLite 文件将由只读 inspect 再次核验；未改变 SQLite 数据库文件或其他 owned data。
    }
    const requestBase = { inputType: 'csv', inputPath: dataset, inputSha256, generation };
    async function dataCommand(mode, { image, input = dataset, expectedFingerprint } = {}) {
      const request = writeRequest(probe, { ...requestBase, inputPath: image ? '/input' : input,
        ...(expectedFingerprint ? { expectedFingerprint } : {}) });
      const env = { ...ctx.environment, LEXIFLOW_DEPLOYMENT_JDBC_URL: sqliteJdbcUrl(databaseFile) };
      try {
        if (image) {
          // 仅映射本安装目录下的SQLite文件目录；不访问或挂载其他数据库。
          const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--user', '0:0', '--read-only',
            '--cap-drop=ALL', '--cap-add=DAC_OVERRIDE', '--security-opt=no-new-privileges',
            '-v', `${request}:/request.json:ro`, '-v', `${input}:/input:ro`,
            '-v', `${path.dirname(databaseFile)}:/db:${mode === 'apply' ? 'rw' : 'ro'}`,
            '-e', 'LEXIFLOW_DEPLOYMENT_JDBC_URL=jdbc:sqlite:/db/lexicon.db',
            '--entrypoint', 'java', image, '-jar', '/app/lexiflow-api.jar', '--deployment-data', mode,
            '--request', '/request.json', '--expected-file', '/db/lexicon.db'],
          { cwd: repo, capture: true, env, logOutput: false, timeout: mode === 'apply' ? 1800000 : 600000 });
          return output;
        }
        const output = await run('java', ['-jar', jar, '--deployment-data', mode, '--request', request, '--expected-file', databaseFile],
          { cwd: repo, capture: true, env: (ctx.javaEnvironment ?? javaBuildEnvironment)(env, { repo }), logOutput: false,
            timeout: mode === 'apply' ? 1800000 : 600000 });
        return output;
      } catch (error) {
        // Java CLI 以 1/3 返回固定 FAIL/BLOCKED JSON；保持非零语义并解码安全类别。
        if ([1, 3].includes(error.commandExitCode) && typeof error.stdout === 'string'
            && Buffer.byteLength(error.stdout) <= 4096) {
          let response;
          try { response = JSON.parse(error.stdout); } catch { /* 引擎输出不是数据协议。 */ }
          if (response?.status === (error.commandExitCode === 1 ? 'FAIL' : 'BLOCKED'))
            databaseResponse(error.stdout);
        }
        throw error;
      } finally { fs.unlinkSync(request); }
    }
    const inspect = async (image, input = dataset) => databaseResponse(await dataCommand('inspect', { image, input }));
    const missing = sha('lexiflow:sqlite:database-absent:v1');
    let observation;
    if (!fs.existsSync(databaseFile)) {
      observation = { status: 'PASS', databaseFile, fingerprint: missing, needsRebuild: true,
        reason: 'DATABASE_ABSENT', generationVersion: null, datasetVersion: null };
    } else {
      observation = await inspect();
      if (observation.databaseFile !== databaseFile) fail('DEPLOYMENT_DATABASE_FILE_MISMATCH');
    }
    if (failedPreparationReplan) {
      if (ctx.planOnly || ctx.confirmPlan || !observation || observation.fingerprint !== replacement.databaseFingerprint)
        fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED');
      // A matching data fingerprint is necessary before a new plan can be presented or applied.
    }
    // 每次 install/upgrade 都刷新并启动目标应用；资料重建仍由数据库事实决定。
    const applicationReason = fresh ? 'NEW_INSTALL' : installed.buildIdentity?.buildId !== identity.buildId ? 'BUILD_CHANGED' : 'INSTALL_REFRESH';
    const plan = createDeploymentPlan({ operation: action, installation: installed?.id ?? `new:${dir}`,
      targetBuildId: identity.buildId,
      generationVersion: generation.generationVersion, databaseFingerprint: observation.fingerprint,
      databaseTarget: target,
      createDatabase: observation.fingerprint === missing,
      applicationUpdate: true, applicationStart: true,
      applicationReason, dataRebuild: observation.needsRebuild,
      dataReason: observation.reason ?? 'OK' });
    const planHash = deploymentPlanHash(plan);
    if (ctx.planOnly) {
      console.log(JSON.stringify({ plan, planHash }));
      console.log(`[LexiFlow] SQLite 文件目标 ${databaseFile}。`);
      console.log(`[LexiFlow] 应用动作=${plan.actions.applicationReason}，资料重建=${plan.actions.dataRebuild ? '是' : '否'}（${plan.actions.dataReason}）。`);
    }
    if (ctx.planOnly) return 0;
    if (ctx.confirmPlan) await confirmDeploymentPlan(planHash, { suppliedHash: ctx.confirmPlan, signal: ctx.signal });
    if (ctx.interrupted()) fail('DEPLOYMENT_CANCELLED');
    if (await isPortTaken(apiPort)) {
      if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
      await ctx.verifyApiPort(installed);
    }
    if (resolveBuildIdentity(repo).buildId !== identity.buildId || await generationCodeVersion(repo) !== generation.codeVersion
      || fileHash(dataset) !== inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
    if (observation.fingerprint !== missing
        && (await inspect()).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
    if (failedPreparationReplan) {
      // Recheck frozen files and the original SQLite file after database observation, before archiving intent.
      if (ordinaryConsent && installed?.digests) ctx.verifyConfig();
      if (resolveBuildIdentity(repo).buildId !== identity.buildId || await generationCodeVersion(repo) !== generation.codeVersion
          || fileHash(dataset) !== inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
      if ((await inspect()).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
      const previous = path.join(dir, `image-replacement-${crypto.randomUUID()}-previous.json`);
      fs.renameSync(path.join(dir, 'image-replacement.json'), previous);
      replacement = { ...replacement, targetBuildId: identity.buildId };
      atomicJson(path.join(dir, 'image-replacement.json'), replacement);
    }
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    let current = installed;
    if (!current) {
      const id = crypto.randomBytes(16).toString('hex');
      current = { schema: 2, id, project: `lexiflow-local-${id}`, root: dir, apiPort, databaseFile,
        captionDebug: false, phase: 'new', version: identity.softwareVersion, buildIdentity: identity,
        databaseTarget: target, packageType: 'source' };
      ctx.setState(current);
    }
    if (!deploymentLocked) { ctx.acquireLock(); deploymentLocked = true; }
    if (!imageSlotLock) imageSlotLock = acquireImageSlotLock(identity.baseVersion, 'source');
    if (!installed) ctx.saveState(current);
    const logFile = path.join(dir, `operation-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.log`);
    fs.writeFileSync(logFile, '', { mode: 0o600, flag: 'wx' }); ctx.setLogFile(logFile);
    const opId = crypto.randomBytes(16).toString('hex');
    let operationArtifacts = null;
    const journalFile = path.join(dir, 'deployment.json');
    if (fs.existsSync(journalFile) && readOperationJournal(journalFile).stage !== 'complete') {
      // 重计划来自实时数据库事实；不以旧本机阶段批准复用。保留上一 journal 快照。
      fs.renameSync(journalFile, path.join(dir, `deployment-${opId}-previous.json`));
    }
    let prepared;
    const startedAt = new Date().toISOString();
    let readinessVerified = false;
    const executeOperation = () => executeDeployment({ plan, confirmedHash: planHash, journalFile, operation: opId, callbacks: {
      prepare: async () => {
        ctx.progress('准备应用与资料制品');
        if (installed && !fresh && !replacement) {
          replacement = { schema: 1, root: dir, installationId: installed.id,
            stateSha256: fileHash(path.join(dir, 'state.json')), targetBuildId: identity.buildId,
            inputSha256, generationVersion: generation.generationVersion, baseVersion: identity.baseVersion,
            databaseFingerprint: observation.fingerprint, databaseTarget: 'file',
            allowedEnvSha256: [fileHash(path.join(dir, 'release.env'))] };
          atomicJson(path.join(dir, 'image-replacement.json'), replacement);
        }
        const dataDir = path.join(dir, 'data', `ecdict-source.${inputSha256.slice(0, 16)}`);
        fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 });
        const dataFile = path.join(dataDir, 'stardict.csv');
        if (!fs.existsSync(dataFile)) fs.copyFileSync(dataset, dataFile);
        if (fileHash(dataFile) !== inputSha256) fail('DEPLOYMENT_DATA_COPY_DRIFT');
        fs.writeFileSync(path.join(dataDir, 'source-summary.json'), JSON.stringify({ upstream: generation.upstream, csv_sha256: inputSha256, generation }), { mode: 0o600 });
        let apiImage, apiJar = installed?.apiJar, extensionArchive;
        const buildEnv = (ctx.javaEnvironment ?? javaBuildEnvironment)(ctx.environment, { repo });
        await run('npm', ['ci'], { cwd: path.join(repo, 'extension'), env: buildEnv });
        await run('npm', ['run', 'build'], { cwd: path.join(repo, 'extension'), env: { ...buildEnv, LEXIFLOW_API_PORT: String(apiPort) } });
        if (resolveBuildIdentity(repo).buildId !== identity.buildId) fail('DEPLOYMENT_INPUT_DRIFT');
        const { createExtensionZip, publishExtensionArchive } = await import('../../../extension/scripts/packaging/package-extension-release.mjs');
        const packaged = await createExtensionZip({ root: path.join(repo, 'extension'), buildIdentity: identity, apiPort });
        const archiveDirectory = path.join(dir, path.dirname(extensionArchiveRelative(identity, apiPort)));
        await publishExtensionArchive(packaged.archive, `${packaged.descriptor.sha256}  ${packaged.descriptor.filename}\n`, archiveDirectory, packaged.descriptor.filename);
        extensionArchive = { path: extensionArchiveRelative(identity, apiPort), bytes: packaged.descriptor.bytes,
          sha256: packaged.descriptor.sha256, buildIdentity: identity };
        const zipPath = path.join(dir, extensionArchive.path);
        const extensionFiles = await readExtensionFiles(zipPath, extensionArchive.sha256);
        validateExtensionArchiveEntries(extensionFiles, { buildIdentity: identity, apiPort, filename: packaged.descriptor.filename });
        const fixedExtension = fresh ? path.join(dir, 'extension') : path.join(dir, `.prepared-${opId}`, 'extension');
        materializeExtensionTree(fixedExtension, extensionFiles);
        await verifyExtensionArchive(dir, extensionArchive, identity, apiPort, fixedExtension);
        const stage = path.join(dir, `.prepared-${opId}`); fs.mkdirSync(path.join(stage, 'api'), { recursive: true, mode: 0o700 });
        for (const [from, to] of [[jar, 'lexiflow-api.jar'], [path.join(repo, 'ops/docker/Dockerfile'), 'Dockerfile'],
          [path.join(repo, 'ops/docker/run-container-command.sh'), 'run-container-command.sh']]) fs.copyFileSync(from, path.join(stage, 'api', to));
        apiJar = path.join(stage, 'api/lexiflow-api.jar');
        // Resolve every source base before removing/replacing either versioned image.
        const baseId = await resolveSourceBaseImage({ run, reference: 'docker.io/library/eclipse-temurin:25-jre' });
        const tag = versionedImageReference('api', identity.baseVersion);
        await removeVersionedImage({ run, role: 'api', baseVersion: identity.baseVersion, installationId: current.id, allowReleaseValidation: true });
        await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `JAVA_RUNTIME_IMAGE=${baseId}`,
          ...ctx.imageLabels(identity), ...versionedImageLabels('api', identity.baseVersion), '-t', tag, path.join(stage, 'api')]);
        apiImage = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', tag], { capture: true });
        if (!/^(sha256:)?[a-f0-9]{64}$/.test(apiImage)) fail('DEPLOYMENT_IMAGE_INVALID');
        verifyExtension(fresh ? path.join(dir, 'extension') : path.join(dir, `.prepared-${opId}`, 'extension'), identity, apiPort);
        const composeText = renderCompose(repo, { apiPort, databaseFile, slot: 'source' });
        const envText = `LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE=${apiImage}\nLEXIFLOW_DATABASE_FILE=${databaseFile}\nLEXIFLOW_INSTALLATION_ID=${current.id}\nLEXIFLOW_RELEASE_KEY=local-${current.id}\n`;
        if (fresh) fs.writeFileSync(path.join(dir, 'compose.yaml'), composeText, { mode: 0o600 });
        // 引用本轮准备的新 API 镜像；数据库为专属持久 SQLite 文件，不随镜像替换。
        if (replacement) {
          replacement.allowedEnvSha256 = [...new Set([...replacement.allowedEnvSha256, sha(envText)])].slice(-4);
          atomicJson(path.join(dir, 'image-replacement.json'), replacement);
        }
        atomicText(path.join(dir, 'release.env'), envText);
        current = { ...current, apiPort, databaseFile, apiImage, apiJar,
          inputType: 'csv', inputSha256, generation,
          dataset: dataDir,
          version: identity.softwareVersion, buildIdentity: identity, generationVersion: generation.generationVersion,
          artifacts: { jar: fileHash(jar), apiImage, extensionArchive },
          digests: null };
        operationArtifacts = current.artifacts;
        ctx.setState(current);
        if (fresh) ctx.saveState(current);
        return { dataFile, apiImage, composeText, envText, extensionArchive,
          extensionStage: !fresh ? path.join(dir, `.prepared-${opId}`, 'extension') : null };
      },
      verifyPlan: async (_plan, preparedData) => {
        ctx.progress('复核计划、目标连接与制品');
        if (ctx.interrupted() || fileHash(dataset) !== inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
        if (resolveBuildIdentity(repo).buildId !== identity.buildId
          || await generationCodeVersion(repo) !== generation.codeVersion || fileHash(jar) !== current.artifacts.jar)
          fail('DEPLOYMENT_INPUT_DRIFT');
        if (await isPortTaken(apiPort)) {
          if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
          await ctx.verifyApiPort(installed);
        }
        if (preparedData.extensionStage) verifyExtension(preparedData.extensionStage, identity, apiPort);
        await verifyExtensionArchive(dir, preparedData.extensionArchive, identity, apiPort,
          preparedData.extensionStage ?? path.join(dir, 'extension'));
        if (observation.fingerprint !== missing
            && (await inspect(preparedData.apiImage, preparedData.dataFile)).fingerprint !== observation.fingerprint)
          fail('DEPLOYMENT_DATABASE_DRIFT');
        // 缺库时 inspect 必须保持只读且不创建；提交阶段才允许 API CLI 创建文件。
        atomicJson(path.join(dir, 'prepared-target.json'), { schema: 1, operation: opId, planHash,
          targetState: current, dataFile: preparedData.dataFile, apiImage: preparedData.apiImage,
          previousApiImage: installed?.apiImage ?? null,
          actions: plan.actions,
          extensionStage: preparedData.extensionStage ?? null,
          composeText: preparedData.composeText ?? null, envText: preparedData.envText ?? null,
          databaseFile, extensionSha256: preparedData.extensionStage
            ? treeDigest(preparedData.extensionStage) : treeDigest(path.join(dir, 'extension')),
          previousExtensionSha256: fs.existsSync(path.join(dir, 'extension')) ? treeDigest(path.join(dir, 'extension')) : null });
      },
      stopApplication: async () => {
        ctx.progress('停止本安装应用');
        if (!fresh) await ctx.compose('stop', 'api');
      },
      applyData: async (_plan, preparedData) => {
        ctx.progress('核对或提交资料');
        if (!plan.actions.dataRebuild) {
          if (observation.generationVersion !== generation.generationVersion || !Number.isSafeInteger(observation.datasetVersion))
            fail('DEPLOYMENT_DATA_SKIP_UNCONFIRMED');
          return { status: 'PASS', rebuilt: false, generationVersion: observation.generationVersion,
            datasetVersion: observation.datasetVersion };
        }
        const now = observation.fingerprint === missing ? observation
          : await inspect(preparedData.apiImage, preparedData.dataFile);
        if (observation.fingerprint !== now.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
        const response = JSON.parse((await dataCommand('apply', { image: preparedData.apiImage,
          input: preparedData.dataFile, expectedFingerprint: now.fingerprint })).trim());
        if (response.status !== 'PASS' || response.generationVersion !== generation.generationVersion || !Number.isSafeInteger(response.datasetVersion)) fail('DEPLOYMENT_DATA_COMMIT_UNCONFIRMED');
        return response;
      },
      activateApplication: async (_plan, preparedData, committed) => {
        ctx.progress('激活目标应用');
        current = { ...current, phase: 'initialized', datasetIdentity: { version: committed.datasetVersion, generationVersion: generation.generationVersion, sourceSha256: inputSha256 } };
        ctx.setState(current);
        if (!fresh) {
          fs.writeFileSync(path.join(dir, 'compose.yaml'), preparedData.composeText, { mode: 0o600 });
          fs.writeFileSync(path.join(dir, 'release.env'), preparedData.envText, { mode: 0o600 });
        }
        current.digests = ctx.digestFiles(current); ctx.saveState(current);
        if (replacement) fs.unlinkSync(path.join(dir, 'image-replacement.json'));
        await ctx.compose('config'); await ctx.compose('up', '-d', '--no-deps', '--force-recreate', 'api');
        if (preparedData.extensionStage) {
          verifyExtension(preparedData.extensionStage, identity, apiPort);
          fs.renameSync(path.join(dir, 'extension'), path.join(dir, `.previous-extension-${opId}`));
          fs.renameSync(preparedData.extensionStage, path.join(dir, 'extension'));
        }
        await verifyExtensionArchive(dir, current.artifacts.extensionArchive, identity, apiPort, path.join(dir, 'extension'));
      },
      verifyApplication: async () => {
        ctx.progress('验证应用就绪');
        await ctx.ready(current);
        await verifyExtensionArchive(dir, current.artifacts.extensionArchive, identity, apiPort, path.join(dir, 'extension'));
        readinessVerified = true;
        current.phase = 'ready';
        current.lastOperation = installationRecord({ operation: opId, kind: action, previous: installed, target: identity,
          startedAt, status: 'PASS', artifacts: current.artifacts, dataset: current.datasetIdentity });
        current.digests = ctx.digestFiles(current); ctx.saveState(current); publishRecord(dir, current.lastOperation);
      },
    } });
    try {
      await executeOperation();
    } catch (operationError) {
      // Ready verification is the success boundary; a later PASS-record persistence error must not
      // manufacture a conflicting FAIL record for the same operation.
      if (readinessVerified) throw operationError;
      try {
        const failureRecord = installationRecord({ operation: opId, kind: action, previous: installed, target: identity,
          startedAt, status: 'FAIL', artifacts: operationArtifacts, dataset: current?.datasetIdentity ?? null });
        publishRecord(dir, failureRecord);
      } catch (recordError) {
        throw new AggregateError([operationError, recordError], 'DEPLOYMENT_FAILURE_RECORD_WRITE_FAILED', { cause: operationError });
      }
      throw operationError;
    }
    console.log(`[LexiFlow] 部署完成：应用版本 softwareVersion=${identity.softwareVersion}；数据导入版本 generationVersion=${generation.generationVersion}`);
    ctx.reportReady?.();
    return 0;
  } finally {
    imageSlotLock?.();
    // 原始输入和请求仅在 ignored probe 中；不删除无法辨认的目录内容。
    if (fs.existsSync(probe) && fs.readdirSync(probe).every(name => /^(?:request-[a-f0-9]{16}\.json|ecdict-source\.[A-Za-z0-9]+|password)$/.test(name))) {
      fs.rmSync(probe, { recursive: true });
    }
  }
}

export function readPreparedTarget(file, journal, dir) {
  const value = privateJson(file);
  if (value.schema !== 1 || value.operation !== journal.operation || value.planHash !== journal.planHash
      || value.targetState?.root !== dir || !/^(?:sha256:)?[a-f0-9]{64}$/.test(value.apiImage ?? '')
      || value.targetState.apiImage !== value.apiImage || !hex.test(value.targetState.generationVersion ?? '')
      || value.previousApiImage !== null && !/^(?:sha256:)?[a-f0-9]{64}$/.test(value.previousApiImage ?? '')
      || !hex.test(value.targetState.inputSha256 ?? '') || value.targetState.generation?.generationVersion !== value.targetState.generationVersion
      || !value.dataFile?.startsWith(path.join(dir, 'data') + path.sep)
      || value.targetState.dataset !== path.dirname(value.dataFile)
      || value.databaseFile !== value.targetState.databaseFile
      || value.databaseFile !== path.join(dir, 'data', 'lexicon.db')
      || value.extensionStage && value.extensionStage !== path.join(dir, `.prepared-${journal.operation}/extension`)
      || value.actions?.applicationUpdate && value.targetState.apiJar
        && !value.targetState.apiJar.startsWith(path.join(dir, `.prepared-${journal.operation}`) + path.sep)
      || value.targetState.databaseTarget?.kind !== 'file' || !hex.test(value.extensionSha256 ?? '')) fail('DEPLOYMENT_PREPARED_TARGET_INVALID');
  return value;
}

/** 只在调用方独立核对数据库成功事实后，补齐本安装的目标制品/状态；不做导入。 */
export async function activatePreparedTarget({ dir, prepared, previous, fact, digestFiles, saveState, setState, compose, ready }) {
  const target = { ...prepared.targetState, phase: 'initialized', datasetIdentity: {
    version: fact.datasetVersion, generationVersion: prepared.targetState.generationVersion,
    sourceSha256: prepared.targetState.inputSha256 } };
  if (fact.needsRebuild || fact.generationVersion !== target.generationVersion
      || !Number.isSafeInteger(fact.datasetVersion) || fileHash(prepared.dataFile) !== target.inputSha256
      || target.databaseTarget?.kind !== 'file' || prepared.databaseFile !== target.databaseFile) fail('DEPLOYMENT_RECOVERY_FACT_DRIFT');
  const allow = (file, expected) => {
    if (!fs.existsSync(file)) return;
    const found = fileHash(file);
    if (found !== expected && found !== previous.digests?.[path.relative(dir, file)]) fail('DEPLOYMENT_RECOVERY_CONFIG_DRIFT');
  };
  for (const [name, content] of [['compose.yaml', prepared.composeText], ['release.env', prepared.envText]]) {
    if (content !== null) allow(path.join(dir, name), sha(content));
  }
  const extension = path.join(dir, 'extension');
  const activeExtension = fs.existsSync(extension) ? treeDigest(extension) : null;
  if (activeExtension !== prepared.extensionSha256) {
    if (activeExtension !== prepared.previousExtensionSha256 || !prepared.extensionStage
        || !fs.existsSync(prepared.extensionStage) || treeDigest(prepared.extensionStage) !== prepared.extensionSha256)
      fail('DEPLOYMENT_RECOVERY_EXTENSION_DRIFT');
    verifyExtension(prepared.extensionStage, target.buildIdentity, target.apiPort);
  } else verifyExtension(extension, target.buildIdentity, target.apiPort);
  await verifyExtensionArchive(dir, target.artifacts?.extensionArchive, target.buildIdentity, target.apiPort,
    activeExtension === prepared.extensionSha256 ? extension : prepared.extensionStage);
  for (const [name, content] of [['compose.yaml', prepared.composeText], ['release.env', prepared.envText]]) {
    if (content === null) continue;
    const file = path.join(dir, name), expected = sha(content);
    if (!fs.existsSync(file) || fileHash(file) !== expected) {
      const next = `${file}.${prepared.operation}.next`;
      fs.writeFileSync(next, content, { mode: 0o600 }); fs.renameSync(next, file);
    }
  }
  if (activeExtension !== prepared.extensionSha256) {
    fs.renameSync(extension, path.join(dir, `.previous-extension-${prepared.operation}`));
    fs.renameSync(prepared.extensionStage, extension);
  }
  verifyExtension(extension, target.buildIdentity, target.apiPort);
  await verifyExtensionArchive(dir, target.artifacts?.extensionArchive, target.buildIdentity, target.apiPort, extension);
  validateSqliteDatabaseFile(target.databaseFile, { installationRoot: dir, allowMissing: false });
  setState(target); target.digests = digestFiles(target); saveState(target);
  await compose('config'); await compose('up', '-d', '--no-deps', '--force-recreate', 'api');
  await ready(target);
  await verifyExtensionArchive(dir, target.artifacts?.extensionArchive, target.buildIdentity, target.apiPort, extension);
  target.phase = 'ready'; target.digests = digestFiles(target); saveState(target);
  return target;
}

export async function verifyInstalledExtensionArchive(dir, state) {
  return verifyExtensionArchive(dir, state?.artifacts?.extensionArchive, state?.buildIdentity, state?.apiPort, path.join(dir, 'extension'));
}

function portTaken(port) {
  return new Promise(resolve => {
    import('node:net').then(({ default: net }) => {
      const server = net.createServer();
      server.once('error', () => resolve(true));
      server.listen(port, '127.0.0.1', () => server.close(() => resolve(false)));
    });
  });
}

/** recover/status 使用只读 SQLite 连接核对已落盘数据库事实；绝不创建缺失文件。 */
export async function inspectInstalledDeployment({ repo, state, run, environment }) {
  if (!state || !['csv', 'package'].includes(state.inputType) || !hex.test(state.inputSha256)
      || !hex.test(state.generationVersion) || state.generation?.generationVersion !== state.generationVersion
      || state.schema !== 2 || state.databaseTarget?.kind !== 'file'
      || state.databaseFile !== path.join(state.root, 'data', 'lexicon.db')) fail('DEPLOYMENT_INSPECTION_STATE_INVALID');
  const input = path.join(state.dataset, state.inputType === 'package' ? 'dataset.zip' : 'stardict.csv');
  if (fileHash(input) !== state.inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
  const databaseFile = validateSqliteDatabaseFile(state.databaseFile, { installationRoot: state.root, allowMissing: false });
  const root = tempRoot(repo);
  try {
    const request = writeRequest(root, { inputType: state.inputType,
      inputPath: '/input', inputSha256: state.inputSha256,
      generation: state.generation });
    if (!/^(?:sha256:)?[a-f0-9]{64}$/.test(state.apiImage ?? '')) fail('DEPLOYMENT_INSPECTION_IMAGE_INVALID');
    const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--user', '0:0', '--read-only',
        '--cap-drop=ALL', '--cap-add=DAC_OVERRIDE', '--security-opt=no-new-privileges',
        '-v', `${request}:/request.json:ro`, '-v', `${input}:/input:ro`,
        '-v', `${path.dirname(databaseFile)}:/db:ro`,
        '-e', 'LEXIFLOW_DEPLOYMENT_JDBC_URL=jdbc:sqlite:/db/lexicon.db',
        '--entrypoint', 'java', state.apiImage, '-jar', '/app/lexiflow-api.jar', '--deployment-data', 'inspect',
        '--request', '/request.json', '--expected-file', '/db/lexicon.db'],
      { cwd: repo, capture: true, env: environment, logOutput: false });
    const fact = databaseResponse(output);
    if (fact.databaseFile !== '/db/lexicon.db') fail('DEPLOYMENT_INSPECTION_TARGET_MISMATCH');
    return fact;
  } finally {
    if (fs.existsSync(root) && fs.readdirSync(root).every(name => /^request-[a-f0-9]{16}\.json$/.test(name))) fs.rmSync(root, { recursive: true });
  }
}
