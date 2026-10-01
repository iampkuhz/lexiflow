import { createHash, randomUUID } from 'node:crypto';
import { lstat, link, open, readFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createManifest, serializeManifest, verifyContainedFile } from './manifest.mjs';
import { checkReleaseSource } from './version.mjs';

const digest = (value) => createHash('sha256').update(value).digest('hex');
const fail = (code) => { throw new Error(code); };
const safePath = (value) => {
  if (typeof value !== 'string' || !value || value.length > 512 || !/^[A-Za-z0-9._/-]+$/.test(value) || value.startsWith('/') || value.split('/').some((part) => !part || part === '.' || part === '..' || part.length > 128)) fail('INVALID_PATH');
  return value;
};
const quote = (value) => {
  if (typeof value !== 'string' || /\u0000/.test(value)) fail('RUNTIME_VALUE_INVALID');
  return `'${value.replaceAll("'", "'\\''")}'`;
};
function fillTemplate(template, values) {
  const seen = new Set();
  const output = template.replace(/@@([A-Z0-9_]+)@@/g, (_match, key) => {
    if (!Object.hasOwn(values, key) || seen.has(key)) fail('RUNTIME_TEMPLATE_INVALID');
    seen.add(key);
    return values[key];
  });
  if (seen.size !== Object.keys(values).length) fail('RUNTIME_TEMPLATE_INVALID');
  return output;
}

function manifestPieces(manifest) {
  const text = serializeManifest(manifest);
  const role = '"role": "runtime-entry"';
  const start = text.indexOf(role);
  if (start < 0 || start !== text.lastIndexOf(role)) fail('RUNTIME_TEMPLATE_INVALID');
  const end = text.indexOf('\n    }', start);
  if (end < 0) fail('RUNTIME_TEMPLATE_INVALID');
  const block = text.slice(start, end);
  const entry = manifest.artifacts.find((item) => item.role === 'runtime-entry');
  const bytesToken = `"bytes": ${entry.bytes}`;
  const hashToken = `"sha256": "${entry.sha256}"`;
  const bytesAt = block.indexOf(bytesToken);
  const hashAt = block.indexOf(hashToken);
  if (bytesAt < 0 || hashAt < 0 || bytesAt >= hashAt || bytesAt !== block.lastIndexOf(bytesToken) || hashAt !== block.lastIndexOf(hashToken)) fail('RUNTIME_TEMPLATE_INVALID');
  const bytesValueAt = start + bytesAt + '"bytes": '.length;
  const hashValueAt = start + hashAt + '"sha256": "'.length;
  return [text.slice(0, bytesValueAt), text.slice(bytesValueAt + String(entry.bytes).length, hashValueAt), text.slice(hashValueAt + 64)];
}

function staticValues(manifest) {
  const dataset = manifest.artifacts.find((item) => item.role === 'dataset');
  const compose = manifest.artifacts.find((item) => item.role === 'compose');
  const withoutEntry = { ...manifest, artifacts: manifest.artifacts.filter((item) => item.role !== 'runtime-entry') };
  return {
    RELEASE_KEY: digest(serializeManifest(withoutEntry)), SOFTWARE_VERSION: manifest.softwareVersion,
    API_CONTRACT: manifest.apiContract, SQL_VERSION: manifest.sqlVersion,
    DATASET_SHA256: dataset.sha256, DATASET_PATH: dataset.path, COMPOSE_PATH: compose.path,
  };
}

async function makeScript(body, manifest) {
  const verifierPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'runtime-verification.sh');
  const template = await readFile(verifierPath, 'utf8');
  const [prefix, middle, suffix] = manifestPieces(manifest);
  const checks = manifest.artifacts.filter((item) => item.role !== 'runtime-entry').map((item) => `  lf_check_file ${quote(item.path)} ${item.bytes} ${quote(item.sha256)} || return 1`).join('\n');
  const cases = manifest.platforms.map((platform) => {
    const api = manifest.artifacts.find((item) => item.role === 'api-image' && item.platform === platform);
    const db = manifest.artifacts.find((item) => item.role === 'postgres-image' && item.platform === platform);
    return `    ${platform}) LF_PLATFORM=${quote(platform)}; LF_API_IMAGE=${quote(api.imageDigest)}; LF_POSTGRES_IMAGE=${quote(db.imageDigest)}; LF_API_ARCHIVE=${quote(api.path)}; LF_POSTGRES_ARCHIVE=${quote(db.path)};;`;
  }).join('\n');
  const values = Object.fromEntries(Object.entries({ ...staticValues(manifest), MANIFEST_PREFIX: prefix, MANIFEST_MIDDLE: middle, MANIFEST_SUFFIX: suffix }).map(([key, value]) => [key, quote(value)]));
  values.ARTIFACT_CHECKS = checks;
  values.PLATFORM_CASES = cases;
  const output = fillTemplate(template, values);
  return `${output}\n${body}\n`;
}

function sourceIdentity(repoRoot) {
  try { return checkReleaseSource(repoRoot); } catch { fail('RELEASE_SOURCE_REJECTED'); }
}
async function assertNoSymlinkPath(target) {
  const absolute = path.resolve(target); let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch((error) => error.code === 'ENOENT' ? null : fail('OUTPUT_PATH_INVALID'));
    if (info?.isSymbolicLink()) fail('OUTPUT_PATH_SYMLINK');
  }
}
async function existingSame(target, expected) {
  const info = await lstat(target).catch((error) => error.code === 'ENOENT' ? null : fail('OUTPUT_PATH_INVALID'));
  if (!info) return false;
  if (!info.isFile() || info.isSymbolicLink()) fail('OUTPUT_EXISTS_DIFFERENT');
  const content = await readFile(target).catch(() => fail('OUTPUT_EXISTS_DIFFERENT'));
  if (!content.equals(expected)) fail('OUTPUT_EXISTS_DIFFERENT');
  return true;
}

export async function generateRuntimeEntry({ repoRoot, descriptor, artifactRoot, lifecycleBody }) {
  if (![repoRoot, artifactRoot].every((item) => typeof item === 'string' && item.length && !/[\u0000\r\n]/.test(item)) || typeof lifecycleBody !== 'string' || /\u0000/.test(lifecycleBody)) fail('ARGUMENTS_INVALID');
  const sourceRoot = path.resolve(repoRoot);
  const first = sourceIdentity(sourceRoot);
  if (descriptor?.artifacts?.some((item) => item.role === 'runtime-entry' || item.path === 'lexiflow.sh')) fail('RUNTIME_ENTRY_ALREADY_DECLARED');
  const license = descriptor?.licenses?.find((item) => item.component === 'LexiFlow');
  if (!license) fail('RUNTIME_ENTRY_LICENSE_MISSING');
  const provisionalEntry = { role: 'runtime-entry', path: 'lexiflow.sh', bytes: 1, sha256: '0'.repeat(64), licenseIds: [license.id] };
  const provisionalDescriptor = { ...descriptor, artifacts: [...descriptor.artifacts, provisionalEntry] };
  const provisionalManifest = createManifest(provisionalDescriptor, first);
  for (const item of provisionalManifest.artifacts) safePath(item.path);
  for (const item of provisionalManifest.licenses) safePath(item.noticePath);
  const root = path.resolve(artifactRoot);
  await assertNoSymlinkPath(root);
  const rootInfo = await lstat(root).catch((error) => error.code === 'ENOENT' ? null : fail('OUTPUT_PATH_INVALID'));
  if (!rootInfo?.isDirectory() || rootInfo.isSymbolicLink()) fail('ARTIFACT_ROOT_INVALID');
  for (const item of provisionalManifest.artifacts) if (item.role !== 'runtime-entry') await verifyContainedFile(root, item.path, item.bytes, item.sha256);
  const script = await makeScript(lifecycleBody, provisionalManifest);
  const bytes = Buffer.from(script, 'utf8');
  const artifact = { ...provisionalEntry, bytes: bytes.length, sha256: digest(bytes) };
  const manifest = createManifest({ ...descriptor, artifacts: [...descriptor.artifacts, artifact] }, first);
  if (await makeScript(lifecycleBody, manifest) !== script) fail('RUNTIME_TEMPLATE_INVALID');
  const second = sourceIdentity(sourceRoot);
  if (second.sourceCommit !== first.sourceCommit || second.softwareVersion !== first.softwareVersion) fail('RELEASE_SOURCE_CHANGED');
  const target = path.join(root, 'lexiflow.sh');
  if (await existingSame(target, bytes)) return { artifact, manifest, script };
  const temp = path.join(root, `.lexiflow-${randomUUID()}.tmp`);
  let created = false;
  try {
    const handle = await open(temp, 'wx', 0o644);
    created = true;
    try { await handle.writeFile(bytes); }
    finally { await handle.close(); }
    await link(temp, target);
  } catch (error) {
    if (created && error.code === 'EEXIST' && await existingSame(target, bytes)) return { artifact, manifest, script };
    if (created && error.code === 'EEXIST') fail('OUTPUT_EXISTS_DIFFERENT');
    fail('OUTPUT_PUBLISH_FAILED');
  } finally {
    if (created) await unlink(temp).catch(() => {});
  }
  return { artifact, manifest, script };
}
