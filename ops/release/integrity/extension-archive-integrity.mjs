import { open, lstat } from 'node:fs/promises';
import { constants as fsConstants } from 'node:fs';
import { inflateRawSync } from 'node:zlib';
import { createHash } from 'node:crypto';

const MAX_CENTRAL = 16 * 1024 * 1024;
const MAX_ENTRIES = 65535;
const MAX_ENTRY = 64 * 1024;
const MAX_COMPRESSED_ENTRY = 128 * 1024;
const MAX_TOTAL = 512 * 1024;
const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;
const DD = 0x08074b50;

function fail(code) { throw new Error(code); }
function safeName(name) {
  return typeof name === 'string' && /^[\x21-\x7e]+$/.test(name) && !name.startsWith('/') &&
    !name.includes('\\') && !name.includes(':') && !name.split('/').some(part => part === '..' || part === '.' || part === '');
}
function checkExtra(extra) {
  for (let at = 0; at < extra.length;) {
    if (at + 4 > extra.length) fail('ARCHIVE_INVALID');
    const id = extra.readUInt16LE(at), size = extra.readUInt16LE(at + 2);
    if ([0x0001, 0x0017, 0x9901].includes(id) || at + 4 + size > extra.length) fail('ARCHIVE_INVALID');
    at += 4 + size;
  }
}
function crc32(buf) {
  let crc = 0xffffffff;
  for (const byte of buf) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function sameStat(a, b) {
  return a.dev === b.dev && a.ino === b.ino && a.size === b.size &&
    a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs;
}
async function rejectSymlinkComponents(path) {
  const absolute = path.startsWith('/');
  const parts = path.split('/').filter(Boolean);
  let prefix = absolute ? '/' : '';
  for (let i = 0; i < parts.length - 1; i++) {
    prefix = prefix ? `${prefix}/${parts[i]}` : parts[i];
    let st;
    try { st = await lstat(prefix); } catch { fail('ARCHIVE_INVALID'); }
    if (st.isSymbolicLink() || !st.isDirectory()) fail('ARCHIVE_INVALID');
  }
}
async function readAt(handle, length, position) {
  const b = Buffer.alloc(length);
  const { bytesRead } = await handle.read(b, 0, length, position);
  if (bytesRead !== length) fail('ARCHIVE_INVALID');
  return b;
}

async function parseZipEntries(filePath, names, full = false) {
  if (!Array.isArray(names) || (!full && names.length === 0) || names.some(n => !safeName(n)) || new Set(names).size !== names.length) fail('ARCHIVE_INVALID');
  try { await rejectSymlinkComponents(filePath); } catch { fail('ARCHIVE_INVALID'); }
  const nofollow = fsConstants.O_NOFOLLOW ?? 0;
  let handle;
  try { handle = await open(filePath, fsConstants.O_RDONLY | nofollow | (fsConstants.O_NONBLOCK ?? 0)); }
  catch { fail('ARCHIVE_INVALID'); }
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size < 22 || (full && before.size > 64 * 1024 * 1024)) fail(full ? 'ARCHIVE_LIMIT_EXCEEDED' : 'ARCHIVE_INVALID');
    const tailSize = Math.min(before.size, 22 + 65535);
    const tail = await readAt(handle, tailSize, before.size - tailSize);
    let eocd = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tail.readUInt32LE(i) === EOCD && i + 22 + tail.readUInt16LE(i + 20) === tail.length) { eocd = i; break; }
    }
    if (eocd < 0) fail('ARCHIVE_INVALID');
    const e = tail.subarray(eocd);
    const disk = e.readUInt16LE(4), cdDisk = e.readUInt16LE(6);
    const diskCount = e.readUInt16LE(8), count = e.readUInt16LE(10);
    const cdSize = e.readUInt32LE(12), cdOffset = e.readUInt32LE(16);
    if (disk || cdDisk || diskCount !== count) fail('ARCHIVE_INVALID');
    if (count === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) fail('ARCHIVE_INVALID');
    if (count > (full ? 512 : MAX_ENTRIES) || cdSize > (full ? 16 * 1024 * 1024 : MAX_CENTRAL)) fail('ARCHIVE_LIMIT_EXCEEDED');
    const eocdOffset = before.size - tailSize + eocd;
    if (cdOffset + cdSize !== eocdOffset || cdOffset + cdSize > before.size) fail('ARCHIVE_INVALID');
    const central = await readAt(handle, cdSize, cdOffset);
    const records = new Map();
    const allNames = new Set();
    const allRecords = [];
    const pathNodes = new Map();
    let p = 0;
    for (let idx = 0; idx < count; idx++) {
      if (p + 46 > central.length || central.readUInt32LE(p) !== CENTRAL) fail('ARCHIVE_INVALID');
      const flags = central.readUInt16LE(p + 8), method = central.readUInt16LE(p + 10);
      const crc = central.readUInt32LE(p + 16), compressed = central.readUInt32LE(p + 20), size = central.readUInt32LE(p + 24);
      const nlen = central.readUInt16LE(p + 28), xlen = central.readUInt16LE(p + 30), clen = central.readUInt16LE(p + 32);
      const diskStart = central.readUInt16LE(p + 34), localOffset = central.readUInt32LE(p + 42);
      const end = p + 46 + nlen + xlen + clen;
      if (end > central.length || diskStart !== 0 || size === 0xffffffff || compressed === 0xffffffff || localOffset === 0xffffffff) fail('ARCHIVE_INVALID');
      if (flags & ~(0x0006 | 0x0008 | 0x0800)) fail('ARCHIVE_INVALID');
      if (method !== 0 && method !== 8) fail('ARCHIVE_INVALID');
      checkExtra(central.subarray(p + 46 + nlen, p + 46 + nlen + xlen));
      const name = central.toString('utf8', p + 46, p + 46 + nlen);
      if (!safeName(name) && !(name.endsWith('/') && safeName(name.slice(0, -1)))) fail('ARCHIVE_INVALID');
      // ZIP 条目也可声明 Unix symlink/设备；即使本函数不落盘，下游解包也不得换解释。
      const attributes = central.readUInt32LE(p + 38);
      const entryType = (attributes >>> 16) & 0o170000;
      const directory = name.endsWith('/');
      if (full && directory && (size !== 0 || compressed !== 0)) fail('ARCHIVE_INVALID');
      if (full) {
        const parts = (directory ? name.slice(0, -1) : name).split('/');
        let current = '';
        for (let index = 0; index < parts.length; index++) {
          const part = parts[index], folded = part.toLocaleLowerCase('en-US');
          const key = current ? `${current}/${folded}` : folded;
          const originalPath = current ? `${current}/${part}` : part;
          const leaf = index === parts.length - 1, kind = leaf && !directory ? 'file' : 'directory';
          const prior = pathNodes.get(key);
          if (prior && (prior.path !== originalPath || prior.kind !== kind && prior.kind === 'file')) fail('ARCHIVE_INVALID');
          if (prior?.kind === 'directory' && kind === 'file') fail('ARCHIVE_INVALID');
          pathNodes.set(key, { path: originalPath, kind: prior?.kind === 'directory' ? 'directory' : kind });
          current = key;
        }
      }
      if (![0, 0o100000, 0o040000].includes(entryType)
        || (entryType === 0o100000 && directory)
        || ((entryType === 0o040000 || (attributes & 0x10)) && !directory)) fail('ARCHIVE_INVALID');
      if (allNames.has(name) || (full && [...allNames].some(existing => existing.toLocaleLowerCase('en-US') === name.toLocaleLowerCase('en-US')))) fail('ARCHIVE_INVALID');
      allNames.add(name);
      const record = { flags, method, crc, compressed, size, localOffset, nameBytes: central.subarray(p + 46, p + 46 + nlen) };
      allRecords.push(record);
      if (full || names.includes(name)) records.set(name, record);
      p = end;
    }
    if (p !== central.length) fail('ARCHIVE_INVALID');
    if (names.some(n => !records.has(n))) fail('ARCHIVE_ENTRY_MISSING');
    let total = 0;
    // 所有 local 记录都受 central 边界约束；只解压请求的元数据。
    const ranges = [];
    for (const r of allRecords) {
      if (r.localOffset + 30 > cdOffset) fail('ARCHIVE_INVALID');
      const h = await readAt(handle, 30, r.localOffset);
      if (h.readUInt32LE(0) !== LOCAL || h.readUInt16LE(6) !== r.flags || h.readUInt16LE(8) !== r.method) fail('ARCHIVE_INVALID');
      const nlen = h.readUInt16LE(26), xlen = h.readUInt16LE(28);
      r.headerEnd = r.localOffset + 30 + nlen + xlen;
      let end = r.headerEnd + r.compressed;
      if (end > cdOffset) fail('ARCHIVE_INVALID');
      checkExtra(await readAt(handle, xlen, r.localOffset + 30 + nlen));
      if (!(await readAt(handle, nlen, r.localOffset + 30)).equals(r.nameBytes)) fail('ARCHIVE_INVALID');
      if (!(r.flags & 8)) {
        if (h.readUInt32LE(14) !== r.crc || h.readUInt32LE(18) !== r.compressed || h.readUInt32LE(22) !== r.size) fail('ARCHIVE_INVALID');
      } else {
        if (end + 12 > cdOffset) fail('ARCHIVE_INVALID');
        const signature = await readAt(handle, 4, end);
        if (signature.readUInt32LE(0) === DD) end += 4;
        if (end + 12 > cdOffset) fail('ARCHIVE_INVALID');
        const descriptor = await readAt(handle, 12, end);
        if (descriptor.readUInt32LE(0) !== r.crc || descriptor.readUInt32LE(4) !== r.compressed || descriptor.readUInt32LE(8) !== r.size) fail('ARCHIVE_INVALID');
        end += 12;
      }
      ranges.push([r.localOffset, end]);
    }
    ranges.sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < ranges.length; i++) if (ranges[i][0] < ranges[i - 1][1]) fail('ARCHIVE_INVALID');
    const result = new Map();
    let archiveSha256;
    const selected = full ? allRecords.filter(record => !record.nameBytes.toString('utf8').endsWith('/')).map(record => record.nameBytes.toString('utf8')) : names;
    if (full && allRecords.some(record => record.size > 16 * 1024 * 1024 || record.compressed > 64 * 1024 * 1024)) fail('ARCHIVE_LIMIT_EXCEEDED');
    let fullTotal = 0;
    for (const name of selected) {
      const r = records.get(name);
      if (r.size > (full ? 16 * 1024 * 1024 : MAX_ENTRY) || (full ? (fullTotal += r.size) : (total += r.size)) > (full ? 64 * 1024 * 1024 : MAX_TOTAL)) fail('ARCHIVE_LIMIT_EXCEEDED');
      if (!full && r.compressed > MAX_COMPRESSED_ENTRY) fail('ARCHIVE_LIMIT_EXCEEDED');
      if (r.method === 0 && r.compressed !== r.size) fail('ARCHIVE_INVALID');
      const { method, headerEnd } = r;
      const payload = await readAt(handle, r.compressed, headerEnd);
      let decoded;
      if (method === 0) decoded = payload;
      else {
        try {
          const inf = inflateRawSync(payload, { maxOutputLength: full ? 16 * 1024 * 1024 : MAX_ENTRY, info: true });
          decoded = inf.buffer;
          if (inf.engine.bytesWritten !== payload.length) fail('ARCHIVE_INVALID');
        } catch (err) { if (err.message === 'ARCHIVE_INVALID') throw err; fail(err.code === 'ERR_BUFFER_TOO_LARGE' ? 'ARCHIVE_LIMIT_EXCEEDED' : 'ARCHIVE_INVALID'); }
      }
      if (decoded.length !== r.size || crc32(decoded) !== r.crc) fail('ARCHIVE_INVALID');
      result.set(name, decoded);
    }
    if (full) {
      const fileBytes = await readAt(handle, before.size, 0);
      archiveSha256 = createHash('sha256').update(fileBytes).digest('hex');
    }
    const after = await handle.stat();
    await rejectSymlinkComponents(filePath);
    const named = await lstat(filePath);
    if (!sameStat(before, after) || named.isSymbolicLink() || !sameStat(after, named)) fail('ARCHIVE_INVALID');
    return full ? { entries: result, archiveSha256 } : result;
  } catch (error) {
    if (['ARCHIVE_INVALID', 'ARCHIVE_ENTRY_MISSING', 'ARCHIVE_LIMIT_EXCEEDED'].includes(error.message)) throw error;
    fail('ARCHIVE_INVALID');
  } finally { await handle.close(); }
}

export function readZipEntries(filePath, names) { return parseZipEntries(filePath, names); }

/** Read a bounded complete extension archive and bind its digest to the same opened file descriptor. */
export async function readExtensionFiles(filePath, expectedSha256) {
  if (typeof expectedSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(expectedSha256)) fail('ARCHIVE_INVALID');
  const parsed = await parseZipEntries(filePath, [], true);
  if (parsed.archiveSha256 !== expectedSha256) fail('ARCHIVE_INVALID');
  return parsed.entries;
}
