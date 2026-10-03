import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, realpath, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { validateBuildIdentity, assertBuildIdentityMatches } from '../version.mjs';
import { verifyJarIdentity, verifyExtensionIdentity } from '../embedded-identity.mjs';
import { makeZip, jarBytes, extensionBytes } from './zip-fixture.mjs';

function identity({ dirty = false, snapshot = false, sourceSha256 = 'b'.repeat(64) } = {}) {
  const baseVersion = `2.0.0${snapshot ? '-SNAPSHOT' : ''}`, sourceCommit = 'a'.repeat(40);
  const softwareVersion = dirty || snapshot ? `2.0.0-SNAPSHOT.gaaaaaaa${dirty ? `.dirty.${sourceSha256.slice(0, 12)}` : ''}` : '2.0.0';
  return { schemaVersion: 1, baseVersion, softwareVersion, chromeVersion: `2.0.0.${dirty || snapshot ? 0 : 1}`,
    sourceCommit, sourceSha256, buildId: createHash('sha256').update(JSON.stringify({ baseVersion, sourceCommit, sourceSha256, dirty })).digest('hex'), dirty, channel: dirty || snapshot ? 'snapshot' : 'release' };
}
async function archive(bytes, fn) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lf-embedded-')));
  try { const file = path.join(root, 'artifact.zip'); await writeFile(file, bytes); await fn(file); }
  finally { await rm(root, { recursive: true, force: true }); }
}

test('full identity validates derivation and compares field order independently', () => {
  for (const options of [{}, { snapshot: true }, { dirty: true }, { snapshot: true, dirty: true }]) {
    const expected = identity(options);
    assert.equal(validateBuildIdentity(expected), expected);
    assert.doesNotThrow(() => assertBuildIdentityMatches(Object.fromEntries(Object.entries(expected).reverse()), expected));
    for (const field of Object.keys(expected)) {
      const changed = { ...expected, [field]: null };
      assert.throws(() => validateBuildIdentity(changed), /INVALID_BUILD_IDENTITY/);
    }
  }
  assert.throws(() => assertBuildIdentityMatches(identity(), identity({ sourceSha256: 'c'.repeat(64) })), /BUILD_IDENTITY_MISMATCH/);
  assert.throws(() => validateBuildIdentity({ ...identity(), extra: true }), /INVALID_BUILD_IDENTITY/);
});

test('JAR and ZIP metadata bind full release or clean SNAPSHOT identity', async () => {
  for (const expected of [identity(), identity({ snapshot: true })]) {
    await archive(jarBytes(expected), file => verifyJarIdentity(file, expected));
    await archive(extensionBytes(expected), file => verifyExtensionIdentity(file, expected));
  }
});

test('same version and commit cannot substitute another source hash or buildId', async () => {
  const expected = identity(), other = identity({ sourceSha256: 'c'.repeat(64) });
  for (const wrong of [other, { ...expected, buildId: '0'.repeat(64) }, { softwareVersion: expected.softwareVersion, sourceCommit: expected.sourceCommit }]) {
    await archive(jarBytes(wrong), file => assert.rejects(verifyJarIdentity(file, expected), /JAR_IDENTITY_MISMATCH/));
    await archive(extensionBytes(wrong), file => assert.rejects(verifyExtensionIdentity(file, expected), /EXTENSION_IDENTITY_MISMATCH/));
  }
});

test('JAR version and extension manifest are independently checked', async () => {
  const expected = identity();
  const build = JSON.stringify(expected);
  await archive(makeZip([['META-INF/lexiflow-build.json', build], ['META-INF/lexiflow-version.txt', '9.9.9\n']]), file => assert.rejects(verifyJarIdentity(file, expected), /JAR_IDENTITY_MISMATCH/));
  for (const mutation of [{ version: '9.9.9.1' }, { version_name: '9.9.9' }, { permissions: ['tabs'] }, { host_permissions: ['http://127.0.0.1:9999/*'] }]) {
    const manifest = { version: expected.chromeVersion, version_name: expected.softwareVersion, permissions: ['storage'], host_permissions: ['http://127.0.0.1:18080/*'], ...mutation };
    await archive(makeZip([['build-identity.json', build], ['manifest.json', JSON.stringify(manifest)]]), file => assert.rejects(verifyExtensionIdentity(file, expected), /EXTENSION_IDENTITY_MISMATCH/));
  }
});
