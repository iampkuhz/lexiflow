import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import { isolateCandidateArchive } from '../images/isolated-candidate-archive.mjs';
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function header(name, size, type = '0', link = '') {
  const b = Buffer.alloc(512);
  b.write(name, 0, 100, 'ascii'); b.write('0000600\0', 100, 8, 'ascii');
  b.write('0000000\0', 108, 8, 'ascii'); b.write('0000000\0', 116, 8, 'ascii');
  b.write(size.toString(8).padStart(11, '0') + '\0', 124, 12, 'ascii');
  b.write('00000000000\0', 136, 12, 'ascii'); b.fill(32, 148, 156); b.write(type, 156, 1, 'ascii');
  b.write('ustar\0', 257, 6, 'ascii'); b.write('00', 263, 2, 'ascii');
  if (link) b.write(link, 157, 100, 'ascii');
  b.write(b.reduce((n, x) => n + x, 0).toString(8).padStart(6, '0') + '\0 ', 148, 8, 'ascii');
  return b;
}
function tar(files) { return Buffer.concat([...files.flatMap(([name, data, type, link]) => {
  const bytes = Buffer.from(data);
  return [header(name, bytes.length, type, link), bytes, Buffer.alloc((512 - bytes.length % 512) % 512)];
}), Buffer.alloc(1024)]); }
function extract(bytes) {
  const out = new Map(); let pos = 0;
  while (pos < bytes.length && !bytes.subarray(pos, pos + 512).every(x => x === 0)) {
    const block = bytes.subarray(pos, pos + 512);
    const name = block.subarray(0, 100).toString('ascii').split('\0')[0];
    const size = Number.parseInt(block.subarray(124, 136).toString('ascii').split('\0')[0], 8);
    out.set(name, bytes.subarray(pos + 512, pos + 512 + size));
    pos += 512 + Math.ceil(size / 512) * 512;
  }
  return out;
}
test('Docker save metadata is isolated before load without changing signed config and layer bytes', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-isolate-'));
  try {
    const config = Buffer.from('{"architecture":"arm64","os":"linux"}');
    const id = `sha256:${sha(config)}`, layer = Buffer.from('layer bytes'), layerName = 'a/layer.tar';
    const original = tar([
      ['manifest.json', JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: ['localhost/lexiflow-api:1.2.3'], Layers: [layerName] }])],
      [id.slice(7) + '.json', config], [layerName, layer],
      ['repositories', JSON.stringify({ 'localhost/lexiflow-api': { '1.2.3': id.slice(7) } })],
    ]);
    const archive = path.join(root, 'input.tar'), output = path.join(root, 'derived.tar');
    fs.writeFileSync(archive, original);
    const result = isolateCandidateArchive({ archive, output, expectedSha256: sha(original), expectedBytes: original.length,
      imageDigest: id, role: 'api', baseVersion: '1.2.3' });
    assert.equal(result.alias, 'localhost/lexiflow-release-validation-api:1.2.3');
    assert.equal(sha(fs.readFileSync(archive)), sha(original));
    const input = extract(original), derived = extract(fs.readFileSync(output));
    assert.deepEqual(derived.get(id.slice(7) + '.json'), input.get(id.slice(7) + '.json'));
    assert.deepEqual(derived.get(layerName), input.get(layerName));
    assert.deepEqual(JSON.parse(derived.get('manifest.json')).at(0).RepoTags, [result.alias]);
    assert.deepEqual(Object.keys(JSON.parse(derived.get('repositories'))), ['localhost/lexiflow-release-validation-api']);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
test('accepts only Podman legacy layer symlinks into manifest-listed regular root blobs', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-isolate-links-'));
  try {
    const config = Buffer.from('{}'), id = `sha256:${sha(config)}`;
    const layer = `${'a'.repeat(64)}.tar`, chain = `${'b'.repeat(64)}/layer.tar`;
    const manifest = JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: [], Layers: [layer] }]);
    const base = [['manifest.json', manifest], [id.slice(7) + '.json', config], [layer, 'exact-layer-bytes'],
      [chain, '', '2', `../${layer}`], ['repositories', '{}']];
    const check = (files, accepted) => {
      const bytes = tar(files), archive = path.join(root, `${sha(bytes)}.tar`), output = `${archive}.out`;
      fs.writeFileSync(archive, bytes);
      const action = () => isolateCandidateArchive({ archive, output, expectedSha256: sha(bytes), expectedBytes: bytes.length,
        imageDigest: id, role: 'api', baseVersion: '1.2.3' });
      if (!accepted) { assert.throws(action, /CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED/); assert.equal(fs.existsSync(output), false); }
      else {
        action(); const rewritten = extract(fs.readFileSync(output));
        assert.deepEqual(rewritten.get(layer), Buffer.from('exact-layer-bytes'));
        assert.deepEqual(JSON.parse(rewritten.get('manifest.json'))[0].RepoTags, ['localhost/lexiflow-release-validation-api:1.2.3']);
        assert.equal(sha(fs.readFileSync(archive)), sha(bytes));
      }
    };
    check(base, true);
    for (const bad of [`../../${layer}`, `../${'c'.repeat(64)}.tar`, `/${layer}`, `../${layer}/evil`])
      check(base.map(x => x[0] === chain ? [chain, '', '2', bad] : x), false);
    check(base.map(x => x[0] === layer ? [layer, '', '2', `../${layer}`] : x), false);
    check(base.map(x => x[0] === chain ? ['other/layer.tar', '', '2', `../${layer}`] : x), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
test('rejects malformed, multiple-image, PAX and changed archive before creating load input', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-isolate-'));
  try {
    const config = Buffer.from('{}'), id = `sha256:${sha(config)}`;
    const cases = [
      tar([['manifest.json', JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: [], Layers: [] }])], [id.slice(7) + '.json', config]]),
      tar([['manifest.json', JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: [], Layers: ['layer.tar'] }, { Config: `${id.slice(7)}.json`, RepoTags: [], Layers: ['layer.tar'] }])], [id.slice(7) + '.json', config], ['layer.tar', 'x']]),
      tar([['manifest.json', '[]', 'x']]),
    ];
    for (let i = 0; i < cases.length; i++) {
      const archive = path.join(root, `${i}.tar`), output = path.join(root, `${i}.out`);
      fs.writeFileSync(archive, cases[i]);
      assert.throws(() => isolateCandidateArchive({ archive, output, expectedSha256: sha(cases[i]), expectedBytes: cases[i].length,
        imageDigest: id, role: 'api', baseVersion: '1.2.3' }), /CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED/);
      assert.equal(fs.existsSync(output), false);
    }
    const valid = tar([['manifest.json', JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: [], Layers: ['layer.tar'] }])], [id.slice(7) + '.json', config], ['layer.tar', 'x']]);
    const archive = path.join(root, 'bad-sha.tar'), output = path.join(root, 'bad-sha.out'); fs.writeFileSync(archive, valid);
    assert.throws(() => isolateCandidateArchive({ archive, output, expectedSha256: '0'.repeat(64), expectedBytes: valid.length,
      imageDigest: id, role: 'api', baseVersion: '1.2.3' }), /CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED/);
    assert.equal(fs.existsSync(output), false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('traversal, symlink, corrupt checksum, compressed and oversized inputs all fail before load', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-isolate-'));
  try {
    const config = Buffer.from('{}'), id = `sha256:${sha(config)}`;
    const manifest = JSON.stringify([{ Config: `${id.slice(7)}.json`, RepoTags: ['localhost/lexiflow-api:1.2.3'], Layers: ['layer.tar'] }]);
    const base = tar([['manifest.json', manifest], [id.slice(7) + '.json', config], ['layer.tar', 'x']]);
    const checksum = Buffer.from(base); checksum[0] = 88;
    const cases = [
      checksum,
      tar([['../manifest.json', manifest], [id.slice(7) + '.json', config], ['layer.tar', 'x']]),
      tar([['manifest.json', manifest], [id.slice(7) + '.json', config], ['layer.tar', 'x'], ['alias', '', '2']]),
      zlib.gzipSync(base),
    ];
    for (let i = 0; i < cases.length; i++) {
      const archive = path.join(root, `${i}.tar`), output = path.join(root, `${i}.out`);
      fs.writeFileSync(archive, cases[i]);
      assert.throws(() => isolateCandidateArchive({ archive, output, expectedSha256: sha(cases[i]), expectedBytes: cases[i].length,
        imageDigest: id, role: 'api', baseVersion: '1.2.3' }), /CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED/);
      assert.equal(fs.existsSync(output), false);
    }
    const archive = path.join(root, 'large.tar'), output = path.join(root, 'large.out'); fs.writeFileSync(archive, base);
    assert.throws(() => isolateCandidateArchive({ archive, output, expectedSha256: sha(base), expectedBytes: 4_000_000_001,
      imageDigest: id, role: 'api', baseVersion: '1.2.3' }), /CANDIDATE_IMAGE_ARCHIVE_UNSUPPORTED/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
