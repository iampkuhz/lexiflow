// 固定本机验证部署槽位。槽位不含 run ID；一次运行的证据目录与持久安装目录分离。
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { DEVELOPMENT_PORT, LOCAL_TEST_PORT, RELEASE_PORT } from './loopback-ports.mjs';
import { acquireProcessLock } from './execution/installation-operation-lock.mjs';

const slots = Object.freeze({
  source: Object.freeze({ apiPort: DEVELOPMENT_PORT, directory: '.local/podman', imageNamespace: 'lexiflow', containerName: 'lexiflow-test' }),
  localTest: Object.freeze({ apiPort: LOCAL_TEST_PORT, directory: 'Library/Application Support/LexiFlow/Testing', imageNamespace: 'lexiflow-test' }),
  releaseValidation: Object.freeze({ apiPort: RELEASE_PORT,
    directory: '.local/podman-release-validation', imageNamespace: 'lexiflow-release-validation' }),
});

export function deploymentSlot(kind, repo) {
  const slot = kind === 'source' ? slots.source : kind === 'release-validation' ? slots.releaseValidation : kind === 'local-test' ? slots.localTest : null;
  if (!slot) throw new Error('DEPLOYMENT_SLOT_INVALID');
  return Object.freeze({ ...slot, directory: path.resolve(kind === 'local-test' ? os.userInfo().homedir : repo, slot.directory) });
}

export function slotImageNamespace(kind) {
  const slot = kind === 'source' ? slots.source : kind === 'release-validation' ? slots.releaseValidation : kind === 'local-test' ? slots.localTest : null;
  if (!slot) throw new Error('DEPLOYMENT_SLOT_INVALID');
  return slot.imageNamespace;
}

// podman ps -a includes stopped containers; a free port alone does not mean the slot is free.
export function assertSourceContainerSlot(rows, installation) {
  if (!Array.isArray(rows) || rows.some(row => !Array.isArray(row?.Names))) {
    throw new Error('DEPLOYMENT_TEST_SLOT_INVENTORY_INVALID');
  }
  const occupants = rows.filter(row => row.Names.includes(slots.source.containerName));
  if (!occupants.length) return;
  const labels = occupants[0].Labels || {};
  const matches = (key, expected) => {
    const values = [labels[`com.docker.compose.${key}`], labels[`io.podman.compose.${key}`]]
      .filter(value => value !== undefined);
    return values.length > 0 && values.every(value => value === expected);
  };
  if (occupants.length !== 1 || !installation || labels['lexiflow.slot'] !== 'source'
      || labels['lexiflow.installation'] !== installation.id
      || !matches('project', installation.project) || !matches('service', 'api')) {
    throw new Error('DEPLOYMENT_TEST_SLOT_OCCUPIED');
  }
}

/** Host-user lock is independent of checkout, evidence run and mutable TMPDIR. */
export function acquireDeploymentSlotLock(kind) {
  slotImageNamespace(kind);
  const directory = path.join('/tmp', `lexiflow-${kind}-slot-${process.getuid()}`);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o077))
    throw new Error('DEPLOYMENT_SLOT_LOCK_INVALID');
  try { return acquireProcessLock(directory); }
  catch (error) { throw new Error(error.message === 'INSTALL_LOCK_BUSY' ? 'DEPLOYMENT_SLOT_BUSY' : 'DEPLOYMENT_SLOT_LOCK_INVALID'); }
}
