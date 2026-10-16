import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveSourceBaseImage } from '../images/source-base-image.mjs';

const java = 'docker.io/library/eclipse-temurin:25-jre';
const pg = 'docker.io/library/postgres:17-bookworm';
const id = 'a'.repeat(64);
function metadata(reference, overrides = {}) {
  const env = reference === java
    ? ['JAVA_VERSION=jdk-25.0.4.1+1']
    : ['PG_MAJOR=17', 'PG_VERSION=17.6-1.pgdg13+1'];
  return { Id: id, Os: 'linux', Architecture: 'arm64', Config: { Env: env }, ...overrides };
}
function runner({ missing = false, images, failure = null } = {}) {
  const calls = [];
  return { calls, run: async (_bin, args) => {
    const command = args.join(' '); calls.push(command);
    if (failure === command) { const error = new Error('hidden'); error.commandExitCode = 2; throw error; }
    if (command.startsWith('image exists')) return { commandExitCode: missing ? 1 : 0, stdout: '' };
    if (command.startsWith('pull ')) return { commandExitCode: 0, stdout: '' };
    if (command.startsWith('image inspect ')) {
      const item = images?.shift() ?? metadata(args[2]);
      return JSON.stringify([item]);
    }
    throw new Error('unexpected command');
  } };
}

test('cached validated source image returns full immutable ID without pulling', async () => {
  const fake = runner();
  assert.equal(await resolveSourceBaseImage({ run: fake.run, reference: java }), `sha256:${id}`);
  assert.equal(fake.calls.filter(x => x.startsWith('pull ')).length, 0);
  assert.equal(fake.calls.length, 3);
});

test('missing image pulls once for linux/arm64 then validates', async () => {
  const fake = runner({ missing: true });
  assert.equal(await resolveSourceBaseImage({ run: fake.run, reference: pg }), `sha256:${id}`);
  assert.deepEqual(fake.calls.slice(0, 2), ['image exists docker.io/library/postgres:17-bookworm', 'pull --platform linux/arm64 docker.io/library/postgres:17-bookworm']);
  assert.equal(fake.calls.filter(x => x.startsWith('pull ')).length, 1);
});

test('initial Java 25 GA and patch releases are both legal cached majors', async () => {
  for (const version of ['jdk-25+36', 'jdk-25.0.1+8', 'jdk-25.0.4.1+1']) {
    const fake = runner({ images: [metadata(java, { Config: { Env: [`JAVA_VERSION=${version}`] } })] });
    assert.equal(await resolveSourceBaseImage({ run: fake.run, reference: java }), `sha256:${id}`);
    assert.equal(fake.calls.some(x => x.startsWith('pull ')), false);
  }
});

for (const [name, reference, overrides] of [
  ['wrong major', pg, { Config: { Env: ['PG_MAJOR=16', 'PG_VERSION=16.9'] } }],
  ['wrong architecture', java, { Architecture: 'amd64' }],
  ['duplicate metadata', java, { Config: { Env: ['JAVA_VERSION=jdk-25.1', 'JAVA_VERSION=jdk-25.2'] } }],
  ['missing metadata', pg, { Config: { Env: ['PG_MAJOR=17'] } }],
]) test(`rejects ${name}`, async () => {
  const fake = runner({ images: [metadata(reference, overrides)] });
  await assert.rejects(resolveSourceBaseImage({ run: fake.run, reference }), /SOURCE_BASE_IMAGE_INVALID/);
});

test('unknown reference is rejected before any command', async () => {
  const fake = runner();
  await assert.rejects(resolveSourceBaseImage({ run: fake.run, reference: 'attacker/java:25' }), /SOURCE_BASE_IMAGE_INVALID/);
  assert.deepEqual(fake.calls, []);
});

test('engine failure other than not-found never causes a pull', async () => {
  const fake = runner({ failure: `image exists ${java}` });
  await assert.rejects(resolveSourceBaseImage({ run: fake.run, reference: java }), /SOURCE_BASE_IMAGE_UNAVAILABLE/);
  assert.equal(fake.calls.some(x => x.startsWith('pull ')), false);
});

test('mutable tag changing between validations is rejected', async () => {
  const fake = runner({ images: [metadata(java), { ...metadata(java), Id: 'b'.repeat(64) }] });
  await assert.rejects(resolveSourceBaseImage({ run: fake.run, reference: java }), /SOURCE_BASE_IMAGE_CHANGED/);
});

test('invalid inspect JSON and multiple records are rejected', async () => {
  for (const body of ['not-json', '[]', '[{},{}]']) {
    const fake = runner();
    fake.run = async (_bin, args) => args[1] === 'exists' ? { commandExitCode: 0, stdout: '' } : body;
    await assert.rejects(resolveSourceBaseImage({ run: fake.run, reference: java }), /SOURCE_BASE_IMAGE_INVALID/);
  }
});

for (const stage of ['image exists', 'pull ', 'image inspect']) {
  test(`source base ${stage.trim()} cancellation retains typed safe cancellation rather than availability`, async () => {
    const fake = runner({ missing: stage === 'pull ' });
    const run = async (bin, args, options) => {
      if (args.join(' ').startsWith(stage)) {
        const error = new Error('private auxiliary output');
        error.commandInterrupted = true;
        throw error;
      }
      return fake.run(bin, args, options);
    };
    await assert.rejects(resolveSourceBaseImage({ run, reference: java }), { message: 'DEPLOYMENT_OPERATION_CANCELLED' });
  });
}
