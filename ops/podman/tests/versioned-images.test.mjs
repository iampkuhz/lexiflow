import test from 'node:test';
import assert from 'node:assert/strict';
import { deploymentImageTag, versionedImageReference, versionedImageLabels, removeVersionedImage } from '../images/versioned-images.mjs';

const id = `sha256:${'a'.repeat(64)}`, cid = 'b'.repeat(64), otherCid = 'c'.repeat(64);
const ok = stdout => ({ commandExitCode: 0, stdout });
const imageData = (imageId = id, role = 'api', labels = {}) => [{ Id: imageId, Labels: { 'io.lexiflow.image-role': role, 'io.lexiflow.deployment-version': '1.2.3', ...labels } }];
const containerData = (containerId = cid, labels = {}) => [{ Id: `/${containerId}`, Image: id, State: { Running: true }, Config: { Labels: {
  'lexiflow.installation': '1'.repeat(32), 'com.docker.compose.project': `lexiflow-local-${'1'.repeat(32)}`,
  'io.podman.compose.project': `lexiflow-local-${'1'.repeat(32)}`, 'com.docker.compose.service': 'api', 'io.podman.compose.service': 'api', ...labels,
} } }];

function fake({ refs = [cid], imageId = id, imageLabels = {}, containers = {}, drift = false, referenceDrift = false, existsCode = 0, failAt = null } = {}) {
  const calls = [];
  const removed = new Set();
  let imageReads = 0, psReads = 0;
  const run = async (_command, args, options) => {
    assert.equal(options?.capture, true);
    calls.push([...args]);
    const key = args.join(' ');
    if (failAt === key) throw new Error('must not leak');
    if (key.startsWith('image exists')) return { commandExitCode: existsCode };
    if (key.startsWith('image inspect')) {
      imageReads++;
      return ok(JSON.stringify(imageData(drift && imageReads > 1 ? `sha256:${'d'.repeat(64)}` : imageId, args[2].split('/').at(-1).split(':')[0].replace('lexiflow-', ''), imageLabels)));
    }
    if (key.startsWith('ps ')) {
      psReads++;
      const listed = referenceDrift && psReads > 1 ? [...refs, otherCid] : refs.filter(value => !removed.has(value));
      return ok(listed.join('\n'));
    }
    if (key.startsWith('container inspect')) {
      const containerId = args[2];
      return ok(JSON.stringify(containers[containerId] ?? containerData(containerId)));
    }
    if (args[0] === 'rm') removed.add(args[1]);
    return { commandExitCode: 0, stdout: '' };
  };
  return { run, calls };
}

test('deployment tag is stable, lowercases SNAPSHOT, and excludes detailed build identity', () => {
  assert.equal(deploymentImageTag('2.0.1-SNAPSHOT'), '2.0.1-snapshot');
  assert.equal(deploymentImageTag('2.0.1'), '2.0.1');
  assert.equal(versionedImageReference('api', '2.0.1-SNAPSHOT'), 'localhost/lexiflow-api:2.0.1-snapshot');
  assert.deepEqual(versionedImageLabels('postgres', '2.0.1-SNAPSHOT'), ['--label', 'io.lexiflow.image-role=postgres', '--label', 'io.lexiflow.deployment-version=2.0.1-snapshot']);
  for (const value of ['2.0', 'v2.0.1', '2.0.1+build', '2.0.1-SNAPSHOT.abc', '2.0.1-snapshot', '01.2.3', '0.0.0', '65536.1.1', '2.0.1\n']) assert.throws(() => deploymentImageTag(value), /VERSIONED_IMAGE_VERSION_INVALID/);
  // Source/build details are intentionally not inputs to this tag API.
  assert.equal(versionedImageReference('api', '2.0.1'), versionedImageReference('api', '2.0.1'));
  assert.throws(() => versionedImageReference('worker', '2.0.1'), /VERSIONED_IMAGE_ROLE_INVALID/);
});

test('missing exact image is a no-op; only exit code 1 means absent', async () => {
  const absent = fake({ existsCode: 1 });
  assert.deepEqual(await removeVersionedImage({ run: absent.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), { removed: false, imageId: null, removedContainerIds: [] });
  assert.deepEqual(absent.calls.map(args => args[0]), ['image']);
  await assert.rejects(removeVersionedImage({ run: fake({ existsCode: 125 }).run, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_CHECK_FAILED/);
});

test('removal preflights then stops, removes owned container, rechecks and removes only exact tag', async () => {
  const { run, calls } = fake();
  assert.deepEqual(await removeVersionedImage({ run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), { removed: true, imageId: id, removedContainerIds: [cid] });
  const names = calls.map(args => args.join(' '));
  assert.deepEqual(names.slice(0, 4), ['image exists localhost/lexiflow-api:1.2.3', 'image inspect localhost/lexiflow-api:1.2.3 --format json', `ps -a --no-trunc --filter ancestor=${id.slice(7)} --format {{.ID}}`, `container inspect ${cid} --format json`]);
  assert.ok(names.indexOf(`stop --time 30 ${cid}`) < names.indexOf(`rm ${cid}`));
  assert.equal(names.at(-1), 'rmi localhost/lexiflow-api:1.2.3');
  assert.ok(!names.some(name => name.includes('prune') || name.endsWith(' rm -v') || name.includes(' rm -f')));
});

test('rejects cross-install, unknown labels, malformed image identity and inspection failures without deleting', async () => {
  for (const [container, opts, code] of [
    [containerData(cid, { 'lexiflow.installation': 'another' }), {}, 'VERSIONED_IMAGE_CONTAINER_MISMATCH'],
    [containerData(cid, { 'com.docker.compose.project': undefined, 'io.podman.compose.project': undefined }), {}, 'VERSIONED_IMAGE_CONTAINER_MISMATCH'],
    [containerData(cid, { 'io.podman.compose.service': 'postgres' }), {}, 'VERSIONED_IMAGE_CONTAINER_MISMATCH'],
  ]) {
    const f = fake({ containers: { [cid]: container }, ...opts });
    await assert.rejects(removeVersionedImage({ run: f.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), new RegExp(code));
    assert.ok(!f.calls.some(args => args[0] === 'stop' || args[0] === 'rm' || args[0] === 'rmi'));
  }
  const badImage = fake({ imageId: 'short' });
  await assert.rejects(removeVersionedImage({ run: badImage.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_OWNERSHIP_MISMATCH/);
  await assert.rejects(removeVersionedImage({ run: fake({ failAt: 'image inspect localhost/lexiflow-api:1.2.3 --format json' }).run, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_INSPECT_FAILED/);
});

test('rejects source-tools in use and refuses unowned containers even with a null installation', async () => {
  const source = fake();
  await assert.rejects(removeVersionedImage({ run: source.run, role: 'source-tools', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_SOURCE_TOOLS_IN_USE/);
  assert.ok(!source.calls.some(args => args[0] === 'stop' || args[0] === 'rm' || args[0] === 'rmi'));
  const nullOwner = fake();
  await assert.rejects(removeVersionedImage({ run: nullOwner.run, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_INSTALLATION_REQUIRED/);
});

test('rechecks tag identity and the complete reference set before image deletion', async () => {
  const imageDrift = fake({ drift: true });
  await assert.rejects(removeVersionedImage({ run: imageDrift.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_DRIFT/);
  assert.ok(!imageDrift.calls.some(args => args[0] === 'rmi'));
  const newReference = fake({ refs: [], referenceDrift: true });
  await assert.rejects(removeVersionedImage({ run: newReference.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_DRIFT/);
  assert.ok(!newReference.calls.some(args => args[0] === 'rmi'));
});

test('uses the actual runCommand text/throw contract rather than inferring object-shaped success', async () => {
  const f = fake();
  const run = async (...args) => {
    const result = await f.run(...args);
    if (result.commandExitCode !== 0) throw Object.assign(new Error('command rejected'), { commandExitCode: result.commandExitCode });
    return result.stdout ?? '';
  };
  const result = await removeVersionedImage({ run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) });
  assert.equal(result.removed, true);
  assert.ok(f.calls.filter(args => args[0] === 'ps').every(args => args.includes('--no-trunc') && args.includes(`ancestor=${id.slice(7)}`)));
  const absent = async () => { throw Object.assign(new Error('absent'), { commandExitCode: 1 }); };
  assert.equal((await removeVersionedImage({ run: absent, role: 'api', baseVersion: '1.2.3' })).removed, false);
  for (const commandExitCode of [125, undefined]) {
    const rejected = async () => { throw Object.assign(new Error('private engine failure'), { commandExitCode }); };
    await assert.rejects(removeVersionedImage({ run: rejected, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_CHECK_FAILED/);
  }
});

test('checks every reference before stopping an owned container and never removes a second tag', async () => {
  const f = fake({ refs: [cid, otherCid], containers: { [otherCid]: containerData(otherCid, { 'lexiflow.installation': 'another' }) } });
  await assert.rejects(removeVersionedImage({ run: f.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_CONTAINER_MISMATCH/);
  assert.ok(!f.calls.some(args => ['stop', 'rm', 'rmi'].includes(args[0])));
  const unused = fake({ refs: [] });
  await removeVersionedImage({ run: unused.run, role: 'api', baseVersion: '1.2.3' });
  assert.deepEqual(unused.calls.filter(args => args[0] === 'rmi'), [['rmi', 'localhost/lexiflow-api:1.2.3']]);
});


test('normalizes full unprefixed IDs from real Podman JSON without accepting abbreviated IDs', async () => {
  const container = containerData();
  container[0].Image = id.slice(7);
  const f = fake({ imageId: id.slice(7), containers: { [cid]: container } });
  const result = await removeVersionedImage({ run: f.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) });
  assert.equal(result.imageId, id);
  assert.deepEqual(result.removedContainerIds, [cid]);
  const short = fake({ imageId: id.slice(7, 19) });
  await assert.rejects(removeVersionedImage({ run: short.run, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_OWNERSHIP_MISMATCH/);
});


test('role/version mismatch and command failures do not authorize fallback deletion', async () => {
  for (const imageLabels of [{ 'io.lexiflow.image-role': 'unknown' }, { 'io.lexiflow.deployment-version': '9.9.9' }]) {
    const f = fake({ imageLabels });
    await assert.rejects(removeVersionedImage({ run: f.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), /VERSIONED_IMAGE_OWNERSHIP_MISMATCH/);
    assert.ok(!f.calls.some(args => ['stop', 'rm', 'rmi'].includes(args[0])));
  }
  for (const [failAt, code] of [
    [`stop --time 30 ${cid}`, 'VERSIONED_IMAGE_CONTAINER_STOP_FAILED'],
    [`rm ${cid}`, 'VERSIONED_IMAGE_CONTAINER_REMOVE_FAILED'],
    ['rmi localhost/lexiflow-api:1.2.3', 'VERSIONED_IMAGE_REMOVE_FAILED'],
  ]) {
    const f = fake({ failAt });
    await assert.rejects(removeVersionedImage({ run: f.run, role: 'api', baseVersion: '1.2.3', installationId: '1'.repeat(32) }), new RegExp(code));
    assert.ok(!f.calls.some(args => args.includes('--force') || args.includes('-f') || args.includes('-v') || args.includes('prune')));
  }
});


test('malformed captured output fails closed before resource deletion', async () => {
  const run = async () => ({ commandExitCode: 0, stdout: {} });
  await assert.rejects(removeVersionedImage({ run, role: 'api', baseVersion: '1.2.3' }), /VERSIONED_IMAGE_CHECK_FAILED/);
});
