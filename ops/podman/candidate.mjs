// 固定发行候选到本机 Podman 安装目录的受限适配；不执行候选内脚本。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { verifyReleaseCandidate } from '../release/verified-candidate.mjs';
import { readExtensionFiles } from '../release/archive-identity.mjs';
import { writePreparedTree } from './prepare-workspace.mjs';

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fail = code => { throw new Error(code); };
function rejectSymlinkParents(target) {
  let cursor = path.parse(target).root;
  for (const part of target.slice(cursor.length).split(path.sep).filter(Boolean).slice(0, -1)) {
    cursor = path.join(cursor, part);
    try {
      const info = fs.lstatSync(cursor);
      if (!info.isDirectory() || info.isSymbolicLink()) fail('CANDIDATE_TARGET_DRIFT');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}
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

export async function installCandidateArtifacts(candidate, { run, installDir, apiPort, includePostgres = true, persistPlan = null }) {
  if (apiPort !== 18080) fail('CANDIDATE_API_PORT_FIXED');
  const recheck = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (recheck.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
  const api = candidate.artifacts['api-image:linux/arm64'];
  const pg = candidate.artifacts['postgres-image:linux/arm64'];
  const loadedImages = new Map();
  const bytesPath = item => path.join(candidate.root, 'payload', ...item.path.split('/'));
  const supported = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../docker/compose.yaml'));
  const composeFd = fs.openSync(bytesPath(candidate.artifacts.compose), fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  try {
    const info = fs.fstatSync(composeFd);
    if (!info.isFile() || info.size !== supported.length || info.size !== candidate.artifacts.compose.bytes
        || !fs.readFileSync(composeFd).equals(supported)) fail('CANDIDATE_COMPOSE_UNSUPPORTED');
  } finally { fs.closeSync(composeFd); }
  const files = await readExtensionFiles(bytesPath(candidate.artifacts.extension), candidate.artifacts.extension.sha256);
  for (const item of (includePostgres ? [api, pg] : [api])) {
    const archive = bytesPath(item);
    await run('podman', ['load', '-i', archive]);
    // Inspect only the digest bound by the verified manifest; never enumerate user images.
    const inspected = JSON.parse(await run('podman', ['image', 'inspect', item.imageDigest], { capture: true }));
    if (!Array.isArray(inspected) || inspected.length !== 1) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
    const data = inspected[0], labels = data?.Config?.Labels || {}, identity = candidate.buildIdentity;
    const actualId = String(data?.Id || '').replace(/^sha256:/, '');
    if (actualId !== item.imageDigest.slice(7)
        || data?.Os !== 'linux' || data?.Architecture !== 'arm64'
        || labels['org.opencontainers.image.version'] !== identity.softwareVersion
        || labels['org.opencontainers.image.revision'] !== identity.sourceCommit
        || labels['io.lexiflow.build-id'] !== identity.buildId
        || labels['io.lexiflow.source-sha256'] !== identity.sourceSha256) fail('CANDIDATE_IMAGE_IDENTITY_MISMATCH');
    loadedImages.set(item.role, data.Id);
  }
  const stage = path.join(installDir, 'extension');
  try {
    if (persistPlan) {
      await writePreparedTree(installDir, [...files].map(([name, bytes]) => ({ path: `extension/${name}`, bytes })), persistPlan);
    } else {
    if (fs.existsSync(stage)) {
      const observed = new Map(), observedDirs = new Set();
      const expectedDirs = new Set();
      for (const name of files.keys()) for (let i = 1; i < name.split('/').length; i++) expectedDirs.add(name.split('/').slice(0, i).join('/'));
      function visit(relative = '') {
        const directory = relative ? path.join(stage, ...relative.split('/')) : stage;
        const info = fs.lstatSync(directory); if (info.isSymbolicLink() || !info.isDirectory()) fail('EXTENSION_TARGET_EXISTS');
        if (relative) { if (!expectedDirs.has(relative)) fail('EXTENSION_TARGET_EXISTS'); observedDirs.add(relative); }
        for (const name of fs.readdirSync(directory).sort()) {
          const child = relative ? `${relative}/${name}` : name, childPath = path.join(stage, ...child.split('/')), childInfo = fs.lstatSync(childPath);
          if (childInfo.isSymbolicLink()) fail('EXTENSION_TARGET_EXISTS');
          if (childInfo.isDirectory()) visit(child);
          else if (childInfo.isFile()) {
            const expected = files.get(child);
            if (!expected || childInfo.size > expected.length) fail('EXTENSION_TARGET_EXISTS');
            const fd = fs.openSync(childPath, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
            let bytes;
            try {
              const opened = fs.fstatSync(fd);
              if (!opened.isFile() || opened.dev !== childInfo.dev || opened.ino !== childInfo.ino || opened.size > expected.length) fail('EXTENSION_TARGET_EXISTS');
              bytes = fs.readFileSync(fd);
              const after = fs.fstatSync(fd);
              if (after.size !== opened.size || after.mtimeMs !== opened.mtimeMs || after.ctimeMs !== opened.ctimeMs) fail('EXTENSION_TARGET_EXISTS');
            } finally { fs.closeSync(fd); }
            if (bytes.length > expected.length || !expected.subarray(0, bytes.length).equals(bytes)) fail('EXTENSION_TARGET_EXISTS');
            observed.set(child, bytes);
          }
          else fail('EXTENSION_TARGET_EXISTS');
        }
      }
      visit();
      const complete = observed.size === files.size && [...files].every(([name, bytes]) => observed.has(name) && observed.get(name).equals(bytes));
      if (!complete) {
        for (const name of observed.keys()) fs.unlinkSync(path.join(stage, ...name.split('/')));
        for (const name of [...observedDirs].sort((a, b) => b.split('/').length - a.split('/').length)) fs.rmdirSync(path.join(stage, ...name.split('/')));
        fs.rmdirSync(stage);
        fs.mkdirSync(stage, { mode: 0o700 });
        for (const [name, content] of files) {
          const target = path.join(stage, ...name.split('/'));
          fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
          const fd = fs.openSync(target, 'wx', 0o600); try { fs.writeFileSync(fd, content); } finally { fs.closeSync(fd); }
        }
      }
    } else {
      fs.mkdirSync(stage, { mode: 0o700 });
      for (const [name, content] of files) {
        const target = path.join(stage, ...name.split('/'));
        fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
        const fd = fs.openSync(target, 'wx', 0o600);
        try { fs.writeFileSync(fd, content); } finally { fs.closeSync(fd); }
      }
    }
    }
  } catch (error) { throw error; }
  const end = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (end.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
  return { apiImage: loadedImages.get('api-image'), postgresImage: loadedImages.get('postgres-image'), datasetPath: bytesPath(candidate.artifacts.dataset), sqlPath: bytesPath(candidate.artifacts.sql) };
}

export async function copyCandidateArtifact(candidate, role, target) {
  const item = candidate.artifacts[role];
  if (!item) fail('CANDIDATE_ARTIFACT_MISSING');
  const before = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (before.manifestSha256 !== candidate.manifestSha256) fail('CANDIDATE_CHANGED');
  const source = path.join(candidate.root, 'payload', ...item.path.split('/'));
  const srcFd = fs.openSync(source, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  let targetFd;
  try {
    const inputStat = fs.fstatSync(srcFd);
    if (!inputStat.isFile() || inputStat.size !== item.bytes) fail('CANDIDATE_CHANGED');
    rejectSymlinkParents(target);
    fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
    let existingSize = 0;
    if (fs.existsSync(target)) {
      const existing = fs.lstatSync(target);
      if (!existing.isFile() || existing.isSymbolicLink() || existing.size > item.bytes) fail('CANDIDATE_TARGET_DRIFT');
      existingSize = existing.size;
      targetFd = fs.openSync(target, fs.constants.O_RDWR | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
      const opened = fs.fstatSync(targetFd);
      if (!opened.isFile() || opened.dev !== existing.dev || opened.ino !== existing.ino || opened.size !== existing.size) fail('CANDIDATE_TARGET_DRIFT');
      const expected = Buffer.alloc(64 * 1024), observed = Buffer.alloc(64 * 1024);
      for (let offset = 0; offset < existingSize;) {
        const count = Math.min(expected.length, existingSize - offset);
        const a = fs.readSync(srcFd, expected, 0, count, offset), b = fs.readSync(targetFd, observed, 0, count, offset);
        if (a !== count || b !== count || !expected.subarray(0, count).equals(observed.subarray(0, count))) fail('CANDIDATE_TARGET_DRIFT');
        offset += count;
      }
      fs.ftruncateSync(targetFd, 0);
    } else targetFd = fs.openSync(target, 'wx', 0o600);
    const digest = crypto.createHash('sha256'), buffer = Buffer.alloc(1024 * 1024);
    let count = 0;
    while (true) {
      const size = fs.readSync(srcFd, buffer, 0, buffer.length, null); if (!size) break;
      count += size; if (count > item.bytes) fail('CANDIDATE_CHANGED');
      digest.update(buffer.subarray(0, size));
      let written = 0; while (written < size) { const amount = fs.writeSync(targetFd, buffer, written, size - written); if (amount <= 0) fail('CANDIDATE_COPY_FAILED'); written += amount; }
    }
    fs.fsyncSync(targetFd);
    const afterStat = fs.fstatSync(srcFd);
    if (count !== item.bytes || digest.digest('hex') !== item.sha256 || inputStat.dev !== afterStat.dev || inputStat.ino !== afterStat.ino || inputStat.size !== afterStat.size || inputStat.mtimeMs !== afterStat.mtimeMs || inputStat.ctimeMs !== afterStat.ctimeMs) fail('CANDIDATE_CHANGED');
  } finally { try { if (targetFd !== undefined) fs.closeSync(targetFd); } finally { fs.closeSync(srcFd); } }
  const after = await verifyReleaseCandidate({ candidateDirectory: candidate.root, candidateSha256: candidate.candidateSha256 });
  if (after.manifestSha256 !== candidate.manifestSha256) {
    fs.unlinkSync(target); fail('CANDIDATE_CHANGED');
  }
  return item.sha256;
}

export function sha256(value) { return hash(value); }
