// 固定发行候选到本机 Podman 安装目录的受限适配；不执行候选内脚本。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyReleaseCandidate } from '../../release/integrity/release-candidate-verifier.mjs';
import { readExtensionFiles } from '../../release/integrity/extension-archive-integrity.mjs';
import { versionedImageReference, deploymentImageTag } from '../images/versioned-images.mjs';
import { removeVersionedImage } from '../images/versioned-images.mjs';
import { publishExtensionArchive, materializeExtensionTree } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';

const fail = code => { throw new Error(code); };
export function validateCandidateRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join(',') !== 'candidateDirectory,candidateSha256'
    || typeof value.candidateDirectory !== 'string' || !path.isAbsolute(value.candidateDirectory)
    || typeof value.candidateSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.candidateSha256)) fail('ARGUMENTS_INVALID');
}

export async function readCandidate(request) {
  validateCandidateRequest(request);
  const verified = await verifyReleaseCandidate(request);
  const artifacts = Object.fromEntries(verified.manifest.artifacts.map(item => [item.role === 'api-image' || item.role === 'postgres-image' ? `${item.role}:${item.platform}` : item.role, item]));
  if (!verified.manifest.platforms.includes('linux/arm64')) fail('CANDIDATE_PLATFORM_UNSUPPORTED');
  for (const role of ['compose', 'dataset', 'extension', 'sql']) if (!artifacts[role]) fail('CANDIDATE_ARTIFACT_MISSING');
  for (const role of ['api-image:linux/arm64', 'postgres-image:linux/arm64']) if (!artifacts[role]) fail('CANDIDATE_ARTIFACT_MISSING');
  return { ...verified, artifacts, root: path.resolve(request.candidateDirectory) };
}

export async function installCandidateArtifacts(candidate, { run, installDir, archiveRoot = installDir, apiPort, includeApi = true, includePostgres = true, baseVersion, installationId }) {
  if (apiPort !== 18080) fail('CANDIDATE_API_PORT_FIXED');
  const deploymentVersion = deploymentImageTag(baseVersion);
  if (typeof installationId !== 'string' || !/^[a-f0-9]{32}$/.test(installationId)) fail('CANDIDATE_INSTALLATION_REQUIRED');
  if (baseVersion !== candidate.buildIdentity?.baseVersion) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
  const recheck = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (recheck.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
  const api = candidate.artifacts['api-image:linux/arm64'];
  const pg = candidate.artifacts['postgres-image:linux/arm64'];
  const loadedImages = new Map();
  const bytesPath = item => path.join(candidate.root, 'payload', ...item.path.split('/'));
  const supported = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../docker/compose.yaml'));
  const composeFd = fs.openSync(bytesPath(candidate.artifacts.compose), fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  try {
    const info = fs.fstatSync(composeFd);
    if (!info.isFile() || info.size !== supported.length || info.size !== candidate.artifacts.compose.bytes
        || !fs.readFileSync(composeFd).equals(supported)) fail('CANDIDATE_COMPOSE_UNSUPPORTED');
  } finally { fs.closeSync(composeFd); }
  const files = await readExtensionFiles(bytesPath(candidate.artifacts.extension), candidate.artifacts.extension.sha256);
  const extensionItem = candidate.artifacts.extension;
  const extensionFilename = `lexiflow-extension-${candidate.buildIdentity.softwareVersion}.zip`;
  const extensionRelative = `extension-artifacts/${candidate.buildIdentity.buildId}/${extensionFilename}`;
  const extensionArchive = path.join(archiveRoot, extensionRelative);
  const sourceArchive = bytesPath(extensionItem), archiveBytes = fs.readFileSync(sourceArchive);
  if (archiveBytes.length !== extensionItem.bytes) fail('CANDIDATE_ARTIFACT_INVALID');
  await publishExtensionArchive(archiveBytes, `${extensionItem.sha256}  ${extensionFilename}\n`, path.dirname(extensionArchive), extensionFilename);
  const extensionArchiveDescriptor = { path: extensionRelative, bytes: extensionItem.bytes,
    sha256: extensionItem.sha256, buildIdentity: candidate.buildIdentity };
  for (const item of [includeApi ? api : null, includePostgres ? pg : null].filter(Boolean)) {
    const archive = bytesPath(item);
    if (item === pg) await removeVersionedImage({ run, role: 'postgres', baseVersion, installationId });
    await run('podman', ['load', '-i', archive]);
    // Inspect only the digest bound by the verified manifest; never enumerate user images.
    const inspected = JSON.parse(await run('podman', ['image', 'inspect', item.imageDigest], { capture: true }));
    if (!Array.isArray(inspected) || inspected.length !== 1) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
    const data = inspected[0], identity = candidate.buildIdentity;
    const topLabels = data?.Labels, configLabels = data?.Config?.Labels;
    if (topLabels && configLabels && ['org.opencontainers.image.version', 'org.opencontainers.image.revision', 'io.lexiflow.build-id', 'io.lexiflow.source-sha256', 'io.lexiflow.deployment-version', 'io.lexiflow.image-role']
      .some(key => topLabels[key] !== configLabels[key])) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
    const labels = topLabels ?? configLabels ?? {};
    const actualId = String(data?.Id || '').replace(/^sha256:/, '');
    if (actualId !== item.imageDigest.slice(7)
        || data?.Os !== 'linux' || data?.Architecture !== 'arm64'
        || labels['org.opencontainers.image.version'] !== identity.softwareVersion
        || labels['org.opencontainers.image.revision'] !== identity.sourceCommit
        || labels['io.lexiflow.build-id'] !== identity.buildId
        || labels['io.lexiflow.source-sha256'] !== identity.sourceSha256
        || labels['io.lexiflow.deployment-version'] !== deploymentVersion
        || labels['io.lexiflow.image-role'] !== (item.role === 'api-image' ? 'api' : 'postgres')) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
    const role = item.role === 'api-image' ? 'api' : 'postgres';
    const reference = versionedImageReference(role, baseVersion);
    await run('podman', ['tag', item.imageDigest, reference]);
    loadedImages.set(item.role, data.Id);
  }
  materializeExtensionTree(path.join(installDir, 'extension'), files);
  const end = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (end.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
  return { apiImage: loadedImages.get('api-image'), postgresImage: loadedImages.get('postgres-image'), datasetPath: bytesPath(candidate.artifacts.dataset), sqlPath: bytesPath(candidate.artifacts.sql), extensionArchive: extensionArchiveDescriptor };
}
