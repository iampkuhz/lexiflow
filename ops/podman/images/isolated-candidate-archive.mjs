// Rewrites only Docker save tag metadata before podman load. Signed input remains immutable.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { deploymentImageTag } from './versioned-images.mjs';
import { slotImageNamespace } from '../deployment-slots.mjs';

const fail = () => { throw new Error('CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED'); };
const zero = Buffer.alloc(512);
const hash = () => crypto.createHash('sha256');
const maxArchive = 4_000_000_000;
function field(block, start, length) {
  const value = block.subarray(start, start + length);
  const end = value.indexOf(0);
  return value.subarray(0, end < 0 ? value.length : end).toString('ascii');
}
function octal(block, start, length) {
  const raw = field(block, start, length).trim();
  if (!/^[0-7]+$/.test(raw)) fail();
  const value = Number.parseInt(raw, 8);
  if (!Number.isSafeInteger(value)) fail();
  return value;
}
function digestFile(fd, size) {
  const h = hash(), buffer = Buffer.alloc(1024 * 1024);
  let offset = 0;
  while (offset < size) {
    const n = fs.readSync(fd, buffer, 0, Math.min(buffer.length, size - offset), offset);
    if (!n) fail(); h.update(buffer.subarray(0, n)); offset += n;
  }
  return h.digest('hex');
}
function stable(a, b) { return ['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs'].every(k => a[k] === b[k]); }
function tarHeader(old, length) {
  const block = Buffer.from(old);
  block.fill(0, 124, 136);
  const oct = length.toString(8);
  if (oct.length > 11) fail();
  block.write(oct.padStart(11, '0') + '\0', 124, 12, 'ascii');
  block.fill(32, 148, 156);
  const sum = block.reduce((n, v) => n + v, 0).toString(8);
  if (sum.length > 6) fail();
  block.write(sum.padStart(6, '0') + '\0 ', 148, 8, 'ascii');
  return block;
}
function writeAll(fd, bytes) {
  let written = 0;
  while (written < bytes.length) { const n = fs.writeSync(fd, bytes, written, bytes.length - written); if (!n) fail(); written += n; }
}
function copy(fdIn, fdOut, start, length) {
  const buffer = Buffer.alloc(1024 * 1024);
  let offset = 0;
  while (offset < length) {
    const n = fs.readSync(fdIn, buffer, 0, Math.min(buffer.length, length - offset), start + offset);
    if (!n) fail(); writeAll(fdOut, buffer.subarray(0, n)); offset += n;
  }
}
function readSmall(fd, entry) {
  if (entry.size > 1024 * 1024) fail();
  const value = Buffer.alloc(entry.size);
  if (fs.readSync(fd, value, 0, entry.size, entry.data) !== entry.size) fail();
  return value;
}
function entries(fd, size) {
  const result = [], names = new Set(); let pos = 0, ended = false;
  while (pos + 512 <= size) {
    const block = Buffer.alloc(512);
    if (fs.readSync(fd, block, 0, 512, pos) !== 512) fail();
    if (block.equals(zero)) { ended = true; break; }
    const original = Buffer.from(block); block.fill(32, 148, 156);
    if (original.subarray(148, 156).toString('ascii').trim().replace(/\0/g, '') !== block.reduce((n, v) => n + v, 0).toString(8)) {
      if (octal(original, 148, 8) !== block.reduce((n, v) => n + v, 0)) fail();
    }
    const name = field(original, 0, 100), prefix = field(original, 345, 155);
    const full = prefix ? `${prefix}/${name}` : name;
    if (!full || full.startsWith('/') || full.includes('\\') || full.split('/').some(s => s === '..' || s === '.')
        || /[\x00-\x1f]/.test(full) || names.has(full)) fail();
    names.add(full);
    const type = String.fromCharCode(original[156]);
    if (type !== '0' && type !== '\0' && type !== '5' && type !== '2') fail();
    if (field(original, 257, 6) !== 'ustar') fail();
    const itemSize = octal(original, 124, 12);
    if ((type === '5' || type === '2') && itemSize !== 0) fail();
    const padded = Math.ceil(itemSize / 512) * 512;
    if (pos + 512 + padded > size) fail();
    result.push({ name: full, size: itemSize, type, link: field(original, 157, 100), header: original, data: pos + 512, padded });
    if (result.length > 100000) fail();
    pos += 512 + padded;
  }
  if (!ended || !result.some(e => e.name === 'manifest.json')) fail();
  while (pos < size) { const b = Buffer.alloc(Math.min(1024 * 1024, size - pos)); const n = fs.readSync(fd, b, 0, b.length, pos); if (!n || b.subarray(0, n).some(x => x !== 0)) fail(); pos += n; }
  return result;
}
/** Derived archive uses only a slot-owned stable alias; no original RepoTag survives. */
export function isolateCandidateArchive({ archive, output, expectedSha256, expectedBytes, imageDigest, role, baseVersion }) {
  if (!['api', 'postgres'].includes(role) || !/^sha256:[a-f0-9]{64}$/.test(imageDigest)
      || !/^[a-f0-9]{64}$/.test(expectedSha256) || !Number.isSafeInteger(expectedBytes)
      || expectedBytes < 1024 || expectedBytes > maxArchive || !path.isAbsolute(archive) || !path.isAbsolute(output)) fail();
  const alias = `localhost/${slotImageNamespace('release-validation')}-${role}:${deploymentImageTag(baseVersion)}`;
  const before = fs.lstatSync(archive);
  if (!before.isFile() || before.isSymbolicLink() || before.size !== expectedBytes) fail();
  const fd = fs.openSync(archive, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  let out;
  try {
    if (!stable(before, fs.fstatSync(fd)) || digestFile(fd, before.size) !== expectedSha256) fail();
    const list = entries(fd, before.size), manifestEntry = list.find(e => e.name === 'manifest.json');
    let manifest;
    try { manifest = JSON.parse(readSmall(fd, manifestEntry).toString('utf8')); } catch { fail(); }
    if (!Array.isArray(manifest) || manifest.length !== 1 || !manifest[0] || typeof manifest[0] !== 'object') fail();
    const image = manifest[0], config = `${imageDigest.slice(7)}.json`;
    if (image.Config !== config || !Array.isArray(image.Layers) || image.Layers.length < 1
        || image.Layers.some(n => typeof n !== 'string' || !list.some(e => e.name === n && (e.type === '0' || e.type === '\0')))
        || image.RepoTags !== null && !Array.isArray(image.RepoTags)) fail();
    // Podman docker-archive includes legacy <chain>/layer.tar symlinks beside the
    // manifest's root layer blobs. Permit only links to those exact regular blobs.
    const layers = new Set(image.Layers);
    for (const entry of list) if (entry.type === '2') {
      const match = /^([a-f0-9]{64})\/layer\.tar$/.exec(entry.name);
      const target = /^\.\.\/([a-f0-9]{64}\.tar)$/.exec(entry.link);
      if (!match || !target || !layers.has(target[1])
          || !list.some(value => value.name === target[1] && (value.type === '0' || value.type === '\0'))) fail();
    }
    const configEntry = list.find(e => e.name === config);
    if (!configEntry || !['0', '\0'].includes(configEntry.type) || configEntry.size > 10_000_000) fail();
    const configBytes = Buffer.alloc(configEntry.size);
    if (fs.readSync(fd, configBytes, 0, configEntry.size, configEntry.data) !== configEntry.size
        || `sha256:${hash().update(configBytes).digest('hex')}` !== imageDigest) fail();
    const rewritten = new Map([['manifest.json', Buffer.from(JSON.stringify([{ ...image, RepoTags: [alias] }]))]]);
    const repositories = list.find(e => e.name === 'repositories');
    if (repositories) {
      let value; try { value = JSON.parse(readSmall(fd, repositories).toString('utf8')); } catch { fail(); }
      if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
      rewritten.set('repositories', Buffer.from(JSON.stringify({ [`localhost/${slotImageNamespace('release-validation')}-${role}`]: { [deploymentImageTag(baseVersion)]: imageDigest.slice(7) } })));
    }
    out = fs.openSync(output, 'wx', 0o600);
    for (const entry of list) {
      const replacement = rewritten.get(entry.name);
      if (replacement) {
        writeAll(out, tarHeader(entry.header, replacement.length)); writeAll(out, replacement);
        const padding = (512 - replacement.length % 512) % 512;
        if (padding) writeAll(out, Buffer.alloc(padding));
      } else { writeAll(out, entry.header); copy(fd, out, entry.data, entry.padded); }
    }
    writeAll(out, Buffer.alloc(1024)); fs.fsyncSync(out); fs.closeSync(out); out = undefined;
    if (!stable(before, fs.fstatSync(fd)) || !stable(before, fs.lstatSync(archive))
        || digestFile(fd, before.size) !== expectedSha256) fail();
    return { path: output, alias };
  } catch (error) { if (out !== undefined) fs.closeSync(out); try { fs.unlinkSync(output); } catch {} throw error; }
  finally { fs.closeSync(fd); }
}
