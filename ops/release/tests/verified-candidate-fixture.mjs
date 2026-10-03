import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assembleReleaseCandidate } from '../candidate.mjs';
import { extensionBytes } from './zip-fixture.mjs';
import { resolveBuildIdentity } from '../version.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = (value) => createHash('sha256').update(value).digest('hex');
export async function fixture(baseVersion = '2.0.0') {
  const root = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-fixture-')));
  await mkdir(path.join(root, 'ops/release'), { recursive: true });
  await writeFile(path.join(root, 'ops/release/version.txt'), `${baseVersion}\n`);
  await writeFile(path.join(root, 'ops/release/version.mjs'), await readFile(path.join(repoRoot, 'ops/release/version.mjs')));
  await writeFile(path.join(root, 'ops/release/runtime-entry.mjs'), await readFile(path.join(repoRoot, 'ops/release/runtime-entry.mjs')));
  await writeFile(path.join(root, 'ops/release/runtime-verification.sh'), await readFile(path.join(repoRoot, 'ops/release/runtime-verification.sh')));
  for (const name of ['lifecycle.mjs', 'lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh']) {
    await writeFile(path.join(root, 'ops/release', name), await readFile(path.join(repoRoot, 'ops/release', name)));
  }
  await writeFile(path.join(root, '.gitignore'), '/input/\n/images/\n/output/\n/descriptor.json\n');
  execFileSync('git', ['init', '-q', root]);
  execFileSync('git', ['-C', root, 'add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean candidate fixture']);
  const sourceCommit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const buildIdentity = resolveBuildIdentity(root);
  const softwareVersion = buildIdentity.softwareVersion;
  const artifactRoot = path.join(root, 'input');
  const outputParent = await realpath(await mkdtemp(path.join(tmpdir(), 'lexiflow-candidate-output-')));
  await mkdir(artifactRoot);
  const licenses = []; const records = [];
  const candidateBases = [];
  async function add(role, relative, value, extra = {}, licenseIds = ['lexiflow']) {
    const bytes = Buffer.from(value); const target = path.join(artifactRoot, relative);
    await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, bytes);
    records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, ...extra });
    return bytes;
  }
  const components = ['LexiFlow', 'extension-third-party', 'API-runtime', 'PostgreSQL', 'dataset'];
  const ids = ['lexiflow', 'ext-third-party', 'api-runtime', 'postgresql', 'dataset-license'];
  for (let i = 0; i < components.length; i += 1) {
    const noticePath = `licenses/${ids[i]}.txt`; const notice = Buffer.from(`Synthetic notice ${ids[i]}\n`);
    licenses.push({ id: ids[i], component: components[i], licenseId: `TEST-${ids[i]}`, licenseName: `Synthetic ${ids[i]}`,
      sourceUrl: `https://example.invalid/${ids[i]}`, noticePath, noticeBytes: notice.length, noticeSha256: hash(notice) });
    await add('license', noticePath, notice, {}, [ids[i]]);
  }
  const dataset = { releaseId: 'release-test', preparationId: 'prep-test', ruleId: 'rules-test' };
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const javaRuntime = { reference: `registry.example.invalid/java/runtime@sha256:${'1'.repeat(64)}`, imageId: `sha256:${'a'.repeat(64)}` };
    const postgresRuntime = { reference: `registry.example.invalid/postgres/runtime@sha256:${'2'.repeat(64)}`, imageId: `sha256:${'b'.repeat(64)}` };
    const base = { platform, javaRuntime, postgresRuntime };
    candidateBases.push(base);
    for (const [role, filename, imageDigest, licenseIds] of [
      ['api-image', 'api-image.tar', `sha256:${'c'.repeat(64)}`, ['lexiflow', 'api-runtime']],
      ['postgres-image', 'postgres-image.tar', `sha256:${'d'.repeat(64)}`, ['postgresql']]
    ]) {
      const relative = `images/${platform.replace('/', '-')}/${filename}`;
      const bytes = Buffer.from(`synthetic ${platform} ${role} archive`);
      await mkdir(path.dirname(path.join(artifactRoot, relative)), { recursive: true });
      await writeFile(path.join(artifactRoot, relative), bytes);
      records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, platform, imageDigest });
    }
  }
  const descriptor = { schemaVersion: 1, buildIdentity, softwareVersion, sourceCommit, apiContract: 'api-test', sqlVersion: 'sql-test', dataset,
    platforms: ['linux/amd64', 'linux/arm64'], artifacts: records, licenses };
  for (const image of records.filter((record) => record.role === 'api-image' || record.role === 'postgres-image')) {
    const bytes = await readFile(path.join(artifactRoot, image.path));

  }
  await add('compose', 'compose.yaml', 'synthetic compose');
  await add('sql', 'database/schema.sql', 'synthetic SQL');
  await add('dataset', 'data/dataset.bin', 'synthetic dataset', { metadata: { ...dataset, sqlVersion: 'sql-test' } }, ['dataset-license']);
  await add('extension', 'extension/package.zip', extensionBytes(buildIdentity), { metadata: { softwareVersion, sourceCommit } }, ['lexiflow', 'ext-third-party']);
  const imageCandidateDirectories = [];
  const buildInputSha256 = 'e'.repeat(64);
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const directory = await realpath(await mkdtemp(path.join(tmpdir(), `lexiflow-shard-${platform.split('/')[1]}-`)));
    const candidateRecords = records.filter((record) => record.role === 'api-image' || record.role === 'postgres-image').filter((record) => record.platform === platform)
      .map(({ role, platform: p, path: artifactPath, bytes, sha256, imageDigest }) => ({ role, platform: p, path: artifactPath, bytes, sha256, imageDigest }));
    const imageCandidate = { schemaVersion: 1, kind: 'lexiflow-image-candidate', buildIdentity, softwareVersion, sourceCommit, platform, buildInputSha256,
      baseImages: [candidateBases.find((base) => base.platform === platform)], artifacts: candidateRecords };
    for (const record of candidateRecords) { const target = path.join(directory, record.path); await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, await readFile(path.join(artifactRoot, record.path))); }
    const marker = `${JSON.stringify(imageCandidate, null, 2)}\n`; await writeFile(path.join(directory, 'candidate.json'), marker);
    imageCandidateDirectories.push({ directory, sha256: hash(Buffer.from(marker)) });
  }
  const descriptorFile = path.join(root, 'descriptor.json'); await writeFile(descriptorFile, JSON.stringify(descriptor));
  return { root, artifactRoot, imageCandidateDirectories: imageCandidateDirectories.map(({ directory, sha256 }, index) => ({ directory, sha256, platform: index === 0 ? 'linux/amd64' : 'linux/arm64' })), outputParent, descriptor, descriptorFile,
    cleanup: async () => { await rm(root, { recursive: true, force: true }); await rm(outputParent, { recursive: true, force: true }); for (const { directory } of imageCandidateDirectories) await rm(directory, { recursive: true, force: true }); } };
}

export async function assembled(baseVersion = '2.0.0') {
  const f = await fixture(baseVersion);
  const result = await assembleReleaseCandidate({ repoRoot: f.root,
    imageCandidates: f.imageCandidateDirectories.map(({ directory, sha256 }) => ({ directory, sha256 })),
    artifactRoot: f.artifactRoot, descriptor: f.descriptor, outputParent: f.outputParent });
  const marker = await readFile(path.join(result.candidateDirectory, 'candidate.json'));
  return { ...f, ...result, marker, input: { candidateDirectory: result.candidateDirectory, candidateSha256: hash(marker) } };
}
