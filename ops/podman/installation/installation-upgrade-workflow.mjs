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
import { probeExternal, inspectDevelopmentTrust } from '../environment/local-database-target.mjs';
import { versionedImageReference, versionedImageLabels, removeVersionedImage } from '../images/versioned-images.mjs';
import { acquireImageSlotLock } from '../images/image-slot-lock.mjs';
import { isolateCandidateArchive } from '../images/isolated-candidate-archive.mjs';
import { resolveSourceBaseImage } from '../images/source-base-image.mjs';

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
const identityFacts = identity => {
  if (!identity || typeof identity !== 'object' || Array.isArray(identity)) return null;
  const { fingerprint, composeSha256, ...facts } = identity;
  return facts;
};
function developmentIdentityFailure(recorded, current) {
  const facts = identityFacts(recorded);
  if (!facts || !hex.test(recorded.fingerprint ?? '')
      || recorded.composeSha256 !== undefined && !hex.test(recorded.composeSha256))
    return 'DEVELOPMENT_DATABASE_RECORD_INVALID';
  // 旧记录先按其原始完整事实核验自洽；只有核验通过才能排除不属于目标的 Compose bytes。
  const recordedFacts = recorded.composeSha256 === undefined ? facts : { ...facts, composeSha256: recorded.composeSha256 };
  if (sha(canonicalJson(recordedFacts)) !== recorded.fingerprint) return 'DEVELOPMENT_DATABASE_RECORD_INVALID';
  const actual = identityFacts(current);
  if (!actual || facts.containerId !== actual.containerId) return 'DEVELOPMENT_DATABASE_CONTAINER_CHANGED';
  if (facts.imageId !== actual.imageId || facts.imageName !== actual.imageName) return 'DEVELOPMENT_DATABASE_IMAGE_CHANGED';
  if (facts.composePath !== actual.composePath || facts.workingDirectory !== actual.workingDirectory
      || facts.composeProject !== actual.composeProject || facts.service !== actual.service) return 'DEVELOPMENT_DATABASE_REPOSITORY_CHANGED';
  if (facts.database !== actual.database || facts.authMethod !== actual.authMethod || facts.user !== actual.user) return 'DEVELOPMENT_DATABASE_AUTH_CHANGED';
  if (facts.hostIp !== actual.hostIp || facts.hostPort !== actual.hostPort || facts.containerPort !== actual.containerPort) return 'DEVELOPMENT_DATABASE_PORT_CHANGED';
  if (facts.volumeName !== actual.volumeName || facts.volumeDestination !== actual.volumeDestination) return 'DEVELOPMENT_DATABASE_VOLUME_CHANGED';
  if (canonicalJson(facts) !== canonicalJson(actual)) return 'DEVELOPMENT_DATABASE_RECORD_INVALID';
  return null;
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
function secret(file) {
  if (!path.isAbsolute(file)) fail('DEPLOYMENT_SECRET_INVALID');
  if (!fs.existsSync(file)) fail('DEPLOYMENT_SECRET_INVALID');
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) fail('DEPLOYMENT_SECRET_INVALID');
  if (stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.size > 4096 || stat.size < 1) fail('DEPLOYMENT_SECRET_INVALID');
  return file;
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
    const reasons = new Set(['POSTGRES_17_REQUIRED', 'SCHEMA_OWNER_MISMATCH', 'SCHEMA_CREATE_PERMISSION_REQUIRED',
      'DATABASE_AUTH_OR_CONNECT_FAILED', 'DATABASE_UNAVAILABLE', 'AUTHORIZATION_FAILED', 'FINGERPRINT_DRIFT',
      'CROSS_SCHEMA_DEPENDENCY', 'REQUEST_INVALID', 'REQUEST_OR_DATA_INVALID', 'TARGET_CHANGED', 'INPUT_CHANGED', 'SECRET_CHANGED']);
    fail(`DEPLOYMENT_DATABASE_${value.status}:${reasons.has(value.reason) ? value.reason : 'UNAVAILABLE'}`);
  }
  if (value.database !== 'lexiflow' || value.schema !== 'lexiflow_release' || !hex.test(value.fingerprint)) fail('DEPLOYMENT_DATABASE_OUTPUT_INVALID');
  return value;
}
export function renderCompose(repo, { apiPort, dbPort, external, dbUser, slot = 'source', authMode = external ? 'password' : 'managed' }) {
  void repo;
  if (!Number.isInteger(apiPort) || !Number.isInteger(dbPort) || apiPort < 1 || apiPort > 65535 || dbPort < 1 || dbPort > 65535
      || typeof dbUser !== 'string' || !dbUser || Buffer.byteLength(dbUser) > 63 || /[\r\n\0]/.test(dbUser)
      || !['source', 'release-validation'].includes(slot) || !['managed', 'password', 'development-trust'].includes(authMode)
      || external !== (authMode !== 'managed') || authMode === 'development-trust' && dbUser !== 'postgres') fail('DEPLOYMENT_COMPOSE_INPUT_INVALID');
  const labels = { 'lexiflow.slot': slot, 'lexiflow.installation': '${LEXIFLOW_INSTALLATION_ID:?LEXIFLOW_INSTALLATION_ID is required}',
    'lexiflow.release': '${LEXIFLOW_RELEASE_KEY:?LEXIFLOW_RELEASE_KEY is required}' };
  const policy = { pull_policy: 'never', platform: '${LEXIFLOW_PLATFORM:?LEXIFLOW_PLATFORM is required}' };
  const hardened = { read_only: true, cap_drop: ['ALL'], security_opt: ['no-new-privileges:true'],
    tmpfs: ['/tmp:size=128m'], mem_limit: '1280m' };
  const api = { labels, image: '${LEXIFLOW_API_IMAGE:?LEXIFLOW_API_IMAGE is required}', ...policy,
    command: ['api'], logging: { driver: 'k8s-file', options: { 'max-size': '10mb' } },
    ports: [`127.0.0.1:${apiPort}:8080`],
    networks: external ? ['published'] : ['private', 'published'],
    ...(!external ? { depends_on: { postgres: { condition: 'service_healthy' } } } : {
      extra_hosts: ['host.containers.internal:host-gateway'], environment: {
        LEXIFLOW_DATABASE_HOST: 'host.containers.internal', LEXIFLOW_DATABASE_PORT: String(dbPort),
        LEXIFLOW_DATABASE_USER: dbUser, LEXIFLOW_DATABASE_AUTH_MODE: authMode } }),
    ...(external && authMode === 'development-trust' ? {} : { secrets: ['app-password'] }),
    ...hardened, stop_grace_period: '30s', healthcheck: {
      test: ['CMD', '/app/run-container-command.sh', 'health'], interval: '30s', timeout: '5s', retries: 3, start_period: '30s' } };
  const config = { services: { ...(!external ? { postgres: { labels,
    image: '${LEXIFLOW_POSTGRES_IMAGE:?LEXIFLOW_POSTGRES_IMAGE is required}', ...policy,
    environment: { POSTGRES_DB: 'lexiflow', POSTGRES_USER: 'postgres', POSTGRES_PASSWORD_FILE: '/run/secrets/postgres-password' },
    secrets: ['postgres-password', 'app-password'], ports: [`127.0.0.1:${dbPort}:5432`],
    volumes: ['pgdata:/var/lib/postgresql/data'], networks: ['private', 'published'], mem_limit: '768m',
    healthcheck: { test: ['CMD-SHELL', 'pg_isready -h 127.0.0.1 -U lexiflow -d lexiflow'], interval: '10s', timeout: '5s', retries: 5, start_period: '20s' } } } : {}), api },
    secrets: { ...(!external ? { 'postgres-password': { file: './secrets/postgres-password' } } : {}),
      ...(external && authMode === 'development-trust' ? {} : { 'app-password': { file: './secrets/app-password' } }) },
    ...(!external ? { volumes: { pgdata: { labels } } } : {}),
    networks: { ...(!external ? { private: { internal: true, labels } } : {}), published: { driver: 'bridge', labels } } };
  return `${JSON.stringify(config, null, 2)}\n`;
}
export async function assertDevelopmentTargetIdentity({ repo, state, run }) {
  const target = state?.databaseTarget;
  if (target?.kind !== 'external' || (target.authMode ?? 'password') !== 'development-trust') return null;
  const found = await inspectDevelopmentTrust({ run, repo, port: target.port });
  if (!found) fail('DEVELOPMENT_DATABASE_TARGET_UNAVAILABLE');
  const identityFailure = developmentIdentityFailure(target.developmentIdentity, found);
  if (identityFailure) fail(identityFailure);
  if (target.developmentIdentityFingerprint !== target.developmentIdentity?.fingerprint
      || target.user !== 'postgres' || target.port !== 15432 || target.host !== '127.0.0.1'
      || target.database !== undefined && target.database !== 'lexiflow') fail('DEVELOPMENT_DATABASE_RECORD_INVALID');
  return found;
}
export async function packageGeneration(file) {
  try {
    const entries = await readZipEntries(file, ['manifest.json']);
    const manifest = JSON.parse(entries.get('manifest.json').toString('utf8'));
    const inputs = manifest.generationInputs, config = manifest.generationConfig;
    if (manifest.schemaVersion !== 2 || !inputs || typeof inputs !== 'object' || Array.isArray(inputs)
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
  const { repo, dir, action, candidateRequest, run } = ctx;
  const isPortTaken = ctx.portTaken ?? portTaken;
  let candidate = ctx.candidate;
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
    if (installed.root !== dir || installed.schema !== 1 || !/^[a-f0-9]{32}$/.test(installed.id)
        || installed.project !== `lexiflow-local-${installed.id}` || !['new', 'initialized', 'ready'].includes(installed.phase)) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
    if (ctx.enforceFixedSlots && !candidate && candidateRequest === undefined && (installed.packageType !== 'source'
        || installed.dbPort !== 15432 || ![18080, 18081].includes(installed.apiPort))) fail('DEPLOYMENT_SOURCE_SLOT_STATE_INVALID');
    if (ctx.enforceFixedSlots && candidateRequest !== undefined && (installed.packageType !== 'release-package'
        || installed.apiPort !== 18080 || installed.dbPort !== 15433)) fail('CANDIDATE_SLOT_STATE_INVALID');
    sourceManagedState = !candidate && candidateRequest === undefined && installed.packageType === 'source'
      && installed.databaseTarget?.kind === 'managed' && installed.dbPort === 15432 && [18080, 18081].includes(installed.apiPort);
    const sourceMigration = !candidate && candidateRequest === undefined && installed.packageType === 'source'
      && installed.apiPort === 18080 && installed.dbPort === 15432 && ctx.apiPort === 18081;
    if (ctx.seen.has('--api-port') && ctx.apiPort !== installed.apiPort && !sourceMigration
        || ctx.seen.has('--db-port') && ctx.dbPort !== installed.dbPort) fail('DEPLOYMENT_TARGET_CHANGED');
    if (installed.apiPort !== ctx.apiPort && !sourceMigration) fail('DEPLOYMENT_TARGET_CHANGED');
    if (sourceMigration) {
      if (await (ctx.portTaken ?? portTaken)(ctx.apiPort)) fail('DEPLOYMENT_API_PORT_OCCUPIED');
    }
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
    if (sourceManagedState && replacement && !ctx.planOnly && !ctx.confirmPlan && !candidate && candidateRequest === undefined
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
    if (sourceMigration && !potentialFailedPreparationReplan) await ctx.verifyLegacyApi(installed);
    await ctx.ownedResources();
  }
  const fresh = !installed || installed.phase === 'new';
  const sourceMigration = Boolean(installed && !candidate && candidateRequest === undefined && installed.packageType === 'source'
    && installed.apiPort === 18080 && installed.dbPort === 15432 && ctx.apiPort === 18081);
  const apiPort = sourceMigration ? ctx.apiPort : installed?.apiPort ?? ctx.apiPort, dbPort = installed?.dbPort ?? ctx.dbPort;
  if (apiPort === dbPort) fail('DEPLOYMENT_PORT_COLLISION');
  const recordedKind = installed?.databaseTarget?.kind;
  const recordedAuthMode = installed?.databaseTarget?.authMode ?? (recordedKind === 'external' ? 'password' : 'managed');
  if (!['managed', 'password', 'development-trust'].includes(recordedAuthMode)
      || installed && (recordedKind === 'external') !== (recordedAuthMode !== 'managed')) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
  ctx.progress?.('核验数据库目标');
  const dbListenerBeforeConsent = await isPortTaken(dbPort);
  let developmentIdentity = null;
  if (!installed && dbListenerBeforeConsent && !candidate && candidateRequest === undefined)
    developmentIdentity = await inspectDevelopmentTrust({ run, repo, port: dbPort });
  else if (recordedAuthMode === 'development-trust') {
    developmentIdentity = await inspectDevelopmentTrust({ run, repo, port: dbPort });
    if (!developmentIdentity) fail('DEVELOPMENT_DATABASE_TARGET_UNAVAILABLE');
    const identityFailure = developmentIdentityFailure(installed.databaseTarget.developmentIdentity, developmentIdentity);
    if (identityFailure) fail(identityFailure);
    if (installed.databaseTarget.developmentIdentityFingerprint !== installed.databaseTarget.developmentIdentity?.fingerprint
        || installed.databaseTarget.user !== 'postgres' || installed.databaseTarget.port !== dbPort
        || installed.databaseTarget.host !== '127.0.0.1') fail('DEVELOPMENT_DATABASE_RECORD_INVALID');
  }
  const dbUser = installed?.databaseTarget?.user ?? (developmentIdentity ? 'postgres' : 'lexiflow');
  if (!installed && dbListenerBeforeConsent && !developmentIdentity) fail('DEPLOYMENT_DATABASE_CREDENTIAL_REQUIRED');
  const assertDevelopmentIdentity = async expected => {
    const current = await inspectDevelopmentTrust({ run, repo, port: dbPort });
    if (!current) fail('DEVELOPMENT_DATABASE_TARGET_UNAVAILABLE');
    const failure = developmentIdentityFailure(expected, current);
    if (failure) fail(failure);
    return current;
  };
  const ordinaryConsent = !ctx.planOnly && !ctx.confirmPlan;
  if (ordinaryConsent) ctx.progress?.('核验部署输入');
  // 确认前只做只读快照，不连接数据库或准备制品；答是后的首次读取也不得换成新的基准。
  const consentBuildId = ordinaryConsent && !candidate && candidateRequest === undefined
    ? resolveBuildIdentity(repo).buildId : null;
  const consentNetwork = ordinaryConsent && !candidate && candidateRequest === undefined
    ? sourceNetworkFingerprint(ctx.environment ?? process.env) : null;
  const consentSecret = path.join(dir, 'secrets/app-password');
  const consentSecretDigest = ordinaryConsent && installed && recordedAuthMode !== 'development-trust'
    && (installed.phase !== 'new' || fs.existsSync(consentSecret)) ? fileHash(secret(consentSecret)) : null;
  if (ordinaryConsent) {
    ctx.progress?.('等待部署确认');
    const accepted = await confirmDeploymentIntent({ port: dbPort, user: dbUser, apiPort, previousApiPort: sourceMigration ? 18080 : undefined, signal: ctx.signal,
      authMode: installed ? recordedAuthMode : developmentIdentity ? 'development-trust' : 'managed',
      input: ctx.confirmInput ?? process.stdin, output: ctx.confirmOutput ?? process.stdout });
    if (!accepted) { console.log('[LexiFlow] 已拒绝本次部署，未连接数据库或准备制品。'); return 0; }
  }
  ctx.progress?.('确认完成，开始连接目标与准备阶段');
  if (consentNetwork && sourceNetworkFingerprint(ctx.environment ?? process.env) !== consentNetwork) fail('SOURCE_NETWORK_ENVIRONMENT_CHANGED');
  if (consentBuildId && resolveBuildIdentity(repo).buildId !== consentBuildId) fail('DEPLOYMENT_INPUT_DRIFT');
  if (consentSecretDigest && fileHash(consentSecret) !== consentSecretDigest) fail('DEPLOYMENT_SECRET_DRIFT');
  if (ordinaryConsent && installed?.digests) ctx.verifyConfig();
  if (ordinaryConsent && developmentIdentity) developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
  if (sourceMigration && !potentialFailedPreparationReplan) await ctx.verifyLegacyApi(installed);
  // 安装目录尚未建立时先用 ignored 私有日志接住准备命令；拒绝路径绝不创建。
  const preparationLog = path.join(tempRoot(repo), 'preparation.log');
  fs.writeFileSync(preparationLog, '', { mode: 0o600, flag: 'wx' });
  ctx.setLogFile?.(preparationLog);
  if (!candidate && candidateRequest !== undefined) candidate = await ctx.candidateAdapter.readCandidate(candidateRequest);
  if (candidate && installed && installed.packageType !== 'release-package') fail('DEPLOYMENT_TARGET_CHANGED');
  let installedServices = installed ? await ctx.ownedResources() : new Set();
  const apiListener = await isPortTaken(apiPort);
  if (apiListener) {
    if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
    await ctx.verifyApiPort(installed);
  }
  if (candidate && (apiPort !== 18080 || installed?.databaseTarget?.kind === 'external')) fail('CANDIDATE_MANAGED_DATABASE_ONLY');
  let dbListener = await isPortTaken(dbPort);
  const external = installed?.databaseTarget?.kind === 'external' || !installed && Boolean(developmentIdentity);
  if (!installed && dbListener && !developmentIdentity) fail('DEPLOYMENT_DATABASE_CREDENTIAL_REQUIRED');
  if (!installed && developmentIdentity && !dbListener) fail('DEVELOPMENT_DATABASE_TARGET_UNAVAILABLE');
  const authMode = installed ? recordedAuthMode : external ? 'development-trust' : 'managed';
  if (candidate && external) fail('CANDIDATE_MANAGED_DATABASE_ONLY');
  if (installed?.databaseTarget?.kind === 'managed' && dbListener && !installedServices.has('postgres')) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED');
  const managedSecret = path.join(dir, 'secrets/app-password');
  if (authMode === 'development-trust' && fs.existsSync(managedSecret)) fail('DEPLOYMENT_INSTALL_STATE_INVALID');
  if (authMode === 'password' && (!installed?.databaseTarget?.user || !fs.existsSync(managedSecret))) fail('DEPLOYMENT_DATABASE_CREDENTIAL_REQUIRED');
  const probe = tempRoot(repo);
  let releaseImageSlot = null;
  let deploymentLocked = false;
  let dataset, generation, identity, jar;
  try {
    const passwordFile = authMode === 'development-trust' ? undefined
      : external || !fresh || fs.existsSync(managedSecret) ? secret(managedSecret) : null;
    const passwordDigest = passwordFile ? fileHash(passwordFile) : null;
    identity = candidate?.buildIdentity ?? resolveBuildIdentity(repo);
    if (candidate) {
      dataset = path.join(candidate.root, 'payload', ...candidate.artifacts.dataset.path.split('/'));
      if (fileHash(dataset) !== candidate.artifacts.dataset.sha256) fail('CANDIDATE_CHANGED');
      ctx.progress?.('核验候选资料生成记录');
      generation = await packageGeneration(dataset);
      // Planning needs the candidate API for read-only DB inspection. Load by the verified digest only;
      // do not retag or remove a stable deployment slot before confirmation.
      const api = candidate.artifacts['api-image:linux/arm64'];
      const archive = path.join(candidate.root, 'payload', ...api.path.split('/'));
      const isolated = isolateCandidateArchive({ archive, output: path.join(probe, 'candidate-api.tar'),
        expectedSha256: api.sha256, expectedBytes: api.bytes, imageDigest: api.imageDigest,
        role: 'api', baseVersion: identity.baseVersion });
      try { await run('podman', ['load', '-i', isolated.path], { cwd: repo }); }
      finally { fs.unlinkSync(isolated.path); }
      const inspected = JSON.parse(await run('podman', ['image', 'inspect', '--format', 'json', api.imageDigest], { capture: true }));
      if (!Array.isArray(inspected) || inspected.length !== 1) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
      const data = inspected[0], labels = data.Labels ?? data.Config?.Labels ?? {};
      if (String(data.Id ?? '').replace(/^sha256:/, '') !== api.imageDigest.slice(7)
          || data.Os !== 'linux' || data.Architecture !== 'arm64'
          || labels['org.opencontainers.image.version'] !== identity.softwareVersion
          || labels['org.opencontainers.image.revision'] !== identity.sourceCommit
          || labels['io.lexiflow.build-id'] !== identity.buildId
          || labels['io.lexiflow.source-sha256'] !== identity.sourceSha256
          || labels['io.lexiflow.image-role'] !== 'api'
          || labels['io.lexiflow.deployment-version'] !== identity.baseVersion.toLowerCase()) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
      jar = null;
    } else {
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
    }
    const inputSha256 = fileHash(dataset);
    if (!hex.test(generation.generationVersion) || !hex.test(generation.inputSha256)
        || !candidate && generation.inputSha256 !== inputSha256) fail('DEPLOYMENT_GENERATION_INVALID');
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
      failedPreparationReplan = ordinaryConsent && !candidate && candidateRequest === undefined
        && sourceManagedState && potentialFailedPreparationReplan
        && installed?.phase === 'ready' && installed.packageType === 'source'
        && installed.databaseTarget?.kind === 'managed'
        && replacement.stateSha256 === fileHash(path.join(dir, 'state.json'))
        && replacement.targetBuildId !== identity.buildId
        && journal?.stage === 'planned' && !prepared;
      if (!failedPreparationReplan) fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED');
    }
    if (failedPreparationReplan) {
      // Verify the owned, running PG and API migration absence before the first database connection.
      await ctx.verifyLegacyApi(installed, { allowMissingApi: true, targetApiPort: ctx.apiPort });
    }
    const target = installed ? { ...installed.databaseTarget, authMode,
      ...(developmentIdentity ? { developmentIdentity, developmentIdentityFingerprint: developmentIdentity.fingerprint } : {}) }
      : { kind: external ? 'external' : 'managed', authMode,
        ...(developmentIdentity ? { developmentIdentity, developmentIdentityFingerprint: developmentIdentity.fingerprint } : {}),
        host: '127.0.0.1', port: dbPort, user: dbUser };
    const requestBase = { inputType: candidate ? 'package' : 'csv', inputPath: dataset, inputSha256, generation };
    async function dataCommand(mode, { password = passwordFile, image, input = dataset, expectedFingerprint } = {}) {
      if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
      const request = writeRequest(probe, { ...requestBase, inputPath: image ? '/input' : input,
        ...(expectedFingerprint ? { expectedFingerprint } : {}) });
      const env = { ...ctx.environment, LEXIFLOW_DEPLOYMENT_JDBC_URL: `jdbc:postgresql://127.0.0.1:${dbPort}/lexiflow?currentSchema=lexiflow_release`,
        LEXIFLOW_DEPLOYMENT_DB_USER: dbUser };
      if (authMode === 'development-trust') {
        delete env.LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE; delete env.LEXIFLOW_DEPLOYMENT_DB_PASSWORD;
        env.LEXIFLOW_DEPLOYMENT_DB_PASSWORD = '';
      } else env.LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE = password;
      try {
        if (image) {
          // root 只在一次性 CLI 容器中读取 owner-only bind mounts；需 DAC_OVERRIDE，否则真实 Podman VM 中 0600 JAR/secret 无法读取。
          const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--user', '0:0', '--read-only',
            '--cap-drop=ALL', '--cap-add=DAC_OVERRIDE', '--security-opt=no-new-privileges', '--add-host', 'host.containers.internal:host-gateway',
            '-v', `${request}:/request.json:ro`, '-v', `${input}:/input:ro`,
            ...(authMode === 'development-trust' ? [] : ['-v', `${password}:/secret:ro`]),
            '-e', `LEXIFLOW_DEPLOYMENT_JDBC_URL=jdbc:postgresql://host.containers.internal:${dbPort}/lexiflow?currentSchema=lexiflow_release`,
            '-e', `LEXIFLOW_DEPLOYMENT_DB_USER=${dbUser}`,
            ...(authMode === 'development-trust' ? ['-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD=', '-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE=']
              : ['-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE=/secret']),
            '--entrypoint', 'java', image, '-jar', '/app/lexiflow-api.jar', '--deployment-data', mode, '--request', '/request.json'],
          { cwd: repo, capture: true, env, logOutput: false, timeout: mode === 'apply' ? 1800000 : 600000 });
          if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
          return output;
        }
        const output = await run('java', ['-jar', jar, '--deployment-data', mode, '--request', request],
          { cwd: repo, capture: true, env: (ctx.javaEnvironment ?? javaBuildEnvironment)(env, { repo }), logOutput: false,
            timeout: mode === 'apply' ? 1800000 : 600000 });
        if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
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
    const inspect = async (password = passwordFile, image = candidate?.artifacts['api-image:linux/arm64'].imageDigest,
      input = dataset) => databaseResponse(await dataCommand('inspect', { password, image, input }));
    const missing = sha('lexiflow:managed:database-absent:v1');
    if (replacement && !external && !(await isPortTaken(dbPort))) {
      // 已确认的前次替换可能在移除 PG 镜像后中断。plan 不改变已有资源；
      // 交互确认后仅按冻结目标重建本安装 PG，随后必须重新读取真实数据库事实。
      if (ctx.planOnly || ctx.confirmPlan) fail('DEPLOYMENT_REPLACEMENT_RECOVERY_REQUIRES_CONFIRMED_INSTALL');
      if (replacement.databaseFingerprint === missing || replacement.databaseTarget !== 'managed')
        fail('DEPLOYMENT_REPLACEMENT_RECORD_INVALID');
      ctx.acquireLock(); deploymentLocked = true;
      releaseImageSlot = acquireImageSlotLock(identity.baseVersion, candidate ? 'release-validation' : 'source');
      let pgImage;
      if (candidate) {
        const loaded = await ctx.candidateAdapter.installCandidateArtifacts(candidate, { run, installDir: path.join(probe, 'replacement'), apiPort,
          includeApi: false, includePostgres: true, baseVersion: identity.baseVersion, installationId: installed.id });
        pgImage = loaded.postgresImage;
      } else {
        const pgId = await resolveSourceBaseImage({ run, reference: 'docker.io/library/postgres:17-bookworm' });
        await removeVersionedImage({ run, role: 'postgres', baseVersion: identity.baseVersion, installationId: installed.id, allowReleaseValidation: true });
        const stage = path.join(probe, 'postgres'); fs.mkdirSync(stage, { recursive: true });
        for (const name of ['Dockerfile.postgres', 'initialize-postgres-app-role.sh']) fs.copyFileSync(path.join(repo, 'ops/docker', name), path.join(stage, name));
        const pgTag = versionedImageReference('postgres', identity.baseVersion);
        await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `POSTGRES_RUNTIME_IMAGE=${pgId}`,
          ...versionedImageLabels('postgres', identity.baseVersion), '-f', path.join(stage, 'Dockerfile.postgres'), '-t', pgTag, stage]);
        pgImage = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', pgTag], { capture: true });
      }
      if (!/^(sha256:)?[a-f0-9]{64}$/.test(pgImage)) fail('DEPLOYMENT_IMAGE_INVALID');
      const envFile = path.join(dir, 'release.env');
      const oldEnv = fs.readFileSync(envFile, 'utf8');
      if ([...oldEnv.matchAll(/^LEXIFLOW_POSTGRES_IMAGE=.*$/gm)].length !== 1) fail('DEPLOYMENT_REPLACEMENT_RECORD_INVALID');
      const newEnv = oldEnv.replace(/^LEXIFLOW_POSTGRES_IMAGE=.*$/m, `LEXIFLOW_POSTGRES_IMAGE=${pgImage}`);
      replacement.allowedEnvSha256 = [...new Set([...replacement.allowedEnvSha256, sha(newEnv)])].slice(-4);
      atomicJson(path.join(dir, 'image-replacement.json'), replacement);
      atomicText(envFile, newEnv);
      await ctx.compose('config');
      await ctx.compose('up', '-d', '--force-recreate', 'postgres');
      await ctx.waitManagedPostgres({ ...installed, postgresImage: newEnv.match(/^LEXIFLOW_POSTGRES_IMAGE=(.+)$/m)?.[1] });
      const recovered = await inspect(path.join(dir, 'secrets/app-password'), undefined, dataset);
      // Recovery changed the live resource set; never authorize from its pre-recovery snapshot.
      installedServices = await ctx.ownedResources();
      dbListener = await isPortTaken(dbPort);
      if (!dbListener || !installedServices.has('postgres')) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED');
      if (recovered.fingerprint !== replacement.databaseFingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
    }
    let observation, externalProbe;
    if (external) {
      const postgresImage = installed?.postgresImage ?? 'docker.io/library/postgres:17-bookworm';
      try {
        const found = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', postgresImage],
          { cwd: repo, capture: true, logOutput: false });
        if (!/^(sha256:)?[a-f0-9]{64}$/.test(found)) fail('DEPLOYMENT_POSTGRES_TOOL_IMAGE_INVALID');
      } catch (error) {
        if (postgresImage !== 'docker.io/library/postgres:17-bookworm') fail('DEPLOYMENT_POSTGRES_TOOL_IMAGE_MISSING');
        await run('podman', ['pull', '--platform', 'linux/arm64', postgresImage], { cwd: repo });
        const found = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', postgresImage],
          { cwd: repo, capture: true, logOutput: false });
        if (!/^(sha256:)?[a-f0-9]{64}$/.test(found)) fail('DEPLOYMENT_POSTGRES_TOOL_IMAGE_INVALID');
      }
      externalProbe = await probeExternal({ run, port: dbPort, user: dbUser, passwordFile, authMode, developmentIdentity, postgresImage });
      observation = await inspect();
    } else if (fresh) {
      const services = installed ? await ctx.ownedResources() : new Set();
      if (services.has('postgres')) observation = await inspect();
      else {
        if (await isPortTaken(dbPort)) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED');
        observation = { status: 'PASS', fingerprint: missing, needsRebuild: true, reason: 'DATABASE_ABSENT', generationVersion: null, datasetVersion: null };
      }
    } else {
      if (!dbListener && !candidate && candidateRequest === undefined && installed?.packageType === 'source'
          && installed.databaseTarget?.kind === 'managed') {
        if (ctx.planOnly || ctx.confirmPlan) fail('DEPLOYMENT_MANAGED_DATABASE_STOPPED');
        if (!installedServices.has('postgres')) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED');
        await ctx.verifyStoppedManagedPostgres(installed);
        if (!deploymentLocked) { ctx.acquireLock(); deploymentLocked = true; }
        await ctx.verifyStoppedManagedPostgres(installed);
        await ctx.compose('up', '-d', 'postgres');
        await ctx.waitManagedPostgres(installed);
      }
      observation = await inspect();
    }
    if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
    if (failedPreparationReplan) {
      if (ctx.planOnly || ctx.confirmPlan || !observation || observation.fingerprint !== replacement.databaseFingerprint)
        fail('DEPLOYMENT_REPLACEMENT_TARGET_CHANGED');
      // A matching data fingerprint is necessary before a new plan can be presented or applied.
    }
    // 每次 install/upgrade 都刷新并启动目标应用；资料重建仍由数据库事实决定。
    const applicationReason = fresh ? 'NEW_INSTALL' : installed.buildIdentity?.buildId !== identity.buildId ? 'BUILD_CHANGED' : 'INSTALL_REFRESH';
    const plan = createDeploymentPlan({ operation: action, installation: installed?.id ?? `new:${dir}`,
      apiPortMigration: sourceMigration ? { from: 18080, to: 18081 } : undefined, targetBuildId: identity.buildId,
      generationVersion: generation.generationVersion, databaseFingerprint: observation.fingerprint,
      databaseTarget: { kind: external ? 'external' : 'managed', authMode,
        developmentIdentityFingerprint: developmentIdentity?.fingerprint ?? installed?.databaseTarget?.developmentIdentityFingerprint ?? null,
        host: '127.0.0.1', port: dbPort, database: 'lexiflow', schema: 'lexiflow_release', user: dbUser, owner: dbUser, serverMajor: 17 },
      takeover: external, databaseAuthority: external ? { verified: true } : undefined,
      createDatabase: !external && observation.fingerprint === missing,
      applicationUpdate: true, applicationStart: true,
      applicationReason, dataRebuild: observation.needsRebuild,
      dataReason: observation.reason ?? 'OK' });
    const planHash = deploymentPlanHash(plan);
    if (ctx.planOnly) {
      console.log(JSON.stringify({ plan, planHash }));
      console.log(`[LexiFlow] ${external ? '接入经认证的外部' : '使用本安装专用'} PostgreSQL 17 目标 127.0.0.1:${dbPort}/lexiflow，schema=lexiflow_release，owner=${dbUser}。`);
      console.log(`[LexiFlow] 应用动作=${plan.actions.applicationReason}，资料重建=${plan.actions.dataRebuild ? '是' : '否'}（${plan.actions.dataReason}）。外部数据库不由本安装管理。`);
    }
    if (ctx.planOnly) return 0;
    if (ctx.confirmPlan) await confirmDeploymentPlan(planHash, { suppliedHash: ctx.confirmPlan, signal: ctx.signal });
    if (ctx.interrupted()) fail('DEPLOYMENT_CANCELLED');
    if (await isPortTaken(apiPort)) {
      if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
      await ctx.verifyApiPort(installed);
    }
    if ((candidate && (await ctx.candidateAdapter.readCandidate(candidateRequest)).candidateSha256 !== candidate.candidateSha256)
      || (!candidate && (resolveBuildIdentity(repo).buildId !== identity.buildId || await generationCodeVersion(repo) !== generation.codeVersion))
      || fileHash(dataset) !== inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
    if (external) {
      if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
      const updated = await probeExternal({ run, port: dbPort, user: dbUser, passwordFile, authMode, developmentIdentity,
        postgresImage: installed?.postgresImage ?? 'docker.io/library/postgres:17-bookworm' });
      if (updated.fingerprint !== externalProbe.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
      if ((await inspect()).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
    } else if (observation.fingerprint === missing) { if (await isPortTaken(dbPort)) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED'); }
    else if ((await inspect()).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
    if (failedPreparationReplan) {
      // Recheck frozen files and the original managed resources after database observation, before archiving intent.
      await ctx.verifyLegacyApi(installed, { allowMissingApi: true, targetApiPort: apiPort });
      if (ordinaryConsent && installed?.digests) ctx.verifyConfig();
      if (consentSecretDigest && fileHash(consentSecret) !== consentSecretDigest) fail('DEPLOYMENT_SECRET_DRIFT');
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
      current = { schema: 1, id, project: `lexiflow-local-${id}`, root: dir, apiPort, dbPort,
        captionDebug: false, phase: 'new', version: identity.softwareVersion, buildIdentity: identity,
        databaseTarget: target, packageType: candidate ? 'release-package' : 'source' };
      ctx.setState(current);
    }
    if (!deploymentLocked) { ctx.acquireLock(); deploymentLocked = true; }
    if (!releaseImageSlot) releaseImageSlot = acquireImageSlotLock(identity.baseVersion, candidate ? 'release-validation' : 'source');
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
            databaseFingerprint: observation.fingerprint, databaseTarget: external ? 'external' : 'managed',
            allowedEnvSha256: [fileHash(path.join(dir, 'release.env'))] };
          atomicJson(path.join(dir, 'image-replacement.json'), replacement);
        }
        // 删除仅限当前 software-version 槽位；helper 在任何变更前完成完整归属预检。
        const secretDir = path.join(dir, 'secrets'); fs.mkdirSync(secretDir, { recursive: true, mode: 0o700 });
        for (const name of authMode === 'development-trust' ? [] : external ? ['app-password'] : ['postgres-password', 'app-password']) {
          const file = path.join(secretDir, name);
          if (!fs.existsSync(file)) fs.writeFileSync(file, crypto.randomBytes(32).toString('hex') + '\n', { mode: 0o600, flag: 'wx' });
          secret(file);
        }
        const dataDir = path.join(dir, 'data', candidate ? `release-package.${inputSha256.slice(0, 16)}` : `ecdict-source.${inputSha256.slice(0, 16)}`);
        fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 });
        const dataFile = path.join(dataDir, candidate ? 'dataset.zip' : 'stardict.csv');
        if (!fs.existsSync(dataFile)) fs.copyFileSync(dataset, dataFile);
        if (fileHash(dataFile) !== inputSha256) fail('DEPLOYMENT_DATA_COPY_DRIFT');
        if (!candidate) fs.writeFileSync(path.join(dataDir, 'source-summary.json'), JSON.stringify({ upstream: generation.upstream, csv_sha256: inputSha256, generation }), { mode: 0o600 });
        const sqlDir = path.join(dir, 'infra/postgres'); fs.mkdirSync(sqlDir, { recursive: true, mode: 0o700 });
        const sqlSource = candidate ? path.join(candidate.root, 'payload', ...candidate.artifacts.sql.path.split('/'))
          : path.join(repo, 'infra/postgres/schema.sql');
        const sqlTarget = path.join(sqlDir, 'schema.sql');
        const nextSql = path.join(dir, `.schema-${opId}.sql`);
        if (!fs.existsSync(sqlTarget)) fs.copyFileSync(sqlSource, sqlTarget);
        if (fileHash(sqlTarget) !== fileHash(sqlSource)) fs.copyFileSync(sqlSource, nextSql);
        let apiImage, postgresImage = installed?.postgresImage, apiJar = installed?.apiJar, extensionArchive;
        const candidateStage = !fresh ? path.join(dir, `.prepared-${opId}`) : dir;
        if (candidate) {
          if (!fresh) fs.mkdirSync(candidateStage, { recursive: true, mode: 0o700 });
          const loaded = await ctx.candidateAdapter.installCandidateArtifacts(candidate, { run, installDir: candidateStage, archiveRoot: dir, apiPort,
            includePostgres: !external, baseVersion: identity.baseVersion, installationId: current.id });
          apiImage = loaded.apiImage; postgresImage = external ? installed?.postgresImage : loaded.postgresImage;
          extensionArchive = loaded.extensionArchive;
        } else {
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
          const postgresBaseId = external ? null : await resolveSourceBaseImage({ run, reference: 'docker.io/library/postgres:17-bookworm' });
          const tag = versionedImageReference('api', identity.baseVersion);
          await removeVersionedImage({ run, role: 'api', baseVersion: identity.baseVersion, installationId: current.id, allowReleaseValidation: true });
          await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `JAVA_RUNTIME_IMAGE=${baseId}`,
            ...ctx.imageLabels(identity), ...versionedImageLabels('api', identity.baseVersion), '-t', tag, path.join(stage, 'api')]);
          apiImage = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', tag], { capture: true });
          if (!external) {
            const pgId = postgresBaseId;
            await removeVersionedImage({ run, role: 'postgres', baseVersion: identity.baseVersion, installationId: current.id, allowReleaseValidation: true });
            fs.mkdirSync(path.join(stage, 'postgres'), { recursive: true });
            for (const name of ['Dockerfile.postgres', 'initialize-postgres-app-role.sh']) fs.copyFileSync(path.join(repo, 'ops/docker', name), path.join(stage, 'postgres', name));
            const pgTag = versionedImageReference('postgres', identity.baseVersion);
            await run('podman', ['build', '--platform', 'linux/arm64', '--pull=never', '--build-arg', `POSTGRES_RUNTIME_IMAGE=${pgId}`,
              ...versionedImageLabels('postgres', identity.baseVersion), '-f', path.join(stage, 'postgres/Dockerfile.postgres'), '-t', pgTag, path.join(stage, 'postgres')]);
            postgresImage = await run('podman', ['image', 'inspect', '--format', '{{.Id}}', pgTag], { capture: true });
          }
        }
        if (!/^(sha256:)?[a-f0-9]{64}$/.test(apiImage) || !external && !/^(sha256:)?[a-f0-9]{64}$/.test(postgresImage)) fail('DEPLOYMENT_IMAGE_INVALID');
        verifyExtension(fresh ? path.join(dir, 'extension') : path.join(candidateStage, 'extension'), identity, apiPort);
        const composeText = renderCompose(repo, { apiPort, dbPort, external, dbUser, authMode, slot: candidate ? 'release-validation' : 'source' });
        const envText = `LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE=${apiImage}\nLEXIFLOW_POSTGRES_IMAGE=${postgresImage ?? 'external'}\nLEXIFLOW_INSTALLATION_ID=${current.id}\nLEXIFLOW_RELEASE_KEY=local-${current.id}\n`;
        if (fresh) fs.writeFileSync(path.join(dir, 'compose.yaml'), composeText, { mode: 0o600 });
        // 引用本轮准备的新镜像；managed PG 随即用原卷重建，不能继续引用刚删除的旧 digest。
        if (replacement) {
          replacement.allowedEnvSha256 = [...new Set([...replacement.allowedEnvSha256, sha(envText)])].slice(-4);
          atomicJson(path.join(dir, 'image-replacement.json'), replacement);
        }
        atomicText(path.join(dir, 'release.env'), envText);
        current = { ...current, apiPort, apiImage, postgresImage, apiJar: candidate ? null : apiJar,
          inputType: candidate ? 'package' : 'csv', inputSha256, generation,
          dataset: dataDir, datasetPackageSha256: candidate ? inputSha256 : undefined,
          version: identity.softwareVersion, buildIdentity: identity, generationVersion: generation.generationVersion,
          artifacts: { ...(candidate ? { candidateSha256: candidate.candidateSha256, manifestSha256: candidate.manifestSha256 } : { jar: fileHash(jar) }), apiImage, extensionArchive },
          digests: null };
        operationArtifacts = current.artifacts;
        ctx.setState(current);
        if (fresh) ctx.saveState(current);
        return { dataFile, apiImage, postgresImage, composeText, envText, extensionArchive, nextSql: fs.existsSync(nextSql) ? nextSql : null,
          extensionStage: !fresh ? path.join(candidateStage, 'extension') : null };
      },
      verifyPlan: async (_plan, preparedData) => {
        ctx.progress('复核计划、目标连接与制品');
        if (ctx.interrupted() || fileHash(dataset) !== inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
        if (passwordDigest && fileHash(passwordFile) !== passwordDigest) fail('DEPLOYMENT_SECRET_DRIFT');
        if ((candidate && (await ctx.candidateAdapter.readCandidate(candidateRequest)).candidateSha256 !== candidate.candidateSha256)
          || (!candidate && (resolveBuildIdentity(repo).buildId !== identity.buildId
            || await generationCodeVersion(repo) !== generation.codeVersion || fileHash(jar) !== current.artifacts.jar)))
          fail('DEPLOYMENT_INPUT_DRIFT');
        if (await isPortTaken(apiPort)) {
          if (!installedServices.has('api')) fail('DEPLOYMENT_API_PORT_OCCUPIED');
          await ctx.verifyApiPort(installed);
        }
        if (authMode !== 'development-trust') secret(path.join(dir, 'secrets/app-password'));
        if (preparedData.extensionStage) verifyExtension(preparedData.extensionStage, identity, apiPort);
        await verifyExtensionArchive(dir, preparedData.extensionArchive, identity, apiPort,
          preparedData.extensionStage ?? path.join(dir, 'extension'));
        if (preparedData.nextSql && fileHash(preparedData.nextSql) !== fileHash(candidate
          ? path.join(candidate.root, 'payload', ...candidate.artifacts.sql.path.split('/')) : path.join(repo, 'infra/postgres/schema.sql'))) fail('DEPLOYMENT_SQL_DRIFT');
        if (!external) {
          await ctx.compose('config');
          await ctx.compose('up', '-d', '--force-recreate', 'postgres');
          await ctx.waitManagedPostgres({ ...current, postgresImage: preparedData.postgresImage });
        }
        if (external) {
          if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
          const again = await probeExternal({ run, port: dbPort, user: dbUser, passwordFile, authMode, developmentIdentity,
            postgresImage: installed?.postgresImage ?? 'docker.io/library/postgres:17-bookworm' });
          if (again.fingerprint !== externalProbe.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
          if ((await inspect(passwordFile, preparedData.apiImage, preparedData.dataFile)).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
        } else if (observation.fingerprint !== missing && (await inspect(path.join(dir, 'secrets/app-password'), preparedData.apiImage, preparedData.dataFile)).fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
        if (external && observation.fingerprint === missing) {
          if (await isPortTaken(dbPort)) fail('DEPLOYMENT_DATABASE_PORT_OCCUPIED');
          await ctx.compose('config');
        }
        // 必须在停止旧 API 前证明实际将运行的镜像能读入请求和私密凭据，并连接目标 PG。
        const actual = await inspect(external ? passwordFile : path.join(dir, 'secrets/app-password'),
          preparedData.apiImage, preparedData.dataFile);
        if (observation.fingerprint !== missing && actual.fingerprint !== observation.fingerprint)
          fail('DEPLOYMENT_DATABASE_DRIFT');
        const sqlTarget = preparedData.nextSql ?? path.join(dir, 'infra/postgres/schema.sql');
        atomicJson(path.join(dir, 'prepared-target.json'), { schema: 1, operation: opId, planHash,
          targetState: current, dataFile: preparedData.dataFile, apiImage: preparedData.apiImage,
          previousApiImage: installed?.apiImage ?? null,
          actions: plan.actions, nextSql: preparedData.nextSql ?? null,
          extensionStage: preparedData.extensionStage ?? null,
          composeText: preparedData.composeText ?? null, envText: preparedData.envText ?? null,
          sqlSha256: fileHash(sqlTarget), extensionSha256: preparedData.extensionStage
            ? treeDigest(preparedData.extensionStage) : treeDigest(path.join(dir, 'extension')),
          previousExtensionSha256: fs.existsSync(path.join(dir, 'extension')) ? treeDigest(path.join(dir, 'extension')) : null,
          secretSha256: authMode === 'development-trust' ? null : fileHash(path.join(dir, 'secrets/app-password')) });
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
        const now = await inspect(external ? passwordFile : path.join(dir, 'secrets/app-password'),
          preparedData.apiImage, preparedData.dataFile);
        if (observation.fingerprint !== missing && now.fingerprint !== observation.fingerprint) fail('DEPLOYMENT_DATABASE_DRIFT');
        const password = external ? passwordFile : path.join(dir, 'secrets/app-password');
        const response = JSON.parse((await dataCommand('apply', { password, image: preparedData.apiImage,
          input: preparedData.dataFile, expectedFingerprint: now.fingerprint })).trim());
        if (response.status !== 'PASS' || response.generationVersion !== generation.generationVersion || !Number.isSafeInteger(response.datasetVersion)) fail('DEPLOYMENT_DATA_COMMIT_UNCONFIRMED');
        return response;
      },
      activateApplication: async (_plan, preparedData, committed) => {
        ctx.progress('激活目标应用');
        current = { ...current, phase: 'initialized', datasetIdentity: { version: committed.datasetVersion, generationVersion: generation.generationVersion, sourceSha256: inputSha256 } };
        ctx.setState(current);
        if (preparedData.nextSql) fs.renameSync(preparedData.nextSql, path.join(dir, 'infra/postgres/schema.sql'));
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
        if (authMode === 'development-trust') developmentIdentity = await assertDevelopmentIdentity(developmentIdentity);
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
    releaseImageSlot?.();
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
      || value.nextSql && value.nextSql !== path.join(dir, `.schema-${journal.operation}.sql`)
      || value.extensionStage && value.extensionStage !== path.join(dir, `.prepared-${journal.operation}/extension`)
      || value.actions?.applicationUpdate && value.targetState.apiJar
        && !value.targetState.apiJar.startsWith(path.join(dir, `.prepared-${journal.operation}`) + path.sep)
      || !hex.test(value.sqlSha256 ?? '') || !hex.test(value.extensionSha256 ?? '')
      || (value.targetState.databaseTarget?.authMode === 'development-trust'
        ? value.secretSha256 !== null || value.targetState.databaseTarget?.kind !== 'external'
        : !hex.test(value.secretSha256 ?? ''))) fail('DEPLOYMENT_PREPARED_TARGET_INVALID');
  return value;
}

/** 只在调用方独立核对数据库成功事实后，补齐本安装的目标制品/状态；不做导入。 */
export async function activatePreparedTarget({ dir, prepared, previous, fact, digestFiles, saveState, setState, compose, ready, verifyDevelopmentIdentity }) {
  const target = { ...prepared.targetState, phase: 'initialized', datasetIdentity: {
    version: fact.datasetVersion, generationVersion: prepared.targetState.generationVersion,
    sourceSha256: prepared.targetState.inputSha256 } };
  if (fact.needsRebuild || fact.generationVersion !== target.generationVersion
      || !Number.isSafeInteger(fact.datasetVersion) || fileHash(prepared.dataFile) !== target.inputSha256
      || (target.databaseTarget?.authMode === 'development-trust'
        ? prepared.secretSha256 !== null || fs.existsSync(path.join(dir, 'secrets/app-password'))
        : fileHash(path.join(dir, 'secrets/app-password')) !== prepared.secretSha256)) fail('DEPLOYMENT_RECOVERY_FACT_DRIFT');
  const allow = (file, expected) => {
    if (!fs.existsSync(file)) return;
    const found = fileHash(file);
    if (found !== expected && found !== previous.digests?.[path.relative(dir, file)]) fail('DEPLOYMENT_RECOVERY_CONFIG_DRIFT');
  };
  const sqlFile = path.join(dir, 'infra/postgres/schema.sql');
  allow(sqlFile, prepared.sqlSha256);
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
  if (prepared.nextSql) {
    if (fs.existsSync(prepared.nextSql)) {
      if (fileHash(prepared.nextSql) !== prepared.sqlSha256) fail('DEPLOYMENT_RECOVERY_SQL_DRIFT');
      fs.renameSync(prepared.nextSql, sqlFile);
    } else if (fileHash(sqlFile) !== prepared.sqlSha256) fail('DEPLOYMENT_RECOVERY_SQL_MISSING');
  } else if (fileHash(sqlFile) !== prepared.sqlSha256) fail('DEPLOYMENT_RECOVERY_SQL_DRIFT');
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
  if (target.databaseTarget?.authMode === 'development-trust') {
    if (typeof verifyDevelopmentIdentity !== 'function') fail('DEVELOPMENT_DATABASE_TARGET_UNAVAILABLE');
    await verifyDevelopmentIdentity(target);
  }
  setState(target); target.digests = digestFiles(target); saveState(target);
  await compose('config'); await compose('up', '-d', '--no-deps', '--force-recreate', 'api');
  await ready(target);
  await verifyExtensionArchive(dir, target.artifacts?.extensionArchive, target.buildIdentity, target.apiPort, extension);
  if (target.databaseTarget?.authMode === 'development-trust') await verifyDevelopmentIdentity(target);
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

/** recover/status 使用已落盘的目标资料和目标制品只读核对 PostgreSQL 成功事实。 */
export async function inspectInstalledDeployment({ repo, state, run, environment }) {
  if (!state || !['csv', 'package'].includes(state.inputType) || !hex.test(state.inputSha256)
      || !hex.test(state.generationVersion) || state.generation?.generationVersion !== state.generationVersion
      || !Number.isInteger(state.dbPort) || state.dbPort < 1 || state.dbPort > 65535) fail('DEPLOYMENT_INSPECTION_STATE_INVALID');
  const input = path.join(state.dataset, state.inputType === 'package' ? 'dataset.zip' : 'stardict.csv');
  if (fileHash(input) !== state.inputSha256) fail('DEPLOYMENT_INPUT_DRIFT');
  const authMode = state.databaseTarget?.authMode ?? (state.databaseTarget?.kind === 'external' ? 'password' : 'managed');
  if (!['managed', 'password', 'development-trust'].includes(authMode)
      || (state.databaseTarget?.kind === 'external') !== (authMode !== 'managed')) fail('DEPLOYMENT_INSPECTION_STATE_INVALID');
  const passwordFile = authMode === 'development-trust' ? null : secret(path.join(state.root, 'secrets/app-password'));
  if (authMode === 'development-trust') {
    if (state.databaseTarget?.user !== 'postgres' || fs.existsSync(path.join(state.root, 'secrets/app-password')))
      fail('DEPLOYMENT_INSPECTION_STATE_INVALID');
    await assertDevelopmentTargetIdentity({ repo, state, run });
  }
  const user = state.databaseTarget?.user ?? 'lexiflow';
  const root = tempRoot(repo);
  try {
    const request = writeRequest(root, { inputType: state.inputType,
      inputPath: '/input', inputSha256: state.inputSha256,
      generation: state.generation });
    if (!/^(?:sha256:)?[a-f0-9]{64}$/.test(state.apiImage ?? '')) fail('DEPLOYMENT_INSPECTION_IMAGE_INVALID');
    const output = await run('podman', ['run', '--rm', '--platform', 'linux/arm64', '--user', '0:0', '--read-only',
        '--cap-drop=ALL', '--cap-add=DAC_OVERRIDE', '--security-opt=no-new-privileges', '--add-host', 'host.containers.internal:host-gateway',
        '-v', `${request}:/request.json:ro`, '-v', `${input}:/input:ro`,
        ...(authMode === 'development-trust' ? [] : ['-v', `${passwordFile}:/secret:ro`]),
        '-e', `LEXIFLOW_DEPLOYMENT_JDBC_URL=jdbc:postgresql://host.containers.internal:${state.dbPort}/lexiflow?currentSchema=lexiflow_release`,
        '-e', `LEXIFLOW_DEPLOYMENT_DB_USER=${user}`,
        ...(authMode === 'development-trust' ? ['-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD=', '-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE=']
          : ['-e', 'LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE=/secret']),
        '--entrypoint', 'java', state.apiImage, '-jar', '/app/lexiflow-api.jar', '--deployment-data', 'inspect', '--request', '/request.json'],
      { cwd: repo, capture: true, env: authMode === 'development-trust'
        ? { ...environment, LEXIFLOW_DEPLOYMENT_DB_PASSWORD: '', LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE: '' }
        : environment, logOutput: false });
    if (authMode === 'development-trust') await assertDevelopmentTargetIdentity({ repo, state, run });
    return databaseResponse(output);
  } finally {
    if (fs.existsSync(root) && fs.readdirSync(root).every(name => /^request-[a-f0-9]{16}\.json$/.test(name))) fs.rmSync(root, { recursive: true });
  }
}
