import { constants, createReadStream } from 'node:fs';
import { lstat, open, opendir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { createManifest, serializeManifest, validateRelativePath } from './manifest.mjs';
import { validateBuildIdentity, assertBuildIdentityMatches } from './version.mjs';
import { verifyExtensionIdentity } from './embedded-identity.mjs';

const fail = (code = 'CANDIDATE_INVALID') => { throw new Error(code); };
const hex = /^[a-f0-9]{64}$/;
const maxMetadata = 2_000_000;
// Manifest permits 512 artifact paths of at most 512 characters. Even with no shared
// prefixes, those paths cannot require more than 512 * 256 directories.
const maxEntries = 512 * 257 + 4;
const maxDepth = 258;
const sha = (value) => createHash('sha256').update(value).digest('hex');
const publicErrors = new Set(['ARGUMENTS_INVALID', 'CANDIDATE_INVALID', 'CANDIDATE_CHANGED',
  'CANDIDATE_FILE_TYPE_INVALID', 'CANDIDATE_METADATA_INVALID', 'CANDIDATE_CANONICAL_INVALID',
  'CANDIDATE_PATH_CONFLICT', 'CANDIDATE_TREE_INVALID', 'CANDIDATE_DIGEST_MISMATCH',
  'CANDIDATE_SCHEMA_INVALID', 'CANDIDATE_IDENTITY_MISMATCH', 'MANIFEST_DIGEST_MISMATCH',
  'MANIFEST_CANONICAL_INVALID', 'MANIFEST_SIDECAR_INVALID', 'IMAGE_CANDIDATE_MISMATCH',
  'ARTIFACT_DIGEST_MISMATCH', 'INVALID_BUILD_IDENTITY', 'BUILD_IDENTITY_MISMATCH',
  'EXTENSION_IDENTITY_MISMATCH']);
const keys = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0');
const stamp = (s) => [s.dev, s.ino, s.mode, s.size, s.mtimeMs, s.ctimeMs, s.nlink].join(':');
const same = (a, b) => stamp(a) === stamp(b);
// Ancestors outside the candidate are identity fences, not content snapshots.
// Their mtime/ctime/size change when unrelated siblings are created.
const directoryIdentity = (s) => [s.dev, s.ino, s.mode].join(':');
const pathname = (root, relative) => relative ? path.join(root, ...relative.split('/')) : root;

async function pathChain(root, relative = '') {
  const absolute = pathname(root, relative);
  const chain = [];
  let cursor = path.parse(absolute).root;
  const parts = absolute.slice(cursor.length).split(path.sep).filter(Boolean);
  for (const [index, part] of parts.entries()) {
    cursor = path.join(cursor, part);
    const stat = await lstat(cursor);
    if (stat.isSymbolicLink() || (index < parts.length - 1 && !stat.isDirectory())) fail('CANDIDATE_FILE_TYPE_INVALID');
    chain.push([cursor, stat.isDirectory() ? 'directory' : 'file',
      stat.isDirectory() ? directoryIdentity(stat) : stamp(stat)]);
  }
  return chain;
}
async function unchanged(chain) {
  const current = await Promise.all(chain.map(async ([name, kind]) => {
    const stat = await lstat(name);
    return [name, stat.isDirectory() ? 'directory' : 'file',
      stat.isDirectory() ? directoryIdentity(stat) : stamp(stat)];
  }));
  if (JSON.stringify(current) !== JSON.stringify(chain)) fail('CANDIDATE_CHANGED');
}
async function regular(root, relative) {
  validateRelativePath(relative);
  const chain = await pathChain(root, relative);
  const info = await lstat(pathname(root, relative));
  if (!info.isFile() || info.isSymbolicLink()) fail('CANDIDATE_FILE_TYPE_INVALID');
  return { chain, info };
}
async function readMetadata(root, relative) {
  const { chain, info } = await regular(root, relative);
  if (info.size < 1 || info.size > maxMetadata) fail('CANDIDATE_METADATA_INVALID');
  let handle;
  try {
    handle = await open(pathname(root, relative), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const opened = await handle.stat();
    if (!opened.isFile() || !same(opened, info)) fail('CANDIDATE_CHANGED');
    const buffer = Buffer.alloc(info.size + 1); let total = 0;
    while (total < buffer.length) {
      const { bytesRead } = await handle.read(buffer, total, buffer.length - total, null);
      if (!bytesRead) break;
      total += bytesRead;
    }
    if (total !== info.size || !same(opened, await handle.stat())) fail('CANDIDATE_CHANGED');
    await unchanged(chain);
    return buffer.subarray(0, total);
  } finally { if (handle) await handle.close().catch(() => {}); }
}
function canonical(bytes) {
  let value;
  try { value = JSON.parse(bytes.toString('utf8')); } catch { fail('CANDIDATE_METADATA_INVALID'); }
  if (!Buffer.from(`${JSON.stringify(value, null, 2)}\n`, 'utf8').equals(bytes)) fail('CANDIDATE_CANONICAL_INVALID');
  return value;
}
async function hashFile(root, relative, expectedBytes, expectedSha) {
  const { chain, info } = await regular(root, relative);
  if (info.size !== expectedBytes) fail('ARTIFACT_DIGEST_MISMATCH');
  let handle;
  try {
    handle = await open(pathname(root, relative), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const opened = await handle.stat();
    if (!opened.isFile() || !same(opened, info)) fail('CANDIDATE_CHANGED');
    const digest = createHash('sha256'); let count = 0;
    for await (const chunk of createReadStream(pathname(root, relative), { fd: handle.fd, autoClose: false })) {
      count += chunk.length;
      if (count > expectedBytes) fail('ARTIFACT_DIGEST_MISMATCH');
      digest.update(chunk);
    }
    if (!same(opened, await handle.stat())) fail('CANDIDATE_CHANGED');
    await unchanged(chain);
    if (count !== expectedBytes || digest.digest('hex') !== expectedSha) fail('ARTIFACT_DIGEST_MISMATCH');
  } finally { if (handle) await handle.close().catch(() => {}); }
}
function expectedTree(manifest) {
  const files = new Set(['candidate.json', 'payload/manifest.json', 'payload/manifest.json.sha256']);
  const directories = new Set(['payload']);
  for (const artifact of manifest.artifacts) {
    const relative = `payload/${artifact.path}`;
    if (files.has(relative) || directories.has(relative)) fail('CANDIDATE_TREE_INVALID');
    files.add(relative);
    const parts = relative.split('/');
    for (let i = 1; i < parts.length; i++) {
      const directory = parts.slice(0, i).join('/');
      if (files.has(directory)) fail('CANDIDATE_TREE_INVALID');
      directories.add(directory);
    }
  }
  if ([...directories].some((directory) => files.has(directory))) fail('CANDIDATE_TREE_INVALID');
  return { files, directories };
}
async function treeSnapshot(root, expected) {
  const entries = new Map();
  const direct = new Map();
  for (const child of [...expected.files, ...expected.directories]) {
    const parent = child.includes('/') ? child.slice(0, child.lastIndexOf('/')) : '';
    direct.set(parent, (direct.get(parent) ?? 0) + 1);
  }
  let count = 0;
  async function walk(relative = '', depth = 0) {
    if (depth > maxDepth) fail('CANDIDATE_TREE_INVALID');
    const directory = pathname(root, relative);
    const chain = await pathChain(root, relative);
    const before = await lstat(directory);
    if (!before.isDirectory()) fail('CANDIDATE_FILE_TYPE_INVALID');
    const folded = new Set();
    let localCount = 0;
    const handle = await opendir(directory);
    try {
      // Streaming directory enumeration cannot allocate an unbounded names array.
      // One extra entry beyond the exact manifest-derived child count is enough
      // to reject a directory containing unknown material.
      for await (const dirent of handle) {
        if (++localCount > (direct.get(relative) ?? 0) || ++count > maxEntries) fail('CANDIDATE_TREE_INVALID');
        const name = dirent.name;
        if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\\')) fail('CANDIDATE_TREE_INVALID');
        const key = name.toLocaleLowerCase('en-US');
        if (folded.has(key)) fail('CANDIDATE_PATH_CONFLICT');
        folded.add(key);
        const child = relative ? `${relative}/${name}` : name;
        if (child.length > 520) fail('CANDIDATE_TREE_INVALID');
        const info = await lstat(pathname(root, child));
        const kind = info.isDirectory() ? 'directory' : info.isFile() ? 'file' : 'other';
        if (info.isSymbolicLink() || kind === 'other') fail('CANDIDATE_FILE_TYPE_INVALID');
        if (kind === 'directory' ? !expected.directories.has(child) : !expected.files.has(child)) fail('CANDIDATE_TREE_INVALID');
        entries.set(child, [kind, stamp(info)]);
        if (kind === 'directory') await walk(child, depth + 1);
      }
    } finally { await handle.close().catch(() => {}); }
    if (localCount !== (direct.get(relative) ?? 0)) fail('CANDIDATE_TREE_INVALID');
    if (!same(before, await lstat(directory))) fail('CANDIDATE_CHANGED');
    await unchanged(chain);
  }
  await walk();
  if (entries.size !== expected.files.size + expected.directories.size) fail('CANDIDATE_TREE_INVALID');
  return JSON.stringify([...entries].sort(([a], [b]) => a.localeCompare(b)));
}

function imagePlatforms(candidate, manifest) {
  if (!Array.isArray(candidate.imageCandidates) || candidate.imageCandidates.length < 1 || candidate.imageCandidates.length > 2) fail('CANDIDATE_SCHEMA_INVALID');
  const declared = new Set();
  for (const record of candidate.imageCandidates) {
    if (!keys(record, ['platform', 'sha256']) || !['linux/amd64', 'linux/arm64'].includes(record.platform)
      || typeof record.sha256 !== 'string' || !hex.test(record.sha256) || declared.has(record.platform)) fail('CANDIDATE_SCHEMA_INVALID');
    declared.add(record.platform);
  }
  if (declared.size !== manifest.platforms.length || manifest.platforms.some((platform) => !declared.has(platform))) fail('IMAGE_CANDIDATE_MISMATCH');
  // Candidate marker digests name the prior image-candidate markers, not the
  // archive bytes. The archive digest is independently checked from manifest.
}

export async function verifyReleaseCandidate(input) {
  try {
    if (!keys(input, ['candidateDirectory', 'candidateSha256']) || typeof input.candidateDirectory !== 'string'
      || !path.isAbsolute(input.candidateDirectory) || /[\u0000-\u001f\u007f]/.test(input.candidateDirectory)
      || typeof input.candidateSha256 !== 'string' || !hex.test(input.candidateSha256)) fail('ARGUMENTS_INVALID');
    const root = path.resolve(input.candidateDirectory);
    const ancestors = await pathChain(root);
    if (!(await lstat(root)).isDirectory()) fail('CANDIDATE_FILE_TYPE_INVALID');
    const markerBytes = await readMetadata(root, 'candidate.json');
    const candidateSha256 = sha(markerBytes);
    if (candidateSha256 !== input.candidateSha256) fail('CANDIDATE_DIGEST_MISMATCH');
    const candidate = canonical(markerBytes);
    if (!keys(candidate, ['schemaVersion', 'kind', 'buildIdentity', 'softwareVersion', 'sourceCommit', 'manifestPath', 'manifestSha256', 'buildInputSha256', 'imageCandidates'])
      || candidate.schemaVersion !== 1 || candidate.kind !== 'lexiflow-release-candidate'
      || candidate.manifestPath !== 'payload/manifest.json' || typeof candidate.manifestSha256 !== 'string' || !hex.test(candidate.manifestSha256)
      || typeof candidate.buildInputSha256 !== 'string' || !hex.test(candidate.buildInputSha256)) fail('CANDIDATE_SCHEMA_INVALID');
    const identity = validateBuildIdentity(candidate.buildIdentity);
    if (identity.dirty || identity.softwareVersion !== candidate.softwareVersion || identity.sourceCommit !== candidate.sourceCommit) fail('CANDIDATE_IDENTITY_MISMATCH');
    const manifestBytes = await readMetadata(root, 'payload/manifest.json');
    const manifestSha256 = sha(manifestBytes);
    if (manifestSha256 !== candidate.manifestSha256) fail('MANIFEST_DIGEST_MISMATCH');
    const manifest = canonical(manifestBytes);
    assertBuildIdentityMatches(manifest.buildIdentity, identity);
    if (!Buffer.from(serializeManifest(createManifest(manifest, identity)), 'utf8').equals(manifestBytes)) fail('MANIFEST_CANONICAL_INVALID');
    imagePlatforms(candidate, manifest);
    const expected = expectedTree(manifest);
    const firstTree = await treeSnapshot(root, expected);
    const sidecar = await readMetadata(root, 'payload/manifest.json.sha256');
    if (sidecar.toString('utf8') !== `${manifestSha256}  manifest.json\n`) fail('MANIFEST_SIDECAR_INVALID');
    for (const item of manifest.artifacts) await hashFile(root, `payload/${item.path}`, item.bytes, item.sha256);
    const extension = manifest.artifacts.find((item) => item.role === 'extension');
    const extensionPath = `payload/${extension.path}`;
    const extensionBefore = await regular(root, extensionPath);
    await verifyExtensionIdentity(pathname(root, extensionPath), identity);
    await unchanged(extensionBefore.chain);
    // Re-read every artifact after ZIP parsing, not merely its metadata. This
    // binds the returned proof to the complete end-state of the candidate.
    for (const item of manifest.artifacts) await hashFile(root, `payload/${item.path}`, item.bytes, item.sha256);
    if (firstTree !== await treeSnapshot(root, expected)) fail('CANDIDATE_CHANGED');
    if (!(await readMetadata(root, 'candidate.json')).equals(markerBytes)
      || !(await readMetadata(root, 'payload/manifest.json')).equals(manifestBytes)) fail('CANDIDATE_CHANGED');
    await unchanged(ancestors);
    return { candidateSha256, manifestSha256, buildIdentity: identity, candidate, manifest };
  } catch (error) {
    if (error instanceof Error && publicErrors.has(error.message)) throw error;
    fail();
  }
}
