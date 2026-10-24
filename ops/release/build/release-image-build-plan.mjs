import { createHash } from 'node:crypto';
import { lstat, readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { isIP } from 'node:net';
import { resolveCleanBuildIdentity } from '../integrity/source-build-identity.mjs';
import { verifyJarIdentity } from '../integrity/artifact-build-identity.mjs';
import { readZipEntries } from '../integrity/extension-archive-integrity.mjs';
import { validateRelativePath, verifyContainedFile } from '../integrity/release-artifact-manifest.mjs';

const LOCK = 'ops/docker/base-images.json';
const TEMPLATE_PATHS = ['ops/docker/Dockerfile.runtime', 'ops/docker/runtime-entrypoint.sh'];
const IMAGE_NOTICE_EXCLUSIONS = new Set(['licenses/api-base-corresponding-sources.tar.gz',
  'licenses/postgres-base-corresponding-sources.tar.gz', 'licenses/OpenJDK25U-jdk-sources_25.0.4.1_1.tar.gz']);
const HASH = /^[a-f0-9]{64}$/;
const reject = (reason) => { throw new Error(reason); };
const keys = (value, fields) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0');
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function assertNoSymlinkPath(target) {
  const absolute = path.resolve(target); let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part); const info = await lstat(cursor).catch(() => null);
    if (!info || info.isSymbolicLink()) reject('INPUT_INVALID');
  }
}

function tracked(root, relative) {
  try { execFileSync('git', ['-C', root, 'ls-files', '--error-unmatch', '--', relative], { stdio: 'ignore', timeout: 10_000 }); return true; }
  catch { return false; }
}
async function trackedBytes(root, relative, maximum = 32 * 1024 * 1024) {
  if (!tracked(root, relative)) reject('TEMPLATE_INVALID');
  let cursor = root; let info;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part); info = await lstat(cursor).catch(() => null);
    if (!info || info.isSymbolicLink()) reject('TEMPLATE_INVALID');
  }
  if (!info.isFile() || info.size < 1 || info.size > maximum) reject('TEMPLATE_INVALID');
  const bytes = await readFile(cursor);
  if (bytes.length !== info.size || bytes.length > maximum) reject('TEMPLATE_INVALID');
  return { path: relative, bytes: bytes.length, sha256: sha(bytes) };
}
async function baseImages(root) {
  const file = path.join(root, LOCK);
  if (!tracked(root, LOCK)) reject('BASE_IMAGES_INVALID');
  const info = await lstat(file).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.size < 1 || info.size > 16 * 1024) reject('BASE_IMAGES_INVALID');
  let lock;
  try { lock = JSON.parse(await readFile(file, 'utf8')); } catch { reject('BASE_IMAGES_INVALID'); }
  if (!keys(lock, ['schemaVersion', 'baseImages']) || lock.schemaVersion !== 1 || !Array.isArray(lock.baseImages)
    || lock.baseImages.length < 1 || lock.baseImages.length > 2) reject('BASE_IMAGES_INVALID');
  const seen = new Set();
  for (const item of lock.baseImages) {
    if (!keys(item, ['platform', 'javaRuntime', 'postgresRuntime']) || !['linux/amd64', 'linux/arm64'].includes(item.platform) || seen.has(item.platform)) reject('BASE_IMAGES_INVALID');
    seen.add(item.platform);
    for (const runtime of [item.javaRuntime, item.postgresRuntime]) {
      const match = typeof runtime?.reference === 'string'
        ? /^([a-z0-9][a-z0-9.-]*(?::([0-9]{1,5}))?)\/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$/.exec(runtime.reference) : null;
      const hostname = match?.[1].replace(/:[0-9]+$/, '');
      if (!keys(runtime, ['reference', 'imageId']) || !match || hostname.split('.').length < 2 || isIP(hostname)
        || hostname === 'localhost' || hostname.endsWith('.localhost') || hostname.endsWith('.local') || hostname.endsWith('.internal')
        || match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535)
        || !/^sha256:[a-f0-9]{64}$/.test(runtime.imageId)) reject('BASE_IMAGES_INVALID');
    }
  }
  const rows = lock.baseImages.filter(item => item.platform === 'linux/arm64');
  if (rows.length !== 1 || !keys(rows[0], ['platform', 'javaRuntime', 'postgresRuntime'])) reject('BASE_IMAGES_INVALID');
  for (const runtime of [rows[0].javaRuntime, rows[0].postgresRuntime]) {
    if (!keys(runtime, ['reference', 'imageId']) || typeof runtime.reference !== 'string'
      || !/^[a-z0-9][a-z0-9.-]*(?::[0-9]+)?\/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$/.test(runtime.reference)
      || !/^sha256:[a-f0-9]{64}$/.test(runtime.imageId)) reject('BASE_IMAGES_INVALID');
  }
  return structuredClone(rows[0]);
}

export async function prepareImageBuild(input) {
  try {
    if (!keys(input, ['repoRoot', 'artifactRoot', 'descriptor'])) reject('INPUT_INVALID');
    const { repoRoot, artifactRoot, descriptor } = input;
    if (![repoRoot, artifactRoot].every(value => typeof value === 'string' && path.isAbsolute(value))) reject('INPUT_INVALID');
    await assertNoSymlinkPath(repoRoot); await assertNoSymlinkPath(artifactRoot);
    if (!keys(descriptor, ['schemaVersion', 'softwareVersion', 'sourceCommit', 'jar', 'apiContract', 'sqlVersion', 'dataset', 'notices'])
      || descriptor.schemaVersion !== 1 || descriptor.apiContract !== 'caption-hints.v2'
      || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(descriptor.sourceCommit) || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/.test(descriptor.sqlVersion)) reject('DESCRIPTOR_INVALID');
    let identity;
    try { identity = resolveCleanBuildIdentity(repoRoot); } catch { reject('RELEASE_SOURCE_REJECTED'); }
    if (identity.dirty || identity.softwareVersion !== descriptor.softwareVersion || identity.sourceCommit !== descriptor.sourceCommit) reject('SOURCE_IDENTITY_MISMATCH');
    const jar = structuredClone(descriptor.jar);
    if (!keys(jar, ['path', 'bytes', 'sha256']) || !Number.isSafeInteger(jar.bytes) || jar.bytes < 1 || jar.bytes > 2 ** 40 || !HASH.test(jar.sha256)) reject('JAR_INVALID');
    validateRelativePath(jar.path);
    await verifyContainedFile(artifactRoot, jar.path, jar.bytes, jar.sha256);
    await verifyJarIdentity(path.join(artifactRoot, ...jar.path.split('/')), identity);
    const data = descriptor.dataset;
    if (!keys(data, ['path', 'bytes', 'sha256', 'releaseId', 'preparationId', 'ruleId'])
      || !Number.isSafeInteger(data.bytes) || data.bytes < 1 || data.bytes > 3 * 1024 ** 3 || !HASH.test(data.sha256)
      || ![data.releaseId, data.preparationId, data.ruleId].every(item => typeof item === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(item))) reject('DATASET_INVALID');
    validateRelativePath(data.path);
    await verifyContainedFile(artifactRoot, data.path, data.bytes, data.sha256);
    if (!Array.isArray(descriptor.notices) || descriptor.notices.length < 1 || descriptor.notices.length > 128) reject('NOTICES_INVALID');
    const notices = [];
    const seen = new Set();
    for (const item of descriptor.notices) {
      if (!keys(item, ['path', 'bytes', 'sha256']) || !Number.isSafeInteger(item.bytes) || item.bytes < 1
        || item.bytes > 2 ** 40 || !HASH.test(item.sha256)) reject('NOTICES_INVALID');
      validateRelativePath(item.path);
      if (!item.path.startsWith('licenses/') || seen.has(item.path)) reject('NOTICES_INVALID');
      seen.add(item.path);
      await verifyContainedFile(artifactRoot, item.path, item.bytes, item.sha256);
      notices.push({ ...item, target: `notices/${item.path.slice('licenses/'.length)}` });
    }
    const templates = [];
    for (const relative of TEMPLATE_PATHS) templates.push(await trackedBytes(repoRoot, relative));
    const sql = await trackedBytes(repoRoot, 'infra/postgres/schema.sql', 16 * 1024 * 1024);
    const jarEntries = await readZipEntries(path.join(artifactRoot, ...jar.path.split('/')), ['META-INF/lexiflow-schema.sql']);
    if (!jarEntries.get('META-INF/lexiflow-schema.sql').equals(await readFile(path.join(repoRoot, 'infra/postgres/schema.sql')))) reject('SQL_IDENTITY_MISMATCH');
    const bases = await baseImages(repoRoot);
    const datasetIdentity = { releaseId: data.releaseId, preparationId: data.preparationId, ruleId: data.ruleId,
      bytes: data.bytes, sha256: data.sha256 };
    const runtimeIdentity = { schemaVersion: 1, softwareVersion: identity.softwareVersion, buildIdentity: identity,
      apiContract: descriptor.apiContract, sqlVersion: descriptor.sqlVersion, dataset: datasetIdentity };
    const env = ['schemaVersion=1', `softwareVersion=${identity.softwareVersion}`, `apiContract=${descriptor.apiContract}`,
      `sqlVersion=${descriptor.sqlVersion}`, `releaseId=${data.releaseId}`, `preparationId=${data.preparationId}`,
      `ruleId=${data.ruleId}`, `datasetSha256=${data.sha256}`].join('\n') + '\n';
    const contextFiles = [
      { path: jar.path, target: 'lexiflow-api.jar', bytes: jar.bytes, sha256: jar.sha256, root: 'artifact' },
      { path: data.path, target: 'dataset.zip', bytes: data.bytes, sha256: data.sha256, root: 'artifact' },
      ...notices.filter(item => !IMAGE_NOTICE_EXCLUSIONS.has(item.path))
        .map(({ path: source, target, bytes, sha256 }) => ({ path: source, target, bytes, sha256, root: 'artifact' })),
      { path: sql.path, target: 'schema.sql', bytes: sql.bytes, sha256: sql.sha256, root: 'repo' },
      ...templates.map(item => ({ ...item, target: path.basename(item.path), root: 'repo' })),
    ];
    const commands = [['docker', 'build', '--pull=false', '--network=none', '--platform', 'linux/arm64',
      '--label', `org.opencontainers.image.version=${identity.softwareVersion}`, '--label', `org.opencontainers.image.revision=${identity.sourceCommit}`,
      '--label', `io.lexiflow.build-id=${identity.buildId}`, '--label', `io.lexiflow.source-sha256=${identity.sourceSha256}`,
      '--label', 'io.lexiflow.image-role=runtime', '--build-arg', `JAVA_RUNTIME_IMAGE=${bases.javaRuntime.reference}`,
      '--build-arg', `POSTGRES_RUNTIME_IMAGE=${bases.postgresRuntime.reference}`, '--file', 'Dockerfile.runtime', '--iidfile', 'runtime.iid', '.']];
    const finalIdentity = resolveCleanBuildIdentity(repoRoot);
    if (JSON.stringify(identity) !== JSON.stringify(finalIdentity)) reject('RELEASE_SOURCE_CHANGED');
    const platform = { platform: 'linux/arm64', buildIdentity: identity, bases, contextFiles, commands };
    return { schemaVersion: 1, softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit,
      buildIdentity: identity, platform: 'linux/arm64', jar, dataset: datasetIdentity, apiContract: descriptor.apiContract,
      sqlVersion: descriptor.sqlVersion, sql, notices, templates, bases, runtimeIdentity, runtimeIdentityEnv: env,
      platforms: [platform] };
  } catch (error) {
    if (['DESCRIPTOR_INVALID', 'SOURCE_IDENTITY_MISMATCH', 'JAR_INVALID', 'JAR_IDENTITY_MISMATCH', 'RELEASE_SOURCE_REJECTED', 'DATASET_INVALID', 'NOTICES_INVALID', 'TEMPLATE_INVALID',
      'BASE_IMAGES_INVALID', 'INPUT_INVALID', 'RELEASE_SOURCE_CHANGED', 'SQL_IDENTITY_MISMATCH', 'ARCHIVE_INVALID', 'ARCHIVE_ENTRY_MISSING', 'ARCHIVE_LIMIT_EXCEEDED'].includes(error.message)) throw error;
    reject('BUILD_INPUT_REJECTED');
  }
}

export async function loadBaseImageLock(repoRoot) { return [await baseImages(path.resolve(repoRoot))]; }
