import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { deploymentSlot, slotImageNamespace } from '../deployment-slots.mjs';

test('source and release validation use disjoint fixed API ports and stable installation roots', () => {
  const repo = '/synthetic/repo';
  const source = deploymentSlot('source', repo), release = deploymentSlot('release-validation', repo);
  assert.deepEqual([source.apiPort, source.directory], [18081, path.join(repo, '.local/podman')]);
  assert.deepEqual([release.apiPort, release.directory], [18080, path.join(repo, '.local/podman-release-validation')]);
  assert.equal('dbPort' in source, false); assert.equal('dbPort' in release, false);
  assert.equal(slotImageNamespace('source'), 'lexiflow');
  assert.equal(slotImageNamespace('release-validation'), 'lexiflow-release-validation');
  assert.throws(() => deploymentSlot('other', repo), /DEPLOYMENT_SLOT_INVALID/);
});
