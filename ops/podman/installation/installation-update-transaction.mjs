// 受管安装的准备/激活/恢复事务；不导入资料、不管理 PostgreSQL、不删除引擎资源。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = code => { throw new Error(code); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function privateFile(root, relative) {
  if (fs.lstatSync(root).isSymbolicLink()) fail('INSTALL_SYMLINK_REJECTED');
  if (path.isAbsolute(relative) || relative.split('/').some(part => !part || part === '..' || part === '.')) fail('UNSAFE_INSTALL_PATH');
  let cursor = root;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part);
    const info = fs.lstatSync(cursor);
    if (info.isSymbolicLink()) fail('INSTALL_SYMLINK_REJECTED');
  }
  if (!fs.lstatSync(cursor).isFile()) fail('INSTALL_FILE_REQUIRED');
  return cursor;
}
export function treeDigest(root) {
  const entries = [];
  function visit(relative) {
    const file = path.join(root, relative), info = fs.lstatSync(file);
    if (info.isSymbolicLink()) fail('EXTENSION_SYMLINK_REJECTED');
    if (info.isDirectory()) for (const name of fs.readdirSync(file).sort()) visit(path.join(relative, name));
    else if (info.isFile()) entries.push([relative, sha256(fs.readFileSync(file))]);
    else fail('EXTENSION_FILE_REQUIRED');
  }
  visit('');
  if (!entries.length) fail('EXTENSION_EMPTY');
  return sha256(JSON.stringify(entries));
}
function readJson(root, name) {
  const file = privateFile(root, name);
  if (fs.statSync(file).size > 1024 * 1024) fail('INSTALL_RECORD_TOO_LARGE');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function exists(file) { try { fs.lstatSync(file); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } }
function ensureDirectory(directory) {
  if (!exists(directory)) fs.mkdirSync(directory, { mode: 0o700 });
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink()) fail('INSTALL_DIRECTORY_INVALID');
}
export function verifyExtension(directory, identity, port) {
  const manifest = readJson(directory, 'manifest.json');
  const embedded = readJson(directory, 'build-identity.json');
  if (!same(embedded, identity) || manifest.version_name !== identity.softwareVersion || manifest.version !== identity.chromeVersion
      || !same(manifest.host_permissions, [`http://127.0.0.1:${port}/*`])) fail('EXTENSION_IDENTITY_MISMATCH');
  return treeDigest(directory);
}
export function installationRecord({ operation, kind, previous, target, startedAt, status, artifacts = null, dataset = null }) {
  return { schema: 1, operation, kind, startedAt, completedAt: new Date().toISOString(), status,
    previousVersion: previous?.version ?? null, targetVersion: target.softwareVersion,
    sourceCommit: target.sourceCommit, buildId: target.buildId, artifacts, dataset };
}
export function publishRecord(dir, record) {
  if (!/^[a-f0-9]{32}$/.test(record.operation)) fail('INSTALL_OPERATION_INVALID');
  const root = path.join(dir, 'installation-records'); ensureDirectory(root);
  const file = path.join(root, `${record.operation}.json`);
  if (exists(file)) {
    if (!same(readJson(root, `${record.operation}.json`), record)) fail('INSTALL_RECORD_CONFLICT');
    return;
  }
  // 先完整落盘再独占发布，强制终止不能留下可被误读为成功的半条记录。
  const temporary = `${file}.${crypto.randomBytes(16).toString('hex')}.next`;
  const fd = fs.openSync(temporary, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(record, null, 2) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  try { fs.linkSync(temporary, file); }
  finally { fs.unlinkSync(temporary); }
}
