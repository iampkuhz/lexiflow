import test from 'node:test';
import assert from 'node:assert/strict';
import { removeCandidateImageAlias } from '../images/candidate-image-alias.mjs';

const id = `sha256:${'a'.repeat(64)}`;
const buildIdentity = { baseVersion: '1.2.3', softwareVersion: '1.2.3.20261012.gabcdef0',
  sourceCommit: 'b'.repeat(40), sourceSha256: 'c'.repeat(64), buildId: 'd'.repeat(64) };
const source = 'localhost/lexiflow-api:1.2.3';
const alias = 'localhost/lexiflow-release-validation-api:1.2.3';
const labels = { 'io.lexiflow.image-role': 'api', 'io.lexiflow.deployment-version': '1.2.3',
  'io.lexiflow.build-id': buildIdentity.buildId, 'io.lexiflow.source-sha256': buildIdentity.sourceSha256,
  'org.opencontainers.image.revision': buildIdentity.sourceCommit,
  'org.opencontainers.image.version': buildIdentity.softwareVersion };
const args = { role: 'api', baseVersion: '1.2.3', allowedImages: [{ imageDigest: id, buildIdentity }] };
function fake({ unknown = false, drift = false } = {}) {
  const tags = new Map([[source, id], [alias, id]]), calls = [];
  let reads = 0;
  const run = async (_tool, argv) => {
    calls.push(argv.join(' '));
    if (argv[0] === 'image' && argv[1] === 'exists') {
      if (!tags.has(argv[2])) throw Object.assign(new Error('absent'), { commandExitCode: 1 });
      return '';
    }
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      reads++;
      return JSON.stringify([{ Id: drift && reads > 1 ? `sha256:${'e'.repeat(64)}` : tags.get(argv[2]),
        RepoTags: [...tags.keys()], Config: { Labels: { ...labels, ...(unknown ? { 'io.lexiflow.build-id': 'foreign' } : {}) } } }]);
    }
    if (argv[0] === 'image' && argv[1] === 'untag') { tags.delete(argv[2]); return ''; }
    throw Error('unexpected');
  };
  return { run, tags, calls };
}

test('shared full image ID cleanup untags only candidate alias and leaves source tag untouched', async () => {
  const f = fake();
  const result = await removeCandidateImageAlias({ run: f.run, ...args });
  assert.deepEqual(result, { removed: true, alias, imageId: id });
  assert.equal(f.tags.get(source), id);
  assert.equal(f.tags.has(alias), false);
  assert.deepEqual(f.calls.filter(x => x.startsWith('image untag')), [`image untag ${alias}`]);
  assert.ok(!f.calls.some(x => x.startsWith('rmi ') || x.includes('--force')));
  assert.equal((await removeCandidateImageAlias({ run: f.run, ...args })).removed, false);
});

test('unknown image identity or alias drift refuses untag without touching either tag', async () => {
  for (const options of [{ unknown: true }, { drift: true }]) {
    const f = fake(options);
    await assert.rejects(removeCandidateImageAlias({ run: f.run, ...args }), /CANDIDATE_ALIAS_/);
    assert.equal(f.tags.get(source), id);
    assert.equal(f.tags.get(alias), id);
    assert.ok(!f.calls.some(x => x.startsWith('image untag')));
  }
});
