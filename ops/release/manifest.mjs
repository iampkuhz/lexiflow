import { createHash } from 'node:crypto';
import { constants, createReadStream } from 'node:fs';
import { lstat } from 'node:fs/promises';
import { isIP } from 'node:net';
import path from 'node:path';
import { checkReleaseSource, resolveBuildIdentity, validateBuildIdentity, assertBuildIdentityMatches } from './version.mjs';
import { verifyExtensionIdentity } from './embedded-identity.mjs';

const roles = new Set(['api-image', 'postgres-image', 'compose', 'dataset', 'extension', 'sql', 'license', 'runtime-entry']);
const platforms = new Set(['linux/amd64', 'linux/arm64']);
const hex = /^[a-f0-9]{64}$/;
const ids = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join('\0') === [...expected].sort().join('\0');
const bad = (code) => { throw new Error(code); };
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const boundedText = (value, max = 512) => typeof value === 'string' && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);

export function validateRelativePath(value) {
  if (typeof value !== 'string' || value.length > 512 || !value || value.includes('\\') || value.startsWith('/') || /^[A-Za-z]:/.test(value) || /[%\u0000-\u001f\u007f]/.test(value)) bad('INVALID_PATH');
  const parts = value.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..' || part.length > 128)) bad('INVALID_PATH');
  return value;
}

export async function verifyContainedFile(root, relative, expectedBytes, expectedSha256) {
  validateRelativePath(relative);
  const rootInfo = await lstat(root).catch(() => null);
  if (!rootInfo?.isDirectory() || rootInfo.isSymbolicLink()) bad('INVALID_ARTIFACT_ROOT');
  let cursor = root;
  const parts = relative.split('/');
  let finalInfo;
  for (const [index, part] of parts.entries()) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch(() => null);
    if (!info || info.isSymbolicLink()) bad('ARTIFACT_MISSING_OR_SYMLINK');
    if (index < parts.length - 1 ? !info.isDirectory() : !info.isFile()) bad('ARTIFACT_NOT_REGULAR_FILE');
    if (index === parts.length - 1) finalInfo = info;
  }
  if (finalInfo.size !== expectedBytes) bad('ARTIFACT_DIGEST_MISMATCH');
  const digest = createHash('sha256'); let bytes = 0;
  try { for await (const chunk of createReadStream(cursor, { flags: constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) })) { bytes += chunk.length; if (bytes > expectedBytes) bad('ARTIFACT_DIGEST_MISMATCH'); digest.update(chunk); } }
  catch (error) { if (error.message === 'ARTIFACT_DIGEST_MISMATCH') throw error; bad('ARTIFACT_MISSING_OR_SYMLINK'); }
  if (bytes !== expectedBytes || digest.digest('hex') !== expectedSha256) bad('ARTIFACT_DIGEST_MISMATCH');
  return { bytes, sha256: expectedSha256 };
}

function validUrl(value) {
  if (!boundedText(value, 2048)) return false;
  try {
    const url = new URL(value); const host = url.hostname.toLowerCase(); const ipHost = host.replace(/^\[|\]$/g, '');
    return url.protocol === 'https:' && !url.username && !url.password && !isIP(ipHost) && host !== 'localhost' && !host.endsWith('.localhost') && !host.endsWith('.local') && !host.endsWith('.internal');
  } catch { return false; }
}

function validateDescriptor(descriptor, source) {
  if (!keys(descriptor, ['schemaVersion', 'buildIdentity', 'softwareVersion', 'sourceCommit', 'apiContract', 'sqlVersion', 'dataset', 'platforms', 'artifacts', 'licenses'])) bad('DESCRIPTOR_SCHEMA');
  if (descriptor.schemaVersion !== 1 || descriptor.softwareVersion !== source.softwareVersion || descriptor.sourceCommit !== source.sourceCommit) bad('SOURCE_IDENTITY_MISMATCH');
  try { validateBuildIdentity(source); assertBuildIdentityMatches(descriptor.buildIdentity, source); if (source.dirty) throw new Error(); } catch { bad('SOURCE_IDENTITY_MISMATCH'); }
  if (typeof descriptor.apiContract !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/.test(descriptor.apiContract) || typeof descriptor.sqlVersion !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/.test(descriptor.sqlVersion)) bad('DESCRIPTOR_SCHEMA');
  const dataset = descriptor.dataset;
  if (!keys(dataset, ['releaseId', 'preparationId', 'ruleId']) || Object.values(dataset).some((v) => typeof v !== 'string' || !ids.test(v))) bad('DESCRIPTOR_SCHEMA');
  if (!Array.isArray(descriptor.platforms) || !descriptor.platforms.length || descriptor.platforms.length > 2 || new Set(descriptor.platforms).size !== descriptor.platforms.length || descriptor.platforms.some((p) => !platforms.has(p))) bad('PLATFORMS_INVALID');
  if (!Array.isArray(descriptor.licenses) || descriptor.licenses.length < 5 || descriptor.licenses.length > 128) bad('LICENSES_INVALID');
  const licenseIds = new Set();
  for (const license of descriptor.licenses) {
    if (!keys(license, ['id', 'component', 'licenseId', 'licenseName', 'sourceUrl', 'noticePath', 'noticeBytes', 'noticeSha256']) || typeof license.id !== 'string' || !ids.test(license.id) || licenseIds.has(license.id) || !boundedText(license.component, 128) || !boundedText(license.licenseId, 128) || !boundedText(license.licenseName, 256) || !validUrl(license.sourceUrl) || !Number.isSafeInteger(license.noticeBytes) || license.noticeBytes < 1 || license.noticeBytes > 2 ** 40 || typeof license.noticeSha256 !== 'string' || !hex.test(license.noticeSha256)) bad('LICENSES_INVALID');
    validateRelativePath(license.noticePath); licenseIds.add(license.id);
  }
  if (!Array.isArray(descriptor.artifacts) || descriptor.artifacts.length < descriptor.platforms.length * 2 + 5 || descriptor.artifacts.length > 512) bad('ARTIFACTS_INVALID');
  const paths = new Set(); const folded = new Set(); const roleKeys = new Set();
  for (const artifact of descriptor.artifacts) {
    const image = artifact && (artifact.role === 'api-image' || artifact.role === 'postgres-image');
    const expected = image ? ['role', 'path', 'bytes', 'sha256', 'licenseIds', 'platform', 'imageDigest'] : ['role', 'path', 'bytes', 'sha256', 'licenseIds', ...(artifact?.role === 'extension' || artifact?.role === 'dataset' ? ['metadata'] : [])];
    if (!keys(artifact, expected) || !roles.has(artifact.role)) bad('ARTIFACTS_INVALID');
    const relative = validateRelativePath(artifact.path); const foldedPath = relative.toLocaleLowerCase('en-US');
    if (paths.has(relative) || folded.has(foldedPath)) bad('ARTIFACT_PATH_CONFLICT'); paths.add(relative); folded.add(foldedPath);
    if (!Number.isSafeInteger(artifact.bytes) || artifact.bytes < 1 || artifact.bytes > 2 ** 40 || typeof artifact.sha256 !== 'string' || !hex.test(artifact.sha256) || !Array.isArray(artifact.licenseIds) || !artifact.licenseIds.length || artifact.licenseIds.some((id) => typeof id !== 'string' || !licenseIds.has(id)) || new Set(artifact.licenseIds).size !== artifact.licenseIds.length) bad('ARTIFACTS_INVALID');
    const roleKey = image ? `${artifact.role}:${artifact.platform}` : artifact.role === 'license' ? `${artifact.role}:${artifact.path}` : artifact.role;
    if (roleKeys.has(roleKey)) bad('ARTIFACT_ROLE_CONFLICT'); roleKeys.add(roleKey);
    if (artifact.role === 'runtime-entry' && artifact.path !== 'lexiflow.sh') bad('RUNTIME_ENTRY_INVALID');
    if (image && (!descriptor.platforms.includes(artifact.platform) || typeof artifact.imageDigest !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(artifact.imageDigest))) bad('IMAGE_INVALID');
    if (artifact.role === 'extension' && (!keys(artifact.metadata, ['softwareVersion', 'sourceCommit']) || artifact.metadata.softwareVersion !== source.softwareVersion || artifact.metadata.sourceCommit !== source.sourceCommit)) bad('EXTENSION_IDENTITY_MISMATCH');
    if (artifact.role === 'dataset' && (!keys(artifact.metadata, ['releaseId', 'preparationId', 'ruleId', 'sqlVersion']) || artifact.metadata.releaseId !== dataset.releaseId || artifact.metadata.preparationId !== dataset.preparationId || artifact.metadata.ruleId !== dataset.ruleId || artifact.metadata.sqlVersion !== descriptor.sqlVersion)) bad('DATASET_IDENTITY_MISMATCH');
  }
  for (const platform of descriptor.platforms) for (const role of ['api-image', 'postgres-image']) if (!roleKeys.has(`${role}:${platform}`)) bad('PLATFORM_ARTIFACT_MISSING');
  for (const role of ['compose', 'dataset', 'extension', 'sql', 'runtime-entry']) if (!roleKeys.has(role)) bad('REQUIRED_ARTIFACT_MISSING');
  for (const license of descriptor.licenses) {
    const record = descriptor.artifacts.find((artifact) => artifact.role === 'license' && artifact.path === license.noticePath && artifact.licenseIds.includes(license.id));
    if (!record) bad('LICENSE_FILE_MISSING');
  }
  const licenseComponents = new Map(descriptor.licenses.map((license) => [license.id, license.component]));
  const requiredAssociation = [
    ['API-runtime', 'api-image'], ['LexiFlow', 'api-image'], ['PostgreSQL', 'postgres-image'],
    ['LexiFlow', 'extension'], ['extension-third-party', 'extension'], ['dataset', 'dataset'], ['LexiFlow', 'runtime-entry']
  ];
  for (const [component, role] of requiredAssociation) {
    // 每个平台的制品独立满足许可要求，同类许可不依赖输入顺序。
    for (const artifact of descriptor.artifacts.filter((item) => item.role === role)) {
      if (!artifact.licenseIds.some((id) => licenseComponents.get(id) === component)) bad('REQUIRED_LICENSE_MISSING');
    }
  }
  return { licenseIds, paths };
}

export function createManifest(descriptor, source) {
  validateDescriptor(descriptor, source);
  const licenses = [];
  for (const item of descriptor.licenses) {
    const licenseArtifact = descriptor.artifacts.find((artifact) => artifact.role === 'license' && artifact.path === item.noticePath && artifact.licenseIds.includes(item.id));
    if (!licenseArtifact) bad('LICENSE_FILE_MISSING');
    if (licenseArtifact.bytes !== item.noticeBytes || licenseArtifact.sha256 !== item.noticeSha256) bad('LICENSE_NOTICE_MISMATCH');
    licenses.push({ ...item });
  }
  const canonicalDataset = { releaseId: descriptor.dataset.releaseId, preparationId: descriptor.dataset.preparationId, ruleId: descriptor.dataset.ruleId };
  const artifacts = descriptor.artifacts.map((item) => {
    const base = { role: item.role, path: item.path, bytes: item.bytes, sha256: item.sha256, licenseIds: [...item.licenseIds].sort() };
    if (item.role === 'api-image' || item.role === 'postgres-image') return { ...base, platform: item.platform, imageDigest: item.imageDigest };
    if (item.role === 'extension') return { ...base, metadata: { softwareVersion: item.metadata.softwareVersion, sourceCommit: item.metadata.sourceCommit } };
    if (item.role === 'dataset') return { ...base, metadata: { releaseId: item.metadata.releaseId, preparationId: item.metadata.preparationId, ruleId: item.metadata.ruleId, sqlVersion: item.metadata.sqlVersion } };
    return base;
  }).sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  licenses.sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const canonicalLicenses = licenses.map((item) => ({ id: item.id, component: item.component, licenseId: item.licenseId, licenseName: item.licenseName, sourceUrl: item.sourceUrl, noticePath: item.noticePath, noticeBytes: item.noticeBytes, noticeSha256: item.noticeSha256 }));
  return { schemaVersion: 1, buildIdentity: { ...source }, softwareVersion: source.softwareVersion, sourceCommit: source.sourceCommit, apiContract: descriptor.apiContract, sqlVersion: descriptor.sqlVersion, dataset: canonicalDataset, platforms: [...descriptor.platforms].sort(), artifacts, licenses: canonicalLicenses };
}

export async function buildManifest({ repoRoot, descriptor, artifactRoot }) {
  if (typeof artifactRoot !== 'string' || !artifactRoot || /[\u0000\r\n]/.test(artifactRoot)) bad('INVALID_ARTIFACT_ROOT');
  let source;
  try { checkReleaseSource(repoRoot); source = resolveBuildIdentity(repoRoot); } catch { bad('RELEASE_SOURCE_REJECTED'); }
  const manifest = createManifest(descriptor, source);
  for (const item of manifest.artifacts) await verifyContainedFile(artifactRoot, item.path, item.bytes, item.sha256);
  const extension = manifest.artifacts.find(item => item.role === 'extension');
  await verifyExtensionIdentity(path.join(artifactRoot, extension.path), source);
  await verifyContainedFile(artifactRoot, extension.path, extension.bytes, extension.sha256);
  try { checkReleaseSource(repoRoot); assertBuildIdentityMatches(resolveBuildIdentity(repoRoot), source); } catch { bad('RELEASE_SOURCE_CHANGED'); }
  return manifest;
}

export function serializeManifest(manifest) { return `${JSON.stringify(manifest, null, 2)}\n`; }
export function manifestSha256(text) { return `${sha(Buffer.from(text, 'utf8'))}  manifest.json\n`; }
