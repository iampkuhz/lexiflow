import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, unlinkSync, copyFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import fsDefault from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { parseVersion, readVersion, checkReleaseSource } from '../version.mjs';
import { evaluateTapSummary, runTests } from '../check.mjs';

const git = (cwd, ...args) => execFileSync('git', args, { cwd, stdio: 'ignore', timeout: 10_000 });
function fixture({ head = true, ignore = false } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  git(root, 'init', '-q'); git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
  mkdirSync(path.join(root, 'ops/release'), { recursive: true });
  writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  writeFileSync(path.join(root, 'source'), 'fixture');
  if (ignore) writeFileSync(path.join(root, '.gitignore'), 'ignored/\n');
  if (head) { git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture'); }
  return root;
}
const withFixture = (options, body) => { const root = fixture(options); try { body(root); } finally { rmSync(root, { recursive: true, force: true }); } };
const summary = (n = 1) => `TAP version 13\n${Array.from({ length: n }, (_, i) => `ok ${i + 1} - valid`).join('\n')}\n1..${n}\n# tests ${n}\n# pass ${n}\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n`;

test('version contract accepts boundaries and one final newline only', () => {
  for (const value of ['0.1.0', '65535.2.3\n', '1.2.3\r\n']) assert.ok(parseVersion(value));
  for (const value of ['', '0.0.0', '01.2.3', '1.02.3', '1.2.65536', '99999.2.3', '1.2.3\n\n', '1.2.3\r', ' 1.2.3', '1.2.3 ', '1.2.3x', '\uFEFF1.2.3', '１.2.3']) assert.throws(() => parseVersion(value));
});
test('readVersion uses requested file and rejects missing source', async () => {
  const root = fixture();
  try {
    assert.equal(await readVersion(path.join(root, 'ops/release/version.txt')), '1.2.3');
    await assert.rejects(readVersion(path.join(root, 'absent')));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('release source accepts clean root and ignored local output', () => withFixture({ ignore: true }, (root) => {
  mkdirSync(path.join(root, 'ignored')); writeFileSync(path.join(root, 'ignored/private'), 'x');
  assert.equal(checkReleaseSource(root).softwareVersion, '1.2.3');
  assert.match(checkReleaseSource(root).sourceCommit, /^[0-9a-f]{40,64}$/);
}));
for (const [name, mutate] of [
  ['staged edit', (root) => { writeFileSync(path.join(root, 'source'), 'x'); git(root, 'add', 'source'); }],
  ['unstaged edit', (root) => writeFileSync(path.join(root, 'source'), 'x')],
  ['untracked file', (root) => writeFileSync(path.join(root, 'new-file'), 'x')],
  ['deleted file', (root) => unlinkSync(path.join(root, 'source'))],
  ['missing version', (root) => unlinkSync(path.join(root, 'ops/release/version.txt'))],
  ['bad version', (root) => writeFileSync(path.join(root, 'ops/release/version.txt'), '0.0.0')],
]) test(`release source rejects ${name}`, () => withFixture({}, (root) => { mutate(root); assert.throws(() => checkReleaseSource(root)); }));
test('release source rejects subdirectory, empty repository and nonrepository', () => {
  withFixture({}, (root) => assert.throws(() => checkReleaseSource(path.join(root, 'ops'))));
  withFixture({ head: false }, (root) => assert.throws(() => checkReleaseSource(root)));
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-no-git-'));
  try { assert.throws(() => checkReleaseSource(root)); } finally { rmSync(root, { recursive: true, force: true }); }
});
test('release source rejects missing Git executable with fixed reason', () => withFixture({}, (root) => {
  const script = `import {checkReleaseSource} from ${JSON.stringify(new URL('../version.mjs', import.meta.url).href)}; try { checkReleaseSource(process.argv[1]); process.exit(2); } catch (error) { process.stdout.write(error.message); }`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], { encoding: 'utf8', env: { ...process.env, PATH: '' } });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, 'release input is not a Git repository');
  assert.equal(result.stderr, '');
}));
test('version CLI rejects invalid arguments and dirty release without leaking paths', () => withFixture({}, (root) => {
  const cli = path.resolve('ops/release/version.mjs');
  const invalid = spawnSync(process.execPath, [cli, '--unknown'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.equal(invalid.stderr, 'release input rejected\n');
  writeFileSync(path.join(root, 'new-file'), 'secret');
  assert.throws(() => checkReleaseSource(root), (error) => !error.message.includes(root) && !error.message.includes('new-file'));
}));
test('release CLI qualifies its own clean source and rejects dirty source', () => withFixture({}, (root) => {
  const cli = path.join(root, 'ops/release/version.mjs');
  copyFileSync(new URL('../version.mjs', import.meta.url), cli);
  git(root, 'add', 'ops/release/version.mjs');
  git(root, 'commit', '-qm', 'synthetic CLI');
  const clean = spawnSync(process.execPath, [cli, '--release'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(clean.status, 0, clean.stderr);
  assert.equal(clean.stderr, '');
  const identity = JSON.parse(clean.stdout);
  assert.equal(identity.softwareVersion, '1.2.3');
  assert.equal(identity.sourceCommit, execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim());
  writeFileSync(path.join(root, 'sensitive-filename'), 'synthetic');
  const dirty = spawnSync(process.execPath, [cli, '--release'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(dirty.status, 1);
  assert.equal(dirty.stdout, '');
  assert.equal(dirty.stderr, 'release input rejected\n');
}));
test('TAP parser requires all unique safe fields, plan, cases and exit zero', () => {
  assert.deepEqual(evaluateTapSummary(summary()), { status: 'PASS', checks_run: 1, failures: 0, errors: 0, skipped: 0, reason: '' });
  const bad = ['', summary() + summary(), summary().replace('1..1', ''), summary().replace('1..1', '1..0'), summary().replace('1..1', '1..2'), summary().replace('1..1', '1..9007199254740993'), summary().replace('# pass 1', ''), summary().replace('# pass 1', '# pass 1\n# pass 1'), summary().replace('# pass 1', '# pass x'), summary().replace('# pass 1', '# pass 9007199254740993'), summary().replace('ok 1 - valid', 'not ok 1 - valid'), summary().replace('ok 1 - valid', ''), summary().replace('# skipped 0', '# skipped 1'), summary().replace('# todo 0', '# todo 1'), summary().replace('# cancelled 0', '# cancelled 1'), summary().replace('# tests 1', '# tests 0')];
  for (const output of bad) assert.equal(evaluateTapSummary(output).status, 'FAIL');
  assert.equal(evaluateTapSummary(summary(), 1).status, 'FAIL');
});
test('runner handles exit failure, timeout, overflow and spawn error', async () => {
  for (const options of [
    { args: ['-e', 'process.exit(1)'] },
    { args: ['-e', 'setTimeout(() => {}, 10000)'], timeoutMs: 30 },
    { args: ['-e', 'process.stdout.write("x".repeat(10000))'], maxOutputBytes: 100 },
    { command: '/not/a/real/runner', args: [] },
  ]) assert.equal((await runTests(options)).status, 'FAIL');
});

test('snapshot input and Chrome channel mapping are deterministic and ordered', async () => {
  const { chromeVersion } = await import('../version.mjs');
  assert.equal(parseVersion('2.0.0-SNAPSHOT\n'), '2.0.0-SNAPSHOT');
  assert.equal(chromeVersion('2.0.0-SNAPSHOT.gabc1234.dirty.123456789abc'), '2.0.0.0');
  assert.equal(chromeVersion('2.0.0'), '2.0.0.1');
  assert.equal(chromeVersion('2.0.1-SNAPSHOT.gabc1234'), '2.0.1.0');
  for (const value of ['0.0.0-SNAPSHOT', '2.0.0-preview', '2.0.0-SNAPSHOT.gABC1234', '2.0.0-SNAPSHOT.gabc1234.dirty.', '2.0.65536-SNAPSHOT', '2.0.0\n']) assert.throws(() => chromeVersion(value));
  assert.throws(() => parseVersion('2.0.0-SNAPSHOT.gabc1234'));
});
test('build identity binds commit and content, not time, staging or ignored local data', async () => {
  const { resolveBuildIdentity } = await import('../version.mjs');
  withFixture({ ignore: true }, root => {
    const clean = resolveBuildIdentity(root);
    assert.equal(clean.softwareVersion, '1.2.3');
    assert.equal(clean.chromeVersion, '1.2.3.1');
    assert.equal(clean.dirty, false);
    assert.deepEqual(resolveBuildIdentity(root), clean);
    mkdirSync(path.join(root, 'ignored'));
    writeFileSync(path.join(root, 'ignored/private'), 'synthetic private input excluded');
    assert.deepEqual(resolveBuildIdentity(root), clean);
    writeFileSync(path.join(root, 'source'), 'changed one');
    const first = resolveBuildIdentity(root);
    assert.equal(first.channel, 'snapshot');
    assert.match(first.softwareVersion, /^1\.2\.3-SNAPSHOT\.g[a-f0-9]{7}\.dirty\.[a-f0-9]{12}$/);
    assert.equal(first.sourceCommit, clean.sourceCommit);
    git(root, 'add', 'source');
    assert.deepEqual(resolveBuildIdentity(root), first);
    writeFileSync(path.join(root, 'source'), 'changed two');
    const second = resolveBuildIdentity(root);
    assert.notEqual(second.buildId, first.buildId);
    assert.notEqual(second.softwareVersion, first.softwareVersion);
    writeFileSync(path.join(root, 'untracked'), 'new input');
    assert.notEqual(resolveBuildIdentity(root).buildId, second.buildId);
    unlinkSync(path.join(root, 'source'));
    assert.doesNotThrow(() => resolveBuildIdentity(root));
  });
});
test('clean snapshots retain full commit identity and cannot become formal tags', async () => {
  const { resolveBuildIdentity, checkReleaseTag } = await import('../version.mjs');
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'snapshot');
    const identity = resolveBuildIdentity(root);
    assert.equal(identity.dirty, false);
    assert.equal(identity.softwareVersion, `2.0.0-SNAPSHOT.g${identity.sourceCommit.slice(0, 7)}`);
    assert.equal(checkReleaseSource(root).softwareVersion, identity.softwareVersion);
    git(root, 'tag', 'v2.0.0');
    assert.throws(() => checkReleaseTag('v2.0.0', root));
  });
});
test('formal tag requires clean numeric version and tag at exact HEAD', async () => {
  const { checkReleaseTag } = await import('../version.mjs');
  withFixture({}, root => {
    assert.throws(() => checkReleaseTag('v1.2.3', root));
    git(root, 'tag', 'v1.2.3');
    assert.equal(checkReleaseTag('v1.2.3', root).channel, 'release');
    assert.throws(() => checkReleaseTag('v1.2.4', root));
    writeFileSync(path.join(root, 'source'), 'dirty');
    assert.throws(() => checkReleaseTag('v1.2.3', root));
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'next commit');
    assert.throws(() => checkReleaseTag('v1.2.3', root));
  });
});
test('build identity refuses symlinks rather than hashing external private input', async () => {
  const { resolveBuildIdentity } = await import('../version.mjs');
  const { symlinkSync } = await import('node:fs');
  withFixture({}, root => {
    symlinkSync('/nonexistent-private-source', path.join(root, 'link'));
    assert.throws(() => resolveBuildIdentity(root), /symlink forbidden/);
  });
});

const identityRaces = [
    ['file edit and restore during read', root => ({ trigger: 'source', change: () => {
      const file = path.join(root, 'source'); const original = fs.readFileSync(file);
      fs.writeFileSync(file, 'transient'); fs.writeFileSync(file, original);
    } })],
    ['previously read file edit and restore', root => ({ trigger: 'source', change: () => {
      const file = path.join(root, 'ops/release/version.txt'); const original = fs.readFileSync(file);
      fs.writeFileSync(file, 'transient'); fs.writeFileSync(file, original);
    } })],
    ['previously read file edit', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.4\n'); } })],
    ['tracked member addition', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'added'), 'x'); git(root, 'add', 'added'); } })],
    ['tracked member deletion', root => ({ trigger: 'source', change: () => { git(root, 'rm', '-q', 'source'); } })],
    ['HEAD change', root => ({ trigger: 'source', change: () => { git(root, 'commit', '--allow-empty', '-qm', 'race'); } })],
    ['version change', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.4\n'); } })],
  ];
for (const [name, mutate] of identityRaces) test(`identity rejects ${name} observed during bounded snapshots`, async () => {
  const { resolveBuildIdentity } = await import('../version.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    let changed = false;
    const race = mutate(root);
    fsDefault.openSync = function patchedOpen(filename, ...args) {
      const fd = originalOpen.call(this, filename, ...args);
      if (!changed && String(filename) === path.join(root, race.trigger)) { changed = true; race.change(); }
      return fd;
    };
    syncBuiltinESMExports();
    try { assert.throws(() => resolveBuildIdentity(root), /build source changed during identity scan/); assert.equal(changed, true); }
    finally { fsDefault.openSync = originalOpen; syncBuiltinESMExports(); }
  });
});

test('identity rejects file bytes changed and restored during an FD read', async () => {
  const { resolveBuildIdentity } = await import('../version.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    const originalRead = fsDefault.readSync;
    const filename = path.join(root, 'source');
    const bytes = fs.readFileSync(filename);
    let targetFd, changed = false;
    fsDefault.openSync = function (...args) {
      const fd = originalOpen.apply(this, args);
      if (String(args[0]) === filename) targetFd = fd;
      return fd;
    };
    fsDefault.readSync = function (...args) {
      const count = originalRead.apply(this, args);
      if (!changed && args[0] === targetFd && count > 0) {
        changed = true;
        fs.writeFileSync(filename, 'transient'); fs.writeFileSync(filename, bytes);
      }
      return count;
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveBuildIdentity(root), /build source changed during identity scan/);
      assert.equal(changed, true);
    } finally {
      fsDefault.openSync = originalOpen; fsDefault.readSync = originalRead; syncBuiltinESMExports();
    }
  });
});
