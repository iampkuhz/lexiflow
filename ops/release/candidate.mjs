import { createHash, randomUUID } from 'node:crypto';
import { constants, createReadStream, createWriteStream } from 'node:fs';
import { chmod, lstat, link, mkdir, mkdtemp, open, readFile, rm, unlink, writeFile } from 'node:fs/promises';
import { isIP } from 'node:net';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import path from 'node:path';
import { createManifest, verifyContainedFile, validateRelativePath } from './manifest.mjs';
import { generateLifecycleEntry } from './lifecycle.mjs';
import { packageManifest } from './package.mjs';
import { checkReleaseSource } from './version.mjs';

const fail = (code) => { throw new Error(code); };
const hash = () => createHash('sha256');
const hex = /^[a-f0-9]{64}$/;
const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
const within = (parent, child) => {
  const rel = path.relative(parent, child);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
};
function validBaseReference(value) {
  if (typeof value !== 'string' || /[\u0000-\u0020\u007f;|&$`<>\\]/.test(value)) return false;
  const match = /^([a-z0-9][a-z0-9._-]*(?::([0-9]{1,5}))?)\/([a-z0-9][a-z0-9._/-]*)@sha256:[a-f0-9]{64}$/.exec(value);
  if (!match) return false;
  const registry = match[1]; const port = match[2]; const repository = match[3];
  const hostname = port ? registry.slice(0, -(port.length + 1)) : registry;
  const parts = repository.split('/');
  const labels = hostname.split('.');
  if (labels.length < 2 || labels.some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
    || isIP(hostname) || hostname === 'localhost' || hostname.endsWith('.localhost')
    || hostname.endsWith('.local') || hostname.endsWith('.internal') || (port && (Number(port) < 1 || Number(port) > 65535))
    || parts.some((part) => !/^[a-z0-9][a-z0-9._-]*$/.test(part))) return false;
  return true;
}
const noSymlinkPath = async (target, allowMissing = false) => {
  const absolute = path.resolve(target); let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch((error) => error.code === 'ENOENT' && allowMissing ? null : fail('INPUT_PATH_INVALID'));
    if (info?.isSymbolicLink()) fail('INPUT_PATH_INVALID');
  }
};

async function readImageCandidate(directory) {
  const file = path.join(directory, 'candidate.json');
  const info = await lstat(file).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.size < 1 || info.size > 2_000_000) fail('IMAGE_CANDIDATE_INVALID');
  let bytes;
  let handle;
  try {
    handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    const opened = await handle.stat();
    if (!opened.isFile() || opened.size < 1 || opened.size > 2_000_000) fail('IMAGE_CANDIDATE_INVALID');
    // 读取大小上限加一字节，防止 stat 之后增长造成无界缓冲。
    const buffer = Buffer.alloc(2_000_001); let total = 0;
    while (total < buffer.length) {
      const { bytesRead } = await handle.read(buffer, total, buffer.length - total, null);
      if (!bytesRead) break;
      total += bytesRead;
    }
    if (total !== opened.size || total > 2_000_000) fail('IMAGE_CANDIDATE_INVALID');
    bytes = buffer.subarray(0, total);
  } catch { fail('IMAGE_CANDIDATE_INVALID'); }
  finally { if (handle) await handle.close().catch(() => {}); }
  let candidate;
  try { candidate = JSON.parse(bytes.toString('utf8')); } catch { fail('IMAGE_CANDIDATE_INVALID'); }
  if (!exactKeys(candidate, ['schemaVersion', 'kind', 'softwareVersion', 'sourceCommit', 'platform', 'buildInputSha256', 'baseImages', 'artifacts'])
    || candidate.schemaVersion !== 1 || candidate.kind !== 'lexiflow-image-candidate'
    || typeof candidate.softwareVersion !== 'string' || !/^(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})$/.test(candidate.softwareVersion)
    || typeof candidate.sourceCommit !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(candidate.sourceCommit)
    || !['linux/amd64', 'linux/arm64'].includes(candidate.platform) || typeof candidate.buildInputSha256 !== 'string' || !hex.test(candidate.buildInputSha256)
    || !Array.isArray(candidate.baseImages) || candidate.baseImages.length !== 1
    || !Array.isArray(candidate.artifacts) || candidate.artifacts.length !== 2) fail('IMAGE_CANDIDATE_INVALID');
  const expectedPlatforms = ['linux/amd64', 'linux/arm64'];
  const bases = new Map();
  for (const base of candidate.baseImages) {
    if (!exactKeys(base, ['platform', 'javaRuntime', 'postgresRuntime']) || !expectedPlatforms.includes(base.platform) || bases.has(base.platform)) fail('IMAGE_CANDIDATE_INVALID');
    for (const runtime of [base.javaRuntime, base.postgresRuntime]) {
      if (!exactKeys(runtime, ['reference', 'imageId']) || !validBaseReference(runtime.reference)
        || !/^sha256:[a-f0-9]{64}$/.test(runtime.imageId)) fail('IMAGE_CANDIDATE_INVALID');
    }
    bases.set(base.platform, base);
  }
  if (bases.size !== 1 || !bases.has(candidate.platform)) fail('IMAGE_CANDIDATE_INVALID');
  const seen = new Set();
  for (const record of candidate.artifacts) {
    if (!exactKeys(record, ['role', 'platform', 'path', 'bytes', 'sha256', 'imageDigest'])
      || !['api-image', 'postgres-image'].includes(record.role) || record.platform !== candidate.platform
      || !Number.isSafeInteger(record.bytes) || record.bytes < 1 || record.bytes > 2 ** 40
      || typeof record.sha256 !== 'string' || !hex.test(record.sha256)
      || typeof record.imageDigest !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(record.imageDigest)) fail('IMAGE_CANDIDATE_INVALID');
    const filename = record.role === 'api-image' ? 'api-image.tar' : 'postgres-image.tar';
    const expectedPath = `images/${record.platform.replace('/', '-')}/${filename}`;
    if (record.path !== expectedPath || seen.has(`${record.role}:${record.platform}`)) fail('IMAGE_CANDIDATE_INVALID');
    seen.add(`${record.role}:${record.platform}`);
  }
  for (const role of ['api-image', 'postgres-image']) if (!seen.has(`${role}:${candidate.platform}`)) fail('IMAGE_CANDIDATE_INVALID');
  return { candidate, bytes, sha256: createHash('sha256').update(bytes).digest('hex') };
}

async function copyStream(sourceRoot, relative, target, bytesExpected, shaExpected) {
  validateRelativePath(relative);
  const source = path.join(sourceRoot, ...relative.split('/'));
  let cursor = sourceRoot; let info;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part);
    info = await lstat(cursor).catch(() => null);
    if (!info || info.isSymbolicLink()) fail('ARTIFACT_INPUT_INVALID');
  }
  if (!info.isFile() || info.size !== bytesExpected) fail('ARTIFACT_INPUT_INVALID');
  await mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
  const digest = hash(); let bytes = 0;
  const meter = new Transform({ transform(chunk, _encoding, callback) {
    bytes += chunk.length;
    if (bytes > bytesExpected) { callback(new Error('ARTIFACT_DIGEST_MISMATCH')); return; }
    digest.update(chunk); callback(null, chunk);
  } });
  try {
    await pipeline(createReadStream(source, { flags: constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) }), meter, createWriteStream(target, { flags: 'wx', mode: 0o600 }));
  } catch (error) {
    if (error.message === 'ARTIFACT_DIGEST_MISMATCH') throw error;
    fail('ARTIFACT_COPY_FAILED');
  }
  if (bytes !== bytesExpected || digest.digest('hex') !== shaExpected) fail('ARTIFACT_DIGEST_MISMATCH');
}

async function snapshot(candidate, imageDirectory, artifactRoot, descriptor) {
  const current = await readImageCandidate(imageDirectory);
  if (!current.bytes.equals(candidate.bytes)) fail('IMAGE_CANDIDATE_CHANGED');
  for (const artifact of descriptor.artifacts) {
    if (artifact.role === 'api-image' || artifact.role === 'postgres-image') {
      if (artifact.platform !== candidate.candidate.platform) continue;
    }
    const root = artifact.role === 'api-image' || artifact.role === 'postgres-image' ? imageDirectory : artifactRoot;
    await verifyContainedFile(root, artifact.path, artifact.bytes, artifact.sha256);
  }
}

export async function assembleReleaseCandidate(input) {
  if (!exactKeys(input, ['repoRoot', 'imageCandidates', 'artifactRoot', 'descriptor', 'outputParent'])
    || !Array.isArray(input.imageCandidates) || input.imageCandidates.length < 1 || input.imageCandidates.length > 2) fail('ARGUMENTS_INVALID');
  const { repoRoot, imageCandidates, artifactRoot, outputParent } = input;
  let descriptor;
  try { descriptor = structuredClone(input.descriptor); } catch { fail('DESCRIPTOR_INVALID'); }
  for (const value of [repoRoot, artifactRoot, outputParent]) {
    if (typeof value !== 'string' || !path.isAbsolute(value) || /[\u0000-\u001f\u007f]/.test(value)) fail('INPUT_PATH_INVALID');
  }
  const repo = path.resolve(repoRoot); const artifacts = path.resolve(artifactRoot); const parent = path.resolve(outputParent);
  for (const value of [repo, artifacts, parent]) await noSymlinkPath(value);
  for (const value of [repo, artifacts, parent]) {
    const info = await lstat(value).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) fail('INPUT_PATH_INVALID');
  }
  const roots = [repo, artifacts];
  for (const root of roots) if (within(root, parent) || within(parent, root)) fail('OUTPUT_PARENT_INVALID');
  let source;
  try { source = checkReleaseSource(repo); } catch { fail('RELEASE_SOURCE_REJECTED'); }
  const byPlatform = new Map();
  for (const item of imageCandidates) {
    if (!exactKeys(item, ['directory', 'sha256']) || typeof item.directory !== 'string' || !path.isAbsolute(item.directory)
      || /[\u0000-\u001f\u007f]/.test(item.directory) || typeof item.sha256 !== 'string' || !hex.test(item.sha256)) fail('IMAGE_CANDIDATE_INVALID');
    const directory = path.resolve(item.directory);
    await noSymlinkPath(directory);
    const info = await lstat(directory).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink()) fail('IMAGE_CANDIDATE_INVALID');
    if (within(directory, parent) || within(parent, directory)) fail('OUTPUT_PARENT_INVALID');
    const imageCandidate = await readImageCandidate(directory);
    if (imageCandidate.sha256 !== item.sha256) fail('IMAGE_CANDIDATE_DIGEST_MISMATCH');
    const platform = imageCandidate.candidate.platform;
    if (byPlatform.has(platform)) fail('IMAGE_CANDIDATE_INVALID');
    byPlatform.set(platform, { directory, imageCandidate });
  }
  const allPlatforms = ['linux/amd64', 'linux/arm64'];
  if (!descriptor || !Array.isArray(descriptor.platforms) || descriptor.platforms.length < 1 || descriptor.platforms.length > 2
    || descriptor.platforms.some((platform) => !allPlatforms.includes(platform))
    || new Set(descriptor.platforms).size !== descriptor.platforms.length) fail('DESCRIPTOR_INVALID');
  const expectedPlatforms = allPlatforms.filter((platform) => descriptor.platforms.includes(platform));
  if (byPlatform.size !== expectedPlatforms.length || expectedPlatforms.some((platform) => !byPlatform.has(platform))) fail('IMAGE_CANDIDATE_INVALID');
  if (imageCandidates.some(({ directory }) => imageCandidates.filter((other) => other.directory === directory).length > 1)) fail('IMAGE_CANDIDATE_INVALID');
  const sortedCandidates = expectedPlatforms.map((platform) => byPlatform.get(platform));
  const imageCandidatesByPlatform = sortedCandidates.map(({ directory, imageCandidate }) => ({ directory, imageCandidate }));
  const common = sortedCandidates[0].imageCandidate.candidate;
  if (sortedCandidates.some(({ imageCandidate }) => imageCandidate.candidate.softwareVersion !== source.softwareVersion
    || imageCandidate.candidate.sourceCommit !== source.sourceCommit || imageCandidate.candidate.buildInputSha256 !== common.buildInputSha256)) fail('SOURCE_IDENTITY_MISMATCH');
  if (!Array.isArray(descriptor.artifacts) || !Array.isArray(descriptor.licenses)
    || descriptor.artifacts.some((item) => !item || item?.role === 'runtime-entry' || item?.path === 'lexiflow.sh')) fail('DESCRIPTOR_INVALID');
  if (descriptor.softwareVersion !== source.softwareVersion || descriptor.sourceCommit !== source.sourceCommit) fail('SOURCE_IDENTITY_MISMATCH');
  const imageRecords = new Map(imageCandidatesByPlatform.flatMap(({ imageCandidate }) => imageCandidate.candidate.artifacts.map((record) => [`${record.role}:${record.platform}`, record])));
  for (const record of descriptor.artifacts.filter((item) => item.role === 'api-image' || item.role === 'postgres-image')) {
    const candidateRecord = imageRecords.get(`${record.role}:${record.platform}`);
    if (!candidateRecord || !exactKeys(record, ['role', 'path', 'bytes', 'sha256', 'licenseIds', 'platform', 'imageDigest'])
      || record.path !== candidateRecord.path || record.bytes !== candidateRecord.bytes || record.sha256 !== candidateRecord.sha256
      || record.imageDigest !== candidateRecord.imageDigest) fail('IMAGE_DESCRIPTOR_MISMATCH');
  }
  if (descriptor.artifacts.filter((item) => item.role === 'api-image' || item.role === 'postgres-image').length !== 2 * expectedPlatforms.length) fail('IMAGE_DESCRIPTOR_MISMATCH');
  const licenses = descriptor.licenses.find((item) => item?.component === 'LexiFlow');
  if (!licenses) fail('RUNTIME_ENTRY_LICENSE_MISSING');
  const provisional = { role: 'runtime-entry', path: 'lexiflow.sh', bytes: 1, sha256: '0'.repeat(64), licenseIds: [licenses.id] };
  const provisionalDescriptor = { ...descriptor, artifacts: [...descriptor.artifacts, provisional] };
  try { createManifest(provisionalDescriptor, source); } catch { fail('DESCRIPTOR_INVALID'); }
  for (const { directory, imageCandidate } of imageCandidatesByPlatform) await snapshot(imageCandidate, directory, artifacts, descriptor);

  const candidateDirectory = await mkdtemp(path.join(parent, '.lexiflow-release-candidate-')).catch(() => fail('CANDIDATE_CREATE_FAILED'));
  let retained = false;
  try {
    await chmod(candidateDirectory, 0o700);
    const work = path.join(candidateDirectory, 'work'); const staging = path.join(work, 'artifacts');
    await mkdir(staging, { recursive: true, mode: 0o700 });
    for (const artifact of descriptor.artifacts) {
      const root = artifact.role === 'api-image' || artifact.role === 'postgres-image'
        ? byPlatform.get(artifact.platform)?.directory : artifacts;
      if (!root) fail('IMAGE_DESCRIPTOR_MISMATCH');
      await copyStream(root, artifact.path, path.join(staging, ...artifact.path.split('/')), artifact.bytes, artifact.sha256);
    }
    for (const template of ['lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh']) {
      const file = path.join(repo, 'ops/release', template);
      await noSymlinkPath(file);
      if (!(await lstat(file)).isFile()) fail('INPUT_PATH_INVALID');
    }
    const generated = await generateLifecycleEntry({ repoRoot: repo, descriptor, artifactRoot: staging });
    const completeDescriptor = { ...descriptor, artifacts: [...descriptor.artifacts, generated.artifact] };
    const descriptorFile = path.join(work, 'descriptor.json');
    await writeFile(descriptorFile, `${JSON.stringify(completeDescriptor, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    const payload = path.join(candidateDirectory, 'payload');
    const manifest = await packageManifest({ repoRoot: repo, descriptorFile, artifactRoot: staging, outputDirectory: payload });
    const manifestText = await readFile(path.join(payload, 'manifest.json'), 'utf8');
    const manifestSha256 = createHash('sha256').update(manifestText).digest('hex');
    for (const { directory, imageCandidate } of imageCandidatesByPlatform) await snapshot(imageCandidate, directory, artifacts, descriptor);
    let currentSource;
    try { currentSource = checkReleaseSource(repo); } catch { fail('RELEASE_SOURCE_CHANGED'); }
    if (currentSource.sourceCommit !== source.sourceCommit || currentSource.softwareVersion !== source.softwareVersion) fail('RELEASE_SOURCE_CHANGED');
    await rm(work, { recursive: true, force: false });
    const candidate = { schemaVersion: 1, kind: 'lexiflow-release-candidate', softwareVersion: source.softwareVersion,
      sourceCommit: source.sourceCommit, manifestPath: 'payload/manifest.json', manifestSha256,
      buildInputSha256: common.buildInputSha256,
      imageCandidates: expectedPlatforms.map((platform) => ({ platform, sha256: byPlatform.get(platform).imageCandidate.sha256 })) };
    const markerText = `${JSON.stringify(candidate, null, 2)}\n`;
    const pending = path.join(candidateDirectory, `.candidate-${randomUUID()}.tmp`);
    await writeFile(pending, markerText, { flag: 'wx', mode: 0o600 });
    try { await link(pending, path.join(candidateDirectory, 'candidate.json')); }
    catch { fail('CANDIDATE_PUBLISH_FAILED'); }
    await unlink(pending);
    retained = true;
    return { candidateDirectory, candidate, manifest };
  } catch (error) {
    // 文件系统原始错误可能包含本机路径，公共失败只返回固定原因。
    if (error instanceof Error && /^[A-Z][A-Z0-9_]+$/.test(error.message)) throw error;
    fail('CANDIDATE_ASSEMBLY_FAILED');
  } finally {
    if (!retained) await rm(candidateDirectory, { recursive: true, force: true }).catch(() => {});
  }
}
