import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { javaBuildEnvironment } from '../environment/java-build-environment.mjs';

const temurin = 'openjdk version "25.0.4" 2026-07-21 LTS\nOpenJDK Runtime Environment Temurin-25.0.4+7 (build 25.0.4+7-LTS)\n';
function probe(t, result) {
  const stub = t.mock.method(childProcess, 'spawnSync', () => ({ status: 0, signal: null, stdout: '', stderr: temurin, ...result }));
  syncBuiltinESMExports();
  t.after(() => { stub.mock.restore(); syncBuiltinESMExports(); });
  return stub;
}

function workspace(t) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow java repo '));
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }));
  const home = path.join(repo, 'inherited');
  fs.mkdirSync(path.join(home, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(home, 'release'), 'JAVA_VERSION="25.0.4"\nIMPLEMENTOR="Eclipse Adoptium"\nIMPLEMENTOR_VERSION="Temurin-25.0.4+7"\n');
  fs.writeFileSync(path.join(home, 'bin/java'), '#!/bin/sh\n', { mode: 0o755 });
  return { repo, home };
}

test('workspace-selected JAVA_HOME is shared by probe and build without mutating the caller', t => {
  const stub = probe(t), { repo, home } = workspace(t);
  const env = { JAVA_HOME: home, PATH: '/another/jdk/bin:/usr/bin', HOME: '/home/test' };
  const original = { ...env };
  const selected = javaBuildEnvironment(env, { repo });
  assert.deepEqual(env, original);
  assert.equal(selected.PATH, `${home}/bin:/another/jdk/bin:/usr/bin`);
  const [command, args, options] = stub.mock.calls[0].arguments;
  assert.equal(command, path.join(home, 'bin/java'));
  assert.deepEqual(args, ['-version']);
  assert.deepEqual(options.env, selected);
  assert.equal(options.timeout, 10000);
  assert.equal(options.maxBuffer, 64 * 1024);
  assert.deepEqual(Object.keys(selected).sort(), Object.keys(env).sort());
});

test('repository is required and PATH-only Java is never probed', t => {
  const stub = probe(t), { repo } = workspace(t);
  assert.throws(() => javaBuildEnvironment({ PATH: '/jdk/bin' }), TypeError);
  for (const bad of [undefined, '', 'relative']) {
    assert.throws(() => javaBuildEnvironment({ PATH: '/jdk/bin' }, { repo: bad }), /JAVA_REPOSITORY_REQUIRED/);
  }
  for (const env of [{ PATH: '/jdk/bin' }, { PATH: '/jdk/bin', JAVA_HOME: '' }]) {
    assert.throws(() => javaBuildEnvironment(env, { repo }), /JAVA_25_UNAVAILABLE/);
  }
  assert.equal(stub.mock.callCount(), 0);
});

for (const [name, result] of [
  ['stdout', { stdout: temurin, stderr: '' }],
  ['both streams', { stdout: temurin }],
  ['major only', { stderr: 'java version "25"\n' }],
  ['build suffix', { stderr: 'openjdk version "25.0.4+7"\n' }],
  ['CRLF', { stderr: temurin.replaceAll('\n', '\r\n') }],
]) test(`accept Java 25 output: ${name}`, t => {
  probe(t, result);
  const { repo, home } = workspace(t);
  assert.equal(javaBuildEnvironment({ JAVA_HOME: home, PATH: '/jdk/bin' }, { repo }).JAVA_HOME, home);
});

for (const [name, result, code] of [
  ['missing', { error: { code: 'ENOENT' }, status: null }, 'JAVA_EXECUTABLE_MISSING'],
  ['permission', { error: { code: 'EACCES' }, status: null }, 'JAVA_EXECUTABLE_DENIED'],
  ['timeout', { error: { code: 'ETIMEDOUT' }, signal: 'SIGTERM', status: null }, 'JAVA_PROBE_TIMEOUT'],
  ['buffer', { error: { code: 'ENOBUFS' }, status: null }, 'JAVA_PROBE_OUTPUT_LIMIT'],
  ['unknown spawn failure', { error: { code: 'UNKNOWN' }, status: null }, 'JAVA_PROBE_START_FAILED'],
  ['signal', { signal: 'SIGABRT', status: null }, 'JAVA_PROBE_SIGNAL'],
  ['exit', { status: 1 }, 'JAVA_PROBE_EXIT'],
  ['null status', { status: null }, 'JAVA_PROBE_EXIT'],
  ['no version', { stderr: 'private-output' }, 'JAVA_VERSION_UNRECOGNIZED'],
  ['conflict', { stdout: 'openjdk version "26.0.2"\n' }, 'JAVA_VERSION_UNRECOGNIZED'],
  ['warning containing fake version', { stderr: 'warning: openjdk version "25.0.4"' }, 'JAVA_VERSION_UNRECOGNIZED'],
  ['Java 26', { stderr: 'openjdk version "26.0.2"\n' }, 'JAVA_VERSION_UNSUPPORTED'],
  ['Java 250', { stderr: 'openjdk version "250.0.4"\n' }, 'JAVA_VERSION_UNSUPPORTED'],
  ['EA', { stderr: 'openjdk version "25-ea"\n' }, 'JAVA_VERSION_UNSUPPORTED'],
]) test(`classify ${name} without disclosing raw tool output`, t => {
  const stub = probe(t, { stdout: 'private-output', ...result });
  const { repo, home } = workspace(t);
  assert.throws(() => javaBuildEnvironment({ JAVA_HOME: home, PATH: '/fallback' }, { repo }), error => {
    assert.match(error.message, new RegExp(`^${code}：`));
    assert.match(error.message, /Java 来源=JAVA_HOME/);
    assert.doesNotMatch(error.message, /private-output/);
    return true;
  });
  assert.equal(stub.mock.callCount(), 1, 'invalid explicit home must not trigger a fallback');
});

test('relative JAVA_HOME is rejected before launching a subprocess', t => {
  const stub = probe(t), { repo } = workspace(t);
  assert.throws(() => javaBuildEnvironment({ JAVA_HOME: 'relative' }, { repo }), /JAVA_HOME_INVALID/);
  assert.equal(stub.mock.callCount(), 0);
});

test('real subprocess handles SDKMAN-style current symlink and spaces ahead of a different PATH Java', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow java '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, 'jdk 25'), current = path.join(root, 'current'), other = path.join(root, 'other');
  fs.mkdirSync(path.join(home, 'bin'), { recursive: true }); fs.mkdirSync(other);
  fs.writeFileSync(path.join(home, 'bin/java'), '#!/bin/sh\nprintf \'openjdk version "25.0.4"\\n\' >&2\n', { mode: 0o755 });
  fs.writeFileSync(path.join(other, 'java'), '#!/bin/sh\nprintf \'openjdk version "26.0.2"\\n\' >&2\n', { mode: 0o755 });
  fs.writeFileSync(path.join(home, 'release'), 'JAVA_VERSION="25.0.4"\nIMPLEMENTOR="Eclipse Adoptium"\nIMPLEMENTOR_VERSION="Temurin-25.0.4+7"\n');
  fs.symlinkSync(home, current, 'dir');
  const env = { JAVA_HOME: current, PATH: other };
  // 旧预检只使用 PATH，会拒绝明明已由 JAVA_HOME 选定的 Java 25。
  const old = childProcess.spawnSync('java', ['-version'], { env, encoding: 'utf8' });
  assert.equal(old.status, 0); assert.match(old.stderr, /26\.0\.2/);
  const selected = javaBuildEnvironment(env, { repo: root });
  const build = childProcess.spawnSync('/bin/sh', ['-c', '"$JAVA_HOME/bin/java" -version; java -version'], { env: selected, encoding: 'utf8' });
  assert.equal(build.status, 0);
  assert.equal([...build.stderr.matchAll(/25\.0\.4/g)].length, 2);
});

test('production selection prefers explicit then repository-local then inherited Temurin 25', t => {
  const stub = probe(t);
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow java repo '));
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }));
  const fixture = (home, version = '25.0.4') => {
    fs.mkdirSync(path.join(home, 'bin'), { recursive: true });
    fs.writeFileSync(path.join(home, 'release'), `JAVA_VERSION="${version}"\nIMPLEMENTOR="Eclipse Adoptium"\nIMPLEMENTOR_VERSION="Temurin-25.0.4+7"\n`);
    fs.writeFileSync(path.join(home, 'bin/java'), '#!/bin/sh\n', { mode: 0o755 });
  };
  const local = path.join(repo, '.local/toolchains/jdk-25/Contents/Home');
  const inherited = path.join(repo, 'inherited');
  const explicit = path.join(repo, 'explicit');
  fixture(local); fixture(inherited); fixture(explicit);
  const base = { PATH: '/java26/bin', JAVA_HOME: inherited };
  assert.equal(javaBuildEnvironment(base, { repo }).JAVA_HOME, local);
  assert.equal(javaBuildEnvironment({ ...base, LEXIFLOW_JAVA_HOME: explicit }, { repo }).JAVA_HOME, explicit);
  assert.equal(stub.mock.calls.at(-1).arguments[0], path.join(explicit, 'bin/java'));
  assert.throws(() => javaBuildEnvironment({ ...base, LEXIFLOW_JAVA_HOME: path.join(repo, 'missing') }, { repo }), /JAVA_RELEASE_INVALID/);
  assert.equal(stub.mock.callCount(), 2, 'invalid explicit home must not fall back');
  fs.rmSync(path.join(repo, '.local'), { recursive: true, force: true });
  assert.equal(javaBuildEnvironment(base, { repo }).JAVA_HOME, inherited);
  assert.throws(() => javaBuildEnvironment({ PATH: '/java26/bin' }, { repo }), /JAVA_25_UNAVAILABLE/);
});

test('workspace selection rejects wrong vendor, major, malformed release and non-executable before probe', t => {
  const stub = probe(t), { repo, home } = workspace(t);
  const release = path.join(home, 'release'), original = fs.readFileSync(release, 'utf8');
  for (const bad of [original.replace('Eclipse Adoptium', 'Other Vendor'),
    original.replace('JAVA_VERSION="25.0.4"', 'JAVA_VERSION="26.0.4"'),
    original.replace('Temurin-25.0.4+7', 'Other-25.0.4+7'), original + 'JAVA_VERSION="25.0.4"\n']) {
    fs.writeFileSync(release, bad);
    assert.throws(() => javaBuildEnvironment({ JAVA_HOME: home }, { repo }), /JAVA_RELEASE_INVALID/);
  }
  fs.writeFileSync(release, original);
  fs.chmodSync(path.join(home, 'bin/java'), 0o644);
  assert.throws(() => javaBuildEnvironment({ JAVA_HOME: home }, { repo }), /JAVA_RELEASE_INVALID/);
  assert.equal(stub.mock.callCount(), 0);
});
