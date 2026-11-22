import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { assertSourceContainerSlot, deploymentSlot, slotImageNamespace } from '../deployment-slots.mjs';

test('source and release validation use disjoint fixed API ports and stable installation roots', () => {
  const repo = '/synthetic/repo';
  const source = deploymentSlot('source', repo), release = deploymentSlot('release-validation', repo);
  assert.deepEqual([source.apiPort, source.directory], [18081, path.join(repo, '.local/podman')]);
  assert.deepEqual([release.apiPort, release.directory], [18080, path.join(repo, '.local/podman-release-validation')]);
  const localTest = deploymentSlot('local-test', repo);
  assert.deepEqual([localTest.apiPort, localTest.directory], [19081, path.join(os.userInfo().homedir, 'Library/Application Support/LexiFlow/Testing')]);
  assert.equal(deploymentSlot('local-test', '/different/checkout').directory, localTest.directory);
  assert.equal(slotImageNamespace('local-test'), 'lexiflow-test');
  assert.equal('dbPort' in source, false); assert.equal('dbPort' in release, false);
  assert.equal(slotImageNamespace('source'), 'lexiflow');
  assert.equal(slotImageNamespace('release-validation'), 'lexiflow-release-validation');
  assert.equal(source.containerName, 'lexiflow-test');
  assert.equal(deploymentSlot('source', '/different/checkout').containerName, source.containerName);
  assert.throws(() => deploymentSlot('other', repo), /DEPLOYMENT_SLOT_INVALID/);
});

test('stopped or running occupants cannot be adopted or treated as an empty test slot', () => {
  const installation = { id: 'b'.repeat(32), project: 'source-project' };
  const Labels = { 'lexiflow.slot': 'source', 'lexiflow.installation': installation.id,
    'io.podman.compose.project': installation.project, 'io.podman.compose.service': 'api' };
  const owned = { Names: ['lexiflow-test'], State: 'exited', Labels };
  const unrelated = { Names: ['lexiflow-test-other'], State: 'running' };
  assert.doesNotThrow(() => assertSourceContainerSlot([unrelated], null));
  assert.doesNotThrow(() => assertSourceContainerSlot([owned, unrelated], installation));
  for (const row of [
    { ...owned, Labels: {}, State: 'running' },
    { ...owned, Labels: { 'io.lexiflow.local-slot': 'lexiflow-test' } },
    { ...owned, Labels: { ...Labels, 'com.docker.compose.project': 'other-project' } },
    { ...owned, Labels: { ...Labels, 'io.podman.compose.service': 'other-service' } },
  ]) assert.throws(() => assertSourceContainerSlot([row], installation), /DEPLOYMENT_TEST_SLOT_OCCUPIED/);
  assert.throws(() => assertSourceContainerSlot([owned], null), /DEPLOYMENT_TEST_SLOT_OCCUPIED/);
  assert.throws(() => assertSourceContainerSlot([owned, owned], installation), /DEPLOYMENT_TEST_SLOT_OCCUPIED/);
  assert.throws(() => assertSourceContainerSlot([{}], installation), /DEPLOYMENT_TEST_SLOT_INVENTORY_INVALID/);
});
