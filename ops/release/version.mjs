import { execFileSync } from 'node:child_process';
import { readFileSync, realpathSync, lstatSync, openSync, closeSync, fstatSync, readSync, constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function parseVersion(raw) {
  const value = raw.endsWith('\r\n') ? raw.slice(0, -2) : raw.endsWith('\n') ? raw.slice(0, -1) : raw;
  if (!/^(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT)?$/.test(value)) throw new Error('invalid software version');
  const parts = value.replace(/-SNAPSHOT$/, '').split('.').map(Number);
  if (parts.some((part) => part > 65535) || parts.every((part) => part === 0)) throw new Error('invalid software version');
  return value;
}

export async function readVersion(source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'version.txt')) {
  try { return parseVersion(await readFile(source, 'utf8')); }
  catch { throw new Error('software version missing or invalid'); }
}

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const digest = value => createHash('sha256').update(value).digest('hex');

function gitOutput(root, ...args) {
  return execFileSync('git', ['-C', root, ...args], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10_000, maxBuffer: 8 * 1024 * 1024,
  });
}

function gitSnapshot(root) {
  const repository = realpathSync(gitOutput(root, 'rev-parse', '--show-toplevel').trim());
  if (repository !== realpathSync(root)) throw new Error();
  const sourceCommit = gitOutput(root, 'rev-parse', '--verify', 'HEAD^{commit}').trim();
  if (!/^[a-f0-9]{40,64}$/.test(sourceCommit)) throw new Error();
  const dirty = gitOutput(root, 'status', '--porcelain=v1', '--untracked-files=all').length > 0;
  const files = [...new Set(gitOutput(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0').filter(Boolean))].sort();
  return { repository, sourceCommit, dirty, files };
}

function statIdentity(stat) {
  return [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs, stat.mode].map(String).join(':');
}

function readStableFile(root, relative) {
  let current = root;
  const parts = relative.split('/');
  if (path.isAbsolute(relative) || parts.some(part => !part || part === '..' || part === '.' || part === '.git')) throw new Error('invalid build source path');
  const ancestors = [];
  let pathBefore;
  const directoryId = info => [info.dev, info.ino, info.mode].map(String).join(':');
  try {
    for (const part of parts) {
      current = path.join(current, part);
      const info = lstatSync(current, { bigint: true });
      if (info.isSymbolicLink()) throw new Error('build source symlink forbidden');
      if (current === path.join(root, relative)) pathBefore = info;
      else {
        if (!info.isDirectory()) throw new Error('build source path invalid');
        ancestors.push([current, directoryId(info)]);
      }
    }
  } catch (error) {
    if (error.code === 'ENOENT') return { input: [relative, 'deleted'], stable: ['deleted'] };
    throw error;
  }
  const filename = path.join(root, relative);
  let fd;
  try {
    fd = openSync(filename, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const before = fstatSync(fd, { bigint: true });
    if (!before.isFile()) throw new Error('build source must be regular file');
    const beforeId = statIdentity(before);
    if (beforeId !== statIdentity(pathBefore)) throw new Error('build source changed during identity scan');
    // Stream a fixed number of bytes plus one growth probe, not an unbounded readFile.
    const hash = createHash('sha256');
    const buffer = Buffer.alloc(64 * 1024);
    let remaining = before.size;
    const version = relative === 'ops/release/version.txt';
    const versionChunks = [];
    if (version && remaining > 128n) throw new Error('invalid software version');
    while (remaining > 0n) {
      const length = Number(remaining < BigInt(buffer.length) ? remaining : BigInt(buffer.length));
      const count = readSync(fd, buffer, 0, length, null);
      if (!count) throw new Error('build source changed during identity scan');
      hash.update(buffer.subarray(0, count));
      if (version) versionChunks.push(Buffer.from(buffer.subarray(0, count)));
      remaining -= BigInt(count);
    }
    if (readSync(fd, buffer, 0, 1, null) !== 0) throw new Error('build source changed during identity scan');
    const after = fstatSync(fd, { bigint: true });
    const pathAfter = lstatSync(filename, { bigint: true });
    if (beforeId !== statIdentity(after) || beforeId !== statIdentity(pathAfter) || pathAfter.isSymbolicLink()) throw new Error('build source changed during identity scan');
    for (const [directory, identity] of ancestors) {
      const info = lstatSync(directory, { bigint: true });
      if (!info.isDirectory() || info.isSymbolicLink() || identity !== directoryId(info)) throw new Error('build source changed during identity scan');
    }
    const executable = before.mode & 0o111n ? 'executable' : 'regular';
    return { input: [relative, executable, hash.digest('hex')], stable: [beforeId, ancestors],
      baseVersion: version ? parseVersion(Buffer.concat(versionChunks).toString('utf8')) : undefined };
  } finally { if (fd !== undefined) closeSync(fd); }
}

function snapshot(root) {
  const start = gitSnapshot(root);
  const inputs = [];
  const files = [];
  let baseVersion;
  for (const relative of start.files) {
    const file = readStableFile(root, relative);
    inputs.push(file.input);
    files.push([relative, file.stable]);
    if (relative === 'ops/release/version.txt' && file.input[1] !== 'deleted') {
      // Keep the exact bytes consumed by the source digest as the base-version source.
      baseVersion = file.baseVersion;
    }
  }
  if (!baseVersion) throw new Error();
  const end = gitSnapshot(root);
  if (JSON.stringify(start) !== JSON.stringify(end)) throw new Error('build source changed during identity scan');
  return { git: start, inputs, files, baseVersion, sourceSha256: digest(JSON.stringify(inputs)) };
}

/** 构建只读取 Git 可见输入，ignored 的字幕、数据库、缓存与运行日志不参与。 */
export function resolveBuildIdentity(root = repositoryRoot) {
  let first, second;
  try {
    first = snapshot(root);
    second = snapshot(root);
  } catch (error) {
    if (error.message === 'build source symlink forbidden' || error.message === 'build source changed during identity scan') throw error;
    throw new Error('build source identity unavailable');
  }
  if (JSON.stringify(first.git) !== JSON.stringify(second.git)
    || JSON.stringify(first.inputs) !== JSON.stringify(second.inputs)
    || JSON.stringify(first.files) !== JSON.stringify(second.files)
    || first.baseVersion !== second.baseVersion) throw new Error('build source changed during identity scan');
  return derivedIdentity({ baseVersion: first.baseVersion, sourceCommit: first.git.sourceCommit,
    sourceSha256: first.sourceSha256, dirty: first.git.dirty });
}

// 所有制品消费者共享此派生规则，不能只比对可伪造的版本与 commit。
function derivedIdentity({ baseVersion, sourceCommit, sourceSha256, dirty }) {
  const development = baseVersion.endsWith('-SNAPSHOT') || dirty;
  const numericVersion = baseVersion.replace(/-SNAPSHOT$/, '');
  const softwareVersion = development
    ? `${numericVersion}-SNAPSHOT.g${sourceCommit.slice(0, 7)}${dirty ? `.dirty.${sourceSha256.slice(0, 12)}` : ''}`
    : numericVersion;
  const buildId = digest(JSON.stringify({ baseVersion, sourceCommit, sourceSha256, dirty }));
  return { schemaVersion: 1, baseVersion, softwareVersion, chromeVersion: chromeVersion(softwareVersion),
    sourceCommit, sourceSha256, buildId, dirty, channel: development ? 'snapshot' : 'release' };
}

export function validateBuildIdentity(identity) {
  const fields = ['schemaVersion', 'baseVersion', 'softwareVersion', 'chromeVersion', 'sourceCommit', 'sourceSha256', 'buildId', 'dirty', 'channel'];
  if (!identity || typeof identity !== 'object' || Array.isArray(identity)
    || Object.keys(identity).sort().join('\0') !== [...fields].sort().join('\0')
    || identity.schemaVersion !== 1 || typeof identity.dirty !== 'boolean'
    || typeof identity.sourceCommit !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(identity.sourceCommit)
    || typeof identity.sourceSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(identity.sourceSha256)) throw new Error('INVALID_BUILD_IDENTITY');
  try {
    if (parseVersion(identity.baseVersion) !== identity.baseVersion) throw new Error();
    const derived = derivedIdentity(identity);
    if (fields.some(field => identity[field] !== derived[field])) throw new Error();
  } catch { throw new Error('INVALID_BUILD_IDENTITY'); }
  return identity;
}

export function assertBuildIdentityMatches(actual, expected) {
  validateBuildIdentity(actual); validateBuildIdentity(expected);
  if (Object.keys(expected).some(field => actual[field] !== expected[field])) throw new Error('BUILD_IDENTITY_MISMATCH');
}

/** 完整展示版本与 Chrome 数字版本分开，正式版总排在同基线开发版之后。 */
export function parseSoftwareVersion(value) {
  if (typeof value !== 'string') throw new Error('invalid software version');
  const numeric = value.split('-')[0];
  if (parseVersion(numeric) !== numeric) throw new Error('invalid software version');
  if (value !== numeric && !/^[0-9.]+-SNAPSHOT\.g[a-f0-9]{7,64}(?:\.dirty\.[a-f0-9]{12,64})?$/.test(value)) throw new Error('invalid software version');
  return value;
}

export function chromeVersion(softwareVersion) {
  parseSoftwareVersion(softwareVersion);
  const numeric = softwareVersion.split('-')[0];
  return `${numeric}.${softwareVersion === numeric ? 1 : 0}`;
}

/** 标签仅匹配干净正式源码；不创建标签，也不将开发输入晋升为正式版。 */
export function checkReleaseTag(tag, root = repositoryRoot) {
  const identity = resolveBuildIdentity(root);
  if (identity.dirty || identity.channel !== 'release' || tag !== `v${identity.softwareVersion}`) throw new Error('release tag identity mismatch');
  let taggedCommit;
  try { taggedCommit = gitOutput(root, 'rev-parse', '--verify', `refs/tags/${tag}^{commit}`).trim(); }
  catch { throw new Error('release tag unavailable'); }
  if (taggedCommit !== identity.sourceCommit) throw new Error('release tag commit mismatch');
  return identity;
}

export function checkReleaseSource(repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')) {
  const git = (...args) => execFileSync('git', ['-C', repoRoot, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10_000, maxBuffer: 100_000 }).trim();
  let root;
  try { root = git('rev-parse', '--show-toplevel'); }
  catch { throw new Error('release input is not a Git repository'); }
  try { if (realpathSync(root) !== realpathSync(repoRoot)) throw new Error('release input must be repository root'); }
  catch (error) { if (error.message === 'release input must be repository root') throw error; throw new Error('release input must be repository root'); }
  let softwareVersion;
  try { softwareVersion = parseVersion(readFileSync(path.join(repoRoot, 'ops/release/version.txt'), 'utf8')); }
  catch { throw new Error('software version missing or invalid'); }
  let sourceCommit;
  try { sourceCommit = git('rev-parse', '--verify', 'HEAD^{commit}'); }
  catch { throw new Error('release input lacks valid HEAD'); }
  if (!/^[0-9a-f]{40,64}$/.test(sourceCommit)) throw new Error('release input lacks valid HEAD');
  let status;
  try { status = git('status', '--porcelain=v1', '--untracked-files=all'); }
  catch { throw new Error('release input status unavailable'); }
  if (status) throw new Error('release input must be clean');
  if (softwareVersion.endsWith('-SNAPSHOT')) softwareVersion = `${softwareVersion}.g${sourceCommit.slice(0, 7)}`;
  return { softwareVersion, sourceCommit };
}

async function main(args) {
  if (args.length === 0) { console.log(JSON.stringify(resolveBuildIdentity())); return; }
  if (args.length === 2 && args[0] === '--tag') { console.log(JSON.stringify(checkReleaseTag(args[1]))); return; }
  if (args.length !== 1 || args[0] !== '--release') throw new Error('invalid arguments');
  console.log(JSON.stringify(checkReleaseSource()));
}

function invokedDirectly() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (invokedDirectly()) {
  main(process.argv.slice(2)).catch(() => { process.stderr.write('release input rejected\n'); process.exitCode = 1; });
}
