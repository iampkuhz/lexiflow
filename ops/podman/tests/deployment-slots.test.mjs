import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { deploymentSlot, slotImageNamespace } from '../deployment-slots.mjs';

test('source and release validation use exactly one disjoint fixed API/DB pair and stable installation roots', () => {
  const repo = '/synthetic/repo';
  const source = deploymentSlot('source', repo), release = deploymentSlot('release-validation', repo);
  assert.deepEqual([source.apiPort, source.dbPort, source.directory], [18081, 15432, path.join(repo, '.local/podman')]);
  assert.deepEqual([release.apiPort, release.dbPort, release.directory], [18080, 15433, path.join(repo, '.local/podman-release-validation')]);
  assert.equal(new Set([source.apiPort, source.dbPort, release.apiPort, release.dbPort]).size, 4);
  assert.equal(slotImageNamespace('source'), 'lexiflow');
  assert.equal(slotImageNamespace('release-validation'), 'lexiflow-release-validation');
  assert.throws(() => deploymentSlot('other', repo), /DEPLOYMENT_SLOT_INVALID/);
});
