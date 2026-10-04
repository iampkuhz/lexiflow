import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { javaBuildEnvironment } from '../java-runtime.mjs';

const temurin = 'openjdk version "25.0.4" 2026-07-21 LTS\nOpenJDK Runtime Environment Temurin-25.0.4+7 (build 25.0.4+7-LTS)\n';
function probe(t, result) {
  const stub = t.mock.method(childProcess, 'spawnSync', () => ({ status: 0, signal: null, stdout: '', stderr: temurin, ...result }));
  syncBuiltinESMExports();
  t.after(() => { stub.mock.restore(); syncBuiltinESMExports(); });
  return stub;
}

test('explicit JAVA_HOME is shared by probe and build without mutating the caller', t => {
  const stub = probe(t);
  const env = { JAVA_HOME: '/sdkman/java/current', PATH: '/another/jdk/bin:/usr/bin', HOME: '/home/test' };
  const selected = javaBuildEnvironment(env);
  assert.deepEqual(env, { JAVA_HOME: '/sdkman/java/current', PATH: '/another/jdk/bin:/usr/bin', HOME: '/home/test' });
  assert.equal(selected.PATH, '/sdkman/java/current/bin:/another/jdk/bin:/usr/bin');
  const [command, args, options] = stub.mock.calls[0].arguments;
  assert.equal(command, '/sdkman/java/current/bin/java');
  assert.deepEqual(args, ['-version']);
  assert.deepEqual(options.env, selected);
  assert.equal(options.timeout, 10000);
  assert.equal(options.maxBuffer, 64 * 1024);
  assert.deepEqual(Object.keys(selected).sort(), Object.keys(env).sort());
});

test('without JAVA_HOME use PATH unchanged, including an empty JAVA_HOME', t => {
  const stub = probe(t);
  for (const env of [{ PATH: '/jdk/bin' }, { PATH: '/jdk/bin', JAVA_HOME: '' }]) {
    assert.deepEqual(javaBuildEnvironment(env), env);
  }
  assert.equal(stub.mock.calls[0].arguments[0], 'java');
});

for (const [name, result] of [
  ['stdout', { stdout: temurin, stderr: '' }],
  ['both streams', { stdout: temurin }],
  ['major only', { stderr: 'java version "25"\n' }],
  ['build suffix', { stderr: 'openjdk version "25.0.4+7"\n' }],
  ['CRLF', { stderr: temurin.replaceAll('\n', '\r\n') }],
]) test(`accept Java 25 output: ${name}`, t => {
  probe(t, result);
  assert.deepEqual(javaBuildEnvironment({ PATH: '/jdk/bin' }), { PATH: '/jdk/bin' });
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
  assert.throws(() => javaBuildEnvironment({ JAVA_HOME: '/explicit/jdk', PATH: '/fallback' }), error => {
    assert.match(error.message, new RegExp(`^${code}：`));
    assert.match(error.message, /Java 来源=JAVA_HOME/);
    assert.doesNotMatch(error.message, /private-output/);
    return true;
  });
  assert.equal(stub.mock.callCount(), 1, 'invalid explicit home must not trigger a fallback');
});

test('relative JAVA_HOME is rejected before launching a subprocess', t => {
  const stub = probe(t);
  assert.throws(() => javaBuildEnvironment({ JAVA_HOME: 'relative' }), /JAVA_HOME_INVALID/);
  assert.equal(stub.mock.callCount(), 0);
});

test('real subprocess handles SDKMAN-style current symlink and spaces ahead of a different PATH Java', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow java '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, 'jdk 25'), current = path.join(root, 'current'), other = path.join(root, 'other');
  fs.mkdirSync(path.join(home, 'bin'), { recursive: true }); fs.mkdirSync(other);
  fs.writeFileSync(path.join(home, 'bin/java'), '#!/bin/sh\nprintf \'openjdk version "25.0.4"\\n\' >&2\n', { mode: 0o755 });
  fs.writeFileSync(path.join(other, 'java'), '#!/bin/sh\nprintf \'openjdk version "26.0.2"\\n\' >&2\n', { mode: 0o755 });
  fs.symlinkSync(home, current, 'dir');
  const env = { JAVA_HOME: current, PATH: other };
  // 旧预检只使用 PATH，会拒绝明明已由 JAVA_HOME 选定的 Java 25。
  const old = childProcess.spawnSync('java', ['-version'], { env, encoding: 'utf8' });
  assert.equal(old.status, 0); assert.match(old.stderr, /26\.0\.2/);
  const selected = javaBuildEnvironment(env);
  const build = childProcess.spawnSync('/bin/sh', ['-c', '"$JAVA_HOME/bin/java" -version; java -version'], { env: selected, encoding: 'utf8' });
  assert.equal(build.status, 0);
  assert.equal([...build.stderr.matchAll(/25\.0\.4/g)].length, 2);
});
