import { createHash } from 'node:crypto';
import { LOCAL_TEST_PORT } from '../loopback-ports.mjs';

export const testName = 'lexiflow-test';
export const slotLabel = 'io.lexiflow.local-slot';
export const portBinding = `127.0.0.1:${LOCAL_TEST_PORT}:8080`;
const idPattern = /^[a-f0-9]{64}$/u;
const fail = code => { throw new Error(code); };
const id = raw => {
  const value = typeof raw === 'string' ? raw.replace(/^sha256:/u, '') : '';
  if (!idPattern.test(value)) fail('LOCAL_TEST_RESOURCE_ID_INVALID');
  return value;
};
const testOwned = row => row.Labels?.[slotLabel] === testName;
const legacyResource = row => row.Names?.some(name => /^(?:localhost\/)?lexiflow-v[0-9]+-test-/u.test(name));
const bindsSlot = row => (row.Ports ?? []).some(port => port.host_port <= LOCAL_TEST_PORT &&
  port.host_port + Math.max(port.range ?? 1, 1) > LOCAL_TEST_PORT);
const soleBinding = row => row.Ports?.length === 1 && row.Ports.every(p =>
  p.host_ip === '127.0.0.1' && p.host_port === LOCAL_TEST_PORT && p.container_port === 8080
  && p.protocol === 'tcp' && (p.range ?? 1) === 1);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function testVolumes(dataset, preferenceVolume = null) {
  const key = hash([dataset.releaseId, dataset.preparationId, dataset.ruleId, dataset.sqlVersion, dataset.sha256]);
  return [`${testName}-data-${key}`, preferenceVolume ?? `${testName}-preferences`];
}

function requireTestImage(row) {
  if (!row || !testOwned(row) || row.Labels?.['io.lexiflow.image-role'] !== 'runtime' ||
      !Array.isArray(row.Names) || !row.Names.length ||
      row.Names.some(name => !/^localhost\/lexiflow-test:[a-zA-Z0-9][a-zA-Z0-9._-]*$/u.test(name)))
    fail('LOCAL_TEST_IMAGE_OWNERSHIP_UNPROVEN');
  return id(row.Id);
}

function requireIdentity(labels, build, dataset) {
  const expected = {
    'io.lexiflow.build-id': build.buildId, 'io.lexiflow.source-sha256': build.sourceSha256,
    'org.opencontainers.image.revision': build.sourceCommit, 'org.opencontainers.image.version': build.softwareVersion,
    'io.lexiflow.dataset-sha256': dataset.sha256
  };
  if (Object.entries(expected).some(([key, value]) => labels[key] !== value)) fail('LOCAL_TEST_ARTIFACT_MISMATCH');
}

/** Selection and authorization are pure; tests exercise the same planner as the command. */
export function planLocalTest({ containers, images, volumes, pods, volumeUsers }, candidate) {
  if (![containers, images, volumes, pods, volumeUsers].every(Array.isArray)) fail('LOCAL_TEST_INVENTORY_INVALID');
  if (pods.some(pod => pod.Name === testName || testOwned(pod))) fail('LOCAL_TEST_POD_CONFLICT');
  const nextId = id(candidate.imageId);
  const nextImage = images.find(row => row.Id === nextId || row.Id === 'sha256:' + nextId);
  requireTestImage(nextImage);
  requireIdentity(nextImage.Labels, candidate.identity, candidate.dataset);
  if (containers.some(legacyResource) || images.some(legacyResource)) fail('LOCAL_TEST_LEGACY_RESOURCES_REQUIRE_REVIEW');
  const old = containers.filter(testOwned);
  if (old.length > 1 || old.some(row => row.Pod !== '' || !soleBinding(row) ||
      row.Names?.length !== 1 || row.Names[0] !== testName)) fail('LOCAL_TEST_CONTAINER_OWNERSHIP_UNPROVEN');
  if (containers.some(row => !testOwned(row) && (row.Names?.includes(testName) || bindsSlot(row))))
    fail('LOCAL_TEST_PORT_OR_NAME_IN_USE');
  const volumeNames = testVolumes(candidate.dataset, candidate.preferenceVolume);
  if (candidate.preferenceVolume && (!/^lexiflow-test-preferences(?:-[a-f0-9]{64})?$/u.test(candidate.preferenceVolume) ||
      !volumes.some(row => row.Name === candidate.preferenceVolume && testOwned(row))))
    fail('LOCAL_TEST_PREFERENCES_OWNERSHIP_UNPROVEN');
  if (volumes.some(row => volumeNames.includes(row.Name) && !testOwned(row)))
    fail('LOCAL_TEST_VOLUME_OWNERSHIP_UNPROVEN');
  if (volumeUsers.some(row => !testOwned(row) || row.Id !== old[0]?.Id)) fail('LOCAL_TEST_VOLUME_IN_USE');
  const oldImages = images.filter(testOwned).map(requireTestImage).filter(image => image !== nextId).sort();
  const foreignImage = containers.some(row => !testOwned(row) &&
    oldImages.includes(String(row.ImageID).replace(/^sha256:/u, '')));
  if (foreignImage) fail('LOCAL_TEST_IMAGE_IN_USE');
  const previous = old[0] ? { id: id(old[0].Id), imageId: id(old[0].ImageID), state: old[0].State } : null;
  if (previous && !oldImages.includes(previous.imageId) && previous.imageId !== nextId)
    fail('LOCAL_TEST_CONTAINER_IMAGE_UNPROVEN');
  if (previous && !['running', 'paused', 'exited', 'stopped', 'created', 'configured'].includes(previous.state))
    fail('LOCAL_TEST_CONTAINER_STATE_INVALID');
  const body = {
    port: LOCAL_TEST_PORT, container: testName, engine: candidate.engine ?? null, previous, oldImages, volumeNames,
    createVolumes: volumeNames.filter(name => !volumes.some(row => row.Name === name)),
    nextId, extensionSha256: candidate.extensionSha256, softwareVersion: candidate.identity.softwareVersion,
  };
  return { ...body, planSha256: hash(body) };
}

export async function deployLocalTest({ inspect, command, assertPortFree, assertHostPublisher, ready, verifyVolumes }, candidate, confirmation) {
  const plan = planLocalTest(await inspect(), candidate);
  if (confirmation && plan.planSha256 !== confirmation) fail('LOCAL_TEST_PLAN_CHANGED');

  // Before destructive steps, check the observed listener belongs to the exact approved container.
  if (['running', 'paused'].includes(plan.previous?.state)) await assertHostPublisher(plan.previous.id);
  else await assertPortFree();
  if (verifyVolumes) await verifyVolumes(plan);
  if (!confirmation) return plan;
  if (plan.previous) {
    if (['running', 'paused'].includes(plan.previous.state)) {
      await command(['stop', '--time', '45', plan.previous.id]);
      const stopped = planLocalTest(await inspect(), candidate);
      if (stopped.previous?.id !== plan.previous.id || stopped.previous.imageId !== plan.previous.imageId ||
          !['exited', 'stopped'].includes(stopped.previous.state) ||
          JSON.stringify(stopped.oldImages) !== JSON.stringify(plan.oldImages) ||
          JSON.stringify(stopped.createVolumes) !== JSON.stringify(plan.createVolumes))
        fail('LOCAL_TEST_STOP_UNOBSERVED');
    }
    if (verifyVolumes) await verifyVolumes(plan);
    await command(['rm', plan.previous.id]);
  }
  const afterContainer = planLocalTest(await inspect(), candidate);
  if (afterContainer.previous || JSON.stringify(afterContainer.oldImages) !== JSON.stringify(plan.oldImages) ||
      JSON.stringify(afterContainer.createVolumes) !== JSON.stringify(plan.createVolumes))
    fail('LOCAL_TEST_CLEANUP_INVENTORY_CHANGED');
  // Refuse orphan or shared images; deliberately do not use --force, prune, or volume deletion.
  for (const image of plan.oldImages) await command(['rmi', '--no-prune', image]);
  const afterImages = planLocalTest(await inspect(), candidate);
  if (afterImages.previous || afterImages.oldImages.length ||
      JSON.stringify(afterImages.createVolumes) !== JSON.stringify(plan.createVolumes))
    fail('LOCAL_TEST_CLEANUP_UNOBSERVED');
  await assertPortFree();
  for (const volume of plan.createVolumes) {
    await command(['volume', 'create', '--label', `${slotLabel}=${testName}`, volume]);
  }
  const beforeRun = planLocalTest(await inspect(), candidate);
  if (beforeRun.previous || beforeRun.oldImages.length || beforeRun.createVolumes.length) fail('LOCAL_TEST_SLOT_CHANGED');
  await command(['run', '--pull=never', '--image-volume=ignore', '--stop-timeout=45', '--memory=2g', '--cpus=2',
    '--detach', '--name', testName, '--label', `${slotLabel}=${testName}`, '--publish', portBinding,
    '--volume', `${plan.volumeNames[0]}:/var/lib/lexiflow`, '--volume', `${plan.volumeNames[1]}:/var/lib/lexiflow/preferences`, plan.nextId]);
  const running = planLocalTest(await inspect(), candidate);
  if (running.previous?.state !== 'running' || running.previous.imageId !== plan.nextId ||
      running.oldImages.length || running.createVolumes.length) fail('LOCAL_TEST_START_UNOBSERVED');
  await ready(running.previous.id, candidate);
  return plan;
}
