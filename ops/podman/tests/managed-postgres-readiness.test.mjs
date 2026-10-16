import test from 'node:test';
import assert from 'node:assert/strict';
import { waitForManagedPostgresReady } from '../environment/managed-postgres-readiness.mjs';

const expected = { containerId: 'a'.repeat(64), imageId: 'b'.repeat(64), installationId: 'c'.repeat(32),
  project: 'lexiflow-local-' + 'c'.repeat(32), port: 15433, volumeName: 'lexiflow-local-' + 'c'.repeat(32) + '_pgdata' };
const facts = (changes = {}) => ({ containerId: expected.containerId, imageId: expected.imageId,
  installationId: expected.installationId, project: expected.project, service: 'postgres', port: expected.port,
  volumeName: expected.volumeName, volumeDestination: '/var/lib/postgresql/data',
  volumeReadWrite: true,
  volumeInstallationId: expected.installationId, volumeProject: expected.project,
  running: true, health: 'healthy', ...changes });

test('managed PG readiness accepts healthy immediately and waits only through starting', async () => {
  let calls = 0, sleeps = 0;
  await waitForManagedPostgresReady({ expected, inspect: async () => {
    calls++;
    return facts({ health: calls === 1 ? 'starting' : 'healthy' });
  }, sleep: async () => { sleeps++; } });
  assert.equal(calls, 2);
  assert.equal(sleeps, 1);
  calls = 0;
  await waitForManagedPostgresReady({ expected, inspect: async () => { calls++; return facts(); } });
  assert.equal(calls, 1);
});

test('managed PG readiness rejects changed identity, unknown health, stopped and unhealthy states', async () => {
  for (const [change, code] of [
    [{ containerId: 'd'.repeat(64) }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ imageId: 'e'.repeat(64) }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ installationId: 'd'.repeat(32) }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ project: 'foreign' }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ service: 'api' }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ port: 15432 }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ volumeName: 'foreign_pgdata' }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ volumeDestination: '/other' }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ volumeReadWrite: false }, 'MANAGED_POSTGRES_IDENTITY_CHANGED'],
    [{ running: false }, 'MANAGED_POSTGRES_STOPPED'],
    [{ health: undefined }, 'MANAGED_POSTGRES_HEALTH_UNKNOWN'],
    [{ health: 'unknown' }, 'MANAGED_POSTGRES_HEALTH_UNKNOWN'],
    [{ health: 'unhealthy' }, 'MANAGED_POSTGRES_UNHEALTHY'],
  ]) await assert.rejects(waitForManagedPostgresReady({ expected, inspect: async () => facts(change) }), { message: code });
});

test('managed PG readiness is bounded and cancellation is explicit', async () => {
  let t = 10, checks = 0;
  await assert.rejects(waitForManagedPostgresReady({ expected, timeoutMs: 3, intervalMs: 2,
    now: () => t, sleep: async ms => { t += ms; }, inspect: async () => { checks++; return facts({ health: 'starting' }); } }),
  { message: 'MANAGED_POSTGRES_READINESS_TIMEOUT' });
  assert.equal(checks, 3);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(waitForManagedPostgresReady({ expected, signal: controller.signal,
    inspect: async () => facts() }), { message: 'MANAGED_POSTGRES_WAIT_CANCELLED' });
});

test('missing or malformed expected managed identity is rejected before inspection', async () => {
  let called = false;
  await assert.rejects(waitForManagedPostgresReady({ expected: { ...expected, containerId: 'short' },
    inspect: async () => { called = true; return facts(); } }), { message: 'MANAGED_POSTGRES_IDENTITY_INVALID' });
  assert.equal(called, false);
});
