#!/usr/bin/env node
import { apiContract } from "../../../scripts/contracts/contract-lock.mjs";
import { spawnSync } from 'node:child_process';
import { realpathSync, statSync } from 'node:fs';
import { rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { readStableRegularFile } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';
import net from 'node:net';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { acquireDeploymentSlotLock, deploymentSlot } from '../deployment-slots.mjs';
import { LOCAL_TEST_PORT } from '../loopback-ports.mjs';
import { deployLocalTest, testVolumes } from '../installation/local-test-slot.mjs';

import { buildLocalTest, loadPlan, names, readCandidate, slotPaths, writePlan } from '../installation/local-test-artifacts.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const digest = /^[a-f0-9]{64}$/u;
const machine = 'podman-machine-default';
let socket;
const loopback = `127.0.0.1:${LOCAL_TEST_PORT}`;
const fail = code => { throw new Error(code); };

function runPodman(args) {
  // Do not inherit a remote endpoint or compose override.
  const env = Object.fromEntries(['HOME', 'PATH', 'TMPDIR'].filter(key => process.env[key]).map(key => [key, process.env[key]]));
  const output = spawnSync('podman', args, {
    env: { ...env, LANG: 'C', LC_ALL: 'C' }, encoding: 'utf8', timeout: 60_000,
    maxBuffer: 8 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe']
  });
  if (output.error || output.signal || output.status !== 0) fail(`LOCAL_TEST_PODMAN_${args[0] === '--url' ? args[2].toUpperCase() : 'MACHINE'}_FAILED`);
  return output.stdout.trim();
}
function localEngine() {
  const [info] = JSON.parse(runPodman(['machine', 'inspect', machine]));
  const socketPath = info?.ConnectionInfo?.PodmanSocket?.Path;
  if (info?.Name !== machine || info.State !== 'running' || info.Rootful !== true ||
      !path.isAbsolute(socketPath ?? '')) fail('LOCAL_TEST_MACHINE_UNAVAILABLE');
  const real = realpathSync(socketPath);
  const stat = statSync(real);
  if (!stat.isSocket()) fail('LOCAL_TEST_ENDPOINT_INVALID');
  return { real, dev: stat.dev, ino: stat.ino };
}
function command(args) {
  const stat = statSync(socket.real);
  if (!stat.isSocket() || stat.dev !== socket.dev || stat.ino !== socket.ino) fail('LOCAL_TEST_ENDPOINT_CHANGED');
  return runPodman(['--url', `unix://${socket.real}`, ...args]);
}
function json(...args) {
  try {
    const rows = JSON.parse(command([...args, '--format', 'json']));
    if (!Array.isArray(rows)) fail('LOCAL_TEST_INVENTORY_INVALID');
    return rows;
  } catch (error) { if (error instanceof SyntaxError) fail('LOCAL_TEST_INVENTORY_INVALID'); throw error; }
}
function inspect(dataset, preferenceVolume) {
  return {
    containers: json('ps', '--all', '--no-trunc'),
    images: json('images', '--no-trunc'),
    volumes: json('volume', 'ls'),
    pods: json('pod', 'ps', '--no-trunc'),
    volumeUsers: testVolumes(dataset, preferenceVolume).flatMap(volume => json('ps', '--all', '--no-trunc', '--filter', `volume=${volume}`)),
  };
}

function selectedPreferences() {
  const rows = json('ps', '--all', '--no-trunc').filter(row => row.Labels?.['io.lexiflow.local-slot'] === 'lexiflow-test');
  if (rows.length > 1) fail('LOCAL_TEST_CONTAINER_OWNERSHIP_UNPROVEN');
  if (rows.length === 1) {
    const [record] = JSON.parse(command(['container', 'inspect', rows[0].Id]));
    const mounts = record?.Mounts?.filter(m => m.Destination === '/var/lib/lexiflow/preferences');
    if (mounts?.length !== 1 || mounts[0].Type !== 'volume' || !/^lexiflow-test-preferences(?:-[a-f0-9]{64})?$/u.test(mounts[0].Name ?? ''))
      fail('LOCAL_TEST_PREFERENCES_OWNERSHIP_UNPROVEN');
    return mounts[0].Name;
  }
  const retained = json('volume', 'ls').filter(row => row.Labels?.['io.lexiflow.local-slot'] === 'lexiflow-test' &&
    /^lexiflow-test-preferences(?:-[a-f0-9]{64})?$/u.test(row.Name));
  if (retained.length > 1) fail('LOCAL_TEST_PREFERENCES_AMBIGUOUS');
  return retained[0]?.Name ?? null;
}
function selectVerifiedCopy(slot, record) {
  const target = 'lexiflow-test-preferences';
  const source = selectedPreferences();
  const proof = JSON.parse(readStableRegularFile(path.join(slot, 'verified-preferences.json'), 2048).toString('utf8'));
  const [volume] = JSON.parse(command(['volume', 'inspect', target]));
  if (!source || source === target || proof.source !== source || proof.target !== target ||
      proof.datasetSha256 !== record.dataset.sha256 || proof.volumeCreatedAt !== volume?.CreatedAt ||
      volume?.Labels?.['io.lexiflow.local-slot'] !== 'lexiflow-test' ||
      volume.Labels['io.lexiflow.preferences-copy-of'] !== source)
    fail('LOCAL_TEST_PREFERENCES_COPY_UNPROVEN');
  return target;
}
function verifyVolumes(plan) {
  for (const [index, name] of plan.volumeNames.entries()) {
    if (plan.createVolumes.includes(name)) continue;
    const role = index === 0 ? 'lexicon' : 'preferences';
    const destination = index === 0 ? '/var/lib/lexiflow' : '/var/lib/lexiflow/preferences';
    const verifier = 'lexiflow-volume-check-' + randomUUID();
    const owner = 'io.lexiflow.storage-check=' + verifier;
    // A bounded read-only checker, not an API container. No port, network or writable product volume.
    try {
      const output = command(['run', '--rm', '--name', verifier, '--label', owner, '--pull=never', '--network=none',
        '--image-volume=ignore', '--read-only', '--tmpfs', '/tmp:rw,size=33554432',
        '--volume', name + ':' + destination + ':ro,nocopy',
        plan.nextId, 'check-volume', role]);
      if (output !== 'PASS') fail('LOCAL_TEST_' + role.toUpperCase() + '_VOLUME_INCOMPATIBLE');
    } catch {
      fail('LOCAL_TEST_' + role.toUpperCase() + '_VOLUME_INCOMPATIBLE');
    } finally {
      const filter = ['ps', '--all', '--no-trunc', '--filter', 'name=^' + verifier + '$'];
      const remaining = json(...filter);
      if (remaining.length === 1 && digest.test(remaining[0].Id) &&
          remaining[0].Labels?.['io.lexiflow.storage-check'] === verifier) {
        if (remaining[0].State === 'running') command(['stop', '--time', '5', remaining[0].Id]);
        if (json(...filter).some(row => row.Id === remaining[0].Id))
          command(['rm', remaining[0].Id]);
      }
      if (json(...filter).length) fail('LOCAL_TEST_VOLUME_CHECK_CLEANUP_UNCONFIRMED');
    }
  }
}
function assertPortFree() {
  return new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.once('error', () => reject(new Error('LOCAL_TEST_PORT_IN_USE')));
    socket.listen(LOCAL_TEST_PORT, '127.0.0.1', () => socket.close(resolve));
  });
}
function assertHostPublisher(id) {
  if (command(['port', id, '8080/tcp']) !== loopback) fail('LOCAL_TEST_PORT_OWNERSHIP_UNPROVEN');
}
async function ready(id, candidate) {
  for (let i = 0; i < 60; i++) {
    assertHostPublisher(id);
    const res = await fetch(`http://${loopback}/api/v1/runtime-status`, {
      signal: AbortSignal.timeout(2000), redirect: 'error'
    }).catch(() => null);
    const status = res?.ok ? await res.json().catch(() => null) : null;
    if (status && (status.softwareVersion !== candidate.identity.softwareVersion || status.apiContract !== apiContract()))
      fail('LOCAL_TEST_RUNTIME_IDENTITY_MISMATCH');
    if (status?.ready === true) {
      if (status.mode !== 'formal' || !['OK', 'PREWARM_DEGRADED'].includes(status.reason) ||
          Object.keys(status.publishedDataset ?? {}).length !== Object.keys(candidate.dataset).length ||
          !Object.entries(candidate.dataset).every(([key, value]) => status.publishedDataset?.[key] === value))
        fail('LOCAL_TEST_DATASET_MISMATCH');
      assertHostPublisher(id);
      return;
    }
    await delay(1000);
  }
  fail('LOCAL_TEST_RUNTIME_UNAVAILABLE');
}

function loadPlannedImage(candidate) {
  const tags = json('images', '--no-trunc').filter(row => row.Names?.includes(candidate.tag));
  if (tags.length > 1 || tags.some(row => row.Id.replace(/^sha256:/u, '') !== candidate.imageId.slice(7)))
    fail('LOCAL_TEST_IMAGE_TAG_IN_USE');
  if (!tags.length) command(['load', '-i', candidate.imageArchive]);
  const [image] = JSON.parse(command(['image', 'inspect', candidate.tag]));
  if (image?.Id?.replace(/^sha256:/u, '') !== candidate.imageId.slice(7)) fail('LOCAL_TEST_IMAGE_LOAD_MISMATCH');
}

let writesStarted = false;
try {
  const [action, ...extra] = process.argv.slice(2);
  const switchPreferences = action === 'plan' && extra.length === 1 && extra[0] === '--use-verified-preferences';
  if (!['build', 'plan', 'start'].includes(action) || (extra.length && !switchPreferences))
    fail('用法: node ops/podman/commands/manage-local-test-slot.mjs build|plan|start');
  if (process.platform !== 'darwin' || process.arch !== 'arm64') fail('LOCAL_TEST_HOST_UNSUPPORTED');
  const unlock = acquireDeploymentSlotLock('local-test');
  try {
    const paths = await slotPaths(deploymentSlot('local-test', root).directory, action === 'build');
    if (action === 'build') console.log(JSON.stringify(await buildLocalTest(root, paths), null, 2));
    else {
      const record = await readCandidate(root, paths.current);
      if (action === 'plan') await rm(path.join(paths.current, names.plan), { force: true });
      const saved = action === 'start' ? loadPlan(paths.current, record) : undefined;
      const selectedVerified = switchPreferences || saved?.preferencesMode === 'verified-copy';
      const confirmation = saved?.planSha256;
      socket = localEngine();
      if (action === 'plan') loadPlannedImage(record);
      const [imageInfo] = JSON.parse(command(['image', 'inspect', record.imageId]));
      if (imageInfo?.Os !== 'linux' || imageInfo.Architecture !== 'arm64') fail('LOCAL_TEST_IMAGE_PLATFORM_MISMATCH');
      const preferenceVolume = selectedVerified ? selectVerifiedCopy(paths.slot, record) : selectedPreferences();
      const candidate = { ...record, engine: { dev: socket.dev, ino: socket.ino }, preferenceVolume };
      const plan = await deployLocalTest({
        inspect: () => inspect(record.dataset, candidate.preferenceVolume),
        command: args => { writesStarted = true; return command(args); },
        assertPortFree, assertHostPublisher, ready, verifyVolumes,
      }, candidate, confirmation);
      const result = { status: 'PASS', action, ...plan, ...(selectedVerified ? { preferencesMode: 'verified-copy' } : {}) };
      if (action === 'plan') await writePlan(paths.current, result);
      else await rm(path.join(paths.current, names.plan), { force: true });
      console.log(JSON.stringify(result, null, 2));
    }
  } finally { unlock(); }
} catch (error) {
  console.error(JSON.stringify({ status: writesStarted ? 'FAIL' : 'BLOCKED', reason: error.message }));
  process.exitCode = 1;
}
