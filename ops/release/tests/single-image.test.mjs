import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { makeZip } from './zip-fixture.mjs';
import { prepareImageBuild } from '../build/release-image-build-plan.mjs';
import { resolveBuildIdentity } from '../integrity/source-build-identity.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'ignore', timeout: 10_000 });

async function fixture() {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-single-image-')));
  const copy = async (from, to) => { const target = path.join(root, to); await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, await readFile(path.join(repo, from))); };
  for (const file of ['ops/release/version.txt', 'ops/release/integrity/source-build-identity.mjs',
    'ops/release/integrity/artifact-build-identity.mjs', 'ops/release/integrity/extension-archive-integrity.mjs',
    'ops/release/integrity/release-artifact-manifest.mjs', 'ops/docker/Dockerfile.runtime',
    'ops/docker/runtime-entrypoint.sh', 'ops/docker/base-images.json', 'infra/postgres/schema.sql']) await copy(file, file);
  await writeFile(path.join(root, '.gitignore'), '/artifacts/\n');
  git(root, 'init', '-q'); git(root, 'add', '.');
  execFileSync('git', ['-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'single image fixture'], { stdio: 'ignore' });
  const identity = resolveBuildIdentity(root);
  const schema = await readFile(path.join(root, 'infra/postgres/schema.sql'));
  const jar = makeZip([['META-INF/lexiflow-build.json', JSON.stringify(identity)],
    ['META-INF/lexiflow-version.txt', `${identity.softwareVersion}\n`], ['META-INF/lexiflow-schema.sql', schema]]);
  const data = Buffer.from('synthetic published data'); const notice = Buffer.from('synthetic notice');
  const rootLicense = await readFile(path.join(repo, 'LICENSE'));
  const artifactRoot = path.join(root, 'artifacts'); await mkdir(path.join(artifactRoot, 'licenses'), { recursive: true });
  await writeFile(path.join(artifactRoot, 'api.jar'), jar);
  await writeFile(path.join(artifactRoot, 'dataset.zip'), data);
  await writeFile(path.join(artifactRoot, 'licenses/notice.txt'), notice);
  await writeFile(path.join(artifactRoot, 'LICENSE'), rootLicense);
  const descriptor = { schemaVersion: 1, softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit,
    jar: { path: 'api.jar', bytes: jar.length, sha256: hash(jar) }, apiContract: 'caption-hints.v2', sqlVersion: 'sql-test',
    dataset: { path: 'dataset.zip', bytes: data.length, sha256: hash(data), releaseId: 'release-1', preparationId: 'prep-1', ruleId: 'rule-1' },
    notices: [{ path: 'LICENSE', bytes: rootLicense.length, sha256: hash(rootLicense) },
      { path: 'licenses/notice.txt', bytes: notice.length, sha256: hash(notice) }] };
  return { root, artifactRoot, descriptor, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test('image plan is target-only, allowlisted and binds embedded dataset, notices, SQL and source identity', async () => {
  const f = await fixture();
  try {
    const plan = await prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: f.descriptor });
    assert.equal(plan.platform, 'linux/arm64');
    assert.deepEqual(plan.platforms.map(item => item.platform), ['linux/arm64']);
    assert.equal(plan.runtimeIdentity.buildIdentity.buildId, plan.buildIdentity.buildId);
    assert.equal(plan.runtimeIdentity.dataset.sha256, f.descriptor.dataset.sha256);
    assert.equal(plan.runtimeIdentityEnv.split('\n').filter(Boolean).length, 8);
    assert.equal(plan.platforms[0].commands.length, 1);
    assert.ok(plan.platforms[0].commands[0].includes('--platform'));
    assert.ok(plan.platforms[0].commands[0].includes('linux/arm64'));
    assert.deepEqual(plan.platforms[0].contextFiles.map(item => item.target).sort(),
      ['Dockerfile.runtime', 'dataset.zip', 'lexiflow-api.jar', 'notices/LICENSE', 'notices/notice.txt', 'runtime-entrypoint.sh', 'schema.sql'].sort());
    const license = plan.platforms[0].contextFiles.find(item => item.target === 'notices/LICENSE');
    assert.equal(license.path, 'LICENSE');
    assert.equal(license.sha256, f.descriptor.notices[0].sha256);
    assert.ok(!JSON.stringify(plan).includes(f.root));
  } finally { await f.cleanup(); }
});

test('root LICENSE stays byte-bound and other root files cannot become image notices', async () => {
  const f = await fixture();
  try {
    for (const filename of ['NOTICE', 'README.md', 'license']) {
      const invalid = structuredClone(f.descriptor);
      invalid.notices[0].path = filename;
      await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: invalid }), /NOTICES_INVALID/);
    }
    await writeFile(path.join(f.artifactRoot, 'LICENSE'), 'changed license');
    await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: f.descriptor }), /BUILD_INPUT_REJECTED/);
  } finally { await f.cleanup(); }
});

test('root LICENSE cannot collide with a licenses/LICENSE image target', async () => {
  const f = await fixture();
  try {
    const license = await readFile(path.join(f.artifactRoot, 'LICENSE'));
    await writeFile(path.join(f.artifactRoot, 'licenses/LICENSE'), license);
    f.descriptor.notices.push({ path: 'licenses/LICENSE', bytes: license.length, sha256: hash(license) });
    await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: f.descriptor }), /NOTICES_INVALID/);
  } finally { await f.cleanup(); }
});

test('rejects dataset byte drift and duplicate/unknown notice inputs before any image build', async () => {
  const f = await fixture();
  try {
    const badData = structuredClone(f.descriptor); badData.dataset.sha256 = '0'.repeat(64);
    await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: badData }), /BUILD_INPUT_REJECTED|DATASET_INVALID/);
    const badNotice = structuredClone(f.descriptor); badNotice.notices.push({ ...badNotice.notices[0] });
    await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: badNotice }), /BUILD_INPUT_REJECTED|NOTICES_INVALID/);
    const extra = structuredClone(f.descriptor); extra.untrustedPath = 'anything';
    await assert.rejects(prepareImageBuild({ repoRoot: f.root, artifactRoot: f.artifactRoot, descriptor: extra }), /BUILD_INPUT_REJECTED|DESCRIPTOR_INVALID/);
  } finally { await f.cleanup(); }
});
