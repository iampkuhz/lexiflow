import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cp, mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { assembleReleaseCandidate } from '../build/release-candidate-assembler.mjs';
import { extensionBytes } from './zip-fixture.mjs';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = value => createHash('sha256').update(value).digest('hex');

/** Synthetic, single linux/arm64 source/candidate fixture. It never uses viewing data. */
async function makeFixture(baseVersion = '2.0.0') {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-fixture-')));
  const outputParent = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-output-')));
  const imageDirectory = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-image-candidate-')));
  try {
    await mkdir(path.join(root, 'ops/release'), { recursive: true });
    for (const directory of ['build', 'integrity', 'runtime']) await mkdir(path.join(root, 'ops/release', directory), { recursive: true });
    await writeFile(path.join(root, 'ops/release/version.txt'), `${baseVersion}\n`);
    await writeFile(path.join(root, 'ops/release/integrity/source-build-identity.mjs'), await readFile(path.join(repoRoot, 'ops/release/integrity/source-build-identity.mjs')));
    await writeFile(path.join(root, 'ops/release/build/release-runtime-entry-generator.mjs'), await readFile(path.join(repoRoot, 'ops/release/build/release-runtime-entry-generator.mjs')));
    for (const name of ['runtime/release-artifact-verification.sh', 'runtime/release-installation-state.sh', 'runtime/release-installation-docker.sh', 'runtime/release-installation-lifecycle.sh']) {
      await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repoRoot, 'ops/release', name)));
    }
    await writeFile(path.join(root, '.gitignore'), '/input/\n/descriptor.json\n/release/\n/out/\n/alias/\n/.*.manifest-*/\n');
    execFileSync('git', ['init', '-q', root]);
    execFileSync('git', ['-C', root, 'add', '.']);
    execFileSync('git', ['-C', root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean candidate fixture']);
    const buildIdentity = resolveBuildIdentity(root);
    const softwareVersion = buildIdentity.softwareVersion;
    const sourceCommit = buildIdentity.sourceCommit;
    const artifactRoot = path.join(root, 'input'); await mkdir(artifactRoot);
    const licenses = []; const records = [];
    const hashFile = async (relative, value, licenseIds) => {
      const bytes = Buffer.from(value); const target = path.join(artifactRoot, relative);
      await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, bytes);
      records.push({ role: 'license', path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds });
      return { bytes, sha256: hash(bytes) };
    };
    const licenseSeeds = [
      ['lexiflow', 'LexiFlow'], ['ext-third-party', 'extension-third-party'],
      ['api-runtime', 'API-runtime'], ['postgresql', 'PostgreSQL'], ['dataset-license', 'dataset']
    ];
    for (const [id, component] of licenseSeeds) {
      const noticePath = `licenses/${id}.txt`; const notice = Buffer.from(`Synthetic notice ${id}\n`);
      const noticeSha256 = hash(notice);
      licenses.push({ id, component, licenseId: `TEST-${id}`, licenseName: `Synthetic ${id}`,
        sourceUrl: `https://example.invalid/${id}`, noticePath, noticeBytes: notice.length, noticeSha256 });
      await hashFile(noticePath, notice, [id]);
    }
    const dataset = { releaseId: 'release-test', preparationId: 'prep-test', ruleId: 'rules-test' };
    const sqlVersion = 'sql-test'; const apiContract = 'caption-hints.v2';
    const dataBytes = Buffer.from('synthetic published dataset'); const dataSha = hash(dataBytes);
    const dataPath = 'data/dataset.bin'; await mkdir(path.dirname(path.join(artifactRoot, dataPath)), { recursive: true });
    await writeFile(path.join(artifactRoot, dataPath), dataBytes);
    records.push({ role: 'dataset', path: dataPath, bytes: dataBytes.length, sha256: dataSha,
      licenseIds: ['dataset-license'], metadata: { ...dataset, sqlVersion } });
    const sqlBytes = Buffer.from('synthetic trusted SQL\n'); const sqlPath = 'database/schema.sql';
    await mkdir(path.dirname(path.join(artifactRoot, sqlPath)), { recursive: true }); await writeFile(path.join(artifactRoot, sqlPath), sqlBytes);
    records.push({ role: 'sql', path: sqlPath, bytes: sqlBytes.length, sha256: hash(sqlBytes), licenseIds: ['lexiflow'] });
    const expectedDataset = { ...dataset, sqlVersion, sha256: dataSha };
    const extensionPath = 'extension/package.zip'; const extension = extensionBytes(buildIdentity, expectedDataset);
    await mkdir(path.dirname(path.join(artifactRoot, extensionPath)), { recursive: true }); await writeFile(path.join(artifactRoot, extensionPath), extension);
    records.push({ role: 'extension', path: extensionPath, bytes: extension.length, sha256: hash(extension),
      licenseIds: ['lexiflow', 'ext-third-party'], metadata: { softwareVersion, sourceCommit, expectedDataset } });
    const imagePath = 'images/linux-arm64/runtime-image.tar'; const imageBytes = Buffer.from('synthetic linux/arm64 runtime image archive');
    await mkdir(path.dirname(path.join(imageDirectory, imagePath)), { recursive: true }); await writeFile(path.join(imageDirectory, imagePath), imageBytes);
    const imageRecord = { role: 'runtime-image', platform: 'linux/arm64', path: imagePath,
      bytes: imageBytes.length, sha256: hash(imageBytes), imageDigest: `sha256:${'c'.repeat(64)}` };
    const imageCandidate = { schemaVersion: 1, kind: 'lexiflow-image-candidate', buildIdentity, softwareVersion, sourceCommit,
      platform: 'linux/arm64', buildInputSha256: 'e'.repeat(64),
      baseImages: [{ platform: 'linux/arm64',
        javaRuntime: { reference: `registry.example.invalid/java/runtime@sha256:${'1'.repeat(64)}`, imageId: `sha256:${'a'.repeat(64)}` },
        postgresRuntime: { reference: `registry.example.invalid/postgres/runtime@sha256:${'2'.repeat(64)}`, imageId: `sha256:${'b'.repeat(64)}` } }],
      artifacts: [{ ...imageRecord, platform: 'linux/arm64' }] };
    const marker = Buffer.from(`${JSON.stringify(imageCandidate, null, 2)}\n`);
    await writeFile(path.join(imageDirectory, 'candidate.json'), marker);
    const descriptor = { schemaVersion: 1, buildIdentity, softwareVersion, sourceCommit, apiContract, sqlVersion, dataset,
      platforms: ['linux/arm64'], artifacts: [
        { role: 'runtime-image', platform: 'linux/arm64', licenseIds: ['lexiflow', 'api-runtime', 'postgresql'] },
        ...records
      ], licenses };
    const descriptorFile = path.join(root, 'descriptor.json'); await writeFile(descriptorFile, `${JSON.stringify(descriptor, null, 2)}\n`);
    return { root, artifactRoot, outputParent, imageDirectory, imageCandidate,
      imageCandidateDirectories: [{ directory: imageDirectory, sha256: hash(marker), platform: 'linux/arm64' }],
      descriptor, descriptorFile, sourceArchive: null,
      cleanup: async () => { await rm(root, { recursive: true, force: true }); await rm(outputParent, { recursive: true, force: true }); await rm(imageDirectory, { recursive: true, force: true }); } };
  } catch (error) {
    await rm(root, { recursive: true, force: true }); await rm(outputParent, { recursive: true, force: true }); await rm(imageDirectory, { recursive: true, force: true }); throw error;
  }
}

async function makeAssembledTemplate(baseVersion = '2.0.0') {
  const f = await getFixtureTemplate(baseVersion);
  try {
    const result = await assembleReleaseCandidate({ repoRoot: f.root,
      imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
      artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent });
    const marker = await readFile(path.join(result.candidateDirectory, 'candidate.json'));
    const sourceRecord = result.manifest.artifacts.find(item => item.role === 'corresponding-sources');
    const sourcePath = path.join(result.candidateDirectory, 'payload', sourceRecord.path);
    const indexBytes = execFileSync('tar', ['-xOzf', sourcePath, 'index.json']);
    const index = JSON.parse(indexBytes.toString('utf8'));
    f.sourceArchive = { path: sourcePath, bytes: sourceRecord.bytes, sha256: sourceRecord.sha256,
      indexSha256: hash(indexBytes), index };
    Object.assign(f, result, { marker,
      input: { candidateDirectory: result.candidateDirectory, candidateSha256: hash(marker) } });
    return f;
  } catch (error) { await f.cleanup(); throw error; }
}

const fixtureTemplates = new Map();
const assembledTemplates = new Map();

function cached(map, version, create) {
  if (!map.has(version)) {
    const pending = create(version).catch(error => { map.delete(version); throw error; });
    map.set(version, pending);
  }
  return map.get(version);
}

function getFixtureTemplate(version) { return cached(fixtureTemplates, version, makeFixture); }
function getAssembledTemplate(version) { return cached(assembledTemplates, version, makeAssembledTemplate); }

async function temporaryPath(prefix) {
  const value = await realpath(await mkdtemp(path.join(tmpdir(), prefix)));
  await rm(value, { recursive: true, force: true });
  return value;
}

async function cloneFixture(template, withCandidate) {
  let root; let outputParent; let imageDirectory;
  try {
    root = await temporaryPath('lexiflow-candidate-clone-');
    await cp(template.root, root, { recursive: true, force: false, preserveTimestamps: true });
    outputParent = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-clone-output-')));
    imageDirectory = await temporaryPath('lexiflow-image-candidate-clone-');
    await cp(template.imageDirectory, imageDirectory, { recursive: true, force: false, preserveTimestamps: true });
    const imageCandidate = structuredClone(template.imageCandidate);
    const imageMarker = await readFile(path.join(imageDirectory, 'candidate.json'));
    const result = {
      root, artifactRoot: path.join(root, 'input'), outputParent, imageDirectory, imageCandidate,
      imageCandidateDirectories: [{ directory: imageDirectory, sha256: hash(imageMarker), platform: 'linux/arm64' }],
      descriptor: structuredClone(template.descriptor), descriptorFile: path.join(root, 'descriptor.json'),
      sourceArchive: null,
      cleanup: async () => {
        await rm(root, { recursive: true, force: true });
        await rm(outputParent, { recursive: true, force: true });
        await rm(imageDirectory, { recursive: true, force: true });
      }
    };
    if (!withCandidate) return result;
    const candidateDirectory = path.join(outputParent, path.basename(template.candidateDirectory));
    await cp(template.candidateDirectory, candidateDirectory, { recursive: true, force: false, preserveTimestamps: true });
    const marker = await readFile(path.join(candidateDirectory, 'candidate.json'));
    const candidate = structuredClone(template.candidate);
    const manifest = structuredClone(template.manifest);
    const sourceRecord = manifest.artifacts.find(item => item.role === 'corresponding-sources');
    const sourcePath = path.join(candidateDirectory, 'payload', sourceRecord.path);
    Object.assign(result, {
      candidateDirectory, candidate, manifest, marker,
      sourceArchive: { ...structuredClone(template.sourceArchive), path: sourcePath },
      input: { candidateDirectory, candidateSha256: hash(marker) }
    });
    return result;
  } catch (error) {
    if (root) await rm(root, { recursive: true, force: true }).catch(() => {});
    if (outputParent) await rm(outputParent, { recursive: true, force: true }).catch(() => {});
    if (imageDirectory) await rm(imageDirectory, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
}

export async function fixture(baseVersion = '2.0.0') {
  return cloneFixture(await getFixtureTemplate(baseVersion), false);
}

export async function assembled(baseVersion = '2.0.0') {
  return cloneFixture(await getAssembledTemplate(baseVersion), true);
}

test.after(async () => {
  const templates = new Set();
  for (const pending of [...fixtureTemplates.values(), ...assembledTemplates.values()]) {
    try { templates.add(await pending); } catch { /* preserve the original test failure */ }
  }
  for (const template of templates) await template.cleanup();
});
