import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, realpath, writeFile, symlink, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { readZipEntries, readExtensionFiles } from '../archive-identity.mjs';
import { createHash } from 'node:crypto';
import { makeZip } from './zip-fixture.mjs';

async function withZip(bytes, fn) {
  const dir = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-zip-')));
  const file = path.join(dir, 'archive.zip');
  await writeFile(file, bytes);
  try { await fn(file, dir); } finally { await rm(dir, { recursive: true, force: true }); }
}
const isCode = (code) => (err) => err.message === code;

test('reads stored and deflate targets, including data descriptors', async () => {
  for (const options of [{}, { deflate: true }, { deflate: true, dataDescriptor: true }]) {
    await withZip(makeZip([['a.txt', 'hello'], ['b.txt', 'world']], options), async file => {
      const got = await readZipEntries(file, ['a.txt', 'b.txt']);
      assert.equal(got.get('a.txt').toString(), 'hello');
      assert.equal(got.get('b.txt').toString(), 'world');
    });
  }
});

test('rejects missing, repeated, unsafe and malformed names', async () => {
  await withZip(makeZip([['a.txt', 'x']]), async file => {
    await assert.rejects(readZipEntries(file, ['missing']), isCode('ARCHIVE_ENTRY_MISSING'));
    await assert.rejects(readZipEntries(file, ['a.txt', 'a.txt']), isCode('ARCHIVE_INVALID'));
  });
  await assert.rejects(readZipEntries('/nonexistent', ['../secret']), isCode('ARCHIVE_INVALID'));
  await withZip(makeZip([['../bad', 'x']]), async file => assert.rejects(readZipEntries(file, ['a']), isCode('ARCHIVE_INVALID')));
});

test('rejects central/local header conflict, CRC mismatch, truncated data, ZIP64 and encryption', async () => {
  const zip = makeZip([['a.txt', 'hello']]);
  const conflict = Buffer.from(zip); conflict.writeUInt16LE(8, 6);
  await withZip(conflict, async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
  const badCrc = Buffer.from(zip); badCrc[14] ^= 1;
  await withZip(badCrc, async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
  await withZip(zip.subarray(0, zip.length - 1), async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
  const zip64 = Buffer.from(zip); zip64.writeUInt32LE(0xffffffff, zip64.length - 22 + 12);
  await withZip(zip64, async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
  const encrypted = Buffer.from(zip); encrypted.writeUInt16LE(1, encrypted.readUInt32LE(encrypted.length - 6) + 8);
  await withZip(encrypted, async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
});

test('rejects duplicate archive names, decompression bombs and symlink paths', async () => {
  await withZip(makeZip([['a.txt', '1'], ['a.txt', '2']]), async f => assert.rejects(readZipEntries(f, ['a.txt']), isCode('ARCHIVE_INVALID')));
  await withZip(makeZip([['large', Buffer.alloc(70 * 1024, 65)]], { deflate: true }), async f => assert.rejects(readZipEntries(f, ['large']), isCode('ARCHIVE_LIMIT_EXCEEDED')));
  const dir = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-link-')));
  try {
    const actual = path.join(dir, 'actual'); await writeFile(actual, makeZip([['a', 'x']]));
    const link = path.join(dir, 'link'); await symlink(actual, link);
    await assert.rejects(readZipEntries(link, ['a']));
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('rejects oversized compressed payloads, ambiguous paths, unsupported flags and overlapping local records', async () => {
  const badPath = makeZip([['x/./a', 'x']]);
  await withZip(badPath, async f => assert.rejects(readZipEntries(f, ['x']), isCode('ARCHIVE_INVALID')));
  const drivePath = makeZip([['C:/secret', 'x']]);
  await withZip(drivePath, async f => assert.rejects(readZipEntries(f, ['x']), isCode('ARCHIVE_INVALID')));
  const unsupportedFlags = Buffer.from(makeZip([['a', 'x']]));
  unsupportedFlags.writeUInt16LE(0x0040, 6);
  await withZip(unsupportedFlags, async f => assert.rejects(readZipEntries(f, ['a']), isCode('ARCHIVE_INVALID')));
  const hugeCompressed = Buffer.from(makeZip([['a', 'x']]));
  const cd = hugeCompressed.length - 22 - 47;
  hugeCompressed.writeUInt32LE(0xffffffff, cd + 20);
  await withZip(hugeCompressed, async f => assert.rejects(readZipEntries(f, ['a']), isCode('ARCHIVE_INVALID')));
  const overlap = Buffer.from(makeZip([['a', 'one'], ['b', 'two']]));
  const centralStart = overlap.length - 22 - (2 * 47);
  const firstOffset = overlap.readUInt32LE(centralStart + 42);
  overlap.writeUInt32LE(firstOffset, centralStart + 47 + 42);
  await withZip(overlap, async f => assert.rejects(readZipEntries(f, ['a', 'b']), isCode('ARCHIVE_INVALID')));
});

test('rejects parent symlink traversal', async () => {
  const dir = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-parent-link-')));
  try {
    const real = path.join(dir, 'real'); await import('node:fs/promises').then(fs => fs.mkdir(path.join(real, 'sub'), { recursive: true }));
    await writeFile(path.join(real, 'sub', 'archive.zip'), makeZip([['a', 'x']]));
    const alias = path.join(dir, 'alias'); await symlink(real, alias, 'dir');
    await assert.rejects(readZipEntries(path.join(alias, 'sub', 'archive.zip'), ['a']), isCode('ARCHIVE_INVALID'));
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('reads complete extension files and binds archive digest to expected bytes', async () => {
  const bytes = makeZip([['manifest.json', '{"version":"1"}'], ['assets/app.js', 'hello'], ['__archiveSha256', 'ordinary file']], { deflate: true });
  await withZip(bytes, async file => {
    const entries = await readExtensionFiles(file, createHash('sha256').update(bytes).digest('hex'));
    assert.deepEqual([...entries.keys()].sort(), ['__archiveSha256', 'assets/app.js', 'manifest.json']);
    assert.equal(entries.get('assets/app.js').toString(), 'hello');
    await assert.rejects(readExtensionFiles(file, '0'.repeat(64)), isCode('ARCHIVE_INVALID'));
  });
});

test('full extension mode rejects case collisions and ancestor-file conflicts', async () => {
  for (const entries of [
    [['A.js', 'a'], ['a.js', 'b']],
    [['folder', 'file'], ['folder/app.js', 'child']],
    [['A', 'file'], ['a/b.js', 'child']],
    [['Foo/x.js', 'a'], ['foo/y.js', 'b']],
    [['foo/', ''], ['foo', 'file']],
  ]) await withZip(makeZip(entries), file => assert.rejects(readExtensionFiles(file, createHash('sha256').update(makeZip(entries)).digest('hex')), isCode('ARCHIVE_INVALID')));
});


test('rejects ZIP symlink and special-file attributes for requested or unrelated entries', async () => {
  for (const index of [0, 1]) for (const mode of [0o120777, 0o010600, 0o020600, 0o060600]) {
    const bytes = makeZip([['identity', '{}'], ['unrelated', 'payload']]);
    const central = bytes.readUInt32LE(bytes.length - 6) + (index ? 46 + Buffer.byteLength('identity') : 0);
    bytes.writeUInt16LE((3 << 8) | 20, central + 4);
    bytes.writeUInt32LE((mode << 16) >>> 0, central + 38);
    await withZip(bytes, file => assert.rejects(readZipEntries(file, ['identity']), isCode('ARCHIVE_INVALID')));
  }
  const valid = makeZip([['directory/', ''], ['directory/Outer$Inner.class', 'synthetic Java class']]);
  const start = valid.readUInt32LE(valid.length - 6);
  for (const [offset, mode] of [[start, 0o040755], [start + 46 + Buffer.byteLength('directory/'), 0o100644]]) {
    valid.writeUInt16LE((3 << 8) | 20, offset + 4);
    valid.writeUInt32LE((mode << 16) >>> 0, offset + 38);
  }
  await withZip(valid, async file => assert.equal((await readZipEntries(file, ['directory/Outer$Inner.class'])).get('directory/Outer$Inner.class').toString(), 'synthetic Java class'));
});
