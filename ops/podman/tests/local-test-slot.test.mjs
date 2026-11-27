import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { loadPlan, names, publishBuild, slotPaths, writePlan } from '../installation/local-test-artifacts.mjs';
import { deployLocalTest, planLocalTest, portBinding, slotLabel, testName, testVolumes } from '../installation/local-test-slot.mjs';
import { LOCAL_TEST_PORT } from '../loopback-ports.mjs';

const oldId = 'a'.repeat(64), nextId = 'b'.repeat(64), oldContainer = 'c'.repeat(64), nextContainer = 'd'.repeat(64);
const own = { [slotLabel]: testName };
const dataset = { releaseId: 'fixture', preparationId: 'fixture-prep', ruleId: 'fixture-rule', sqlVersion: 'fixture-sql', sha256: '0'.repeat(64) };
const candidate = { imageId: `sha256:${nextId}`, extensionSha256: 'f'.repeat(64), dataset,
  identity: { softwareVersion: '3.0.0', buildId: 'e'.repeat(64), sourceSha256: '1'.repeat(64), sourceCommit: '2'.repeat(40) } };
const targetLabels = { ...own, 'io.lexiflow.image-role': 'runtime', 'io.lexiflow.build-id': candidate.identity.buildId,
  'io.lexiflow.source-sha256': candidate.identity.sourceSha256, 'org.opencontainers.image.revision': candidate.identity.sourceCommit,
  'org.opencontainers.image.version': candidate.identity.softwareVersion, 'io.lexiflow.dataset-sha256': dataset.sha256 };
const binding = { host_ip: '127.0.0.1', host_port: LOCAL_TEST_PORT, container_port: 8080, protocol: 'tcp', range: 1 };
const vc = id => ({ Id: id, Names: [testName], State: 'running', Pod: '', Ports: [binding], Labels: own,
  ImageID: id === oldContainer ? oldId : nextId });
function inventory() {
  return {
    containers: [vc(oldContainer), { Names: ['unrelated-gateway'], Ports: [{ ...binding, host_port: 18080 }] }],
    images: [{ Id: oldId, Labels: { ...own, 'io.lexiflow.image-role': 'runtime' }, Names: ['localhost/lexiflow-test:old'] },
      { Id: nextId, Labels: { ...targetLabels }, Names: ['localhost/lexiflow-test:next'] }],
    volumes: testVolumes(dataset).map(Name => ({ Name, Labels: own })), pods: [], volumeUsers: [],
  };
}
function simulation(edit = () => {}, hooks = {}) {
  const state = inventory(), actions = [];
  edit(state);
  const runtime = {
    inspect: () => structuredClone(state),
    assertPortFree: () => { if (hooks.portBusy) throw Error('LOCAL_TEST_PORT_IN_USE'); actions.push(['port-free']); },
    assertHostPublisher: id => { assert.equal(id, oldContainer); actions.push(['publisher', id]); },
    ready: (id, value) => { assert.equal(id, nextContainer); assert.equal(value, candidate); actions.push(['ready']); },
    command: args => {
      actions.push(args);
      switch (args[0]) {
        case 'stop': if (!hooks.stopUnobserved) state.containers[0].State = 'exited'; break;
        case 'rm': state.containers.shift(); break;
        case 'rmi':
          if (hooks.imageFailure) throw Error('LOCAL_TEST_PODMAN_RMI_FAILED');
          state.images = state.images.filter(x => x.Id !== args.at(-1));
          break;
        case 'volume': state.volumes.push({ Name: args.at(-1), Labels: own }); break;
        case 'run': state.containers.push(vc(nextContainer)); break;
        default: assert.fail(args.join(' '));
      }
    }
  };
  return { state, actions, runtime };
}
test('plan is read-only and hashes the exact resources; replace stops, removes container and old image before starting one new container', async () => {
  const s = simulation();
  const plan = await deployLocalTest(s.runtime, candidate);
  assert.equal(plan.port, 19081);
  assert.deepEqual(plan.oldImages, [oldId]);
  assert.deepEqual(s.actions, [['publisher', oldContainer]]);
  await deployLocalTest(s.runtime, candidate, plan.planSha256);
  const effects = s.actions.slice(2);
  assert.deepEqual(effects.map(args => args[0]), ['stop', 'rm', 'rmi', 'port-free', 'run', 'ready']);
  assert.deepEqual(effects[0], ['stop', '--time', '45', oldContainer]);
  assert.deepEqual(effects[1], ['rm', oldContainer]);
  assert.deepEqual(effects[2], ['rmi', '--no-prune', oldId]);
  const run = effects[4];
  assert.equal(run[run.indexOf('--publish') + 1], portBinding);
  assert.equal(run.at(-1), nextId);
  assert.ok(run.includes(`${testVolumes(dataset)[1]}:/var/lib/lexiflow/preferences`));
  assert.deepEqual(s.state.images.map(row => row.Id), [nextId]);
  assert.deepEqual(s.state.volumes, inventory().volumes);
  assert.ok(s.state.containers.some(row => row.Names[0] === 'unrelated-gateway'));
});
test('drift, foreign resources, stale ad hoc tests, and wrong artifact pairs block without writes', async () => {
  for (const [edit, reason] of [
    [s => { s.containers.push({ Names: ['unrelated'], Ports: [{ ...binding, host_port: 19080, range: 2 }] }); }, /PORT_OR_NAME_IN_USE/u],
    [s => { s.containers.push({ Names: ['lexiflow-v30-test-old'], Ports: [{ ...binding, host_port: 19083 }] }); }, /LEGACY_RESOURCES/u],
    [s => { s.images.push({ Names: ['localhost/lexiflow-v30-test-leftover:local'] }); }, /LEGACY_RESOURCES/u],
    [s => { s.containers.push({ Names: ['other'], ImageID: oldId }); }, /IMAGE_IN_USE/u],
    [s => { s.images[0].Names.push('localhost/other-project:local'); }, /IMAGE_OWNERSHIP_UNPROVEN/u],
    [s => { s.volumes[0].Labels = {}; }, /VOLUME_OWNERSHIP_UNPROVEN/u],
    [s => { s.volumeUsers.push({ Id: oldContainer, Labels: {} }); }, /VOLUME_IN_USE/u],
    [s => { s.pods.push({ Name: testName }); }, /POD_CONFLICT/u],
    [s => { delete s.images[1].Labels['io.lexiflow.build-id']; }, /ARTIFACT_MISMATCH/u],
    [s => { s.containers[0].Ports = [{ ...binding, host_port: 19083 }]; }, /CONTAINER_OWNERSHIP_UNPROVEN/u],
  ]) {
    const s = simulation(edit);
    await assert.rejects(deployLocalTest(s.runtime, candidate), reason);
    assert.deepEqual(s.actions, []);
  }
  const s = simulation();
  const digest = planLocalTest(s.state, candidate).planSha256;
  s.state.containers[0].State = 'exited';
  await assert.rejects(deployLocalTest(s.runtime, candidate, digest), /PLAN_CHANGED/u);
  assert.deepEqual(s.actions, []);
});
test('incomplete cleanup or a host-only conflict never deploys a fallback instance', async () => {
  for (const [options, reason] of [
    [{ stopUnobserved: true }, /STOP_UNOBSERVED/u],
    [{ imageFailure: true }, /RMI_FAILED/u],
    [{ portBusy: true }, /PORT_IN_USE/u],
  ]) {
    const s = simulation(undefined, options);
    await assert.rejects(deployLocalTest(s.runtime, candidate, planLocalTest(s.state, candidate).planSha256), reason);
    assert.equal(s.actions.some(a => a[0] === 'run'), false);
  }
});
test('fresh slot creates only owned volumes; changing publication changes only the lexicon volume', async () => {
  const next = testVolumes({ ...dataset, releaseId: 'another' });
  assert.notEqual(next[0], testVolumes(dataset)[0]);
  assert.equal(next[1], testVolumes(dataset)[1]);
  const s = simulation(state => { state.containers.shift(); state.images.shift(); state.volumes = []; });
  const plan = await deployLocalTest(s.runtime, candidate);
  assert.deepEqual(plan.createVolumes, testVolumes(dataset));
  await deployLocalTest(s.runtime, candidate, plan.planSha256);
  assert.deepEqual(s.actions.filter(a => a[0] === 'volume'), testVolumes(dataset).map(name => ['volume', 'create', '--label', `${slotLabel}=${testName}`, name]));
  assert.equal(s.actions.filter(a => a[0] === 'run').length, 1);
});


const artifactRecord = { role: 'lexiflow-local-test', imageId: 'sha256:' + nextId,
  tag: 'localhost/lexiflow-test:' + 'a'.repeat(64) + '-000000000000', imageSha256: 'a'.repeat(64),
  extensionSha256: candidate.extensionSha256, apiContract: 'caption-hints.v5' };

test('one physical Chrome directory is stable across builds; stale plans are not carried forward', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'lexiflow-test-publish-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const paths = await slotPaths(path.join(root, '.local/podman-test'), true);
  const publish = async value => {
    const stage = await mkdtemp(path.join(paths.slot, '.build-'));
    const output = path.join(stage, 'next');
    await mkdir(path.join(output, 'chrome'), { recursive: true, mode: 0o700 });
    await writeFile(path.join(output, names.record), JSON.stringify(artifactRecord), { mode: 0o600 });
    await writeFile(path.join(output, 'chrome/manifest.json'), value);
    await publishBuild(paths.current, stage, output);
    await rm(stage, { recursive: true });
  };
  await publish('first');
  const chrome = path.join(paths.current, 'chrome/manifest.json');
  assert.equal(await readFile(chrome, 'utf8'), 'first');
  await writePlan(paths.current, { status: 'PASS', action: 'plan', nextId, extensionSha256: candidate.extensionSha256, planSha256: '9'.repeat(64) });
  assert.equal(loadPlan(paths.current, candidate).planSha256, '9'.repeat(64));
  assert.throws(() => loadPlan(paths.current, { ...candidate, extensionSha256:'8'.repeat(64) }), /PLAN_NOT_CURRENT/u);
  await publish('second');
  assert.equal(await readFile(chrome, 'utf8'), 'second');
  assert.throws(() => loadPlan(paths.current, candidate), /PLAN_REQUIRED/u);
});

test('failed publication restores the current tree; symlink path fails closed', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'lexiflow-test-failed-publish-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const paths = await slotPaths(path.join(root, '.local/podman-test'), true);
  const stage = await mkdtemp(path.join(paths.slot, '.build-'));
  await mkdir(paths.current, { mode: 0o700 });
  await writeFile(path.join(paths.current, names.record), JSON.stringify(artifactRecord));
  await assert.rejects(publishBuild(paths.current, stage, path.join(stage, 'absent')), { code: 'ENOENT' });
  assert.deepEqual(JSON.parse(await readFile(path.join(paths.current, names.record))), artifactRecord);
  const alias = path.join(root, '.local/alias');
  await symlink(paths.slot, alias);
  await assert.rejects(slotPaths(alias), /DIRECTORY_UNSAFE/u);
});
