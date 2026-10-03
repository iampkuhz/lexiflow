import { createHash } from 'node:crypto';
import { lstat, readFile } from 'node:fs/promises';
import { lstatSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { isIP } from 'node:net';
import path from 'node:path';
import { checkReleaseSource, resolveBuildIdentity } from './version.mjs';
import { verifyJarIdentity } from './embedded-identity.mjs';
import { validateRelativePath, verifyContainedFile } from './manifest.mjs';

const TEMPLATE_PATHS = [
  'ops/docker/Dockerfile',
  'ops/docker/Dockerfile.postgres',
  'ops/docker/.dockerignore',
  'ops/docker/entrypoint.sh',
  'ops/docker/bootstrap.sh',
];
const TEMPLATE_NAMES = ['Dockerfile', 'Dockerfile.postgres', '.dockerignore', 'entrypoint.sh', 'bootstrap.sh'];
const BASE_IMAGE_LOCK_PATH = 'ops/docker/base-images.json';
const MAX_BASE_IMAGE_LOCK_BYTES = 16 * 1024;
const PLATFORMS = ['linux/amd64', 'linux/arm64'];
const HASH = /^[a-f0-9]{64}$/;
const reject = (reason) => { throw new Error(reason); };
const hasKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
const digest = (content) => createHash('sha256').update(content).digest('hex');

function safeAbsoluteDirectory(value) {
  if (typeof value !== 'string' || !value || !path.isAbsolute(value) || /[\u0000-\u001f\u007f]/.test(value)) reject('INPUT_INVALID');
  let cursor = path.parse(value).root;
  for (const part of value.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    let info;
    try { info = lstatSync(cursor); } catch { reject('INPUT_INVALID'); }
    if (info.isSymbolicLink()) reject('INPUT_INVALID');
  }
  try { if (!lstatSync(value).isDirectory()) reject('INPUT_INVALID'); } catch { reject('INPUT_INVALID'); }
}

function validRuntime(runtime) {
  if (!hasKeys(runtime, ['reference', 'imageId'])) return false;
  const reference = runtime.reference;
  if (typeof reference !== 'string' || /[\u0000-\u0020\u007f;&|$`<>\\'"(){}!]/.test(reference)) return false;
  const match = /^([a-z0-9.-]+(?::([0-9]+))?)\/([a-z0-9._/-]+)@sha256:([a-f0-9]{64})$/.exec(reference);
  if (!match || match[3].split('/').some((part) => !part || !/^[a-z0-9][a-z0-9._-]*$/.test(part))) return false;
  const host = match[1].split(':')[0];
  const labels = host.split('.');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal') || isIP(host)
    || labels.length < 2 || labels.some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) return false;
  if (/^\d+$/.test(match[2] ?? '') && (Number(match[2]) < 1 || Number(match[2]) > 65535)) return false;
  if (typeof runtime.imageId !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(runtime.imageId)) return false;
  return true;
}

function parseBaseImageLock(text) {
  let value;
  try { value = JSON.parse(text); } catch { reject('BASE_IMAGES_INVALID'); }
  if (!hasKeys(value, ['schemaVersion', 'baseImages']) || value.schemaVersion !== 1
    || !Array.isArray(value.baseImages) || value.baseImages.length < 1 || value.baseImages.length > PLATFORMS.length) reject('BASE_IMAGES_INVALID');
  const byPlatform = new Map();
  for (const item of value.baseImages) {
    if (!hasKeys(item, ['platform', 'javaRuntime', 'postgresRuntime']) || !PLATFORMS.includes(item.platform)
      || byPlatform.has(item.platform) || !validRuntime(item.javaRuntime) || !validRuntime(item.postgresRuntime)) reject('BASE_IMAGES_INVALID');
    byPlatform.set(item.platform, {
      platform: item.platform,
      javaRuntime: { reference: item.javaRuntime.reference, imageId: item.javaRuntime.imageId },
      postgresRuntime: { reference: item.postgresRuntime.reference, imageId: item.postgresRuntime.imageId },
    });
  }
  return PLATFORMS.filter((platform) => byPlatform.has(platform)).map((platform) => byPlatform.get(platform));
}

/** Load the canonical, tracked base-image lock from a repository without following symlinks. */
async function readBaseImageLock(repoRoot) {
  let root;
  try { safeAbsoluteDirectory(repoRoot); root = path.resolve(repoRoot); } catch { reject('BASE_IMAGES_INVALID'); }
  try {
    execFileSync('git', ['-C', root, 'ls-files', '--error-unmatch', '--', BASE_IMAGE_LOCK_PATH], { stdio: 'ignore', timeout: 10_000 });
    let cursor = root; let info;
    for (const part of BASE_IMAGE_LOCK_PATH.split('/')) {
      cursor = path.join(cursor, part);
      info = await lstat(cursor).catch(() => null);
      if (!info || info.isSymbolicLink()) reject('BASE_IMAGES_INVALID');
    }
    if (!info.isFile() || info.size < 1 || info.size > MAX_BASE_IMAGE_LOCK_BYTES) reject('BASE_IMAGES_INVALID');
    const bytes = await readFile(cursor);
    if (bytes.length !== info.size || bytes.length > MAX_BASE_IMAGE_LOCK_BYTES) reject('BASE_IMAGES_INVALID');
    return { baseImages: parseBaseImageLock(bytes.toString('utf8')), bytes: bytes.length, sha256: digest(bytes) };
  } catch (error) {
    if (error.message === 'BASE_IMAGES_INVALID') throw error;
    reject('BASE_IMAGES_INVALID');
  }
}

export async function loadBaseImageLock(repoRoot) {
  return (await readBaseImageLock(repoRoot)).baseImages;
}

function buildCommands(platform, javaId, postgresId, javaReference, postgresReference, buildIdentity) {
  const labels = [
    `org.opencontainers.image.version=${buildIdentity.softwareVersion}`,
    `org.opencontainers.image.revision=${buildIdentity.sourceCommit}`,
    `io.lexiflow.build-id=${buildIdentity.buildId}`,
    `io.lexiflow.source-sha256=${buildIdentity.sourceSha256}`,
  ].flatMap((label) => ['--label', label]);
  return [
    ['docker', 'image', 'inspect', javaId],
    ['docker', 'image', 'inspect', postgresId],
    ['docker', 'build', '--pull=false', '--network=none', '--platform', platform, ...labels, '--build-arg', `JAVA_RUNTIME_IMAGE=${javaReference}`, '--file', 'Dockerfile', '--iidfile', 'api.iid', '.'],
    ['docker', 'build', '--pull=false', '--network=none', '--platform', platform, ...labels, '--build-arg', `POSTGRES_RUNTIME_IMAGE=${postgresReference}`, '--file', 'Dockerfile.postgres', '--iidfile', 'postgres.iid', '.'],
  ];
}

export async function prepareImageBuild(input) {
  if (!hasKeys(input, ['repoRoot', 'artifactRoot', 'descriptor'])) reject('INPUT_INVALID');
  const { repoRoot, artifactRoot, descriptor } = input;
  try { safeAbsoluteDirectory(repoRoot); safeAbsoluteDirectory(artifactRoot); } catch { reject('INPUT_INVALID'); }
  let source;
  try { source = checkReleaseSource(repoRoot); } catch { reject('RELEASE_SOURCE_REJECTED'); }
  let buildIdentity;
  try { buildIdentity = resolveBuildIdentity(repoRoot); } catch { reject('RELEASE_SOURCE_REJECTED'); }
  if (buildIdentity.dirty || buildIdentity.sourceCommit !== source.sourceCommit
    || buildIdentity.softwareVersion !== source.softwareVersion) reject('RELEASE_SOURCE_REJECTED');
  try {
    if (!hasKeys(descriptor, ['schemaVersion', 'softwareVersion', 'sourceCommit', 'jar', 'baseImages'])
      || descriptor.schemaVersion !== 1 || descriptor.softwareVersion !== source.softwareVersion
      || descriptor.sourceCommit !== source.sourceCommit) reject('SOURCE_IDENTITY_MISMATCH');
    if (!hasKeys(descriptor.jar, ['path', 'bytes', 'sha256']) || typeof descriptor.jar.path !== 'string'
      || !Number.isSafeInteger(descriptor.jar.bytes) || descriptor.jar.bytes < 1 || descriptor.jar.bytes > 2 ** 40
      || typeof descriptor.jar.sha256 !== 'string' || !HASH.test(descriptor.jar.sha256)) reject('JAR_INVALID');
    validateRelativePath(descriptor.jar.path);
    await verifyContainedFile(artifactRoot, descriptor.jar.path, descriptor.jar.bytes, descriptor.jar.sha256);
    if (!Array.isArray(descriptor.baseImages) || descriptor.baseImages.length < 1 || descriptor.baseImages.length > PLATFORMS.length) reject('BASE_IMAGES_INVALID');
    const byPlatform = new Map();
    for (const item of descriptor.baseImages) {
      if (!hasKeys(item, ['platform', 'javaRuntime', 'postgresRuntime']) || !PLATFORMS.includes(item.platform)
        || byPlatform.has(item.platform) || !validRuntime(item.javaRuntime) || !validRuntime(item.postgresRuntime)) reject('BASE_IMAGES_INVALID');
      byPlatform.set(item.platform, item);
    }
    const selectedPlatforms = PLATFORMS.filter((platform) => byPlatform.has(platform));
    let lock;
    try { lock = await readBaseImageLock(repoRoot); } catch { reject('BASE_IMAGES_INVALID'); }
    const lockedBaseImages = lock.baseImages;
    for (const platform of selectedPlatforms) {
      const descriptorImages = byPlatform.get(platform);
      const lockedImages = lockedBaseImages.find((item) => item.platform === platform);
      const matches = (left, right) => left.reference === right.reference && left.imageId === right.imageId;
      if (!matches(descriptorImages.javaRuntime, lockedImages.javaRuntime)
        || !matches(descriptorImages.postgresRuntime, lockedImages.postgresRuntime)) reject('BASE_IMAGES_INVALID');
    }
    const templates = [];
    for (const relative of TEMPLATE_PATHS) {
      const tracked = (() => {
        try { execFileSync('git', ['-C', repoRoot, 'ls-files', '--error-unmatch', '--', relative], { stdio: 'ignore', timeout: 10_000 }); return true; } catch { return false; }
      })();
      if (!tracked) reject('TEMPLATE_INVALID');
      let cursor = repoRoot; let info;
      for (const part of relative.split('/')) {
        cursor = path.join(cursor, part);
        info = await lstat(cursor).catch(() => null);
        if (!info || info.isSymbolicLink()) reject('TEMPLATE_INVALID');
      }
      if (!info.isFile()) reject('TEMPLATE_INVALID');
      const content = await readFile(cursor);
      templates.push({ path: relative, bytes: content.length, sha256: digest(content) });
    }
    templates.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
    const jar = { path: descriptor.jar.path, bytes: descriptor.jar.bytes, sha256: descriptor.jar.sha256 };
    // Bind the embedded application identity to the same contained artifact whose
    // bytes and digest were validated above. Recheck after parsing to close races.
    try { await verifyContainedFile(artifactRoot, jar.path, jar.bytes, jar.sha256); }
    catch { reject('BUILD_INPUT_REJECTED'); }
    const verifiedJarPath = path.join(artifactRoot, ...jar.path.split('/'));
    await verifyJarIdentity(verifiedJarPath, buildIdentity);
    try { await verifyContainedFile(artifactRoot, jar.path, jar.bytes, jar.sha256); }
    catch { reject('BUILD_INPUT_REJECTED'); }
    const platforms = selectedPlatforms.map((platform) => {
      const images = byPlatform.get(platform);
      const contextFiles = [
        ...TEMPLATE_PATHS.map((templatePath, index) => ({ path: templatePath, target: TEMPLATE_NAMES[index] })),
        { path: jar.path, target: 'lexiflow-api.jar' },
      ];
      return { platform, buildIdentity, javaRuntime: { ...images.javaRuntime }, postgresRuntime: { ...images.postgresRuntime }, contextFiles, commands: buildCommands(platform, images.javaRuntime.imageId, images.postgresRuntime.imageId, images.javaRuntime.reference, images.postgresRuntime.reference, buildIdentity) };
    });
    let finalSource;
    try { finalSource = checkReleaseSource(repoRoot); } catch { reject('RELEASE_SOURCE_CHANGED'); }
    let finalBuildIdentity;
    try { finalBuildIdentity = resolveBuildIdentity(repoRoot); } catch { reject('RELEASE_SOURCE_CHANGED'); }
    if (source.sourceCommit !== finalSource.sourceCommit || source.softwareVersion !== finalSource.softwareVersion
      || JSON.stringify(buildIdentity) !== JSON.stringify(finalBuildIdentity)) reject('RELEASE_SOURCE_CHANGED');
    const baseImageLock = { path: BASE_IMAGE_LOCK_PATH, bytes: lock.bytes, sha256: lock.sha256 };
    return { schemaVersion: 1, softwareVersion: source.softwareVersion, sourceCommit: source.sourceCommit, buildIdentity, jar, templates, baseImageLock, platforms };
  } catch (error) {
    if (['SOURCE_IDENTITY_MISMATCH', 'JAR_INVALID', 'JAR_IDENTITY_MISMATCH', 'ARCHIVE_INVALID', 'ARCHIVE_ENTRY_MISSING', 'ARCHIVE_LIMIT_EXCEEDED', 'BASE_IMAGES_INVALID', 'TEMPLATE_INVALID', 'RELEASE_SOURCE_CHANGED'].includes(error.message)) throw error;
    reject('BUILD_INPUT_REJECTED');
  }
}
