const fullId = /^[a-f0-9]{64}$/;

/** Wait only for the exact managed PostgreSQL instance that was just started. */
export async function waitForManagedPostgresReady({ inspect, expected, signal, timeoutMs = 120_000,
  intervalMs = 500, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), now = () => Date.now() }) {
  if (typeof inspect !== 'function' || !expected || !fullId.test(expected.containerId ?? '')
      || !fullId.test(String(expected.imageId ?? '').replace(/^sha256:/, ''))
      || !/^[a-f0-9]{32}$/.test(expected.installationId ?? '')
      || typeof expected.project !== 'string' || !expected.project
      || !Number.isInteger(expected.port) || ![15432, 15433].includes(expected.port)
      || typeof expected.volumeName !== 'string' || !expected.volumeName.endsWith('_pgdata'))
    throw new Error('MANAGED_POSTGRES_IDENTITY_INVALID');
  const deadline = now() + timeoutMs;
  while (true) {
    if (signal?.aborted) throw new Error('MANAGED_POSTGRES_WAIT_CANCELLED');
    const value = await inspect(expected.containerId);
    if (!value || value.containerId !== expected.containerId
        || value.imageId !== String(expected.imageId).replace(/^sha256:/, '')
        || value.installationId !== expected.installationId || value.project !== expected.project
        || value.service !== 'postgres' || value.port !== expected.port
        || value.volumeName !== expected.volumeName || value.volumeDestination !== '/var/lib/postgresql/data'
        || value.volumeReadWrite !== true
        || value.volumeInstallationId !== expected.installationId || value.volumeProject !== expected.project)
      throw new Error('MANAGED_POSTGRES_IDENTITY_CHANGED');
    if (value.running !== true) throw new Error('MANAGED_POSTGRES_STOPPED');
    if (!['starting', 'healthy', 'unhealthy'].includes(value.health)) throw new Error('MANAGED_POSTGRES_HEALTH_UNKNOWN');
    if (value.health === 'healthy') return;
    if (value.health === 'unhealthy') throw new Error('MANAGED_POSTGRES_UNHEALTHY');
    const remaining = deadline - now();
    if (remaining <= 0) throw new Error('MANAGED_POSTGRES_READINESS_TIMEOUT');
    await sleep(Math.min(intervalMs, remaining));
  }
}
