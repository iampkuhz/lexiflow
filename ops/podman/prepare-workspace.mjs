// 有界、可恢复的事务工作区写入；恢复只删除 journal 已证明归属的内容。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const CHUNK = 64 * 1024;
const MAX_FILES = 4096;
const MAX_BYTES = 128 * 1024 * 1024;
const MAX_JOURNAL_BYTES = 900 * 1024;
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fail = code => { throw new Error(code); };
function safeRelative(name) {
  if (typeof name !== 'string' || !name || Buffer.byteLength(name) > 1024 || name.startsWith('/') || /[\\\x00-\x1f\x7f]/.test(name)) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
  const parts = name.split('/');
  if (parts.length > 32 || parts.some(part => !part || Buffer.byteLength(part) > 255 || part === '.' || part === '..')) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
}
function infoOrNull(file) { try { return fs.lstatSync(file); } catch (error) { if (error.code === 'ENOENT') return null; throw error; } }
function regularBytes(file, maxBytes) {
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink() || info.size > maxBytes) fail('UPGRADE_WORKSPACE_DRIFT');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  try {
    const opened = fs.fstatSync(fd); if (!opened.isFile() || opened.dev !== info.dev || opened.ino !== info.ino || opened.size > maxBytes) fail('UPGRADE_WORKSPACE_DRIFT');
    const chunks = []; let total = 0, buffer = Buffer.alloc(Math.min(64 * 1024, maxBytes + 1));
    while (true) {
      const count = fs.readSync(fd, buffer, 0, Math.min(buffer.length, maxBytes + 1 - total), total);
      if (!count) break;
      total += count; if (total > maxBytes) fail('UPGRADE_WORKSPACE_DRIFT');
      chunks.push(Buffer.from(buffer.subarray(0, count)));
    }
    const after = fs.fstatSync(fd), pathAfter = fs.lstatSync(file), bytes = Buffer.concat(chunks, total);
    if (bytes.length !== after.size || after.dev !== opened.dev || after.ino !== opened.ino || after.mtimeMs !== opened.mtimeMs || after.ctimeMs !== opened.ctimeMs || pathAfter.isSymbolicLink() || pathAfter.dev !== opened.dev || pathAfter.ino !== opened.ino) fail('UPGRADE_WORKSPACE_DRIFT');
    return bytes;
  }
  finally { fs.closeSync(fd); }
}
export function readBoundedFile(file, maxBytes) { return regularBytes(file, maxBytes); }

/** 计划和每个待写块先写入事务 journal，再对工作区产生副作用。 */
export async function writePreparedTree(root, files, persist) {
  if (!Array.isArray(files) || files.length > MAX_FILES) fail('UPGRADE_WORKSPACE_LIMIT');
  let totalBytes = 0;
  const descriptors = files.map(({ path: name, bytes }) => {
    safeRelative(name);
    if (!(Buffer.isBuffer(bytes) || bytes instanceof Uint8Array) || !Number.isSafeInteger(bytes.byteLength)) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
    totalBytes += bytes.byteLength; if (totalBytes > MAX_BYTES) fail('UPGRADE_WORKSPACE_LIMIT');
    return { path: name, original: bytes, size: bytes.byteLength };
  });
  const names = new Set(descriptors.map(file => file.path));
  if (names.size !== descriptors.length) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
  for (const name of names) { const parts = name.split('/'); for (let i = 1; i < parts.length; i++) if (names.has(parts.slice(0, i).join('/'))) fail('UPGRADE_WORKSPACE_PLAN_INVALID'); }
  const normalized = descriptors.map(file => { const content = Buffer.from(file.original); return { path: file.path, bytes: content, size: content.length, sha256: digest(content) }; });
  const dirs = new Set();
  for (const file of normalized) { const parts = file.path.split('/'); for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/')); }
  const journal = { schema: 1, directories: [...dirs].sort(), files: normalized.map(({ path: name, size, sha256 }) => ({ path: name, size, sha256, confirmed: 0, prefixSha256: digest(Buffer.alloc(0)), pending: null })) };
  if (Buffer.byteLength(JSON.stringify(journal)) > MAX_JOURNAL_BYTES) fail('UPGRADE_WORKSPACE_LIMIT');
  await persist(journal);
  for (const name of journal.directories) {
    const target = path.join(root, ...name.split('/'));
    if (infoOrNull(target)) fail('UPGRADE_WORKSPACE_DRIFT');
    fs.mkdirSync(target, { mode: 0o700 });
  }
  for (let i = 0; i < normalized.length; i++) {
    const source = normalized[i], entry = journal.files[i], target = path.join(root, ...entry.path.split('/'));
    if (infoOrNull(target)) fail('UPGRADE_WORKSPACE_DRIFT');
    const fd = fs.openSync(target, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW ?? 0), 0o600);
    const prefixHasher = crypto.createHash('sha256');
    try {
      for (let offset = 0; offset < source.size; offset += CHUNK) {
        const bytes = source.bytes.subarray(offset, Math.min(source.size, offset + CHUNK));
        entry.pending = { offset, bytes: bytes.toString('base64') };
        await persist(journal);
        let written = 0; while (written < bytes.length) { const count = fs.writeSync(fd, bytes, written, bytes.length - written); if (!count) fail('UPGRADE_WORKSPACE_WRITE_FAILED'); written += count; }
        fs.fsyncSync(fd);
        entry.confirmed = offset + bytes.length;
        prefixHasher.update(bytes);
        entry.prefixSha256 = prefixHasher.copy().digest('hex');
        entry.pending = null;
        await persist(journal);
      }
    } finally { fs.closeSync(fd); }
  }
  return journal;
}

/** 逐项确认意图和实际字节前缀后删除；未知项/漂移保留现场。 */
export function cleanupPreparedTree(root, plan, { preserve = [] } = {}) {
  if (!plan || plan.schema !== 1 || !Array.isArray(plan.files) || !Array.isArray(plan.directories) || plan.files.length > MAX_FILES) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
  const files = new Map(); let total = 0;
  for (const file of plan.files) {
    safeRelative(file.path);
    if (files.has(file.path) || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_BYTES || !/^[a-f0-9]{64}$/.test(file.sha256) || !Number.isSafeInteger(file.confirmed) || file.confirmed < 0 || file.confirmed > file.size || !/^[a-f0-9]{64}$/.test(file.prefixSha256)) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
    if (file.pending !== null) {
      if (!file.pending || !Number.isSafeInteger(file.pending.offset) || file.pending.offset !== file.confirmed || typeof file.pending.bytes !== 'string') fail('UPGRADE_WORKSPACE_PLAN_INVALID');
      const chunk = Buffer.from(file.pending.bytes, 'base64');
      if (!chunk.length || chunk.length > CHUNK || chunk.toString('base64') !== file.pending.bytes || file.confirmed + chunk.length > file.size) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
    }
    total += file.size; if (total > MAX_BYTES) fail('UPGRADE_WORKSPACE_LIMIT'); files.set(file.path, file);
  }
  const preserved = new Set(preserve);
  for (const name of files.keys()) { const parts = name.split('/'); for (let i = 1; i < parts.length; i++) if (files.has(parts.slice(0, i).join('/'))) fail('UPGRADE_WORKSPACE_PLAN_INVALID'); }
  const allowed = new Set([...files.keys(), ...plan.directories, ...preserved]);
  if (new Set(plan.directories).size !== plan.directories.length) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
  for (const dir of plan.directories) safeRelative(dir);
  const expectedDirs = new Set();
  for (const name of files.keys()) { const parts = name.split('/'); for (let i = 1; i < parts.length; i++) expectedDirs.add(parts.slice(0, i).join('/')); }
  if (expectedDirs.size !== plan.directories.length || plan.directories.some(name => !expectedDirs.has(name))) fail('UPGRADE_WORKSPACE_PLAN_INVALID');
  function inspect(relative = '') {
    const current = relative ? path.join(root, ...relative.split('/')) : root;
    const info = fs.lstatSync(current);
    if (info.isSymbolicLink() || !info.isDirectory()) fail('UPGRADE_WORKSPACE_DRIFT');
    for (const child of fs.readdirSync(current).sort()) {
      const name = relative ? `${relative}/${child}` : child;
      if (!allowed.has(name)) fail('UPGRADE_WORKSPACE_DRIFT');
      const childPath = path.join(current, child), childInfo = fs.lstatSync(childPath);
      if (childInfo.isSymbolicLink()) fail('UPGRADE_WORKSPACE_DRIFT');
      if (preserved.has(name)) { if (!childInfo.isDirectory()) fail('UPGRADE_WORKSPACE_DRIFT'); continue; }
      if (childInfo.isDirectory()) inspect(name);
      else {
        const expected = files.get(name); if (!expected) fail('UPGRADE_WORKSPACE_DRIFT');
        const bytes = regularBytes(childPath, expected.size), pending = expected.pending;
        if (bytes.length > expected.size) fail('UPGRADE_WORKSPACE_DRIFT');
        if (bytes.length < expected.confirmed || digest(bytes.subarray(0, expected.confirmed)) !== expected.prefixSha256) fail('UPGRADE_WORKSPACE_DRIFT');
        if (pending) {
          const chunk = Buffer.from(pending.bytes, 'base64');
          if (chunk.length > CHUNK || pending.offset !== expected.confirmed || bytes.length > pending.offset + chunk.length || !bytes.subarray(pending.offset).equals(chunk.subarray(0, Math.max(0, bytes.length - pending.offset)))) fail('UPGRADE_WORKSPACE_DRIFT');
        } else if (bytes.length !== expected.confirmed) fail('UPGRADE_WORKSPACE_DRIFT');
        if (bytes.length === expected.size && digest(bytes) !== expected.sha256) fail('UPGRADE_WORKSPACE_DRIFT');
      }
    }
  }
  inspect();
  for (const name of files.keys()) { const file = path.join(root, ...name.split('/')); if (infoOrNull(file)) fs.unlinkSync(file); }
  for (const name of [...plan.directories].sort((a, b) => b.split('/').length - a.split('/').length)) { const dir = path.join(root, ...name.split('/')); if (infoOrNull(dir)) fs.rmdirSync(dir); }
}

export const preparationLimits = { chunkBytes: CHUNK, maxFiles: MAX_FILES, maxBytes: MAX_BYTES, maxJournalBytes: MAX_JOURNAL_BYTES };
