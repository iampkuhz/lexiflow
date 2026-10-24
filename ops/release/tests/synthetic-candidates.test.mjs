import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, realpath } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { Readable } from 'node:stream';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { extensionBytes } from './zip-fixture.mjs';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const sha = data => createHash('sha256').update(data).digest('hex');
const dataset = (i) => ({ file: ['dataset-a.zip', 'dataset-b.zip'][i], bytes: 5,
  sha256: sha(`zip-${i}`), datasetVersion: i + 1, preparationPolicy: 'lexiflow.deterministic-preparation.v1' });
const input = { scratchDirectory: '/private/synthetic-test', endpoint: 'unix:///private/docker.sock', datasets: [dataset(0), dataset(1)] };
const { validateSyntheticInput, syntheticDescriptor, readSyntheticInput, syntheticLayout } = await import('../test_tools/build-synthetic-release-candidates.mjs');

test('fixed input rejects modes, platforms, path selection, shape and non-distinct generations', () => {
  assert.equal(validateSyntheticInput(input), input);
  for (const value of [{ ...input, platform: 'linux/amd64' }, { ...input, operation: 'images' },
    { ...input, repoRoot: '/untrusted' }, { ...input, endpoint: 'tcp://untrusted:2375' },
    { ...input, datasets: [dataset(0), dataset(0)] }, { ...input, scratchDirectory: '/private/../tmp/x' },
    { ...input, datasets: [{ ...dataset(0), bytes: 0 }, dataset(1)] }])
    assert.throws(() => validateSyntheticInput(value));
});
test('anonymous stdin is bounded and validates exact shape', async () => {
  assert.deepEqual(await readSyntheticInput(Readable.from([Buffer.from(JSON.stringify(input))])), input);
  await assert.rejects(readSyntheticInput(Readable.from([Buffer.alloc(65 * 1024)])));
  await assert.rejects(readSyntheticInput(Readable.from([Buffer.from(JSON.stringify(input).replace('{"scratchDirectory":', '{"scratchDirectory":"ignored","scratchDirectory":'))])));
});
test('synthetic descriptor derives fixed identities without redistribution eligibility', () => {
  const descriptor = syntheticDescriptor({ softwareVersion: '1.2.3', sourceCommit: 'a'.repeat(40) }, [], [], dataset(0), 'b'.repeat(64));
  assert.deepEqual(descriptor.platforms, ['linux/arm64']);
  assert.equal(descriptor.dataset.releaseId, `synthetic-${dataset(0).sha256}`);
  assert.equal(descriptor.artifacts[0].metadata.ruleId, 'synthetic-no-model');
});

test('frozen source inside owned scratch is allowed, actual artifact/output overlaps are not', () => {
  const layout = syntheticLayout('/private/scratch/source-parent/fixture', '/private/scratch');
  assert.equal(layout.artifactRoot, '/private/scratch/artifacts');
  for (const root of ['/private/scratch/artifacts/repo', '/private/scratch/synthetic-images/repo', '/private/scratch'])
    assert.throws(() => syntheticLayout(root, '/private/scratch'));
});

test('single Node transaction really calls one image build, two assembly and verification operations', async (t) => {
  // Native test loader replaces imported dependencies; production has no injectable executor seam.
  const calls = []; const identity = { softwareVersion: '1.2.3', sourceCommit: 'a'.repeat(40), buildId: 'c'.repeat(64), dirty: false };
  t.mock.module('../integrity/source-build-identity.mjs', { namedExports: { resolveCleanBuildIdentity: () => identity } });
  const extensionBindings = []; const imageBindings = [];
  t.mock.module(pathToFileURL(path.join(repo, 'extension/scripts/packaging/package-extension-release.mjs')).href, { namedExports: { createExtensionZip: async ({ buildIdentity, expectedDataset }) => {
    extensionBindings.push(expectedDataset); assert.deepEqual(buildIdentity, identity);
    return { archive: extensionBytes(identity, expectedDataset) };
  } } });
  t.mock.module('../build/release-image-builder.mjs', { namedExports: { buildCandidateImages: async request => {
    calls.push('images'); assert.equal(request.platform, 'linux/arm64'); imageBindings.push(request.descriptor.dataset);
    assert.equal(request.endpoint, input.endpoint);
    const directory = path.join(request.outputParent, `candidate-${calls.filter(item => item === 'images').length}`); await mkdir(directory);
    await writeFile(path.join(directory, 'candidate.json'), '{}'); return { candidateDirectory: directory };
  } } });
  t.mock.module('../build/release-candidate-assembler.mjs', { namedExports: { assembleReleaseCandidate: async request => {
    calls.push('assemble'); assert.equal(request.imageCandidates.length, 1);
    assert(request.descriptor.licenses.every(row => row.licenseId === 'UNVERIFIED-SYNTHETIC-ONLY'));
    assert.equal(request.descriptor.dataset.ruleId, 'synthetic-no-model');
    const directory = path.join(request.outputParent, `candidate-${calls.length}`); await mkdir(directory);
    await writeFile(path.join(directory, 'candidate.json'), JSON.stringify({ buildIdentity: identity }));
    return { candidateDirectory: directory };
  } } });
  t.mock.module('../integrity/release-candidate-verifier.mjs', { namedExports: { verifyReleaseCandidate: async request => {
    calls.push('verify'); return { candidateSha256: request.candidateSha256, buildIdentity: identity };
  } } });
  const { assembleSyntheticCandidates } = await import('../test_tools/build-synthetic-release-candidates.mjs?transaction-test');
  const scratch = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-synthetic-test-')));
  try {
    await mkdir(path.join(scratch, 'artifacts'));
    await writeFile(path.join(scratch, 'artifacts/api.jar'), 'jar');
    await mkdir(path.join(scratch, 'producer-output/synthetic-datasets'), { recursive: true });
    for (let i = 0; i < 2; i++) await writeFile(path.join(scratch, 'producer-output/synthetic-datasets', dataset(i).file), `zip-${i}`);
    const result = await assembleSyntheticCandidates({ ...input, scratchDirectory: scratch });
    assert.deepEqual(calls, ['images', 'assemble', 'verify', 'images', 'assemble', 'verify']);
    assert.equal(result.candidates.length, 2); assert.equal(result.apiContract, 'caption-hints.v2');
    assert.equal(extensionBindings.length, 2);
    assert.notEqual(extensionBindings[0].sha256, extensionBindings[1].sha256);
    assert.deepEqual(extensionBindings.map(binding => binding.releaseId), input.datasets.map(row => `synthetic-${row.sha256}`));
    assert.deepEqual(imageBindings.map(binding => binding.sha256), input.datasets.map(row => row.sha256));
    assert.deepEqual(result.datasets, input.datasets.map(row => ({ version: row.datasetVersion, sha256: row.sha256 })));
  } finally { await rm(scratch, { recursive: true }); }
});
