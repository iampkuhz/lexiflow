import { openSync, constants, readSync, fstatSync, closeSync } from 'node:fs';

export const LOCK_PATH = 'contracts/versions.lock.json';
export const SCOPES = Object.freeze(['extension-api', 'sqlite-storage']);
export const MAX_LOCK_BYTES = 2048;
const digest = /^[a-f0-9]{64}$/u;
export function encodeLock(value) {
  return JSON.stringify(Object.fromEntries(SCOPES.map(s => [s, { version: value[s].version, sha256: value[s].sha256 }])), null, 2) + '\n';
}
export function parseLock(text) {
  if (Buffer.byteLength(text) > MAX_LOCK_BYTES) throw new Error('CONTRACT_LOCK_TOO_LARGE');
  const value = JSON.parse(text);
  if (!value || Object.keys(value).sort().join(',') !== SCOPES.join(',') ||
      SCOPES.some(s => !value[s] || Object.keys(value[s]).sort().join(',') !== 'sha256,version' ||
        !Number.isSafeInteger(value[s].version) || value[s].version < 1 || value[s].version > 999999999 ||
        typeof value[s].sha256 !== 'string' || !digest.test(value[s].sha256)) ||
      encodeLock(value) !== text) throw new Error('CONTRACT_LOCK_INVALID');
  return value;
}
export function readLock(path = new URL('../../contracts/versions.lock.json', import.meta.url)) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size > MAX_LOCK_BYTES) throw new Error('CONTRACT_LOCK_INVALID');
    const bytes = Buffer.alloc(MAX_LOCK_BYTES + 1);
    const count = readSync(fd, bytes);
    return parseLock(bytes.subarray(0,count).toString('utf8'));
  } finally { closeSync(fd); }
}
export const apiContract = (lock = readLock()) => 'caption-hints.v' + lock['extension-api'].version;
