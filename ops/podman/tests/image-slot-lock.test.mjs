import test from 'node:test';
import assert from 'node:assert/strict';
import { acquireImageSlotLock } from '../image-slot-lock.mjs';

test('image slot lock is non-waiting, shared per host user and isolated by base version', () => {
  const release = acquireImageSlotLock('7.0.0-SNAPSHOT');
  try {
    assert.throws(() => acquireImageSlotLock('7.0.0-SNAPSHOT'), /IMAGE_SLOT_BUSY/);
    const other = acquireImageSlotLock('7.0.1-SNAPSHOT');
    other();
  } finally { release(); }
  const next = acquireImageSlotLock('7.0.0-SNAPSHOT');
  next();
  assert.throws(() => acquireImageSlotLock('7.0.0-snapshot'), /IMAGE_SLOT_VERSION_INVALID/);
});
