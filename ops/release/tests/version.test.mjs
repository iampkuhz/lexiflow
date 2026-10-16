import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, unlinkSync, copyFileSync, cpSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import childProcess from 'node:child_process';
import * as fs from 'node:fs';
import fsDefault from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { parseVersion, resolveCleanBuildIdentity, checkReleaseSource } from '../integrity/source-build-identity.mjs';
import { evaluateTapSummary, runTests } from '../test_tools/run-release-tool-tests.mjs';

const git = (cwd, ...args) => execFileSync('git', args, { cwd, stdio: 'ignore', timeout: 10_000, env: { ...process.env, GIT_AUTHOR_DATE: '2026-10-05T00:00:00Z', GIT_COMMITTER_DATE: '2026-10-05T18:00:00Z' } });
function createFixture({ head = true, ignore = false } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  git(root, 'init', '-q'); git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
  mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
  writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  writeFileSync(path.join(root, 'source'), 'fixture');
  if (ignore) writeFileSync(path.join(root, '.gitignore'), 'ignored/\n');
  if (head) { git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture'); }
  return root;
}
// 每次测试进程只生成三类不变合成 Git 基线；每例复制完整独立目录，
// 不共享 index、refs、工作树或身份解析结果，也不复用任何验证 PASS。
const fixtureSeeds = new Map();
test.after(() => { for (const root of fixtureSeeds.values()) rmSync(root, { recursive: true, force: true }); });
function fixture({ head = true, ignore = false } = {}) {
  const key = JSON.stringify({ head, ignore });
  if (!fixtureSeeds.has(key)) fixtureSeeds.set(key, createFixture({ head, ignore }));
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  try { cpSync(fixtureSeeds.get(key), root, { recursive: true }); return root; }
  catch (error) { rmSync(root, { recursive: true, force: true }); throw error; }
}
const withFixture = (options, body) => { const root = fixture(options); try { body(root); } finally { rmSync(root, { recursive: true, force: true }); } };
const summary = (n = 1) => `TAP version 13\n${Array.from({ length: n }, (_, i) => `ok ${i + 1} - valid`).join('\n')}\n1..${n}\n# tests ${n}\n# pass ${n}\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n`;

test('copied version fixtures isolate worktree, index, refs and cleanup', () => {
  const first = fixture(), second = fixture();
  try {
    const expected = resolveCleanBuildIdentity(second);
    assert.deepEqual(resolveCleanBuildIdentity(first), expected);
    writeFileSync(path.join(first, 'source'), 'independent edit');
    git(first, 'add', 'source'); git(first, 'commit', '-qm', 'independent commit');
    assert.notEqual(resolveCleanBuildIdentity(first).sourceCommit, expected.sourceCommit);
    assert.deepEqual(resolveCleanBuildIdentity(second), expected);
    rmSync(first, { recursive: true, force: true });
    assert.deepEqual(resolveCleanBuildIdentity(second), expected);
  } finally { rmSync(first, { recursive: true, force: true }); rmSync(second, { recursive: true, force: true }); }
});

test('version contract accepts boundaries and one final newline only', () => {
  for (const value of ['0.1.0', '65535.2.3\n', '1.2.3\r\n']) assert.ok(parseVersion(value));
  for (const value of ['', '0.0.0', '01.2.3', '1.02.3', '1.2.65536', '99999.2.3', '1.2.3\n\n', '1.2.3\r', ' 1.2.3', '1.2.3 ', '1.2.3x', '\uFEFF1.2.3', '１.2.3']) assert.throws(() => parseVersion(value));
});
test('release projection reads the version from the same full source scan and rejects committed missing/invalid input', () => {
  withFixture({}, root => {
    const { softwareVersion, sourceCommit } = resolveCleanBuildIdentity(root);
    assert.deepEqual(checkReleaseSource(root), { softwareVersion, sourceCommit });
    for (const value of ['', '0.0.0']) {
      writeFileSync(path.join(root, 'ops/release/version.txt'), value);
      git(root, 'add', '.'); git(root, 'commit', '-qm', 'invalid version');
      assert.throws(() => checkReleaseSource(root), /build source identity unavailable/);
    }
    unlinkSync(path.join(root, 'ops/release/version.txt'));
    git(root, 'add', '-u'); git(root, 'commit', '-qm', 'missing version');
    assert.throws(() => checkReleaseSource(root), /build source identity unavailable/);
  });
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
  const script = `import {checkReleaseSource} from ${JSON.stringify(new URL('../integrity/source-build-identity.mjs', import.meta.url).href)}; try { checkReleaseSource(process.argv[1]); process.exit(2); } catch (error) { process.stdout.write(error.message); }`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], { encoding: 'utf8', env: { ...process.env, PATH: '' } });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, 'build source identity unavailable');
  assert.equal(result.stderr, '');
}));
test('version CLI rejects invalid arguments and dirty release without leaking paths', () => withFixture({}, (root) => {
  const cli = path.resolve('ops/release/integrity/source-build-identity.mjs');
  const invalid = spawnSync(process.execPath, [cli, '--unknown'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.equal(invalid.stderr, 'release input rejected\n');
  writeFileSync(path.join(root, 'new-file'), 'secret');
  assert.throws(() => checkReleaseSource(root), (error) => !error.message.includes(root) && !error.message.includes('new-file'));
}));
test('release CLI qualifies its own clean source and rejects dirty source', () => withFixture({}, (root) => {
  const cli = path.join(root, 'ops/release/integrity/source-build-identity.mjs');
  copyFileSync(new URL('../integrity/source-build-identity.mjs', import.meta.url), cli);
  git(root, 'add', 'ops/release/integrity/source-build-identity.mjs');
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
  const { chromeVersion } = await import('../integrity/source-build-identity.mjs');
  assert.equal(parseVersion('2.0.0-SNAPSHOT\n'), '2.0.0-SNAPSHOT');
  assert.equal(chromeVersion('2.0.0-SNAPSHOT.20261006.gabc1234.dirty.123456789abc'), '2.0.0.0');
  assert.equal(chromeVersion('2.0.0'), '2.0.0.1');
  assert.equal(chromeVersion('2.0.1-SNAPSHOT.20261006.gabc1234'), '2.0.1.0');
  for (const value of ['0.0.0-SNAPSHOT', '2.0.0-preview', '2.0.0-SNAPSHOT.20261006.gABC1234', '2.0.0-SNAPSHOT.20261006.gabc1234.dirty.', '2.0.65536-SNAPSHOT', '2.0.0\n']) assert.throws(() => chromeVersion(value));
  assert.throws(() => parseVersion('2.0.0-SNAPSHOT.20261006.gabc1234'));
});
test('build identity binds commit and content, not time, staging or ignored local data', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
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
    assert.match(first.softwareVersion, /^1\.2\.3-SNAPSHOT\.[1-9][0-9]{7}\.g[a-f0-9]{7}\.dirty\.[a-f0-9]{12}$/);
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
    const beforeLicense = resolveBuildIdentity(root);
    writeFileSync(path.join(root, 'LICENSE'), 'synthetic authorized notice');
    const withLicense = resolveBuildIdentity(root);
    assert.notEqual(withLicense.sourceSha256, beforeLicense.sourceSha256);
    assert.notEqual(withLicense.buildId, beforeLicense.buildId);
    writeFileSync(path.join(root, 'LICENSE'), 'synthetic changed notice');
    assert.notEqual(resolveBuildIdentity(root).sourceSha256, withLicense.sourceSha256);
  });
});
test('clean snapshots retain full commit identity and cannot become formal tags', async () => {
  const { resolveBuildIdentity, checkReleaseTag } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'snapshot');
    const identity = resolveBuildIdentity(root);
    assert.equal(identity.dirty, false);
    assert.equal(identity.softwareVersion, `2.0.0-SNAPSHOT.20261006.g${identity.sourceCommit.slice(0, 7)}`);
    assert.equal(checkReleaseSource(root).softwareVersion, identity.softwareVersion);
    git(root, 'tag', 'v2.0.0');
    assert.throws(() => checkReleaseTag('v2.0.0', root));
  });
});
test('formal tag requires clean numeric version and tag at exact HEAD', async () => {
  const { checkReleaseTag } = await import('../integrity/source-build-identity.mjs');
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
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
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
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
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
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
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


test('snapshot date uses committer date in UTC+08 and ignores build clock and host timezone', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'dated snapshot');
    const expected = resolveBuildIdentity(root);
    assert.match(expected.softwareVersion, /^2\.0\.0-SNAPSHOT\.20261006\.g[a-f0-9]{7}$/);
    assert.equal(checkReleaseSource(root).softwareVersion, expected.softwareVersion);
    const script = `import {resolveBuildIdentity} from ${JSON.stringify(new URL('../integrity/source-build-identity.mjs', import.meta.url).href)}; Date.now = () => 1; console.log(JSON.stringify(resolveBuildIdentity(process.argv[1])));`;
    for (const TZ of ['UTC', 'Pacific/Honolulu', 'Pacific/Auckland']) {
      const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], {
        encoding: 'utf8', timeout: 10000, env: { ...process.env, TZ },
      });
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(JSON.parse(result.stdout), expected);
    }
  });
});

test('snapshot date must be a real calendar date and is bound into the development build ID', async () => {
  const { parseSoftwareVersion, resolveBuildIdentity, validateBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  assert.equal(parseSoftwareVersion('2.0.0-SNAPSHOT.20240229.gabc1234'), '2.0.0-SNAPSHOT.20240229.gabc1234');
  for (const date of ['20260229', '20260431', '20261301', '20260001', '00000101', '2026106']) {
    assert.throws(() => parseSoftwareVersion(`2.0.0-SNAPSHOT.${date}.gabc1234`));
  }
  assert.throws(() => parseSoftwareVersion('2.0.0-SNAPSHOT.gabc1234'));
  withFixture({}, root => {
    writeFileSync(path.join(root, 'source'), 'dirty fixture');
    const expected = resolveBuildIdentity(root);
    assert.equal(validateBuildIdentity(expected), expected);
    assert.throws(() => validateBuildIdentity({ ...expected,
      softwareVersion: expected.softwareVersion.replace('.20261006.', '.20261007.') }), /INVALID_BUILD_IDENTITY/);
  });
});

test('combined snapshot metadata matches the two-command Git oracle across repository states', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  for (const state of ['clean', 'staged', 'unstaged', 'untracked', 'head']) {
    withFixture({}, root => {
      if (state === 'staged') { writeFileSync(path.join(root, 'source'), 'staged'); git(root, 'add', 'source'); }
      if (state === 'unstaged') writeFileSync(path.join(root, 'source'), 'unstaged');
      if (state === 'untracked') writeFileSync(path.join(root, 'new-file'), 'new');
      if (state === 'head') { git(root, 'commit', '--allow-empty', '-qm', 'second'); }
      const original = childProcess.execFileSync;
      let oracle = false;
      const actualCommands = [];
      const counts = { combined: 0, show: 0, status: 0, lsFiles: 0 };
      childProcess.execFileSync = function (command, args, options) {
        if (!oracle && command === 'git') actualCommands.push(args.slice(2));
        if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) {
          counts.combined++;
          if (!oracle) return original.call(this, command, args, options);
          const top = original.call(this, command, ['-C', root, 'rev-parse', '--show-toplevel'], options);
          const head = original.call(this, command, ['-C', root, 'rev-parse', '--verify', 'HEAD^{commit}'], options);
          return `${top}${head}`;
        }
        if (command === 'git' && args[2] === 'show') counts.show++;
        if (command === 'git' && args[2] === 'status') counts.status++;
        if (command === 'git' && args[2] === 'ls-files') counts.lsFiles++;
        return original.call(this, command, args, options);
      };
      syncBuiltinESMExports();
      try {
        const identity = resolveBuildIdentity(root);
        assert.equal(actualCommands.length, 16, `${state} physical Git commands`);
        assert.deepEqual(counts, { combined: 4, show: 4, status: 4, lsFiles: 4 });
        oracle = true;
        assert.deepEqual(resolveBuildIdentity(root), identity, `${state} identity oracle`);
        assert.deepEqual(counts, { combined: 8, show: 8, status: 8, lsFiles: 8 });
      } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
    });
  }
});

test('clean release identity keeps four Git snapshots, two stable scans, and rejects dirty states', async () => {
  const { resolveBuildIdentity, resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({ ignore: true }, root => {
    mkdirSync(path.join(root, 'ignored')); writeFileSync(path.join(root, 'ignored/private'), 'ignored');
    const original = childProcess.execFileSync;
    const counts = { combined: 0, show: 0, status: 0, lsFiles: 0 };
    let expectedBuffer = 100_000;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) counts.combined++;
      if (command === 'git' && args[2] === 'show') counts.show++;
      if (command === 'git' && args[2] === 'status') {
        counts.status++;
        assert.equal(options.maxBuffer, expectedBuffer);
      }
      if (command === 'git' && args[2] === 'ls-files') counts.lsFiles++;
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      const clean = resolveCleanBuildIdentity(root);
      assert.equal(clean.channel, 'release');
      assert.equal(clean.dirty, false);
      assert.deepEqual(counts, { combined: 4, show: 4, status: 4, lsFiles: 4 });
      expectedBuffer = 8 * 1024 * 1024;
      assert.deepEqual(resolveBuildIdentity(root), clean, 'ordinary resolver retains its existing status bound');
      assert.deepEqual(counts, { combined: 8, show: 8, status: 8, lsFiles: 8 });
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  });
  for (const [state, mutate] of [
    ['staged', root => { writeFileSync(path.join(root, 'source'), 'dirty'); git(root, 'add', 'source'); }],
    ['unstaged', root => writeFileSync(path.join(root, 'source'), 'dirty')],
    ['untracked', root => writeFileSync(path.join(root, 'new-file'), 'dirty')],
    ['deleted', root => unlinkSync(path.join(root, 'source'))],
  ]) withFixture({}, root => {
    mutate(root);
    assert.equal(resolveBuildIdentity(root).dirty, true, `${state}: ordinary identity supports dirty trees`);
    assert.throws(() => resolveCleanBuildIdentity(root), /release input must be clean/, state);
  });
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'snapshot');
    assert.equal(resolveCleanBuildIdentity(root).channel, 'snapshot');
  });
});

test('clean identity maps porcelain overflow to release rejection inside its four snapshots', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    let statusCalls = 0;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args[2] === 'status') {
        statusCalls++;
        assert.equal(options.maxBuffer, 100_000);
        const error = new Error('synthetic ENOBUFS'); error.code = 'ENOBUFS'; throw error;
      }
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
      assert.equal(statusCalls, 1);
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  });
});

test('clean resolver rejects missing/invalid source, wrong roots, absent HEAD, and tracked symlinks', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    unlinkSync(path.join(root, 'ops/release/version.txt'));
    git(root, 'add', '-u'); git(root, 'commit', '-qm', 'missing version');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
  });
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '0.0.0\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'invalid version');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
  });
  withFixture({ head: false }, root => assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/));
  withFixture({}, root => assert.throws(() => resolveCleanBuildIdentity(path.join(root, 'ops')), /build source identity unavailable/));
  const noRepo = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-clean-no-git-'));
  try { assert.throws(() => resolveCleanBuildIdentity(noRepo), /build source identity unavailable/); } finally { rmSync(noRepo, { recursive: true, force: true }); }
  withFixture({}, root => {
    unlinkSync(path.join(root, 'source'));
    fs.symlinkSync('ops/release/version.txt', path.join(root, 'source'));
    git(root, 'add', '-A'); git(root, 'commit', '-qm', 'tracked symlink');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source symlink forbidden/);
  });
});

test('clean resolver rejects a tracked scan drift detected while opening an input', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    let changed = false;
    fsDefault.openSync = function patchedOpen(filename, ...args) {
      const fd = originalOpen.call(this, filename, ...args);
      if (!changed && String(filename) === path.join(root, 'source')) {
        changed = true;
        writeFileSync(path.join(root, 'source'), 'raced');
      }
      return fd;
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveCleanBuildIdentity(root), /build source changed during identity scan/);
      assert.equal(changed, true);
    } finally { fsDefault.openSync = originalOpen; syncBuiltinESMExports(); }
  });
});

test('combined snapshot metadata supports roots with spaces, Unicode, and internal newlines', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const parent = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-'));
  const root = path.join(parent, '空 格\nrepo');
  try {
    mkdirSync(root); git(root, 'init', '-q'); git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
    mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n'); writeFileSync(path.join(root, 'source'), 'fixture');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture');
    const actual = resolveBuildIdentity(root);
    assert.equal(actual.sourceCommit, execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim());
  } finally { rmSync(parent, { recursive: true, force: true }); }
});

test('combined rev-parse rejects malformed or failed output and preserves no-HEAD rejection', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  for (const malformed of [() => 'malformed', root => `${root}\n`, root => `${root}\nnot-a-commit`]) {
    withFixture({}, root => {
      const original = childProcess.execFileSync;
      childProcess.execFileSync = function (command, args, options) {
        if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) return malformed(root);
        return original.call(this, command, args, options);
      };
      syncBuiltinESMExports();
      try { assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/); }
      finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
    });
  }
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) throw new Error('synthetic git failure');
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try { assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/); }
    finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  });
  withFixture({ head: false }, root => assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/));
  withFixture({}, root => assert.throws(() => resolveBuildIdentity(path.join(root, 'ops')), /build source identity unavailable/));
});

test('SHA-256 Git object identity uses an actual 64-digit commit', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-sha256-'));
  try {
    git(root, 'init', '-q', '--object-format=sha256');
    git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
    mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n'); writeFileSync(path.join(root, 'source'), 'fixture');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture');
    assert.match(resolveBuildIdentity(root).sourceCommit, /^[a-f0-9]{64}$/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

function runnerChildScript(readyFile, heartbeatFile, workerReadyFile, overflow = false) {
  const workerCode = `const fs=require('node:fs');process.on('SIGTERM',()=>{});fs.writeFileSync(${JSON.stringify(heartbeatFile)},'x');fs.writeFileSync(${JSON.stringify(workerReadyFile)},JSON.stringify({worker:process.pid}));setInterval(()=>fs.appendFileSync(${JSON.stringify(heartbeatFile)},'x'),20);`;
  return `const fs=require('node:fs');const cp=require('node:child_process');const worker=cp.spawn(process.execPath,['-e',${JSON.stringify(workerCode)}],{stdio:'ignore'});const until=Date.now()+2500;const poll=()=>{if(fs.existsSync(${JSON.stringify(workerReadyFile)})){fs.writeFileSync(${JSON.stringify(readyFile)},JSON.stringify({supervisor:process.pid,worker:JSON.parse(fs.readFileSync(${JSON.stringify(workerReadyFile)},'utf8')).worker}));${overflow ? "process.stdout.write('x'.repeat(1000));" : ''}setInterval(()=>{},1000);return;}if(Date.now()>until)process.exit(3);setTimeout(poll,5)};poll();`;
}

async function waitForRunnerHandshake(readyFile) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (fs.existsSync(readyFile)) return JSON.parse(fs.readFileSync(readyFile, 'utf8'));
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('runner child handshake timed out');
}

function pidIsDead(pid) {
  try { process.kill(pid, 0); return false; }
  catch (error) { if (error.code === 'ESRCH') return true; throw error; }
}

async function assertRunnerPidsReaped(pids) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (pids.every(pidIsDead)) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.deepEqual(pids.filter(pid => !pidIsDead(pid)), [], 'runner-owned supervisor and worker must be ESRCH before test cleanup');
}

function spawnTracked(command, args, options = {}) {
  const child = childProcess.spawn(command, args, { ...options, stdio: options.stdio ?? 'ignore' });
  child.exitResult = new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  return child;
}

async function waitForExit(child, timeoutMs = 3000) {
  let deadline;
  try {
    return await Promise.race([
      child.exitResult,
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error(`owned process ${child.pid} did not exit within ${timeoutMs}ms`)), timeoutMs); }),
    ]);
  } finally { clearTimeout(deadline); }
}

async function assertSentinelAlive(pid) {
  assert.doesNotThrow(() => process.kill(pid, 0), 'independent sentinel remains alive');
}

test('runner timeout and overflow kill their owned groups without touching a detached sentinel', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  for (const mode of ['timeout', 'overflow']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-'));
    const readyFile = path.join(root, 'ready'); const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const sentinelReady = path.join(root, 'sentinel-ready');
    let supervisor;
    let sentinel;
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      sentinel.unref();
      const sentinelPid = sentinel.pid;
      const sentinelDeadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < sentinelDeadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      const args = ['-e', runnerChildScript(readyFile, heartbeatFile, workerReadyFile, mode === 'overflow')];
      const resultPromise = runTests({ command: process.execPath, args, timeoutMs: mode === 'timeout' ? 3000 : 10000, maxOutputBytes: mode === 'overflow' ? 64 : 10000 });
      const ready = await waitForRunnerHandshake(readyFile);
      supervisor = ready.supervisor;
      await assertSentinelAlive(sentinelPid);
      const result = await resultPromise;
      assert.equal(result.status, 'FAIL');
      assert.equal(result.reason, mode === 'timeout' ? 'test process timed out' : 'test process output exceeded limit');
      await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
      await assertSentinelAlive(sentinelPid);
    } finally {
      if (supervisor) { try { process.kill(-supervisor, 'SIGKILL'); } catch {} }
      if (sentinel?.pid) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch {} }
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test('runner forwards parent TERM and INT after a real worker handshake and restores listeners', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  for (const signal of ['SIGTERM', 'SIGINT']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-cancel-'));
    const readyFile = path.join(root, 'ready'); const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const termBefore = process.listenerCount('SIGTERM'); const intBefore = process.listenerCount('SIGINT');
    let ready;
    try {
      const resultPromise = runTests({ command: process.execPath, args: ['-e', runnerChildScript(readyFile, heartbeatFile, workerReadyFile)], timeoutMs: 10000 });
      ready = await waitForRunnerHandshake(readyFile);
      process.emit(signal);
      const result = await resultPromise;
      assert.equal(result.status, 'FAIL'); assert.equal(result.reason, 'test process cancelled');
      await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
      assert.equal(process.listenerCount('SIGTERM'), termBefore);
      assert.equal(process.listenerCount('SIGINT'), intBefore);
    } finally {
      if (ready) {
        try { process.kill(-ready.supervisor, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
        for (const pid of [ready.supervisor, ready.worker]) { try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      }
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test('runner private child group does not escape real wrapper-group TERM and INT cancellation', async () => {
  const wrapperModule = new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href;
  for (const signal of ['SIGTERM', 'SIGINT']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-wrapper-'));
    const readyFile = path.join(root, 'ready'); const childReady = path.join(root, 'child-ready');
    const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const sentinelReady = path.join(root, 'sentinel-ready');
    const wrapperCode = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(wrapperModule)};const args=['-e',${JSON.stringify(runnerChildScript(childReady, heartbeatFile, workerReadyFile))}];const pending=runTests({command:process.execPath,args,timeoutMs:15000});const end=Date.now()+4000;while(!fs.existsSync(${JSON.stringify(childReady)})&&Date.now()<end)await new Promise(r=>setTimeout(r,10));if(!fs.existsSync(${JSON.stringify(childReady)}))process.exit(4);fs.writeFileSync(${JSON.stringify(readyFile)},JSON.stringify({wrapper:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(childReady)},'utf8'))}));const result=await pending;process.exitCode=result.status==='PASS'?0:1;`;
    let wrapper; let sentinel;
    let owned = []; let ownedGroupLeaders = [];
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      const sentinelPid = sentinel.pid;
      const sentinelDeadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < sentinelDeadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      wrapper = spawnTracked(process.execPath, ['--input-type=module', '-e', wrapperCode], { detached: true });
      const ready = await waitForRunnerHandshake(readyFile);
      owned = [ready.wrapper, ready.supervisor, ready.worker];
      ownedGroupLeaders = [ready.wrapper, ready.supervisor];
      await assertSentinelAlive(sentinelPid);
      process.kill(-wrapper.pid, signal);
      await new Promise(resolve => setTimeout(resolve, 500));
      if (!pidIsDead(ready.wrapper)) {
        try { process.kill(-wrapper.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      const exit = await waitForExit(wrapper, 3000);
      assert.ok(exit.code !== 0 || exit.signal !== null, 'wrapper must not report successful completion');
      await assertRunnerPidsReaped(owned);
      await assertSentinelAlive(sentinelPid);
    } finally {
      if (wrapper?.pid && !pidIsDead(wrapper.pid)) { try { process.kill(-wrapper.pid, 'SIGKILL'); } catch {} }
      // 组长退出不代表同组后代已退出；清理仍使用本次握手绑定的组。
      for (const pid of ownedGroupLeaders) { try { process.kill(-pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      if (wrapper) await waitForExit(wrapper, 3000).catch(() => {});
      if (sentinel?.pid && !pidIsDead(sentinel.pid)) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch {} }
      if (sentinel?.pid) await waitForExit(sentinel, 3000).catch(() => {});
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test('runner preserves TAP pass, nonzero exit, and spawn-error results', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  const tap = 'TAP version 13\n1..1\nok 1 - runner direct pass\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n';
  const pass = await runTests({ command: process.execPath, args: ['-e', `process.stdout.write(${JSON.stringify(tap)})`] });
  assert.deepEqual(pass, { status: 'PASS', checks_run: 1, failures: 0, errors: 0, skipped: 0, reason: '' });
  const nonzero = await runTests({ command: process.execPath, args: ['-e', 'process.exit(7)'] });
  assert.equal(nonzero.status, 'FAIL');
  const spawnError = await runTests({ command: '/not/a/real/runner', args: [] });
  assert.equal(spawnError.status, 'FAIL'); assert.equal(spawnError.errors, 1);
});

test('fixed CLI suites preserve exact files and budgets and reject free-form overrides', () => {
  const entry = path.resolve('ops/release/test_tools/run-release-tool-tests.mjs');
  const expected = {
    version: [30_000, ['version']],
    build: [170_000, ['archive-identity', 'embedded-identity', 'build', 'build-execution', 'candidate', 'verified-candidate', 'candidate-proof', 'pipeline', 'workflow', 'synthetic-candidates', 'runner']],
    manifest: [80_000, ['manifest']],
    'runtime-entry': [80_000, ['runtime-entry']],
    lifecycle: [120_000, ['lifecycle']],
    'lifecycle-install': [120_000, ['lifecycle-install']],
  };
  const script = `import cp from 'node:child_process';import {syncBuiltinESMExports} from 'node:module';
    const spawn=cp.spawn;const timers=[];const timer=globalThis.setTimeout;globalThis.setTimeout=(fn,ms,...args)=>{timers.push(ms);return timer(fn,ms,...args)};
    const calls=[];cp.spawn=(command,args,options)=>{calls.push({command,args});return spawn(process.execPath,['-e',${JSON.stringify(`process.stdout.write(${JSON.stringify(summary())})`)}],options)};syncBuiltinESMExports();
    process.argv=[process.execPath,${JSON.stringify(entry)},...JSON.parse(process.argv[1])];
    const output=[];console.log=value=>output.push(JSON.parse(value));
    await import(${JSON.stringify(new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href)});
    process.on('beforeExit',()=>process.stdout.write(JSON.stringify({calls,timers,output})));`;
  for (const [suite, [budget, names]] of Object.entries(expected)) {
    const argv = suite === 'version' ? [] : ['--suite', suite];
    const run = spawnSync(process.execPath, ['--input-type=module', '-e', script, JSON.stringify(argv)], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(run.status, 0, run.stderr);
    const { calls, timers, output } = JSON.parse(run.stdout);
    assert.deepEqual(calls, [{ command: process.execPath, args: [...(suite === 'build' ? ['--experimental-test-module-mocks'] : []), '--test', '--test-reporter=tap', ...names.map(name => path.join(path.dirname(entry), `../tests/${name}.test.mjs`))] }]);
    assert.deepEqual(timers, [budget]);
    assert.equal(output[0].status, 'PASS');
  }
  for (const argv of [['--suite', 'version'], ['--suite', 'missing'], ['--suite', '../version'], ['--command', 'sh'], ['--suite', 'build', '--timeout', '1'], ['--suite']]) {
    const run = spawnSync(process.execPath, ['--input-type=module', '-e', script, JSON.stringify(argv)], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(run.status, 1);
    const { calls, timers, output } = JSON.parse(run.stdout);
    assert.deepEqual(calls, []); assert.deepEqual(timers, []);
    assert.equal(output[0].status, 'FAIL'); assert.equal(output[0].reason, 'invalid arguments');
  }
});

test('release projection uses one 15 second deadline for every Git process and source read', t => {
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    let elapsed = 0;
    t.mock.method(performance, 'now', () => elapsed);
    const budgets = [];
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git') {
        budgets.push(options.timeout);
        assert.equal(options.timeout, Math.min(10_000, 15_000 - elapsed));
        const result = original.call(this, command, args, options);
        elapsed += 1100;
        return result;
      }
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => checkReleaseSource(root), /build source identity timed out/);
      assert.ok(budgets.some(ms => ms < 10_000), 'late subprocesses consume remaining total budget');
      assert.ok(budgets.every(ms => ms > 0));
      assert.ok(budgets.length < 16, 'expired deadline does not start another full Git snapshot');
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); t.mock.restoreAll(); }
  });
});

test('source file reads consume the same deadline and close the active file on expiry', t => {
  withFixture({}, root => {
    const read = fsDefault.readSync; const close = fsDefault.closeSync;
    let elapsed = 0; let openedFd; let closed = false;
    t.mock.method(performance, 'now', () => elapsed);
    fsDefault.readSync = function (fd, ...args) {
      openedFd = fd;
      const count = read.call(this, fd, ...args);
      elapsed = 15_001;
      return count;
    };
    fsDefault.closeSync = function (fd) { if (fd === openedFd) closed = true; return close.call(this, fd); };
    syncBuiltinESMExports();
    try {
      assert.throws(() => checkReleaseSource(root), /build source identity timed out/);
      assert.ok(closed, 'timed-out source read still releases its own descriptor');
    } finally {
      fsDefault.readSync = read; fsDefault.closeSync = close; syncBuiltinESMExports(); t.mock.restoreAll();
    }
  });
});
