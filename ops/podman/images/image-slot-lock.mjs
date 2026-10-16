// Host-user scoped, non-waiting lock for a shared versioned image slot.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { acquireProcessLock } from '../execution/installation-operation-lock.mjs';
import { deploymentImageTag } from './versioned-images.mjs';

const fail = code => { throw new Error(code); };
function privateDirectory(directory) {
  try { fs.mkdirSync(directory, { mode: 0o700 }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o077)) fail('IMAGE_SLOT_LOCK_DIRECTORY_INVALID');
}

/** Acquire the same-version host-user image slot immediately; callers must release in finally. */
export function acquireImageSlotLock(baseVersion, kind = 'source') {
  if (!['source', 'release-validation'].includes(kind)) fail('IMAGE_SLOT_KIND_INVALID');
  let tag;
  try { tag = deploymentImageTag(baseVersion); } catch { fail('IMAGE_SLOT_VERSION_INVALID'); }
  const root = path.join(os.tmpdir(), `lexiflow-image-slots-${process.getuid()}`);
  privateDirectory(root);
  const namespace = path.join(root, kind);
  privateDirectory(namespace);
  const slot = path.join(namespace, tag);
  privateDirectory(slot);
  try { return acquireProcessLock(slot); }
  catch (error) {
    if (error.message === 'INSTALL_LOCK_BUSY') fail('IMAGE_SLOT_BUSY');
    fail('IMAGE_SLOT_LOCK_UNAVAILABLE');
  }
}
