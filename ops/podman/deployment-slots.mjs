// 固定本机验证部署槽位。槽位不含 run ID；一次运行的证据目录与持久安装目录分离。
import path from 'node:path';
import fs from 'node:fs';
import { acquireProcessLock } from './execution/installation-operation-lock.mjs';

const slots = Object.freeze({
  source: Object.freeze({ apiPort: 18081, dbPort: 15432, directory: '.local/podman', imageNamespace: 'lexiflow' }),
  releaseValidation: Object.freeze({ apiPort: 18080, dbPort: 15433,
    directory: '.local/podman-release-validation', imageNamespace: 'lexiflow-release-validation' }),
});

export function deploymentSlot(kind, repo) {
  const slot = kind === 'source' ? slots.source : kind === 'release-validation' ? slots.releaseValidation : null;
  if (!slot) throw new Error('DEPLOYMENT_SLOT_INVALID');
  return Object.freeze({ ...slot, directory: path.resolve(repo, slot.directory) });
}

export function slotImageNamespace(kind) {
  const slot = kind === 'source' ? slots.source : kind === 'release-validation' ? slots.releaseValidation : null;
  if (!slot) throw new Error('DEPLOYMENT_SLOT_INVALID');
  return slot.imageNamespace;
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
